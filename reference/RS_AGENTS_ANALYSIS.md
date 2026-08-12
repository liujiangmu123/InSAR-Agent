# 遥感 Agent 项目：下载与分析研究（2026-08-10）

> 来源：用户提供的文章（Earth-Agent / ThinkGeo 详述 + 声称 6 个项目）
> 状态：原文仅详述 2 个项目；已通过官方调研仓库补齐生态全景。
> 本地克隆：`reference/repos/Earth-Agent`、`ThinkGeo`、`Awesome-Remote-Sensing-Agents`

---

## 1. 验证结论

| 项目 | 仓库 | 真伪 | 顶会 | Star | 活跃度 |
|---|---|---|---|---|---|
| Earth-Agent | opendatalab/Earth-Agent | ✅ 真实 | ICLR 2026 | 190 | 2026-08-07 |
| ThinkGeo | mbzuai-oryx/ThinkGeo | ✅ 真实 | CVPR 2026 **Best Paper (Oral)** | 83 | 2026-07-30 |
| 调研列表 | PolyX-Research/Awesome-Remote-Sensing-Agents | ✅ 真实（578★，100+ 论文） | Survey | 578 | 2026-08-10 |

> ⚠️ **用户文章"6 个项目"只公开了 2 个**；另外 4 个名称未在文本中给出，无法定向下载。
> 通过 Awesome 调研列表已确认当前遥感 Agent 领域**有代码可跑**的主要项目（见 §4），
> 如用户能补充那 4 个的名称，可直接补下。

---

## 2. Earth-Agent 深挖（ICLR 2026，上海AI实验室 + 中山大学）

**一句话**：给 LLM 配 104 个遥感专业工具，让模型自己决定什么时候用哪个——EO Agent 的开山框架。

### 2.1 架构（源码核验）

```
LLM (policy) ←→ ReAct 循环：tool calling → memory update → deliberation → action
工具层：104 个工具 = 5 个 Kit（agent/tools/ 下 5 个文件确认）
  Index.py       光谱指数（NDVI 等）
  Inversion.py   参数反演（含水量/温度等）
  Perception.py  感知（检测/分割/分类）
  Analysis.py    分析（变化检测/时序）
  Statistics.py  统计（面积/数量统计）
评测：Earth-Bench（248 题，Spectrum / Products / RGB 三模态）
  end-to-end: 最终 Accuracy + 轨迹 Efficiency
  step-by-step: Tool-Any-Order / Tool-In-Order / Tool-Exact-Match / Parameter Accuracy
```

### 2.2 怎么用（官方路径）

```bash
# 1. 下载评测数据（Earth-Bench, HuggingFace）
pip install huggingface-hub
huggingface-cli download Sssunset/Earth-Bench --local-dir ./benchmark/data --repo-type dataset
# 2. 配模型 API key（GPT-5 / DeepSeek / KimiK2 / Gemini 2.5 四套配置）
cp agent/config.json.example agent/config.json   # 填 key
# 3. 跑评测（langchain 框架，task_gpt.sh / task_deepseek.sh 等入口）
```
另：RGB 任务的专家模型权重已开源（online_infer 分支）。

### 2.3 对我们可吸收的（按价值排序）

1. **双级评测协议是论文直接素材**：`Tool-Any-Order / Tool-In-Order / Tool-Exact-Match / Parameter Accuracy` 四指标
   精确量化"LLM 是否可靠调用工具"→ 我们可对本地/云端模型跑同协议，产出"决策成功率对比"实验
2. **5-Kit 按科学功能组织工具**（而非按引擎）→ 印证我们 registry 的"步骤×方法矩阵"设计；
   它的 Index/Perception/Analysis 对应我们的能力分类
3. **MCP-based 工具生态**（agentlego 284 文件）→ 与我们"工具即接口"的层同构
4. **POMDP 形式化**：把 agent 循环建模为 POMDP（policy=LLM、观测=工具结果）→ 论文理论框架可引用
5. **反例（它没有而我们有的）**：零 provenance、零 stale detection、零断点续跑、零人机共控
   → 恰好坐实我们的 novelty 位置："评测框架 vs 可复现工程系统"

**链接**：GitHub github.com/opendatalab/Earth-Agent · 论文 arxiv.org/pdf/2509.23141 ·
数据集 huggingface.co/datasets/Sssunset/Earth-Bench

---

## 3. ThinkGeo 深挖（CVPR 2026 Best Paper）

**一句话**：给 Agent 打分的地理空间推理基准——486 道多步推理题，专家验证 1778 个推理步骤。

### 3.1 关键数据

- 486 任务：RGB 光学 436 + **SAR 50**；领域：城市/灾害响应/环境监测/航空
- 14 个可执行工具模拟真实 RS 工作流：perception / computation / logic / visual annotation 四类
- 双评测模式：step-by-step（指令遵循/参数结构/推理步骤）+ end-to-end（最终答案/图像接地准确率）
- **GPT-4o 得分率 38.6%** → 多步地理空间推理仍是前沿难题

### 3.2 怎么用

```bash
git clone https://github.com/mbzuai-oryx/ThinkGeo.git && cd ThinkGeo
# 数据：HF 下载 MBZUAI/ThinkGeo 放 ./opencompass/data/
# 模型：LMDeploy 环境跑 Qwen 等开源权重（或 API 模型）
conda create -n lmdeploy python=3.10 && conda activate lmdeploy
pip install lmdeploy
```

### 3.3 对我们可吸收的

1. **SAR 50 题是评测集种子**：含"飓风前后 Sentinel-1 影像识别受灾最严重区域"类任务
   → 直接对接我们的场景 A（Ridgecrest 地震）+ §13 "构造 InSAR 任务评测集"
2. **工具四分类**（perception/computation/logic/annotation）→ registry 工具分层的参考
3. **专家验证推理步骤的做法** → 与 OpenDiscoveryTrace 轨迹评测互证："只看最终输出会漏掉过程差异"
4. **论文论据**：GPT-4o 38.6% 说明"开放多步规划"不可靠 → 我们"规则收窄 + LLM 选择题"的差异化论据

**链接**：GitHub github.com/mbzuai-oryx/ThinkGeo · 项目页 mbzuai-oryx.github.io/ThinkGeo ·
论文 arxiv.org/abs/2505.23752 · 数据 huggingface.co/datasets/MBZUAI/ThinkGeo

---

## 4. 遥感 Agent 生态全景（来自 Awesome-Remote-Sensing-Agents，100+ 论文）

### 4.1 有代码、值得关注的项目

| 项目 | 类型 | 链接 | 与我们的关系 |
|---|---|---|---|
| **REMSA** | LLM Agent 选 RS 基础模型（2025.11） | github.com/be-chen/REMSA | "选方法"与我们的候选集选择题同思路 |
| **VHM** (AAAI'25) | 遥感 VLM（诚实性强调） | github.com/opendatalab/VHM | VLM 能力上限参考 |
| **LHRS-Bot** (ECCV'24) | 遥感多模态对话模型 | github.com/NJU-LHRS/LHRS-Bot | VLM 对话式分析参考 |
| **WildfireGPT** | RAG 多 Agent 自然灾害（2025.04） | github.com/Xieyangxinyu/WildfireGPT | RAG + 多 agent 工程参考 |
| **EarthLink** | 气候科学自进化 Agent | arXiv 2507.17311（无代码） | 自进化概念参考 |
| **CangLing-KnowFlow** | 知识+流程融合 Agent（2025.12） | arXiv 2512.15231（无代码） | 知识库注入参考 |
| **GeoMMAgent / RemoteAgent / OpenEarthAgent / GeoEvolver** | 2026 最新 | arXiv 2604.08896 / 2604.07765 / 2602.17665 / 2602.02559 | 论文引用素材 |

### 4.2 关键判断

- 遥感 Agent 主流形态 = **ReAct 开放规划 + 工具调用**（Earth-Agent/ThinkGeo 皆然），
  全部**没有** provenance / stale detection / 断点续跑 / 人机共控
- 我们的差异化定位在生态中更清晰：**科学工作流可复现性契约**是唯一空白象限
- 论文 related work 直接引用：Earth-Agent (ICLR'26)、ThinkGeo (CVPR'26)、
  Intelligent Remote Sensing Agents: A Survey (PolyX)、OpenDiscoveryTrace (ICML'26)

---

## 5. 与我们架构的对照（更新版）

| 维度 | Earth-Agent | ThinkGeo | 我们 | 结论 |
|---|---|---|---|---|
| LLM 角色 | 开放 ReAct 规划 | 评测对象 | 规则收窄后选择题 | 我们更稳（GPT-4o 38.6% 论据） |
| 工具组织 | 5 Kit（功能分类） | 14 工具（四类） | registry 步骤×方法矩阵 | 同构，可互证 |
| provenance | 无 | 无 | 完整（artifact SHA256+命令轨迹） | **唯一空白象限** |
| 失效判定 | 无 | 无 | 参数级 stale detection | **唯一空白象限** |
| 断点续跑 | 无 | 无 | SQLite 状态机步骤级 | **唯一空白象限** |
| 评测协议 | 双级（轨迹+步骤四指标） | 双模式（486题） | 六级证据阶梯 + 轨迹日志 | 可吸收其指标定义 |
| 人机共控 | 无 | 无 | 专家模式/向导模式切换 | **唯一空白象限** |

---

## 6. 行动建议

1. **吸收 Earth-Agent 双级评测四指标**进我们 §13 评测集设计（Tool-Exact-Match / Parameter Accuracy 直接可用）
2. **ThinkGeo 的 50 道 SAR 题**作为我们 InSAR 评测集的种子来源（对接场景 A）
3. **related work 更新**：加入遥感 Agent 生态 4 篇（Earth-Agent/ThinkGeo/Survey/OpenDiscoveryTrace）
4. 待用户确认那"6 个项目"中另外 4 个的名称 → 定向补下（可能含 GeoAgent/LandsatGPT 等）
5. REMSA / WildfireGPT 等如需可补下；VHM/LHRS-Bot 是 VLM 模型项目，与 agent 架构学习相关性低