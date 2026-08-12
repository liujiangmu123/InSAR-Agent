"""a11y 静态断言(不开浏览器):
- 用 html.parser 构建轻量 DOM 树,断言 index.html 的可达性结构
  (live region / dialog 语义 / 按钮可及名 / aria-keyshortcuts 声明);
- 复用 scripts/check_a11y_contrast.py 断言设计令牌全部达到 WCAG AA;
- 有 node 时驱动 a11y.js 的纯解析函数做单元级验证(无 DOM 依赖)。
"""
from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROTO = ROOT / "prototype"
INDEX = PROTO / "index.html"


# ---------------- 轻量 DOM 树 ----------------

class Node:
    def __init__(self, tag: str, attrs):
        self.tag = tag
        self.attrs = dict(attrs)
        self.children: list[Node] = []
        self.text = ""

    def inner_text(self) -> str:
        return self.text + "".join(c.inner_text() for c in self.children)


class TreeBuilder(HTMLParser):
    VOID = {"meta", "link", "input", "br", "img", "hr", "source"}

    def __init__(self):
        super().__init__()
        self.root = Node("#root", [])
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.stack[-1].children.append(Node(tag, attrs))

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        self.stack[-1].text += data


def walk(node: Node):
    yield node
    for c in node.children:
        yield from walk(c)


@pytest.fixture(scope="module")
def dom() -> Node:
    tb = TreeBuilder()
    tb.feed(INDEX.read_text(encoding="utf-8"))
    return tb.root


def by_id(dom: Node, el_id: str) -> Node:
    for n in walk(dom):
        if n.attrs.get("id") == el_id:
            return n
    raise AssertionError(f"index.html 缺少 id={el_id} 的元素")


# ---------------- DOM 结构断言 ----------------

def test_html_lang_declared(dom):
    html = next(n for n in walk(dom) if n.tag == "html")
    assert html.attrs.get("lang"), "html 元素必须声明 lang"


def test_stream_is_live_log(dom):
    stream = by_id(dom, "stream")
    assert stream.attrs.get("role") == "log"
    assert stream.attrs.get("aria-live") == "polite"
    assert stream.attrs.get("aria-label"), "事件流容器需要 aria-label"


def test_status_and_budget_are_live_status(dom):
    for el_id in ("status", "budget"):
        el = by_id(dom, el_id)
        assert el.attrs.get("role") == "status", f"#{el_id} 缺 role=status"
        assert el.attrs.get("aria-live") == "polite", f"#{el_id} 缺 aria-live"


def test_attach_row_is_live(dom):
    row = by_id(dom, "attachRow")
    assert row.attrs.get("aria-live") == "polite"


def test_lightbox_dialog_semantics(dom):
    lb = by_id(dom, "lightbox")
    assert lb.attrs.get("role") == "dialog"
    assert lb.attrs.get("aria-modal") == "true"
    assert lb.attrs.get("aria-label")


def test_scrim_and_grip_labelled(dom):
    assert by_id(dom, "scrim").attrs.get("aria-label")
    grip = by_id(dom, "grip")
    assert grip.attrs.get("role") == "separator"
    assert grip.attrs.get("tabindex") == "0", "拖拽把手必须键盘可达"


def test_every_button_has_accessible_name(dom):
    bad = []
    for n in walk(dom):
        if n.tag != "button":
            continue
        name = (n.attrs.get("aria-label") or n.attrs.get("title")
                or n.inner_text().strip())
        if not name:
            bad.append(n.attrs)
    assert not bad, f"以下按钮没有可及名(aria-label/title/文本):{bad}"


def test_static_svgs_are_decorative(dom):
    bad = [n.attrs for n in walk(dom)
           if n.tag == "svg" and n.attrs.get("aria-hidden") != "true"]
    assert not bad, f"静态 svg 应标 aria-hidden=true:{bad}"


def test_keyboard_shortcuts_declared(dom):
    declared = {n.attrs.get("aria-keyshortcuts") for n in walk(dom)
                if "aria-keyshortcuts" in n.attrs}
    expect = {"Control+B", "Control+J", "Control+K", "Control+Enter",
              "Enter", "Escape"}
    missing = expect - declared
    assert not missing, f"aria-keyshortcuts 声明缺失:{missing}"


def test_skip_link_targets_prompt(dom):
    links = [n for n in walk(dom) if n.tag == "a"]
    assert links and links[0].attrs.get("href") == "#prompt", "首个链接应为跳到输入框的 skip link"
    by_id(dom, "prompt")   # 目标必须存在


def test_a11y_script_included(dom):
    srcs = {n.attrs.get("src") for n in walk(dom) if n.tag == "script"}
    assert "js/a11y.js" in srcs, "index.html 必须引入 js/a11y.js"


# ---------------- CSS 层断言 ----------------

def test_focus_visible_ring_defined():
    css = (PROTO / "css" / "base.css").read_text(encoding="utf-8")
    m = re.search(r":focus-visible\s*\{([^}]*)\}", css)
    assert m and "--border-focus" in m.group(1), "base.css 需定义统一 :focus-visible 焦点环"
    assert ":focus:not(:focus-visible)" in css, "鼠标焦点不显环的豁免规则缺失"


def test_reduced_motion_respected():
    css = (PROTO / "css" / "tokens.css").read_text(encoding="utf-8")
    m = re.search(r"@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{(.*?)\n\}", css, re.S)
    assert m, "tokens.css 需有 prefers-reduced-motion 降级块"
    body = m.group(1)
    for prop in ("animation-duration", "transition-duration", "animation-iteration-count"):
        assert prop in body, f"reduced-motion 块缺少 {prop}"


def test_toast_live_region_source():
    js = (PROTO / "js" / "dom.js").read_text(encoding="utf-8")
    assert "'aria-live': 'polite'" in js and "role: 'status'" in js, \
        "toast 宿主必须是 polite live region"
    assert "ensureToastHost()" in js, "live region 需在首条消息前就建立"


def test_design_tokens_meet_wcag_aa():
    spec = importlib.util.spec_from_file_location(
        "check_a11y_contrast", ROOT / "scripts" / "check_a11y_contrast.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    rows = mod.audit()
    assert rows, "对比度审计不应为空"
    fails = [r for r in rows if not r["ok"]]
    detail = "\n".join(
        f"{r['theme']} {r['fg']}({r['fg_hex']}) on {r['bg']}({r['bg_hex']}) "
        f"= {r['ratio']} < {r['need']} @ {r['where']}" for r in fails)
    assert not fails, f"设计令牌对比度未达标:\n{detail}"


# ---------------- a11y.js 纯函数(node 驱动) ----------------

RUNNER_MJS = """\
import assert from 'node:assert/strict';
import { normalizeKeys, stripShortcutSuffix, parseHintText, dedupeShortcuts }
  from './a11y.mjs';

assert.deepEqual(normalizeKeys('Control+Enter'), ['Ctrl', 'Enter']);
assert.deepEqual(normalizeKeys('Escape'), ['Esc']);
assert.deepEqual(normalizeKeys('Shift+Enter'), ['Shift', 'Enter']);
assert.deepEqual(normalizeKeys(''), []);

assert.equal(stripShortcutSuffix('发送 Enter'), '发送');
assert.equal(stripShortcutSuffix('切换主题 Ctrl+J'), '切换主题');
assert.equal(stripShortcutSuffix('停止 Esc'), '停止');
assert.equal(stripShortcutSuffix('执行待运行步骤 Ctrl+Enter'), '执行待运行步骤');
assert.equal(stripShortcutSuffix('附加文件（演示：不上传）'), '附加文件（演示：不上传）');

assert.deepEqual(parseHintText('Enter 发送 · Shift+Enter 换行'), [
  { keys: ['Enter'], label: '发送' },
  { keys: ['Shift', 'Enter'], label: '换行' },
]);
const line2 = parseHintText('Ctrl+B 面板 · Ctrl+K 聚焦 · Esc 停止 · ? 快捷键');
assert.equal(line2.length, 4);
assert.deepEqual(line2[0], { keys: ['Ctrl', 'B'], label: '面板' });
assert.deepEqual(line2[3], { keys: ['?'], label: '快捷键' });
// 普通中文句子不会被误判成快捷键
assert.deepEqual(parseHintText('描述研究任务 例如分析'), []);

const d = dedupeShortcuts([
  { keys: ['Esc'], label: '停止' },
  { keys: ['Esc'], label: '关闭' },
  { keys: ['Ctrl', 'B'], label: '面板' },
]);
assert.equal(d.length, 2);
assert.equal(d[0].label, '停止');
console.log('a11y logic ok');
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node 不可用,跳过 a11y.js 逻辑测试")
def test_a11y_logic_with_node(tmp_path):
    # 仓库无 package.json,node 会把 .js 当 CJS;复制为 .mjs 并改写内部 import
    for name in ("dom.js", "a11y.js"):
        src = (PROTO / "js" / name).read_text(encoding="utf-8")
        (tmp_path / name.replace(".js", ".mjs")).write_text(
            src.replace("./dom.js", "./dom.mjs"), encoding="utf-8")
    runner = tmp_path / "run.mjs"
    runner.write_text(RUNNER_MJS, encoding="utf-8")
    r = subprocess.run([shutil.which("node"), str(runner)],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, f"stdout={r.stdout}\nstderr={r.stderr}"
