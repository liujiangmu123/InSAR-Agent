"""API 并发压力探针(秒级完成,I/O 压力·非重型计算)。

两个场景:
  A. 线程池 20 并发 × 每 worker 200 请求(合计 4000)混合打
     state / registry / impact / actions,统计 p50/p95/最大延迟与错误率
     (错误 = 状态码 ≥ 500)。
  B. 同一 session 并发 8 个 turn(短文本),超时保护下断言无 5xx、无死锁
     (所有回合在截止时间内完成)。

实现要点:
  - 用真实 uvicorn(后台线程 + 随机端口)打真实 socket —— TestClient 的
    ASGITransport 会缓冲响应且并发语义弱,压力/断连类测试必须真实网络栈。
  - 密封 probe(空引擎)+ 短路 WSL 探测:不碰宿主环境,结果稳定且快。
  - 只做「规划(turn)」与廉价 GET/入队,绝不触发整链 pipeline 执行(避免重算)。

运行:  .venv\\Scripts\\python.exe scripts\\stress_api.py
退出码:不变式全部成立返回 0,否则返回 1(便于 CI 门禁串联)。
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path

# 项目内 src 优先(允许不安装直接跑)
_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import httpx  # noqa: E402
import uvicorn  # noqa: E402

import insar_agent.loop.driver as _driver  # noqa: E402
import insar_agent.runtime.wsl_probe as _wsl  # noqa: E402
from insar_agent.api.app import create_app  # noqa: E402
from insar_agent.runtime.probe import ProbeResult  # noqa: E402

# ---- 压力规模(保持秒级;如需更狠可调,但注意本机重型计算管控)----
WORKERS = 20
PER_WORKER = 200          # 场景 A 合计 = WORKERS * PER_WORKER = 4000
CONCURRENT_TURNS = 8      # 场景 B 同会话并发回合数
TURN_DEADLINE_S = 60.0    # 场景 B 无死锁的截止时间


def _empty_probe(*args, **kwargs) -> ProbeResult:
    return ProbeResult(
        engines={k: None for k in ("isce2", "mintpy", "snaphu", "gdal", "snap",
                                   "pystamps", "pyaps")},
        credentials={"earthdata": False, "cds": False, "gacos": False},
        disk_free_gb=100.0, disk_total_gb=200.0, cpu_count=8)


def _seal() -> None:
    """密封宿主探测:driver 与 setup 都从各自命名空间取 probe_environment,故直接改属性。"""
    _driver.probe_environment = _empty_probe
    _wsl.probe_wsl_engines = lambda **kw: {"ok": False}


def _start_server() -> tuple[uvicorn.Server, threading.Thread, str]:
    home = Path(tempfile.mkdtemp(prefix="insar_stress_"))
    app = create_app(home=home / "home")
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error",
                            timeout_graceful_shutdown=3)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 20
    while not server.started:
        if not thread.is_alive():
            raise RuntimeError("uvicorn 线程提前退出")
        if time.time() > deadline:
            raise RuntimeError("uvicorn 未在 20s 内启动")
        time.sleep(0.05)
    port = server.servers[0].sockets[0].getsockname()[1]
    return server, thread, f"http://127.0.0.1:{port}"


def _percentile(values: list[float], pct: float) -> float:
    """线性插值分位(pct ∈ [0,100]);空列表回 0。"""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (pct / 100.0) * (len(ordered) - 1)
    low = int(rank)
    frac = rank - low
    if low + 1 >= len(ordered):
        return ordered[-1]
    return ordered[low] + (ordered[low + 1] - ordered[low]) * frac


def _plan_run(base: str, session: str) -> None:
    """发起一次 turn(规划),消费完整流 —— 让该会话有一个带步骤的 run。"""
    with httpx.Client(base_url=base, timeout=30) as c:
        assert c.post("/api/sessions", json={"id": session}).status_code == 200
        with c.stream("POST", "/api/turn",
                      json={"session": session, "text": "Ridgecrest 地震同震"}) as r:
            for _ in r.iter_lines():
                pass


def scenario_a(base: str) -> dict:
    """混合读负载:20 并发 × 200 请求。返回统计字典。"""
    endpoints = ("state", "registry", "impact", "actions")

    def one(client: httpx.Client, i: int) -> tuple[str, int, float]:
        kind = endpoints[i % len(endpoints)]
        t0 = time.perf_counter()
        if kind == "state":
            r = client.get("/api/state", params={"session": "bench"})
        elif kind == "registry":
            r = client.get("/api/registry", params={"session": "bench"})
        elif kind == "impact":
            r = client.get("/api/impact", params={
                "session": "bench", "step": 7,
                "params": json.dumps({"max_temporal_baseline": 90})})
        else:  # actions:合法入队(202)
            r = client.post("/api/actions", json={
                "session": "bench", "scope": "step", "target": "9",
                "action": "SET_METHOD", "payload": {"method": "exponential"},
                "deliver_as": "next_run"})
        return kind, r.status_code, (time.perf_counter() - t0) * 1000.0

    results: list[tuple[str, int, float]] = []
    lock = threading.Lock()
    limits = httpx.Limits(max_connections=WORKERS * 2,
                          max_keepalive_connections=WORKERS * 2)

    def worker() -> None:
        local: list[tuple[str, int, float]] = []
        with httpx.Client(base_url=base, timeout=30, limits=limits) as client:
            for i in range(PER_WORKER):
                local.append(one(client, i))
        with lock:
            results.extend(local)

    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futs = [pool.submit(worker) for _ in range(WORKERS)]
        wait(futs)
    wall = time.perf_counter() - t0
    for f in futs:  # 冒泡 worker 内异常
        f.result()

    latencies = [ms for _, _, ms in results]
    codes: dict[int, int] = {}
    for _, code, _ms in results:
        codes[code] = codes.get(code, 0) + 1
    total = len(results)
    errors_5xx = sum(n for c, n in codes.items() if c >= 500)
    non_2xx = sum(n for c, n in codes.items() if not (200 <= c < 300))
    return {
        "total": total, "wall_s": wall, "rps": total / wall if wall else 0.0,
        "p50_ms": _percentile(latencies, 50), "p95_ms": _percentile(latencies, 95),
        "p99_ms": _percentile(latencies, 99), "max_ms": max(latencies, default=0.0),
        "codes": codes, "errors_5xx": errors_5xx, "non_2xx": non_2xx,
        "err_rate": errors_5xx / total if total else 0.0,
    }


def scenario_b(base: str) -> dict:
    """同会话并发回合:8 个 turn 抢同一 session,超时保护下断言无 5xx、无死锁。"""
    with httpx.Client(base_url=base, timeout=30) as c:
        c.post("/api/sessions", json={"id": "conc"})

    def one_turn(idx: int) -> tuple[int, int, bool]:
        with httpx.Client(base_url=base, timeout=TURN_DEADLINE_S) as client:
            with client.stream("POST", "/api/turn",
                               json={"session": "conc",
                                     "text": f"并发回合#{idx} 短文本"}) as r:
                bad = False
                lines = 0
                for line in r.iter_lines():
                    if not line.strip():
                        continue
                    lines += 1
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        bad = True  # 不可解析行(不应发生)
                        continue
                    if ev.get("t") == "note" and ev.get("tone") == "bad":
                        bad = True  # 流内错误被转成事件(可接受,但计入观测)
                return r.status_code, lines, bad

    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=CONCURRENT_TURNS) as pool:
        futs = [pool.submit(one_turn, i) for i in range(CONCURRENT_TURNS)]
        done, not_done = wait(futs, timeout=TURN_DEADLINE_S)
    wall = time.perf_counter() - t0

    deadlocked = len(not_done)
    statuses: list[int] = []
    parse_bad = 0
    stream_errors = 0
    for f in done:
        code, _lines, bad = f.result()
        statuses.append(code)
        if bad:
            parse_bad += 1
    err_5xx = sum(1 for s in statuses if s >= 500)
    return {
        "launched": CONCURRENT_TURNS, "completed": len(done),
        "deadlocked": deadlocked, "wall_s": wall,
        "status_counts": {s: statuses.count(s) for s in sorted(set(statuses))},
        "errors_5xx": err_5xx, "with_bad_note": parse_bad,
        "stream_errors": stream_errors,
    }


def _fmt_codes(codes: dict[int, int]) -> str:
    return ", ".join(f"{c}×{n}" for c, n in sorted(codes.items()))


def main() -> int:
    try:  # Windows 控制台默认 GBK/OEM 码页,强制 UTF-8 输出避免中文乱码
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    _seal()
    server, thread, base = _start_server()
    try:
        _plan_run(base, "bench")
        a = scenario_a(base)
        b = scenario_b(base)
    finally:
        server.should_exit = True
        thread.join(timeout=15)

    ok_a = a["errors_5xx"] == 0
    ok_b = b["errors_5xx"] == 0 and b["deadlocked"] == 0 and b["completed"] == b["launched"]

    print("=" * 68)
    print("InSAR-Agent API 并发压力汇总")
    print("=" * 68)
    print(f"\n[场景 A] 混合读负载  {WORKERS} 并发 × {PER_WORKER} = {a['total']} 请求")
    print(f"  墙钟         : {a['wall_s']:.2f}s  ({a['rps']:.0f} req/s)")
    print(f"  延迟 p50/p95 : {a['p50_ms']:.2f} / {a['p95_ms']:.2f} ms"
          f"   (p99={a['p99_ms']:.2f}, max={a['max_ms']:.2f})")
    print(f"  状态码分布   : {_fmt_codes(a['codes'])}")
    print(f"  5xx 错误率   : {a['err_rate'] * 100:.3f}%  "
          f"(5xx={a['errors_5xx']}, 非2xx={a['non_2xx']})")
    print(f"  判定         : {'PASS' if ok_a else 'FAIL'}(无 5xx)")

    print(f"\n[场景 B] 同会话并发回合  launched={b['launched']}")
    print(f"  墙钟         : {b['wall_s']:.2f}s")
    print(f"  完成/死锁    : {b['completed']}/{b['launched']}  (deadlocked={b['deadlocked']})")
    print(f"  状态码分布   : {_fmt_codes(b['status_counts'])}")
    print(f"  5xx / bad-note : {b['errors_5xx']} / {b['with_bad_note']}")
    print(f"  判定         : {'PASS' if ok_b else 'FAIL'}(无 5xx、无死锁、全部完成)")

    verdict = ok_a and ok_b
    print("\n" + "-" * 68)
    print(f"总判定:{'PASS' if verdict else 'FAIL'}")
    print("-" * 68)
    return 0 if verdict else 1


if __name__ == "__main__":
    raise SystemExit(main())
