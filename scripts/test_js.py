# -*- coding: utf-8 -*-
"""前端 JS 单元测试运行器(零 npm 依赖)。

用法(项目根目录):
    .venv\\Scripts\\python.exe scripts\\test_js.py [额外 node --test 参数]

原理:直接调用 Node 内置 test runner(node --test)跑 tests/js/*.test.mjs,
不引入 package.json / node_modules。显式枚举测试文件传给 node,
避免不同 Node 版本对目录参数的解释差异。退出码与 node --test 一致,供门禁汇总。
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS_TEST_DIR = ROOT / "tests" / "js"


def main(argv: list[str]) -> int:
    # 中文 Windows 控制台默认 GBK,统一按 UTF-8 输出,避免摘要行乱码
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    node = shutil.which("node")
    if node is None:
        print(
            "[test_js] 未找到 node:前端测试需要 Node.js(>=18,内置 node --test)。\n"
            "[test_js] 请从 https://nodejs.org/ 安装并确保 node 在 PATH 中后重试。",
            file=sys.stderr,
        )
        return 2
    test_files = sorted(JS_TEST_DIR.glob("*.test.mjs"))
    if not test_files:
        print(f"[test_js] 未找到测试文件:{JS_TEST_DIR}\\*.test.mjs", file=sys.stderr)
        return 2
    cmd = [node, "--test", "--test-reporter=spec", *argv, *map(str, test_files)]
    proc = subprocess.run(cmd, cwd=ROOT)
    print(f"[test_js] {len(test_files)} 个测试文件,node --test 退出码 = {proc.returncode}")
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
