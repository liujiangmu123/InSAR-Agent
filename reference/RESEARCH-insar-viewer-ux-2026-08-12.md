# 调研报告：InSAR/遥感软件的结果浏览与图件查看 UX

- 日期：2026-08-12
- 方法：纯网络检索（官方文档 / 手册 / 插件仓库 / 教程 PDF / 论文），未运行任何软件
- 目的：为 insar-agent 原型（`prototype/js/dock.js` 影像面板 + `prototype/js/figures.js`）提炼**零依赖可落地**的结果浏览交互
- 调研对象：ESA SNAP、QGIS、ENVI/SARscape、ASF Vertex(HyP3)、COMET-LiCSAR、MintPy、GMTSAR、InSAR Explorer、EGMS Explorer，另附 NASA Worldview 与 COG/瓦片技术两个横向参照

---

## 一、逐对象调研

### 1. ESA SNAP（Sentinel-1 桌面工具箱）

**定位**：桌面重客户端，多窗口 MDI 模型，InSAR 用户看干涉图/相干性的第一站。

要点：

- **多视图 + 同步**是核心范式：每个波段双击开一个 Image View，可开任意多个；`Window → Tile Evenly` 平铺；Navigation Window 里有两个开关按钮——**Synchronise Views**（所有兼容视图共用同一视口，平移缩放联动）与 **Synchronise Cursor**（所有视图显示同步游标十字）。这是"多产品对比"的主路径，而非卷帘。
- **Navigation Window**：视口缩略小图 + 半透明矩形拖拽定位 + 缩放滑杆 + 四个按钮（Zoom In/Out、Zoom Actual Pixel 即 1:1、Zoom All 全图）。大图导航的标准配置。
- **Pixel Info View**：鼠标悬停即实时显示 地理坐标 / 图像行列 / 各波段值（带物理单位）/ tie-point / 质量 flags / 行时间；可"Snap to selected pin"冻结到某个 pin；**Copy Pixel Info to Clipboard** 复制为制表符分隔文本可直接粘进 Excel。
- **Colour Manipulation**：basic / slider / table 三档调色模式；可导出色标图例为图片（Export Colour Legend as Image）、调色板为 CSV。色标是独立可导出的一等公民。
- **性能**：视图内部用**瓦片影像 + 图像金字塔**渲染（文档明言 "The view uses tiled images and image pyramids"），这是桌面端浏览大栅格的标准做法。
- 图件导出：Export View as Image（JPEG/PNG/TIFF/BMP）、Export Transect/Mask Pixels。

来源：
- Navigation Window（同步视图/游标、缩放按钮）: https://step.esa.int/main/wp-content/help/versions/13.0.0/snap/org.esa.snap.snap.help/desktop/NavigationWindow.html
- Product Scene View（瓦片金字塔、导出、Copy Pixel Info）: https://step.esa.int/main/wp-content/help/versions/13.0.0/snap/org.esa.snap.snap.help/desktop/ProductSceneView.html
- Pixel Info View: https://step.esa.int/main/wp-content/help/versions/13.0.0/snap/org.esa.snap.snap.help/desktop/ProductExplorer.html
- 教程（实际同步操作流程）: https://fsf.nerc.ac.uk/training/eodataprocessing/tutorial_4/

### 2. QGIS（卷帘/对比插件生态）

**定位**：QGIS 核心无卷帘，全靠插件；插件生态恰好展示了"对比交互"的几种流派与踩坑。

要点：

- **MapSwipe Tool**（最老牌，2015 起）：活动图层压在其它层之上，鼠标拖动卷帘。著名 bug：高 DPI 屏幕上两层**错位**（devicePixelRatio 问题）——教训：卷帘必须对**同一坐标系下完全对齐的两张图**做裁剪，任何独立渲染管线的缩放差异都会露馅。
- **QMapCompare**（2025-2026 活跃）：提供 **mirror（镜像双屏）/ split（分屏）/ lens（镜头）** 三种对比模式，是"对比模式作为一组可切换选项"的代表。
- **QuickMapCompare**：交互最轻——**按住 S 键 + 移动鼠标即时卷帘**，松开即恢复；1.1.0 起在对比内容尚未加载完时把光标变成 spinner，解决"按了 S 看似无响应"的 UX 死角。教训：对比源未就绪时必须有忙碌指示。
- Leafmap 插件：支持垂直与水平两个方向的卷帘。
- 点查询由专门插件承担（InSAR Explorer、PS Time Series Viewer，见第 8 节）。

来源：
- MapSwipe Tool: https://plugins.qgis.org/plugins/mapswipetool_plugin/ ；对齐 bug: https://github.com/lmotta/mapswipetool_plugin/issues/11
- QMapCompare（mirror/split/lens）: https://plugins.qgis.org/plugins/qmapcompare/
- QuickMapCompare（S 键卷帘 + spinner）: https://plugins.qgis.org/plugins/quick_map_compare/version/1.1.1/

### 3. ENVI / SARscape

**定位**：商业桌面软件；ENVI 的 Portal 是"两景对比"交互的教科书；SARscape 的 Time Series Analyzer 是点选出时序的桌面标准件。

要点：

- **ENVI Portals**：两类——View Portal（整个视图做动画）与 Standard Portal（一个**可拖动、可缩放的子窗口"镜头"**，透视到下一层）。三种动画模式，业内命名即出自此处：
  - **Blend**：上层透明度渐变过渡；
  - **Flicker**：定时在两层间切换（闪烁）；
  - **Swipe**：一条垂直分割线自动往返扫动。
  动画可调速、可暂停/播放；层序约定：Layer Manager 第一个非 portal 层为显示层、第二层为对比源；右键任意层可"Display in Portal"。API 亦可编程驱动（`ENVIPortal::Animate, speed, /FLICKER|/BLEND|/SWIPE`）。
- **SARscape Time Series Analyzer**（Vector / Raster 两个变体）：
  - Vector：加载 `mean_geo`（SAR 平均亮度底图）+ PS 点 shapefile → 面板中设定速率值域 → **Color Apply** 按值域给点着色 → 选中某 PS 点 → 点 **Plot Time Series** 按钮弹出该点时序曲线窗口。右键其它 shp"Set as active layer"可在不关面板的情况下换层。
  - Raster：打开 `SI_disp_meta`（每期形变量的栅格堆栈元文件）→ 定位像元 → plot 出该像元逐期形变曲线。
- **大点云降级策略**：PS 地理编码时**自动分块输出多个 shapefile**（`_PS_75_0.shp`、`_PS_75_1.shp`……，每文件默认最多 100,000 点，阈值可配）——用"分文件"而非"抽稀"来控制单次加载量。
- SBAS 教程还约定：解缠相位系列建议用 Rainbow 色表查看、`*_fint.tiff` 用普通图片查看器即可——商业软件也大量依赖"落盘普通图片"做快检。

来源：
- ENVI Portals（Blend/Flicker/Swipe 定义与层序约定）: https://www.nv5geospatialsoftware.com/docs/Portals.html
- ENVIPortal::Animate API: https://www.nv5geospatialsoftware.com/docs/enviPortal__Animate.html
- Portal 实操（拖动镜头、调速）: https://hyspeedblog.wordpress.com/2013/12/06/application-tips-for-envi-5-utilizing-the-new-portal-view-for-visualizing-data-layers/
- SARscape PS 教程 PDF（Time Series Analyzer 流程）: https://www.sarmap.ch/tutorials/PS_v562.pdf
- SARscape SBAS 教程 PDF: https://www.sarmap.ch/tutorials/SBAS_Tutorial_562.pdf
- 中文实操（分块 shp、Raster Analyzer）: https://www.cnblogs.com/enviidl/p/16280922.html

### 4. ASF Vertex（含 HyP3 On-Demand 门户）

**定位**：Web 检索门户的标杆；它的"结果三栏 + 浏览查看器 + 基线图表"组合对我们影像/文件面板最有直接参考价值。

要点：

- **结果面板三栏**：左=场景列表（每行带加购物车/缩放到位置图标），中=**场景详情**（Start Date/Beam Mode/Path/Frame/Flight Direction/Polarization/Absolute Orbit + browse 缩略图 + Baseline Tool / SBAS Tool 按钮），右=该场景文件清单。选中左栏行，中右两栏联动填充。
- **浏览查看器（Open in Image Viewer）**：眼睛图标打开大图窗口——`+/-` 按钮与鼠标缩放平移；**底部胶片条缩略图**（点击或滚轮切换本次检索所有场景的 browse 图）；右侧显示场景元数据；默认勾选"只显示有 browse 图的场景"，无图场景显示 "No Browse Available" 占位缩略图。这是"缩略墙 + 点击放大 + 翻页"三件套的完整 Web 实现。
- **Baseline / SBAS 图表**：时间-垂直基线散点图；悬停点出数值 tooltip；点击点/连线选中场景或干涉对并联动中栏元数据；Zoom In/Out/Zoom to Fit 按钮；滑杆调基线阈值；可手工增删 custom pair。
- 地图辅助：overview map 小地图开关、季节相干性底图图层、经纬网开关。
- **HyP3 InSAR 产物打包习惯**（问题 4/5 的关键证据）：
  - 每个产物 zip 内固定含：GeoTIFF（32-bit float 数据本体）+ **PNG browse 图（固定 2048px 宽）** + KMZ（Google Earth）+ 参数元数据 txt + **每产品定制的 README.md.txt**；
  - browse PNG 带 `.png.aux.xml` / `.png.xml` sidecar 提供地理参考与 ArcGIS 元数据，普通图片带上 sidecar 就能进 GIS；
  - wrapped 相位的 `color_phase.png` **永远打包**（即使不出 GeoTIFF）——官方明说"这张彩色图对多数用户比数值更有用"；
  - 色标语义写进 README：wrapped browse 一个条纹 = 2π ≈ 2.8 cm LOS；unwrapped browse 用 6π 一个条纹 ≈ 8.3 cm——**把"一个条纹等于几厘米"这类换算随图交付**。

来源：
- Vertex 用户手册（三栏、浏览查看器、胶片条）: https://docs.asf.alaska.edu/vertex/manual/
- SBAS 工具: https://docs.asf.alaska.edu/vertex/sbas/ ；Baseline 工具: https://docs.asf.alaska.edu/vertex/baseline/
- HyP3 InSAR 产品指南（2048px browse、README、sidecar）: https://hyp3-docs.asf.alaska.edu/guides/insar_product_guide/
- HyP3 样例 README（条纹换算）: https://hyp3-examples.s3.amazonaws.com/InSAR_GAMMA/sample_metadata/SampleReadme.pdf

### 5. COMET-LiCSAR portal（含 Volcano Deformation Portal）

**定位**：全自动批量干涉图的公开门户；"固定三件套快视图 + 滑杆翻阅"的代表。

要点：

- 门户首页是 **frame 多边形地图**，每个 frame 的颜色编码 = 该 frame 已有产品数量；点击 frame 进入产品列表（目录按干涉对日期 `YYYYMMDD_YYYYMMDD` 命名）。"地图即目录"的组织方式。
- 每个干涉对固定输出三件套，**GeoTIFF 与 PNG 快视图一一配对**：`geo.diff_pha`（wrapped 相位）、`geo.unw`（解缠相位）、`geo.cc`（相干性，0-255）。分辨率统一多视到约 100 m，快视图体积可控。
- **Volcano portal** 的浏览工具分三段（对多图翻阅最有参考）：
  1. 时序工具：累积位移地图 + 起止日期**滑杆**控制时间窗；地图下方按钮**选取要画的点**与**参考点/参考区**；"save"下载曲线数据；
  2. 单干涉图区：页面底部一条**滑杆逐对翻阅**全部干涉图（wrapped / unwrapped / coherence 并排展示），图下方直接给 GeoTIFF 与 PNG 的下载链接；
  3. ML 概率图与干涉图同步翻阅。
- 新影像入库后 2 周内自动出图——"图件是流水线的默认副产物"而不是事后手工绘制。

来源：
- LiCSAR portal（frame 地图、产品组织）: https://comet.nerc.ac.uk/comet-lics-portal/
- 产品细节（三件套定义）: https://comet.nerc.ac.uk/comet-lics-portal-product-details/
- Volcano portal 工具说明（滑杆、选点、参考点按钮）: https://comet.nerc.ac.uk/comet-volcano-portal/about-tools
- CEDA 数据集记录: https://catalogue.ceda.ac.uk/uuid/52cda2e0e6c04272ae15ac836c1e8493/

### 6. MintPy（view.py / tsview.py 的出图与交互习惯）

**定位**：我们流水线第 7-9 步的引擎本尊，其出图习惯就是我们影像面板要"复刻到 Web"的原型。

要点：

- **`./pic` 目录习惯**：`smallbaselineApp.py` 配置 `mintpy.plot = auto`（默认开）——流水线跑完自动把所有关键产物画成 PNG 存进 `./pic`（默认 dpi 150）。**"每次运行结束必有一套标准图"**是 MintPy 用户的肌肉记忆，我们影像面板的"产物图件"区正对应这个目录。
- **`view.py` 多子图网格**：`--nrows N --ncols M` 一条命令画整墙子图；对 `ifgramStack.h5` 一次画出**全部干涉图的缩略图墙**（每格标日期对）；`--wrap` 重缠绕显示、`-c` 色表、`-v vmin vmax` 值域、`--dpi`（默认 300）、`--figext`（png/pdf/svg…）、`--save/--nodisplay` 批量出图不弹窗。**参考点默认画在图上**（运行日志"plot reference point"），标题自动含日期与文件名，色标自动带单位。
- **`tsview.py` 双窗交互**（点图出时序的原型）：窗口 1 = 速率/位移地图，窗口 2 = 点时序曲线；**点击地图任意像元 → 曲线即时更新**，同时把该点时序数值打印到终端；**左右方向键在日期间滑动**切换地图显示的 epoch（官方示例注释"press left / right key to slide images"）；`--yx/--lalo` 指定初始点；多个时序文件叠加对比（`--off` 垂直错开）；自动做时间函数拟合并在图上标注 `velocity: 3.20 +/- 0.34 cm/year`。
- 配套交互脚本：`plot_coherence_matrix.py`（点像元出相干矩阵）、`plot_transection.py`（交互画剖面线出剖面图）、`plot_network.py`（基线网络图）。
- **`save_kmz_timeseries.py` 的分级降级策略**（问题 5 的最完整答案）：
  - 3 级 LOD（低/中/高分辨率抽样），按 Google Earth 视高切换（默认 0/1500/4000 m 档），启动只载低分辨率级；
  - 每级再按 **300×300 点分块成 Region**，用 KML Network Link 按视窗**按需加载**；
  - 高分辨率级**只覆盖"正在形变的区域"**（判据：框内 >20% 像元速率 > 3×全图 MAD）——把带宽花在有信号的地方。

来源：
- MintPy 文档（pic 目录、脚本清单）: https://mintpy.readthedocs.io/en/latest/
- view.py 手册（nrows/ncols、wrap、ref-yx）: https://manpages.debian.org/unstable/mintpy/mintpy-view.1
- tsview.py 手册（--yx/--lalo、左右键、多文件）: https://manpages.debian.org/testing/mintpy/mintpy-tsview.1
- tsview 教程 notebook（拟合标注、点击交互日志）: https://github.com/insarlab/MintPy-tutorial/blob/main/visualization/tsview.ipynb
- Google Earth KMZ（LOD + Region 策略）: https://mintpy.readthedocs.io/en/latest/google_earth/
- smallbaselineApp.cfg（mintpy.plot 默认值）: https://github.com/insarlab/MintPy/blob/main/src/mintpy/defaults/smallbaselineApp.cfg

### 7. GMTSAR 生态

**定位**：无 GUI 的命令行流派；"浏览"靠落盘静态图 + Google Earth，代表了最低成本的图件交付路线。

要点：

- 每步产物 = `.grd` 数据 + GMT 画的 `.ps`/`.pdf` 静态图（如 `phase_mask_ll.ps`）；`p2p_processing.csh` 开地理编码时**自动产出 pdf 与 kml**。
- **`grd2kml.csh`**：`grd + cpt 色表 → PNG + KML 成对输出`（两文件必须同目录），Google Earth 就是它的"结果浏览器"。色表用 `gmt makecpt/grd2cpt` 生成——**色表是显式的、可复现的输入**，不是查看器内部状态。
- 官方文档明说 "GoogleEarth is a great way to view results quickly"——把浏览外包给通用地理查看器，工具本身只保证产物齐全。
- 时序浏览近年补位：InSAR Explorer 支持直接读 GMTSAR 的 `vel_*.grd`+ 同目录时序 grd（见下节）。

来源：
- grd2kml.csh: https://gmtsar.github.io/documentation/grd2kml.csh.html
- 地理编码与 KML 工作流: https://gmtsar.github.io/documentation/Projecting_To_LatitudeLongitude.html
- Quick Start（自动出 pdf/kml）: https://gmtsar.github.io/documentation/Quick_Start_Guide.html
- GMTSAR 技术文档（.ps/.kml 产物清单）: https://www.osti.gov/servlets/purl/1090004

### 8. InSAR Explorer（QGIS 插件，2024-2026 活跃）

**定位**：目前开源界"点图出时序"体验最好的实现，通吃 SARvey/MintPy/MiaplPy/GMTSAR/SARscape/EGMS 六家产物；交互细节值得逐条抄。

要点：

- **核心交互一句话**：打开速率图层 → 点击地图上任意点/像元 → dock 面板即时画出时序曲线。矢量（shp/gpkg）与栅格（GMT grd）都支持。
- **命名约定驱动的零配置**：打开 `vel_*.grd` 后自动发现同目录 `timeseries-YYYYMMDD*.grd` 并接管——**"速率图 + 按日期命名的兄弟文件"就是接口契约**，不需要注册表或工程文件。
- 分析功能：线性/多项式/季节**拟合** + **残差图**（观测减模型，用于找异常）；**动态改参考点**（在地图上重选参考点，全图与曲线随之重算）；polygon 圈选多点；**hold-on 按钮**叠加多点曲线对比；y 轴范围多种控制；图表自带缩放/平移/导出工具栏。
- UX 细节：符号化一键应用（内置色表 + "从数据取值域"按钮 + 实时 symbology 开关）；状态栏对每步操作给即时反馈；图层不兼容时插件自动禁用；换层保留曲线。
- MintPy 官方文档直接推荐用它做 QGIS 可视化（`save_explorer.py` 导出 grd / `save_qgis.py` 导出 shp）。

来源：
- GitHub: https://github.com/luhipi/insar-explorer
- 文档（数据契约、命名约定）: https://insar-explorer.readthedocs.io/en/latest/
- 插件页 changelog（reference point、hold-on、残差）: https://plugins.qgis.org/plugins/insar_explorer-dev/
- MintPy QGIS 集成: https://mintpy.readthedocs.io/en/latest/QGIS/
- 第三方评测（拟合/残差/参考点价值）: https://geoawesome.com/maximizing-the-potential-of-sar-analysis-with-insar-explorer/

### 9. EGMS Explorer（欧洲地面运动服务浏览器）

**定位**：面向公众的洲级 InSAR 点云 Web 浏览器（数亿测量点），时序查看器交互打磨得最细，是我们"点位时序卡"的升级模板。

要点（依据官方手册 v4.0，2026-04）：

- **地图**：点云按平均速率 mm/yr 着色；点击点 → 白圈高亮 + 打开时序查看器；底部坐标条点击可切换 WGS84/LAEA；2D/3D 切换；位置/坐标搜索框；分享当前视图链接。
- **时序查看器**（交互清单可直接当验收标准用）：
  - 左上角**点信息块**：dataset、Point ID、位置、平均速率、RMSE；
  - 滚轮缩放曲线；**Shift+滚轮只缩 Y 轴，Ctrl/Alt 锁 X 轴**；**Fit 按钮**一键复位；可设固定 Y 轴范围；
  - 悬停数据点出数值；点/连线两种显示切换；x 轴网格密度可调；
  - **Shift+点击地图多点 → 曲线叠加对比**，信息块变成可滚动列表逐点查看；
  - **polygon 圈选 → 区域平均时序**，信息块标注参与平均的点数（如"115 points"）；
  - **View Data** 打开原始数据表（逐日期位移值 + 质量字段）；
  - 线性/多项式**最佳拟合**曲线可选；
  - **Generate SVG** 导出当前曲线图。
- **Dataset Settings**（色标管理模板）：透明度、点大小；多色表 + **预设 stretch 档位（very fine / fine / medium / coarse / custom）**；**色盲友好色带**；全黑点模式；隐藏地形以下点。图例有独立窗口（工具栏 Legend 按钮）。
- **Calibration ON/OFF**：同一图层可切换"含/不含 GNSS 模型"的显示（残差视图），手册强调对比必须"同图层、同范围、同色标"——区域性构造运动 vs 局部形变的一键分离，纯显示层操作不改产品。
- 档案检索：画多边形查询产品，**hover 结果列表高亮地图上对应区块**；下载按钮直接显示结果数。

来源：
- EGMS Explorer: https://egms.land.copernicus.eu/
- EGMS Explorer Manual v4.0（EGMS2-D8.1-EM-SC1-036）: https://land.copernicus.eu/en/technical-library/egms-end-user-interface-manual/@@download/file
- 产品页: https://land.copernicus.eu/en/products/european-ground-motion-service/

### 10.（横向参照 A）NASA Worldview 的 A/B 对比模式

业界 Web 端"两景对比"的事实标准，与 ENVI Portal 桌面端三模式一一对应：

- **Start Comparison** → 界面进入 A/B 双状态：图层列表分裂为 A、B 两个 tab，时间轴出现 A/B 日期选择器——**对比的本质是两份完整的 (图层,日期) 状态**，而不只是两张图；
- 三模式：**Swipe**（默认，拖动垂直分割线）/ **Opacity**（滑杆渐隐）/ **Spy**（圆形放大镜看另一侧）；Exit Comparison 一键退出。

来源：
- https://www.earthdata.nasa.gov/news/blog/introducing-worldviews-comparison-feature
- https://www.earthdata.nasa.gov/news/feature-articles/data-tool-focus-nasa-worldview

### 11.（横向参照 B）大栅格 Web 浏览的三条降级路线

1. **固定尺寸 browse PNG**（HyP3 路线）：2048px 宽 PNG + 元数据 sidecar；实现成本≈0，够干涉图/速率图快检用。LiCSAR 同路线（100 m 多视 + PNG 快视图）。
2. **分级 + 分块按需加载**（MintPy KMZ 路线）：3 级 LOD × 300×300 点 Region，网络链接按视窗加载；高分辨率只给形变区。适合点云/超大图，无需服务器切瓦片。
3. **COG（Cloud Optimized GeoTIFF）**：内部 256/512 瓦片 + 逐半递减 overview 金字塔（降到 256×256 为止，体积开销约 +30-40%），HTTP Range Request 按需取块；`gdal_translate -of COG` 一步生成（gdal2tiles 预切瓦片路线已过时，GDAL 3.13 起弃用）。浏览器端需要 georaster 类 JS 库解码——**对零依赖原型不适用，列为将来后端选项**。

来源：
- COG 结构: https://www.kitware.com/deciphering-cloud-optimized-geotiffs/ ；https://swyvl.io/blog/what-is-cloud-optimized-geotiff/
- 生产实践: https://sean-rennie.medium.com/cogs-in-production-e9a42c7f54e4

---

## 二、五个问题的横向结论

**Q1 多图网格/画廊怎么做？**
通行做法是三层：① **缩略墙**（MintPy view.py 的 nrows×ncols 干涉图墙、Vertex 浏览查看器底部胶片条、LiCSAR 门户产品列表）；② **点击放大**成单图查看器（Vertex 眼睛图标 → 大图 + 缩放平移）；③ **翻页**用两种等价手段——胶片条点击/滚轮（Vertex）或滑杆逐对翻阅（LiCSAR volcano portal 底部滑杆）+ 键盘左右键（MintPy tsview 惯例）。分组维度按产物类型（wrapped/unw/coh 三件套并排）或按日期对。无 browse 图的项要有占位缩略图（Vertex "No Browse Available"）。

**Q2 时序点查询的通行设计？**
高度收敛的"**地图-曲线双联动**"模式：速率图/点云按速率着色 → 点击点/像元 → 旁侧面板即时出时序曲线（MintPy tsview 双窗、LiCSBAS 双窗、SARscape Plot Time Series、InSAR Explorer dock、EGMS 时序查看器全部同构）。成熟实现的增量特性依次是：曲线上悬停出数值 → **多点叠加对比**（EGMS Shift+点击 / InSAR Explorer hold-on）→ **拟合线 + 速率±不确定度标注**（MintPy 自动标注、EGMS/InSAR Explorer 可选线性/多项式/季节）→ 残差图（InSAR Explorer）→ 区域 polygon 平均（EGMS，标注点数）→ 原始数据表 + 导出（EGMS View Data / Generate SVG）。参考点在图上永远可见（MintPy 黑方块、LiCSBAS 黑虚线框且可拖动改参考区、InSAR Explorer 可动态改参考点）。

**Q3 两景对比的主流交互？**
三模式已成行业词汇：**Swipe（卷帘）/ Blend·Opacity（透明度渐变）/ Flicker·Spy（闪烁/镜头）**——ENVI Portal（桌面）与 Worldview（Web）两套命名基本重合。卷帘是默认模式；闪烁对"找变化"最灵（视觉暂留），实现也最便宜；镜头（spy/lens/standard portal）最炫但使用频率低。桌面派还有第四种：**并排 + 同步视口/游标**（SNAP Synchronise Views/Cursor），适合不同物理量（相位 vs 相干性）而非同量两期。工程要点：两图必须像素级对齐（QGIS MapSwipe 的 DPI 错位 bug）；对比源未加载完要有忙碌指示（QuickMapCompare）；对比状态是"两份 (图层,日期) 配置"（Worldview A/B tab）。

**Q4 图件元数据怎么随图展示？**
两条并行惯例：① **烧进图里**（MintPy/GMTSAR 派）——色标+单位、标题含日期与文件名、参考点标记、比例尺直接由 matplotlib/GMT 画进 PNG，图自包含、离线可读；② **随图附带结构化元数据**（HyP3/EGMS 派）——PNG 旁挂 sidecar（.png.aux.xml、README、参数 txt），查看器里用**信息块**动态显示（EGMS 点信息块：dataset/ID/位置/速率/RMSE；Vertex 中栏元数据）。共同点：**单位与换算语义必须显式**（HyP3 README 写明"一个条纹=2.8 cm"）；色标可独立导出（SNAP Export Colour Legend）；色表档位预设化（EGMS very fine→coarse 四档 stretch + 色盲色带）。
**Q5 大栅格降级策略？**
见第 11 节三路线。共识排序：**原型/快检期用固定尺寸 browse PNG（2048px 宽已被 HyP3 验证够用）→ 数据量上来后按需加载（LOD+分块，MintPy KMZ 已给出参数：3 级、300×300 块、形变区优先）→ 正式产品化才上 COG/瓦片服务**。桌面软件（SNAP/ENVI）则一律内置瓦片+金字塔渲染。另一个普遍习惯：**全分辨率 GeoTIFF 永远只作为下载/GIS 入口，不直接进浏览视图**。

---

## 三、可借鉴机制排行（实现成本 × 价值）

按"性价比"排序；成本按**零 npm 依赖原生 JS 原型**内的实现难度估计。

| # | 机制 | 出处 | 价值 | 零依赖成本 | 判断 |
|---|------|------|------|-----------|------|
| 1 | 灯箱翻页：←/→ 键 + 上/下一张按钮 + "n/N" 计数 | Vertex 胶片条、MintPy 左右键 | 高 | 极低（数组索引） | **必做**，现灯箱只能看单图 |
| 2 | 图件元数据条：单位/色标范围/参考点/主辅日期/指纹随图显示 | MintPy 自动标注、HyP3 README、EGMS 信息块 | 高 | 极低（IMAGES 加字段+渲染） | **必做**，与审计面板天然衔接 |
| 3 | 闪烁对比（flicker）：按住空格或自动定时切换两图 | ENVI Flicker、Worldview | 高（震前后快检最有效） | 极低（toggle opacity） | **必做**，比卷帘还便宜 |
| 4 | 卷帘对比（swipe）：叠两图 + clip-path 随鼠标裁剪 | ENVI/Worldview/QGIS 全家 | 高 | 低（CSS clip-path + pointer 事件） | **必做**，行业标配动作 |
| 5 | 时序卡拟合线 + "速率±σ"标注 + 悬停数值 | MintPy tsview、EGMS、InSAR Explorer | 高 | 低（最小二乘 10 行 + SVG title/十字线） | **必做**，让曲线可读出结论 |
| 6 | 多点时序叠加对比（Shift+点选 / hold-on） | EGMS、InSAR Explorer | 中高 | 低（timeSeriesSvg 已支持多 series） | 建议做 |
| 7 | 缩略墙分组 + 干涉对日期滑杆翻阅 | LiCSAR volcano portal、MintPy ifgramStack 墙 | 中高 | 低（range input 驱动重渲染） | 建议做，配合 7 个 DATES 演示 |
| 8 | browse 分级尺寸契约：thumb ~512px / browse 2048px / GeoTIFF 只下载 | HyP3、LiCSAR | 高（后端契约，前端零改动） | 低（写进产物约定） | **必做**（落到后端出图规范） |
| 9 | 无图占位 + STALE 蒙灰缩略图 | Vertex "No Browse Available" | 中 | 极低 | 顺手做 |
| 10 | 并排 + 同步十字游标 | SNAP Synchronise Cursor | 中 | 中（mousemove 广播到两 SVG） | 二期 |
| 11 | 色标 stretch 预设（fine/medium/coarse）+ wrap 开关 | EGMS 四档、MintPy --wrap | 中 | 中（演示 SVG 需参数化渐变） | 二期，接真实后端出图更合适 |
| 12 | 区域 polygon 平均时序（标注点数） | EGMS | 中 | 中高（圈选几何+均值） | 二期 |
| 13 | 参考点可视 + 动态改参考点 | LiCSBAS 拖参考框、InSAR Explorer | 中（科研刚需） | 中高（需重算全场） | 二期，先做"参考点标记可见"（并进 #2） |
| 14 | spy/lens 镜头对比 | ENVI Standard Portal、Worldview Spy | 低 | 中 | 不做 |
| 15 | COG/瓦片金字塔 | COG 生态 | 高（大数据期） | 高（需解码库，违反零依赖） | 原型期不做，写入将来后端选项 |

---

## 四、落地建议（映射到 prototype）

原则：全部零 npm 依赖，纯原生 DOM/CSS/SVG；演示期用 `figures.js` 的 SVG 假图驱动，接真后端时同一交互直接换成 `<img>` 加载 browse PNG。

### 4.1 `figures.js`：给 `IMAGES` 补元数据字段（对应排行 #2/#8）

现在 `IMAGES` 只有 `id/name/title/meta/step`。建议扩成：

```js
{ id: 'vel', name: 'vel_ridgecrest_2019.png', title: '同震位移 · LOS (mm)',
  meta: 'figure_journal · 2.4 MB', step: 10,
  kind: 'map',                       // map | ts | ifg | coh —— 决定可参与哪种对比
  unit: 'mm', cmap: 'RdYlBu', crange: [-40, 40],
  refPoint: 'GNSS P580',             // 参考点名，随图显示
  dates: ['2019-06-10', '2019-08-15'],  // 主辅/起止日期
  scale: '1 fringe = 2.8 cm LOS',    // HyP3 式换算语义（ifg 类才有）
}
```

这些字段有三处消费：画廊卡片 footer、灯箱元数据条、将来后端出图时的 **PNG sidecar JSON 契约**（同名 `.json` 存 unit/cmap/crange/ref/dates/指纹——正好与我们 provenance 指纹体系并轨，HyP3 的 `.png.aux.xml` 思路）。

### 4.2 灯箱升级：翻页 + 元数据条（排行 #1/#2）

改 `stream.js` 的 `openLightbox` 与 `app.js` 的键盘处理：

- `openLightbox(figId)` 记住 `IMAGES` 中的索引；灯箱头部加 `‹ ›` 两个按钮与 `2/4` 计数；`ArrowLeft/ArrowRight` 翻页（ESC 关闭已有）。翻页只是 `figureNode(nextId)` 重渲染，成本为零。
- 灯箱底部加一条元数据带（复用 `.tags` 样式）：`单位 mm · 色标 ±40 · 参考点 GNSS P580 · 2019-06-10 → 08-15 · 指纹 st_(step).fingerprint · STALE 状态`。STALE 时整条橙色——图和它的有效性永远一起出现（这是我们相对所有被调研软件的差异化优势，务必保留）。

### 4.3 影像面板加"对比"卡片：闪烁 + 卷帘（排行 #3/#4）

在 `dock.js` `imagesView()` 的画廊与小地图之间插一张 `compare` 卡：

- **选择器**：A/B 两个 `<select>`，选项来自 `IMAGES`（kind 为 map/ifg/coh 的图）+ 演示用"震前对 06-22→07-04 / 同震对 07-04→07-16"两个干涉对（`figures.js` 可以从现有 `ifgSvg` 参数化出一张"震前几乎无条纹"的变体，成本一个函数）。
- **三模式按钮**（`aria-pressed` 单选）：
  - 卷帘：容器内叠两个 `div`（各含一张 SVG，`position:absolute; inset:0`），上层 `clip-path: inset(0 calc(100% - var(--x)) 0 0)`；`pointermove` 更新 `--x`，加一条 2px 分割线 + 圆形把手。**两图同一容器同一尺寸，天然像素对齐**，避开 QGIS MapSwipe 的 DPI 坑。
  - 闪烁：按住空格/长按鼠标切换上层 `opacity 0/1`（`keydown/keyup`），另给一个 1.5s `setInterval` 自动模式（ENVI 默认 0.5s，可调速做成 3 档）。
  - 并排：`grid-template-columns: 1fr 1fr` 摆两图（窄面板自动堆叠），二期再加同步十字线。
- 卡片 footer 标注两图日期差（`Δt = 12 d`）——对比语义随图可见（Worldview A/B 日期的简化版）。

### 4.4 时序卡增强（排行 #5/#6）

改 `figures.js` 的 `timeSeriesSvg`：

- 选项 `fit: true` 时对 series 做最小二乘直线（7 个点，一行公式），画虚线拟合线并在角落标 `-182.0 ± 12.4 mm/yr`（数据已在 `POINTS.rate/std` 里，直接复用）；
- 每个数据点包一个透明大热区 `<circle r=10>` + `<title>07-16 · -168.2 mm</title>`（原生 tooltip，零 JS），再给最近点画十字线可以二期用 mousemove 做；
- `dock.js` `tsCard`：点位 pin 支持 **Shift+点击叠加**——`S.comparePoints` 数组，`timeSeriesSvg` 本来就吃多 series（GNSS 虚线对比已在用），EGMS 的多点对比交互一步到位；信息行沿用 EGMS 信息块字段顺序：名称 / 速率±σ / 点数 / 参考标记。

### 4.5 画廊：分组 + 干涉对滑杆 + 占位（排行 #7/#9）

- 画廊按 `kind` 分两组小标题：「形变产品」（vel/ts）与「干涉产品」（ifg/coh）——对应 LiCSAR 三件套心智；
- `ifg` 卡片内嵌一个 `<input type=range min=0 max=5>`（6 个干涉对），拖动即重渲染标题日期对（演示期换 `ifgSvg` 的条纹密度参数模拟不同对；真实期换 `ifg_<date1>_<date2>.png` 的 src）——LiCSAR volcano portal 底部滑杆的卡片化；
- 步骤未跑完/无图时渲染灰底占位卡（"尚未生成 · 运行第 N 步后可用"），STALE 时缩略图 `filter: grayscale(.6) opacity(.75)` 蒙灰——Vertex 占位 + 我们指纹体系的结合。

### 4.6 后端出图契约（写给将来的 `figure_journal.py`，排行 #8）

原型不改代码，但建议把以下约定写进产物规范（可加到 AGENT-DESIGN 的产物章节）：

1. 每个图件产物三档：`thumb/`（≤512px，画廊用）、`browse/`（2048px 宽 PNG，灯箱/对比用，HyP3 已验证的尺寸）、`geotiff/`（全分辨率，只出现在文件面板做下载，不进影像面板）；
2. 每张 PNG 同名 `.json` sidecar：`{unit, cmap, crange, ref_point, date1, date2, fingerprint, step}` ——前端元数据条直接读它，杜绝"图和标注分家"；
3. 时序类产物额外落一份 `ts_points.json`（点位 + 逐日期值），点选交互无需回源 HDF5——InSAR Explorer"命名约定驱动"的思路；
4. 干涉图快视图固定三件套 wrapped/unw/coh 一起出（LiCSAR 惯例），色标语义（一个条纹=?cm）写进 sidecar 的 `scale` 字段（HyP3 惯例）；
5. COG/瓦片列为非目标：数据量到需要瓦片时再由后端出 COG + 轻量 tile 服务，前端交互不变。

### 4.7 明确不做

- spy/lens 镜头、3D 视图、WMS 外接图层——价值/成本比过低；
- 前端解码 GeoTIFF（violates 零依赖）；
- polygon 圈选平均、动态参考点重算——依赖真实数据栈，进后端接入后的二期清单。

---

## 附：本报告未覆盖但值得后续跟踪

- ARIA/OPERA 的 DISP 产品浏览门户（NASA 新一代位移产品，2025 起陆续上线）；
- Sentinel Hub EO Browser 的 compare 模式与 timelapse 生成器（商业 Web 端参照）；
- LiCSBAS2 是否给交互查看器加了 Web 化输出。
