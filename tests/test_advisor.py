"""下一步建议引擎(report/advisor)+ /api/advise 端点契约。

覆盖:
  - 四种终态的建议正确性(done 达标 / done 证据偏低 / failed / interrupted);
  - 失败类映射与 core/failures.DISPOSITIONS 的同源性守护(全枚举参数化,
    DISPOSITIONS 改文案本测试自动跟随 —— 断言引用而非复述);
  - LLM 只润色不决策:合格润色替换 why,数字被改/新增 → 逐条回退规则文案,
    provider 异常整体回退,响应里的多余条目/坏 id 被忽略(条目数/标题/动作不变);
  - /api/report/draft 路由存在性运行时探测(不硬依赖);
  - API 归属校验(跨会话 404,口径同 resolve_run)与非终态空建议(note 运行中)。

全部离线、零真实计算(LLM 一律假 provider)。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from insar_agent.api.advisor_router import route_paths
from insar_agent.api.app import create_app
from insar_agent.brain.provider import BrainUnavailable
from insar_agent.core.db import Database
from insar_agent.core.failures import DISPOSITIONS, FailureClass
from insar_agent.core.store import Store
from insar_agent.report.advisor import QA_STEP_ID, TERMINAL_STATUSES, advise

_HASHES = {"task_hash": "t0", "args_hash": "a0",
           "local_hash": "l0", "eval_hash": "e0" * 6}


# ---------------------------------------------------------------------------
# 造数助手
# ---------------------------------------------------------------------------

def mk_run(store: Store, run_id: str, status: str, *, session: str = "s1",
           workspace: str = "ws", simulated: bool = False) -> None:
    store.create_session(session, session)
    store.create_run(run_id, session, workspace=workspace, simulated=simulated)
    store.set_run_status(run_id, status)


def add_step(store: Store, run_id: str, sid: int, *, state: str = "done",
             run_ok: int | None = 1, failure_class: str | None = None,
             params: dict | None = None, name: str | None = None) -> None:
    store.upsert_step(run_id, sid, capability=f"cap{sid}", name=name or f"步骤{sid}",
                      method="m", params=params or {}, hashes=_HASHES)
    if state == "done":
        # VERIFIED + run_ok:证据阶梯 L0/L1 的判定输入(store.advance 单调推进)
        store.advance(run_id, sid, "VERIFIED", state="done", run_ok=run_ok)
    elif state != "pending":
        store.mark_step(run_id, sid, state=state, failure_class=failure_class)


def by_id(result: dict) -> dict:
    return {s["id"]: s for s in result["suggestions"]}


def done_run(store: Store, run_id: str = "r-done", *, with_qa: bool = False,
             qa_pending: bool = True, simulated: bool = False) -> None:
    """10 步 done(VERIFIED/run_ok=1)的完成 run;第 11 步可选进计划/已完成。"""
    mk_run(store, run_id, "done", simulated=simulated)
    for sid in range(1, 11):
        params = {"min_coherence": 0.25} if sid == 6 else {}
        add_step(store, run_id, sid, state="done", params=params)
    if with_qa:
        add_step(store, run_id, QA_STEP_ID,
                 state="pending" if qa_pending else "done",
                 params={"corr_threshold": 0.85})


# ---------------------------------------------------------------------------
# done + 证据级达标
# ---------------------------------------------------------------------------

def test_done_qualified_suggestions(store):
    """达标 done(11 步不在计划):QA 复核话术 + 复现包 + 敏感性重跑。"""
    done_run(store, with_qa=False)
    result = advise(store, "r-done")

    assert result["status"] == "done"
    # 全部步骤 VERIFIED+run_ok、无产物/指标缺口、无 GNSS → 阶梯停在 audited
    assert result["evidence_level"] == "audited"
    assert "audited" in result["context"]

    sg = by_id(result)
    # ① 第 11 步不在计划内:没有现成端点可指,回退预填话术
    qa = sg["qa-crossval"]
    assert qa["action"]["kind"] == "chat_prefill"
    assert "crossval_ps_sbas" in qa["action"]["text"]
    # ③ 复现包:现成端点 GET /api/repro-bundle(下载)
    bundle = sg["repro-bundle"]
    assert bundle["action"]["kind"] == "api_action"
    assert bundle["action"]["endpoint"] == "/api/repro-bundle"
    assert bundle["action"]["params"] == {"session": "s1", "run_id": "r-done"}
    assert bundle["action"]["download"] is True
    # ④ 敏感性重跑:预填话术引用当前 min_coherence 值
    sens = sg["sensitivity-rerun"]
    assert sens["action"]["kind"] == "chat_prefill"
    assert "min_coherence" in sens["action"]["text"]
    assert "0.25" in sens["action"]["text"]
    assert "第 6 步" in sens["action"]["text"]
    # ② 路由未探测到 /api/report/draft → 整条建议不出现(不硬依赖)
    assert "report-draft" not in sg


def test_done_qa_in_plan_pending_gets_api_action(store):
    """第 11 步在计划内但未跑:仍走达标分支(未跑的只剩质检不算证据偏低),
    QA 建议给现成端点(显式列表补跑);阶梯层面 runnable 如实上报。"""
    done_run(store, with_qa=True, qa_pending=True)
    result = advise(store, "r-done")
    assert result["evidence_level"] == "runnable"  # 阶梯诚实:有未完成步骤
    sg = by_id(result)
    qa = sg["qa-crossval"]
    assert qa["action"]["kind"] == "api_action"
    assert qa["action"]["endpoint"] == "/api/pipeline"
    assert qa["action"]["body"]["step_ids"] == [QA_STEP_ID]
    assert qa["action"]["body"]["session"] == "s1"
    assert "repro-bundle" in sg and "sensitivity-rerun" in sg
    assert "evidence-gap" not in sg


def test_done_qa_already_ran_no_suggestion(store):
    """第 11 步已 done:不再建议补跑 QA(建议列表随事实收缩)。"""
    done_run(store, with_qa=True, qa_pending=False)
    sg = by_id(advise(store, "r-done"))
    assert "qa-crossval" not in sg
    assert "repro-bundle" in sg  # 其余建议照常


def test_report_draft_route_probing(store):
    """/api/report/draft 运行时探测:路由在 → 建议出现;不在 → 消失。"""
    done_run(store)
    with_route = by_id(advise(store, "r-done",
                              available_routes={"/api/report/draft"}))
    without = by_id(advise(store, "r-done", available_routes=set()))
    assert "report-draft" in with_route
    assert with_route["report-draft"]["action"]["endpoint"] == "/api/report/draft"
    assert "report-draft" not in without


# ---------------------------------------------------------------------------
# done 但证据级偏低(runnable / 模拟)
# ---------------------------------------------------------------------------

def test_done_simulated_low_evidence(store):
    """模拟 run:诚实说明封顶 runnable、差在哪、补什么;不建议导出复现包。"""
    done_run(store, run_id="r-sim", simulated=True)
    result = advise(store, "r-sim")

    assert result["evidence_level"] == "runnable"
    assert "模拟执行" in result["context"]
    sg = by_id(result)
    # 越级动作(复现包/QA 复核/敏感性)一概不出:证据不够先补可信度
    assert set(sg) == {"evidence-gap", "env-real-rerun"}
    gap = sg["evidence-gap"]
    assert "runnable" in gap["why"]
    assert "模拟执行" in gap["why"]
    assert "checked" in gap["why"]  # 对照 ladder:下一级是什么、要什么
    assert gap["action"] == {"kind": "open_tab", "tab": "audit"}
    assert sg["env-real-rerun"]["action"] == {"kind": "open_tab", "tab": "env"}


def test_done_runnable_not_simulated_prefills_fix(store):
    """非模拟但停在 runnable(有步骤未完成):补齐方案交给 Agent(预填话术)。"""
    mk_run(store, "r-low", "done")
    add_step(store, "r-low", 1, state="done")
    add_step(store, "r-low", 2, state="pending")  # 未完成步骤 → 阶梯停 runnable
    sg = by_id(advise(store, "r-low"))
    assert set(sg) == {"evidence-gap", "evidence-fix"}
    assert sg["evidence-fix"]["action"]["kind"] == "chat_prefill"
    assert "runnable" in sg["evidence-fix"]["action"]["text"]


# ---------------------------------------------------------------------------
# failed:与 DISPOSITIONS 同源
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fc", list(FailureClass))
def test_failed_disposition_same_source(store, fc):
    """同源性守护:每个失败类的建议文案必须内嵌 DISPOSITIONS 的处置原文。

    断言引用 DISPOSITIONS[fc]["note"] 本身 —— 处置表改文案时测试自动跟随,
    advisor 若自造一套处置说法立刻在此爆红。
    """
    run_id = f"r-fail-{fc.value}"
    mk_run(store, run_id, "failed")
    add_step(store, run_id, 1, state="done")
    add_step(store, run_id, 3, state="failed", failure_class=fc.value, name="配准")
    add_step(store, run_id, 4, state="pending")

    result = advise(store, run_id)
    sg = by_id(result)
    disp = sg["failure-disposition"]

    assert DISPOSITIONS[fc]["note"] in disp["why"]  # 同源:原文内嵌
    assert fc.value in disp["title"]
    assert "第 3 步" in disp["why"]
    # 可自动重试的类给现成端点(显式列表 = RESET+续跑);其余交 Agent 决策
    if DISPOSITIONS[fc]["auto"] in ("retry", "retry_reduced"):
        assert disp["action"]["kind"] == "api_action"
        assert disp["action"]["body"]["step_ids"] == [3]
    else:
        assert disp["action"]["kind"] == "chat_prefill"
        assert DISPOSITIONS[fc]["note"] in disp["action"]["text"]

    # 「从断点续跑」:显式列表 = 失败步 + 待跑步骤(已完成部分不重算)
    resume = sg["resume-breakpoint"]
    assert resume["action"]["endpoint"] == "/api/pipeline"
    assert resume["action"]["body"]["step_ids"] == [3, 4]

    # 「查看技能文档失败处置节」:带步骤号,跳流水线面板选中该步
    skill = sg["skill-failures"]
    assert "第 3 步" in skill["title"]
    assert "常见失败与处置" in skill["title"]
    assert skill["action"] == {"kind": "open_tab", "tab": "pipeline", "step": 3}

    assert fc.value in result["context"]


def test_failed_without_failed_step_falls_back_unknown(store):
    """run failed 但无失败步骤(如 driver 意外异常收尾):按 UNKNOWN 停链问人。"""
    mk_run(store, "r-odd", "failed")
    add_step(store, "r-odd", 1, state="done")
    sg = by_id(advise(store, "r-odd"))
    assert DISPOSITIONS[FailureClass.UNKNOWN]["note"] in sg["failure-disposition"]["why"]
    assert "skill-failures" not in sg  # 没有步骤号,不指没有目标的文档


# ---------------------------------------------------------------------------
# interrupted
# ---------------------------------------------------------------------------

def test_interrupted_resume_and_cause(store):
    mk_run(store, "r-int", "interrupted")
    add_step(store, "r-int", 1, state="done")
    add_step(store, "r-int", 2, state="interrupted")
    add_step(store, "r-int", 3, state="pending")

    result = advise(store, "r-int")
    sg = by_id(result)
    resume = sg["resume-run"]
    assert resume["action"] == {"kind": "api_action", "method": "POST",
                                "endpoint": "/api/resume", "body": {"session": "s1"}}
    cause = sg["interrupt-cause"]
    assert cause["action"] == {"kind": "open_tab", "tab": "term", "step": 2}
    assert "1/3 步完成" in result["context"]


def test_interrupted_orphaned_mentions_wsl_note(store):
    """环境停止(orphaned):续跑建议引用 WSL_ORPHANED 处置原文(同源)。"""
    mk_run(store, "r-orp", "interrupted")
    add_step(store, "r-orp", 2, state="orphaned")
    sg = by_id(advise(store, "r-orp"))
    assert DISPOSITIONS[FailureClass.WSL_ORPHANED]["note"] in sg["resume-run"]["why"]


# ---------------------------------------------------------------------------
# 非终态与建议形状
# ---------------------------------------------------------------------------

def test_non_terminal_empty_suggestions(store):
    mk_run(store, "r-run", "running")
    result = advise(store, "r-run")
    assert result["suggestions"] == []
    assert result["status"] == "running"


def test_unknown_run_raises(store):
    with pytest.raises(KeyError):
        advise(store, "ghost")


def test_suggestion_shape_closed_set(store):
    """建议形状契约:id/title/why/action 齐备,action.kind 是三选一闭集。"""
    done_run(store, run_id="r-a")
    mk_run(store, "r-b", "failed")
    add_step(store, "r-b", 1, state="failed", failure_class="oom")
    mk_run(store, "r-c", "interrupted")
    add_step(store, "r-c", 1, state="interrupted")

    for rid in ("r-a", "r-b", "r-c"):
        result = advise(store, rid)
        assert result["status"] in TERMINAL_STATUSES
        assert isinstance(result["context"], str) and result["context"]
        assert result["suggestions"], rid
        for s in result["suggestions"]:
            assert set(s) == {"id", "title", "why", "action"}
            assert s["action"]["kind"] in ("chat_prefill", "api_action", "open_tab")


# ---------------------------------------------------------------------------
# LLM 润色:只动措辞,决策与数字不可动,失败回退
# ---------------------------------------------------------------------------

class FakeProvider:
    """duck-type LLMProvider:enabled + complete_json(system=, user=, max_tokens=)。"""

    def __init__(self, reply=None, error=None):
        self.reply = reply
        self.error = error
        self.calls = 0
        self.enabled = True

    def complete_json(self, *, system: str, user: str, max_tokens: int = 512) -> dict:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.reply if not callable(self.reply) else self.reply(user)


def test_polish_replaces_why_when_numbers_preserved(store):
    """合格润色(数字集合一致)→ why 换新,决策字段纹丝不动。"""
    mk_run(store, "r-p1", "interrupted")
    add_step(store, "r-p1", 2, state="interrupted")
    base = advise(store, "r-p1")  # 规则文案基线

    def polish(_user):
        # 就地重述 interrupt-cause 的 why:保留「第 2 步」的 2,措辞全换
        return {"items": [{"id": "interrupt-cause",
                           "why": "执行停在第 2 步:去终端面板看日志尾部即可定位中断现场"}]}

    provider = FakeProvider(reply=polish)
    result = advise(store, "r-p1", provider=provider)
    assert provider.calls == 1
    assert result["polish_source"] == "llm"
    sg, sg0 = by_id(result), by_id(base)
    assert sg["interrupt-cause"]["why"] == "执行停在第 2 步:去终端面板看日志尾部即可定位中断现场"
    # 未被润色的条目保留规则文案;标题与动作(决策部分)永不受 LLM 影响
    assert sg["resume-run"]["why"] == sg0["resume-run"]["why"]
    assert ({s["title"] for s in result["suggestions"]}
            == {s["title"] for s in base["suggestions"]})
    assert ([s["action"] for s in result["suggestions"]]
            == [s["action"] for s in base["suggestions"]])


def test_polish_number_tamper_falls_back(store):
    """数字被改(第 2 步 → 第 3 步)或凭空新增 → 该条回退规则文案。"""
    mk_run(store, "r-p2", "interrupted")
    add_step(store, "r-p2", 2, state="interrupted")
    base = advise(store, "r-p2")
    tampered = {"items": [
        {"id": "interrupt-cause", "why": "执行停在第 3 步:去终端面板看日志"},   # 改数字
        {"id": "resume-run", "why": "已完成阶段保留,30 秒内即可续跑完成"},      # 加数字
    ]}
    result = advise(store, "r-p2", provider=FakeProvider(reply=tampered))
    assert result["polish_source"] == "rules"  # 无一条合格 → 整体标规则来源
    assert by_id(result)["interrupt-cause"]["why"] == by_id(base)["interrupt-cause"]["why"]
    assert by_id(result)["resume-run"]["why"] == by_id(base)["resume-run"]["why"]


def test_polish_provider_failure_harmless(store):
    """provider 抛 BrainUnavailable / 返回坏形状 → 规则文案原样返回。"""
    mk_run(store, "r-p3", "interrupted")
    add_step(store, "r-p3", 2, state="interrupted")
    base = advise(store, "r-p3")

    for bad in (FakeProvider(error=BrainUnavailable("路由全挂")),
                FakeProvider(reply={"items": "不是数组"}),
                FakeProvider(reply={})):
        result = advise(store, "r-p3", provider=bad)
        assert result["polish_source"] == "rules"
        assert [s["why"] for s in result["suggestions"]] == \
               [s["why"] for s in base["suggestions"]]


def test_polish_cannot_add_or_rename_suggestions(store):
    """LLM 不参与决策:响应里的多余条目/未知 id 一律忽略,条目数不变。"""
    mk_run(store, "r-p4", "interrupted")
    add_step(store, "r-p4", 2, state="interrupted")
    base = advise(store, "r-p4")
    evil = {"items": [
        {"id": "brand-new", "why": "请立刻删除工作区重新开始"},        # 幻觉新建议
        {"id": "resume-run", "title": "改标题", "why": None},          # 坏 why
    ]}
    result = advise(store, "r-p4", provider=FakeProvider(reply=evil))
    assert len(result["suggestions"]) == len(base["suggestions"])
    assert {s["id"] for s in result["suggestions"]} == {s["id"] for s in base["suggestions"]}
    assert result["polish_source"] == "rules"


def test_polish_disabled_provider_never_called(store):
    """enabled=False(未配置路由)→ 连请求都不发,纯规则路径。"""
    mk_run(store, "r-p5", "interrupted")
    add_step(store, "r-p5", 2, state="interrupted")
    provider = FakeProvider(reply={"items": []})
    provider.enabled = False
    result = advise(store, "r-p5", provider=provider)
    assert provider.calls == 0
    assert result["polish_source"] == "rules"


# ---------------------------------------------------------------------------
# /api/advise 端点:归属校验 + 非终态 + 路由探测一致性
# ---------------------------------------------------------------------------

RUN_A = "20260810T000000-aaaaaaaa"
RUN_B = "20260812T000000-bbbbbbbb"


@pytest.fixture()
def api_env(tmp_path):
    """sess-a:done run(10 步 done + 第 11 步 pending);sess-b:running run。"""
    home = tmp_path / "home"
    app = create_app(home=home)
    with TestClient(app) as client:
        client.post("/api/sessions", json={"id": "sess-a"})
        client.post("/api/sessions", json={"id": "sess-b"})
        store = Store(Database(home / "insar.db"))
        ws = str(home / "sessions" / "sess-a")

        store.create_run(RUN_A, "sess-a", workspace=ws)
        store.set_run_status(RUN_A, "done")
        for sid in range(1, 11):
            params = {"min_coherence": 0.25} if sid == 6 else {}
            add_step(store, RUN_A, sid, state="done", params=params)
        add_step(store, RUN_A, QA_STEP_ID, state="pending")

        store.create_run(RUN_B, "sess-b", workspace=str(home / "sessions" / "sess-b"))
        store.set_run_status(RUN_B, "running")

        yield {"client": client, "store": store, "app": app}
        store.close()


def test_api_advise_done_run(api_env):
    r = api_env["client"].get("/api/advise", params={"session": "sess-a"})
    assert r.status_code == 200
    data = r.json()
    assert data["run_id"] == RUN_A
    assert data["status"] == "done"
    ids = {s["id"] for s in data["suggestions"]}
    assert {"qa-crossval", "repro-bundle", "sensitivity-rerun"} <= ids
    # 复现包动作直接指向现成端点,参数带归属(前端拼 URL 即用)
    bundle = next(s for s in data["suggestions"] if s["id"] == "repro-bundle")
    assert bundle["action"]["params"] == {"session": "sess-a", "run_id": RUN_A}


def test_api_advise_route_probe_consistency(api_env):
    """draft 建议出现与否 == 应用里真有没有 /api/report/draft 路由(并行开发安全:
    并行代理若挂上该路由,本断言自动跟随,不锁死「必须缺席」)。"""
    paths = route_paths(api_env["app"])
    has_draft = "/api/report/draft" in paths
    data = api_env["client"].get("/api/advise", params={"session": "sess-a"}).json()
    ids = {s["id"] for s in data["suggestions"]}
    assert ("report-draft" in ids) == has_draft


def test_route_paths_sees_included_routers(api_env):
    """路由收集器守护:include_router 挂载的端点必须可被探测到。

    新版 starlette 把 include_router 包成 _IncludedRouter(顶层无 path),
    只扫 app.routes 顶层会让探测永假 —— 以本模块自己的 /api/advise 与
    另一个 include_router 端点 /api/skills 做自见性断言。
    """
    paths = route_paths(api_env["app"])
    assert "/api/advise" in paths
    assert "/api/skills" in paths


def test_api_advise_ownership_404(api_env):
    """跨会话按「不存在」处理,错误文案不泄露真实归属(resolve_run 口径)。"""
    r = api_env["client"].get("/api/advise",
                              params={"session": "sess-b", "run": RUN_A})
    assert r.status_code == 404
    assert "sess-a" not in r.text


def test_api_advise_non_terminal_note(api_env):
    r = api_env["client"].get("/api/advise", params={"session": "sess-b"})
    assert r.status_code == 200
    assert r.json() == {"run_id": RUN_B, "status": "running",
                        "suggestions": [], "note": "运行中"}


def test_api_advise_no_run_404(api_env):
    assert api_env["client"].get(
        "/api/advise", params={"session": "ghost"}).status_code == 404


def test_api_advise_run_id_alias(api_env):
    """run 与 run_id 两个参数名等价(与其他端点的 run_id 习惯兼容)。"""
    a = api_env["client"].get("/api/advise",
                              params={"session": "sess-a", "run": RUN_A}).json()
    b = api_env["client"].get("/api/advise",
                              params={"session": "sess-a", "run_id": RUN_A}).json()
    assert a["run_id"] == b["run_id"] == RUN_A
