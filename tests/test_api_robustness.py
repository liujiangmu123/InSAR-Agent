"""API 全端点健壮性 + 安全审计(输入面 / 归属隔离 / 路径穿越 / 竞态 / 流协议)。

纪律:
  - 非法输入一律 4xx + 结构化 JSON(FastAPI {"detail": ...}),绝不 500 堆栈;
  - 校验挡在 API 边界(app.py),store/driver 语义不动;
  - 流式端点错误是事件不是裸断连;客户端断开不产生 status=running 的孤儿 run;
  - session_id 会成为目录名:路径穿越/非法字符/Windows 保留名必须挡下。
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from conftest import TIME_FACTOR
from insar_agent.api.app import create_app
from insar_agent.core.db import Database
from insar_agent.core.store import StageConflict, Store

HASHES = {"task_hash": "t", "args_hash": "a", "local_hash": "l", "eval_hash": "e"}


def _seal(monkeypatch) -> None:
    """密封测试:probe 返回空引擎(不受宿主 PATH 影响);WSL 探测短路(不碰 wsl.exe)。"""
    from insar_agent.runtime.probe import ProbeResult

    def empty_probe(*args, **kwargs):
        return ProbeResult(
            engines={k: None for k in ("isce2", "mintpy", "snaphu", "gdal", "snap",
                                       "pystamps", "pyaps")},
            credentials={"earthdata": False, "cds": False, "gacos": False},
            disk_free_gb=100.0, cpu_count=8)

    monkeypatch.setattr("insar_agent.loop.driver.probe_environment", empty_probe)
    monkeypatch.setattr("insar_agent.runtime.wsl_probe.probe_wsl_engines",
                        lambda **kw: {"ok": False})


@pytest.fixture()
def home(tmp_path) -> Path:
    return tmp_path / "home"


@pytest.fixture()
def client(home, monkeypatch):
    _seal(monkeypatch)
    app = create_app(home=home)
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def live_server(home, monkeypatch):
    """真实 uvicorn 服务器(线程 + 随机端口)。

    TestClient(httpx ASGITransport)会缓冲整个响应:对无限的 SSE 流永远不返回,
    也无法产生真实的客户端断连 —— SSE 合规与断连孤儿测试必须打真实 socket。
    """
    import uvicorn

    _seal(monkeypatch)
    app = create_app(home=home)
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning",
                            timeout_graceful_shutdown=3)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 20 * TIME_FACTOR  # 启动等待上限,负载系数放宽
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


def _stream_events(client: TestClient, url: str, body: dict) -> list[dict]:
    events = []
    with client.stream("POST", url, json=body) as resp:
        assert resp.status_code == 200
        for line in resp.iter_lines():
            if line.strip():
                events.append(json.loads(line))
    return events


def _mk_run(client: TestClient, session: str = "demo") -> str:
    """建会话 → 规划 → 执行到 done,返回 run_id。"""
    client.post("/api/sessions", json={"id": session})
    _stream_events(client, "/api/turn", {"session": session, "text": "Ridgecrest 地震同震"})
    _stream_events(client, "/api/pipeline", {"session": session})
    state = client.get("/api/state", params={"session": session}).json()
    assert state["run"]["status"] == "done"
    return state["run"]["run_id"]


def _detail_of(resp) -> object:
    body = resp.json()  # 报错体必须是结构化 JSON,而非 HTML/堆栈文本
    assert "detail" in body, body
    return body["detail"]


# ---------------- 1. 输入面:非法 session_id ----------------

BAD_SESSION_IDS = [
    "../evil", "..\\evil", "a/b", "a\\b",             # 路径穿越/分隔符
    "..", "...",                                       # 纯点号(结尾 '.')
    "a:b", 'a"b', "a*b", "a?b", "a<b", "a|b",          # Windows 非法字符
    "bad\tid", "bad\nid",                              # 控制字符
    "trailing.", "trailing ", " leading",              # 首尾空格/点
    "CON", "nul", "COM1.txt",                          # Windows 保留设备名
    "x" * 65,                                          # 超长
]


@pytest.mark.parametrize("sid", BAD_SESSION_IDS)
def test_bad_session_id_rejected_on_query_endpoints(client, sid):
    for url in ("/api/registry", "/api/env", "/api/events"):
        r = client.get(url, params={"session": sid})
        assert r.status_code == 400, f"{url} 对 {sid!r} 返回 {r.status_code}"
        _detail_of(r)


@pytest.mark.parametrize("sid", BAD_SESSION_IDS)
def test_bad_session_id_rejected_on_body_endpoints(client, sid):
    r = client.post("/api/sessions", json={"id": sid})
    assert r.status_code == 400
    _detail_of(r)
    r2 = client.post("/api/turn", json={"session": sid, "text": "hi"})
    assert r2.status_code == 400
    _detail_of(r2)


def test_traversal_session_id_creates_nothing_outside_home(client, home, tmp_path):
    """核心安全断言:穿越型 id 不会在 home 之外(或 sessions 之外)创建目录。"""
    for sid in ("../evil", "../../evil", "..\\..\\evil"):
        assert client.get("/api/registry", params={"session": sid}).status_code == 400
        assert client.post("/api/sessions", json={"id": sid}).status_code == 400
    assert not (home / "evil").exists()
    assert not (tmp_path / "evil").exists()
    assert not (tmp_path.parent / "evil").exists()
    # 会话表也不留半截行
    ids = {s["session_id"] for s in client.get("/api/sessions").json()}
    assert not any("evil" in i for i in ids)


def test_unicode_session_id_still_accepted(client, home):
    """黑名单式校验:中文等合法目录名不受影响(回归护栏)。"""
    r = client.post("/api/sessions", json={"id": "会话-中文01"})
    assert r.status_code == 200
    assert r.json()["session_id"] == "会话-中文01"
    events = _stream_events(client, "/api/turn",
                            {"session": "会话-中文01", "text": "Ridgecrest 地震同震"})
    assert any(e["t"] == "plan" for e in events)
    assert (home / "sessions" / "会话-中文01").is_dir()


def test_empty_session_id_rejected(client):
    assert client.post("/api/sessions", json={"id": ""}).status_code == 400
    assert client.post("/api/turn", json={"session": "", "text": "hi"}).status_code == 400
    assert client.get("/api/env", params={"session": ""}).status_code == 400
    # registry 的 session 为空串时走「无会话」分支(返回全量 registry)——现状契约
    assert client.get("/api/registry", params={"session": ""}).status_code == 200


# ---------------- 1. 输入面:缺参 / 坏 JSON / 类型错误 ----------------

def test_malformed_json_body_is_422_not_500(client):
    for url in ("/api/turn", "/api/pipeline", "/api/actions", "/api/fork",
                "/api/message", "/api/sessions", "/api/abort", "/api/admin/terminate"):
        r = client.post(url, content=b"{not json", headers={"content-type": "application/json"})
        assert r.status_code == 422, f"{url} → {r.status_code}"
        _detail_of(r)


def test_missing_required_fields_422(client):
    assert client.post("/api/turn", json={}).status_code == 422
    assert client.post("/api/fork", json={"session": "demo"}).status_code == 422
    assert client.post("/api/actions", json={"session": "demo"}).status_code == 422
    assert client.get("/api/impact", params={"session": "demo"}).status_code == 422  # 缺 step
    assert client.get("/api/logs", params={"session": "demo"}).status_code == 422    # 缺 step
    assert client.get("/api/state").status_code == 422                               # 缺 session


def test_wrong_types_422(client):
    assert client.post("/api/sessions", json={"id": 123}).status_code == 422
    assert client.post("/api/turn", json={"session": "demo", "text": ["l"]}).status_code == 422
    assert client.post("/api/pipeline",
                       json={"session": "demo", "step_ids": "7"}).status_code == 422
    assert client.post("/api/fork",
                       json={"session": "demo", "run_id": "r",
                             "changes": {"abc": {}}}).status_code == 422
    r = client.get("/api/impact", params={"session": "demo", "step": "abc"})
    assert r.status_code == 422


def test_huge_numbers_bounded_not_sqlite_overflow(client):
    """step 超过 SQLite INTEGER 边界曾会 OverflowError → 500;现在 422 挡下。"""
    _mk_run(client)
    huge = str(10 ** 30)
    assert client.get("/api/impact",
                      params={"session": "demo", "step": huge}).status_code == 422
    assert client.get("/api/logs",
                      params={"session": "demo", "step": huge}).status_code == 422
    assert client.get("/api/logs", params={"session": "demo", "step": 1,
                                           "tail_kb": 0}).status_code == 422
    assert client.get("/api/logs", params={"session": "demo", "step": 1,
                                           "tail_kb": 4096}).status_code == 422


def test_unknown_run_id_404_everywhere(client):
    client.post("/api/sessions", json={"id": "demo"})
    ghost = "20990101T000000-deadbeef"
    for url in ("/api/provenance", "/api/run.sh", "/api/methods.md"):
        r = client.get(url, params={"session": "demo", "run_id": ghost})
        assert r.status_code == 404, url
    assert client.get("/api/impact", params={
        "session": "demo", "step": 7, "run_id": ghost}).status_code == 404
    assert client.get("/api/logs", params={
        "session": "demo", "step": 1, "run_id": ghost}).status_code == 404
    assert client.post("/api/abort",
                       json={"session": "demo", "run_id": ghost}).status_code == 404
    assert client.post("/api/pipeline",
                       json={"session": "demo", "run_id": ghost}).status_code == 404
    assert client.post("/api/fork", json={
        "session": "demo", "run_id": ghost, "changes": {}}).status_code == 404
    assert client.post("/api/admin/terminate",
                       json={"run_id": ghost}).status_code == 404
    # 无 run 时的空态契约保持不变
    assert client.get("/api/state", params={"session": "demo"}).json() == {
        "run": None, "steps": []}
    assert client.get("/api/trace", params={"session": "demo"}).json() == []


def test_impact_bad_params_and_step(client):
    _mk_run(client)
    r = client.get("/api/impact", params={"session": "demo", "step": 7,
                                          "params": "{bad json"})
    assert r.status_code == 400  # 曾是 json.JSONDecodeError → 500
    _detail_of(r)
    r2 = client.get("/api/impact", params={"session": "demo", "step": 7,
                                           "params": "[1,2]"})
    assert r2.status_code == 400  # 非对象曾在 refresh_run 里 TypeError → 500
    r3 = client.get("/api/impact", params={"session": "demo", "step": 999})
    assert r3.status_code == 404  # 步骤不在 run 内
    r4 = client.get("/api/impact", params={"session": "demo", "step": 7,
                                           "method": "no_such_method"})
    assert r4.status_code == 400
    # 合法调用不受影响(成功路径契约不变)
    ok = client.get("/api/impact", params={
        "session": "demo", "step": 7,
        "params": json.dumps({"max_temporal_baseline": 90})})
    assert ok.status_code == 200
    assert [a["step_id"] for a in ok.json()["affected"]] == [7, 8, 9, 10, 11]


def test_fork_invalid_changes_400(client):
    run_id = _mk_run(client)
    r = client.post("/api/fork", json={
        "session": "demo", "run_id": run_id,
        "changes": {"9": {"params": {"nonexistent_param": 1}}}})
    assert r.status_code == 400  # 曾是 fork_run ValueError → 500
    assert "参数校验失败" in str(_detail_of(r))
    r2 = client.post("/api/fork", json={
        "session": "demo", "run_id": run_id, "changes": {"99": {"method": "x"}}})
    assert r2.status_code == 400
    r3 = client.post("/api/fork", json={
        "session": "demo", "run_id": run_id,
        "changes": {"9": {"method": "no_such_method"}}})
    assert r3.status_code == 400
    # 合法 fork 不受影响
    ok = client.post("/api/fork", json={
        "session": "demo", "run_id": run_id,
        "changes": {"9": {"method": "exponential"}}})
    assert ok.status_code == 200 and ok.json()["runId"]


def test_pipeline_invalid_step_ids_rejected_before_stream(client):
    _mk_run(client)
    r = client.post("/api/pipeline", json={"session": "demo", "step_ids": [999]})
    assert r.status_code == 400  # 曾在流中 KeyError 炸断连接
    _detail_of(r)
    r2 = client.post("/api/pipeline", json={"session": "demo", "step_ids": [-1]})
    assert r2.status_code == 400
    # 无 run 时带 step_ids → 404(不开流)
    client.post("/api/sessions", json={"id": "fresh"})
    r3 = client.post("/api/pipeline", json={"session": "fresh", "step_ids": [7]})
    assert r3.status_code == 404


def test_actions_validation_rejects_poison(client):
    client.post("/api/sessions", json={"id": "demo"})
    bad_bodies = [
        {"action": "DROP TABLE", "target": "6"},                       # 未知动作
        {"action": "SET_METHOD", "target": "abc",
         "payload": {"method": "icu"}},                                # target 非整数
        {"action": "SET_METHOD", "target": "999",
         "payload": {"method": "icu"}},                                # 未知步骤
        {"action": "SET_METHOD", "target": "6", "payload": {}},        # 缺 payload.method
        {"action": "SET_METHOD", "target": "6",
         "payload": {"method": "no_such"}},                            # 未知方法
        {"action": "SET_PARAMS", "target": "6", "payload": {}},        # 缺 payload.params
        {"action": "SET_PARAMS", "target": "6",
         "payload": {"params": [1, 2]}},                               # params 非对象
        {"action": "SET_PARAMS", "target": "6",
         "payload": {"params": {"bogus": 1}}},                         # 未声明参数
        {"action": "SKIP", "target": "6", "deliver_as": "bogus"},      # 曾 ValueError → 500
        {"action": "SKIP", "target": "6", "scope": "global"},          # 未知 scope
    ]
    for extra in bad_bodies:
        body = {"session": "demo", "scope": "step", "deliver_as": "next_run",
                "payload": {}, **extra}
        r = client.post("/api/actions", json=body)
        assert r.status_code == 400, f"{extra} → {r.status_code}: {r.text}"
        _detail_of(r)
    # 合法动作仍 202,且之后的 turn 不被队列毒死
    ok = client.post("/api/actions", json={
        "session": "demo", "scope": "step", "target": "9", "action": "SET_METHOD",
        "payload": {"method": "exponential"}, "deliver_as": "next_run"})
    assert ok.status_code == 202
    events = _stream_events(client, "/api/turn",
                            {"session": "demo", "text": "Ridgecrest 地震同震"})
    assert any(e["t"] == "plan" for e in events)


def test_message_unknown_session_404_not_integrity_error(client):
    r = client.post("/api/message", json={"session": "ghost", "text": "hi"})
    assert r.status_code == 404  # 曾是 sqlite3.IntegrityError → 500
    _detail_of(r)
    client.post("/api/sessions", json={"id": "demo"})
    r2 = client.post("/api/message",
                     json={"session": "demo", "text": "hi", "deliver_as": "bogus"})
    assert r2.status_code == 400
    r3 = client.post("/api/message", json={"session": "demo", "text": "hi"})
    assert r3.status_code == 202


def test_huge_and_control_char_text_survives(client):
    client.post("/api/sessions", json={"id": "demo"})
    weird = "Ridgecrest 地震 \x01\x02\t\n" + "A" * 100_000
    events = _stream_events(client, "/api/turn", {"session": "demo", "text": weird})
    assert events, "大文本回合应正常出流"
    assert client.get("/api/chat", params={"session": "demo"}).status_code == 200


# ---------------- 2. 会话/运行归属隔离 ----------------

def test_cross_session_run_isolation(client):
    """A 会话的 run 经 B 会话路径操作必须 404,且 A 的 run 不被扰动。"""
    run_a = _mk_run(client, "alice")
    client.post("/api/sessions", json={"id": "bob"})

    read_urls = ["/api/state", "/api/trace", "/api/provenance", "/api/run.sh",
                 "/api/methods.md"]
    for url in read_urls:
        r = client.get(url, params={"session": "bob", "run_id": run_a})
        assert r.status_code == 404, f"{url} 跨会话读应拒绝,得到 {r.status_code}"
    assert client.get("/api/impact", params={
        "session": "bob", "step": 7, "run_id": run_a}).status_code == 404
    assert client.get("/api/logs", params={
        "session": "bob", "step": 1, "run_id": run_a}).status_code == 404

    # 变更类:abort / pipeline / fork / actions(KILL)
    assert client.post("/api/abort",
                       json={"session": "bob", "run_id": run_a}).status_code == 404
    assert client.post("/api/pipeline",
                       json={"session": "bob", "run_id": run_a}).status_code == 404
    assert client.post("/api/fork", json={
        "session": "bob", "run_id": run_a,
        "changes": {"9": {"method": "exponential"}}}).status_code == 404
    assert client.post("/api/actions", json={
        "session": "bob", "scope": "run", "target": run_a, "action": "KILL",
        "run_id": run_a, "payload": {}, "deliver_as": "steer"}).status_code == 404

    # A 的 run 未被扰动:状态仍 done,control 未被置 cancel
    state = client.get("/api/state", params={"session": "alice"}).json()
    assert state["run"]["run_id"] == run_a
    assert state["run"]["status"] == "done"
    assert state["run"]["control"] == "running"
    # A 自己的访问不受影响
    assert client.get("/api/provenance", params={
        "session": "alice", "run_id": run_a}).status_code == 200


def test_own_session_paths_still_work(client):
    """归属校验不改变本会话成功路径(前端契约不变)。"""
    run_id = _mk_run(client)
    for url in ("/api/state", "/api/trace", "/api/provenance", "/api/run.sh",
                "/api/methods.md"):
        r = client.get(url, params={"session": "demo", "run_id": run_id})
        assert r.status_code == 200, url
    assert client.post("/api/abort",
                       json={"session": "demo", "run_id": run_id}).status_code == 202


# ---------------- 3. 路径与文件类端点 ----------------

def test_export_endpoints_reject_pathlike_run_ids(client):
    """run_id 不是文件路径:穿越型 run_id 在归属解析处就 404,不碰文件系统。"""
    client.post("/api/sessions", json={"id": "demo"})
    for evil in ("../../etc/passwd", "..\\..\\secret", "a/../../b"):
        for url in ("/api/provenance", "/api/run.sh", "/api/methods.md", "/api/trace"):
            r = client.get(url, params={"session": "demo", "run_id": evil})
            assert r.status_code in (404, 200), url  # trace 空态返回 []
            if r.status_code == 200:
                assert r.json() == []


def test_logs_endpoint_paths_come_from_db_only(client, home):
    """logs 只读 DB 里的 log_path(服务端写入),step 越界/无日志一律 404。"""
    _mk_run(client)
    ok = client.get("/api/logs", params={"session": "demo", "step": 1})
    assert ok.status_code == 200
    assert "X-Log-Size" in ok.headers
    # 云端跳过的步骤没有日志文件
    assert client.get("/api/logs",
                      params={"session": "demo", "step": 3}).status_code == 404
    assert client.get("/api/logs",
                      params={"session": "demo", "step": 12345}).status_code == 404


# ---------------- 4. admin terminate 并发 CAS ----------------

@pytest.mark.timing
def test_admin_terminate_race_with_settle_no_double_terminal(client, home):
    """并发 terminate × N + 执行器结算:命令只结算一次,步骤单一终态,
    stage 高水位不回退,不出现 500。"""
    client.post("/api/sessions", json={"id": "demo"})
    db = Database(home / "insar.db")
    st = Store(db)
    try:
        st.create_run("r-race", "demo", workspace=str(home / "sessions" / "demo"))
        st.create_step("r-race", 6, capability="6", name="解缠", method="snaphu_mcf",
                       params={}, hashes=HASHES)
        cid = st.reserve_command("r-race", 6, ["echo", "hi"])
        st.advance("r-race", 6, "PREPARED", state="running", started_at=time.time())
        st.advance("r-race", 6, "LAUNCHED", job_dir=str(home / "no-such-job"),
                   command_id=cid)
        st.set_run_status("r-race", "running")

        responses: list = []
        errors: list = []
        barrier = threading.Barrier(5)

        def hit_terminate():
            barrier.wait()
            try:
                responses.append(client.post(
                    "/api/admin/terminate", json={"run_id": "r-race", "reason": "竞态"}))
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        def settle_like_executor():
            barrier.wait()
            try:
                st.advance("r-race", 6, "VERIFIED", state="done", run_ok=1,
                           ended_at=time.time())
                st.settle_command(cid, exit_code=0, duration=0.1)
            except StageConflict:
                pass  # 竞争的输者自停,合法
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=hit_terminate) for _ in range(4)]
        threads.append(threading.Thread(target=settle_like_executor))
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30 * TIME_FACTOR)
        assert not any(t.is_alive() for t in threads)
        assert not errors, errors
        assert all(r.status_code == 200 for r in responses), \
            [(r.status_code, r.text) for r in responses]

        step = st.load_step("r-race", 6)
        # 单一终态:绝不出现 running/半截态;stage 单调(不被终结者回退)
        assert step.state in ("done", "interrupted"), step.state
        assert step.stage in ("LAUNCHED", "VERIFIED")
        if step.state == "done":
            assert step.stage == "VERIFIED" and step.run_ok == 1
        # 命令恰好结算一次(WHERE exit_code IS NULL 守卫),值稳定
        cmds = st.commands_of("r-race", 6)
        assert len(cmds) == 1
        first_rc = cmds[0]["exit_code"]
        assert first_rc in (0, -255)
        time.sleep(0.2)
        assert st.commands_of("r-race", 6)[0]["exit_code"] == first_rc
        run = st.get_run("r-race")
        assert run["status"] == "interrupted"  # 终结者收尾;可显式续跑
        assert run["control"] == "running"     # 取消意图已兑现,不残留

        # 幂等:再终结一次,处置集合为空,状态不被二次改写
        again = client.post("/api/admin/terminate",
                            json={"run_id": "r-race", "reason": "再来"})
        assert again.status_code == 200
        assert again.json()["steps"] == []
    finally:
        db.close()


# ---------------- 5. 流式端点 ----------------

def test_ndjson_every_line_parses_and_content_type(client):
    client.post("/api/sessions", json={"id": "demo"})
    with client.stream("POST", "/api/turn",
                       json={"session": "demo", "text": "Ridgecrest 地震同震"}) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/x-ndjson")
        for line in resp.iter_lines():
            if line.strip():
                event = json.loads(line)  # 每行必须独立可解析
                assert "t" in event


@pytest.mark.timing
def test_ndjson_disconnect_leaves_no_running_orphan(live_server):
    """客户端读了一行就真实断开 TCP:run 归服务端所有,应继续推进到终态,
    绝不留 status=running 且无人跟随的孤儿。"""
    with httpx.Client(base_url=live_server, timeout=60 * TIME_FACTOR) as http:
        assert http.post("/api/sessions", json={"id": "demo"}).status_code == 200
        r = http.post("/api/turn", json={"session": "demo", "text": "Ridgecrest 地震同震"})
        assert r.status_code == 200

        with http.stream("POST", "/api/pipeline", json={"session": "demo"}) as resp:
            assert resp.status_code == 200
            for line in resp.iter_lines():
                if line.strip():
                    break  # 读到第一个事件就关闭连接(真实断连)

        # 断开后轮询:run 必须在合理时间内离开 running 态并到达终态
        # (等待上限随负载系数放宽:高负载下服务端收尾整条 sim 链会显著变慢)
        deadline = time.time() + 30 * TIME_FACTOR
        status = None
        while time.time() < deadline:
            state = http.get("/api/state", params={"session": "demo"}).json()
            status = state["run"]["status"]
            if status in ("done", "failed", "interrupted"):
                break
            time.sleep(0.2)
        assert status == "done", f"断开后 run 应收尾到终态,实际 {status}"


def test_stream_internal_error_becomes_event_not_hard_disconnect(client, home):
    """流中途的内部异常必须转成事件(note/bad)收尾,不能裸断连。
    构造:绕过 API 校验直接向队列塞毒动作(模拟历史遗留脏数据)。"""
    client.post("/api/sessions", json={"id": "demo"})
    db = Database(home / "insar.db")
    try:
        Store(db).push_action(scope="step", target="not-an-int", action="SET_METHOD",
                              payload={}, deliver_as="next_run")
    finally:
        db.close()
    events = _stream_events(client, "/api/turn",
                            {"session": "demo", "text": "Ridgecrest 地震同震"})
    assert events, "即使内部出错也要有事件流"
    last = events[-1]
    assert last["t"] == "note" and last["tone"] == "bad"
    assert "内部错误" in last["text"]
    # 服务还活着,后续请求正常
    assert client.get("/api/health").json()["ok"] is True


@pytest.mark.timing
def test_sse_events_format_and_delivery(live_server):
    """SSE:格式为 data: <json>\\n\\n;订阅后能收到回合事件(真实 socket)。"""
    with httpx.Client(base_url=live_server, timeout=60 * TIME_FACTOR) as http:
        assert http.post("/api/sessions", json={"id": "demo"}).status_code == 200
        got: list[str] = []
        status_seen: list = []
        ready = threading.Event()

        def listen():
            try:
                with httpx.stream("GET", live_server + "/api/events",
                                  params={"session": "demo"},
                                  timeout=httpx.Timeout(10 * TIME_FACTOR,
                                                        read=30 * TIME_FACTOR)) as resp:
                    status_seen.append((resp.status_code,
                                        resp.headers.get("content-type", "")))
                    ready.set()
                    for line in resp.iter_lines():
                        if line.startswith("data: "):
                            got.append(line)
                            if len(got) >= 3:
                                break
            except httpx.HTTPError:
                ready.set()  # 失败也放行主线程,由断言收尾

        t = threading.Thread(target=listen, daemon=True)
        t.start()
        assert ready.wait(15 * TIME_FACTOR), "SSE 流应立即建立(响应头先行)"
        # 等订阅真正挂上总线:响应头先行与 bus.subscribe 之间的窗口无法从外部
        # 观测,只能给固定余量 —— 属判定窗,随负载系数放宽
        time.sleep(0.3 * TIME_FACTOR)
        r = http.post("/api/turn", json={"session": "demo", "text": "Ridgecrest 地震同震"})
        assert r.status_code == 200
        t.join(timeout=45 * TIME_FACTOR)
        assert not t.is_alive(), "SSE 监听线程应在收到事件后退出"
        assert status_seen and status_seen[0][0] == 200
        assert status_seen[0][1].startswith("text/event-stream")
        assert len(got) >= 3
        for line in got:
            payload = json.loads(line[len("data: "):])
            assert "t" in payload


# ---------------- 6. CORS / headers 体检 ----------------

def test_no_cors_headers_same_origin_posture(client):
    """create_app 未配置 CORS 中间件:前端与 API 同源部署(静态 UI 挂在 /),
    保持现状 —— 断言不会意外出现放宽的 CORS 头(有人加了会被这里察觉)。"""
    r = client.get("/api/health", headers={"Origin": "http://evil.example"})
    assert r.status_code == 200
    assert "access-control-allow-origin" not in {k.lower() for k in r.headers}


def test_health_and_error_bodies_are_json(client):
    assert client.get("/api/health").headers["content-type"].startswith("application/json")
    # 断言演进注记(集成波次 P2):abort 不带 run_id 且会话无 run 已改为 202
    # (会话级取消受理),不再是 404 —— 改用显式 ghost run_id 保住本用例的
    # 原意图「错误响应体必须是结构化 JSON」(显式 run_id 的 404 口径不变)
    r = client.post("/api/abort", json={"session": "demo", "run_id": "no-such-run"})
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/json")
    _detail_of(r)


# ---------------- 7. driver_of 并发(REVIEW P2-7) ----------------

def test_driver_of_concurrent_first_requests_build_single_driver(home, monkeypatch):
    """同一会话的并发首次请求只构造一个 Driver。

    FastAPI 同步端点跑线程池:check-then-set 无锁时两个首请求各建一个
    Driver(各自 EventBus/探测),/api/events 可能订阅到与实际执行不同的
    总线而收不到事件。构造函数注入 sleep 拉大竞态窗口,无锁必现双建。
    """
    import insar_agent.api.app as app_module

    _seal(monkeypatch)
    created: list[int] = []
    real_driver = app_module.Driver

    class SlowDriver(real_driver):
        def __init__(self, *args, **kwargs):
            created.append(1)
            time.sleep(0.15)  # 拉大构造窗口:竞态若存在,8 线程必然重复构造
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(app_module, "Driver", SlowDriver)
    app = app_module.create_app(home=home)
    with TestClient(app) as c:
        codes: list[int] = []

        def hit():
            codes.append(c.get("/api/registry", params={"session": "racer"}).status_code)

        threads = [threading.Thread(target=hit) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
    assert codes and all(code == 200 for code in codes)
    assert len(created) == 1  # 无锁时为 2-8:各线程各建一个 Driver
