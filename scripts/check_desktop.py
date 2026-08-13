"""桌面冻结包完整性矩阵一键入口(自动化断言 + 手工清单见 docs/DESKTOP-PARITY.md)。

行为:dist 产物缺失(或显式 --rebuild)→ 先跑 desktop/backend-bundle/build_backend.ps1
构建冻结后端;随后执行 pytest -m desktop -q(tests/test_desktop_matrix.py)。

用法(仓库根,任一皆可):
    .venv\\Scripts\\python.exe scripts/check_desktop.py            # 有 dist 直接跑矩阵
    .venv\\Scripts\\python.exe scripts/check_desktop.py --rebuild  # UI/源码改动后强制重建再跑

注意:矩阵测试自身还有「dist 过旧」探测(冻结包内 UI 资源与源码逐字节比对),
比对失败时按提示 --rebuild 即可;矩阵起的冻结 exe 用随机高位端口 + 临时
INSAR_HOME,不触碰 8873 生产实例。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EXE = REPO / "desktop" / "backend-bundle" / "dist" / "insar-backend" / "insar-backend.exe"
BUILD_PS1 = REPO / "desktop" / "backend-bundle" / "build_backend.ps1"


def main() -> int:
    parser = argparse.ArgumentParser(description="桌面冻结包完整性矩阵(构建缺省按需)")
    parser.add_argument("--rebuild", action="store_true",
                        help="强制重建 dist 再跑矩阵(UI/源码改动后使用)")
    args, extra = parser.parse_known_args()

    if args.rebuild or not EXE.exists():
        print(f"[check_desktop] dist {'重建' if args.rebuild else '缺失,先构建'}:{BUILD_PS1}")
        rc = subprocess.run(
            ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(BUILD_PS1)],
            cwd=REPO).returncode
        if rc != 0:
            print(f"[check_desktop] 构建失败(exit={rc}),矩阵中止", file=sys.stderr)
            return rc

    # 透传多余参数给 pytest(如 -k、-x);缺省只跑 desktop 标记的矩阵
    cmd = [sys.executable, "-m", "pytest", "-m", "desktop", "-q", *extra]
    print(f"[check_desktop] {' '.join(cmd)}")
    return subprocess.run(cmd, cwd=REPO).returncode


if __name__ == "__main__":
    raise SystemExit(main())
