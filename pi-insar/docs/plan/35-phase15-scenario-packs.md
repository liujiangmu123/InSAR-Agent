# Phase 15 · 科学能力扩展 VI:多源数据接入 + 场景包 + 端到端剧本

> **一句话目标**:让 Agent 不挑数据、不挑场景——接入 MintPy 支持的全部处理器产品(ISCE/ARIA/GAMMA/GMTSAR/SNAP/HyP3),并为常见应用(同震/沉降/滑坡/冻土/火山)提供"开箱即得专家配置"的场景包与端到端剧本。
> **前置事实(已核实)**:场景包加载器(`registry/scenarios.py`)是**目录扫描 + frontmatter 闭集校验**——`registry/scenario_packs/<key>/{SKILL.md, overrides.yaml}`,新增场景包 = 加目录,零代码;本机 MintPy 有全部 9 个 `prep_*.py` 接入器。

## 1. 多源数据接入(先修数据面,场景包才有意义)

### 1.1 问题

`engines/mintpy.py` 的 `_CFG_TEMPLATE` 把数据面**硬编码**为 HyP3:

```python
        processor="hyp3",
        unw_file="../hyp3/*/*unw_phase_clipped.tif",
        cor_file="../hyp3/*/*corr_clipped.tif",
        ...
```

这意味着 ISCE2 本地链(第 3-6 步自产的干涉对)、ARIA GUNW、GAMMA、GMTSAR、SNAP 产品**都进不了第 7 步**——数据面是全流程 Agent 的第一瓶颈。

### 1.2 做法:第 7 步参数化数据面

`capabilities.py` 第 7 步 `params` 追加(全部 science,进指纹):

```python
            "processor": Param("hyp3", kind="science", type="str",
                               enum=("hyp3", "isce", "aria", "gamma", "gmtsar", "snap", "roipac", "nisar"),
                               hint="干涉产品的处理器布局(MintPy load.processor 语义)"),
            # 文件模式:相对 mintpy/ 工作目录;默认值 = HyP3 布局(与旧硬编码逐字相同,
            # 保证既有 run 的 cfg 渲染结果不变 —— 这是回归保护的关键)
            "unw_pattern": Param("../hyp3/*/*unw_phase_clipped.tif", kind="science", type="str"),
            "cor_pattern": Param("../hyp3/*/*corr_clipped.tif", kind="science", type="str"),
            "dem_pattern": Param("../hyp3/*/*dem_clipped.tif", kind="science", type="str"),
            "inc_pattern": Param("../hyp3/*/*lv_theta_clipped.tif", kind="science", type="str"),
            "water_pattern": Param("../hyp3/*/*water_mask_clipped.tif", kind="science", type="str"),
```

`engines/mintpy.py` 的 `render_cfg()` 改为从 p7 读这些参数(回退值与今日硬编码**逐字相同**):

```python
        processor=p7.get("processor", "hyp3"),
        unw_file=p7.get("unw_pattern", "../hyp3/*/*unw_phase_clipped.tif"),
        ...
```

**ISCE2 本地链布局**(第 3-6 步自产,stripmap 场景包用):`processor=isce`,`unw_pattern="../isce2/interferogram/filt_topophase.unw.geo"` 等——具体模式以 `docs/VALIDATION-isce2-wsl.md` 实测布局为准,**落场景包 overrides,不落默认值**。

### 1.3 回归保护测试

```python
def test_cfg_data_plane_default_is_byte_identical_to_legacy():
    """参数化后,默认渲染的 cfg 与参数化前逐字节相同(不破坏既有 run 语义)。"""
```

## 2. 新场景包(每个 = 一个目录,零代码)

已有 4 包:`quake` / `landslide` / `permafrost` / `stripmap_coseismic`。新增 2 包:

### 2.1 `registry/scenario_packs/subsidence/`(城市地面沉降 —— InSAR 最高频应用)

`overrides.yaml`:

```yaml
step_overrides:
  7:
    params:
      max_temporal_baseline: 90        # 城市高相干,短基线密网
  8:
    params:
      ramp: linear                     # 局地沉降:线性去斜合法(capabilities.py C1 注释的例外场景)
  9:
    method: poly_periodic              # 抽水型沉降常带年周期
    params:
      periods: [1]
  10:
    params:
      figure_set: [velocity, coherence, mask, points_timeseries]
```

`SKILL.md` frontmatter 按闭集(`name`/`description` + `metadata` 的 `label/match/chain/model/pick_7` 必填,照抄 `quake` 包形态),正文写:选参依据(为何 90 天/为何年周期)、质量门侧重(参考点选在稳定基岩)、交付侧重(漏斗范围 + 点位曲线 + GeoTIFF)。

### 2.2 `registry/scenario_packs/volcano/`(火山形变)

`overrides.yaml`:`9: method: exponential`(岩浆房充放的指数衰减形态)+ `8: params: {ramp: no}`(长波长信号是目标,绝不能当轨道误差扣掉)+ 出图侧重缠绕干涉图。正文写清"火山场景 deramp=no 是红线"的理由。

> **纪律**:每个新包的 `match` 关键词闭集不得与既有包重叠(`classify_text` 先到先得会引发抢占);提交前跑 `scenario_of()` 对六个包的关键词各测一遍。

## 3. 端到端剧本(写进 `skills/00-insar-agent/SKILL.md`)

每个剧本 = 用户一句话 → 工具调用序列 → 交付物清单。至少四个:

| 剧本 | 序列 |
|---|---|
| **同震形变**("查 2019 Ridgecrest 地震") | create_session → recommend_route → plan_run(scenario=quake) → execute → vision_qa → timeseries_point(震中) → report(full) → export(gtiff) → repro_bundle |
| **城市沉降监测**("监测某市地面沉降") | plan_run(scenario=subsidence) → execute → 分析 run(21 掩膜 → 24 统计 → 26 加速检测 → 27 外推) → figure_set 曲线 → report + kmz |
| **升降轨三维**("这个区域到底是垂直沉降还是水平滑移") | 两条主链 run(升/降) → 分析 run(20 登记两源 → 22 板块校正 → 23 分解 → 25 出图) → 垂直/东西向图 + 报告 |
| **深度解译交接**("帮我反演断层") | 主链 run → 分析 run(21 掩膜 → 28 bridge_gbis) → 如实说明边界 + GBIS 侧下一步 |

## 4. 涉及文件与测试

| 文件 | 动作 |
|---|---|
| `src/insar_agent/registry/capabilities.py` | 第 7 步数据面参数(§1.2) |
| `src/insar_agent/engines/mintpy.py` | render_cfg 参数化(§1.2) |
| `src/insar_agent/registry/scenario_packs/subsidence/{SKILL.md,overrides.yaml}` | 新建 |
| `src/insar_agent/registry/scenario_packs/volcano/{SKILL.md,overrides.yaml}` | 新建 |
| `pi-insar/skills/00-insar-agent/SKILL.md` | 四个剧本 |
| `tests/test_cfg_data_plane.py` | 逐字节回归 + processor 覆写穿透 |
| `tests/test_scenario_packs.py` | 若既有(先 `rg scenario tests/`):包数断言 4→6、关键词无抢占 |
| `pi-insar/test/skills.test.ts` | 若断言技能/场景包数量,同步 4→6 |

## 5. 验收

```powershell
.venv\Scripts\python.exe -m pytest tests/test_cfg_data_plane.py tests/test_scenario_packs.py -q
.venv\Scripts\python.exe -m pytest -q; cd pi-insar; npx vitest run
# 场景包冒烟:六个包全部加载、无 ScenarioPackWarning
.venv\Scripts\python.exe -c "from insar_agent.registry.scenarios import SCENARIOS; print(sorted(SCENARIOS))"
```

真实验收:pi 里说"我要监测城市地面沉降"→ 模型经 recommend/plan 选中 `subsidence` 场景 → 计划里 ramp=linear、periods=[1] 与包声明一致(查 `insar_run_status` 的 narrowed 解释)。

## 6. Git

```powershell
git add src/insar_agent/registry/capabilities.py src/insar_agent/engines/mintpy.py `
        src/insar_agent/registry/scenario_packs/subsidence src/insar_agent/registry/scenario_packs/volcano `
        pi-insar/skills/00-insar-agent/SKILL.md tests/test_cfg_data_plane.py tests/test_scenario_packs.py `
        pi-insar/test/skills.test.ts
git commit -m "feat(scenarios): 数据面参数化(8 种处理器布局)+ 沉降/火山场景包 + 四个端到端剧本

第 7 步数据面此前硬编码 HyP3,其他处理器产品全部进不了时序反演;
参数化并以逐字节回归守护默认行为。新增两个高频应用场景包(零代码,目录即包)。"
```

## 7. 完成标记

- [ ] 默认 cfg 渲染与参数化前逐字节相同(回归测试守护)
- [ ] processor/模式参数覆写正确穿透到 cfg
- [ ] 六个场景包全部加载、关键词无抢占、frontmatter 闭集通过
- [ ] 四个剧本写入操作技能并被 skills 测试覆盖
- [ ] pi 里自然语言触发 subsidence 场景,计划参数与包声明一致

→ 下一个文件:`40-phase16-docs-and-final.md`
