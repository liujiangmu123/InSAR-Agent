# -*- mode: python ; coding: utf-8 -*-
"""insar-backend 冻结规格(onedir + 无窗)。

构建:desktop/backend-bundle/build_backend.ps1(或手动
  .venv\\Scripts\\python.exe -m PyInstaller desktop\\backend-bundle\\insar_backend.spec)

要点:
  - onedir:桌面 sidecar 场景启动快(onefile 每次启动都要向临时目录解压);
  - console=False:无窗常驻,诊断依赖壳/脚本的输出重定向;
  - datas:prototype/ 整目录 + 包内数据文件按原包相对路径摆进 _internal,
    冻结态 importlib.resources.files(...) / Path(__file__) 才能解析到同样的位置;
    清单必须与 pyproject.toml 的 [tool.setuptools.package-data] 保持同步
    (schema.sql / contract.yaml / wsl_wrapper.sh / scenario_packs/**);
  - local_wrapper.py 额外以数据文件带上:runtime/jobs.py 用
    Path(module.__file__) 把它交给外部解释器执行,PYZ 里的模块没有真实
    磁盘路径,补一份源码文件让该路径存在。
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

SPEC_DIR = Path(SPECPATH).resolve()      # noqa: F821  # desktop/backend-bundle
REPO_ROOT = SPEC_DIR.parents[1]
SRC_PKG = REPO_ROOT / "src" / "insar_agent"

datas = [
    (str(REPO_ROOT / "prototype"), "prototype"),
    (str(SRC_PKG / "core" / "schema.sql"), "insar_agent/core"),
    (str(SRC_PKG / "audit" / "contract.yaml"), "insar_agent/audit"),
    (str(SRC_PKG / "runtime" / "wsl_wrapper.sh"), "insar_agent/runtime"),
    (str(SRC_PKG / "runtime" / "local_wrapper.py"), "insar_agent/runtime"),
    # 场景技能包整目录:registry/scenarios.py 用 Path(__file__) 同级 scenario_packs
    # 扫描,漏掉不报错 —— SCENARIOS 静默变空,意图识别整体失效(首次打包实测补上)
    (str(SRC_PKG / "registry" / "scenario_packs"), "insar_agent/registry/scenario_packs"),
]

hiddenimports = sorted(set(
    collect_submodules("insar_agent")   # engines/* 等惰性 import,静态分析追不全
    + collect_submodules("uvicorn")     # logging/loops/protocols/lifespan 按字符串动态选择
    + ["numpy", "h5py"]                 # audit.runok / engines.qa 在函数内 import
))

a = Analysis(
    [str(SPEC_DIR / "entry.py")],
    pathex=[str(REPO_ROOT / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # hypothesis:dev-only 测试库,经 pydantic.v1._hypothesis_plugin /
    # hypothesis.extra.numpy 的可选 import 被静态分析拖进包(首次打包实测),显式排除
    # PIL:requirements.txt 声明为「桌面打包脚本(非 agent 运行时)」,却经
    # pygments.formatters.img 的可选 import 被拖进包(冻结探测复验实测,xref 溯源);
    # 该 formatter 只在显式请求 ImageFormatter 时才 import,后端无此路径,排除安全
    excludes=["tkinter", "hypothesis", "PIL"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="insar-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,   # 无窗:作为桌面 sidecar 不能闪黑框
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="insar-backend",
)
