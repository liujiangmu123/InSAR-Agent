# 后端冻结管线(PyInstaller)

目的:桌面版不依赖用户装 Python —— 把 FastAPI 后端(`src/insar_agent`)连同
静态 UI(`prototype/`)冻结成独立 onedir 产物,由 Tauri 壳(`desktop/`)作为
sidecar 启动。业务代码零改动(唯一例外:`api/app.py` 增加 `INSAR_UI_DIR`
环境变量覆盖点,见下)。

## 一键构建

```powershell
powershell -ExecutionPolicy Bypass -File desktop\backend-bundle\build_backend.ps1
```

- 静默构建:全程输出写入 `desktop\backend-bundle\build.log`,不弹任何窗口,
  控制台只出一行结果;
- 自动确保仓库根 `.venv`(缺则 `py -m venv .venv`),安装 `requirements.txt`
  + `pip install -e .` + `requirements-desktop.txt`(PyInstaller 版本钉住);
  环境已就绪时可加 `-SkipDeps` 提速;
- 产物落在 `desktop\backend-bundle\dist\insar-backend\`(gitignore,不入库)。

## 产物结构

```
dist/insar-backend/
├── insar-backend.exe        # 无窗(console=False)启动器
└── _internal/
    ├── prototype/           # 静态 UI 整目录
    ├── insar_agent/
    │   ├── core/schema.sql          # 三个包内数据文件按原包相对路径摆放,
    │   ├── audit/contract.yaml      # importlib.resources 冻结态解析到同一位置
    │   └── runtime/wsl_wrapper.sh
    │       └── local_wrapper.py     # 源码文件随包:jobs.py 按 __file__ 路径
    │                                # 把它交给外部解释器执行
    ├── python314.dll + numpy/h5py 等依赖
    └── ...
```

## 运行时约定(entry.py)

| 环境变量 | 含义 | 缺省 |
|---|---|---|
| `INSAR_PORT` | 监听端口 | `8873` |
| `INSAR_HOST` | 监听地址 | `127.0.0.1` |
| `INSAR_HOME` | 数据目录 | 冻结态 `%LOCALAPPDATA%\insar-agent\workspace`(源码运行仍是 cwd 相对 `workspace/`,行为不变) |
| `INSAR_UI_DIR` | 静态 UI 目录覆盖 | 自动探测:exe 旁 `prototype/` 优先,其次 `_internal\prototype` |

UI 目录解析:冻结后 `api/app.py` 里按 `__file__` 回溯源码树的 `PROTOTYPE_DIR`
失效,`app.py` 只认 `INSAR_UI_DIR` 这一个环境变量覆盖点,`entry.py` 在
import app 之前解析并设置它。在 exe 旁手动放一份 `prototype/` 会优先生效,
便于不重打包调 UI。

日志:调用方重定向了 stdout/stderr(Tauri 壳、冒烟脚本)则日志走重定向目标;
裸启动(双击/无重定向)时 console=False 进程的标准流是 None,`entry.py` 会把
它们绑定到 `%INSAR_HOME%\logs\backend.log`(否则 uvicorn 对着 None 写日志会
秒崩,实测踩过)。启动阶段崩溃另落 `logs\backend-crash.log`。

## 冒烟验证

```powershell
powershell -ExecutionPolicy Bypass -File desktop\backend-bundle\smoke_test.ps1   # 可选 -Port 18873
```

无窗后台启动冻结 exe → 轮询 `/api/health` 等 200 → 立即结束进程。
退出码 0 即通过;数据目录用一次性 `.smoke-home`,不污染真实 workspace;
后端输出重定向到 `smoke-backend.log` / `smoke-backend.err.log` 供排查。

## 与 Tauri 壳(desktop/)的对接(已接入)

壳(`desktop/src/sidecar.rs` 的 `find_backend`)当前探测顺序:

1. **exe 同目录 `backend\insar-backend.exe` 最优先** —— 打包分发形态:把
   `dist\insar-backend\` 整目录拷到壳 exe 旁并命名为 `backend\`,直接 spawn,
   不再需要任何 Python(端口裁决在探测之前:`INSAR_PORT` 上已有健康后端时
   直接连接、不 spawn,开发时手动起 uvicorn 的场景不变);
2. 环境变量 `INSAR_PYTHON`;
3. 仓库根 `.venv`;
4. PATH python(2-4 为源码开发形态兜底,spawn `python -m insar_agent.api.app`)。

spawn 语义两种形态完全相同:显式传 `INSAR_PORT`、stdout/stderr 重定向到 5MB
滚动日志、轮询 `/api/health`(30s 超时)、退出直接 kill(后端自带 orphan/
reattach 语义,杀进程等价断点续跑)。唯一区别:冻结形态不把工作目录设为仓库根
(设为产物目录)—— 产物自带 UI 与数据文件,`INSAR_HOME` 缺省自动落
`%LOCALAPPDATA%`。诊断页会显示探测结果是「冻结后端:路径」还是
「Python:路径(来源:…)」。

## 边界与已知限制

- **引擎环境不进包**:ISCE2/MintPy 等仍走 WSL/conda(2GB+ 不进安装包),
  `wsl_wrapper.sh` 已随包,WSL 执行链不受冻结影响;
- **本地 python 作业**:`runtime/jobs.py` 用 `sys.executable` 拉起
  `local_wrapper.py`,冻结态 `sys.executable` 是本 exe 而非 python。
  `local_wrapper.py` 源码已随包(路径存在),但接通本地引擎解释器时
  jobs.py 应改为显式解释器路径(如读 `INSAR_PYTHON`)—— 属后续壳/运行时
  工作,不在本目录范围;
- **matplotlib 未随包**(不在 `requirements.txt`):`figure_journal` 的真实
  实现 `import matplotlib` 会失败,与源码环境行为一致,模拟模式不受影响
  (构建日志里 matplotlib/uvloop 的 missing 警告属预期);
- **升级 PyInstaller**:改 `requirements-desktop.txt` 后必须重跑
  `smoke_test.ps1`(bootloader/布局跨版本差异大)。

## spec 要点(insar_backend.spec)

- **onedir 而非 onefile**:桌面 sidecar 要求冷启动快;onefile 每次启动都要向
  临时目录解压,且被 kill 后残留脏临时目录;
- **console=False**:无窗常驻,不闪黑框;诊断依赖调用方的输出重定向;
- **datas**:`prototype/` 整目录 + `schema.sql` / `contract.yaml` /
  `wsl_wrapper.sh`(按原包相对路径)+ `local_wrapper.py` 源码;
- **hiddenimports**:`collect_submodules("insar_agent")`(engines/* 惰性
  import,静态分析追不全)+ `collect_submodules("uvicorn")`(logging/loops/
  protocols/lifespan 按字符串动态选择)+ `numpy` / `h5py`(函数内 import);
- 产物体积:实测 67.5 MB(numpy + h5py 占大头),冷启动到健康检查通过约 2~3 秒。
