# -*- coding: utf-8 -*-
"""scripts/check_frontend.py(前端质量门一键运行器)自身的测试。

全部用 tmp 假项目树(假 *.test.mjs / *.check.mjs / 假 python 质检脚本)驱动,
不碰真实前端套件:验证 glob 发现机制、--only 过滤、聚合退出码、node 缺失提示、
无覆盖模块警告、输出解析(TAP / check.mjs 断言行 / a11y 统计行)。
需要真跑 node 的用例带 skipif 守护(与 tests/test_a11y_dom.py 同约定)。
"""
from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location(
    "check_frontend", ROOT / "scripts" / "check_frontend.py")
cf = importlib.util.module_from_spec(_spec)
# 先注册再执行(importlib 标准配方):py3.14 的 dataclass 解析字符串注解时
# 会经 sys.modules 找回模块命名空间,不注册会在 @dataclass 处崩
sys.modules["check_frontend"] = cf
_spec.loader.exec_module(cf)

needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node 不可用,跳过需真跑 js 套件的用例")

# ---------------- 假套件素材(全 ASCII 输出,避开控制台码页差异) ----------------

PASS_TEST_MJS = (
    "import { test } from 'node:test';\n"
    "import assert from 'node:assert/strict';\n"
    "test('one', () => { assert.equal(1, 1); });\n"
    "test('two', () => { assert.equal(2, 2); });\n"
)
PASS_CHECK_MJS = (
    "console.log('  ok  first');\n"
    "console.log('  ok  second');\n"
    "process.exit(0);\n"
)
FAIL_CHECK_MJS = (
    "console.log('  ok  first');\n"
    "console.error('  FAIL second');\n"
    "process.exit(1);\n"
)
PASS_PY = "import sys\nprint('py check ok')\nsys.exit(0)\n"
FAIL_PY = "import sys\nprint('py check broken')\nsys.exit(1)\n"


def make_root(tmp_path: Path) -> Path:
    """搭与真仓库同形的最小假项目树(三个套件目录都建好但先不放文件)。"""
    root = tmp_path / "fakeproj"
    for d in ("tests/js", "prototype/js", "scripts"):
        (root / d).mkdir(parents=True)
    return root


def put(root: Path, rel: str, text: str) -> None:
    (root / rel).write_text(text, encoding="utf-8")


# ---------------- 发现机制 ----------------

def test_discover_glob_dynamic(tmp_path):
    """glob 动态发现:js 单测/check.mjs/py 质检各归各类,新增 check.mjs 自动纳入。"""
    root = make_root(tmp_path)
    put(root, "tests/js/alpha.test.mjs", PASS_TEST_MJS)
    put(root, "tests/js/beta.test.mjs", PASS_TEST_MJS)
    put(root, "tests/js/_env.mjs", "// 助手文件,不是 *.test.mjs,不应被发现\n")
    put(root, "prototype/fail-demo.check.mjs", PASS_CHECK_MJS)
    put(root, "scripts/check_css_syntax.py", PASS_PY)
    put(root, "scripts/check_a11y_contrast.py", PASS_PY)

    names = [s.name for s in cf.discover_suites(root)]
    assert names == [
        "js:alpha", "js:beta", "check:fail-demo", "py:css_syntax", "py:a11y_contrast"]

    # 并行新增一个 check 脚本(如 cmdk),无需登记即被纳入
    put(root, "prototype/cmdk.check.mjs", PASS_CHECK_MJS)
    names = [s.name for s in cf.discover_suites(root)]
    assert "check:cmdk" in names
    kinds = {s.name: s.kind for s in cf.discover_suites(root)}
    assert kinds["js:alpha"] == "node_test"
    assert kinds["check:cmdk"] == "check_mjs"
    assert kinds["py:css_syntax"] == "py_check"


def test_empty_root_exits_2(tmp_path, capsys):
    """空目录(零套件)按环境错误退出 2,提示确认项目根。"""
    root = make_root(tmp_path)
    assert cf.main([], root=root) == 2
    assert "未发现任何前端质检套件" in capsys.readouterr().err


# ---------------- --only 过滤 ----------------

def test_only_filter_substring_case_insensitive(tmp_path):
    """--only 子串匹配大小写不敏感,多个值取并集;不匹配则剔除。"""
    root = make_root(tmp_path)
    put(root, "tests/js/gallery_meta.test.mjs", PASS_TEST_MJS)
    put(root, "prototype/fail-demo.check.mjs", PASS_CHECK_MJS)
    put(root, "scripts/check_css_syntax.py", PASS_PY)
    suites = cf.discover_suites(root)

    assert [s.name for s in cf.filter_suites(suites, ["GALLERY"])] == ["js:gallery_meta"]
    assert [s.name for s in cf.filter_suites(suites, ["check:"])] == ["check:fail-demo"]
    both = cf.filter_suites(suites, ["gallery", "css"])
    assert [s.name for s in both] == ["js:gallery_meta", "py:css_syntax"]
    assert cf.filter_suites(suites, None) == suites


def test_only_no_match_exits_2(tmp_path, capsys):
    """--only 全都没匹配上:退出 2 并列出可选套件名,不静默跑空集。"""
    root = make_root(tmp_path)
    put(root, "scripts/check_css_syntax.py", PASS_PY)
    assert cf.main(["--only", "nosuchsuite"], root=root) == 2
    err = capsys.readouterr().err
    assert "未匹配到套件" in err
    assert "py:css_syntax" in err


# ---------------- 退出码聚合(纯 python 假套件,不依赖 node) ----------------

def test_exit_code_aggregation_py_only(tmp_path, capsys):
    """py 假套件全绿退出 0;任一失败退出 1 且汇总表标 FAIL。"""
    root = make_root(tmp_path)
    put(root, "scripts/check_css_syntax.py", PASS_PY)
    put(root, "scripts/check_a11y_contrast.py", PASS_PY)
    assert cf.main([], root=root) == 0
    out = capsys.readouterr().out
    assert "门禁通过" in out

    put(root, "scripts/check_css_syntax.py", FAIL_PY)
    assert cf.main([], root=root) == 1
    out = capsys.readouterr().out
    assert "FAIL(1)" in out
    assert "py check broken" in out  # 失败套件的完整输出被回放
    assert "门禁失败" in out


def test_node_missing_clear_message(tmp_path, capsys, monkeypatch):
    """套件含 js 单测而 node 缺失:退出 2,提示安装 Node.js;纯 py 套件不受影响。"""
    root = make_root(tmp_path)
    put(root, "tests/js/alpha.test.mjs", PASS_TEST_MJS)
    monkeypatch.setattr(cf.shutil, "which", lambda _cmd: None)
    assert cf.main([], root=root) == 2
    err = capsys.readouterr().err
    assert "Node.js" in err
    assert "nodejs.org" in err

    # 只挑 py 套件时不需要 node,照常通过
    put(root, "scripts/check_css_syntax.py", PASS_PY)
    assert cf.main(["--only", "py:"], root=root) == 0


# ---------------- 端到端:真跑 node 假套件 ----------------

@needs_node
def test_end_to_end_table_counts_and_gate(tmp_path, capsys):
    """混合套件端到端:TAP 用例数与 check.mjs 断言数进表;新增失败脚本翻红门禁。"""
    root = make_root(tmp_path)
    put(root, "tests/js/alpha.test.mjs", PASS_TEST_MJS)
    put(root, "prototype/beta.check.mjs", PASS_CHECK_MJS)
    put(root, "scripts/check_css_syntax.py", PASS_PY)
    put(root, "scripts/check_a11y_contrast.py", PASS_PY)

    assert cf.main([], root=root) == 0
    out = capsys.readouterr().out
    assert "js:alpha" in out and "check:beta" in out
    row = next(ln for ln in out.splitlines() if ln.startswith("js:alpha"))
    assert " 2 " in f"{row} "  # TAP "# tests 2" 解析进用例列
    assert "门禁通过" in out

    put(root, "prototype/gamma.check.mjs", FAIL_CHECK_MJS)  # 模拟并行新增且挂掉的套件
    assert cf.main([], root=root) == 1
    out = capsys.readouterr().out
    assert "check:gamma" in out
    assert "FAIL second" in out  # 失败输出回放
    assert "门禁失败" in out


# ---------------- 无测试覆盖警告 ----------------

def test_coverage_warnings_list_unreferenced_modules(tmp_path, capsys):
    """被引用的模块不告警;未引用的列入清单;random.js 不给 dom.js 记假引用。"""
    root = make_root(tmp_path)
    put(root, "prototype/js/covered.js", "export const x = 1;\n")
    put(root, "prototype/js/orphan.js", "export const y = 2;\n")
    put(root, "prototype/js/dom.js", "export const z = 3;\n")
    # 校验文件引用了 covered.js;random.js 里的 "dom.js" 子串不构成对 dom.js 的引用
    put(root, "prototype/x.check.mjs",
        "import './js/covered.js';\nconsole.log('  ok  covered');\nprocess.exit(0);\n")
    put(root, "tests/js/misc.test.mjs", "// 提到 random.js 而非 dom 模块本身\n")

    assert cf.coverage_warnings(root) == ["dom.js", "orphan.js"]

    # 主流程把清单打印成警告且不影响退出码(唯一套件 x.check.mjs 是绿的)
    if shutil.which("node"):
        assert cf.main([], root=root) == 0
        out = capsys.readouterr().out
        assert "无测试覆盖" in out
        assert "orphan.js" in out and "dom.js" in out
        assert "covered.js" not in out.split("无测试覆盖")[1]


def test_coverage_warnings_all_referenced(tmp_path):
    """全部模块被引用时清单为空(tests/*.py 里的引用同样算数)。"""
    root = make_root(tmp_path)
    put(root, "prototype/js/state.js", "export const s = 1;\n")
    put(root, "tests/test_contract.py", "APP = ROOT / 'prototype' / 'js' / 'state.js'\n")
    assert cf.coverage_warnings(root) == []


# ---------------- 输出解析(纯函数,不起子进程) ----------------

def test_parse_counts_units():
    """三类输出的用例数/失败数解析:TAP 汇总行、check.mjs 断言行、a11y 统计行。"""
    tap = cf.Suite("js:x", "node_test", Path("x"))
    out = "TAP version 13\nok 1 - one\n1..15\n# tests 15\n# pass 14\n# fail 1\n# cancelled 0\n"
    assert cf.parse_counts(tap, out) == (15, 1)
    assert cf.parse_counts(tap, "node crashed before summary") == (None, None)

    chk = cf.Suite("check:x", "check_mjs", Path("x"))
    out = "  ok  a\n  ok  b\n  FAIL c\n随后是 DOM 序列化转储,不应计入\n"
    assert cf.parse_counts(chk, out) == (3, 1)

    a11y = cf.Suite("py:a11y_contrast", "py_check", Path("x"))
    assert cf.parse_counts(a11y, "……\n合计 54 项,未达标 2 项。\n") == (54, 2)
