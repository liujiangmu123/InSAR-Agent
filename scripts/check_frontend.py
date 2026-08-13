# -*- coding: utf-8 -*-
"""前端质量门一键运行器(零 npm 依赖,纯 stdlib)。

把散落各处的前端质检收拢成一条命令,串行跑齐并输出汇总表(套件×用例×结果×耗时):
  1. tests/js/*.test.mjs            每个文件一个套件,node --test 驱动(动态发现);
  2. prototype/*.check.mjs          无浏览器 DOM stub 自查脚本(动态发现,新增自动纳入);
  3. scripts/check_css_syntax.py    CSS 括号配平 + @media 断点清单一致性;
  4. scripts/check_a11y_contrast.py a11y 对比度审计(信息性:脚本恒退出 0,
                                    硬门禁在 tests/test_a11y_dom.py)。

另做「无测试覆盖」体检(警告,不阻断):prototype/js 下每个模块应至少被一个
测试/校验文件(tests/js/*.mjs、prototype/*.check.mjs、tests/*.py)按文件名引用,
未被引用的模块列成清单,防止新增模块悄悄裸奔。

用法(项目根目录):
  .venv\\Scripts\\python.exe scripts\\check_frontend.py                 # 全量
  .venv\\Scripts\\python.exe scripts\\check_frontend.py --only gallery  # 套件名子串过滤,可多次

退出码:0 = 全部套件通过;1 = 任一套件失败;2 = 环境/用法错误(node 缺失、--only 无匹配等)。
被 tests/test_check_frontend.py 直接 import 做单元测试(main 接受 root 注入假项目树)。
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 纳入门禁的 python 质检脚本:(scripts/ 下文件名, 套件名),按此顺序执行
PY_CHECKS: tuple[tuple[str, str], ...] = (
    ("check_css_syntax.py", "py:css_syntax"),
    ("check_a11y_contrast.py", "py:a11y_contrast"),
)


@dataclass
class Suite:
    name: str  # 汇总表与 --only 用的套件名,如 js:notify / check:fail-demo
    kind: str  # node_test | check_mjs | py_check
    path: Path


@dataclass
class SuiteResult:
    suite: Suite
    code: int  # 子进程退出码(门禁判据)
    secs: float
    cases: int | None  # 从输出解析的用例数;解析不出为 None(表格显示 "-")
    fails: int | None
    output: str


# ---------------- 套件发现与过滤 ----------------

def discover_suites(root: Path) -> list[Suite]:
    """glob 动态发现全部套件:新增 *.test.mjs / *.check.mjs 自动纳入,无需登记。"""
    suites = [
        Suite(f"js:{p.name[: -len('.test.mjs')]}", "node_test", p)
        for p in sorted((root / "tests" / "js").glob("*.test.mjs"))
    ]
    suites += [
        Suite(f"check:{p.name[: -len('.check.mjs')]}", "check_mjs", p)
        for p in sorted((root / "prototype").glob("*.check.mjs"))
    ]
    for fname, name in PY_CHECKS:
        p = root / "scripts" / fname
        if p.exists():
            suites.append(Suite(name, "py_check", p))
    return suites


def filter_suites(suites: list[Suite], only: list[str] | None) -> list[Suite]:
    """--only 过滤:套件名子串匹配,大小写不敏感,多个取并集。"""
    if not only:
        return list(suites)
    pats = [s.lower() for s in only]
    return [s for s in suites if any(p in s.name.lower() for p in pats)]


# ---------------- 单套件执行与输出解析 ----------------

# node --test 的 TAP 汇总行(实测 node 22:"# tests 15" / "# pass 15" / "# fail 0")
_TAP_SUMMARY = re.compile(r"^# (tests|pass|fail|cancelled|skipped|todo) (\d+)\s*$", re.M)
# *.check.mjs 的断言行约定(四个既有脚本同一 check() 助手):"  ok  名称" / "  FAIL 名称"
_CHECK_OK = re.compile(r"^  ok {2}", re.M)
_CHECK_FAIL = re.compile(r"^  FAIL ", re.M)
# check_css_syntax.py 逐文件报告行:"  base.css: 括号配平检查完成 …"
_CSS_FILE = re.compile(r"^  \S+\.css: ", re.M)
# check_a11y_contrast.py 结尾统计:"合计 54 项,未达标 0 项。"(逗号写法不做假设)
_A11Y_TOTAL = re.compile(r"合计\s*(\d+)\s*项.未达标\s*(\d+)\s*项")


def parse_counts(suite: Suite, output: str) -> tuple[int | None, int | None]:
    """尽力从套件输出解析 (用例数, 失败数);解析不出返回 None,门禁判据始终是退出码。"""
    if suite.kind == "node_test":
        stats = {k: int(v) for k, v in _TAP_SUMMARY.findall(output)}
        if "tests" not in stats:
            return None, None
        return stats["tests"], stats.get("fail", 0) + stats.get("cancelled", 0)
    if suite.kind == "check_mjs":
        ok, fail = len(_CHECK_OK.findall(output)), len(_CHECK_FAIL.findall(output))
        return (ok + fail, fail) if ok + fail else (None, None)
    if suite.name == "py:css_syntax":
        files = len(_CSS_FILE.findall(output))
        return (files, None) if files else (None, None)  # 失败数不可归因,交给退出码
    if suite.name == "py:a11y_contrast":
        m = _A11Y_TOTAL.search(output)
        return (int(m.group(1)), int(m.group(2))) if m else (None, None)
    return None, None


def build_cmd(suite: Suite, node_exe: str | None, py_exe: str) -> list[str]:
    if suite.kind == "node_test":
        # TAP 报告器:纯 ASCII 汇总行,跨 Node 版本可解析(spec 报告器的 ℹ 行不稳)
        return [str(node_exe), "--test", "--test-reporter=tap", str(suite.path)]
    if suite.kind == "check_mjs":
        return [str(node_exe), str(suite.path)]
    return [py_exe, str(suite.path)]


def run_suite(suite: Suite, node_exe: str | None, py_exe: str, root: Path) -> SuiteResult:
    """串行跑一个套件,stderr 并入 stdout(check.mjs 的 FAIL 行走 console.error)。"""
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    t0 = time.perf_counter()
    proc = subprocess.run(
        build_cmd(suite, node_exe, py_exe),
        cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
    )
    secs = time.perf_counter() - t0
    output = proc.stdout.decode("utf-8", errors="replace")
    cases, fails = parse_counts(suite, output)
    if fails is None and cases is not None and proc.returncode == 0:
        fails = 0  # 套件绿且用例数可解析时,失败数补 0(如 css 质检的逐文件行)
    return SuiteResult(suite, proc.returncode, secs, cases, fails, output)


# ---------------- 无测试覆盖体检(警告,不阻断) ----------------

def coverage_warnings(root: Path) -> list[str]:
    """prototype/js 下未被任何测试/校验文件按文件名引用的模块清单。

    引用判定:模块文件名(如 gallery.js)出现在 tests/js/*.mjs、prototype/*.check.mjs、
    tests/*.py 任一文件的文本里;要求前一个字符不是 [\\w-],防止 random.js 误配 dom.js。
    文本级启发式,目标是拦住「新增模块忘配测试」,不是精确依赖分析。
    """
    modules = sorted(p.name for p in (root / "prototype" / "js").glob("*.js"))
    if not modules:
        return []
    ref_files = [
        *sorted((root / "tests" / "js").glob("*.mjs")),
        *sorted((root / "prototype").glob("*.check.mjs")),
        *sorted((root / "tests").glob("*.py")),
    ]
    blob = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in ref_files)
    return [m for m in modules if not re.search(r"(?<![\w-])" + re.escape(m), blob)]


# ---------------- 汇总表 ----------------

def _disp_w(s: str) -> int:
    """显示宽度:CJK 全角记 2,保证中英文混排的表格对齐。"""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def _pad(s: str, width: int, right: bool = False) -> str:
    gap = max(0, width - _disp_w(s))
    return " " * gap + s if right else s + " " * gap


def format_table(results: list[SuiteResult]) -> str:
    """套件 × 用例数 × 失败数 × 结果 × 耗时 的等宽汇总表。"""
    rows = [("套件", "用例", "失败", "结果", "耗时")]
    for r in results:
        rows.append((
            r.suite.name,
            "-" if r.cases is None else str(r.cases),
            "-" if r.fails is None else str(r.fails),
            "PASS" if r.code == 0 else f"FAIL({r.code})",
            f"{r.secs:.1f}s",
        ))
    widths = [max(_disp_w(row[i]) for row in rows) for i in range(5)]
    sep = "-" * (sum(widths) + 4 * 2)
    lines: list[str] = []
    for i, row in enumerate(rows):
        lines.append("  ".join((
            _pad(row[0], widths[0]),
            _pad(row[1], widths[1], right=True),
            _pad(row[2], widths[2], right=True),
            _pad(row[3], widths[3]),
            _pad(row[4], widths[4], right=True),
        )).rstrip())
        if i == 0:
            lines.append(sep)
    lines.append(sep)
    total_cases = sum(r.cases or 0 for r in results)
    bad = sum(1 for r in results if r.code != 0)
    lines.append(
        f"合计 {len(results)} 套件 · 用例 {total_cases} · 失败套件 {bad}"
        f" · 总耗时 {sum(r.secs for r in results):.1f}s"
    )
    return "\n".join(lines)


# ---------------- 主流程 ----------------

def main(argv: list[str] | None = None, root: Path | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # 中文 Windows 控制台默认 GBK,统一 UTF-8
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser(
        prog="check_frontend", description="前端质量门一键运行器(详见模块 docstring)")
    ap.add_argument(
        "--only", action="append", metavar="名",
        help="按套件名子串过滤(大小写不敏感,可多次),如 --only gallery --only py:")
    args = ap.parse_args(argv)

    root = root or ROOT
    suites = discover_suites(root)
    if not suites:
        print(f"[check_frontend] 未发现任何前端质检套件,请确认项目根:{root}", file=sys.stderr)
        return 2
    picked = filter_suites(suites, args.only)
    if not picked:
        names = "\n  ".join(s.name for s in suites)
        print(f"[check_frontend] --only 未匹配到套件。可选套件:\n  {names}", file=sys.stderr)
        return 2

    node_exe = shutil.which("node")
    if node_exe is None and any(s.kind in ("node_test", "check_mjs") for s in picked):
        print(
            "[check_frontend] 未找到 node:前端测试需要 Node.js(>=18,内置 node --test)。\n"
            "[check_frontend] 请从 https://nodejs.org/ 安装并确保 node 在 PATH 中后重试。",
            file=sys.stderr,
        )
        return 2

    n_js = sum(1 for s in picked if s.kind == "node_test")
    n_check = sum(1 for s in picked if s.kind == "check_mjs")
    n_py = sum(1 for s in picked if s.kind == "py_check")
    print(
        f"[check_frontend] 发现 {len(suites)} 个套件,本次运行 {len(picked)} 个"
        f"(js 单测 {n_js} · check.mjs {n_check} · py 质检 {n_py}),串行执行……"
    )

    results: list[SuiteResult] = []
    for i, suite in enumerate(picked, 1):
        r = run_suite(suite, node_exe, sys.executable, root)
        results.append(r)
        mark = "PASS" if r.code == 0 else "FAIL"
        cases = "-" if r.cases is None else r.cases
        print(f"[{i:>2}/{len(picked)}] {mark} {suite.name}(用例 {cases} · {r.secs:.1f}s)")
        if r.code != 0:  # 失败套件立即回放完整输出,CI 里不用翻日志拼现场
            print(f"------ {suite.name} 输出(退出码 {r.code})------")
            print(r.output.rstrip())
            print("------")

    print()
    print(format_table(results))

    orphans = coverage_warnings(root)
    n_modules = len(list((root / "prototype" / "js").glob("*.js")))
    if orphans:
        print(f"\n警告:无测试覆盖的 prototype/js 模块({len(orphans)}/{n_modules} 个,不阻断门禁):")
        for name in orphans:
            print(f"  - {name}")
        print("  判定:未被 tests/js/*.mjs、prototype/*.check.mjs、tests/*.py"
              " 按文件名引用,新增模块请补测试。")
    elif n_modules:
        print(f"\n模块覆盖:prototype/js 全部 {n_modules} 个模块均被测试/校验文件引用。")

    bad = [r.suite.name for r in results if r.code != 0]
    if bad:
        print(f"\n门禁失败:{len(bad)} 个套件未通过 → {'、'.join(bad)}")
        return 1
    print("\n门禁通过:全部套件绿。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
