"""B11:/api/converse 协议层鲁棒性与模糊测试(LOOP-CONTRACT §11)。

分工背景(2026-08-14 波次,11 单元并行):B5 开发端点(契约 §7)、B1 开发
driver.converse_loop(契约 §4),两者与本文件同期演进。为不依赖其进度,本文件
把 Driver.converse_loop 整体替换为「契约 §4 形状」的假异步生成器(类级
monkeypatch,raising=False,B1 未落地也能打),只测端点协议层的不变量 ——
集成真实循环后这些不变量必须原样成立(桩不触碰端点侧任何代码路径)。

验收的不变量(每类对应一组用例):
  1. 请求体模糊:max_cycles 越界/类型混乱一律 400/422 且挡在开流前(响应为
     非流式 JSON,绝不触发 converse_loop);session 非法字符集同 /api/turn
     口径 400;text 不设内容校验,任意 UTF-8 内容无损往返。
  2. 事件流:桩发出乱序/未知类型事件 → NDJSON 每行仍是独立合法 JSON、保序
     保内容;桩中途抛异常 → 流以 note 收尾,异常细节不外泄,连接不裸断。
  3. 会话隔离:A 会话 converse 的事件绝不出现在 B 会话的 /api/events SSE。
     (此项打真实 uvicorn + httpx:TestClient 的 ASGI 传输会缓冲整个响应,
     无限 SSE 流永不返回 —— test_api_robustness.live_server 同法。)
  4. 并发:同会话并发两请求行为确定(当前实现=并发放行、各得独立完整流,
     桩内会合点证明两流真实交叠);不同会话并发互不串台。
  5. 取消风暴:回合中(桩停在闸门上,服务端信号证明在途)连发 /api/abort →
     幂等 202、无 500。
  6. 泄漏面:llm.json 放假密钥后,converse 全链路(成功流/异常流/错误响应/
     配置回显)全文 grep 不出现明文密钥(test_api_surface_r3 同款红线)。

密封纪律:autouse 断网(urllib.request.urlopen 拦截)+ probe_environment 空引擎
+ 临时 INSAR_HOME(create_app(home=临时目录) 且 setenv 兜底);绝不真联网、
绝不触碰 8873 生产实例。时序敏感判定窗一律乘 conftest.TIME_FACTOR
(@pytest.mark.timing);固定轮询间隔不乘(conftest 纪律)。
"""

from __future__ import annotations

import asyncio
import itertools
import json
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from conftest import TIME_FACTOR
from insar_agent.api.app import create_app
from insar_agent.core.db import Database
from insar_agent.core.store import Store
from insar_agent.loop.driver import Driver
from insar_agent.runtime.probe import ProbeResult

# 短促、无副作用库、放宽健康检查(与 test_api_fuzz 同型:模块级 fixture 是
# 本测试的正常形态,首例连带建 driver 较慢不误伤)
settings.register_profile(
    "loop_robust",
    deadline=None,
    database=None,
    max_examples=30,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
settings.load_profile("loop_robust")

JSON_HEADERS = {"content-type": "application/json"}

#: 测试专用假密钥(绝不是真实密钥):任何响应里出现它即判泄漏
_FAKE_KEY = "sk-rob-SECRET-DO-NOT-LEAK-0123456789abcdef"

#: 全文件唯一序号:给每个请求造独立 marker,模块级共享 app 下定位「自己的」桩调用
_SEQ = itertools.count(1)


# ---------------- 端点探测(B5 并行开发:端点缺失时按契约整模块跳过) ----------------


def _converse_route_mounted() -> bool:
    """探测 /api/converse 是否已挂载。

    一次性 create_app(不启动、不发请求、不触发引擎探测),路由表是唯一真相源;
    临时目录里只落一个空 DB。Windows 上 DB 句柄未关导致目录删不掉 → 忽略清理错误。
    """
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        probe_app = create_app(home=Path(td) / "home")
        return any(getattr(r, "path", "") == "/api/converse" for r in probe_app.routes)


_HAS_CONVERSE = _converse_route_mounted()
pytestmark = pytest.mark.skipif(
    not _HAS_CONVERSE,
    reason="/api/converse 尚未挂载(B5 并行开发中):用例按 LOOP-CONTRACT §7 先行,"
           "端点落地后自动生效")


# ---------------- 假 converse_loop(契约 §4 形状)与调用账本 ----------------

_CALLS: list[dict] = []
_CALLS_LOCK = threading.Lock()


def _record(session: str, text: object, max_cycles: object) -> None:
    with _CALLS_LOCK:
        _CALLS.append({"session": session, "text": text, "max_cycles": max_cycles})


def _calls_with(marker: str) -> list[dict]:
    """按 text 中的 marker 检索桩调用(模块级共享 app,marker 保证互不误伤)。"""
    with _CALLS_LOCK:
        return [c for c in _CALLS
                if isinstance(c.get("text"), str) and marker in c["text"]]


def _single_call(marker: str) -> dict:
    hits = _calls_with(marker)
    assert len(hits) == 1, f"期望恰好一次 converse_loop 调用(marker={marker!r}),实得 {len(hits)}"
    return hits[0]


class _GateState:
    """abort 风暴测试的跨线程闸门:桩停在闸门上 = 「回合进行中」的确定性窗口。

    桩在事件循环上等待 asyncio.Event;测试线程经 call_soon_threadsafe 放行,
    绝无跨线程直接操作 asyncio 原语的竞态。
    """

    def __init__(self):
        self.loop: asyncio.AbstractEventLoop | None = None
        self.event: asyncio.Event | None = None
        self.opened = threading.Event()

    def reset(self) -> None:
        self.__init__()

    def release(self) -> None:
        if self.loop is not None and self.event is not None:
            self.loop.call_soon_threadsafe(self.event.set)


_GATE = _GateState()


class _MeetState:
    """同会话并发测试的会合点:两个桩实例都到场才放行,证明两流在时间上真实交叠。

    两个生成器都跑在 TestClient 门户的同一事件循环上,普通属性即线程安全。
    """

    def __init__(self):
        self.event: asyncio.Event | None = None  # 由首个到场的桩在事件循环上创建
        self.count = 0
        self.met: list[bool] = []

    def reset(self) -> None:
        self.__init__()


_MEET = _MeetState()

#: 乱序 n / 未知事件类型 / 缺字段 / 孤儿 tool.end / say 之后仍有事件 / 空类型:
#: 端点协议层必须逐行透传为合法 JSON,不重排不丢弃不裸断(消费端如何忽略是前端的事)
_WEIRD_SEQUENCE = [
    {"t": "agent.cycle", "n": 3, "max": 6, "action": "status"},
    {"t": "alien.event", "payload": {"deep": [1, {"x": "\u0000控\n制"}], "emoji": "🛰️"}},
    {"t": "agent.cycle", "n": 1, "action": "not_in_closed_set"},
    {"t": "tool.end", "id": "ghost-未配对", "exit": -1, "summary": ""},
    {"t": "say", "parts": ["提前 say"]},
    {"t": "", "": None},
    {"t": "note", "tone": "weird", "text": "尾随 note"},
]


async def _stub_converse_loop(self, *args, **kwargs):
    """契约 §4 形状的假 converse_loop(B1 并行开发中,端点协议层独立可测)。

    - 事件一律经 self._emit 走双通道(NDJSON 回合流 + SSE 总线,契约 §1),
      会话隔离测试依赖 SSE 侧镜像;
    - 剧本按 session 前缀选择,普通用例之间零共享状态(rob-gate / rob-meet
      例外:各自持有显式 reset 的模块级会合点);
    - 对调用形状保持宽容:text 取首个字符串位置参数或 kwargs(契约 §4 里
      max_cycles 是关键字专用,B5 按 converse_loop(text, max_cycles=...) 调用)。
    """
    text = kwargs.get("text")
    if text is None:
        text = next((a for a in args if isinstance(a, str)), "")
    mc = kwargs.get("max_cycles", 6)
    session = self.workspace.name
    _record(session, text, mc)

    if session.startswith("rob-boom"):
        # 中途爆炸:异常 message 里埋假密钥 —— 端点必须换成通用 note,细节零外泄
        yield self._emit({"t": "agent.cycle", "n": 1, "max": mc, "action": "status"})
        raise RuntimeError(f"桩内部爆炸 probe={_FAKE_KEY}")

    if session.startswith("rob-weird"):
        for ev in _WEIRD_SEQUENCE:
            yield self._emit(ev)
        return

    if session.startswith("rob-gate"):
        _GATE.loop = asyncio.get_running_loop()
        _GATE.event = asyncio.Event()
        yield self._emit({"t": "agent.cycle", "n": 1, "max": mc, "action": "status"})
        _GATE.opened.set()  # 首事件已入流 → 此刻起「回合进行中」
        try:
            await asyncio.wait_for(_GATE.event.wait(), timeout=30 * TIME_FACTOR)
        except TimeoutError:
            pass  # 防挂死兜底:测试线程放行丢失也能收尾
        yield self._emit({"t": "say", "parts": ["gate-done", text]})
        return

    if session.startswith("rob-meet"):
        if _MEET.event is None:
            _MEET.event = asyncio.Event()
        _MEET.count += 1
        if _MEET.count >= 2:
            _MEET.event.set()
        yield self._emit({"t": "agent.cycle", "n": 1, "max": mc, "action": "status"})
        try:
            await asyncio.wait_for(_MEET.event.wait(), timeout=8 * TIME_FACTOR)
            met = True
        except TimeoutError:
            met = False
        _MEET.met.append(met)
        yield self._emit({"t": "say", "parts": [text, "met" if met else "solo"]})
        return

    # 缺省剧本:≤2 个周期 + say 收束,text 原样回显(round-trip 断言的对手面)
    cycles = min(2, mc) if isinstance(mc, int) and mc >= 1 else 1
    for i in range(1, cycles + 1):
        yield self._emit({"t": "agent.cycle", "n": i, "max": mc, "action": "status"})
    yield self._emit({"t": "say", "parts": [text]})


# ---------------- 密封 fixtures ----------------


def _empty_probe(*args, **kwargs) -> ProbeResult:
    """密封 probe:空引擎、固定磁盘/CPU,签名吞掉 driver 与 setup 两处调用形态。"""
    return ProbeResult(
        engines={k: None for k in ("isce2", "mintpy", "snaphu", "gdal", "snap",
                                   "pystamps", "pyaps")},
        credentials={"earthdata": False, "cds": False, "gacos": False},
        disk_free_gb=100.0, cpu_count=8)


def _no_net(*args, **kwargs):
    raise AssertionError("测试禁止真实出网:urllib.request.urlopen 被 autouse 拦截(B11 密封纪律)")


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    """autouse 断网 + 探测密封:即使某个用例绕过 rob_ctx 也绝不出网/碰真实引擎。"""
    monkeypatch.setattr("insar_agent.loop.driver.probe_environment", _empty_probe)
    monkeypatch.setattr("insar_agent.runtime.wsl_probe.probe_wsl_engines",
                        lambda **kw: {"ok": False})
    monkeypatch.setattr("urllib.request.urlopen", _no_net)


@pytest.fixture(scope="module")
def rob_ctx(tmp_path_factory):
    """模块级密封 app(临时 INSAR_HOME)+ Driver.converse_loop 契约桩。

    模块级共享的理由与 test_api_fuzz.fuzz_ctx 相同:hypothesis 多例必须复用
    一个 app 才能把 fuzz 时间压进预算。raise_server_exceptions=False 让服务端
    未捕获异常落为「500 响应」(可对『永不 5xx』做断言),而非在测试进程重抛。
    """
    home = tmp_path_factory.mktemp("rob_home")
    mp = pytest.MonkeyPatch()
    mp.setenv("INSAR_HOME", str(home))  # 兜底:任何回落环境变量的路径也进临时目录
    mp.setattr("insar_agent.loop.driver.probe_environment", _empty_probe)
    mp.setattr("insar_agent.runtime.wsl_probe.probe_wsl_engines", lambda **kw: {"ok": False})
    mp.setattr("insar_agent.api.setup_router.probe_environment", _empty_probe)
    mp.setattr("urllib.request.urlopen", _no_net)
    # 类级替换:B1 尚未落地 converse_loop 也能打(raising=False),落地后照样覆盖
    mp.setattr(Driver, "converse_loop", _stub_converse_loop, raising=False)
    app = create_app(home=home)
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client, home
    finally:
        mp.undo()


@pytest.fixture()
def rob_live_server(tmp_path, monkeypatch):
    """真实 uvicorn(线程 + 随机高位端口,绝不触碰 8873)+ converse_loop 契约桩。

    只有 SSE 隔离用例用它:TestClient 的 ASGI 传输把整个响应缓冲后才返回
    (starlette testclient.handle_request 阻塞到 app 调用结束),无限的
    /api/events 流永远等不到 —— 必须打真实 socket(test_api_robustness.
    live_server 同法,其 docstring 即此结论的出处)。
    """
    import uvicorn

    home = tmp_path / "home"
    monkeypatch.setenv("INSAR_HOME", str(home))
    monkeypatch.setattr("insar_agent.loop.driver.probe_environment", _empty_probe)
    monkeypatch.setattr("insar_agent.runtime.wsl_probe.probe_wsl_engines",
                        lambda **kw: {"ok": False})
    monkeypatch.setattr("insar_agent.api.setup_router.probe_environment", _empty_probe)
    monkeypatch.setattr(Driver, "converse_loop", _stub_converse_loop, raising=False)
    app = create_app(home=home)
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning",
                            timeout_graceful_shutdown=3)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 20 * TIME_FACTOR
    while not server.started:
        if not thread.is_alive():
            raise RuntimeError("uvicorn 线程提前退出")
        if time.time() > deadline:
            raise RuntimeError(f"uvicorn 未在 {20 * TIME_FACTOR:g}s 内启动")
        time.sleep(0.05)
    port = server.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=15)


# ---------------- 请求与断言小工具 ----------------


@dataclass
class _Result:
    """一次 /api/converse 调用的观测结果(缓冲式读完整流)。"""
    status: int
    ctype: str
    text: str
    events: list[dict] = field(default_factory=list)
    #: None=流完整;否则是破坏原因(读流异常 / NDJSON 行不是合法 JSON)
    broke: str | None = None


def _post_converse(client: TestClient, json_body: dict | None = None, *,
                   content: bytes | None = None) -> _Result:
    kw: dict = ({"content": content, "headers": JSON_HEADERS}
                if content is not None else {"json": json_body})
    with client.stream("POST", "/api/converse", **kw) as resp:
        status = resp.status_code
        ctype = resp.headers.get("content-type", "")
        try:
            body = resp.read()
        except Exception as exc:  # 裸断连:读流中途传输层异常(协议红线)
            return _Result(status, ctype, "", [], f"读流异常:{type(exc).__name__}: {exc}")
    text = body.decode("utf-8", errors="replace")
    events: list[dict] = []
    broke = None
    if status == 200:
        # NDJSON 行分隔符只有 \n:JSON 字符串里允许携带裸 U+2028/U+2029/U+0085
        # (json.dumps 只转义 <0x20),str.splitlines 会误把它们当换行 → 不能用
        for line in text.split("\n"):
            if not line.strip():
                continue
            try:
                events.append(json.loads(line))
            except ValueError:
                broke = f"NDJSON 行不是合法 JSON:{line[:120]!r}"
                break
    return _Result(status, ctype, text, events, broke)


def _assert_rejected_before_stream(res: _Result, *, why: str = "") -> None:
    """非法输入的统一断言:400/422、响应非流式(application/json)、体可解析。"""
    assert res.status in (400, 422), (why, res.status, res.text[:200])
    assert res.ctype.startswith("application/json"), (why, res.ctype)
    json.loads(res.text)


def _mk_owned_run(home: Path, session: str, run_id: str) -> None:
    """直接落库一个归属 session 的 run(不跑任何计算)。

    注:/api/abort 已不再要求会话有 run(集成波次 P2:无 run 时置会话级取消
    token,仍 202)——保留造 run 是为了覆盖「有 run → control 位落在 runs 表」
    的原语义路径。与 test_api_surface_r3 同法:TestClient 存活期间另开一把
    Database 连同一库(WAL,幂等迁移)。
    """
    db = Database(home / "insar.db")
    store = Store(db)
    try:
        store.create_session(session, session)
        store.create_run(run_id, session, workspace=str(home / "sessions" / session))
    finally:
        db.close()


# ==================== 0. 基线:契约形状冒烟 ====================


def test_converse_happy_path_contract_shape(rob_ctx):
    """基线:200 + application/x-ndjson;事件按契约 §1 形状透传;say 收尾;
    text 与 max_cycles 原样抵达 converse_loop(桩账本对账)。"""
    client, _ = rob_ctx
    client.post("/api/sessions", json={"id": "rob-base"})
    marker = f"base-{next(_SEQ)}"
    res = _post_converse(client, {"session": "rob-base", "text": marker, "max_cycles": 4})
    assert res.status == 200 and res.broke is None, (res.status, res.broke, res.text[:200])
    assert res.ctype.startswith("application/x-ndjson"), res.ctype
    assert [e["t"] for e in res.events] == ["agent.cycle", "agent.cycle", "say"]
    first = res.events[0]
    assert first["n"] == 1 and first["max"] == 4 and "action" in first
    call = _single_call(marker)
    assert call == {"session": "rob-base", "text": marker, "max_cycles": 4}


# ==================== 1. 请求体模糊:max_cycles / text / session ====================


def test_max_cycles_boundary_matrix(rob_ctx):
    """契约 §7:1<=max_cycles<=12。越界/非整数(含布尔、可解析数字串、整值浮点)
    一律 400/422 且挡在开流前;缺省与显式 null 走配置缺省(本模块从不写
    agent_max_cycles → 契约默认 6);合法边界 1/12 原样透传。"""
    client, _ = rob_ctx
    for ok in (1, 12):
        marker = f"mc-ok-{next(_SEQ)}"
        res = _post_converse(client, {"session": "rob-bounds", "text": marker,
                                      "max_cycles": ok})
        assert res.status == 200 and res.broke is None, (ok, res.status, res.text[:200])
        assert _single_call(marker)["max_cycles"] == ok

    for extra in ({}, {"max_cycles": None}):
        marker = f"mc-default-{next(_SEQ)}"
        res = _post_converse(client, {"session": "rob-bounds", "text": marker, **extra})
        assert res.status == 200, (extra, res.status, res.text[:200])
        assert _single_call(marker)["max_cycles"] == 6

    for bad in (0, 13, -1, 10 ** 18, -(10 ** 18), 10 ** 100, 6.5, 6.0, "6", "abc",
                True, False, [6], {"n": 6}):
        marker = f"mc-bad-{next(_SEQ)}"
        res = _post_converse(client, {"session": "rob-bounds", "text": marker,
                                      "max_cycles": bad})
        _assert_rejected_before_stream(res, why=f"max_cycles={bad!r}")
        assert not _calls_with(marker), f"非法 max_cycles={bad!r} 不得触发 converse_loop"


@given(mc=st.one_of(st.integers(min_value=-(10 ** 24), max_value=10 ** 24),
                    st.sampled_from([0, 1, 6, 12, 13, -1, 2 ** 31, 2 ** 63, -(2 ** 63)])))
@settings(max_examples=40)
def test_fuzz_max_cycles_integer_domain(rob_ctx, mc):
    """整数全域性质:区间内原样透传成流;区间外 400/422 且绝不触发循环;永不 5xx。"""
    client, _ = rob_ctx
    marker = f"mc-int-{next(_SEQ)}"
    res = _post_converse(client, {"session": "rob-fuzz-mc", "text": marker,
                                  "max_cycles": mc})
    if 1 <= mc <= 12:
        assert res.status == 200 and res.broke is None, (mc, res.status, res.text[:200])
        assert _single_call(marker)["max_cycles"] == mc
    else:
        _assert_rejected_before_stream(res, why=f"max_cycles={mc}")
        assert not _calls_with(marker)


@given(mc=st.one_of(
    st.booleans(),
    st.floats(allow_nan=False, allow_infinity=False),
    st.text(alphabet=st.characters(codec="utf-8"), max_size=8),
    st.lists(st.integers(-5, 20), max_size=3),
    st.dictionaries(st.text(alphabet=st.characters(codec="utf-8"), max_size=4),
                    st.integers(), max_size=2)))
@settings(max_examples=30)
def test_fuzz_max_cycles_non_integer_rejected(rob_ctx, mc):
    """类型面性质:一切非整数形态(含整值浮点 6.0 / 布尔 / 可解析数字串)一律
    400/422,响应非流式,绝不触发循环 —— 契约口径:类型不做隐式弯折。"""
    client, _ = rob_ctx
    marker = f"mc-type-{next(_SEQ)}"
    res = _post_converse(client, {"session": "rob-fuzz-mc", "text": marker,
                                  "max_cycles": mc})
    _assert_rejected_before_stream(res, why=f"max_cycles={mc!r}")
    assert not _calls_with(marker)


#: 针对 text 的脏串抽样(无孤代理:hypothesis 侧由 codec="utf-8" 排除;
#: 孤代理只能经原始字节体构造,单列在 test_raw_body_* 用例)
_NASTY_TEXT = [
    "", " ", "\t", "\n", "\r\n", "\x00", "\x01", "\x1f", "\x7f", "\x9f",
    "\u202eevil", "a\u200bb", "\ufeff", "e\u0301\u0301", "🛰️",
    "𝔘𝔫𝔦𝔠𝔬𝔡𝔢", "中文回合目标", '{"t":"say","parts":["伪装事件"]}', "null", "NaN",
    "'; DROP TABLE runs;--", "${jndi:ldap://x}", "{{7*7}}", "x" * 4096,
]


@given(text=st.one_of(st.sampled_from(_NASTY_TEXT),
                      st.text(alphabet=st.characters(codec="utf-8"), max_size=300)))
@settings(max_examples=40)
def test_fuzz_text_roundtrip_never_5xx(rob_ctx, text):
    """text 任意 UTF-8 可编码内容(控制字符/RTL/零宽/JSON 伪装/超长):端点永不
    5xx;流逐行合法且 text 无损往返(桩账本与 say 事件双向对账)。"""
    client, _ = rob_ctx
    marker = f"⟦txt-{next(_SEQ)}⟧"
    payload = marker + text
    res = _post_converse(client, {"session": "rob-fuzz-text", "text": payload})
    assert res.status == 200 and res.broke is None, (res.status, res.broke, res.text[:200])
    say = res.events[-1]
    assert say["t"] == "say" and say["parts"] == [payload]
    assert _single_call(marker)["text"] == payload


def test_text_extremes_deterministic(rob_ctx):
    """text 极端确定性矩阵:空串 / 100KB 超长 / 控制字符簇 —— 不 5xx、不裸断、
    无损往返(空串口径与 /api/turn 一致:不是错误)。"""
    client, _ = rob_ctx
    # 空串:当前实现与 /api/turn 同口径放行(text: str 无最小长度)
    res_empty = _post_converse(client, {"session": "rob-extreme", "text": ""})
    assert res_empty.status == 200 and res_empty.broke is None
    assert res_empty.events[-1] == {"t": "say", "parts": [""]}

    # 100KB 超长:无截断无变形(长度与内容全等)
    marker = f"huge-{next(_SEQ)}"
    huge = marker + ("洪" * 20_000) + ("x" * 80_000)
    res_huge = _post_converse(client, {"session": "rob-extreme", "text": huge})
    assert res_huge.status == 200 and res_huge.broke is None
    got = _single_call(marker)["text"]
    assert len(got) == len(huge) and got == huge
    assert res_huge.events[-1]["parts"] == [huge]

    # 控制字符簇(C0/C1/DEL/ANSI 转义):逐行 JSON 合法,内容无损
    marker2 = f"ctrl-{next(_SEQ)}"
    ctrl = marker2 + "\x00\x01\x08\x0b\x1b[31m\x7f\x9f\u202e"
    res_ctrl = _post_converse(client, {"session": "rob-extreme", "text": ctrl})
    assert res_ctrl.status == 200 and res_ctrl.broke is None
    assert res_ctrl.events[-1]["parts"] == [ctrl]


#: 确定无疑非法的 session(契约 §7:同 /api/turn 口径 → check_session_id 400):
#: 路径穿越 / 分隔符与保留字符 / Windows 保留名 / 超长 / 控制字符 / 首尾空白点
_BAD_SESSIONS = [
    "", "../evil", "..\\evil", "..\\..\\evil", "a/b", "a\\b", "a:b", 'a"b',
    "a*b", "a?b", "a<b", "a>b", "a|b", "CON", "con", "com1", "NUL", "aux.log",
    "x" * 65, " lead", "trail ", "trail.", "a\x00b", "a\x1fb", "a\x7fb",
]


def test_session_malformed_rejected_before_stream(rob_ctx):
    """session 非法字符集:一律 400/422 非流式,绝不触发 converse_loop,
    也绝不在 home 之外落目录(穿越零落盘)。"""
    client, home = rob_ctx
    for sid in _BAD_SESSIONS:
        marker = f"sess-bad-{next(_SEQ)}"
        res = _post_converse(client, {"session": sid, "text": marker})
        _assert_rejected_before_stream(res, why=f"session={sid!r}")
        assert not _calls_with(marker), f"非法 session={sid!r} 不得触发 converse_loop"
    # 穿越攻击零落盘:home 之外与 sessions 之外都不允许出现 evil 目录
    assert not (home / "evil").exists()
    assert not (home.parent / "evil").exists()
    assert not (home / "sessions" / "evil").exists()


@given(session=st.one_of(
    st.sampled_from(_BAD_SESSIONS + ["ok-sess", "中文会话", "%2e%2e%2f", "a.b-c_d"]),
    st.text(alphabet=st.characters(codec="utf-8"), max_size=80)))
@settings(max_examples=40)
def test_fuzz_session_string_domain(rob_ctx, session):
    """session 全域性质:结果闭集 {200 合法, 400 非法};400 必须非流式 JSON 且
    不触发循环;永不 5xx。(%2e%2e%2f 这类无穿越语义的字面名属合法目录名。)"""
    client, _ = rob_ctx
    marker = f"sess-fuzz-{next(_SEQ)}"
    res = _post_converse(client, {"session": session, "text": marker})
    assert res.status in (200, 400), (session, res.status, res.text[:200])
    if res.status == 200:
        assert res.broke is None and res.events[-1]["t"] == "say"
    else:
        assert res.ctype.startswith("application/json"), res.ctype
        json.loads(res.text)
        assert not _calls_with(marker)


def test_raw_body_nonfinite_and_surrogate_session_rejected(rob_ctx):
    """只能经原始字节体构造的向量(合法 JSON 客户端发不出):非有限数 max_cycles
    (NaN/±Infinity/1e999)与孤代理 session → 一律 4xx 结构化 JSON,永不 5xx。"""
    client, _ = rob_ctx
    for raw in (rb'{"session": "rob-raw", "text": "t", "max_cycles": NaN}',
                rb'{"session": "rob-raw", "text": "t", "max_cycles": Infinity}',
                rb'{"session": "rob-raw", "text": "t", "max_cycles": -Infinity}',
                rb'{"session": "rob-raw", "text": "t", "max_cycles": 1e999}',
                rb'{"session": "s\ud834x", "text": "t"}'):
        res = _post_converse(client, content=raw)
        _assert_rejected_before_stream(res, why=repr(raw[:60]))


def test_raw_body_surrogate_text_stream_safety(rob_ctx):
    """孤代理进 text(未配对代理对,只能经原始字节体注入):安全闭集 =
    开流前 4xx 拒绝,或 200 且流内安全处理(逐行合法 + say/note 收尾)。
    绝不允许:裸断连 / 无收尾静默截断。"""
    client, _ = rob_ctx
    res = _post_converse(
        client, content=rb'{"session": "rob-surr", "text": "a\ud800b", "max_cycles": 2}')
    if res.status != 200:
        _assert_rejected_before_stream(res, why="surrogate text")
        return
    assert res.broke is None, f"孤代理 text 导致流裸断:{res.broke}"
    assert res.events and res.events[-1].get("t") in ("say", "note"), \
        f"孤代理 text 导致回合无收尾(静默截断):{[e.get('t') for e in res.events]}"


# ==================== 2. 事件流不变量(乱序/未知类型/中途异常) ====================


def test_stream_unknown_and_out_of_order_events_passthrough(rob_ctx):
    """桩发出乱序 n / 未知事件类型 / 缺字段 / 孤儿 tool.end / say 后续发:
    端点逐行透传为独立合法 JSON,保序保内容,不重排不丢弃不裸断
    (未知类型如何忽略是消费端 app.js 的事,协议层只负责保真)。"""
    client, _ = rob_ctx
    res = _post_converse(client, {"session": "rob-weird", "text": "chaos"})
    assert res.status == 200 and res.broke is None, (res.status, res.broke)
    assert res.events == _WEIRD_SEQUENCE


def test_stream_generator_exception_ends_with_note(rob_ctx):
    """循环生成器中途抛异常:已发事件保留,流以 note 收尾(契约 §1:错误是事件
    不是裸断连),异常 message(内埋密钥探针)绝不透传给客户端。"""
    client, _ = rob_ctx
    res = _post_converse(client, {"session": "rob-boom", "text": "explode"})
    assert res.status == 200 and res.broke is None, (res.status, res.broke)
    kinds = [e.get("t") for e in res.events]
    assert kinds and kinds[0] == "agent.cycle" and kinds[-1] == "note", kinds
    assert "probe=" not in res.text and _FAKE_KEY not in res.text


# ==================== 3. 会话隔离(converse 事件 × /api/events SSE) ====================


def _drain_live(http: httpx.Client, session: str, text: str) -> list[dict]:
    """经真实 socket 跑一个完整 converse 回合并要求成功,返回事件列表。"""
    r = http.post("/api/converse", json={"session": session, "text": text})
    assert r.status_code == 200, (session, r.status_code, r.text[:200])
    return [json.loads(ln) for ln in r.text.split("\n") if ln.strip()]


@pytest.mark.timing
def test_converse_events_isolated_between_sessions(rob_live_server):
    """A 会话 converse 的事件绝不出现在 B 会话的 /api/events SSE(真实 socket)。

    时序设计(免 15s keepalive、零固定长 sleep):
      1. 后台线程订阅 B 的 SSE(读超时兜底,线程必然自行退出);
      2. 反复投递 B 的 hello 回合直到 SSE 收到(订阅生效握手,EventBus 无回放);
      3. 跑 A 的 converse(桩经 self._emit 把事件镜像进 A 自己的总线);
      4. 投递 B 的哨兵回合,SSE 线程读到哨兵即停 —— A 的事件若会串台,
         必然先于哨兵入队(各总线 FIFO),故「见哨兵不见 A 标记」即隔离成立。
    """
    base = rob_live_server
    a_marker = f"iso-a-secret-{next(_SEQ)}"
    sentinel = f"iso-b-done-{next(_SEQ)}"
    sse: list[dict] = []
    stopped = threading.Event()

    def reader():
        try:
            with httpx.stream("GET", base + "/api/events",
                              params={"session": "rob-iso-b"},
                              timeout=httpx.Timeout(10 * TIME_FACTOR,
                                                    read=60 * TIME_FACTOR)) as resp:
                for raw in resp.iter_lines():
                    if not raw.startswith("data: "):
                        continue
                    ev = json.loads(raw[len("data: "):])
                    sse.append(ev)
                    if ev.get("t") == "say" and sentinel in str(ev.get("parts")):
                        return
        except httpx.HTTPError:
            pass  # 读超时/断连兜底:线程自行退出,由主线程断言定失败
        finally:
            stopped.set()

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    with httpx.Client(base_url=base, timeout=30 * TIME_FACTOR) as http:
        # 握手:订阅生效前发布的事件会丢,反复发 hello 直到 SSE 收到任意一条
        deadline = time.time() + 20 * TIME_FACTOR
        subscribed = False
        while time.time() < deadline and not subscribed:
            _drain_live(http, "rob-iso-b", f"iso-b-hello-{next(_SEQ)}")
            time.sleep(0.05)
            subscribed = any("iso-b-hello" in json.dumps(e, ensure_ascii=False)
                             for e in sse)
        assert subscribed, "B 的 SSE 订阅在判定窗内未生效"

        a_events = _drain_live(http, "rob-iso-a", a_marker)
        assert any(a_marker in json.dumps(e, ensure_ascii=False) for e in a_events), \
            "A 自己的 NDJSON 流应包含自己的事件(正向对照)"

        _drain_live(http, "rob-iso-b", sentinel)
    assert stopped.wait(30 * TIME_FACTOR), "SSE 线程未在判定窗内读到哨兵"
    t.join(timeout=10 * TIME_FACTOR)
    assert not t.is_alive()
    leaked = [e for e in sse if a_marker in json.dumps(e, ensure_ascii=False)]
    assert not leaked, f"A 会话事件串入 B 的 SSE:{leaked!r}"


# ==================== 4. 并发(同会话 / 不同会话) ====================


@pytest.mark.timing
def test_same_session_concurrent_converse_semantics(rob_ctx):
    """同会话并发两个 converse(Barrier 同发)→ 行为确定。

    语义注释:LOOP-CONTRACT §7 未规定同会话互斥;当前 B5 实现无锁,ndjson 泵
    每请求独立 —— 期望语义 = 并发放行,两请求各得独立完整流(桩内会合点
    _MEET.met == [True, True] 证明两生成器在时间上真实交叠)。若集成波次改成
    「串行」(会合点将呈 [False, True])或「明确拒绝」(其一非 200),本用例
    会在对应断言失败,提醒重审语义并更新此处。绝不允许的结果:5xx、流截断、
    跨流串台。
    """
    client, _ = rob_ctx
    _MEET.reset()
    barrier = threading.Barrier(2)
    results: list[_Result | None] = [None, None]

    def hit(i: int):
        barrier.wait()
        results[i] = _post_converse(
            client, {"session": "rob-meet", "text": f"meet-req-{i}", "max_cycles": 3})

    threads = [threading.Thread(target=hit, args=(i,), daemon=True) for i in range(2)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=60 * TIME_FACTOR)
    assert not any(th.is_alive() for th in threads), "并发 converse 请求未在判定窗内返回"

    for i, res in enumerate(results):
        assert res is not None
        assert res.status == 200 and res.broke is None, (i, res.status, res.broke)
        say = res.events[-1]
        assert say["t"] == "say" and f"meet-req-{i}" in say["parts"], (i, say)
        other = f"meet-req-{1 - i}"
        assert all(other not in json.dumps(e, ensure_ascii=False) for e in res.events), \
            f"流 {i} 串入了另一请求的事件"
    assert _MEET.count == 2, "两个生成器都必须真实运行(无静默丢请求)"
    assert _MEET.met == [True, True], f"并发放行语义下两流应真实交叠,实得 {_MEET.met}"


@pytest.mark.timing
def test_distinct_sessions_concurrent_no_crosstalk(rob_ctx):
    """不同会话并发 converse:互不干扰,各自 NDJSON 流只含自己的事件。"""
    client, _ = rob_ctx
    sessions = ("rob-par-a", "rob-par-b")
    markers = {s: f"par-{s}-{next(_SEQ)}" for s in sessions}
    barrier = threading.Barrier(len(sessions))
    results: dict[str, _Result] = {}
    lock = threading.Lock()

    def hit(sess: str):
        barrier.wait()
        r = _post_converse(client, {"session": sess, "text": markers[sess]})
        with lock:
            results[sess] = r

    threads = [threading.Thread(target=hit, args=(s,), daemon=True) for s in sessions]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=60 * TIME_FACTOR)
    assert not any(th.is_alive() for th in threads)

    for sess in sessions:
        res = results[sess]
        other = markers[sessions[1] if sess == sessions[0] else sessions[0]]
        assert res.status == 200 and res.broke is None, (sess, res.status, res.broke)
        assert res.events[-1] == {"t": "say", "parts": [markers[sess]]}
        assert all(other not in json.dumps(e, ensure_ascii=False) for e in res.events), \
            f"{sess} 的流串入了另一会话的事件"


# ==================== 5. 取消风暴(/api/abort 幂等) ====================


def _abort_storm(client: TestClient, session: str, n: int) -> list[tuple[int, object]]:
    """Barrier 同发 n 个 /api/abort,返回 (状态码, 载荷) 列表。"""
    barrier = threading.Barrier(n)
    outcomes: list[tuple[int, object]] = []
    lock = threading.Lock()

    def hit():
        barrier.wait()
        r = client.post("/api/abort", json={"session": session})
        payload = r.json() if r.status_code == 202 else r.text[:200]
        with lock:
            outcomes.append((r.status_code, payload))

    threads = [threading.Thread(target=hit, daemon=True) for _ in range(n)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=30 * TIME_FACTOR)
    assert not any(th.is_alive() for th in threads), "abort 风暴线程未收敛"
    return outcomes


@pytest.mark.timing
def test_abort_storm_idempotent_202(rob_ctx):
    """回合进行中(桩停在闸门上)连发 /api/abort:全部幂等 202、无 500;
    回合结束后再风暴一轮仍 202(control 位重复置位无害,风暴不打断流本身,
    取消的循环语义由 B1 在周期边界响应,不属协议层)。

    实现注记:TestClient 会把流式响应整体缓冲(handle_request 阻塞到回合结束),
    故 converse 由工作线程发出;主线程凭 _GATE.opened(桩已吐出首事件后置位)
    确认「回合确实在途」再打风暴 —— 风暴期间闸门未放行,回合不可能已结束。
    语义注记:/api/abort 对有 run 的会话仍经 resolve_run 置 control 位(落在
    runs 表);本用例落库一个归属 run 走该路径(无 run 会话的会话级取消口径
    见 test_api_loop 的 ⑧,集成波次 P2)。
    """
    client, home = rob_ctx
    _mk_owned_run(home, "rob-gate", "r-rob-gate")
    _GATE.reset()
    holder: list[_Result] = []

    def run_converse():
        holder.append(_post_converse(
            client, {"session": "rob-gate", "text": "gated", "max_cycles": 2}))

    worker = threading.Thread(target=run_converse, daemon=True)
    worker.start()
    try:
        assert _GATE.opened.wait(20 * TIME_FACTOR), "桩未进入回合(闸门未就位)"
        outcomes = _abort_storm(client, "rob-gate", 8)
    finally:
        _GATE.release()  # 无论风暴成败都放行,绝不留挂起回合拖死模块 teardown
    worker.join(timeout=60 * TIME_FACTOR)
    assert not worker.is_alive(), "converse 回合未在判定窗内收尾"

    for code, payload in outcomes:
        assert code == 202, (code, payload)
        assert payload == {"accepted": True}
    res = holder[0]
    assert res.status == 200 and res.broke is None, (res.status, res.broke)
    kinds = [e.get("t") for e in res.events]
    assert kinds and kinds[0] == "agent.cycle" and kinds[-1] == "say", kinds
    # 回合已结束:再来一轮取消风暴,仍幂等 202
    for code, payload in _abort_storm(client, "rob-gate", 4):
        assert code == 202, (code, payload)
        assert payload == {"accepted": True}


# ==================== 6. 泄漏面(假密钥全链路 grep) ====================


def test_no_key_leak_over_converse_chain(rob_ctx):
    """llm.json 放假密钥后,converse 全链路的每一种响应(成功流 / 循环异常流 /
    各类错误响应 / 原始字节向量 / 配置回显)全文 grep 不得出现明文密钥
    (test_api_surface_r3:43,115-131 同款最高优先级红线)。"""
    client, home = rob_ctx
    r = client.post("/api/llm/config", json={
        "base_url": "https://api.example/v1", "api_key": _FAKE_KEY, "chat_model": "m"})
    assert r.status_code == 200 and _FAKE_KEY not in r.text
    assert _FAKE_KEY in (home / "llm.json").read_text(encoding="utf-8")  # 前提:密钥确已落盘

    corpus: list[str] = [r.text]
    # 新会话首次 converse:driver_of 以带密钥的路由构建 brain(密钥真实进入进程)
    res_ok = _post_converse(client, {"session": "rob-leak", "text": "leak probe"})
    assert res_ok.status == 200 and res_ok.broke is None
    corpus.append(res_ok.text)
    # 循环异常:异常 message 里埋着密钥,note 收尾也不得回显(rob-boom 桩)
    res_boom = _post_converse(client, {"session": "rob-boom", "text": "leak boom"})
    assert res_boom.status == 200
    corpus.append(res_boom.text)
    # 错误响应族:非法 max_cycles / 非法 session / 缺 text(422)/ 原始字节 NaN
    corpus.append(client.post("/api/converse", json={
        "session": "rob-leak", "text": "t", "max_cycles": 99}).text)
    corpus.append(client.post("/api/converse", json={"session": "../bad", "text": "t"}).text)
    corpus.append(client.post("/api/converse", json={"session": "rob-leak"}).text)
    corpus.append(client.post(
        "/api/converse", headers=JSON_HEADERS,
        content=rb'{"session": "rob-leak", "text": "t", "max_cycles": NaN}').text)
    view = client.get("/api/llm/config")
    corpus.append(view.text)

    blob = "\n".join(corpus)
    assert _FAKE_KEY not in blob, "明文密钥泄漏进 converse 链路的某个响应"
    assert "…" in view.json()["api_key_masked"]  # 掩码形态仍在(密钥确实被系统持有)
