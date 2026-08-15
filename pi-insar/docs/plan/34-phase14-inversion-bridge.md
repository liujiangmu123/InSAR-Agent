# Phase 14 · 科学能力扩展 V:反演桥(GBIS / Kite / GMT / QGIS / HDF-EOS5 标准导出)

> **一句话目标**:把速度场/形变场按**专业反演与 GIS 软件的标准格式**导出,让"断层滑动反演、形变源建模"这类深度解译可以无缝交给专业软件。
> **边界(重申 `04-science-capability-map.md` §6)**:**不自造反演算法**。Okada/贝叶斯滑动分布是独立学科软件的领域;自造反演无法验证、无法与文献对齐,违背"证据可溯源"的立身之本。本项目把桥修好:降采样、格式、元数据做对。

## 0. 本机已有的桥(实测清点)

| CLI | 目标软件 | 用途 |
|---|---|---|
| `save_gbis.py` | GBIS(Geodetic Bayesian Inversion Software,MATLAB) | 贝叶斯形变源反演(Okada/Mogi/Yang 等) |
| `save_kite.py` | Kite(pyrocko 生态) | InSAR 场景降采样(quadtree)与协方差估计 → Grond 反演 |
| `save_gmt.py` | GMT | 制图级栅格(grd) |
| `save_qgis.py` | QGIS | 时序矢量点(直接拖进 QGIS 看曲线) |
| `save_hdfeos5.py` | HDF-EOS5 | 数据存档/共享标准(UNAVCO 惯例) |

全部在 `E:\miniforge3\envs\insar` 实测存在——工作量是声明与接线,不是造轮子。

## 1. 能力设计(分析链追加第 28 步)

`Capability(id=28, name="反演桥导出", group="analysis", deps=(24,), default_method="passthrough")`

| 方法 | CLI | 关键参数 |
|---|---|---|
| `bridge_gbis` | `save_gbis.py <vel> -g <geom>` | 输入速度/位移场 + 几何文件 |
| `bridge_kite` | `save_kite.py <file>` | 输入位移/速度场 |
| `bridge_gmt` | `save_gmt.py <geo_file>` | 需已地理编码 |
| `bridge_qgis` | `save_qgis.py <ts_file> -g <geom_geo>` | 需地理编码时序 |
| `bridge_hdfeos5` | `save_hdfeos5.py <ts_file>` | 需完整 mintpy 工作区元数据 |
| `passthrough` | — | 本次分析不导出 |

产物:`analysis/bridge/`(`ArtifactSpec("bridge_out", ("analysis/bridge",), kind="DATA", policy="stat")`)。

**诚实前置**:每个方法的输入前提(是否需地理编码、需几何文件)写进 `Method.extra` 与 hint;前提不满足让 MintPy CLI 真实报错并由 triage 呈现,**不做静默转换**(隐式 geocode 会改变数据语义,必须是用户看得见的一步)。

## 2. 文件级改动

1. `src/insar_agent/registry/capabilities.py`:按 §1 追加第 28 步(形态照抄 25 步);
2. `src/insar_agent/engines/mintpy_post.py`:追加五个 `bridge_*` 分支(单 argv,输出统一 `analysis/bridge/`);
3. `src/insar_agent/engines/__init__.py`:`bridge_*` 方法路由到 `mintpy_post.build`;
4. `pi-insar/APPEND_SYSTEM.md` 补:

```markdown
- 用户要做断层反演/形变源建模 → 不在本 Agent 内反演;用分析 run 第 28 步
  bridge_gbis(GBIS)或 bridge_kite(Kite/Grond)导出标准输入,并说明后续在
  对应软件中的操作入口。被问"帮我反演出断层参数"时如实说明边界。
- QGIS 时序点交付 → bridge_qgis;GMT 制图 → bridge_gmt;数据存档 → bridge_hdfeos5。
```

5. `skills/00-insar-agent/SKILL.md` 补《反演桥剧本》:同震场景跑完主链 → 分析 run(21 掩膜 → 28 bridge_gbis)→ 告知用户 GBIS 侧的输入文件与下一步。
6. 测试:`tests/test_registry_groups.py` 分析断言更新为 `[20..28]`;`tests/test_bridge_export.py` 新建——对 realtest 真实 `velocity.h5` 跑 `bridge_kite`(最轻依赖),断言输出文件存在且非空;引擎缺失场景断言 ToolMissing 语义(**不是**静默跳过)。

## 3. 验收

```powershell
.venv\Scripts\python.exe -m pytest tests/test_bridge_export.py -q; .venv\Scripts\python.exe -m pytest -q
```

真实验收(轻计算):realtest 真实速度场 → `bridge_gbis` + `bridge_qgis`,产物用对应软件打开各验一次(GBIS 的 .mat 用 `scipy.io.loadmat` 抽查字段;QGIS 手动拖入看点)。

## 4. Git

```powershell
git add src/insar_agent/registry/capabilities.py src/insar_agent/engines/mintpy_post.py `
        src/insar_agent/engines/__init__.py pi-insar/APPEND_SYSTEM.md `
        pi-insar/skills/00-insar-agent/SKILL.md tests/test_bridge_export.py tests/test_registry_groups.py
git commit -m "feat(analysis): 反演桥 —— GBIS/Kite/GMT/QGIS/HDF-EOS5 标准导出(不自造反演)

深度解译的正确姿势是把桥修好而不是重造反演:五个 save_* CLI 本机已有,
声明为分析链 28 步;输入前提如实声明,不满足即真实报错,不做静默转换。"
```

## 5. 完成标记

- [ ] 五个桥方法声明齐全,前提写进 extra/hint
- [ ] realtest 真实产物过 GBIS 与 QGIS 桥并在目标软件侧抽查
- [ ] 引擎缺失 → ToolMissing,绝不静默跳过
- [ ] APPEND_SYSTEM 写明"不在 Agent 内反演"的边界话术
- [ ] 分析规划断言更新为 [20..28]

→ 下一个文件:`35-phase15-scenario-packs.md`
