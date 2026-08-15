# Phase 11 · 科学能力扩展 II:校正链补齐(解缠误差 / 电离层 / 板块运动 / 不确定度)

> **一句话目标**:把 MintPy 已支持、注册表却没声明的校正能力补进主链——解缠误差改正、电离层改正、OPERA 对流层产品、板块运动改正、速度不确定度量化。
> **关键事实(已用本机 `E:\miniforge3\envs\insar\Lib\site-packages\mintpy\defaults\smallbaselineApp.cfg` 逐键核实)**:这些校正**本来就落在第 7/8/9 步的 `--start/--end` 区间内**,补齐 = 扩 `_CFG_TEMPLATE` + 加 `Param`/`Method` 声明,**不新增步骤、不新增工具**。

## 0. 核实过的配置键(照抄,不要凭记忆写)

```text
mintpy.unwrapError.method          = auto  #[bridging / phase_closure / bridging+phase_closure / no], auto for no
mintpy.ionosphericDelay.method     = auto  #[split_spectrum / no], auto for no
mintpy.troposphericDelay.method    = auto  #[pyaps / height_correlation / gacos / opera / no], auto for pyaps
mintpy.solidEarthTides             = auto  #[yes / no], auto for no
mintpy.timeFunc.uncertaintyQuantification = auto  #[residue, covariance, bootstrap], auto for residue
mintpy.timeFunc.bootstrapCount            = auto  #[int>1], auto for 400
```

smallbaselineApp 步骤序列(注释已写在 `engines/mintpy.py:24-27`):`correct_unwrap_error` 在 `load_data..invert_network` 之间(**第 7 步区间**);`correct_ionosphere`/`correct_troposphere`/`correct_SET` 在 `correct_LOD..reference_date` 之间(**第 8 步区间**);`uncertaintyQuantification` 属 `velocity`(**第 9 步区间**)。

## 1. 文件级改动

### 1.1 `src/insar_agent/registry/capabilities.py`(修改)

**第 7 步** `params` 追加:

```python
            # 解缠误差改正(C 新增):发生在 invert_network 之前,MintPy 上游默认 no。
            # bridging 适合被水体/低相干带分隔的连通域;phase_closure 用闭合环冗余,
            # 需要网络冗余度(pairs 数)支撑;两者可叠加(Yunjun et al. 2019 §4.2)
            "unwrap_error_method": Param(
                "no", kind="science", type="str",
                enum=("no", "bridging", "phase_closure", "bridging+phase_closure"),
                hint="解缠误差改正:no=不改正(上游默认);bridging=连通域桥接;"
                     "phase_closure=闭合环法(需网络冗余);可叠加"),
```

**第 8 步** `methods` 追加一个方法、`params` 追加一个参数:

```python
            Method("tropo_opera", "tropo_opera", "mintpy",
                   why="OPERA 对流层产品(需产品已在位)", requires_engines=("mintpy",),
                   extra="cfg: troposphericDelay.method=opera;无产品时运行期诚实失败"),
```

```python
            # 电离层改正(L 波段/长波长场景重要;C 波段短时序通常可忽略)。
            # 诚实声明:split_spectrum 需要 ISCE-2 stack 处理器产出的分频谱干涉对,
            # HyP3 云端路线没有这些输入 —— 选了它,MintPy 会在运行期以真实报错失败,
            # 由 triage 如实呈现;不做静默降级
            "iono_method": Param(
                "no", kind="science", type="str", enum=("no", "split_spectrum"),
                hint="电离层改正:split_spectrum 需 ISCE2 stack 分频谱产物"
                     "(HyP3 路线无此输入,勿选);L 波段(ALOS)建议开启"),
```

**第 9 步** `params` 追加:

```python
            # 速度不确定度(Phase 13 预测的输入):residue=残差传播(上游默认,最快);
            # covariance=时序协方差传播;bootstrap=自助抽样(最稳健,最慢,默认 400 次)
            "uncertainty": Param("residue", kind="science", type="str",
                                 enum=("residue", "covariance", "bootstrap"),
                                 hint="velocityStd 的估计方式;bootstrap 最稳健但慢"),
            "bootstrap_count": Param(400, kind="science", type="int", min=50, max=5000,
                                     hint="仅 uncertainty=bootstrap 时生效"),
```

**分析链第 22 步(速度场校正,Phase 10 已建)** `methods` 追加:

```python
            # 本机实测存在:mintpy.cli.plate_motion(ITRF2014-PMM 刚性板块运动改正)。
            # 用全球参考框架(如与 GNSS 对比)时必须做;局地相对形变可不做。
            # 双源(升降轨)时对 primary/secondary 各自按其几何改正
            Method("plate_motion_itrf", "plate_motion.py", "mintpy",
                   why="扣除 ITRF 刚性板块运动:长波长速度偏差的主要来源之一",
                   requires_engines=("mintpy",),
                   extra="需几何文件(geometryRadar.h5/geometryGeo.h5)在位"),
```

`plate` 参数已在 Phase 10 的第 22 步预声明,无需再加;本 Phase 只追加方法并让引擎消费该参数。

### 1.2 `src/insar_agent/engines/mintpy.py`(修改)

**位置 1** · `_CFG_TEMPLATE` 的 corrections 区(第 56-62 行附近)追加三行:

```python
mintpy.unwrapError.method           = {unwrap_error}
mintpy.ionosphericDelay.method      = {iono_method}
##---------velocity uncertainty:
mintpy.timeFunc.uncertaintyQuantification = {uncertainty}
mintpy.timeFunc.bootstrapCount            = {bootstrap_count}
```

**位置 2** · `render_cfg()`:

```python
    tropo = {"tropo_era5_pyaps": "pyaps", "tropo_gacos": "gacos",
             "tropo_height_corr": "height_correlation",
             "tropo_opera": "opera"}.get(m8, "pyaps")
```

并在 `_CFG_TEMPLATE.format(...)` 里补:

```python
        unwrap_error=p7.get("unwrap_error_method", "no"),
        iono_method=p8.get("iono_method", "no"),
        uncertainty=p9.get("uncertainty", "residue"),
        bootstrap_count=int(p9.get("bootstrap_count", 400)),
```

> **回退默认必须与注册表默认一致**(`no`/`residue`/400)——这是 `render_cfg` 的既有纪律(见该文件 133-141 行注释:绕过 planner 的调用不该拿到非默认值)。

### 1.3 `src/insar_agent/engines/mintpy_post.py`(修改,Phase 10 已建)

追加 `plate_motion_itrf` 分支(规范链约定,Phase 10 §2.3):读 `analysis/masked.h5`(存在 `masked_2.h5` 则双流各跑一次),
`python -m mintpy.cli.plate_motion --plate {plate} --velo {输入} -o analysis/corrected(_2).h5`,
几何文件按源 run 工作区探测(`mintpy/inputs/geometryRadar.h5` / `geometryGeo.h5`),找不到 → 真实报错,绝不跳过改正装作做了。
`plate` 为空而方法被选中 → 规划期由参数校验拒绝(hint 写明必填)。

## 2. 指纹与复用的连带影响(必须理解,不是坑是性质)

给第 7/8/9 步**新增参数**会改变 `default_params()`,因此**新计划**的 `eval_hash` 与旧 run 不同 → fork 旧 run 时这些步骤不再零重算复用。这是**正确行为**(参数面变了,复现语义就变了),不是回归。验收时如实预期:`insar_preview_change` 会显示这些步骤 stale。

## 3. 测试

1. **先找既有 cfg 测试**:`rg "troposphericDelay|_CFG_TEMPLATE|render_cfg" tests/` —— 有断言渲染内容的测试必须同步更新;
2. `tests/test_mintpy_cfg_corrections.py`(**新建**):

```python
def test_cfg_renders_correction_defaults():
    cfg = render_cfg({"chain": {}}, this_step=7, this_method="mintpy_sbas", this_params={})
    assert "mintpy.unwrapError.method           = no" in cfg
    assert "mintpy.ionosphericDelay.method      = no" in cfg
    assert "mintpy.timeFunc.uncertaintyQuantification = residue" in cfg

def test_cfg_renders_bridging_and_bootstrap():
    run = {"chain": {7: {"method": "mintpy_sbas",
                         "params": {"unwrap_error_method": "bridging"}},
                     9: {"method": "linear",
                         "params": {"uncertainty": "bootstrap", "bootstrap_count": 800}}}}
    cfg = render_cfg(run, this_step=7, this_method="mintpy_sbas", this_params={})
    assert "= bridging" in cfg and "= bootstrap" in cfg and "= 800" in cfg

def test_registry_rejects_hallucinated_correction_values():
    cap = REGISTRY[8]
    assert cap.validate_params({"iono_method": "magic"})  # 非闭集值必须被拒
```

3. 参数闭集经 `validate_params` 拒绝幻觉值(上面第三条)——这是 LLM 安全面,不可省。

## 4. 明确暂缓(诚实边界,写进文档不写进代码)

| 能力 | 本机工具 | 暂缓原因 |
|---|---|---|
| 闭合相位偏差改正(fading signal) | `closure_phase_bias.py` | 需缠绕相位栈输入与专门流程,HyP3 路线不具备;待 ISCE2 stack 路线落地后评估 |
| S1 A/B 距离偏差 | `s1ab_range_bias.py` | 仅长时序跨 S1A/B 混采场景需要;realtest(单月同震)不适用,无法真实验收 |
| 固体潮(Windows) | `solid_earth_tides.py` | 参数已有(`solid_earth_tides`),但 conda-forge pysolid 的 Fortran DLL 在 Windows 加载失败是**已记录的崩溃坑**(capabilities.py:356 注释)——默认保持 False,开启前须单独验证 |

## 5. 验收

```powershell
cd 'E:\01所有项目\06定职讲师\00insaragent'
.venv\Scripts\python.exe -m pytest tests/test_mintpy_cfg_corrections.py -q
.venv\Scripts\python.exe -m pytest -q
```

真实验收(⚠️重型,须用户明确同意):对 realtest 数据 fork 一个 run,第 7 步改 `unwrap_error_method=bridging`,真实重跑第 7-9 步,期望:cfg 里出现 `bridging`、run done、`velocityStd` 数据集存在(`uncertainty=residue` 默认)。

## 6. Git

```powershell
git add src/insar_agent/registry/capabilities.py src/insar_agent/engines/mintpy.py `
        src/insar_agent/engines/mintpy_post.py tests/test_mintpy_cfg_corrections.py
git commit -m "feat(registry): 补齐校正链 —— 解缠误差/电离层/OPERA对流层/板块运动/速度不确定度

MintPy 上游早已支持且键名逐一核实,注册表未声明导致 LLM 无法选用。
全部走既有 cfg 渲染与参数闭集校验,不新增步骤;电离层如实标注 HyP3 路线不可用。"
```

## 7. 完成标记

- [ ] cfg 三个新键按默认渲染,覆写值正确穿透
- [ ] 幻觉参数值被 `validate_params` 拒绝
- [ ] `plate_motion_itrf` 在分析链 22 步可选可跑
- [ ] 全量 pytest 全绿;既有 cfg 断言已同步
- [ ] 指纹连带影响已在 PR/提交说明里写明

→ 下一个文件:`32-phase12-figure-system.md`
