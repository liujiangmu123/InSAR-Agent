# 环境向导后端(/api/setup)集成说明

桌面版(desktop/ Tauri 壳)首启环境向导的后端,代码在
`src/insar_agent/api/setup_router.py`,**自包含、零改动既有模块**
(probe 复用 `runtime/probe.py` 的 `probe_environment`)。
本文档给出 app.py 的集成点;本分支**不改 app.py**(由集成分支执行)。

## 集成点(app.py,一行)

在 `create_app` 里 `app = FastAPI(...)` 之后加:

```python
from insar_agent.api.setup_router import setup_router

app.include_router(setup_router)
```

### INSAR_HOME 的传递方式

`settings.json` 落在 INSAR_HOME 下,router 有两种拿到 home 的方式:

1. **默认实例 `setup_router`(推荐,零参数)**:每次请求动态解析
   `os.environ["INSAR_HOME"]`(缺省 `./workspace`),与 `create_app(home=None)`
   的解析规则完全一致。桌面壳按现状通过环境变量传 INSAR_HOME 时,直接用上面那一行。
2. **`create_app(home=...)` 显式传了非环境变量的目录**:用工厂把同一个 home 传进来,
   保证 settings.json 与数据库同目录:

```python
from insar_agent.api.setup_router import create_setup_router

app.include_router(create_setup_router(home))   # home 是 create_app 已 resolve 的那个
```

注意:`create_app` 目前在启动时 `home.mkdir(...)`,先于向导;settings.json
由 `POST /api/setup/save` 首次写入时自动建目录,两边不冲突。

## 端点契约

### GET /api/setup/status —— 首启一次拿全的检测

无参数。加载顺序:若 `INSAR_HOME/settings.json` 存在,先加载并
`os.environ.setdefault`(**显式环境变量优先**,settings 只补空缺),再做检测。

```jsonc
{
  "ready": false,                       // 所有 required 检查项通过 = true
  "agent":  { "python": "3.12.7", "venv": true, "executable": "...python.exe" },
  "engine": {
    "prefix": "E:\\miniforge3\\envs\\insar",   // 或 null
    "prefix_configured": true,
    "prefix_exists": true,
    "engines": { "mintpy": "present(insar)", "gdal": "present(insar)",
                 "snaphu": null, "pyaps": null }   // probe 结果:非 null 即探测到
  },
  "data": { "source": "E:\\...\\RidgecrestSenDT71", "configured": true,
            "exists": true, "pair_count": 11 },    // */*unw_phase_clipped.tif 计数
  "disk": { "free_gb": 512.3, "total_gb": 1863.0 },
  "checks": [                            // 向导逐项渲染;ok=false 必有中文 fix_hint
    { "key": "agent_python",  "ok": true,  "message": "Agent 运行时 Python 3.12.7(venv)",
      "fix_hint": "", "required": true },
    { "key": "engine_prefix", "ok": false, "message": "未配置引擎环境(INSAR_ENGINE_PREFIX)",
      "fix_hint": "调用 POST /api/setup/engine-env 获取创建命令;……", "required": true },
    // engine_mintpy / engine_gdal(required)
    // engine_snaphu / engine_pyaps(required=false,缺失不拦 ready)
    // data_source / disk_space(required)
  ],
  "settings_file": "E:\\...\\workspace\\settings.json",
  "settings": { "engine_prefix": "...", "hyp3_source": "..." }   // 向导回填用
}
```

`ready = all(ok for 检查项 if required)`。snaphu/pyaps 标 `required=false`:
HyP3 云端路线不需要本地解缠,pyaps3 在数据源带缓存 ERA5.h5 时可免——缺失只提示不拦停。

### POST /api/setup/engine-env —— 只出命令清单,不执行

无请求体。**后端绝不执行安装**(遵守重型计算管控),只返回给前端展示/复制的命令。
conda 检测顺序:`E:\miniforge3`(condabin/Scripts)→ PATH(conda/mamba)。

```jsonc
{
  "detected_conda": "E:\\miniforge3\\condabin\\conda.bat",   // 或 null
  "commands": [
    { "title": "安装 Miniforge(已检测到 conda 时可跳过)",
      "command": "https://mirrors.tuna.tsinghua.edu.cn/github-release/conda-forge/miniforge/LatestRelease/Miniforge3-Windows-x86_64.exe",
      "note": "清华镜像下载,建议安装到 E:\\miniforge3;官方源:…" },
    { "title": "创建引擎环境(python=3.11 + MintPy,清华 conda-forge 镜像)",
      "command": "conda create -n insar -c https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud/conda-forge/ --override-channels python=3.11 mintpy -y",
      "note": "对应 README 已验证环境(MintPy 1.6.4 + GDAL 3.13.2)…" },
    { "title": "BLAS 切 OpenBLAS(必做)",
      "command": "conda install -n insar -c … \"libblas=*=*openblas\" -y",
      "note": "README「环境坑位记录」:MKL 2024 多线程延迟加载硬崩(0xC06D007F)" },
    { "title": "(可选)本地解缠 snaphu", "command": "conda install -n insar … snaphu -y", "note": "…" },
    { "title": "完成后保存引擎环境路径", "command": "E:\\miniforge3\\envs\\insar",
      "note": "作为 engine_prefix 通过 POST /api/setup/save 保存后复检 status" }
  ]
}
```

检测到 conda 时,命令前缀用检测到的完整路径(含空格自动加引号)。

### POST /api/setup/save —— 配置落盘 + 进程内热更新

```jsonc
// 请求(两个字段都可选,至少给一个;传 "" 表示清除该项)
{ "engine_prefix": "E:\\miniforge3\\envs\\insar",
  "hyp3_source":   "E:\\...\\RidgecrestSenDT71" }

// 响应
{ "ok": true,
  "settings_file": "E:\\...\\workspace\\settings.json",
  "settings": { "engine_prefix": "...", "hyp3_source": "..." },   // 合并后的全量
  "exists": { "engine_prefix": true, "hyp3_source": false } }     // 目录存在性如实上报
```

行为要点:

- **原子写**:同目录临时文件 + `os.replace`,崩溃不会留下半截 settings.json;
- **合并语义**:只更新传入的字段,未传字段保留;
- **热更新**:写入成功即更新本进程 `os.environ`
  (`INSAR_ENGINE_PREFIX` / `INSAR_HYP3_SOURCE`),probe/引擎解析立即生效,无需重启;
- 路径不存在**不报错**(向导允许「先保存、后创建环境」),存在性通过 `exists` 与
  下一次 `GET status` 反馈;
- 什么字段都不传 → 400。

## 桌面首启流程(前端参考)

```
GET /api/setup/status ─→ ready? ──是──→ 进主界面
        │ 否
        ├─ engine 项缺失 → POST /api/setup/engine-env → 展示命令清单(用户手动执行)
        ├─ 用户选好 conda 环境目录 / HyP3 数据目录 → POST /api/setup/save
        └─ 重新 GET /api/setup/status(轮询或「重新检测」按钮)直到 ready
```

## 测试

```powershell
.venv\Scripts\python.exe -m pytest tests/test_setup_router.py -q
```

测试直接构造独立 FastAPI 实例挂 router(不经 create_app),对 probe 打桩密封,
不依赖宿主 conda/PATH;**不要顺手跑全量 pytest**(其它测试会起子进程)。
