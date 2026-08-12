# 开源参照项目：使用指南与参考链接

> 生成日期：2026-08-10
> 配套：`reference/repos/` 本地克隆 + `reference/COMPARISON_LEARNING.md` 对比学习表。
> 所有命令以官方 README/文档为准，此处为核实后的速查。

---

## 1. InSAR_Agent（直接竞品，DeepSeek + MintPy + HyP3）

**怎么用（官方推荐 Docker）**：
```bash
git clone https://github.com/KevinTyn/InSAR_Agent.git
cd InSAR_Agent
cp .env.example .env        # 填 DEEPSEEK_API_KEY（必填）
docker compose up -d        # 开发模式（热重载）
# 或 docker compose -f docker-compose.prod.yml up -d
# 打开 http://localhost:8866
```

**手动安装**：`pip install -e .` + `python -m uvicorn web.app:app --host 0.0.0.0 --port 8866 --reload`

**.env 关键配置**（已核实 `.env.example`）：
| 变量 | 必填 | 说明 |
|---|---|---|
| `DEEPSEEK_API_KEY` | 是 | DeepSeek API key |
| `DEEPSEEK_BASE_URL` / `DEEPSEEK_MODEL` | 否 | 默认 `api.deepseek.com` / `deepseek-v4-flash` |
| `HYP3_USERNAME` / `HYP3_PASSWORD` | 否 | ASF/HyP3 账号，用于提交 InSAR 作业 |
| `CDSAPI_KEY` | 否 | ERA5 大气校正 |

**对话示例**（自然语言驱动 6 步流水线）：
```
"查询北京地区2023年的Sentinel-1数据" → 查 SLC
"提交处理" → 提交 HyP3 作业
"运行MintPy时序分析，用ERA5大气校正" → 时序反演
"分析形变结果" → 形变分析/出图
```

**依赖**：Python 3.10+、GDAL、MintPy、ASF Search、HyP3 SDK、FastAPI。
**注意（审计结论）**：明文密码落盘、无鉴权文件接口、零测试——只作参考不部署。

**链接**：GitHub https://github.com/KevinTyn/InSAR_Agent

---

## 2. agentic-swmm-workflow（最重要参照物，已发 SCI）

**怎么用（5 分钟上手）**：
```bash
# macOS/Linux
curl -fsSL https://aiswmm.com/install.sh | bash
# Windows PowerShell
irm https://aiswmm.com/install.ps1 | iex
# 或 pip
pip install aiswmm==0.9.0
# 安装后
aiswmm            # 启动，首次跑 aiswmm setup 引导选 provider
```

**可复现运行（不装本地，pinned Docker 镜像）**：
```bash
docker run --rm -v "$PWD/runs:/app/runs" ghcr.io/zhonghao1995/agentic-swmm-workflow:v0.9.0 acceptance
```

**LLM provider**：10 条路由（OpenAI/Anthropic/OpenRouter/DeepSeek/Groq/Gemini/本地 Ollama/LM Studio/网关/自定义），`aiswmm setup` 向导检测。核心主张：自然语言编排 + 确定性 swmm5 执行 + 显式 provenance + 项目记忆 + verification-first。

**产物**：`model.inp/.rpt/.out` + manifest + 命令轨迹 + QA 摘要 + `experiment_provenance.json` / `comparison.json` / `experiment_note.md`（Obsidian 兼容）。

**也能给外部 Agent 用**：`npx skills add Zhonghao1995/agentic-swmm-workflow`（Codex/Claude Code/OpenClaw/Hermes 兼容）。

**链接**：
- GitHub https://github.com/Zhonghao1995/agentic-swmm-workflow
- 在线 demo（免装、浏览器跑）：https://aiswmm.com/demo/
- 论文（AI for Engineering, MDPI）：https://doi.org/10.3390/aieng1010005
- 预印本（EarthArXiv）：https://doi.org/10.31223/X5F47G
- Zenodo：https://doi.org/10.5281/zenodo.20337281
- PyPI：https://pypi.org/project/aiswmm/
- 生态：agentic-hydrology-platform / SWMMCanada / Agentic-MIKE-Plus（同作者）

---

## 3. aiida-core + aiida-workgraph（provenance 全栈）

**aiida-core**：
```bash
pip install aiida-core
verdi presto        # 一键初始化 profile（Postgres 或 SQLite）
verdi daemon start  # 守护进程
```
文档：https://aiida-core.readthedocs.io/ · GitHub https://github.com/aiidateam/aiida-core

**aiida-workgraph**（图式工作流 DSL，含 GUI）：
```bash
pip install aiida-workgraph
verdi presto                       # 前提：先有 AiiDA profile
pip install aiida-gui-workgraph
aiida-gui start                    # http://127.0.0.1:8000/workgraph
```
文档：https://aiida-workgraph.readthedocs.io/ · GitHub https://github.com/aiidateam/aiida-workgraph

用法形态：`@task` 装饰 Python 函数 → `@task.graph` 组图 → `add_multiply.run(...)`；provenance 图自动生成；checkpoint 由 plumpy 隐式提供。
注意：恢复粒度是“整进程重启 + 任务状态跳过”，`tests/test_checkpoint.py` 为空文件。

---

## 4. agentic-data-scientist（多 Agent 数据科学，K-Dense）

```bash
pip install agentic-data-scientist     # 或 uv tool install
# 需要两个 key：OPENROUTER_API_KEY（规划/评审）+ ANTHROPIC_API_KEY（编码）
export OPENROUTER_API_KEY=... ANTHROPIC_API_KEY=...
# 编排模式（规划→分阶段执行→评审→总结）
agentic-data-scientist "Perform differential expression analysis" --mode orchestrated --files data.csv
# 简单模式（直接写码，无规划）
agentic-data-scientist "Explain how gradient boosting works" --mode simple
```
产物保存 `./agentic_output/`；143 个科学技能自动 clone 到 `.claude/skills/`；可 `DISABLE_NETWORK_ACCESS=true` 断网。
GitHub https://github.com/K-Dense-AI/agentic-data-scientist

---

## 5. scientific-agent-skills（161 个科学技能包，33k★）

```bash
# 方式一：标准技能安装器（Claude Code/Codex/Cursor/Gemini CLI 等支持）
npx skills add K-Dense-AI/scientific-agent-skills
# 方式二：GitHub CLI（v2.90.0+）
gh skill install K-Dense-AI/scientific-agent-skills
```
技能=目录 `skills/<name>/{SKILL.md, references/, scripts/, assets/}`，frontmatter 闭集 6 字段 + `metadata.version` 必填；带测试契约与安全扫描。
GitHub https://github.com/K-Dense-AI/scientific-agent-skills · 标准 https://agentskills.io/
相关：K-Dense BYOK（桌面 AI 共同研究员，可自持 key）https://github.com/K-Dense-AI/k-dense-byok

---

## 6. OpenDiscoveryTrace（Agent 轨迹评测集，ICML 2026）

```bash
pip install -r requirements.txt
python src/analysis/analyze_trajectories.py     # 跑 4 个评测任务的分析
# 浏览一条轨迹
python -c "import json; t=json.load(open('data/samples/frontier/dd_e01_gpt-5.4.json')); print(t['metadata']['total_steps'], t['outcome'])" 
```
轨迹 step 10 字段（step_id/timestamp/phase/thought/action/observation/error/revision_trigger/confidence/raw_response）。完整 522 轨迹在 HuggingFace：https://huggingface.co/datasets/aayambansall/OpenDiscoveryTrace
GitHub https://github.com/aayambansal/OpenDiscoveryTrace

---

## 7. redun（惰性表达式工作流引擎）

```bash
pip install redun
redun run make.py make        # 懒表达式 → 动态 DAG → 缓存/provenance
redun log prog                # 查某个产物的完整上游推导链
redun log --file              # 查全部文件读写记录
```
特点：函数源码哈希+文件哈希双重失效判定、call graph 存 SQLite、三级缓存。
文档 https://insitro.github.io/redun/ · GitHub https://github.com/insitro/redun

---

## 8. 参考链接汇总表

| 项目 | GitHub | 文档/在线 | 论文/DOI |
|---|---|---|---|
| InSAR_Agent | github.com/KevinTyn/InSAR_Agent | — | 无论文 |
| agentic-swmm-workflow | github.com/Zhonghao1995/agentic-swmm-workflow | aiswmm.com/demo | doi.org/10.3390/aieng1010005 |
| aiida-core | github.com/aiidateam/aiida-core | aiida-core.readthedocs.io | doi.org/10.1038/s41597-020-00638-4 |
| aiida-workgraph | github.com/aiidateam/aiida-workgraph | aiida-workgraph.readthedocs.io | — |
| agentic-data-scientist | github.com/K-Dense-AI/agentic-data-scientist | — | — |
| scientific-agent-skills | github.com/K-Dense-AI/scientific-agent-skills | agentskills.io | — |
| OpenDiscoveryTrace | github.com/aayambansal/OpenDiscoveryTrace | HF: aayambansall/OpenDiscoveryTrace | ICML 2026 数据集竞赛 |
| redun | github.com/insitro/redun | insitro.github.io/redun | — |

---

## 9. 一句话结论

- **想最快看到“Agent 怎么驱动科学流程”**：跑 agentic-swmm 在线 demo（浏览器，零安装）。
- **想看 InSAR 领域现成的 Agent**：InSAR_Agent，Docker 一键起，但只建议参考其 4 个设计点（缓存锚定/task_id 句柄/事件工厂/双 SSE 取消），不要部署。
- **想要 provenance/断点能力**：aiida 全家桶是唯一集齐的，但重；我们的自研 SQLite 状态机路径不变。
- **想抄 registry 规范**：scientific-agent-skills 的 SKILL.md 契约 + 测试模式。
- **想造论文评测集**：OpenDiscoveryTrace 的 10 字段轨迹 schema 是现成模板。

---

## 10. 补充下载（2026-08-10 追加）：生态项目 4 个

### 10.1 agentic-hydrology-platform（同作者，上层编排平台）

**定位**：一个 agentic runtime（aiswmm）指挥多个专用引擎（SWMM / MIKE+ / LSTM）的"平台蓝图"，本仓库是其中 LSTM 引擎部分。

**怎么用**（本仓库是 LSTM 降雨-径流引擎）：
```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
python scripts/01_generate_data.py     # 合成降雨-径流数据
python scripts/03_train_lstm.py         # 训练+评估（NSE≈0.87）
```
> ⚠️ 训练属重型计算，本机按管控规则不执行，只读代码学习。

**吸收点**：多引擎编排蓝图（runtime 说话、引擎干活、每步都可审计产物）与我们的"Brain 选择题 + Engines 执行"同构；它已在论文体系中实践"同一套编排层接三种引擎"。

**链接**：https://github.com/Zhonghao1995/agentic-hydrology-platform

### 10.2 SWMMCanada（数据层/模型构建层）

**定位**：圈一块地 → 拉取加拿大开放数据（降雨/地形/土地覆盖/土壤/真实管网）→ 自动生成可运行的 `model.inp`。是 agentic-swmm 的上游数据源。

**怎么用**：
```bash
# 在线 beta（零安装，小面积）：https://swmm.h2ox.me/
docker run --rm -p 8000:8000 zhonghao0901/swmmcanada:latest   # 本地起 API
# 从源码（backend Python 3.11 + frontend npm）
cd backend && python3.11 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/uvicorn swmmcanada.api.main:app --port 8000
# 离线验证安装（不联网）：
backend/.venv/bin/python backend/scripts/smoke_build.py
```
Python API：`build_from_aoi(aoi_geojson, start_date, end_date, "out/")`。

**吸收点**：
1. **双模式自动路由**：Real network（35 城真实管网）vs Synthesize（其余区域街道图+开放数据合成），用户不设置、系统按"在哪画"自动选 → 与我们"数据获取候选方法矩阵"（ASF 查询/HyP3/本地导入）同构
2. **离线 smoke 验证**：`smoke_build.py` 不联网构建一个微型可运行模型 → 我们的 runtime probe 思路
3. **证据诚实**："uncalibrated model 复现全部 12 个降雨事件但体积偏 -22.3%，结构缺陷诚实说明" → 证据边界表范例

**链接**：https://github.com/Zhonghao1995/SWMMCanada · demo https://swmm.h2ox.me/ · DOI 10.5281/zenodo.21058544

### 10.3 Agentic-MIKE-Plus（headless 商业引擎自动化）

**定位**：MCP server + 5 个 skills 让 Agent 无 GUI 驱动 DHI MIKE+（商业软件）。验证于 MIKE+ 2026 `Sirius_RTC` 例（568 节点/576 链接）。

**怎么用**（把这段话贴给 AI 编码 Agent 即可）：
```
Install "Agentic MIKE+": clone 仓库；Python 3.11 x64 venv；
pip install -e ".[run]"（有 MIKE+ 许可证）或 pip install -e .（只读/出图免许可证）；
claude mcp add mike-plus -- "<路径>\\.venv\\Scripts\\python.exe" -m mikeplus_mcp.server；
复制 skills/* 到 ~/.claude/skills/；跑 scripts/smoke_test.py（应看到 10 个工具）。
```
10 个工具：`mike_model_info / mike_get_values / mike_set_values / mike_run / mike_results_list / summary / read / mike_plot_rain_flow / timeseries / network`。

**吸收点（对我们最重要的三个）**：
1. **许可证分层**：read/plot 免 license 跨平台；run/edit 才要 → 我们 HyP3 云端 vs 本地 ISCE2 的执行分层可借鉴
2. **worker 子进程隔离**：mikeplus 与 mikeio* 不能同进程，每个工具跑在独立 worker 子进程，server 本身不 import 任何引擎 → 我们 ISCE2/SNAP/PyStamps 的 conda 环境隔离同构
3. **引擎-agnostic 公共结果 schema**（"ready to sit beside SWMM and LSTM"）→ 与我们的 kind/layout 分离设计同构，是论文可引的旁证

**链接**：https://github.com/Zhonghao1995/Agentic-MIKE-Plus

### 10.4 K-Dense BYOK（桌面 AI 研究助手，149 技能）

**定位**：自带 API key 的桌面科研助手（Kady），Windows/macOS/Linux 本地运行，数据不出域（可接 Ollama 本地模型）。

**怎么用**：
```powershell
git clone https://github.com/K-Dense-AI/k-dense-byok.git
cd k-dense-byok
copy .env.example .env        # 可选：填 OpenRouter key
.\start.cmd                    # 首次启动自动装依赖（几分钟），浏览器开 http://localhost:3000
```
前提：Windows 需 Node.js 22+ 与 Git for Windows；至少一个模型源（OpenRouter key / 订阅 / Ollama）。

**吸收点（与我们的审计层直接对应）**：
1. **Living Lab Notebook**：记录 hypotheses/methods/observations/decisions/confidence/code/artifacts，可导出 Markdown/JSON/bundle，并**生成 manuscript 风格 Methods 草稿** → 与我们"provenance → 方法章节草稿"完全同构，可借鉴其条目 schema
2. **问之前先确认**：任务有歧义时暂停弹多选题表单，不猜 → 与我们"LLM 只做选择题"同思路的工程化
3. 本地 Ollama 支持（数据不出域）→ 我们的 provider 路由验证素材

**链接**：https://github.com/K-Dense-AI/k-dense-byok · 安装文档 docs/installation.md · Ollama 本地 docs/local-models-ollama.md