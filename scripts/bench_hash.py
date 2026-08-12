"""指纹/哈希热路径基准:perf_counter 计时 + cProfile 热点 top10。

用法(项目根目录):
  .venv\\Scripts\\python.exe scripts\\bench_hash.py            # 计时 + 剖析
  .venv\\Scripts\\python.exe scripts\\bench_hash.py --no-profile

三个主场景(总耗时秒级,遵守本机重型计算管控;工作负载全部定长且种子固定,
优化前后跑同一脚本即可直接对比):
  a) 万级节点嵌套参数结构 hash_struct
  b) 2000 个小文件目录 fingerprint_dir(项目内 .bench_tmp 现造,跑完即删)
  c) 500 步全管线 step_hashes Merkle 级联

附加微基准(只为决策「值不值得优化」,量级很小):
  d) discover_artifacts:220 文件工作区 × 含 glob 候选
  e) trim_history:400 条消息裁剪
"""

from __future__ import annotations

import argparse
import cProfile
import io
import platform
import pstats
import random
import shutil
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))  # 允许不安装直接跑

from insar_agent.core.filehash import fingerprint_dir  # noqa: E402
from insar_agent.core.fingerprint import step_hashes  # noqa: E402
from insar_agent.core.normalize import hash_struct  # noqa: E402
from insar_agent.loop.budget import trim_history  # noqa: E402
from insar_agent.registry.model import ArtifactSpec  # noqa: E402
from insar_agent.runtime.discover import discover_artifacts  # noqa: E402

_BENCH_TMP = _ROOT / ".bench_tmp"


# ---------------------------------------------------------------------------
# 工作负载构造(全部定长、种子固定)
# ---------------------------------------------------------------------------


def count_nodes(v: object) -> int:
    """结构规模计数:标量 1;容器 1 + 子节点;dict 的 key 也算节点。"""
    if isinstance(v, dict):
        return 1 + sum(count_nodes(k) + count_nodes(x) for k, x in v.items())
    if isinstance(v, (list, tuple, set, frozenset)):
        return 1 + sum(count_nodes(x) for x in v)
    return 1


def build_nested_params(n_steps: int = 240, seed: int = 20260812) -> dict:
    """仿真实管线的参数树:中文键、浮点、None/bool、大整数、嵌套 list。"""
    rnd = random.Random(seed)
    steps = []
    for i in range(n_steps):
        steps.append({
            "步骤": f"step_{i:04d}",
            "capability": rnd.choice(["insar.coreg", "insar.ifg", "insar.unwrap", "insar.geocode"]),
            "params": {
                "looks": {"range": rnd.randint(1, 16), "azimuth": rnd.randint(1, 8)},
                "coherence_threshold": rnd.random(),
                "filter_alpha": rnd.random() * rnd.choice([1.0, 1e-8, 1e13]),
                "掩膜": rnd.choice([None, True, False]),
                "多边形": [[rnd.random() * 360 - 180, rnd.random() * 180 - 90] for _ in range(6)],
                "日期对": [f"202401{d:02d}" for d in range(1, rnd.randint(3, 9))],
                "flags": {f"f{k}": bool(rnd.getrandbits(1)) for k in range(8)},
                "大整数": 2 ** 64 + rnd.getrandbits(70),
            },
        })
    return {"pipeline": steps,
            "全局": {"dem": "srtm_30m", "工区": "西藏_测试区", "版本": 3}}


def build_file_tree(root: Path, n_files: int = 2000, seed: int = 7) -> int:
    """25 个顶层目录 × 2 子目录 × 40 文件 = 2000 个小文件(含中文名/混合大小写)。"""
    rnd = random.Random(seed)
    made = 0
    top_names = [f"track_{i:02d}" if i % 3 else f"轨道_{i:02d}" for i in range(25)]
    for t, top in enumerate(top_names):
        for s, sub in enumerate(("IFG", "coreg")):
            d = root / top / sub
            d.mkdir(parents=True, exist_ok=True)
            for k in range(40):
                name = (f"IMG-HH-ALOS2_{k:03d}.par" if (t + k) % 4
                        else f"scene_{k:03d}_干涉.int")
                (d / name).write_bytes(rnd.randbytes(rnd.randint(8, 64)))
                made += 1
                if made >= n_files:
                    return made
    return made


def build_step_kwargs(i: int, upstream: list[str]) -> dict:
    return dict(
        capability=f"insar.cap_{i % 11}",
        version="2.1.0",
        tool_versions={"snaphu": "2.0.7", "gdal": "3.8.3", "isce2": "2.6.3"},
        method="snaphu_mcf" if i % 2 else "icu",
        params={
            "tiles": i % 16, "coherence_threshold": 0.05 + (i % 90) / 100,
            "cpu": 16, "mem_gb": 12, "colormap": "viridis",
            "初相位": -0.0, "缩放": 1e-13, "窗口": [i, i + 1, i + 2],
            "flags": {"deramp": True, "ml": i % 3 == 0},
        },
        param_kinds={"cpu": "resource", "mem_gb": "resource", "colormap": "presentation"},
        upstream_eval_hashes=upstream,
    )


def build_workspace(root: Path) -> tuple[Path, tuple[ArtifactSpec, ...]]:
    """discover 微基准:220 个文件的工作区 + 真实形状的候选声明(含 glob)。"""
    ws = root / "ws"
    (ws / "mintpy").mkdir(parents=True, exist_ok=True)
    (ws / "products" / "figures").mkdir(parents=True, exist_ok=True)
    (ws / "data" / "unw").mkdir(parents=True, exist_ok=True)
    for i in range(200):
        (ws / "data" / "unw" / f"filt_{i:03d}.unw.geo").write_bytes(b"x")
    (ws / "mintpy" / "velocity.h5").write_bytes(b"v")
    (ws / "mintpy" / "timeseries_ERA5_demErr.h5").write_bytes(b"t")
    for i in range(18):
        (ws / "products" / "figures" / f"vel_{i:02d}.png").write_bytes(b"p")
    specs = (
        ArtifactSpec("velocity", ("mintpy/velocity.h5", "products/velocity.h5", "velocity.h5"),
                     policy="content"),
        ArtifactSpec("timeseries_corrected",
                     ("mintpy/timeseries_ERA5_ramp_demErr.h5", "mintpy/timeseries_ERA5_demErr.h5",
                      "mintpy/timeseries_ramp_demErr.h5", "mintpy/timeseries_demErr.h5",
                      "products/timeseries_corrected.h5", "timeseries_ERA5_ramp_demErr.h5",
                      "timeseries_ramp_demErr.h5"), policy="content"),
        ArtifactSpec("unw_any", ("data/unw/*.unw.geo", "data/unw/geo/*.unw.geo"), policy="stat"),
        ArtifactSpec("figures", ("products/figures", "products/velocities"), policy="stat"),
        ArtifactSpec("qa_report", ("products/report/qa.json", "qa.json"), required=False),
        ArtifactSpec("missing_glob", ("logs/**/*.err", "*.err"), required=False),
    )
    return ws, specs


def build_history(n: int = 400) -> list[dict]:
    rnd = random.Random(3)
    roles = ("user", "assistant", "tool")
    return [{"role": roles[i % 3], "content": "消息" * rnd.randint(5, 120) + f"#{i}"}
            for i in range(n)]


# ---------------------------------------------------------------------------
# 计时与剖析
# ---------------------------------------------------------------------------


def timeit(fn, reps: int, warmup: int = 1) -> tuple[float, float]:
    """返回 (单次均值 ms, 单次最小 ms)。"""
    for _ in range(warmup):
        fn()
    samples = []
    for _ in range(reps):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000)
    return sum(samples) / len(samples), min(samples)


def profile_top10(tag: str, fn, reps: int) -> None:
    pr = cProfile.Profile()
    pr.enable()
    for _ in range(reps):
        fn()
    pr.disable()
    buf = io.StringIO()
    stats = pstats.Stats(pr, stream=buf)
    stats.strip_dirs().sort_stats("tottime").print_stats(10)
    lines = buf.getvalue().splitlines()
    # 只留表头与 top10 行,砍掉空行噪声
    keep = [ln for ln in lines if ln.strip()]
    print(f"\n--- cProfile top10 (tottime) · {tag} · reps={reps} ---")
    for ln in keep[1:16]:
        print(ln)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-profile", action="store_true", help="只计时不剖析(数值更稳)")
    args = ap.parse_args()

    print(f"Python {sys.version.split()[0]} · {platform.system()} · "
          f"perf_counter 分辨率 {time.get_clock_info('perf_counter').resolution:g}s")

    summary: list[tuple[str, float, float]] = []

    # ---- (a) 万级嵌套结构 hash_struct ----
    struct = build_nested_params()
    n_nodes = count_nodes(struct)
    fn_a = lambda: hash_struct(struct)  # noqa: E731
    mean_a, best_a = timeit(fn_a, reps=20)
    print(f"\n[a] hash_struct 嵌套参数结构:{n_nodes} 节点")
    print(f"    单次 mean={mean_a:.2f} ms  best={best_a:.2f} ms")
    summary.append((f"a: hash_struct {n_nodes} 节点", mean_a, best_a))

    # ---- (b) 2000 小文件 fingerprint_dir ----
    if _BENCH_TMP.exists():
        shutil.rmtree(_BENCH_TMP)
    tree = _BENCH_TMP / "tree"
    tree.mkdir(parents=True)
    try:
        n_made = build_file_tree(tree)
        fn_b = lambda: fingerprint_dir(tree, "stat")  # noqa: E731
        mean_b, best_b = timeit(fn_b, reps=5)
        print(f"\n[b] fingerprint_dir stat 档:{n_made} 个小文件")
        print(f"    单次 mean={mean_b:.2f} ms  best={best_b:.2f} ms")
        summary.append((f"b: fingerprint_dir stat {n_made} 文件", mean_b, best_b))

        fn_b2 = lambda: fingerprint_dir(tree, "content")  # noqa: E731
        mean_b2, best_b2 = timeit(fn_b2, reps=2, warmup=0)
        print(f"    content 档 mean={mean_b2:.2f} ms(对照,读全文)")
        summary.append((f"b2: fingerprint_dir content {n_made} 文件", mean_b2, best_b2))

        # ---- (c) 500 步 step_hashes 级联 ----
        def cascade() -> str:
            upstream: list[str] = []
            for i in range(500):
                out = build_step_kwargs(i, upstream)
                h = step_hashes(**out)
                upstream = [h["eval_hash"]]
            return upstream[0]

        mean_c, best_c = timeit(cascade, reps=10)
        print("\n[c] step_hashes 级联 500 步(Merkle 链)")
        print(f"    单次 mean={mean_c:.2f} ms  best={best_c:.2f} ms")
        summary.append(("c: step_hashes 级联 500 步", mean_c, best_c))

        # ---- (d/e) 附加微基准 ----
        ws, specs = build_workspace(_BENCH_TMP)

        def run_discover() -> None:
            for _ in range(50):
                discover_artifacts(ws, specs)

        mean_d, best_d = timeit(run_discover, reps=5)
        print("\n[d] discover_artifacts × 50(6 spec,含 glob 候选)")
        print(f"    50 次 mean={mean_d:.2f} ms  best={best_d:.2f} ms")
        summary.append(("d: discover ×50", mean_d, best_d))

        history = build_history()

        def run_trim() -> None:
            for _ in range(200):
                trim_history(history, max_chars=8000)

        mean_e, best_e = timeit(run_trim, reps=5)
        print("\n[e] trim_history × 200(400 条消息)")
        print(f"    200 次 mean={mean_e:.2f} ms  best={best_e:.2f} ms")
        summary.append(("e: trim_history ×200", mean_e, best_e))

        # ---- cProfile 热点 ----
        if not args.no_profile:
            profile_top10("a hash_struct", fn_a, reps=5)
            profile_top10("b fingerprint_dir stat", fn_b, reps=2)
            profile_top10("c step_hashes 级联", cascade, reps=3)
    finally:
        shutil.rmtree(_BENCH_TMP, ignore_errors=True)

    print("\n================ 汇总(单次耗时) ================")
    for tag, mean, best in summary:
        print(f"{tag:<38} mean={mean:9.2f} ms   best={best:9.2f} ms")


if __name__ == "__main__":
    main()
