"""设计令牌语义层(--c-*)对比度门禁:解析 prototype/css/tokens.css 的明暗两套
CSS 变量(支持 var() 引用链,别名层可参与计算),按真实使用场景的
「前景 × 背景」组合计算 WCAG 2.x 对比度,并核对 --c-* 别名层完整性。

与 scripts/check_a11y_contrast.py 的分工:
  - 旧脚本只认字面十六进制变量(旧名层),恒退出 0,硬门禁在 tests/test_a11y_dom.py;
  - 本脚本面向 2026-08 引入的 --c-* 语义层(状态五件套/焦点环/悬停底),
    会解析 var() 别名链,且**本身就是硬门禁**:任一项未达标即退出 1。

判定标准(WCAG 2.1 AA):
  - 正文/小字文本:>= 4.5;大字与 UI 组件/图形指示:>= 3.0;
  - 禁用态豁免(1.4.3 例外):.btn:disabled 等 opacity 降低的组合不参与判定。

用法(项目根目录):
  .venv\\Scripts\\python.exe scripts\\check_contrast.py           # 全表
  .venv\\Scripts\\python.exe scripts\\check_contrast.py --fails   # 只看未达标项

无第三方依赖;被 scripts/check_frontend.py 以套件 py:contrast_tokens 纳入门禁。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOKENS_CSS = ROOT / "prototype" / "css" / "tokens.css"

HEX_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
VAR_DECL_RE = re.compile(r"(--[\w-]+)\s*:\s*([^;]+);")
VAR_REF_RE = re.compile(r"^var\(\s*(--[\w-]+)\s*\)$")


def parse_theme_decls(css_text: str) -> dict[str, dict[str, str]]:
    """提取 :root(light)与 [data-theme='dark'] 两块的全部变量声明(不筛类型)。

    dark 继承 light 未覆写的变量,与 CSS 级联一致;值保留原文(可能是
    十六进制、rgba(...)、var(--x) 引用或尺寸),解析交给 resolve()。
    """
    blocks: dict[str, str] = {}
    for name, sel in (("light", ":root"), ("dark", "[data-theme='dark']")):
        m = re.search(re.escape(sel) + r"\s*\{(.*?)\}", css_text, re.S)
        blocks[name] = m.group(1) if m else ""
    light = {var: raw.strip() for var, raw in VAR_DECL_RE.findall(blocks["light"])}
    dark = dict(light)
    dark.update({var: raw.strip() for var, raw in VAR_DECL_RE.findall(blocks["dark"])})
    return {"light": light, "dark": dark}


def resolve(decls: dict[str, str], token: str, _depth: int = 0) -> str | None:
    """沿 var() 引用链解析到具体值;悬空引用或成环返回 None。"""
    if _depth > 12:
        return None
    val = decls.get(token)
    if val is None:
        return None
    m = VAR_REF_RE.match(val)
    return resolve(decls, m.group(1), _depth + 1) if m else val


def _channel(c8: int) -> float:
    c = c8 / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    r, g, b = (int(h[i: i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def contrast(fg: str, bg: str) -> float:
    """WCAG 对比度,值域 1..21。"""
    l1, l2 = relative_luminance(fg), relative_luminance(bg)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


# 组合清单:(前景 token, 背景 token, 达标线, 用在哪里)
# 依据 base/stream/dock/rail/setup 各文件的真实选择器整理,不是穷举笛卡尔积。
PAIRS: list[tuple[str, str, float, str]] = [
    # -- 状态五件套:前景落在同语义弱底(徽章/横幅/卡片,小字 4.5) --
    ("--c-ok-fg",   "--c-ok-bg",   4.5, "成功徽章/横幅(.tag.is-ok/.note.is-ok/.xcheck)"),
    ("--c-warn-fg", "--c-warn-bg", 4.5, "警示徽章/审批卡(.tag.is-stale/.ask .hd/.degrade)"),
    ("--c-bad-fg",  "--c-bad-bg",  4.5, "失败徽章/失败卡(.tag.is-bad/.fail .hd/.gate .hd)"),
    ("--c-info-fg", "--c-info-bg", 4.5, "信息横幅(.note.is-info/.reattach/.setup-note.is-info)"),
    ("--c-run-fg",  "--c-run-bg",  4.5, "运行态徽章/工具卡动词(.tag.is-run/.tool .verb)"),
    # -- 五件套前景落在卡面/页面底(计数徽标、成本行、链接) --
    ("--c-ok-fg",   "--c-surface", 4.5, "卡面上的成功文字(.pstep.d .fl/.plr-chip.is-done b)"),
    ("--c-warn-fg", "--c-surface", 4.5, "卡面上的警示文字(.pstep.s .fl/.fail .acts .cost)"),
    ("--c-bad-fg",  "--c-surface", 4.5, "卡面上的失败文字(.pstep.f .fl/.tkv.bad .v)"),
    ("--c-info-fg", "--c-surface", 4.5, "卡面上的信息链接(.plr-pop .lnk/.setup-discovered .ln)"),
    ("--c-run-fg",  "--c-surface", 4.5, "卡面上的运行态文字(.plr-chip.is-running b)"),
    ("--c-warn-fg", "--c-bg",      4.5, "页面底上的警示文字(.budget.is-warn)"),
    # -- 实心指示色:圆点/边条/进度(UI 组件 3.0) --
    ("--c-ok-solid",   "--c-bg",      3.0, "成功圆点/边条(.tool.is-ok .dot/.pstep.d 左缘)"),
    ("--c-warn-solid", "--c-bg",      3.0, "警示圆点/边条(.tool.is-warn .dot/.ceiling 左缘)"),
    ("--c-bad-solid",  "--c-bg",      3.0, "失败圆点/边条(.tool.is-bad .dot/.stepfail 左缘)"),
    ("--c-info-solid", "--c-bg",      3.0, "信息边条(.reattach 左缘)"),
    ("--c-run-solid",  "--c-bg",      3.0, "运行态圆点(.tool.is-run .dot)"),
    ("--c-ok-solid",   "--c-bg-sub",  3.0, "浅灰底上的成功指示(.plan .mini i.d 落 .arts 槽)"),
    ("--c-warn-solid", "--c-bg-sub",  3.0, "浅灰底上的警示指示(.rail-btn .pip)"),
    ("--c-bad-solid",  "--c-bg-sub",  3.0, "浅灰底上的失败指示(.tcard.err 左缘)"),
    ("--c-run-solid",  "--c-bg-sub",  3.0, "浅灰底上的运行指示(dock 抽屉把手徽标)"),
    ("--c-ok-solid",   "--c-bg-mute", 3.0, "进度/磁盘条轨道上的成功填充(.diskbar i/.prog > i)"),
    ("--c-warn-solid", "--c-bg-mute", 3.0, "磁盘条紧张态填充(.diskbar.tight i)"),
    ("--c-run-solid",  "--c-bg-mute", 3.0, "进度条运行填充(.prog.indet > i)"),
    # -- 实心底上的反色图形(步骤圆标中的对勾/数字,图形指示 3.0;
    #    按钮面上的真实文本已由旧门禁按 4.5 管:accent/stale/bad × text-inv) --
    ("--c-text-inv", "--c-ok-solid",   3.0, "完成步骤圆标(.plan li.d .mk/.pstep.d .no)"),
    ("--c-text-inv", "--c-warn-solid", 3.0, "失效步骤圆标(.pstep.s .no/.dock-handle .n)"),
    ("--c-text-inv", "--c-bad-solid",  3.0, "失败步骤圆标(.plan li.f .mk/.stepfail .cls)"),
    ("--c-text-inv", "--c-run-solid",  3.0, "运行步骤圆标(.plan li.r .mk/.pstep.r .no)"),
    # -- 焦点环:环绕元素可能落在页面/浅灰/卡面(UI 组件 3.0) --
    ("--c-focus-ring", "--c-bg",      3.0, "焦点环落页面底(:focus-visible 全局环)"),
    ("--c-focus-ring", "--c-bg-sub",  3.0, "焦点环落浅灰底(rail/sider/dock 内控件)"),
    ("--c-focus-ring", "--c-surface", 3.0, "焦点环落卡面(卡片内按钮/表单)"),
    # -- 悬停底上的文本:行悬停背景加深后仍需可读(4.5) --
    ("--c-text",   "--c-bg-hover", 4.5, "悬停行主文本(.row:hover/.pin:hover)"),
    ("--c-text-2", "--c-bg-hover", 4.5, "悬停行次级文本(.cbtn:hover/.pacts button:hover)"),
    ("--c-text-3", "--c-bg-hover", 4.5, "悬停行辅助文本(.row:hover .mt/.hs 小徽标)"),
]

# --c-* 别名层完整性:tokens.css 内声明的每个 --c-* 变量,在两主题下都必须
# 能沿 var() 链解析到具体值(防止悬空引用/成环/漏定义)。


def audit(css_text: str | None = None) -> list[dict]:
    """返回逐项审计结果(对比度组合 + 别名完整性),供命令行与测试共用。"""
    text = css_text if css_text is not None else TOKENS_CSS.read_text(encoding="utf-8")
    themes = parse_theme_decls(text)
    rows: list[dict] = []
    for theme in ("light", "dark"):
        decls = themes[theme]
        for fg, bg, need, where in PAIRS:
            fg_val, bg_val = resolve(decls, fg), resolve(decls, bg)
            ok_hex = bool(fg_val and bg_val and HEX_RE.match(fg_val) and HEX_RE.match(bg_val))
            ratio = contrast(fg_val, bg_val) if ok_hex else 0.0
            rows.append({
                "kind": "contrast", "theme": theme, "fg": fg, "bg": bg,
                "fg_hex": fg_val or "(悬空)", "bg_hex": bg_val or "(悬空)",
                "ratio": round(ratio, 2), "need": need,
                "ok": ok_hex and ratio >= need, "where": where,
            })
        for var in sorted(v for v in decls if v.startswith("--c-")):
            val = resolve(decls, var)
            rows.append({
                "kind": "alias", "theme": theme, "fg": var, "bg": "-",
                "fg_hex": val or "(悬空)", "bg_hex": "-",
                "ratio": None, "need": None,
                "ok": val is not None, "where": "别名链可解析",
            })
    return rows


def main(argv: list[str]) -> int:
    # Windows 控制台默认 GBK,强制 UTF-8 避免中文用途列变成乱码
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    only_fails = "--fails" in argv
    rows = audit()
    fails = [r for r in rows if not r["ok"]]
    contrast_rows = [r for r in rows if r["kind"] == "contrast"]
    alias_rows = [r for r in rows if r["kind"] == "alias"]

    shown = [r for r in (fails if only_fails else contrast_rows) if r["kind"] == "contrast"]
    print(f"{'主题':<5} {'前景':<16} {'背景':<16} {'色值(前景/背景)':<19} "
          f"{'对比度':>6} {'达标线':>5} 结果  用途")
    for r in shown:
        mark = "PASS" if r["ok"] else "FAIL"
        print(f"{r['theme']:<6} {r['fg']:<16} {r['bg']:<16} "
              f"{r['fg_hex']}/{r['bg_hex']:<9} {r['ratio']:>6.2f} {r['need']:>5.1f} "
              f"{mark}  {r['where']}")
    alias_bad = [r for r in alias_rows if not r["ok"]]
    print(f"\n别名完整性:--c-* 共 {len(alias_rows)} 项(两主题合计),"
          f"悬空 {len(alias_bad)} 项。")
    for r in alias_bad:
        print(f"  FAIL {r['theme']} {r['fg']} -> {r['fg_hex']}")

    print(f"\n合计 {len(rows)} 项,未达标 {len(fails)} 项。")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
