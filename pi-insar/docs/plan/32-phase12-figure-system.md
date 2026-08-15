# Phase 12 · 科学能力扩展 III:出图体系(图集 / 网络图 / 相干矩阵 / 时序曲线 / 剖面图 / KMZ)

> **一句话目标**:从"只有一张速度场图"升级为**完整的科学出图体系**——中间过程质检图、网络诊断图、点位时序曲线、剖面图、Google Earth 交付,全部来自真实产物。
> **两条腿**:核心第 10 步扩 `figure_set` 参数(自建 matplotlib,继承 600 dpi/色盲安全/sidecar 纪律);分析链新增第 25 步"分析出图"(MintPy CLI:view/plot_transection/save_kmz)。

## 0. 设计取舍(为什么是两条腿)

| 腿 | 实现 | 适用 |
|---|---|---|
| 第 10 步 `figure_set` 参数 | 扩 `engines/figures.py`(纯 Python + h5py + matplotlib) | run 内标准图集:速度/相干/掩膜/不确定度/网络图 —— 继承本项目的期刊级排版、分档尺寸、sidecar 元数据、图注体系 |
| 分析链第 25 步 | `engines/mintpy_post.py` 调 MintPy CLI | 任意数据集浏览图(`view.py`)、剖面图(`plot_transection.py`)、KMZ(`save_kmz.py`/`save_kmz_timeseries.py`) —— 不重造 MintPy 已打磨的可视化 |

**为什么不给第 10 步加"出网络图"这类新方法**:一步只能选一个方法,把图种做成方法会变成"要网络图就丢速度图"。图种是**参数**(`figure_set` 列表),不是方法。

## 1. 文件级改动

### 1.1 `src/insar_agent/registry/capabilities.py`(修改)

**第 10 步** `params` 追加:

```python
            # 图集清单(presentation 参数,进指纹但不改变科学语义):
            # velocity 主图之外,按需渲染质检与诊断图。全部读真实 h5,缺数据的图
            # 如实跳过并在 sidecar 里记录跳过原因,绝不画占位图
            "figure_set": Param(["velocity"], kind="presentation", type="list",
                                hint="可选项:velocity / coherence / mask / velocity_std / "
                                     "network / coherence_matrix / displacement_epochs / "
                                     "points_timeseries;缺输入的项如实跳过"),
            # points_timeseries 的点位(science:决定画哪些点)
            "points_lalo": Param([], kind="science", type="list",
                                 hint='如 [[35.75,-117.60],[35.60,-117.50]]'),
```

**分析链追加第 25 步**(`group="analysis"`,`deps=(24,)`):

```python
    Capability(
        id=25, name="分析出图", phase="分析", group="analysis", deps=(24,),
        methods=(
            Method("view_snapshot", "view.py", "mintpy",
                   why="任意 h5 数据集的标准浏览图(MintPy 官方渲染)", recommend=True,
                   requires_engines=("mintpy",)),
            Method("transection_figure", "plot_transection.py", "mintpy",
                   why="剖面图:形变梯度可视化", requires_engines=("mintpy",)),
            Method("kmz", "save_kmz.py", "mintpy",
                   why="Google Earth 交付(汇报/共享)", requires_engines=("mintpy",)),
            Method("kmz_timeseries", "save_kmz_timeseries.py", "mintpy",
                   why="交互式时序 KMZ(点开看曲线)", requires_engines=("mintpy",),
                   extra="需地理编码时序;文件较大"),
            Method("passthrough", "passthrough", "-", why="本次分析不出图"),
        ),
        default_method="passthrough",
        params={
            "input": Param("analysis/decomposed.h5", kind="science", type="str"),
            "dataset": Param("", kind="science", type="str",
                             hint="h5 内数据集名,如 velocity/vertical/east;空=默认"),
            "start_lalo": Param("", kind="science", type="str", hint="transection 起点"),
            "end_lalo": Param("", kind="science", type="str", hint="transection 终点"),
            "dpi": Param(300, kind="presentation", type="int", min=72, max=1200),
            "cmap": Param("", kind="presentation", type="str",
                          hint="空=按产物类型路由(与第 10 步同一色带纪律)"),
        },
        artifacts=(ArtifactSpec("analysis_figures", ("analysis/figures",),
                                kind="FIGURE", policy="stat"),),
        inputs=("measure",),
        run_ok=(RunOkCheck("exit_code", equals=0),
                RunOkCheck("artifact_exists", id="analysis_figures")),
        timeouts=Timeouts(idle=600, total=1800), disk=DiskEstimate("0.3"),
        io="light", replay="safe",
    ),
```

### 1.2 `src/insar_agent/engines/figures.py`(修改 —— 本 Phase 的核心工作量)

现状:读 `velocity.h5` 渲染主图,已有 `pub_cmap`(Crameri 色盲安全路由)、`save_tiers`(三档尺寸)、`write_sidecar`(元数据)。

**扩展**:按 `figure_set` 渲染多图,每张图 = 一个渲染函数,全部**只读真实 h5**:

| 图种 | 数据源(run 工作区) | 内容 | 缺数据时 |
|---|---|---|---|
| `velocity` | `mintpy/velocity.h5` | 既有主图(不动) | 既有失败语义 |
| `coherence` | `mintpy/temporalCoherence.h5` | 时相相干图(batlow 色带) | 跳过+记录 |
| `mask` | `mintpy/maskTempCoh.h5` | 可信像元掩膜 | 跳过+记录 |
| `velocity_std` | `velocity.h5` 的 `velocityStd` 数据集 | 不确定度图(Phase 11 产物) | 跳过+记录 |
| `network` | `mintpy/inputs/ifgramStack.h5` 的 `date12List` + `bperp` 属性 | 时空基线网络图(散点+连线,纯 matplotlib) | 跳过+记录 |
| `coherence_matrix` | ifgramStack 的平均相干 | 相干矩阵热图 | 跳过+记录 |
| `displacement_epochs` | `timeseries*.h5` | 首/中/末历元累计位移三联图 | 跳过+记录 |
| `points_timeseries` | `timeseries*.h5` + `points_lalo` | 点位时序曲线(误差棒用 velocityStd 若在) | 跳过+记录 |

**硬纪律**:
1. **绝不画占位图**——缺数据集就跳过,并把 `{"skipped": "...", "reason": "..."}` 写进 sidecar;`run_ok` 只要求 `figures` 目录存在且**申请的图集中至少主图成功**;
2. 每张图走既有 `save_tiers` + `write_sidecar`(图注体系 `report/captions.py` 靠 sidecar 取事实,新图种自动获得图注能力);
3. 色带走既有 `_CMAP_ROUTE` 路由(速度=vik、相干=batlow、缠绕相位=romaO),新图种在路由表里登记,不得随手 `jet`;
4. 网络图的日期/基线**读 h5 真实属性**,不从文件名猜。

### 1.3 `src/insar_agent/engines/mintpy_post.py`(修改)

追加 `view_snapshot` / `transection_figure` / `kmz` / `kmz_timeseries` 分支,输出统一进 `analysis/figures/`:

```python
    if method == "view_snapshot":
        argv = [py, "-u", "-m", "mintpy.cli.view", str(inp), *(dset and [dset] or []),
                "--dpi", str(params.get("dpi", 300)), "--nodisplay",
                "-o", str(out_figs / f"view_{stem}.png")]
```

> `--nodisplay` 是无头环境的关键旗标(view/plot_transection 都要带),漏了会在无显示环境挂住——先 `--help` 核实旗标名再落盘。

### 1.4 `pi-insar` 扩展侧(小改)

- `insar_view_figure` 已按 `/api/figures` 列图,分析图落 `analysis/figures/` 后**核实后端 `/api/figures` 的扫描范围是否覆盖它**(bridge/figures 端点按产物账本列,分析步产物在账本里 → 应自动可见;若按目录扫描则需检查);
- `APPEND_SYSTEM.md` 补:"需要网络图/相干矩阵/时序曲线 → 第 10 步 `figure_set` 参数(fork 或 apply_change);需要剖面图/KMZ → 分析 run 第 25 步"。

## 2. 测试

1. `tests/test_figures_set.py`(**新建**,跑在模拟 run 的真实落盘产物上):
   - `figure_set=["velocity"]` 与既有行为完全一致(回归保护);
   - 申请缺数据的图种 → 跳过且 sidecar 记录原因,run_ok 仍真;
   - 每张成功图都有三档尺寸 + sidecar;
2. Phase 10 的分析规划断言更新:`[20,21,22,23,24]` → `[20,21,22,23,24,25]`;
3. `pi-insar/test/render.test.ts` 若断言步数/图数,同步核对。

## 3. 验收

```powershell
.venv\Scripts\python.exe -m pytest tests/test_figures_set.py -q; .venv\Scripts\python.exe -m pytest -q
cd pi-insar; npx tsc --noEmit; npx vitest run
```

真实验收(读回为主,出图为轻计算,无需批准):对 realtest 真实 run fork 第 10 步 `figure_set=["velocity","coherence","network","points_timeseries"]`、`points_lalo=[[35.75,-117.60]]`,重跑第 10 步(秒级),期望:4 张真实图 + sidecar 落盘,`insar_view_figure` 能列出并内联显示;网络图上 11 对干涉对连线与 provenance 的 pairs 一致。

## 4. Git

```powershell
git add src/insar_agent/registry/capabilities.py src/insar_agent/engines/figures.py `
        src/insar_agent/engines/mintpy_post.py pi-insar/APPEND_SYSTEM.md `
        tests/test_figures_set.py tests/test_registry_groups.py
git commit -m "feat(figures): 图集体系 —— 相干/掩膜/不确定度/网络/时序曲线 + 分析链出图步

出图此前只有速度场主图,质检图与诊断图全靠脑补。figure_set 参数化图集
(缺数据如实跳过绝不画占位),分析链 25 步接 MintPy view/transection/KMZ。"
```

## 5. 完成标记

- [ ] `figure_set=["velocity"]` 与旧行为逐字节等价(回归)
- [ ] 新图种全部有三档尺寸 + sidecar,图注体系可用
- [ ] 缺数据的图种跳过并记录,绝无占位图
- [ ] 分析链 25 步 view/transection/KMZ 真实可跑
- [ ] realtest 上 4 图真实落盘并在 pi TUI 内联可见

→ 下一个文件:`33-phase13-analysis-and-prediction.md`
