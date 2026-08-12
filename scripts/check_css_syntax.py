"""前端 CSS 质检:括号配平 + @media 断点与 base.css 清单的一致性。

无浏览器环境下的最低限度语法检查(配合人工 code review 使用):
1. 去掉注释与字符串后,逐文件核对 {} () [] 三种括号配平,报错带行号;
2. 抽取全部 @media 的 max-/min-width 像素值,与 base.css :root 声明的
   --bp-* 断点变量对照 —— min-width 允许 bp+1(与 max-width 互补的写法),
   其余必须命中清单,防止断点再度散落。

用法:.venv\\Scripts\\python.exe scripts\\check_css_syntax.py
退出码:0 = 全部通过;1 = 存在配平错误或清单外断点。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS_DIR = ROOT / "prototype" / "css"

PAIRS = {"}": "{", ")": "(", "]": "["}
OPENS = set(PAIRS.values())


def strip_comments_and_strings(text: str) -> str:
    """把注释与字符串替换为等长空白(保留换行,行号不漂移)。"""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append("".join("\n" if c == "\n" else " " for c in text[i:j]))
            i = j
        elif ch in ("'", '"'):
            j = i + 1
            while j < n and text[j] != ch:
                j += 2 if text[j] == "\\" else 1
            j = min(j + 1, n)
            out.append(" " * (j - i))
            i = j
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def check_balance(name: str, stripped: str) -> list[str]:
    """三种括号入栈出栈;错配时弹栈顶继续,避免一处错误级联刷屏。"""
    errors: list[str] = []
    stack: list[tuple[str, int]] = []
    line = 1
    for ch in stripped:
        if ch == "\n":
            line += 1
        elif ch in OPENS:
            stack.append((ch, line))
        elif ch in PAIRS:
            if stack and stack[-1][0] == PAIRS[ch]:
                stack.pop()
            else:
                errors.append(f"{name}:{line} 多余或错配的 '{ch}'")
                if stack:
                    stack.pop()
    errors.extend(f"{name}:{ln} 未闭合的 '{ch}'" for ch, ln in stack)
    return errors


def media_widths(stripped: str) -> list[tuple[str, int]]:
    """收集 @media 条件里的 (kind, px):kind ∈ {max, min}。"""
    found: list[tuple[str, int]] = []
    for cond in re.findall(r"@media([^{]*)\{", stripped):
        for kind, px in re.findall(r"(max|min)-width:\s*(\d+)px", cond):
            found.append((kind, int(px)))
    return found


def main() -> int:
    try:  # Windows 控制台默认码页可能吞中文
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    files = sorted(CSS_DIR.glob("*.css"))
    if not files:
        print(f"找不到 CSS 目录:{CSS_DIR}")
        return 1

    base_text = (CSS_DIR / "base.css").read_text(encoding="utf-8")
    bps = {
        m.group(1): int(m.group(2))
        for m in re.finditer(r"(--bp-[\w-]+)\s*:\s*(\d+)px", base_text)
    }
    print("base.css 声明的断点清单:")
    for name, px in bps.items():
        print(f"  {name} = {px}px")
    if not bps:
        print("FAIL:base.css 未声明任何 --bp-* 断点变量")
        return 1

    allowed = set(bps.values())
    errors: list[str] = []
    for f in files:
        stripped = strip_comments_and_strings(f.read_text(encoding="utf-8"))
        errors.extend(check_balance(f.name, stripped))
        widths = media_widths(stripped)
        marks = []
        for kind, px in widths:
            # min-width 允许「断点 + 1」的互补写法(如 1241 对应 1240)
            ok = px in allowed or (kind == "min" and px - 1 in allowed)
            marks.append(f"{kind}:{px}{'' if ok else '(!清单外)'}")
            if not ok:
                errors.append(f"{f.name} @media {kind}-width:{px}px 不在断点清单内")
        print(f"  {f.name}: 括号配平检查完成 · @media 宽度 [{', '.join(marks) or '无'}]")

    if errors:
        print("\nFAIL:")
        for e in errors:
            print(f"  {e}")
        return 1
    print("\nPASS:括号全部配平,@media 宽度断点全部命中 base.css 清单")
    return 0


if __name__ == "__main__":
    sys.exit(main())
