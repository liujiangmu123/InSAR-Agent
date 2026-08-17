"""quake 场景全旅程深验(API 级,TestClient 全流程驱动,simulated 秒级)。

与 tests/test_e2e_contract.py 的分工:那边固定「前端事件契约」,这里固定
「quake 用户旅程语义」—— 每个旅程节点一个测试函数,模块内按定义序执行,
共享同一个 TestClient(module 夹具),状态经 ctx 字典沿旅程传递。

旅程矩阵(J1→J6b 顺序依赖,断言点见各函数内 ①②③… 标号):
    J1  规划   turn「Ridgecrest 地震同震形变」→ 意图 rules 命中 quake、
               2-6 步 skipped(云端)、决策点=第 1 步、候选含
               asf_search_slc/hyp3_submit/local_import、场景覆写入计划
    J2  执行   只跑 [1,7,8,9,10,11]、事件骨架逐步闭合、run done、
               provenance.step_sources 2-6 落 cloud/missing 闭集
               (无 hyp3_manifest → missing)、命令账本基线
    J3  干预   done 后改第 9 步 step_date(science)→ impact 预览
               affected==[9,10,11] → 排队 SET_PARAMS + 显式重跑 [9,10,11]
               → 恰好这三步各 +1 条命令、provenance 参数/干预入账
    J4  fork  改第 8 步参数 → 1/7 零重算复用、2-6 保持 skipped →
               执行只跑 8-11 → evidence 的 inherited(parent=…) 徽标、
               父链外部验证只列出不继承
    J5  报告   methods.md 含同震阶跃(Heaviside)描述与 20190706;
               figures 列表与产物账本一致;run.sh 步骤序完整
    J6a 边界   同 run 并发第二个 execute 被运行锁拒绝(note 文案),
               拒绝回合零副作用,胜者照常消费干预并重跑
    J6b 边界   对 skipped(云端已完成)步骤显式重跑被忽略并 note,
               状态与命令账本零漂移

发现并小修的 bug(注明,修改在 src/insar_agent/loop/driver.py):
    「假重跑」P1 —— 改参重跑是两连发(先排队 SET_PARAMS(steer),再显式
    step_ids 执行)。
    steer 在首步检查点才被消费,而入口复位 pass 用的是消费前快照:被标脏的
    done 步骤 stage 仍停在 VERIFIED,五阶段幂等守卫把重跑空转成 no-op ——
    步骤卡照发 tool.end/step.end(exit 0),命令数却为 0,结果仍是旧配置,
    收尾再补一条「待重跑」note。修复 = 步间消费后对 stale 步骤补一次
    reset_step_for_rerun(与入口 pass 同语义);J3 的 ⑤-⑧ 是回归守护。
"""

from __future__ import annotations

import json
import threading

import pytest
from fastapi.testclient import TestClient

from insar_agent.api.app import create_app

SID = "quake-journey"
TEXT = "Ridgecrest 地震同震形变"
EXEC_STEPS = [1, 7, 8, 9, 10, 11]       # 本地待跑集合(quake 场景)
CLOUD_STEPS = [2, 3, 4, 5, 6]           # 云端(HyP3)已完成 → skipped
#: J3 的干预值:主震时刻 03:19:53 UTC,把场景包默认 0320 修到 0319 ——
#: 真实的 science 变更(args_hash 变),且报告里仍保留 20190706 日期可查
NEW_STEP_DATE = "20190706T0319"


# ---------------------------------------------------------------------------
# 夹具:密封环境(空引擎 probe + LLM 路由清空 + 允许 simulated),module 级
# 共享同一个 app/TestClient —— 旅程节点之间的状态就是真实服务端状态
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def journey(tmp_path_factory):
    from insar_agent.runtime.probe import ProbeResult

    def empty_probe(*args, **kwargs):
        return ProbeResult(
            engines={k: None for k in ("isce2", "mintpy", "snaphu", "gdal", "snap",
                                       "pystamps", "pyaps")},
            credentials={"earthdata": False, "cds": False, "gacos": False},
            disk_free_gb=100.0, cpu_count=8)

    mp = pytest.MonkeyPatch()
    mp.setattr("insar_agent.loop.driver.probe_environment", empty_probe)
    for var in ("INSAR_LLM_BASE_URL", "INSAR_LLM_MODEL",
                "INSAR_LLM_FALLBACK_BASE_URL", "INSAR_LLM_FALLBACK_MODEL"):
        mp.delenv(var, raising=False)
    mp.setenv("INSAR_ALLOW_SIMULATED", "1")
    app = create_app(home=tmp_path_factory.mktemp("quake-journey") / "home")
    with TestClient(app) as client:
        yield {"client": client, "ctx": {}}
    mp.undo()


def _stream(client: TestClient, url: str, body: dict) -> list[dict]:
    """消费 NDJSON 流;每行必须 json.loads 成功且含类型字段 "t"(流协议纪律)。"""
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


def _idx(events: list[dict], t: str, key: str, val) -> int:
    return next(i for i, e in enumerate(events) if e["t"] == t and e.get(key) == val)


# ---------------------------------------------------------------------------
# J1 规划:意图 → 探测 → 计划(云端跳过)→ 决策点候选
# ---------------------------------------------------------------------------
def test_j1_turn_plans_quake_with_cloud_skip(journey):
    client, ctx = journey["client"], journey["ctx"]
    r = client.post("/api/sessions", json={"id": SID, "name": "quake 旅程", "mode": "expert"})
    assert r.status_code == 200 and r.json()["session_id"] == SID

    events = _stream(client, "/api/turn", {"session": SID, "text": TEXT})
    kinds = [e["t"] for e in events]

    # ① 规划回合骨架:thinking → 探测工具 → plan → say → candidates(有序)
    for t in ("thinking", "tool.start", "tool.log", "tool.end", "plan", "say", "candidates"):
        assert t in kinds, f"规划回合缺 {t} 事件: {kinds}"
    assert (kinds.index("thinking") < kinds.index("tool.start")
            < kinds.index("tool.end") < kinds.index("plan")
            < kinds.index("say") < kinds.index("candidates"))

    # ② 意图分类:规则层(零 LLM)命中 quake 场景,来源如实标注
    thinking = next(e for e in events if e["t"] == "thinking")
    assert "同震形变" in thinking["body"] and "(来源:rules)" in thinking["body"]

    # ③ 决策点是第 1 步(数据获取),且恰好一个决策点
    cands = [e for e in events if e["t"] == "candidates"]
    assert len(cands) == 1 and cands[0]["stepId"] == 1

    # ④ 决策叙述:面向第 1 步、给出推荐方法 local_import、待跑步数如实
    say_text = "".join(str(p) for e in events if e["t"] == "say" for p in e["parts"])
    assert "第 1 步" in say_text and "local_import" in say_text and "待跑 6 步" in say_text

    # ⑤ 引擎全缺 → 显式「模拟执行」横幅(诚实性,不冒充真实证据)
    assert any(e["t"] == "note" and "模拟" in e["text"] for e in events)

    # ⑥ plan 面板明示待跑集合 [1, 7, 8, 9, 10, 11](2-6 不在其中)
    plan_items = next(e for e in events if e["t"] == "plan")["items"]
    assert any("[1, 7, 8, 9, 10, 11]" in it["text"] for it in plan_items)

    # ⑦ 状态镜像:run ready、scenario=quake、simulated;2-6 skipped 其余 pending
    state = client.get("/api/state", params={"session": SID}).json()
    run = state["run"]
    assert run["status"] == "ready" and run["scenario"] == "quake"
    assert bool(run["simulated"]) is True
    st = {s["id"]: s for s in state["steps"]}
    assert sorted(st) == list(range(1, 12))
    assert all(st[sid]["state"] == "skipped" for sid in CLOUD_STEPS)
    assert all(st[sid]["state"] == "pending" for sid in EXEC_STEPS)

    # ⑧ 场景包覆写进计划:第 9 步 step(20190706T0320)、第 11 步诚实降级 coherence_mask
    assert st[9]["method"] == "step" and st[9]["params"]["step_date"] == "20190706T0320"
    assert st[11]["method"] == "coherence_mask"

    # ⑨ 决策点候选(审批卡数据源 /api/registry):第 1 步候选含三条路线,
    #    可行性三元组(ok/simulated/blocked)齐备;计划选中 local_import(recommend)
    reg = client.get("/api/registry", params={"session": SID}).json()
    step1 = next(c for c in reg if c["id"] == 1)
    assert {"asf_search_slc", "hyp3_submit", "local_import"} <= {m["id"] for m in step1["methods"]}
    assert all({"ok", "simulated", "blocked"} <= set(m) for m in step1["methods"])
    assert st[1]["method"] == "local_import"

    ctx["run_id"] = run["run_id"]


# ---------------------------------------------------------------------------
# J2 执行:只跑本地待跑集合;事件骨架;provenance 的云端语义
# ---------------------------------------------------------------------------
def test_j2_execute_runs_local_subset_only(journey):
    client, ctx = journey["client"], journey["ctx"]
    run_id = _need(ctx, "run_id")
    events = _stream(client, "/api/pipeline", {"session": SID})
    kinds = [e["t"] for e in events]

    # ① 只执行 [1,7,8,9,10,11],拓扑序;云端 2-6 一步不碰
    started = [e["stepId"] for e in events if e["t"] == "step.start"]
    assert started == EXEC_STEPS

    # ② 每步骨架闭合:step.start < tool.start(sN) < tool.end(sN) < step.end,
    #    exit 全 0,tool.end 播报产物形状 {path,hash}
    ends = {e["stepId"]: e for e in events if e["t"] == "step.end"}
    tool_ends = {e["id"]: e for e in events if e["t"] == "tool.end"}
    for sid in EXEC_STEPS:
        assert ends[sid]["exit"] == 0
        te = tool_ends[f"s{sid}"]
        assert te["exit"] == 0 and isinstance(te["artifacts"], list)
        assert all({"path", "hash"} <= set(a) for a in te["artifacts"])
        assert (_idx(events, "step.start", "stepId", sid)
                < _idx(events, "tool.start", "id", f"s{sid}")
                < _idx(events, "tool.end", "id", f"s{sid}")
                < _idx(events, "step.end", "stepId", sid))

    # ③ 五阶段推进:每个执行步骤都到 VERIFIED(双通道泵入回合流)
    verified = {e["stepId"] for e in events
                if e["t"] == "step.stage" and e["stage"] == "VERIFIED"}
    assert verified == set(EXEC_STEPS)

    # ④ 进度单调至 100;终态序 …step.end < result < report;账本导出 note
    pcts = [e["pct"] for e in events if e["t"] == "overall"]
    assert pcts == sorted(pcts) and pcts[-1] == 100
    last_end = max(i for i, e in enumerate(events) if e["t"] == "step.end")
    assert last_end < kinds.index("result") < kinds.index("report")
    assert any(e["t"] == "note" and "provenance" in e.get("text", "") for e in events)
    assert not any(e["t"] == "note" and "指标重解析" in e.get("text", "") for e in events)

    # ⑤ 状态镜像与事件一致:run done;执行步 done、云端步 skipped
    state = client.get("/api/state", params={"session": SID}).json()
    assert state["run"]["run_id"] == run_id and state["run"]["status"] == "done"
    st = {s["id"]: s["state"] for s in state["steps"]}
    assert all(st[sid] == "done" for sid in EXEC_STEPS)
    assert all(st[sid] == "skipped" for sid in CLOUD_STEPS)

    # ⑥ 账本头部:schema/simulated/scenario/意图来源/qa 全部如实
    prov = client.get("/api/provenance", params={"session": SID}).json()
    assert prov["schema_version"] == "1.0" and prov["simulated"] is True
    assert prov["run_id"] == run_id and prov["scenario"] == "quake"
    assert prov["intent"]["source"] == "rules"
    assert prov["qa"]["status"] == "pass"

    # ⑦ step_sources 云端语义闭集:skipped 步骤 ∈ {cloud, missing};
    #    本工作区没有 hyp3_manifest.json → 必须如实落 missing(跳过 ≠ 免检)
    srcs = prov["evidence"]["step_sources"]
    for sid in CLOUD_STEPS:
        entry = srcs[str(sid)]
        assert entry["origin"] in ("cloud", "missing")
        assert entry["origin"] == "missing" and entry["source"] == "missing"
        assert "缺本地证据" in entry["detail"]
        step_row = prov["steps"][str(sid)]
        assert step_row["state"] == "skipped" and step_row["commands"] == []
        assert step_row["cloud_evidence"]["present"] is False
    for sid in EXEC_STEPS:
        assert srcs[str(sid)] == {"origin": "local", "source": "local"}

    # ⑧ 证据阶梯:simulated 封顶 runnable,封顶原因可读
    assert prov["evidence_level"] == "runnable"
    assert prov["evidence"]["level"] == "runnable"
    assert "模拟执行" in prov["evidence"]["ceiling_reason"]

    # ⑨ 命令账本基线:每个执行步骤恰好 1 条已结算命令(J3 计数的参照系)
    for sid in EXEC_STEPS:
        cmds = prov["steps"][str(sid)]["commands"]
        assert len(cmds) == 1 and cmds[0]["exit_code"] == 0
    ctx["baseline_done"] = True


# ---------------------------------------------------------------------------
# J3 干预:impact 预览 → SET_PARAMS(science)→ 显式重跑受影响子集
# ---------------------------------------------------------------------------
def test_j3_set_params_impact_preview_then_rerun(journey):
    client, ctx = journey["client"], journey["ctx"]
    run_id = _need(ctx, "run_id")
    _need(ctx, "baseline_done")

    # ① impact 预览(审批卡数据源):science 参数变更 → 自身 + 全部下游
    imp = client.get("/api/impact", params={
        "session": SID, "step": 9,
        "params": json.dumps({"step_date": NEW_STEP_DATE})}).json()
    assert imp["changedStep"] == 9 and imp["reason"] == "param_changed"
    assert [a["step_id"] for a in imp["affected"]] == [9, 10, 11]
    by_id = {a["step_id"]: a for a in imp["affected"]}
    assert by_id[9]["reason"] == "param_changed"
    assert by_id[10]["reason"] == by_id[11]["reason"] == "upstream_changed"
    assert all(a["state_before"] == "done" for a in imp["affected"])

    # ② 预估诚实(§7.5):历史样本不足 3 次 → 时长「未知」,不编数
    assert imp["rerunMinutes"] is None and "不足 3 次" in imp["rerunBasis"]

    # ③ 预览纯只读:不落库、不标脏
    state = client.get("/api/state", params={"session": SID}).json()
    assert all(not s["stale"] and s["state"] in ("done", "skipped") for s in state["steps"])

    # ④ UI 的「改参数→重跑」两连发:排队 SET_PARAMS(steer)→ 显式重跑 [9,10,11]
    r = client.post("/api/actions", json={
        "session": SID, "run_id": run_id, "scope": "step", "target": "9",
        "action": "SET_PARAMS", "payload": {"params": {"step_date": NEW_STEP_DATE}},
        "deliver_as": "steer"})
    assert r.status_code == 202 and r.json()["accepted"] is True
    events = _stream(client, "/api/pipeline",
                     {"session": SID, "run_id": run_id, "step_ids": [9, 10, 11]})

    # ⑤ 干预在步间检查点被消费且留痕:恰好一条 intervention,影响集完整
    iv = [e for e in events if e["t"] == "intervention"]
    assert len(iv) == 1 and iv[0]["affected"] == [9, 10, 11]

    # ⑥ 只重跑这三步(修复后的真重跑:五阶段重新走到 VERIFIED)
    assert [e["stepId"] for e in events if e["t"] == "step.start"] == [9, 10, 11]
    verified = {e["stepId"] for e in events
                if e["t"] == "step.stage" and e["stage"] == "VERIFIED"}
    assert verified == {9, 10, 11}
    assert any(e["t"] == "result" for e in events)
    assert [e["pct"] for e in events if e["t"] == "overall"][-1] == 100

    # ⑦ 「假重跑」回归守护:重跑后不允许再有「待重跑」尾巴(修复前的症状)
    assert not any(e["t"] == "note" and "待重跑" in e.get("text", "") for e in events)

    # ⑧ commands 计数:恰好 9/10/11 各 +1(1/7/8 不动)—— 重跑范围的硬证据
    prov = client.get("/api/provenance", params={"session": SID}).json()
    counts = {sid: len(prov["steps"][str(sid)]["commands"]) for sid in EXEC_STEPS}
    assert counts == {1: 1, 7: 1, 8: 1, 9: 2, 10: 2, 11: 2}
    assert all(c["exit_code"] == 0
               for sid in (9, 10, 11) for c in prov["steps"][str(sid)]["commands"])

    # ⑨ provenance 更新:新参数入账、步骤回到 done 且不再 stale、干预可归属
    assert prov["steps"]["9"]["params"]["step_date"] == NEW_STEP_DATE
    for sid in (9, 10, 11):
        s = prov["steps"][str(sid)]
        assert s["state"] == "done" and s["stale"] is False
    assert any(i["action"] == "SET_PARAMS" and i["target"] == "9" and i["consumed_at"]
               for i in prov["interventions"])
    assert client.get("/api/state", params={"session": SID}).json()["run"]["status"] == "done"
    ctx["intervened"] = True


# ---------------------------------------------------------------------------
# J4 fork:改第 8 步参数 → 零重算复用 1/7,2-6 保持 skipped,inherited 徽标
# ---------------------------------------------------------------------------
def test_j4_fork_reuses_upstream_and_marks_inherited(journey):
    client, ctx = journey["client"], journey["ctx"]
    parent = _need(ctx, "run_id")
    _need(ctx, "intervened")

    fork = client.post("/api/fork", json={
        "session": SID, "run_id": parent,
        "changes": {"8": {"params": {"ramp": "quadratic"}}}}).json()
    fork_id = fork["runId"]
    assert fork_id != parent

    # ① 计划形状:1/7 零重算复用(done);2-6 保持 skipped;8 及下游 pending
    fs = {s["id"]: s for s in fork["steps"]}
    assert fs[1]["state"] == "done" and fs[7]["state"] == "done"
    assert all(fs[sid]["state"] == "skipped" for sid in CLOUD_STEPS)
    assert all(fs[sid]["state"] == "pending" for sid in (8, 9, 10, 11))
    assert fs[8]["method"] == "tropo_era5_pyaps"  # 只改参数,方法不漂移

    # ② 执行 fork run:只跑 8-11,一次到 done
    events = _stream(client, "/api/pipeline", {"session": SID, "run_id": fork_id})
    assert [e["stepId"] for e in events if e["t"] == "step.start"] == [8, 9, 10, 11]
    assert any(e["t"] == "result" for e in events)
    state = client.get("/api/state", params={"session": SID, "run_id": fork_id}).json()
    assert state["run"]["status"] == "done"

    # ③ 谱系与配置入账:parent_run_id / forked_from;第 8 步新参数;
    #    第 9 步继承的是干预后的父配置(NEW_STEP_DATE),不是场景包默认值
    prov = client.get("/api/provenance", params={"session": SID, "run_id": fork_id}).json()
    assert prov["parent_run_id"] == parent
    assert prov["intent"]["forked_from"] == parent
    assert prov["steps"]["8"]["params"]["ramp"] == "quadratic"
    assert prov["steps"]["9"]["params"]["step_date"] == NEW_STEP_DATE

    # ④ inherited 徽标(#11 fork 不空洞过审):复用步骤沿祖先链定位产物记录,
    #    指纹一致才计入;source 形如 inherited(parent=<父 run>),产物指纹随带
    srcs = prov["evidence"]["step_sources"]
    for sid, arts in ((1, {"slc", "unw", "era5"}), (7, {"timeseries"})):
        entry = srcs[str(sid)]
        assert entry["origin"] == "inherited"
        assert entry["source"] == f"inherited(parent={parent})"
        assert entry["parent_run_id"] == parent
        assert set(entry["artifacts"]) == arts and all(entry["artifacts"].values())

    # ⑤ fork 不漂移云端语义:2-6 仍是 missing(无 manifest);8-11 本地执行
    assert all(srcs[str(sid)]["origin"] == "missing" for sid in CLOUD_STEPS)
    assert all(srcs[str(sid)]["origin"] == "local" for sid in (8, 9, 10, 11))

    # ⑥ 外部验证不随 fork 继承:父链 crossval_* 指标只列出供人判断
    pv = prov["evidence"]["parent_validations"]
    assert {v["name"] for v in pv} == {"crossval_r", "crossval_rmse_mm"}
    assert all(v["inherited"] is False and v["run_id"] == parent for v in pv)

    ctx["fork_id"] = fork_id


# ---------------------------------------------------------------------------
# J5 报告链:methods.md / figures / run.sh(对父 run,链路最完整)
# ---------------------------------------------------------------------------
def test_j5_report_chain_methods_figures_runsh(journey):
    client, ctx = journey["client"], journey["ctx"]
    parent = _need(ctx, "run_id")
    _need(ctx, "intervened")

    # ① methods.md:模板降级(无 LLM)如实标注;含同震阶跃(Heaviside)描述,
    #    阶跃日期直引 provenance(J3 干预后仍是 20190706 主震日)
    r = client.get("/api/methods.md", params={"session": SID, "run_id": parent})
    assert r.status_code == 200
    assert r.headers["x-narrate-source"] == "template"
    md = r.text
    assert "# 处理方法(自动生成草稿)" in md
    assert "模拟执行" in md
    assert "同震阶跃" in md and "Heaviside" in md
    assert "20190706" in md and NEW_STEP_DATE in md

    # ② 云端跳过段:如实声明由 ASF HyP3 完成 + 凭据缺失已降级(跳过 ≠ 免检)
    assert "云端(ASF HyP3)标准流程完成" in md
    assert "暂缺本地凭据" in md

    # ③ 图件引用与证据边界章节齐备,证据级别与账本一致(runnable)
    assert "## 图件引用" in md and "products/figures" in md
    assert "## 证据边界" in md and "runnable" in md

    # ④ figures 列表与产物账本一致:FIGURE 产物在账本(第 10 步产出);
    #    simulated 图目录里没有真实图像文件 → 列表如实为空,不硬造条目
    figs = client.get("/api/figures", params={"session": SID, "run_id": parent}).json()
    assert figs["run"] == parent and isinstance(figs["figures"], list)
    assert figs["figures"] == []
    prov = client.get("/api/provenance", params={"session": SID, "run_id": parent}).json()
    fig_arts = {aid: a for aid, a in prov["artifacts"].items() if a["kind"] == "FIGURE"}
    assert fig_arts and all(a["produced_by"] == 10 for a in fig_arts.values())

    # ⑤ run.sh:骨架 + 1→11 步骤序注释 + 跳过步说明恰好 5 处 + 执行步真命令
    sh = client.get("/api/run.sh", params={"session": SID, "run_id": parent}).text
    assert "set -euo pipefail" in sh and f"# run_id: {parent}" in sh
    pos = [sh.index(f"# ── 第 {sid} 步 ·") for sid in range(1, 12)]
    assert pos == sorted(pos)
    assert sh.count("本地无等价命令") == 5
    for sid in EXEC_STEPS:
        assert f"python .sim/s{sid:02d}.py" in sh


# ---------------------------------------------------------------------------
# J6a 边界:同 run 并发第二个 execute 被运行锁拒绝(§4.11)
# ---------------------------------------------------------------------------
def test_j6a_concurrent_execute_rejected_by_run_lock(journey):
    client, ctx = journey["client"], journey["ctx"]
    fork_id = _need(ctx, "fork_id")

    # 排队一个真实 science 变更(ramp quadratic→no)让胜者重跑 [8,9,10,11],
    # 窗口足够长,保证两个并发回合真的相遇在锁上
    r = client.post("/api/actions", json={
        "session": SID, "run_id": fork_id, "scope": "step", "target": "8",
        "action": "SET_PARAMS", "payload": {"params": {"ramp": "no"}},
        "deliver_as": "steer"})
    assert r.status_code == 202

    barrier = threading.Barrier(2)
    results: list[list[dict] | None] = [None, None]
    errors: list[BaseException | None] = [None, None]

    def fire(i: int) -> None:
        try:
            barrier.wait(timeout=30)
            results[i] = _stream(client, "/api/pipeline",
                                 {"session": SID, "run_id": fork_id})
        except BaseException as exc:  # noqa: BLE001 —— 收集到主线程统一断言
            errors[i] = exc

    threads = [threading.Thread(target=fire, args=(i,)) for i in (0, 1)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=300)
    assert not any(t.is_alive() for t in threads), "并发执行回合未在时限内结束"
    assert errors == [None, None], f"并发回合异常:{errors}"

    # ① 恰好一个被运行锁拒绝、一个真执行(租约互斥的外部可见语义)
    diag = [[e["t"] for e in r_ or []] for r_ in results]
    rejected = [r_ for r_ in results
                if any(e["t"] == "note" and "运行锁被占用" in e.get("text", "")
                       for e in r_ or [])]
    executed = [r_ for r_ in results if any(e["t"] == "step.start" for e in r_ or [])]
    assert len(rejected) == 1 and len(executed) == 1, f"锁语义异常,两流事件:{diag}"

    # ② 拒绝回合:一条 bad note 说明占用与接管路径,除此零事件零副作用
    note = rejected[0][0]
    assert [e["t"] for e in rejected[0]] == ["note"]
    assert note["tone"] == "bad"
    assert "并发执行被拒绝" in note["text"] and "接管" in note["text"]

    # ③ 胜者照常:消费干预(影响 [8,9,10,11])并只重跑该子集,一次到 done
    win = executed[0]
    iv = [e for e in win if e["t"] == "intervention"]
    assert len(iv) == 1 and iv[0]["affected"] == [8, 9, 10, 11]
    assert [e["stepId"] for e in win if e["t"] == "step.start"] == [8, 9, 10, 11]
    assert any(e["t"] == "result" for e in win)

    # ④ 终态一致:run done、新参数入账(锁已释放,后续节点还能执行)
    state = client.get("/api/state", params={"session": SID, "run_id": fork_id}).json()
    assert state["run"]["status"] == "done"
    prov = client.get("/api/provenance", params={"session": SID, "run_id": fork_id}).json()
    assert prov["steps"]["8"]["params"]["ramp"] == "no"
    ctx["lock_checked"] = True


# ---------------------------------------------------------------------------
# J6b 边界:对 skipped(云端已完成)步骤显式重跑被忽略并 note
# ---------------------------------------------------------------------------
def test_j6b_explicit_rerun_of_skipped_steps_ignored(journey):
    client, ctx = journey["client"], journey["ctx"]
    fork_id = _need(ctx, "fork_id")
    _need(ctx, "lock_checked")

    before = client.get("/api/provenance", params={"session": SID, "run_id": fork_id}).json()
    counts_before = {sid: len(before["steps"][str(sid)]["commands"])
                     for sid in range(1, 12)}

    events = _stream(client, "/api/pipeline",
                     {"session": SID, "run_id": fork_id, "step_ids": [2, 3, 4, 5, 6]})

    # ① 云端步骤没有本地作业可执行:一步不跑
    assert not [e for e in events if e["t"] == "step.start"]

    # ② note 文案:列出被忽略集合、说明产物由云端交付、指路 RESET
    ignore = [e for e in events
              if e["t"] == "note" and "忽略云端已完成步骤" in e.get("text", "")]
    assert len(ignore) == 1 and ignore[0]["tone"] == "warn"
    assert "[2, 3, 4, 5, 6]" in ignore[0]["text"]
    assert "云端交付" in ignore[0]["text"] and "RESET" in ignore[0]["text"]

    # ③ 状态与账本零漂移:run 仍 done、全部步骤命令计数不变、2-6 仍 skipped
    state = client.get("/api/state", params={"session": SID, "run_id": fork_id}).json()
    assert state["run"]["status"] == "done"
    after = client.get("/api/provenance", params={"session": SID, "run_id": fork_id}).json()
    counts_after = {sid: len(after["steps"][str(sid)]["commands"])
                    for sid in range(1, 12)}
    assert counts_after == counts_before
    assert all(after["steps"][str(sid)]["state"] == "skipped" for sid in CLOUD_STEPS)
