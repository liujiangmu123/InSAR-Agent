# pi 真实数据走查手册(Ridgecrest audited 读回)

人工验收剧本。走查第 1–3 步是**纯读回**:对着 `workspace\realtest` 里既有的 audited Ridgecrest run,不跑 MintPy、不新建假数据。第 4 步是重型计算,**必须先获用户明确批准**。

默认值与 `scripts/real_ridgecrest.py` 第 24–27 行同源:`INSAR_ENGINE_PREFIX=E:\miniforge3\envs\insar`,`INSAR_HYP3_SOURCE` 指向 RidgecrestSenDT71。真实模式固定 `INSAR_ALLOW_SIMULATED=0`(不可行就失败,绝不静默模拟)。密钥只从 `workspace/llm.json` 运行时读取,本手册不含任何 key。

仓库根:`E:\01所有项目\06定职讲师\00insaragent`。以下命令均在该目录执行。

---

## 1. 终端 A:真实模式后端

```powershell
pwsh scripts/insar-backend-real.ps1
```

默认:`INSAR_HOME=...\workspace\realtest`,端口 `8873`。启动日志应打印这三项:

- `INSAR_HOME` → `E:\01所有项目\06定职讲师\00insaragent\workspace\realtest`
- `INSAR_ENGINE_PREFIX` → `E:\miniforge3\envs\insar`
- `INSAR_HYP3_SOURCE` → `E:\01所有项目\06定职讲师\InSAR-Pro\insar-pro\backend\data\real_data\RidgecrestSenDT71`

另开一个 PowerShell,确认健康检查:

```powershell
Invoke-WebRequest http://127.0.0.1:8873/api/health
```

响应体应含 `{"ok": true, ...}`。窗口 A 保持开着,不要关。

改 HOME / 端口:

```powershell
pwsh scripts/insar-backend-real.ps1 -InsarHome workspace          # 主 HOME,不是 realtest
pwsh scripts/insar-backend-real.ps1 -Port 8874                    # 8873 被占时
$env:INSAR_API_BASE = "http://127.0.0.1:8874"                     # 再起 pi 前同步
```

---

## 2. 终端 B:pi 绑定既有 `real` 会话

```powershell
pwsh scripts/insar-pi.ps1 --insar-session real
```

绑定 `workspace\realtest` 里既有的 `real` 会话。侧栏应能看到该会话;若 `insar_list_sessions` 看不到 `real`,多半是 `INSAR_HOME` 指错(见下方排障)。

---

## 3. 会话内读回(全部真实数据,无重型计算)

在 pi 对话里依次让模型执行并核对:

1. **`insar_list_sessions`** → 列表含 `real`。
2. **`insar_run_status`(session=`real`)** → 11 步全 `done`(2-6 云端 skipped)、progress 100%、`simulated=false`、evidence 为 **checked**(云端跳过步无 `hyp3_manifest.json` 的如实封顶,封顶原因在账本里可读;2026-08-12 创建时按旧阶梯记为 audited);侧栏轨道同步显示。
3. **`insar_export_provenance`** → 真实台账(哈希、metrics 及 `reparsed_ok`)。
4. **`insar_read_log`(任选一步)** → 真实执行日志。
5. **`/insar-mode strict`** → 让模型试跑 `bash`,确认被 guard 拦截且给出 `insar_*` 引导话术;随后 `/insar-mode free` 恢复。

第 3 步全部通过即本手册的轻型验收(不需批准)。

---

## 4. ⚠️ 重型:新会话真实执行(须用户明确批准)

**不要擅自跑。** 先向用户说明:脚本等价基准为 `.venv\Scripts\python.exe scripts\real_ridgecrest.py`,预计分钟级、MintPy 满 CPU,一次只跑一个,低优先级启动。得到明确同意后再做:

1. `insar_create_session`(如 `ridgecrest-pi`)
2. `insar_plan_run`("Ridgecrest 地震同震形变分析")
3. `insar_execute_run`

未获批准则停在第 3 步。禁止用假数据/模拟数据代替这次真实执行。

---

## 5. 截图归档指引

人工验收证据,不入库亦可,存 `workspace/` 下由用户处置:

- pi TUI 全貌(侧栏 + 对话)
- strict 拦截时刻
- audited 状态行(`insar_run_status` 或侧栏)

---

## 排障

- **8873 被占**(旧后端还在跑):`netstat -ano | findstr 8873` → 结束旧进程,或 `-Port 8874` 并 `$env:INSAR_API_BASE="http://127.0.0.1:8874"` 再起 pi。
- **`INSAR_HOME` 指错**:pi 里 `insar_list_sessions` 看不到 `real` 即为此症;确认脚本打印的 `INSAR_HOME` 是 `...\workspace\realtest`。
- **realtest 的 `-shm/-wal` 文件**:属 SQLite WAL 正常伴生,勿删;后端独占期间不要并行再起第二个后端指向同一 HOME(锁冲突)。
- **引擎探测失败**:`insar_env_probe` 工具可在 pi 内直接诊断(它读 `/api/env` 真实探测结果);检查 `INSAR_ENGINE_PREFIX` 与 conda env 是否完好。
