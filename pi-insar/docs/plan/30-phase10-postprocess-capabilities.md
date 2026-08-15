# Phase 10 · 科学能力扩展 I:后处理与分析 run(掩膜/子集/统计/剖面/**升降轨分解**)

> **一句话目标**:让 Agent 除了"跑出速度场",还能**对结果做科学后处理**——掩膜、子集、栅格代数、空间/时间统计、剖面,以及 InSAR 解译里最重要的一步:**升降轨分解出垂直与东西向形变**。
> **性质**:这是第一个真正扩展科学能力的 Phase。它同时落地 `06-completeness-verdict.md` §4 的三个设计决策。
> **新增工具数:0**。全部通过注册表声明,由既有 `insar_plan_run` / `insar_apply_change` / `insar_execute_run` 驱动。

## 0. 开工前必做的核实(5 分钟,别跳过)

```powershell
cd 'E:\01所有项目\06定职讲师\00insaragent'
# 1) 确认本机 MintPy 后处理 CLI 真的在
& E:\miniforge3\envs\insar\python.exe -m mintpy.cli.asc_desc2horz_vert --help
& E:\miniforge3\envs\insar\python.exe -m mintpy.cli.mask --help
& E:\miniforge3\envs\insar\python.exe -m mintpy.cli.spatial_average --help
# 2) 确认 run 工作区语义(分析 run 要按相对路径找源产物)
python -c "import sqlite3,os;d=sqlite3.connect(os.path.expandvars(r'%INSAR_HOME%\insar.db'));print(d.execute('select run_id,workspace from runs limit 5').fetchall())"
# 3) 确认规划器/执行器的注册表驱动点没变
#    planner/plan.py:51  for sid in sorted(registry)
#    runtime/executor.py:103  cap = ctx.registry[step_id]
#    loop/driver.py:222  self.registry = registry or REGISTRY
```

三条都对上再动手。若 `workspace` 是**按会话**而非按 run 建的,分析 run 用相对路径找源产物就是安全的;若是按 run 建的,必须改用"源 run 工作区 + 相对路径"两段式参数(下文 §2.3 有说明)。

## 1. 三个设计决策的落地

### D1:`group` 字段 —— 让可选步骤不污染核心 run

**问题**:规划器给注册表里每一个 capability 都排一步。直接加"剖面"步会让每个 run 都多出一步。

**做法**:`Capability` 加 `group` 字段,规划器按 group 过滤。核心 11 步用默认值 `"core"`,**现有声明一行都不用改**,现有"11 步"断言全部继续通过。

### D2:分析 run —— 跨 run 分析怎么落账

升降轨分解需要**两个** run 的产物,不可能是某个 run 内部的一步。做法:用 `groups=("analysis",)` 规划一个独立的分析 run,**源 run 的产物路径作为 science 参数进入指纹**——源变了 `args_hash` 就变,失效传播照常正确。

这个做法完整复用五阶段执行器、产物发现、run_ok 双判定、provenance、证据阶梯,**不改数据库结构**。

### D3:相对路径作为 science 参数

已有先例:第 3 步 stripmap 链的 `reference_image` / `dem_path` 就是相对路径 science 参数(`capabilities.py:107-119`)。分析 run 沿用同一形态,不发明新机制。

## 2. 文件级改动

### 2.1 `src/insar_agent/registry/model.py`(修改 · **第一个改**)

**位置**:`Capability` 数据类,`phase: str = ""` 之后。

```python
    phase: str = ""  # 轨迹 phase 名(§7.2 面板7)
    group: str = "core"  # core = 主处理链(默认);analysis = 按需规划的分析步骤
```

**理由必须写进 docstring**:分组只影响**规划时是否入选**,不影响执行、账本、指纹的任何语义 —— 执行器始终能按 step_id 在全量注册表里查到声明。

### 2.2 `src/insar_agent/planner/plan.py`(修改)

**位置 1** · `_plan_methods` 签名(约 43 行):

```python
def _plan_methods(registry: dict[int, Capability], probe: ProbeResult, *,
                  scenario: Scenario | None, allow_simulated: bool,
                  overrides: dict[int, dict] | None = None,
                  groups: tuple[str, ...] = ("core",)):
```

**位置 2** · 循环体开头(约 51 行,`for sid in sorted(registry):` 之后第一行):

```python
    for sid in sorted(registry):
        cap = registry[sid]
        if cap.group not in groups:
            continue  # 分组过滤:核心 run 不排分析步,分析 run 不重跑主链
```

**位置 3** · `make_plan` 签名与转发:加 `groups: tuple[str, ...] = ("core",)`,原样传给 `_plan_methods`。

**位置 4** · `make_plan` 落库时把 groups 写进 `intent`,便于 `fork_run` 与审计还原:

```python
    store.create_run(run_id, session_id, workspace=workspace,
                     intent={**(intent or {}), "groups": list(groups)}, ...)
```

> `fork_run` 不需要改:它遍历的是**父 run 已落库的步骤**,与 group 无关。

### 2.3 `src/insar_agent/registry/capabilities.py`(修改 · 追加)

在 `PIPELINE` 之后追加分析链。**id 从 20 起,12-19 留给核心链未来成长**。

```python
ANALYSIS: tuple[Capability, ...] = (
    Capability(
        id=20, name="分析输入", phase="分析", group="analysis", deps=(),
        methods=(Method("register_sources", "register_sources", "-",
                        why="登记并校验待分析的源产物(不复制、不改写)", recommend=True),),
        default_method="register_sources",
        params={
            # 相对 run 工作区的路径(与第 3 步 stripmap 的路径参数同形态,进指纹)
            "primary": Param("mintpy/velocity.h5", kind="science", type="str",
                             hint="主源产物路径(升轨速度场 / 待分析时序)"),
            "secondary": Param("", kind="science", type="str",
                               hint="次源产物路径(降轨速度场);单源分析留空"),
            "primary_run": Param("", kind="science", type="str",
                                 hint="主源 run_id,仅作溯源记录"),
            "secondary_run": Param("", kind="science", type="str", hint="次源 run_id"),
        },
        artifacts=(
            # register_sources 把 params 路径物化为规范链首(NTFS 硬链接优先、拷贝兜底,
            # 数据是真实的,只是换了规范位置)—— 后续步骤全部读固定的规范路径,零决策
            ArtifactSpec("src_primary", ("analysis/source.h5",), kind="DATA", policy="stat"),
            ArtifactSpec("src_secondary", ("analysis/source_2.h5",), kind="DATA",
                         policy="stat", required=False),
        ),
        run_ok=(RunOkCheck("exit_code", equals=0),
                RunOkCheck("artifact_exists", id="src_primary")),
        timeouts=Timeouts(idle=120, total=600), disk=DiskEstimate("0.01"),
        io="light", replay="safe",
    ),
    Capability(
        id=21, name="掩膜子集", phase="分析", group="analysis", deps=(20,),
        methods=(
            Method("mask_by_coherence", "mask.py --mask maskTempCoh.h5", "mintpy",
                   why="按时相相干掩膜剔除不可信像元", recommend=True,
                   requires_engines=("mintpy",)),
            Method("subset_lalo", "subset.py --lat/--lon", "mintpy",
                   why="裁到研究区,后续统计与出图都更快", requires_engines=("mintpy",)),
            Method("passthrough", "passthrough", "-", why="不做掩膜/裁剪,直接透传上游产物"),
        ),
        default_method="passthrough",
        params={
            "mask_file": Param("mintpy/maskTempCoh.h5", kind="science", type="str"),
            "subset_lat": Param("", kind="science", type="str", hint="如 35.6:36.0,留空=不裁"),
            "subset_lon": Param("", kind="science", type="str", hint="如 -117.9:-117.2"),
        },
        artifacts=(ArtifactSpec("masked", ("analysis/masked.h5",), kind="VELOCITY",
                                layout="mintpy_h5", policy="content"),),
        inputs=("src_primary",),
        run_ok=(RunOkCheck("exit_code", equals=0), RunOkCheck("artifact_exists", id="masked")),
        timeouts=Timeouts(idle=600, total=1800), disk=DiskEstimate("1"), io="medium",
    ),
    Capability(
        id=22, name="速度场校正", phase="分析", group="analysis", deps=(21,),
        # 本 Phase 只声明 passthrough 占位;Phase 11 在此追加 plate_motion_itrf 方法
        # (ITRF 刚性板块运动改正 —— 分解前必须先各自改正,校正步因此排在分解之前)
        methods=(
            Method("passthrough", "passthrough", "-",
                   why="不做速度场级校正,规范链透传", recommend=True),
        ),
        default_method="passthrough",
        params={
            "plate": Param("", kind="science", type="str",
                           hint="板块名(ITRF2014-PMM 命名,如 NorthAmerica);Phase 11 启用"),
        },
        artifacts=(
            ArtifactSpec("corrected", ("analysis/corrected.h5",), kind="DATA", policy="stat"),
            ArtifactSpec("corrected_2", ("analysis/corrected_2.h5",), kind="DATA",
                         policy="stat", required=False),
        ),
        inputs=("masked",),
        run_ok=(RunOkCheck("exit_code", equals=0), RunOkCheck("artifact_exists", id="corrected")),
        timeouts=Timeouts(idle=600, total=1800), disk=DiskEstimate("1"), io="light",
    ),
    Capability(
        id=23, name="几何分解", phase="分析", group="analysis", deps=(22,),
        methods=(
            # 本机实测存在:mintpy.cli.asc_desc2horz_vert
            Method("asc_desc_horz_vert", "asc_desc2horz_vert.py", "mintpy",
                   why="升降轨 LOS 分解为垂直 + 水平(默认东西向)—— InSAR 解译标准动作",
                   recommend=True, requires_engines=("mintpy",),
                   extra="需双源(analysis/corrected.h5 + corrected_2.h5),"
                        "且两源已地理编码到同一分辨率与范围"),
            Method("raster_diff", "diff.py", "mintpy",
                   why="两期/两源相减(变化量、与参考解的差异)", requires_engines=("mintpy",)),
            Method("passthrough", "passthrough", "-", why="单源分析,不做分解"),
        ),
        default_method="passthrough",
        params={
            # 水平方位角:MintPy 约定自北起、逆时针为正;-90 = 东向(上游默认)
            "horz_az_angle": Param(-90.0, kind="science", min=-180, max=180,
                                   hint="关心的水平方向方位角(度),自北起逆时针为正;"
                                        "-90=东西向(MintPy 上游默认);跨断层可设为断层走向"),
            "use_geometry_files": Param(False, kind="science", type="bool",
                                        hint="用逐像元入射/方位角替代常量元数据"),
        },
        artifacts=(ArtifactSpec("decomposed", ("analysis/decomposed.h5",),
                                kind="VELOCITY", layout="mintpy_h5", policy="content"),),
        inputs=("corrected",),
        run_ok=(RunOkCheck("exit_code", equals=0),
                RunOkCheck("artifact_exists", id="decomposed"),
                RunOkCheck("not_all_nan", id="decomposed")),
        timeouts=Timeouts(idle=900, total=3600), disk=DiskEstimate("2"),
        cpu=4, mem_gb=8, io="medium",
    ),
    Capability(
        id=24, name="统计剖面", phase="分析", group="analysis", deps=(23,),
        methods=(
            Method("spatial_average", "spatial_average.py", "mintpy",
                   why="区域平均(沉降漏斗强度、参考区稳定性)", requires_engines=("mintpy",)),
            Method("temporal_average", "temporal_average.py", "mintpy",
                   why="时段平均(季节项、年际对比)", requires_engines=("mintpy",)),
            Method("transection", "plot_transection.py", "mintpy",
                   why="剖面:跨断层/跨漏斗的形变梯度", recommend=True,
                   requires_engines=("mintpy",)),
            Method("timeseries_rms", "timeseries_rms.py", "mintpy",
                   why="残差 RMS:噪声水平与参考日期选择依据", requires_engines=("mintpy",)),
        ),
        default_method="transection",
        params={
            "start_lalo": Param("", kind="science", type="str", hint="剖面起点 lat,lon"),
            "end_lalo": Param("", kind="science", type="str", hint="剖面终点 lat,lon"),
            "aoi_lalo": Param("", kind="science", type="str",
                              hint="统计区 lat0:lat1,lon0:lon1"),
            "dataset": Param("", kind="science", type="str",
                             hint="h5 内数据集名(分解产物用 vertical/east);空=默认"),
        },
        artifacts=(ArtifactSpec("measure", ("analysis/measure.json", "analysis/transect.txt"),
                                kind="REPORT", policy="content"),),
        inputs=("decomposed",),
        run_ok=(RunOkCheck("exit_code", equals=0), RunOkCheck("artifact_exists", id="measure")),
        timeouts=Timeouts(idle=600, total=1800), disk=DiskEstimate("0.2"),
        io="light", replay="safe",
    ),
)

REGISTRY: dict[int, Capability] = {c.id: c for c in (*PIPELINE, *ANALYSIS)}
```

> **规范路径链(canonical chain)**:20 物化 `analysis/source.h5`(双源加 `source_2.h5`)→ 21 产 `masked.h5` → 22 产 `corrected.h5` → 23 产 `decomposed.h5` → 24 产 `measure.json`。每步引擎**只读上一步的规范路径**(双源步骤看 `*_2.h5` 是否存在决定单/双流),`passthrough` 负责把规范路径物化下去 —— 零决策、全确定。
> 出图步(id=25)在 **Phase 12** 追加;变化检测/预测(26/27)在 **Phase 13**;反演桥(28)在 **Phase 14**。本 Phase 先把数据面打通。

### 2.4 `src/insar_agent/engines/mintpy_post.py`(**新建**)

**用途**:MintPy 后处理 CLI 的薄封装。与 `engines/mintpy.py` 分开,因为后者专做 `smallbaselineApp --start/--end` 分段,语义完全不同。

**结构**:

```python
"""MintPy 后处理薄封装:独立 CLI 子命令(零决策)。

与 engines/mintpy.py 的区别:那个跑 smallbaselineApp 的分段区间,这个跑
asc_desc2horz_vert / mask / subset / spatial_average / plot_transection 等
独立命令。引擎 Python 解析复用 mintpy.engine_python()(同一真值来源)。

纪律:本模块只拼 argv,不做任何方法/参数选择;路径一律相对 workspace 解析,
绝不接受绝对路径逃逸出工作区(路径面防御,与 export_router 同款)。
"""

from __future__ import annotations
from pathlib import Path
from typing import Any

from insar_agent.engines.mintpy import engine_python
from insar_agent.registry.model import Capability
from insar_agent.runtime.jobs import CommandPlan

_OUT_DIR = "analysis"

def _safe_rel(workspace: Path, rel: str, label: str) -> Path:
    """相对路径解析 + 逃逸防御:结果必须仍在 workspace 内。"""
    target = (workspace / rel).resolve()
    if not str(target).startswith(str(workspace.resolve())):
        raise ValueError(f"{label} 逃逸出工作区:{rel}")
    return target

def build(*, cap: Capability, method: str, params: dict[str, Any], run: dict,
          workspace: Path) -> CommandPlan:
    py = engine_python()
    out = workspace / _OUT_DIR
    if method == "asc_desc_horz_vert":
        # 规范链约定:输入永远是上一步(22)的规范产物,不接受任意路径
        primary = workspace / _OUT_DIR / "corrected.h5"
        secondary = workspace / _OUT_DIR / "corrected_2.h5"
        if not secondary.exists():
            raise ValueError("升降轨分解需要双源:analysis/corrected_2.h5 不存在"
                             "(第 20 步 register_sources 是否给了 secondary?)")
        argv = [py, "-u", "-m", "mintpy.cli.asc_desc2horz_vert",
                str(primary), str(secondary),
                "--az", str(params.get("horz_az_angle", -90.0)),
                "-o", str(out / "decomposed.h5")]
    elif method == "mask_by_coherence":
        ...
    elif method == "transection":
        ...
    else:
        raise ToolMissing(f"mintpy_post 无此方法:{method}")
    return CommandPlan(
        argv=argv, cwd=str(workspace),
        env={"HDF5_USE_FILE_LOCKING": "FALSE", "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
        files={},                      # 后处理命令不需要渲染配置文件
        shell_line=" ".join(...),      # 等价裸命令,进 run.sh
    )
```

**必须遵守的三条**(与既有引擎一致):
1. `env` 里 `PYTHONUTF8=1` + `PYTHONIOENCODING=utf-8` —— 中文 Windows 下 MintPy 不指定编码会崩(`engines/mintpy.py:162-163` 的既有教训);
2. `shell_line` 必须是**真正等价**的裸命令,它会进 `run.sh` 与复现包;
3. 输出目录 `analysis/` 由 `CommandPlan` 执行前创建(参照 `engines/figures.py` 的处理),不要指望 MintPy 自建。

### 2.5 `src/insar_agent/engines/passthrough.py`(**新建**)

`passthrough` = **规范路径物化**:校验本步的规范输入存在,然后把它物化为本步的规范输出——NTFS 硬链接优先(`os.link`,同卷零拷贝),失败回退 `shutil.copy2`;同时写 `<输出>.json` sidecar 记录来源路径与 sha256。数据是真实的,只是落到规范位置,供下一步零决策读取。

`register_sources`(第 20 步)是同一构建器的双源形态:把 `params.primary`(与非空的 `params.secondary`)物化为 `analysis/source.h5`(与 `source_2.h5`);源文件不存在 → 真实报错,绝不静默。

> **红线**:绝不路由到 `simulate` —— 那会往真实 run 里注入合成产物。`engines/__init__.py:24-28` 的注释已经把这条写死。物化用硬链接/拷贝真实文件,与"伪造产物"有本质区别。

### 2.6 `src/insar_agent/engines/__init__.py`(修改)

在方法级路由区(约 34-44 行)追加:

```python
    if method_id in ("passthrough", "register_sources"):
        from insar_agent.engines import passthrough
        return passthrough.build    # 规范路径物化(register_sources 是其双源形态)
    if method_id in ("asc_desc_horz_vert", "raster_diff", "mask_by_coherence",
                     "subset_lalo", "spatial_average", "temporal_average",
                     "transection", "timeseries_rms"):
        from insar_agent.engines import mintpy_post
        return mintpy_post.build
```

> 注意顺序:方法级路由在引擎级路由**之前**,否则 `engine="mintpy"` 会先命中 `mintpy.build`(跑 smallbaselineApp),整个分析步就跑错命令。

### 2.7 `src/insar_agent/api/app.py`(修改)

`POST /api/turn` 的请求体加可选字段 `pipeline: "core" | "analysis"`(默认 `core`),转发到 driver → `make_plan(groups=...)`。

**核实点**:`loop/driver.py:626` 是 `make_plan` 的调用处,把 groups 从 turn 请求穿到这里。若 driver 的 turn 签名较深,允许把 groups 放进 `intent` 由 driver 读取——**但必须在一处集中转换,不要两处各读一次**。

### 2.8 `pi-insar/src/tools.ts`(修改)

`insar_plan_run` 增加一个参数(**不新增工具**):

```ts
    pipeline: Type.Optional(
      StringEnum(["core", "analysis"],
        "core = the 11-step processing chain (default). " +
        "analysis = a post-processing run over products of existing runs " +
        "(masking, subsetting, ascending/descending decomposition, statistics, profiles). " +
        "For analysis you must also give the source product paths via params_json."),
    ),
```

`APPEND_SYSTEM.md` 补:

```markdown
- 需要"垂直/东西向形变""剖面""区域统计""按相干掩膜" → insar_plan_run pipeline=analysis,
  在 params_json 里给源产物路径;然后 insar_execute_run。
- 升降轨分解需要两个已完成的 run(一升一降),且两者都必须已地理编码到同一分辨率与范围。
```

## 3. 测试

### 3.1 `tests/test_registry_groups.py`(**新建**)

```python
def test_core_plan_still_has_exactly_11_steps():
    """分组过滤的核心保证:加了分析能力后,核心 run 一步都不多。"""
    choices, problems = _plan_methods(REGISTRY, probe, scenario=None, allow_simulated=True)
    assert sorted(choices) == list(range(1, 12))

def test_analysis_plan_selects_only_analysis_steps():
    choices, _ = _plan_methods(REGISTRY, probe, scenario=None, allow_simulated=True,
                               groups=("analysis",))
    assert sorted(choices) == [20, 21, 22, 23, 24]   # Phase 12/13/14 依次扩到 25/26-27/28

def test_analysis_params_enter_the_fingerprint():
    """源产物路径变了,eval_hash 必须变 —— 否则失效传播是假的。"""
    h1 = compute_step_hashes(REGISTRY[20], "register_sources", {"primary": "a.h5"}, [], {})
    h2 = compute_step_hashes(REGISTRY[20], "register_sources", {"primary": "b.h5"}, [], {})
    assert h1["eval_hash"] != h2["eval_hash"]
```

### 3.2 既有测试的连带影响(**必查**)

| 断言 | 是否受影响 | 处理 |
|---|---|---|
| `monitor.steps` 长度 = 11 | ❌ 不受影响(核心 run 仍 11 步) | 无需改 |
| 技能数 = 11 | ❌ 不受影响 | 无需改 |
| `/api/registry` 返回步数 | ✅ **11 → 16**(20-24 加入) | 更新断言,或让端点按 group 分组返回 |
| `insar_capabilities` 工具输出 | ✅ 会多出分析步 | 更新期望;**建议按 group 分节渲染** |

## 4. 验收

```powershell
cd 'E:\01所有项目\06定职讲师\00insaragent'
.venv\Scripts\python.exe -m pytest tests/test_registry_groups.py -q
.venv\Scripts\python.exe -m pytest -q            # 全量必须仍全绿
cd pi-insar; npx tsc --noEmit; npx vitest run
```

**真实数据验收**(核心,不可用模拟顶替):

Ridgecrest 的 realtest 只有单轨,无法做真正的升降轨分解。因此本 Phase 的真实验收分两级:

1. **必做**:掩膜 + 子集 + 剖面 + 空间统计 在 realtest 的真实 `velocity.h5` 上跑通,产物真实存在且 `not_all_nan` 通过;
2. **升降轨分解**:若暂无降轨数据,允许**如实标注为未验收**,在 `41-appendix-final-checklist.md` 里记 PENDING,并写明"待补一景降轨数据后验收"。
   **绝不允许**用同一景数据伪造成"降轨"来让测试变绿——那正是本项目最禁止的行为。

## 5. Git

```powershell
git add src/insar_agent/registry/model.py src/insar_agent/registry/capabilities.py `
        src/insar_agent/planner/plan.py src/insar_agent/engines/mintpy_post.py `
        src/insar_agent/engines/passthrough.py src/insar_agent/engines/__init__.py `
        src/insar_agent/api/app.py pi-insar/src/tools.ts pi-insar/APPEND_SYSTEM.md `
        tests/test_registry_groups.py
git commit -m "feat(registry): add analysis capability group with post-processing chain

本机 MintPy 早已具备掩膜/子集/统计/剖面/升降轨分解,注册表却一个都没声明,
科学后处理完全够不着。加 group 字段做规划过滤(核心 run 仍恰好 11 步),
新增 20-23 分析链与 mintpy_post 引擎,源产物路径进指纹以保证失效传播正确。"
```

## 6. 完成标记

- [ ] `Capability.group` 落地,核心 run 仍**恰好 11 步**(断言守护)
- [ ] 分析 run 能规划出 20-24 并真实执行(规范路径链全通)
- [ ] 源产物路径进 `eval_hash`(测试守护)
- [ ] 方法级路由在引擎级路由之前(分析步不会误跑 smallbaselineApp)
- [ ] 真实 `velocity.h5` 上跑通掩膜/子集/剖面/统计
- [ ] 升降轨分解:已验收 **或** 如实记 PENDING(禁止伪造降轨数据)
- [ ] 全量 pytest + vitest 仍全绿

→ 下一个文件:`31-phase11-correction-capabilities.md`
