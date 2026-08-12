"""a11y 对比度审计:解析 prototype/css/tokens.css 的明暗两套 CSS 变量,
按界面中实际使用的「前景 × 背景」组合计算 WCAG 2.x 对比度并输出审计表。

判定标准(WCAG 2.1 AA):
  - 正文/小字文本(本项目绝大多数文本 < 18.66px bold):>= 4.5
  - 大字文本(>= 24px 或 >= 18.66px bold)与 UI 组件/图形指示:>= 3.0

用法(项目根目录):
  .venv\\Scripts\\python.exe scripts\\check_a11y_contrast.py           # 全表
  .venv\\Scripts\\python.exe scripts\\check_a11y_contrast.py --fails   # 只看未达标项

无第三方依赖,可被 tests/test_a11y_dom.py 直接 import 复用。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOKENS_CSS = ROOT / "prototype" / "css" / "tokens.css"

HEX_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
VAR_DECL_RE = re.compile(r"(--[\w-]+)\s*:\s*([^;]+);")


def parse_theme_vars(css_text: str) -> dict[str, dict[str, str]]:
    """提取 :root(light)与 [data-theme='dark'] 两个块里的十六进制颜色变量。

    dark 主题继承 light 中未覆写的变量(与 CSS 级联行为一致)。
    非颜色变量(尺寸/字体/阴影)自然被 HEX_RE 过滤掉。
    """
    blocks: dict[str, str] = {}
    for name, sel in (("light", ":root"), ("dark", "[data-theme='dark']")):
        m = re.search(re.escape(sel) + r"\s*\{(.*?)\}", css_text, re.S)
        blocks[name] = m.group(1) if m else ""

    themes: dict[str, dict[str, str]] = {"light": {}, "dark": {}}
    for name, body in blocks.items():
        for var, raw in VAR_DECL_RE.findall(body):
            val = raw.strip()
            if HEX_RE.match(val):
                themes[name][var] = val.lower()
    merged_dark = dict(themes["light"])
    merged_dark.update(themes["dark"])
    themes["dark"] = merged_dark
    return themes


def _channel(c8: float) -> float:
    c = c8 / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def contrast(fg: str, bg: str) -> float:
    """WCAG 对比度,值域 1..21。"""
    l1, l2 = relative_luminance(fg), relative_luminance(bg)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


# 组合清单:(前景变量或字面色, 背景变量或字面色, 达标线, 用在哪里)
# 依据 base.css / stream.css / dock.css 的真实选择器整理,不是穷举笛卡尔积。
PAIRS: list[tuple[str, str, float, str]] = [
    ("--text",        "--bg",          4.5, "正文(body)"),
    ("--text-2",      "--bg",          4.5, "次级文本(.hero p/.who 等)"),
    ("--text-2",      "--bg-sub",      4.5, "徽章/侧栏次级文本(.tag/.gcard .bar)"),
    ("--text-3",      "--bg",          4.5, "辅助文本(时间戳/占位符/.cmeta 快捷键提示)"),
    ("--text-3",      "--bg-sub",      4.5, "辅助文本落在浅灰底(.sider-hd .lbl/.sect)"),
    ("--text-3",      "--bg-mute",     4.5, "小徽标(.tool .code/.dot-tag.wait/.row .hs)"),
    ("--accent",      "--bg",          4.5, "链接 a{color:var(--accent)}"),
    ("--accent-text", "--accent-weak", 4.5, "运行态徽章/工具卡动词(.tag.is-run/.verb)"),
    ("--ok-text",     "--ok-weak",     4.5, "成功徽章(.tag.is-ok/.xcheck)"),
    ("--stale-text",  "--stale-weak",  4.5, "失效徽章/审批卡标题(.tag.is-stale/.ask .hd)"),
    ("--bad-text",    "--bad-weak",    4.5, "失败徽章/失败卡(.tag.is-bad/.fail .hd)"),
    ("--think",       "--bg",          4.5, "思考条目标题(.think>summary)"),
    ("--text-inv",    "--accent",      4.5, "主按钮文字(.btn-pri/发送钮/用户气泡)"),
    ("--text-inv",    "--stale",       4.5, "警示按钮文字(.btn-wrn 重跑失效步骤)"),
    ("--text-inv",    "--bad",         4.5, "危险按钮文字(.btn-dng/停止钮)"),
    ("--bg",          "--text",        4.5, "toast 气泡(反色)"),
    ("--term-fg",     "--term-bg",     4.5, "终端正文(.tool .out/.shell)"),
    ("--term-dim",    "--term-bg",     4.5, "终端弱化行(.ln.dim)"),
    ("--term-cmd",    "--term-bg",     4.5, "终端命令行(.ln.cmd)"),
    ("--term-ok",     "--term-bg",     4.5, "终端成功行(.ln.ok)"),
    ("--term-warn",   "--term-bg",     4.5, "终端警告行(.ln.warn)"),
    ("--term-err",    "--term-bg",     4.5, "终端错误行(.ln.err)"),
    ("--border-focus", "--bg",         3.0, "键盘焦点环(:focus-visible,UI 组件 3:1)"),
    ("--accent",      "--bg",          3.0, "运行态圆点/图标类指示(UI 组件 3:1)"),
    ("--stale",       "--bg",          3.0, "失效圆点/边条指示(UI 组件 3:1)"),
    ("--bad",         "--bg",          3.0, "失败圆点/边条指示(UI 组件 3:1)"),
    ("--ok",          "--bg",          3.0, "成功圆点/边条指示(UI 组件 3:1)"),
]


def resolve(themes: dict[str, dict[str, str]], theme: str, token: str) -> str | None:
    if token.startswith("--"):
        return themes[theme].get(token)
    return token if HEX_RE.match(token) else None


def audit(css_text: str | None = None) -> list[dict]:
    """返回逐项审计结果,供命令行输出与单元测试共用。"""
    text = css_text if css_text is not None else TOKENS_CSS.read_text(encoding="utf-8")
    themes = parse_theme_vars(text)
    rows: list[dict] = []
    for theme in ("light", "dark"):
        for fg, bg, need, where in PAIRS:
            fg_hex, bg_hex = resolve(themes, theme, fg), resolve(themes, theme, bg)
            if not fg_hex or not bg_hex:
                continue
            ratio = contrast(fg_hex, bg_hex)
            rows.append({
                "theme": theme, "fg": fg, "bg": bg,
                "fg_hex": fg_hex, "bg_hex": bg_hex,
                "ratio": round(ratio, 2), "need": need,
                "ok": ratio >= need, "where": where,
            })
    return rows


def main(argv: list[str]) -> int:
    # Windows 控制台默认 GBK,强制 UTF-8 避免中文用途列变成乱码
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    only_fails = "--fails" in argv
    rows = audit()
    fails = [r for r in rows if not r["ok"]]
    shown = fails if only_fails else rows
    print(f"{'主题':<5} {'前景':<14} {'背景':<14} {'色值(前景/背景)':<19} "
          f"{'对比度':>6} {'达标线':>5} 结果  用途")
    for r in shown:
        mark = "PASS" if r["ok"] else "FAIL"
        print(f"{r['theme']:<6} {r['fg']:<14} {r['bg']:<14} "
              f"{r['fg_hex']}/{r['bg_hex']:<9} {r['ratio']:>6.2f} {r['need']:>5.1f} "
              f"{mark}  {r['where']}")
    print(f"\n合计 {len(rows)} 项,未达标 {len(fails)} 项。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
