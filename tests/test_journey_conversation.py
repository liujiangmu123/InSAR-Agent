"""对话驱动全旅程端到端测试(API 级,TestClient 驱动,mock LLM,零真实出网)。

锁定主线:「AI 驱动的 InSAR 处理助手」= 对话 → 规划 → 执行 → 建议 → 报告,
任何一环回退立刻红灯。与既有测试的分工:
  - tests/test_journey_quake.py 固定 quake 旅程语义(关键词规划 + /api/pipeline
    显式执行 + 干预/fork/报告矩阵);
  - tests/test_converse.py 固定 converse 契约本身(facade/driver 单元级);
  - 本文件固定「对话驱动」的全链贯通:/api/turn 是唯一入口,规划/干预/执行全部
    由自然语言回合驱动,mock LLM 按剧本出牌,终态后账本与报告可用。

三个剧本(每个剧本一个 test 函数;C1 的增强探测按任务要求拆成独立小用例,
经 module 夹具按定义序共享 C1 终态):
  C1 对话剧本   六回合:你好(纯聊天)→ 查环境 → 规划 quake → 改第 6 步
                min_coherence=0.3(入队)→ 开始跑(检查点消费干预 + 模拟执行
                到 done)→ 终态账本/报告断言;advise 与记忆/标题增强动态探测,
                未合并 skip(注明「等待合并」)。
  C2 降级剧本   同一批话术、Brain(None)(无 LLM):回合1 走规则路径固定追问
                (ask 表单),回合3 关键词命中照常规划 —— 双轨都活着。
  C3 崩溃剧本   规划 + 排队干预后「服务重启」(丢弃整个 app,同一 home 重建
                app/driver),重启后的执行回合把 run 状态与干预队列从 DB 续上,
                一次跑到 done。

mock LLM 注入(tests/test_converse.py 的 ConverseScriptProvider 同款,但打在
API 层):monkeypatch insar_agent.api.app.Brain —— driver_of 构造会话 Driver 时
拿到的就是脚本化 Brain;非 converse 职责(select/intent/triage/narrate)一律
BrainUnavailable 走各自既有降级路径,脚本只被 converse 消耗(用例内有守卫)。

场景语义备注(断言口径的依据,均为产品既定语义,不是缺陷):
  - quake 场景包 cloud_completed=[2,3,4,5,6],第 6 步(解缠)是云端 skipped
    步骤:对它 SET_PARAMS 会真实入账并级联刷新自身+全部下游的指纹
    (intervention.affected=[6..11]),但 stale 旗只打在 done/stale 态步骤上
    (core/stale.refresh_run),待跑集为 pending 时表现为「配置刷新+影响清单」;
  - 报告骨架按设计不叙述云端 skipped 步骤的本地参数(report/draft 的
    _step_sentence:云端产物不反映本地改参,叙述了反而失实),0.3 的在场性
    由 facts_used 事实闭集锁定。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from insar_agent.api.app import create_app
from insar_agent.brain.facade import Brain
from insar_agent.brain.provider import BrainUnavailable, LLMProvider, LLMRoute
from insar_agent.core.db import Database
from insar_agent.core.store import Store
from insar_agent.runtime.probe import ProbeResult

SID = "conv-journey"          # C1 剧本会话
SID_DEGRADED = "conv-degraded"  # C2 剧本会话
SID_CRASH = "conv-crash"      # C3 剧本会话

# 用户台词(C1/C2/C3 共用同一批话术:降级剧本必须证明同样的话在无 LLM 时也活)
T_HELLO = "你好"
T_ENV = "我的环境配置好了吗"
T_PLAN = "分析 Ridgecrest 2019 同震形变"
T_TUNE = "把第 6 步 min_coherence 改成 0.3"
T_RUN = "开始跑"

EXEC_STEPS = [1, 7, 8, 9, 10, 11]  # quake 本地待跑集合
CLOUD_STEPS = [2, 3, 4, 5, 6]      # 云端(HyP3)已完成 → skipped

#: C1 的 LLM 剧本:每回合恰好一次 converse 调用,按序出牌
C1_SCRIPT = [
    {"reply": "你好!我是 InSAR 处理助手,想分析哪个区域的形变?", "action": None},
    {"reply": "我帮你看下环境探测结果。", "action": {"type": "check_env"}},
    {"reply": "好,按同震场景规划 Ridgecrest。", "action": {
        "type": "plan", "scenario": "quake",
        "region": "Ridgecrest 断裂带", "timerange": "2019-06..2019-08"}},
    {"reply": "把第 6 步相干性阈值改成 0.3,下个检查点生效。", "action": {
        "type": "set_params", "step": 6, "params": {"min_coherence": 0.3}}},
    {"reply": "开始执行。", "action": {"type": "execute"}},
]


# ---------------------------------------------------------------------------
# 夹具与工具
# ---------------------------------------------------------------------------

def _empty_probe(*args, **kwargs) -> ProbeResult:
    """密封探测:全部引擎缺失 + 无凭据 → 规划必然落到「模拟执行」路径。"""
    return ProbeResult(
        engines={k: None for k in ("isce2", "mintpy", "snaphu", "gdal", "snap",
                                   "pystamps", "pyaps")},
        credentials={"earthdata": False, "cds": False, "gacos": False},
        disk_free_gb=100.0, cpu_count=8)


def _seal(mp) -> None:
    """密封环境:LLM 环境变量路由清空(报告路由的独立 provider 保持禁用 →
    骨架可预期)、空引擎探测、允许模拟执行。零真实出网的硬前提。"""
    for var in ("INSAR_LLM_BASE_URL", "INSAR_LLM_MODEL",
                "INSAR_LLM_FALLBACK_BASE_URL", "INSAR_LLM_FALLBACK_MODEL"):
        mp.delenv(var, raising=False)
    mp.setenv("INSAR_ALLOW_SIMULATED", "1")
    mp.setattr("insar_agent.loop.driver.probe_environment", _empty_probe)


class ConverseScriptProvider(LLMProvider):
    """只对 converse 调用回剧本的假 LLM(不走网络)。

    其余职责(select/intent/triage/narrate)按 system prompt 识别后一律
    BrainUnavailable → 各自走既有降级路径(recommend/规则/模板):剧本只被
    converse 消耗,规划期的 select 调用不会偷吃台词。
    """

    def __init__(self, responses):
        super().__init__(routes=[LLMRoute("http://fake", "", "fake-model")])
        self.script = list(responses)  # 剩余台词(测试收官时断言演完)
        self.converse_calls: list[dict] = []

    def complete_json(self, *, system, user, max_tokens=512):
        if "InSAR 数据处理助手" not in system:
            raise BrainUnavailable("非 converse 调用,走降级")
        self.converse_calls.append(
            {"system": system, "user": user, "max_tokens": max_tokens})
        if not self.script:
            raise BrainUnavailable("剧本已演完,不许多说一句")
        return self.script.pop(0)


def _stream(client: TestClient, url: str, body: dict) -> list[dict]:
    """消费 NDJSON 回合流;每行必须 json.loads 成功且含类型字段 "t"(流协议纪律)。"""
    events: list[dict] = []
    with client.stream("POST", url, json=body) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/x-ndjson")
        for line in resp.iter_lines():
            if not line.strip():
                continue
            event = json.loads(line)
            assert isinstance(event, dict) and "t" in event, f"事件缺类型字段: {line!r}"
            events.append(event)
    return events


def _need(ctx: dict, key: str):
    """旅程节点顺序依赖:前置节点失败时给出可读的失败原因,而不是 KeyError。"""
    if key not in ctx:
        pytest.fail(f"前置旅程节点未完成(ctx 缺 {key});本模块必须按文件内定义序执行")
    return ctx[key]


def _state(client: TestClient, session: str) -> dict:
    return client.get("/api/state", params={"session": session}).json()


def _steps_by_id(state: dict) -> dict[int, dict]:
    return {s["id"]: s for s in state["steps"]}


def _api_paths(client: TestClient) -> set[str]:
    """已注册路由路径集合(增强特性的动态探测面:路由不存在 = 尚未合并)。"""
    return {getattr(r, "path", "") for r in client.app.routes}


@pytest.fixture(scope="module")
def c1(tmp_path_factory):
    """C1 剧本夹具:密封环境 + 脚本化 Brain 注入 API 层,module 级共享同一个
    app/TestClient —— 回合之间的状态就是真实服务端状态(home 内 SQLite)。"""
    mp = pytest.MonkeyPatch()
    _seal(mp)
    provider = ConverseScriptProvider(C1_SCRIPT)
    # driver_of 构造 Driver 时调用 Brain(LLMProvider(routes_from_config(home))):
    # 补丁忽略真 provider,固定返回脚本化 Brain(与 test_api_robustness 的
    # Driver 补丁同一注入面)
    mp.setattr("insar_agent.api.app.Brain", lambda _provider: Brain(provider))
    home = tmp_path_factory.mktemp("conv-journey") / "home"
    app = create_app(home=home)
    with TestClient(app) as client:
        yield {"client": client, "home": home, "provider": provider, "ctx": {}}
    mp.undo()


# ---------------------------------------------------------------------------
# C1 对话剧本:六回合把「对话→规划→干预→执行→报告」一口气讲完
# ---------------------------------------------------------------------------

def test_c1_conversation_drives_full_journey(c1):
    client, home, provider, ctx = c1["client"], c1["home"], c1["provider"], c1["ctx"]

    # ══ 回合1 「你好」:纯聊天 ══
    ev1 = _stream(client, "/api/turn", {"session": SID, "text": T_HELLO})
    # 为什么:闲聊绝不能触发规划/探测 —— 事件流只许一条 say,出现 thinking/
    # plan/candidates 任何一个都说明对话入口把寒暄误判成了任务
    assert [e["t"] for e in ev1] == ["say"], f"闲聊回合事件超集: {[e['t'] for e in ev1]}"
    assert ev1[0]["parts"] == [C1_SCRIPT[0]["reply"]]
    # 为什么:寒暄不许创建 run(状态零副作用)
    assert _state(client, SID)["run"] is None
    # 对话落库,重开会话可回看(user → agent 各一条)
    hist = client.get("/api/chat", params={"session": SID}).json()
    assert [m["role"] for m in hist] == ["user", "agent"]

    # ══ 回合2 「我的环境配置好了吗」:check_env 动作 ══
    ev2 = _stream(client, "/api/turn", {"session": SID, "text": T_ENV})
    assert [e["t"] for e in ev2] == ["say"]
    parts = ev2[0]["parts"]
    assert parts[0] == C1_SCRIPT[1]["reply"]
    # 为什么:探测数据必须是系统真值(密封探测:磁盘 100 GB / CPU 8 核 /
    # 引擎全缺),由系统在 reply 之后附上 —— LLM 只许给回复语,不许编数值
    assert "磁盘 100 GB 可用" in parts[1] and "CPU 8 核" in parts[1]
    assert "缺失" in parts[1], "空探测必须如实报缺失,不许粉饰环境"
    assert _state(client, SID)["run"] is None  # 查询类动作同样零副作用

    # ══ 回合3 「分析 Ridgecrest 2019 同震形变」:plan 动作进既有规划流程 ══
    ev3 = _stream(client, "/api/turn", {"session": SID, "text": T_PLAN})
    kinds = [e["t"] for e in ev3]
    # 为什么:过渡语(converse reply)必须先行,之后与规则路径共用同一套
    # 规划流程 —— 骨架事件一个不少且有序
    assert kinds[0] == "say" and ev3[0]["parts"] == [C1_SCRIPT[2]["reply"]]
    for t in ("thinking", "tool.start", "tool.end", "plan", "candidates"):
        assert t in kinds, f"规划回合缺 {t} 事件: {kinds}"
    assert (kinds.index("thinking") < kinds.index("tool.start")
            < kinds.index("tool.end") < kinds.index("plan")
            < kinds.index("candidates"))
    thinking = next(e for e in ev3 if e["t"] == "thinking")
    # 为什么:意图来源必须如实标注 converse(不是 rules)—— 溯源不许张冠李戴
    assert "(来源:converse)" in thinking["body"]
    # 为什么:LLM 抽取的区域/时间要真正进入规划展示(只覆盖展示元数据,不进指纹)
    assert "Ridgecrest 断裂带" in thinking["body"]
    assert "2019-06..2019-08" in thinking["body"]
    # 为什么:引擎全缺 → 必须亮「模拟执行」横幅,不冒充真实证据
    assert any(e["t"] == "note" and "模拟" in e["text"] for e in ev3)
    # 为什么:决策点应恰好一个且是第 1 步(数据获取);select 因脚本只服务
    # converse 而降级到 registry 推荐 —— 「来源:recommend」证明剧本没被偷吃
    cands = [e for e in ev3 if e["t"] == "candidates"]
    assert len(cands) == 1 and cands[0]["stepId"] == 1
    say_text = "".join(str(p) for e in ev3 if e["t"] == "say" for p in e["parts"])
    assert "local_import" in say_text and "来源:recommend" in say_text
    # 为什么:计划面板要明示本地待跑集合(2-6 云端已完成,不进待跑)
    plan_items = next(e for e in ev3 if e["t"] == "plan")["items"]
    assert any("[1, 7, 8, 9, 10, 11]" in it["text"] for it in plan_items)

    # run 创建 + 11 步计划落库(状态镜像与事件一致)
    state = _state(client, SID)
    run = state["run"]
    assert run["status"] == "ready" and run["scenario"] == "quake"
    assert bool(run["simulated"]) is True
    # 为什么:溯源账本的意图来源字段必须记 converse —— 这条 run 是对话驱动的
    assert json.loads(run["intent"])["source"] == "converse"
    st = _steps_by_id(state)
    assert sorted(st) == list(range(1, 12)), "quake 计划必须是完整 11 步"
    assert all(st[s]["state"] == "skipped" for s in CLOUD_STEPS)
    assert all(st[s]["state"] == "pending" for s in EXEC_STEPS)
    # 场景包覆写进计划(第 9 步同震阶跃日期)—— 对话路径不许丢场景知识
    assert st[9]["params"]["step_date"] == "20190706T0320"
    ctx["run_id"] = run["run_id"]

    # ══ 回合4 「把第 6 步 min_coherence 改成 0.3」:set_params 入队 ══
    ev4 = _stream(client, "/api/turn", {"session": SID, "text": T_TUNE})
    # 为什么:改参回合 = 回复 + 入队留痕,恰好两个事件;不许当场执行
    assert [e["t"] for e in ev4] == ["say", "intervention"]
    iv4 = ev4[1]
    assert "已排队" in iv4["text"] and "第 6 步" in iv4["text"] and "SET_PARAMS" in iv4["text"]
    assert iv4["affected"] == [6] and iv4["mode"] == "queue"  # 入队回执只声明目标步
    # 为什么:入队 ≠ 生效 —— 消费前状态必须零漂移(参数仍是默认 0.25,无 stale)
    state = _state(client, SID)
    st = _steps_by_id(state)
    assert st[6]["params"]["min_coherence"] == 0.25
    assert all(not s["stale"] for s in state["steps"])
    # 队列侧证(同 home 第二连接直读 DB):动作归属当前 run,防跨 run 互吞
    store = Store(Database(home / "insar.db"))
    try:
        queued = store.due_actions("steer", run_id=ctx["run_id"],
                                   include_unattributed=False)
        assert len(queued) == 1
        assert queued[0]["action"] == "SET_PARAMS" and queued[0]["target"] == "6"
        assert queued[0]["payload"] == {"params": {"min_coherence": 0.3}}
    finally:
        store.close()

    # ══ 回合5 「开始跑」:execute 动作,消费干预后模拟执行到 done ══
    ev5 = _stream(client, "/api/turn", {"session": SID, "text": T_RUN})
    kinds = [e["t"] for e in ev5]
    assert kinds[0] == "say" and ev5[0]["parts"] == [C1_SCRIPT[4]["reply"]]
    # 为什么:排队的 SET_PARAMS 必须在任何步骤启动前的检查点被消费,且
    # science 参数的失效级联覆盖自身+全部下游(6→11);此刻待跑集是 pending,
    # 级联表现为配置刷新 + 影响清单(stale 旗只打 done/stale 态 —— 见模块头注)
    ivs = [e for e in ev5 if e["t"] == "intervention"]
    assert len(ivs) == 1 and "param_changed" in ivs[0]["text"]
    assert ivs[0]["affected"] == [6, 7, 8, 9, 10, 11]
    assert ev5.index(ivs[0]) < kinds.index("step.start")
    # 为什么:只执行本地待跑集合 [1,7,8,9,10,11] 且拓扑序;云端 2-6 一步不碰
    started = [e["stepId"] for e in ev5 if e["t"] == "step.start"]
    assert started == EXEC_STEPS
    ends = {e["stepId"]: e["exit"] for e in ev5 if e["t"] == "step.end"}
    assert all(ends[s] == 0 for s in EXEC_STEPS), f"模拟执行必须全绿: {ends}"
    # 为什么:进度单调收敛到 100,终态卡(result/report)齐备 —— 前端据此收尾
    pcts = [e["pct"] for e in ev5 if e["t"] == "overall"]
    assert pcts == sorted(pcts) and pcts[-1] == 100
    assert "result" in kinds and "report" in kinds

    # 终态状态镜像:同一个 run 到 done;干预后的参数真实变更
    state = _state(client, SID)
    assert state["run"]["run_id"] == ctx["run_id"]
    assert state["run"]["status"] == "done"
    st = _steps_by_id(state)
    assert st[6]["params"]["min_coherence"] == 0.3, "消费后参数必须真实落库,不是纸面回执"
    # 为什么:改参不改云端事实 —— 第 6 步保持 skipped(产物在云端,本地不假跑)
    assert st[6]["state"] == "skipped" and not st[6]["stale"]
    assert all(st[s]["state"] == "done" for s in EXEC_STEPS)

    # ══ 终态账本:GET /api/provenance ══
    prov = client.get("/api/provenance", params={"session": SID}).json()
    assert prov["run_id"] == ctx["run_id"] and prov["scenario"] == "quake"
    assert len(prov["steps"]) == 11, "账本必须覆盖全部 11 步(含云端跳过步)"
    # 为什么:模拟执行的证据级别必须封顶 runnable,且封顶原因可读 —— 诚实性红线
    assert prov["simulated"] is True and prov["evidence_level"] == "runnable"
    assert "模拟执行" in prov["evidence"]["ceiling_reason"]
    assert prov["intent"]["source"] == "converse"
    assert prov["qa"]["status"] == "pass"
    # 为什么:对话入队的干预必须可归属、可审计 —— 恰好 1 条且已消费
    assert len(prov["interventions"]) == 1
    only = prov["interventions"][0]
    assert only["action"] == "SET_PARAMS" and only["target"] == "6"
    assert only["consumed_at"], "已生效的干预必须带消费时间戳"
    assert prov["steps"]["6"]["params"]["min_coherence"] == 0.3
    # 为什么:云端步骤不许伪造本地命令账本(改参 ≠ 执行过)
    assert prov["steps"]["6"]["commands"] == []
    for sid in EXEC_STEPS:
        cmds = prov["steps"][str(sid)]["commands"]
        assert len(cmds) == 1 and cmds[0]["exit_code"] == 0, f"第 {sid} 步命令账本不完整"
    # 云端语义不因对话驱动而漂移:2-6 无本地证据 → 如实 missing
    srcs = prov["evidence"]["step_sources"]
    assert all(srcs[str(s)]["origin"] == "missing" for s in CLOUD_STEPS)
    assert all(srcs[str(s)]["origin"] == "local" for s in EXEC_STEPS)

    # ══ 终态报告:POST /api/report/draft ══
    r = client.post("/api/report/draft", json={"session": SID})
    assert r.status_code == 200
    body = r.json()
    assert body["run_id"] == ctx["run_id"]
    # 为什么:注入的 mock 只服务 converse;报告路由的 provider 独立且未配置 →
    # 必须走纯骨架(llm_polish=false),证明 mock 没有泄漏进报告链
    assert body["llm_polish"] is False
    draft = body["draft"]
    # 为什么:骨架里的数字必须来自账本真值 —— 场景覆写的阶跃日期原样出现
    assert "20190706T0320" in draft
    assert "不构成科学证据" in draft, "模拟 run 的强制警示句不许丢"
    assert "由云端/缓存完成" in draft, "云端步骤必须如实叙述,不冒充本地执行"
    # 为什么:0.3 已入账并进入报告事实闭集(facts_used)。第 6 步是云端
    # skipped 步骤,骨架正文按设计不叙述其本地参数(云端产物不反映本地改参,
    # 叙述了反而失实)—— 0.3 的在场性由事实闭集锁定
    assert "step6.param.min_coherence=0.3" in body["facts_used"]
    assert body["saved"] is True, "草稿必须落盘 run 工作区(文件面板可见)"

    # ══ 剧本收官守卫 ══
    # 为什么:converse 恰好被调用 5 次(每回合一次)—— 多一次说明其他职责偷吃
    # 了剧本,少一次说明某回合根本没走对话入口
    assert len(provider.converse_calls) == 5
    assert not provider.script, "剧本必须演完(有剩余台词 = 某回合没走 converse)"
    # 系统状态注入进 converse prompt(回合5 的 LLM 看得到 run 就绪状态)
    assert "【系统状态】" in provider.converse_calls[-1]["user"]
    # 全部 5 条用户消息落聊天历史(对话是一等公民,不是事件流的副产品):
    # 末尾应是「user: 开始跑 → agent: 过渡语」的收官对
    hist = client.get("/api/chat", params={"session": SID}).json()
    assert [m["role"] for m in hist].count("user") == 5
    assert hist[-2]["role"] == "user" and hist[-2]["content"] == T_RUN
    assert hist[-1]["role"] == "agent"
    ctx["journey_done"] = True


# ---------------------------------------------------------------------------
# C1 增强探测:advise / 记忆·标题(未合并 → skip,注明「等待合并」)
# ---------------------------------------------------------------------------

def test_c1_advise_endpoint_when_merged(c1):
    """建议端点(advise/recommend)动态探测:存在则断言终态建议非空。"""
    client, ctx = c1["client"], c1["ctx"]
    _need(ctx, "journey_done")
    hits = sorted(p for p in _api_paths(client)
                  if "advise" in p or "recommend" in p)
    if not hits:
        # 为什么 skip 而非 fail:advise 与 recommend 的合并尚未落地(动态探测
        # 路由表无此端点);合并后本用例自动转为硬断言
        pytest.skip("等待合并:/api/advise(或 /api/recommend)尚未上线;"
                    "上线后断言:done run 的建议列表非空")
    r = client.get(hits[0], params={"session": SID})
    # 为什么:建议端点一旦存在,对 done run 必须可用且给出非空建议 ——
    # 「执行完却无话可说」的建议链等于回退
    assert r.status_code == 200, f"{hits[0]} 对 done run 应可用,得到 {r.status_code}"
    assert r.json(), "终态 run 的建议不应为空"


def test_c1_turn6_memory_title_when_merged(c1):
    """回合6 增强(会话记忆/自动标题)动态探测:未合并 skip。"""
    client, ctx = c1["client"], c1["ctx"]
    _need(ctx, "journey_done")
    route_hits = sorted(p for p in _api_paths(client)
                        if "memory" in p or "title" in p)
    sess = next(s for s in client.get("/api/sessions").json()
                if s["session_id"] == SID)
    # 自动标题若已合并:旅程结束后会话名不应仍是裸 id(driver_of 创建时 name=id)
    auto_titled = sess["name"] != SID
    if not route_hits and not auto_titled:
        pytest.skip("等待合并:会话记忆/自动标题增强未上线(无相关路由,"
                    "会话名仍为裸 id);合并后在此断言记忆可回读/标题非空")
    if auto_titled:
        # 为什么:自动标题一旦启用,标题必须非空且不含控制字符(UI 不破版)
        assert sess["name"].strip() and "\n" not in sess["name"]
    for path in route_hits:
        r = client.get(path, params={"session": SID})
        assert r.status_code < 500, f"增强端点 {path} 不应 5xx"


# ---------------------------------------------------------------------------
# C2 降级剧本:Brain(None) 走规则路径,双轨都活着
# ---------------------------------------------------------------------------

def test_c2_degraded_no_llm_rules_track_alive(tmp_path, monkeypatch):
    _seal(monkeypatch)
    # 为什么显式打回 Brain(None):C1 的 module 夹具在整个文件运行期间都存活,
    # 其 api.app.Brain 补丁仍在 —— 降级剧本必须真·无 LLM,不能继承脚本化 Brain
    monkeypatch.setattr("insar_agent.api.app.Brain", lambda _provider: Brain(None))
    app = create_app(home=tmp_path / "home")
    with TestClient(app) as client:
        # ── 回合1 「你好」:规则路径固定追问 ──
        ev1 = _stream(client, "/api/turn", {"session": SID_DEGRADED, "text": T_HELLO})
        # 为什么:Brain(None) 从不进 converse —— 无 say、无「LLM 暂不可用」降级
        # note,唯一事件是补充表单(与手动流水线时代逐字节一致的守护)
        assert [e["t"] for e in ev1] == ["ask"], f"降级回合1 事件异常: {[e['t'] for e in ev1]}"
        assert ev1[0]["prompt"] == "无法从描述中识别场景,请补充:"
        sc_field = next(f for f in ev1[0]["fields"] if f["key"] == "scenario")
        assert "quake" in sc_field["options"], "表单场景选项必须来自场景包闭集"
        assert _state(client, SID_DEGRADED)["run"] is None  # 追问不创建 run

        # ── 回合3(同一句话):关键词规则命中 quake,规划全流程照常 ──
        ev3 = _stream(client, "/api/turn", {"session": SID_DEGRADED, "text": T_PLAN})
        kinds = [e["t"] for e in ev3]
        # 为什么:无 converse 过渡语,首事件即 thinking;全程不许出现任何
        # LLM 相关提示 —— 无 LLM 是正常形态,不是故障形态
        assert kinds[0] == "thinking"
        assert not any(e["t"] == "note" and "LLM" in e.get("text", "") for e in ev3)
        thinking = next(e for e in ev3 if e["t"] == "thinking")
        assert "(来源:rules)" in thinking["body"], "意图来源必须如实标 rules"
        assert "plan" in kinds and "candidates" in kinds
        # 规则轨产出与对话轨同形状的计划:run ready、quake、完整 11 步、云端跳过
        state = _state(client, SID_DEGRADED)
        run = state["run"]
        assert run["status"] == "ready" and run["scenario"] == "quake"
        assert json.loads(run["intent"])["source"] == "rules"
        st = _steps_by_id(state)
        assert sorted(st) == list(range(1, 12))
        assert all(st[s]["state"] == "skipped" for s in CLOUD_STEPS)
        assert all(st[s]["state"] == "pending" for s in EXEC_STEPS)


# ---------------------------------------------------------------------------
# C3 崩溃剧本:规划+排队干预 → 重启(同 home 重建 app)→ 执行续上
# ---------------------------------------------------------------------------

def test_c3_restart_recovers_run_and_queued_intervention(tmp_path, monkeypatch):
    _seal(monkeypatch)
    home = tmp_path / "home"

    # ── 崩溃前进程:规划 + 排队改参(不执行) ──
    monkeypatch.setattr(
        "insar_agent.api.app.Brain",
        lambda _provider: Brain(ConverseScriptProvider([
            {"reply": "按同震场景规划。", "action": {"type": "plan", "scenario": "quake"}},
            {"reply": "第 6 步阈值改 0.3。", "action": {
                "type": "set_params", "step": 6, "params": {"min_coherence": 0.3}}},
        ])))
    app_before = create_app(home=home)
    with TestClient(app_before) as client:
        _stream(client, "/api/turn", {"session": SID_CRASH, "text": T_PLAN})
        run = _state(client, SID_CRASH)["run"]
        assert run is not None and run["status"] == "ready"
        run_id = run["run_id"]
        ev = _stream(client, "/api/turn", {"session": SID_CRASH, "text": T_TUNE})
        assert any(e["t"] == "intervention" for e in ev), "改参必须入队留痕"

    # ── 「服务重启」:旧 app 连同 driver/EventBus/租约等内存态整体丢弃,
    #    幸存的只有 home 下的 SQLite 与工作区文件(与真实崩溃等价的可观测面);
    #    新进程的 Brain 只有一句台词:执行 ──
    monkeypatch.setattr(
        "insar_agent.api.app.Brain",
        lambda _provider: Brain(ConverseScriptProvider([
            {"reply": "继续,开始执行。", "action": {"type": "execute"}},
        ])))
    app_after = create_app(home=home)
    with TestClient(app_after) as client:
        # 为什么:重启后 run/步骤/干预队列必须从 DB 原样恢复 —— 同一 run_id、
        # 仍 ready、参数未变(队列里的干预不因重启蒸发,也不提前生效)
        state = _state(client, SID_CRASH)
        assert state["run"]["run_id"] == run_id and state["run"]["status"] == "ready"
        st = _steps_by_id(state)
        assert sorted(st) == list(range(1, 12))
        assert st[6]["params"]["min_coherence"] == 0.25

        # ── 回合5 「开始跑」:重启后的执行回合把一切续上 ──
        ev5 = _stream(client, "/api/turn", {"session": SID_CRASH, "text": T_RUN})
        kinds = [e["t"] for e in ev5]
        assert kinds[0] == "say"
        # 为什么:崩溃前排队的干预由重启后的回合在检查点消费 —— 跨进程的
        # 干预队列语义(steer 持久化)是崩溃恢复的核心承诺
        ivs = [e for e in ev5 if e["t"] == "intervention"]
        assert len(ivs) == 1 and ivs[0]["affected"] == [6, 7, 8, 9, 10, 11]
        assert [e["stepId"] for e in ev5 if e["t"] == "step.start"] == EXEC_STEPS
        assert "result" in kinds

        # 为什么:执行续接的是崩溃前那一个 run(不重建、不分叉),状态到 done
        state = _state(client, SID_CRASH)
        assert state["run"]["run_id"] == run_id
        assert state["run"]["status"] == "done"
        prov = client.get("/api/provenance", params={"session": SID_CRASH}).json()
        assert prov["steps"]["6"]["params"]["min_coherence"] == 0.3
        assert len(prov["interventions"]) == 1 and prov["interventions"][0]["consumed_at"]
        assert prov["evidence_level"] == "runnable"
