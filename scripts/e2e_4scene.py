"""E2E 真实数据走查:4 景(5 对 HyP3)全流程驱动器。

像 pi 工具层一样走 HTTP API(同端点、同顺序),覆盖用户实际使用的完整旅程:
  env      环境体检(health/env/doctor/datasets/recommend/registry)
  core     核心 run:建会话 → 规划(11 步) → 执行 → 步骤态
  outputs  核心输出全展示:state/trace/artifacts/figures/logs/provenance
  analysis 分析 run(20-28):规划 → 执行 → 输出展示
  apply    应用层:点位时序 / 导出 / 报告 / run.sh / 复现包
  all      以上全部

用法(先按真实模式起后端,HOME 独立、INSAR_HYP3_SOURCE 指向 4 景子集):
  .venv\\Scripts\\python.exe scripts/e2e_4scene.py --stage all
  .venv\\Scripts\\python.exe scripts/e2e_4scene.py --stage core --base http://127.0.0.1:8874
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import httpx

SEP = "=" * 78


def hr(title: str) -> None:
    print(f"\n{SEP}\n== {title}\n{SEP}", flush=True)


def show(obj: object, limit: int = 8000) -> None:
    text = json.dumps(obj, ensure_ascii=False, indent=1)
    if len(text) > limit:
        text = text[:limit] + f"\n... (截断,共 {len(text)} 字符)"
    print(text, flush=True)


def fmt_event(e: dict) -> str:
    """NDJSON 回合事件 → 人读行(对齐 scripts/real_ridgecrest.py 的口径)。"""
    t = e.get("t")
    if t == "tool.log":
        return f"    | {e.get('line', '')}"
    if t == "tool.start":
        return f"  ▶ {e.get('verb', '')} {e.get('label', '')} $ {e.get('cmd', '')}"
    if t == "tool.end":
        return f"  ■ exit={e.get('exit')} {e.get('summary', '')}"
    if t in ("step.start", "step.end"):
        return f"[{t} {e.get('stepId')}]" + (f" exit={e['exit']}" if "exit" in e else "")
    if t == "note":
        return f"  ({e.get('tone')}) {e.get('text')}"
    if t == "say":
        return "AGENT: " + "".join(p if isinstance(p, str) else str(p)
                                   for p in e.get("parts", []))
    if t == "plan":
        return "PLAN: " + " / ".join(i.get("text", "") for i in e.get("items", []))
    if t == "thinking":
        return f"THINKING: {e.get('title')}"
    return f"[{t}] " + json.dumps({k: v for k, v in e.items() if k != "t"},
                                  ensure_ascii=False)[:300]


class E2E:
    def __init__(self, base: str, session: str) -> None:
        self.base = base.rstrip("/")
        self.session = session
        self.client = httpx.Client(base_url=self.base, timeout=httpx.Timeout(60.0))

    # ---------- HTTP 薄层 ----------

    def get(self, path: str, **params) -> object:
        r = self.client.get(path, params={k: v for k, v in params.items() if v is not None})
        r.raise_for_status()
        ctype = r.headers.get("content-type", "")
        return r.json() if "json" in ctype else r.text

    def post(self, path: str, body: dict) -> object:
        r = self.client.post(path, json=body)
        r.raise_for_status()
        return r.json()

    def stream(self, path: str, body: dict, *, timeout: float = 3600.0) -> list[dict]:
        """POST NDJSON 流:实时逐行打印,返回全部事件。"""
        events: list[dict] = []
        with self.client.stream("POST", path, json=body,
                                timeout=httpx.Timeout(timeout, connect=10.0)) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line.strip():
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    print(f"  [坏行] {line[:200]}", flush=True)
                    continue
                events.append(e)
                print(fmt_event(e), flush=True)
        return events

    def download(self, path: str, dest: Path, **params) -> dict:
        with self.client.stream("GET", path,
                                params={k: v for k, v in params.items() if v is not None},
                                timeout=httpx.Timeout(300.0)) as r:
            if r.status_code != 200:
                r.read()
                return {"status": r.status_code, "detail": r.text[:400]}
            dest.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            size = 0
            with dest.open("wb") as f:
                for chunk in r.iter_bytes():
                    f.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
        return {"status": 200, "file": str(dest), "bytes": size,
                "sha256": digest.hexdigest()[:16]}

    # ---------- 阶段 ----------

    def stage_env(self, data_root: str) -> None:
        hr("health")
        show(self.get("/api/health"))
        hr("数据登记:POST /api/datasets/roots")
        show(self.post("/api/datasets/roots", {"path": data_root}))
        hr("数据集清单:GET /api/datasets")
        ds = self.get("/api/datasets", rescan=1)
        show(ds)
        hr("数据集详情 + 路线推荐")
        for d in ds.get("datasets", []):
            show(self.get(f"/api/datasets/{d['id']}"), limit=3000)
            show(self.get("/api/recommend", dataset_id=d["id"]), limit=4000)
        hr("环境探测:GET /api/env(引擎/凭据/磁盘/阈值台账)")
        env = self.get("/api/env", session=self.session)
        show(env, limit=6000)
        hr("一键体检:GET /api/doctor")
        show(self.get("/api/doctor"), limit=6000)

    def stage_core(self, plan_text: str) -> None:
        hr("建会话:POST /api/sessions")
        show(self.post("/api/sessions", {"id": self.session, "name": "E2E 4景走查"}))
        hr(f"规划回合:POST /api/turn(core)· “{plan_text}”")
        self.stream("/api/turn", {"session": self.session, "text": plan_text,
                                  "pipeline": "core"})
        hr("执行回合:POST /api/pipeline(五阶段执行器,NDJSON 实时流)")
        t0 = time.time()
        self.stream("/api/pipeline", {"session": self.session})
        print(f"\n[执行墙钟] {time.time() - t0:.0f}s", flush=True)
        self._steps_table()

    def _steps_table(self) -> None:
        hr("步骤终态:GET /api/state")
        st = self.get("/api/state", session=self.session)
        run = st.get("run") or {}
        print(f"run={run.get('run_id')} status={run.get('status')}"
              f" scenario={run.get('scenario')} simulated={run.get('simulated')}",
              flush=True)
        for s in st.get("steps", []):
            print(f"  step {s['id']:>2} {s['name']:<6} {s['state']:<9}"
                  f" stage={s['stage']:<9} method={s['method']:<22}"
                  f" run_ok={s['runOk']}"
                  + (f" failure={s['failureClass']}" if s.get("failureClass") else ""),
                  flush=True)

    def stage_outputs(self) -> None:
        hr("监控面板数据:GET /api/monitor(侧栏同源 payload)")
        show(self.get("/api/monitor", session=self.session), limit=5000)
        hr("产物清单:GET /api/artifacts(交付核对:path/size/hash)")
        arts = self.get("/api/artifacts", session=self.session)
        show(arts, limit=9000)
        hr("图件与文件:GET /api/figures")
        figs = self.get("/api/figures", session=self.session)
        for f in figs.get("figures", []):
            print(f"  [图] step{f['step']:>2} {f['name']:<44} {f['size']:>9} B", flush=True)
        for f in figs.get("files", []):
            print(f"  [件] step{f['step']:>2} {f['name']:<44} {f['size']:>9} B"
                  f" kind={f['kind']}", flush=True)
        hr("逐步日志尾部:GET /api/logs(每步末 12 行)")
        st = self.get("/api/state", session=self.session)
        for s in st.get("steps", []):
            if s["state"] in ("done", "failed"):
                try:
                    text = self.get("/api/logs", session=self.session, step=s["id"])
                except httpx.HTTPStatusError as exc:
                    print(f"-- step {s['id']} {s['name']}: 无日志({exc.response.status_code})",
                          flush=True)
                    continue
                lines = [ln for ln in str(text).splitlines() if ln.strip()]
                print(f"-- step {s['id']} {s['name']} 日志末 {min(12, len(lines))} 行:",
                      flush=True)
                for ln in lines[-12:]:
                    print(f"    {ln}", flush=True)
        hr("provenance 台账:GET /api/provenance(证据阶梯 + 指标重解析)")
        prov = self.get("/api/provenance", session=self.session)
        print(f"evidence={prov.get('evidence_level')}"
              f" ceiling={prov.get('evidence', {}).get('ceiling')}"
              f" ({prov.get('evidence', {}).get('ceiling_reason')})", flush=True)
        for name, m in (prov.get("metrics") or {}).items():
            print(f"  {name} = {m.get('value')} reparsed_ok={m.get('reparsed_ok')}"
                  f" ← {m.get('source_artifact')}#{m.get('source_field')}", flush=True)
        for w in prov.get("warnings", []):
            print(f"  [warn] {w}", flush=True)
        hr("执行时间线:GET /api/trace(阶段与耗时)")
        trace = self.get("/api/trace", session=self.session)
        if isinstance(trace, list):
            print(f"事件总数 {len(trace)},末 20 条:", flush=True)
            for e in trace[-20:]:
                print("  " + json.dumps(e, ensure_ascii=False)[:220], flush=True)

    def stage_analysis(self, text: str, params: dict) -> None:
        hr(f"分析规划:POST /api/turn(analysis)· “{text}”")
        self.stream("/api/turn", {"session": self.session, "text": text,
                                  "pipeline": "analysis", "params": params})
        hr("分析执行:POST /api/pipeline")
        t0 = time.time()
        self.stream("/api/pipeline", {"session": self.session})
        print(f"\n[执行墙钟] {time.time() - t0:.0f}s", flush=True)
        self._steps_table()

    def stage_apply(self, out_dir: Path, run_id: str | None = None) -> None:
        hr("点位时序:GET /api/timeseries-point(震中附近 35.77N,-117.60E)")
        try:
            ts = self.get("/api/timeseries-point", session=self.session,
                          run_id=run_id, lat=35.77, lon=-117.60)
            print(f"source={ts.get('source')} point={ts.get('point')}"
                  f" ref={ts.get('ref_point')} shape={ts.get('shape')}", flush=True)
            pairs = list(zip(ts.get("dates", []), ts.get("values_mm", [])))
            for d, v in pairs:
                print(f"  {d}  {v:+8.1f} mm", flush=True)
        except httpx.HTTPStatusError as exc:
            print(f"timeseries-point → {exc.response.status_code}:"
                  f" {exc.response.text[:300]}", flush=True)
        hr("导出能力矩阵:GET /api/export/options")
        opts = self.get("/api/export/options", session=self.session, run_id=run_id)
        show(opts, limit=4000)
        hr("导出数据产品:GET /api/export(逐个可用项)")
        for item in opts.get("products", []):
            pid = item.get("product")
            for fid, meta in (item.get("formats") or {}).items():
                if not meta.get("available"):
                    print(f"  [跳过] {pid}.{fid}: {meta.get('reason', '不可用')}", flush=True)
                    continue
                dest = out_dir / f"export_{pid}.{fid}"
                show({f"{pid}.{fid}": self.download("/api/export", dest,
                                                    session=self.session, run_id=run_id,
                                                    product=pid, fmt=fid)}, limit=1200)
        hr("方法章节:GET /api/methods.md")
        print(str(self.get("/api/methods.md", session=self.session, run_id=run_id))[:3000],
              flush=True)
        hr("结果报告:POST /api/report/results 与 /api/report/full")
        for ep in ("/api/report/results", "/api/report/full"):
            try:
                rep = self.post(ep, {"session": self.session,
                                     **({"run_id": run_id} if run_id else {})})
                text = rep.get("markdown") or rep.get("text") or json.dumps(
                    rep, ensure_ascii=False)
                print(f"---- {ep} → {str(text)[:2200]}", flush=True)
            except httpx.HTTPStatusError as exc:
                print(f"{ep} → {exc.response.status_code}: {exc.response.text[:300]}",
                      flush=True)
        hr("等价命令脚本:GET /api/run.sh")
        print(str(self.get("/api/run.sh", session=self.session, run_id=run_id))[:2600],
              flush=True)
        hr("复现包:GET /api/repro-bundle")
        show(self.download("/api/repro-bundle", out_dir / "repro-bundle.zip",
                           session=self.session, run_id=run_id))

    def core_run_id(self) -> str | None:
        """最早的 core run id(分析 run 建立后 latest 口径会变,应用层要锁核心 run)。"""
        runs = self.get("/api/runs", session=self.session)
        items = runs.get("runs", [])
        for r in reversed(items):  # created_at 倒序 → 反转后最老在前
            return r["run_id"]
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default="http://127.0.0.1:8874")
    ap.add_argument("--session", default="e2e-4scene")
    ap.add_argument("--stage", default="all",
                    choices=["env", "core", "outputs", "analysis", "apply", "all"])
    ap.add_argument("--data-root", default=str(Path(__file__).resolve().parents[1]
                                               / "workspace" / "data-4scene"))
    ap.add_argument("--out-dir", default=str(Path(__file__).resolve().parents[1]
                                             / "workspace" / "e2e-4scene" / "deliver"))
    args = ap.parse_args()

    e2e = E2E(args.base, args.session)
    stages = ([args.stage] if args.stage != "all"
              else ["env", "core", "outputs", "analysis", "apply"])
    core_run: str | None = None
    for st in stages:
        if st == "env":
            e2e.stage_env(args.data_root)
        elif st == "core":
            e2e.stage_core("Ridgecrest 地震同震形变分析(4 景 5 对子集,真实执行)")
            core_run = e2e.core_run_id()
        elif st == "outputs":
            e2e.stage_outputs()
        elif st == "analysis":
            core_run = core_run or e2e.core_run_id()
            e2e.stage_analysis(
                "对速度场做相干掩膜、统计剖面、分析出图、变化检测与外推预测",
                {"primary": "mintpy/velocity.h5",
                 **({"primary_run": core_run} if core_run else {})})
        elif st == "apply":
            core_run = core_run or e2e.core_run_id()
            e2e.stage_apply(Path(args.out_dir), run_id=core_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
