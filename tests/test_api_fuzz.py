"""API 模糊测试(hypothesis + TestClient):字符串 / JSON 结构 / NDJSON 流三类输入面。

唯一硬验收标准:任何输入都不得触发 5xx —— 非法输入必须 4xx(结构化 JSON 体),
合法输入 2xx;NDJSON 流端点要么开流前 4xx 拒绝(JSON 体),要么每行独立可解析。

纪律(与本波「模糊 + 并发压力」任务约束一致):
  - 黑盒·只读:不改 api/*.py。模糊过程中命中的服务端 5xx 用 xfail 回归钉死
    (见文件末「已知缺陷」段),最小复现 + 建议修法写进本波报告。
  - 全平面 unicode 经 codec="utf-8" 生成(天然排除孤代理 U+D800–U+DFFF):真实
    HTTP 客户端也无法把孤代理编成 UTF-8 字节,故孤代理不属于「经 json= 可达」的
    服务端输入面;孤代理 / 非有限数(NaN/Infinity)只能经「原始字节体」构造,
    单列在「已知缺陷」段作为服务端向量。
  - 时间预算:合计 fuzz 控制在 <120s —— 每个 property 设 max_examples 上限,
    deadline=None(Windows 首调抖动不误伤合法用例),hypothesis 示例库关闭
    (database=None)保持工作树干净;流端点(turn/pipeline)每例较贵,单列低配额。
"""

from __future__ import annotations

import json
from unittest import mock

import pytest
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from insar_agent.api.app import create_app
from insar_agent.core.actions import ACTIONS
from insar_agent.core.store import DELIVER_AS
from insar_agent.runtime.probe import ProbeResult

# 短促、无副作用库、放宽健康检查(模块级 fixture + 首例较慢是本测试的正常形态)
settings.register_profile(
    "api_fuzz",
    deadline=None,
    database=None,
    max_examples=50,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
settings.load_profile("api_fuzz")

JSON_HEADERS = {"content-type": "application/json"}


def _empty_probe(*args, **kwargs) -> ProbeResult:
    """密封 probe:空引擎、固定磁盘/CPU,签名吞掉 driver 与 setup 两处不同调用形态。"""
    return ProbeResult(
        engines={k: None for k in ("isce2", "mintpy", "snaphu", "gdal", "snap",
                                   "pystamps", "pyaps")},
        credentials={"earthdata": False, "cds": False, "gacos": False},
        disk_free_gb=100.0, disk_total_gb=200.0, cpu_count=8)


@pytest.fixture(scope="module")
def fuzz_ctx(tmp_path_factory):
    """模块级共享:密封 app + 一个已规划出步骤的 demo run(impact/fork 需要 run)。

    raise_server_exceptions=False —— 让服务端未捕获异常落为「500 响应」而非在测试
    进程里重抛,便于对「永不 5xx」做断言并读取响应体。
    """
    home = tmp_path_factory.mktemp("fuzz_home")
    with mock.patch("insar_agent.loop.driver.probe_environment", _empty_probe), \
         mock.patch("insar_agent.runtime.wsl_probe.probe_wsl_engines",
                    lambda **kw: {"ok": False}), \
         mock.patch("insar_agent.api.setup_router.probe_environment", _empty_probe):
        app = create_app(home=home)
        with TestClient(app, raise_server_exceptions=False) as client:
            client.post("/api/sessions", json={"id": "demo"})
            # 仅「规划」(turn)即可产出带 11 个步骤的 run;不执行 pipeline(省时,
            # impact/fork 只需步骤存在)。消费完整个流确保 run 已落库。
            with client.stream("POST", "/api/turn",
                               json={"session": "demo", "text": "Ridgecrest 地震同震"}) as r:
                for _ in r.iter_lines():
                    pass
            run_id = client.get("/api/state", params={"session": "demo"}).json()["run"]["run_id"]
            yield client, run_id


# ---------------- 生成策略 ----------------

# UTF-8 可编码全平面(排除孤代理);含 C0/C1 控制、组合、格式(RTL/零宽)等类别
_UNICODE = st.characters(codec="utf-8")
_SHORT_TEXT = st.text(alphabet=_UNICODE, max_size=32)
_TEXT = st.text(alphabet=_UNICODE, max_size=200)

#: 针对性「脏串」:穿越/分隔符/保留名/首尾空白点/控制/RTL/零宽/组合/BOM/超长/emoji 簇/JSON 字面量伪装
NASTY = [
    "", " ", "  ", "\t", "\n", "\r\n", ".", "..", "...", "./", ".\\",
    "../evil", "..\\evil", "....//evil", "a/b", "a\\b", "a:b", 'a"b',
    "a*b", "a?b", "a<b", "a>b", "a|b", "%2e%2e%2f", "\x00", "\x01", "\x1f",
    "\x7f", "\x80", "\x9f", "\xa0", "CON", "con", "NUL", "COM1", "COM1.txt",
    "LPT9", "aux.log", "   leading", "trailing   ", "trailing.", "trailing ",
    "\u202eevil", "a\u200bb", "e\u0301\u0301\u0301", "\ufeff", "\u200d",
    "\U0001f600", "\U0001f469\u200d\U0001f467", "\U0001f1e8\U0001f1f3",
    "中文会话-01", "𝔘𝔫𝔦𝔠𝔬𝔡𝔢", "x" * 64, "x" * 65, "x" * 4096,
    "null", "true", "false", "0", "-1", "NaN", "Infinity", "1e999",
    "'; DROP TABLE sessions;--", "${jndi:ldap://x}", "{{7*7}}", "../../../etc/passwd",
]
_STRINGY = st.one_of(st.sampled_from(NASTY), _SHORT_TEXT)
_TEXTY = st.one_of(st.sampled_from(NASTY), _TEXT)

#: 数值边界(全部有限:非有限数无法经 httpx json= 送出,单列已知缺陷段)
_NUM_EDGE = st.sampled_from([
    0, -0.0, 1, -1, 2 ** 31, -2 ** 31, 2 ** 31 - 1, 2 ** 53, 2 ** 63, 2 ** 63 - 1,
    -2 ** 63, 2 ** 64, 10 ** 30, -10 ** 30, 1.5, -1.5, 1e308, -1e308, 5e-324, 3.14159,
])

#: 任意 JSON 值(有限数、大整数、无孤代理串、嵌套 list/dict);控制递归深度省时
_JSON = st.recursive(
    st.one_of(
        st.none(), st.booleans(),
        st.integers(),
        st.floats(allow_nan=False, allow_infinity=False),
        _NUM_EDGE,
        st.text(alphabet=_UNICODE, max_size=16),
    ),
    lambda children: st.one_of(
        st.lists(children, max_size=4),
        st.dictionaries(st.text(alphabet=_UNICODE, max_size=8), children, max_size=4),
    ),
    max_leaves=12,
)

_ACTION_NAMES = st.one_of(
    st.sampled_from([*ACTIONS, "USER_MESSAGE", "", "kill", "set_method",
                     "DROP TABLE", "RESET\n", "  SKIP  "]),
    _SHORT_TEXT,
)
_DELIVER = st.one_of(st.sampled_from([*DELIVER_AS, "", "bogus", "STEER", "steer "]),
                     _SHORT_TEXT)
_SCOPE = st.one_of(st.sampled_from(["step", "run", "global", "", "STEP"]), _SHORT_TEXT)
_TARGET = st.one_of(
    st.sampled_from(["6", "7", "9", "0", "-1", "12", "999", "abc", "6.0", "", " 6"]),
    st.integers(min_value=-5, max_value=30).map(str),
    _SHORT_TEXT,
)


def _assert_no_5xx_json(resp) -> None:
    """非流式端点通用断言:状态 <500,且响应体是合法 JSON。"""
    assert resp.status_code < 500, (resp.status_code, resp.text[:400])
    # 这些端点无论成功/报错都应回 JSON(成功体或 {"detail": ...})
    resp.json()


# ---------------- 1. 字符串输入面 ----------------

@given(session=_STRINGY)
@settings(max_examples=50)
def test_fuzz_session_string_query_endpoints(fuzz_ctx, session):
    """会话串打查询类端点:registry/state/env/chat/impact —— 永不 5xx,体为 JSON。"""
    client, _ = fuzz_ctx
    for url in ("/api/registry", "/api/state", "/api/env", "/api/chat"):
        _assert_no_5xx_json(client.get(url, params={"session": session}))
    # impact 还需 step;非法会话在解析 run 前就该 4xx,不得 5xx
    _assert_no_5xx_json(
        client.get("/api/impact", params={"session": session, "step": 7}))


@given(sid=_STRINGY,
       name=st.one_of(st.none(), _TEXTY),
       mode=st.one_of(st.none(), _SHORT_TEXT))
@settings(max_examples=40)
def test_fuzz_sessions_post(fuzz_ctx, sid, name, mode):
    """建会话:任意 id/name/mode —— 合法 200、非法 4xx,永不 5xx。"""
    client, _ = fuzz_ctx
    body: dict = {"id": sid}
    if name is not None:
        body["name"] = name
    if mode is not None:
        body["mode"] = mode
    _assert_no_5xx_json(client.post("/api/sessions", json=body))


@given(session=st.one_of(st.sampled_from(["demo", "ghost", ""]), _STRINGY),
       text=st.one_of(st.none(), _TEXTY),
       deliver_as=st.one_of(st.none(), _DELIVER))
@settings(max_examples=40)
def test_fuzz_message_post(fuzz_ctx, session, text, deliver_as):
    """消息投递:未知会话 404、坏 deliver_as 400、缺字段 422 —— 永不 5xx。"""
    client, _ = fuzz_ctx
    body: dict = {"session": session}
    if text is not None:
        body["text"] = text
    if deliver_as is not None:
        body["deliver_as"] = deliver_as
    _assert_no_5xx_json(client.post("/api/message", json=body))


# ---------------- 2. JSON 负载结构 fuzz ----------------

@given(session=st.one_of(st.sampled_from(["act", "demo", ""]), _SHORT_TEXT),
       scope=_SCOPE, target=_TARGET, action=_ACTION_NAMES,
       payload=st.one_of(st.dictionaries(st.text(alphabet=_UNICODE, max_size=10),
                                         _JSON, max_size=5),
                         _JSON),
       deliver_as=_DELIVER,
       run_id=st.one_of(st.none(), _SHORT_TEXT))
@settings(max_examples=70)
def test_fuzz_actions_body(fuzz_ctx, session, scope, target, action, payload,
                           deliver_as, run_id):
    """干预入队:闭集/形状/target 类型/payload 结构全模糊 —— 坏动作一律 4xx,永不 5xx。

    payload 亦可为非 dict(pydantic 该字段要求 dict → 422),覆盖类型混乱面。
    """
    client, _ = fuzz_ctx
    body: dict = {"session": session, "scope": scope, "target": target,
                  "action": action, "payload": payload, "deliver_as": deliver_as}
    if run_id is not None:
        body["run_id"] = run_id
    _assert_no_5xx_json(client.post("/api/actions", json=body))


@given(step=st.one_of(st.integers(), _NUM_EDGE),
       method=st.one_of(st.none(), _SHORT_TEXT),
       params=st.one_of(st.none(), _TEXTY, _JSON.map(lambda v: json.dumps(v))))
@settings(max_examples=60)
def test_fuzz_impact_params(fuzz_ctx, step, method, params):
    """预览影响:step 越界 422、method 不存在 400、params 非 JSON/非对象 400 —— 永不 5xx。

    params 分支之一是「合法 JSON 文本」(含 NaN 之外的数值边界/深嵌套):走进
    preview_change→refresh_run→规范化哈希,验证怪异但合法的参数值不炸端点。
    """
    client, _ = fuzz_ctx
    query: dict = {"session": "demo", "step": step}
    if method is not None:
        query["method"] = method
    if params is not None:
        query["params"] = params
    _assert_no_5xx_json(client.get("/api/impact", params=query))


_CHANGE_VALUE = st.dictionaries(
    keys=st.sampled_from(["method", "params", "bogus"]),
    values=st.one_of(_SHORT_TEXT, _JSON,
                     st.dictionaries(st.text(alphabet=_UNICODE, max_size=8),
                                     _JSON, max_size=3)),
    max_size=3,
)


@given(run_id=st.sampled_from(["__DEMO__", "ghost", "../../etc/passwd", ""]),
       changes=st.dictionaries(
           keys=st.one_of(st.sampled_from(["6", "7", "9", "0", "99", "-1", "9.5",
                                           "abc", ""]),
                          st.integers(min_value=-5, max_value=20).map(str)),
           values=st.one_of(_CHANGE_VALUE, st.none(), st.integers(), _SHORT_TEXT),
           max_size=3))
@settings(max_examples=40)
def test_fuzz_fork_body(fuzz_ctx, run_id, changes):
    """分叉:坏 run_id 404、非 int 键 422、非 dict 值 422、坏方法/参数 400 —— 永不 5xx。"""
    client, demo_run = fuzz_ctx
    real_run = demo_run if run_id == "__DEMO__" else run_id
    _assert_no_5xx_json(
        client.post("/api/fork", json={"session": "demo", "run_id": real_run,
                                       "changes": changes}))


@given(session=st.one_of(st.sampled_from(["ghost", "", "pf", "../bad"]), _SHORT_TEXT),
       run_id=st.one_of(st.none(), _SHORT_TEXT),
       step_ids=st.one_of(st.none(), st.lists(st.integers(), max_size=4),
                          _JSON))
@settings(max_examples=40)
def test_fuzz_pipeline_body_validation(fuzz_ctx, session, run_id, step_ids):
    """pipeline 开流前的体校验:step_ids 非 int 列表 422、无 run 404 —— 永不 5xx。

    只打「无 run 的会话」并强制排除 demo:合法 body 对无 run 会话仅回一条 note 事件
    (廉价),彻底规避整链重算。缓冲式 POST 读回响应即可。
    """
    client, _ = fuzz_ctx
    if session == "demo":
        session = "demo_x"  # demo 有 run,step_ids=None 会触发执行 —— 规避
    body: dict = {"session": session}
    if run_id is not None:
        body["run_id"] = run_id
    if step_ids is not None:
        body["step_ids"] = step_ids
    resp = client.post("/api/pipeline", json=body)
    assert resp.status_code < 500, (resp.status_code, resp.text[:400])
    if resp.status_code != 200:
        json.loads(resp.content)  # 4xx 体必须是 JSON


def test_pipeline_bad_step_ids_with_run_rejected_before_stream(fuzz_ctx):
    """确定性补充:run 存在 + 越界/非计划内 step_ids,必须开流前 400(JSON),不执行。"""
    client, _ = fuzz_ctx
    for bad in ([999], [-1], [0], [7, 999], list(range(1, 50))):
        resp = client.post("/api/pipeline", json={"session": "demo", "step_ids": bad})
        assert resp.status_code == 400, (bad, resp.status_code, resp.text[:200])
        assert "detail" in resp.json()


# ---------------- 3. NDJSON 流端点 ----------------

def _assert_stream_ok(client, url, body):
    """流端点通用断言:开流前 4xx(JSON 体)或 200 且每行独立可解析。"""
    with client.stream("POST", url, json=body) as resp:
        assert resp.status_code < 500, (resp.status_code,)
        if resp.status_code != 200:
            json.loads(resp.read())  # 拒绝路径:结构化 JSON
            return
        assert resp.headers["content-type"].startswith("application/x-ndjson")
        for line in resp.iter_lines():
            if line.strip():
                event = json.loads(line)  # 每行必须是独立合法 JSON
                assert isinstance(event, dict) and "t" in event


@given(session=st.one_of(st.sampled_from(["sf", "sf", "sf", "", "ghost", "../bad", "a/b"]),
                         _SHORT_TEXT),
       text=st.one_of(st.text(alphabet=_UNICODE, min_size=1, max_size=40),
                      st.sampled_from(["Ridgecrest 地震", "洛杉矶 形变", "hi"]),
                      st.none()),
       extra=st.one_of(st.none(), _JSON))
@settings(max_examples=12)
def test_fuzz_turn_stream(fuzz_ctx, session, text, extra):
    """/api/turn:缺 text 422、坏会话 400,否则规划流每行可解析(planning-only,较省时)。

    session 权重偏向合法(多个 "sf")、text 多数非空 —— 保证少量用例真正开流,
    让「每行独立可解析」的 200 分支被实打实覆盖(而非清一色 4xx)。
    """
    client, _ = fuzz_ctx
    body: dict = {"session": session}
    if text is not None:
        body["text"] = text
    if extra is not None:
        body["unexpected"] = extra  # 多余字段:pydantic 默认忽略,不应 5xx
    _assert_stream_ok(client, "/api/turn", body)


@given(session=st.one_of(st.sampled_from(["", "ghost", "../bad", "a\\b", "CON"]),
                         _SHORT_TEXT),
       run_id=st.one_of(st.none(), _SHORT_TEXT),
       step_ids=st.one_of(st.none(), st.lists(st.integers(-3, 20), max_size=4)))
@settings(max_examples=12)
def test_fuzz_pipeline_stream(fuzz_ctx, session, run_id, step_ids):
    """/api/pipeline 与 /api/resume 流:仅打「无 run 的会话」避免触发整链重算。

    无 run 时:带 step_ids → 404(JSON);不带 → 200 且流内是一条 note 事件。
    """
    client, _ = fuzz_ctx
    # 保证 session 不是 demo(demo 有 run,合法 step_ids 会触发执行);随机串若等于
    # demo 则改写,彻底规避重算。
    if session == "demo":
        session = "demo_x"
    body: dict = {"session": session}
    if run_id is not None:
        body["run_id"] = run_id
    if step_ids is not None:
        body["step_ids"] = step_ids
    _assert_stream_ok(client, "/api/pipeline", body)
    _assert_stream_ok(client, "/api/resume", {"session": session})


# ---------------- 4. content-type / charset 体检 ----------------

def test_content_type_and_charset(fuzz_ctx):
    """全端点响应头体检:JSON 端点 application/json;文本端点显式 charset=utf-8;
    NDJSON 流 application/x-ndjson。任一不符即在此暴露(现状应全部正确)。"""
    client, demo_run = fuzz_ctx

    json_calls = [
        ("GET", "/api/health", {}),
        ("GET", "/api/sessions", {}),
        ("GET", "/api/registry", {"params": {"session": "demo"}}),
        ("GET", "/api/state", {"params": {"session": "demo"}}),
        ("GET", "/api/env", {"params": {"session": "demo"}}),
        ("GET", "/api/chat", {"params": {"session": "demo"}}),
        ("GET", "/api/trace", {"params": {"session": "demo"}}),
        ("GET", "/api/provenance", {"params": {"session": "demo"}}),
        ("GET", "/api/impact", {"params": {"session": "demo", "step": 7}}),
        ("GET", "/api/version", {}),
        ("GET", "/api/version/check", {}),
        ("GET", "/api/setup/status", {}),
        ("GET", "/api/admin/runs", {}),
        ("POST", "/api/abort", {"json": {"session": "demo", "run_id": "nope"}}),  # 404 JSON
    ]
    for method, url, kwargs in json_calls:
        resp = client.request(method, url, **kwargs)
        assert resp.status_code < 500, (url, resp.status_code, resp.text[:200])
        ctype = resp.headers.get("content-type", "")
        assert ctype.startswith("application/json"), (url, ctype)
        resp.json()

    # 纯文本导出端点:必须显式 charset=utf-8(含中文,charset 缺失会被浏览器误判)
    for url in ("/api/run.sh", "/api/methods.md"):
        resp = client.get(url, params={"session": "demo"})
        assert resp.status_code < 500, (url, resp.status_code)
        if resp.status_code == 200:
            ctype = resp.headers.get("content-type", "").lower()
            assert ctype.startswith("text/plain") and "charset=utf-8" in ctype, (url, ctype)

    # NDJSON 回合流:application/x-ndjson(UTF-8 by spec,无需 charset 参数);
    # 顺带把「每行独立可解析」的不变式钉在一条真实多事件流上(确定性覆盖)。
    with client.stream("POST", "/api/turn",
                       json={"session": "demo", "text": "Ridgecrest 地震同震"}) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/x-ndjson"), \
            resp.headers.get("content-type")
        seen = 0
        for line in resp.iter_lines():
            if line.strip():
                event = json.loads(line)
                assert isinstance(event, dict) and "t" in event
                seen += 1
        assert seen >= 2, f"回合流事件过少:{seen}"


# ---------------- 已知缺陷(xfail 回归钉死;详见本波报告 P0/P1/P2) ----------------
# 以下向量只能经「原始字节体」构造(合法的 HTTP 客户端 / 浏览器 无法用 json= 送出),
# 但服务端 json.loads 会解出对应值并在处理/响应渲染时抛未捕获异常 → 5xx(非 JSON 体)。
# 不修 api/*.py(归网格视图分支独占);strict=False:修复后自动转 XPASS,不阻断门禁。

@pytest.mark.xfail(reason="P1:check_session_id 未拒绝孤代理,原始体经 sqlite/mkdir "
                          "抛 UnicodeEncodeError → 500(见报告 §发现1)", strict=False)
def test_known_bug_surrogate_session_id_should_be_4xx(fuzz_ctx):
    client, _ = fuzz_ctx
    resp = client.post("/api/sessions", content=rb'{"id": "s\ud834x"}',
                       headers=JSON_HEADERS)
    assert resp.status_code < 500
    resp.json()


@pytest.mark.xfail(reason="P1:体内非有限数(NaN/Infinity)触发 422 回显,Starlette "
                          "JSONResponse(allow_nan=False) 渲染再抛 → 500(见报告 §发现2)",
                   strict=False)
def test_known_bug_nonfinite_number_in_body_should_be_4xx(fuzz_ctx):
    client, _ = fuzz_ctx
    resp = client.post("/api/turn", content=rb'{"session": "demo", "text": NaN}',
                       headers=JSON_HEADERS)
    assert resp.status_code < 500
