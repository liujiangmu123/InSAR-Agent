---
name: 25-analysis-figures
description: 当规划或诊断分析链第 25 步分析出图(MintPy view/剖面图/KMZ,或默认不出图)时使用:默认 passthrough;本步不是核心第 10 步 figure_journal,不要把跳过标记当成期刊图。
capability: 25
version: "1.0.0"
applies_to: all
---

# 分析出图(view / 剖面图 / KMZ / 透传)

第 25 步为分析产物出浏览图或地球浏览器件。方法 id:`view_snapshot`(mintpy
`view.py`)、`transection_figure`(`plot_transection.py`)、`kmz`(`save_kmz.py`)、
`kmz_timeseries`(`save_kmz_timeseries.py`)、`passthrough`(默认、推荐)。
**`default_method=passthrough`**:写出 `analysis/figures/passthrough.json`
(`reason: 本次分析不出图`),**不生成 png/kmz**。产物 `analysis_figures`
(`analysis/figures`,policy=stat)。输入 `measure`(依赖第 24 步)。`replay=safe`。

与核心第 10 步的区别:第 10 步默认 `figure_journal`(600 dpi、色盲安全色带、
论文件);本步默认不出图,走的是 MintPy 官方 CLI 渲染,不是 engines/figures.py。

## 适用判据

- **`passthrough`(默认)**:分析 run 只为统计/分解/导出,图已在第 10 步交付,
  或稍后手工 view。规划了 25 不等于出了图。
- **`view_snapshot`**:任意已登记 h5 的标准浏览图。适合垂直/东西向分量、
  差分场快看。
- **`transection_figure`**:剖面可视化。registry 有 `start_lalo`/`end_lalo`,
  **当前实现未把这两点编进 argv**(只 `--nodisplay --save`);要带坐标的剖面
  数据用第 24 步 `transection`(写 `transect.txt`),本方法不要假装已锁定端点。
- **`kmz`**:Google Earth 静态场。输入须已地理编码。
- **`kmz_timeseries`**:点开看曲线,文件大,需地理编码时序;实现读 `ts_file`
  (缺省 `mintpy/timeseries.h5`),registry 未声明该参数 —— 分析工作区没有
  mintpy 时序时会找不到文件。

## 参数启发式

- **`input`**(science,默认 `analysis/decomposed.h5`):要画的场。
- **`dataset`**(science,默认 `""`):如 `velocity` / `vertical` / `east`;空=
  MintPy 默认。透传 LOS 不要填 vertical。
- **`dpi`**(presentation,默认 300,72–1200):浏览档;期刊级仍走第 10 步 600 dpi。
- **`cmap`**(presentation,默认 `""`):空=MintPy/本机默认,不自动套第 10 步
  vik/batlow/romaO 路由;需要 CVD 安全色带时显式给 Crameri 名,或改用第 10 步。
- **`start_lalo` / `end_lalo`**:声明给剖面图,但 `transection_figure` 尚未消费。

## 常见失败与处置

1. **默认出图却只有 passthrough.json** → 预期。要图则 SET_METHOD 到
   `view_snapshot` 等,并保证 MintPy 引擎 Python 可用。
2. **ToolMissing / MintPy 不存在** → 分析出图依赖 mintpy CLI,不是 figures.py
   → 配引擎或改回 passthrough / 用第 10 步。
3. **view 输入不在工作区** → `input` 相对路径错误 → 对准 `decomposed.h5` 或
   第 20 步源。
4. **kmz 报未地理编码** → 缺 `Y_FIRST` 一类属性 → 先 `mintpy_geocode`;不做
   静默 geocode(与第 28 步 GMT 桥同一纪律)。
5. **kmz_timeseries 找不到 timeseries.h5** → 分析 run 常无核心产物 → 把时序
   拷入或改 `passthrough`;不要生成空 KMZ。

## QA 依据

- run_ok:`exit_code == 0` + `artifact_exists(analysis_figures)`。passthrough
  靠目录内 json 满足「目录存在」;目录空才算失败。
- 真实出图:png/kmz 应非 0 字节;MintPy `--nodisplay` 避免无显示器挂起。
- 色标/单位以 MintPy 图内标注为准;本步不写论文 caption sidecar。

## 参考文献

- Crameri F., Shephard G.E., Heron P.J. (2020). The misuse of colour in science
  communication. *Nat. Commun.* 11:5444. doi:10.1038/s41467-020-19160-7
  (色带纪律;本步不自动执行,第 10 步才路由)
- MintPy `view.py` / `save_kmz.py` / `save_kmz_timeseries.py`
- 本仓库:engines/mintpy_post.py 第 25 步方法;engines/passthrough.py 标记
  `analysis/figures/passthrough.json`;核心出图见 skills/10-figure-export
