# 浏览器端图像/栅格查看技术调研(2026-08-12)

> 背景:insar-agent 前端是零 npm 依赖的原生 JS 原型(ES modules,手写 SVG);后端 FastAPI;
> 产物是 matplotlib 生成的 PNG 图件(`engines/figures.py`,默认 600 dpi,输出到 run 工作区
> `products/figures/`)与 HDF5 栅格(MintPy `velocity.h5` 等)。
> 本文评估浏览器端图像/栅格查看方案,重点是**零依赖或单文件可内嵌**路线,并给出可直接抄的
> 参考实现(Canvas pan/zoom、lightbox、卷帘对比,各 <80 行)。

## 结论速览

- **近期(推荐,零依赖)**:matplotlib 双档出图(缩略图 + 全尺寸)→ `<img srcset>` 网格画廊
  → 原生 `<dialog>` lightbox(浏览器自带焦点陷阱)→ 手写 Canvas pan/zoom 查看器(~75 行)
  → `clip-path` 卷帘对比(~1 行 JS)。合计 ~250 行自有代码,0 外部依赖,与现有原型架构完全一致。
- **远期(大栅格瓦片化)**:MintPy `save_gdal.py` 导出 GeoTIFF → `gdal_translate -of COG` →
  两条路二选一:① 静态金字塔(`vips dzsave` / `gdal2tiles`)+ FastAPI `StaticFiles` 托管,零运行时依赖;
  ② 动态瓦片 `titiler.core`(FastAPI 挂载约 10 行,但引入 rasterio/GDAL 依赖链 ~25MB)。
  前端届时可 vendor 单文件 OpenSeadragon(纯 JS、零外部依赖)。

---

## 一、方案逐评

### 1.1 手写 Canvas pan/zoom(近期首选)

| 维度 | 评估 |
| --- | --- |
| 依赖 | 0(浏览器原生 Canvas 2D + Pointer Events + wheel) |
| 体积 | ~75 行 / ~3KB 源码 |
| 集成 | ES module 直接放 `prototype/js/`,无构建 |

**最小可用行数拆解**(回答重点问题 1):

- 只有鼠标拖拽 + 滚轮缩放到光标 + 高 DPI + fit 居中:**约 50 行**;
- 加双指捏合(Pointer Events 两指距离比):**约 75 行**(见第四节骨架 A);
- 加惯性平移(velocity 采样 + rAF 指数衰减):**再 +20 行**;
- 要做到 PhotoSwipe 级手感(弹簧动画、边界回弹、双击缩放动画):迅速膨胀到数百行,不建议手写,
  届时直接 vendor PhotoSwipe(53KB)更划算。

**核心算法**(共识写法,来源:Stack Overflow "Zoom Canvas to Mouse Cursor" 高票答案、
harrisonmilbradt.com canvas panning 教程):

- 只维护一份视图状态 `{scale, ox, oy}`,含义是 `屏幕坐标 = 图像坐标 × scale + (ox, oy)`;
- 缩放锚点公式:`o' = p − (p − o) × f`(p 为光标屏幕坐标,f 为本次缩放因子),光标下的像素不动;
- 每帧 `ctx.setTransform(scale, 0, 0, scale, ox, oy)` 后 `drawImage(img, 0, 0)`。
  **不要**用 `ctx.translate/scale` 连乘累积——浮点漂移且难以求逆。

**已知的坑**(全部有出处,重点问题 1 的第二问):

1. **高 DPI 模糊**:canvas 位图尺寸必须是 CSS 尺寸 × `devicePixelRatio`,再在变换里乘 dpr,
   否则 Retina/高分屏上整体发虚(web.dev "High DPI Canvas"、MDN devicePixelRatio)。
   注意:浏览器页面缩放(Ctrl+滚轮)会改变 dpr;窗口拖到另一台显示器 dpr 也会变——
   用 `ResizeObserver` + 重建位图兜底。
2. **平滑/像素化**:`imageSmoothingEnabled` 默认 true,放大科学栅格会把像元糊成一片;
   放大(scale>1)时应置 false(等效 CSS `image-rendering: pixelated`),缩小时保持 true 并设
   `imageSmoothingQuality='high'`,否则缩略时出锯齿/摩尔纹(MDN imageSmoothingEnabled)。
   另一个认知坑:600 dpi matplotlib PNG 放大后看到的是**渲染像素**而非数据像元,
   像元级检查要靠远期瓦片方案或后端取值接口。
3. **wheel 事件三件套**:
   - 监听必须 `{ passive: false }` 才能 `preventDefault()`,否则页面跟着滚/整页缩放;
   - `deltaMode` 归一化:Firefox 可能给 LINE(×8~16)或 PAGE(×24+)单位,不能假定是像素
     (MDN deltaMode;danburzo.ro/dom-gestures);
   - **触控板捏合被浏览器编码为 `ctrlKey: true` 的 wheel 事件**,delta 很小(±0.5~3),
     而鼠标滚轮 delta ±100+。通用解法:把 deltaY 夹紧到 ±10~24 再喂给指数因子
     `Math.exp(-d * k)`,两种设备手感都顺(tigerabrodi.blog 触控板处理专文)。
     iOS Safari 还有非标 `gesturestart` 事件,需 `preventDefault` 防整页缩放。
4. **惯性(可选)**:拖拽时滚动记录最近 ~100ms 的指针样本,释放时算出 px/ms 速度,
   rAF 循环里 `v *= Math.pow(decay, dt)`(每帧约 ×0.95),低于阈值(~0.03px/ms)停;
   新的 pointerdown/wheel 必须取消惯性动画(ariya.io kinetic scrolling 系列)。
   桌面工作台场景收益低,**建议先不做**。
5. **指针管理**:用 Pointer Events 统一鼠标/触控笔/手指;`setPointerCapture` 保证拖出画布
   不丢事件;元素要设 `touch-action: none` 否则浏览器手势抢先(MDN setPointerCapture)。
6. **大图性能**:6000×4800 PNG 每帧整幅 `drawImage` 在现代机器没问题(走 GPU 纹理),
   但接近 8K+ 要注意浏览器纹理上限;可用 `createImageBitmap()` 预解码避免首帧卡顿。

**替代承载:CSS transform 直接变换 `<img>`**(如 timmywil/panzoom,~4KB)。更省代码,但有
专属坑:`will-change: transform` 会把元素固化成一张位图,放大后**不重栅格化、永远是糊的**;
Chrome 53+ 在没有 will-change 时缩放结束会自动重栅格化,Safari 需要手动切换 will-change 强制
重绘(Chrome Developers "Re-rastering composited layers")。Canvas 方案没有这个问题,且为将来
叠加十字线、像元取值、卷帘留了余地——**本项目选 Canvas**。

### 1.2 网格画廊 + lightbox 的无依赖模式(重点问题 2)

**成品库参照:PhotoSwipe v5**——0 依赖、纯 ESM、无构建可用;core 53KB min + lightbox 14KB min
+ 单个 CSS 7.2KB(合计 gzip ~16.6KB);图标全部 JS 生成、无外部资源(photoswipe.com、npm)。
它是"卷到头"的实现基准:捏合、双击缩放动画、拖拽关闭都有。若某天要这些手感,直接 vendor
它的两个 esm 文件即可,不违背零 npm 原则。

**手写路线(推荐)**,三个关键点:

1. **焦点陷阱:用原生 `<dialog>` + `showModal()`,不要手写 focus trap**。
   2026 年共识(accessibility.build、CSS-Tricks、W3C APA 工作组结论):`showModal()` 自动
   把对话框提升到 top layer、背景整体 `inert`(不可聚焦不可点击、对读屏隐藏)、Esc 关闭、
   关闭后焦点自动还原到触发元素、还送一个可样式化的 `::backdrop`。Tab 能跳到浏览器地址栏
   是**预期行为**,不需要也不应该拦。所有现代浏览器 Baseline 2022 起支持。
   唯一的坑:用成 `show()` 或 `open` 属性就没有这些语义(非模态),必须是 `showModal()`。
2. **键盘导航**:在 dialog 上挂一个 `keydown`,处理 ←/→/Home/End;Esc 不用管(原生)。
   点击 backdrop 关闭:backdrop 点击事件的 `target` 是 dialog 自身,判断 `e.target === dlg`。
3. **预加载策略**:
   - 网格缩略图:`loading="lazy"` + `decoding="async"`,并写死 `width/height`(防布局跳动);
   - 打开第 i 张时,用 `new Image().src = url` 预热第 i±1 张(经典 Lightbox2 的
     `preloadNeighboringImages` 模式,浏览器缓存生效后切换零延迟);
   - 无 JS 退化:缩略图包在 `<a href="全尺寸.png">` 里,脚本失效时点击仍能看原图。

参考实现见第四节骨架 B(约 55 行,含 HTML/CSS/JS)。

### 1.3 `<img srcset>` 金字塔(与画廊配套,零 JS)

浏览器原生的"多分辨率选档":`srcset` 用 `w` 描述符列出各档宽度,`sizes` 告诉浏览器该图在
布局中实际占宽,浏览器按 `占宽 × devicePixelRatio` 挑最小够用的档(MDN Responsive images、
web.dev)。对本项目就是**双档**:缩略图(~480w)+ 全尺寸(600dpi 原图)。

已知的坑:`sizes` 必须与真实布局一致——漏写等于宣称 100vw,浏览器会在网格里也拉全尺寸大图,
这是响应式图片的第一大 bug(sushi.dev srcset 指南);LCP 首屏图不要 `loading="lazy"`。

它只解决"该拉哪档",不提供 pan/zoom;和 Canvas 查看器是互补关系(画廊用 srcset,
lightbox 里加载全尺寸)。成本:纯 HTML 属性,0 行 JS。

### 1.4 OpenSeadragon(deep zoom,远期前端候选)

- **依赖/体积**:纯 JavaScript、**零外部依赖**,BSD-3;v6.0.2(2026-03),发行包
  openseadragon-bin ~982KB(zip,含 min.js 与导航按钮图片),可整目录 vendor,`<script>` 或
  AMD/CommonJS 引入,无构建(openseadragon.github.io)。
- **吃的数据**:DZI(Deep Zoom)、IIIF、TMS、Zoomify、自定义 tile source;还有两个对本项目
  很有用的低成本模式:
  - `type: 'image'`(Simple Image):直接给一张普通 PNG,内部自建金字塔(`buildPyramid` 可关);
  - `type: 'legacy-image-pyramid'`:给若干**离散尺寸**的同一张图(thumb/detail/best),
    正好对应"matplotlib 双档/三档出图"——**不用切瓦片就有按需选档的 deep zoom**。
    注意每档是整图加载,单档超过 ~10MB 体验会崩(官方 issue #1489:20480² 的 JPEG 直接白屏),
    大图仍要 DZI。
- **DZI 生成**:`vips dzsave input.tif out --layout dz`(libvips,快、内存占用小;也支持
  zoomify/google/iiif 布局);GDAL 侧是 `gdal2tiles.py`。dzsave 不做重投影,纯切片。
- **成本结论**:近期用它属于"为一张 PNG 扛一个 1MB 查看器",不划算;远期大栅格 + 静态金字塔
  路线里它是零 npm 阵营里最顺的前端(vendor 单文件 + 静态瓦片目录即可)。

### 1.5 Leaflet + georaster-layer-for-leaflet(不推荐)

浏览器端直读 GeoTIFF/COG 渲染,无需瓦片服务(FOSS4G 演讲 "Truly Server-Free GeoTIFF
Visualization")。但依赖链与零依赖原则冲突最严重:peer 依赖 `georaster` + `leaflet`(~150KB
min / 42KB gz),自身运行时依赖 **20+ 个包**(proj4、geowarp、geotiff 系列、web worker 打包),
browserify bundle 数百 KB(npm 页 dependencies 列表)。且它吃 GeoTIFF 不吃 PNG——近期产物形态
不匹配,远期若要 web 地图叠加(底图 + 形变场)才值得重新评估。

### 1.6 maplibre-gl(不推荐,除非要底图生态)

WebGL 矢量/栅格地图引擎。v6 起 **ESM only**,dist JS 总量 ~1.4MB(主包 + worker;官方 PR #7745
拆共享 chunk 后 ~1.0MB)+ CSS;worker 文件的打包姿势有专门文档(`?worker&url`)。支持 raster
tiles、COG source、PMTiles(单文件瓦片包,免瓦片服务)。能力强但体积/复杂度对本项目过重,
只有当需求变成"标准 web 地图 + 多图层 + 底图"时才考虑。

### 1.7 deck.gl(不推荐)

WebGL 数据可视化框架。官方 v9 参考数字:`Deck + Layer` 基线 501KB min / 145KB gz;栅格显示
走 `TileLayer`(@deck.gl/geo-layers)+ `BitmapLayer`(@deck.gl/layers),依赖 luma.gl/loaders.gl
生态,实际需要 bundler 做 tree-shaking。适合大规模点云/时序 3D(百万级散点),
对"看 PNG 图件"完全过杀。

### 1.8 COG + titiler / rio-tiler 动态瓦片(远期后端候选,重点问题 3)

**FastAPI 集成成本:很低,约 10 行**(developmentseed.org/titiler Getting Started):

```python
from fastapi import FastAPI
from titiler.core.factory import TilerFactory

app = FastAPI()
cog = TilerFactory(router_prefix="/cog")     # 也可挂到现有 insar-agent app 上
app.include_router(cog.router, prefix="/cog", tags=["COG"])
# 自动获得 /cog/tiles/{z}/{x}/{y}、/cog/tilejson.json、/cog/info、/cog/statistics、/cog/point
```

注意事项与真实成本:

- **包名**:2025 年底起 PyPI 的 `titiler` 元包已废弃,要装子包 `titiler.core`(官方公告)。
- **依赖链才是成本大头**:titiler.core → rio-tiler → rasterio。好消息是 rasterio 官方 wheel
  **自带 GDAL**(1.5.0 wheel ~23MB,Linux/macOS/Windows 全有,含 HDF5/netCDF/OpenJPEG 驱动),
  `pip install` 即可,Windows 无需装系统 GDAL;坏消息是 rasterio 1.5 要求 Python ≥3.12、
  numpy ≥2(旧环境用 1.4.x)。
- **更薄的替代**:跳过 titiler,直接用 rio-tiler 手写一个端点(约 30 行),依赖面小一层,
  URL 规则完全自控:

```python
from rio_tiler.io import Reader
from fastapi.responses import Response

@app.get("/tiles/{run_id}/{z}/{x}/{y}.png")
def tile(run_id: str, z: int, x: int, y: int):
    with Reader(workspace(run_id) / "products/velocity_cog.tif") as src:
        img = src.tile(x, y, z)                      # 窗口读 + 重采样
    return Response(img.render(img_format="PNG"), media_type="image/png")
```

- **HDF5 直读**:titiler.xarray / rio-tiler `XarrayReader` 支持 NetCDF/HDF5/Zarr(再加
  xarray、rioxarray、h5netcdf 依赖)。但 MintPy 的 HDF5 属性不是标准 CF 坐标,直接喂通常要写
  自定义 opener;**更稳的路径是先落成 GeoTIFF**(MintPy 自带 `save_gdal.py` 可把
  geocode 后的 velocity.h5 导出 GeoTIFF),再走 COG。
- **前端配套**:动态瓦片端点出来后,前端仍需一个瓦片查看器(Leaflet / maplibre / OpenLayers,
  或 OpenSeadragon 自定义 tile source 吃 `/tiles/{z}/{x}/{y}.png`)。

**COG 转换(GDAL,重点问题 3 第二问)**:

```bash
# GDAL >= 3.1:专用 COG 驱动,自动建 overview、自动整理布局
gdal_translate velocity.tif velocity_cog.tif -of COG \
    -co COMPRESS=DEFLATE -co OVERVIEW_RESAMPLING=AVERAGE -co BLOCKSIZE=256
# GDAL 3.4+ 默认 COMPRESS=LZW;浮点数据可加 -co PREDICTOR=FLOATING_POINT

# 旧版 GDAL(<3.1)的等价两步
gdaladdo -r average velocity.tif 2 4 8 16
gdal_translate velocity.tif velocity_cog.tif -co TILED=YES -co COPY_SRC_OVERVIEWS=YES -co COMPRESS=DEFLATE

# 纯 Python 替代(rio-cogeo):
#   rio cogeo create velocity.tif velocity_cog.tif --cog-profile deflate
#   rio cogeo validate velocity_cog.tif
```

(来源:gdal.org COG driver 文档、cogeo.org developers guide、cog-spec)

**静态金字塔替代方案(值得优先考虑)**:run 是一次性产物,`vips dzsave`(或 gdal2tiles)在
figure_journal 步骤一次性切好静态瓦片目录,FastAPI 只用 `StaticFiles` 托管——**零运行时依赖、
零动态计算**,与"产物即文件"的工作区模型最契合;代价是磁盘占用(约原图 1.3 倍)和
不能动态调色带/拉伸(InSAR 图件的配色本来就在 matplotlib 里定死,损失不大)。

### 1.9 图像对比交互(重点问题 4)

三种交互,从便宜到贵:

1. **卷帘(curtain/swipe)——近乎零 JS**。两张同网格图叠放,上层用
   `clip-path: inset(0 calc(100% - var(--pos)) 0 0)` 裁掉右侧,一个透明的
   `<input type="range">` 铺满容器,`oninput` 一行更新 `--pos`。天然支持拖拽、触屏、
   键盘(←/→/Home/End)、读屏(原生 range 语义),这是 2025-2026 各教程收敛出的最优解
   (effect-labs.com、dev.to、codefronts.com 均为此模式)。参考实现见骨架 C,~40 行。
   **前提**:两图必须同尺寸、同像素网格(见 1.10 matplotlib 对齐注意)。
2. **opacity 混合**:同样两图叠放,range 直接驱动上层 `opacity`,3 行搞定(骨架 C 附带)。
3. **并排同步 pan/zoom**:本质是"一份视图状态驱动 N 个画布"。在骨架 A 上改造:把
   `{scale, ox, oy}` 提出来共享,每个 canvas 的事件处理器都写同一份状态,然后重绘所有画布,
   增量 ~10 行。库方案参照:Leaflet.Sync(`mapA.sync(mapB)` 双向各调一次)、OpenSeadragon
   多 viewer 同步要点是 `zoomTo/panTo` 传 `immediately=true` + `constrainDuringPan: true`
   否则弹簧动画互相打架(OSD issue #1862);OSD 还有现成 curtain-sync 插件。

### 1.10 matplotlib 双档出图策略(重点问题 5)

**推荐:同一个 Figure 连续两次 `savefig`,只改 dpi。**

```python
fig.savefig(out / "velocity.png", dpi=600)          # 全尺寸(约 6000×4800 px,数 MB)
fig.savefig(out / "velocity.thumb.png", dpi=96)     # 缩略图(约 960×768 px,~100KB)
```

理由与坑:

- matplotlib 的版式(字号、线宽、colorbar 占比)以英寸为单位,改 dpi 只改像素密度,
  **缩略图与全图版式严格一致、文字相对大小不变**,可读性远好于事后像素缩放;
  Agg 后端二次渲染是秒级,成本可忽略。
- 事后缩放的替代:`matplotlib.image.thumbnail(src, dst, scale=0.15)`(内部走 Pillow)或
  `PIL.Image.thumbnail((640, 640), Image.LANCZOS)`——更快,但小图上细字发虚,只在
  "全图已存在、不想重渲染"时用。
- **`bbox_inches="tight"` 的坑**:裁边后的像素尺寸不可精确预知,且 pad 是固定英寸,
  两档的宽高比会略有差异。对 srcset 无所谓,但**卷帘对比的两张图必须像素对齐**:
  对比组图要固定 `figsize` + 固定 axes 位置(`fig.add_axes([...])` 或 constrained_layout,
  且不要用 tight 裁剪),colorbar 范围不同也会挤动轴区,务必用同一渲染参数一次性生成。
- 顺手输出一个 `manifest.json`(文件名、宽、高、caption、缩略图路径),前端画廊按清单渲染,
  避免运行时探测图片尺寸(`width/height` 属性防 CLS 也从这里来)。
- 双档天然对接 OpenSeadragon 的 `legacy-image-pyramid`(1.4 节):将来想要 deep zoom,
  加一档 2000px 中间档即可,不用切瓦片。

---

## 二、推荐路线

### 近期:零依赖三件套(建议本周期落地)

1. **后端**(改 `figures.py` 的出图脚本):每张图双档 PNG + `manifest.json`;
   FastAPI 挂 `StaticFiles` 或复用现有产物下载端点。
2. **前端画廊**:网格 `<img srcset>`(thumb 档)+ `loading="lazy"` + 固定宽高;
   点击进 `<dialog>` lightbox(骨架 B),lightbox 内嵌 Canvas 查看器(骨架 A)加载全尺寸档。
3. **对比页**:同网格双图用卷帘(骨架 C);跨 run 版本对比用并排同步(骨架 A 共享状态改造)。

合计新增 ~250 行前端代码 + ~20 行后端改动,0 新依赖,风险集中在 1.1 节列出的坑,均有对策。

### 远期:大栅格瓦片化(等真实需求出现再做)

触发条件:栅格超过 ~8000px 单边、或需要叠底图/多图层/像元级取值。

1. `smallbaselineApp` 的 geocode 产物 → MintPy `save_gdal.py` → GeoTIFF;
2. `gdal_translate -of COG`(引擎环境里 GDAL 现成);
3. 服务优先级:**静态金字塔(vips dzsave / gdal2tiles + StaticFiles)> rio-tiler 手写端点
   (~30 行)> titiler.core(~10 行但依赖面大)**;
4. 前端 vendor OpenSeadragon 单文件(自定义 tile source 或 DZI),继续零 npm。

---

## 三、五个重点问题速答

1. **Canvas pan/zoom 最小实现多少行?** 拖拽+滚轮缩放+高DPI ~50 行;含捏合 ~75 行(骨架 A);
   惯性 +20 行(建议先不做)。坑:高 DPI 位图尺寸、imageSmoothing 随缩放切换、wheel 的
   passive/deltaMode/ctrlKey 捏合三件套、锚点公式用状态重设而非变换累积、setPointerCapture +
   touch-action:none。
2. **画廊 + lightbox 无依赖模式?** `<dialog>.showModal()` 原生焦点陷阱(top layer + inert +
   Esc + 焦点还原),不手写 focus trap;keydown 处理 ←/→;`new Image().src` 预热相邻图;
   网格 lazy + 双档 srcset;`<a href>` 无 JS 退化。
3. **titiler/rio-tiler 集成成本?COG 怎么转?** titiler.core 挂 FastAPI ~10 行,真实成本是
   rasterio/GDAL 依赖链(官方 wheel 自带 GDAL,pip 可装,~23MB);更薄是 rio-tiler 手写端点
   ~30 行。COG:`gdal_translate in.tif out.tif -of COG -co COMPRESS=DEFLATE`(GDAL≥3.1),
   旧版 TILED=YES+COPY_SRC_OVERVIEWS=YES,校验用 `rio cogeo validate`。
4. **对比交互最小实现?** 卷帘 = clip-path + CSS 变量 + 透明 range(1 行 JS,骨架 C);
   opacity 混合 3 行;并排同步 = 共享一份 {scale,ox,oy} 驱动多画布(骨架 A +10 行)。
5. **matplotlib 双档策略?** 同一 Figure 两次 savefig(dpi=600 / dpi≈96),版式一致、成本秒级;
   卷帘对比图禁用 bbox_inches='tight' 并固定 axes 保证像素对齐;附带 manifest.json 供前端。

---

## 四、参考实现(可直接抄)

### 骨架 A:Canvas pan/zoom 查看器(ES module,约 75 行)

```js
// pan-zoom-viewer.js —— 零依赖 Canvas 图像查看器
// 用法:const v = attachViewer(canvasEl); v.load('/runs/xx/products/figures/velocity.png');
export function attachViewer(canvas) {
  const ctx = canvas.getContext('2d');
  const img = new Image();
  let scale = 1, ox = 0, oy = 0, dpr = 1;      // 屏幕坐标 = 图像坐标 × scale + (ox, oy)
  const ptrs = new Map();                       // 活跃指针 pointerId -> 上次位置
  let pinchD = 0;                               // 双指上一帧距离

  const rect = () => canvas.getBoundingClientRect();
  const fitScale = () =>
    Math.min(rect().width / img.naturalWidth, rect().height / img.naturalHeight);

  function draw() {
    ctx.setTransform(1, 0, 0, 1, 0, 0);         // 每帧重设,避免变换累积漂移
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    if (!img.naturalWidth) return;
    ctx.setTransform(scale * dpr, 0, 0, scale * dpr, ox * dpr, oy * dpr);
    ctx.imageSmoothingEnabled = scale <= 1;     // 放大保像素边界;缩小抗锯齿
    ctx.imageSmoothingQuality = 'high';
    ctx.drawImage(img, 0, 0);
  }
  function fit() {                              // 适配画布并居中(双击复位)
    scale = fitScale();
    ox = (rect().width - img.naturalWidth * scale) / 2;
    oy = (rect().height - img.naturalHeight * scale) / 2;
    draw();
  }
  function resize() {                           // 高 DPI:位图尺寸 = CSS 尺寸 × dpr
    dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(rect().width * dpr);
    canvas.height = Math.round(rect().height * dpr);
    img.naturalWidth ? fit() : draw();
  }
  function zoomAt(px, py, f) {                  // 以屏幕点 (px,py) 为锚点缩放 f 倍
    const s = Math.min(Math.max(scale * f, fitScale() / 2), 64);
    f = s / scale; scale = s;
    ox = px - (px - ox) * f;                    // 锚点不动:o' = p − (p − o)·f
    oy = py - (py - oy) * f;
    draw();
  }
  canvas.style.touchAction = 'none';            // 手势全部交给 Pointer Events
  canvas.addEventListener('wheel', (e) => {
    e.preventDefault();                         // 需 passive:false,阻止页面滚动/整页缩放
    const unit = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? 100 : 1;   // 行/页归一化
    const d = Math.max(-24, Math.min(24, e.deltaY * unit));  // 夹紧:滚轮±100+ vs 捏合±3
    const r = rect();                           // 触控板捏合 = ctrlKey:true 的 wheel,一并处理
    zoomAt(e.clientX - r.left, e.clientY - r.top, Math.exp(-d * 0.012));
  }, { passive: false });
  canvas.addEventListener('pointerdown', (e) => {
    canvas.setPointerCapture(e.pointerId);      // 拖出画布不丢事件
    ptrs.set(e.pointerId, { x: e.clientX, y: e.clientY });
    const p = [...ptrs.values()];
    if (p.length === 2) pinchD = Math.hypot(p[0].x - p[1].x, p[0].y - p[1].y);
  });
  canvas.addEventListener('pointermove', (e) => {
    if (!ptrs.has(e.pointerId)) return;
    const prev = ptrs.get(e.pointerId);
    ptrs.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (ptrs.size === 1) {                      // 单指/鼠标拖拽平移
      ox += e.clientX - prev.x; oy += e.clientY - prev.y; draw();
    } else if (ptrs.size === 2) {               // 双指捏合:距离比 = 缩放因子,中点为锚
      const [a, b] = [...ptrs.values()], r = rect();
      const d = Math.hypot(a.x - b.x, a.y - b.y);
      if (pinchD) zoomAt((a.x + b.x) / 2 - r.left, (a.y + b.y) / 2 - r.top, d / pinchD);
      pinchD = d;
    }
  });
  const lift = (e) => { ptrs.delete(e.pointerId); pinchD = 0; };
  canvas.addEventListener('pointerup', lift);
  canvas.addEventListener('pointercancel', lift);
  canvas.addEventListener('dblclick', fit);
  new ResizeObserver(resize).observe(canvas);   // 容器尺寸/跨屏 dpr 变化时重建位图
  img.onload = resize;
  return { load: (url) => { img.src = url; }, fit, draw };
}
```

> 并排同步改造:把 `{scale, ox, oy}` 提为外部共享对象,`draw()` 改为遍历重绘所有注册的
> canvas;两个查看器的事件都写同一份状态即可,增量 ~10 行。

### 骨架 B:网格画廊 + `<dialog>` lightbox(约 55 行)

```html
<!-- 画廊:双档 srcset,缩略档进网格;<a href> 提供无 JS 退化 -->
<div class="gallery" id="gal">
  <a href="figures/velocity.png">
    <img src="figures/velocity.thumb.png"
         srcset="figures/velocity.thumb.png 960w, figures/velocity.png 6000w"
         sizes="(max-width: 700px) 45vw, 220px"
         width="960" height="768" loading="lazy" decoding="async" alt="LOS velocity">
  </a>
  <!-- 其余图件同构,由 manifest.json 生成 -->
</div>
<dialog id="lb"><img id="lb-img" alt=""><p id="lb-cap"></p></dialog>
```

```css
.gallery { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 8px; }
.gallery img { width: 100%; height: auto; display: block; }
#lb { border: 0; padding: 0; background: #111; }
#lb::backdrop { background: rgb(0 0 0 / .78); }
#lb-img { max-width: 92vw; max-height: 86vh; object-fit: contain; display: block; }
#lb-cap { color: #ccc; margin: .4em .8em; font-size: 13px; }
```

```js
// lightbox.js —— showModal() 自带焦点陷阱/背景 inert/Esc 关闭/焦点还原,无需手写 trap
const dlg = document.getElementById('lb'), big = document.getElementById('lb-img'),
      cap = document.getElementById('lb-cap'),
      links = [...document.querySelectorAll('#gal a')];
let cur = -1;
const wrap = (i) => (i + links.length) % links.length;

function show(i) {
  cur = wrap(i);
  big.src = links[cur].href;
  cap.textContent = links[cur].querySelector('img').alt;
  new Image().src = links[wrap(cur + 1)].href;   // 预热相邻两张,切换零等待
  new Image().src = links[wrap(cur - 1)].href;
}
links.forEach((a, i) => a.addEventListener('click', (e) => {
  e.preventDefault(); show(i); dlg.showModal();
}));
dlg.addEventListener('keydown', (e) => {          // Esc 由 <dialog> 原生处理
  if (e.key === 'ArrowRight') show(cur + 1);
  else if (e.key === 'ArrowLeft') show(cur - 1);
  else if (e.key === 'Home') show(0);
  else if (e.key === 'End') show(links.length - 1);
});
dlg.addEventListener('click', (e) => { if (e.target === dlg) dlg.close(); });  // 点背景关闭
dlg.addEventListener('close', () => { big.src = ''; });
// 若要 lightbox 内 pan/zoom:把 <img id="lb-img"> 换成 <canvas> 并接骨架 A 的 attachViewer
```

### 骨架 C:卷帘对比 + opacity 混合(约 40 行)

```html
<!-- 卷帘:两图同栅格叠放,上层按 --pos 裁剪;透明 range 铺满容器承接拖拽/键盘/触屏 -->
<div class="cmp" style="--pos: 50%">
  <img src="figures/velocity_run1.png" alt="run1">
  <img class="top" src="figures/velocity_run2.png" alt="run2">
  <div class="bar" aria-hidden="true"></div>
  <input type="range" min="0" max="100" value="50" aria-label="卷帘位置"
         oninput="this.parentElement.style.setProperty('--pos', this.value + '%')">
</div>
```

```css
.cmp { position: relative; display: inline-block; max-width: 100%; }
.cmp img { display: block; max-width: 100%; }
.cmp .top {                                     /* 只露出左侧 --pos 的部分 */
  position: absolute; inset: 0;
  clip-path: inset(0 calc(100% - var(--pos)) 0 0);
}
.cmp .bar {                                     /* 分割线,纯装饰 */
  position: absolute; top: 0; bottom: 0; left: var(--pos); width: 2px;
  background: #fff; box-shadow: 0 0 4px rgb(0 0 0 / .6);
  pointer-events: none; transform: translateX(-1px);
}
.cmp input {                                    /* 原生 range:拖拽/←→/触屏/读屏全都白送 */
  position: absolute; inset: 0; width: 100%; height: 100%;
  margin: 0; opacity: 0; cursor: ew-resize;
}
```

```js
// opacity 混合变体:换掉 range 的 oninput 即可
// oninput="this.parentElement.querySelector('.top').style.opacity = this.value / 100"
// (此时删掉 .top 的 clip-path 与 .bar)
```

> 前提:两图同尺寸、同像素网格——出图时固定 figsize/axes、统一色标范围、
> 不要用 bbox_inches='tight'(见 1.10)。

---

## 五、主要来源

- OpenSeadragon:openseadragon.github.io(API/DZI/Image/Legacy tile source 示例)、
  github.com/openseadragon v6.0.2 release、issue #1489(legacy 大图白屏)、#1862(双 viewer 同步)
- libvips:libvips.org "Making image pyramids"(dzsave)
- Leaflet + georaster:npmjs.com/package/georaster-layer-for-leaflet(依赖表)、FOSS4G 演讲
- titiler / rio-tiler:developmentseed.org/titiler(Getting Started、包拆分公告、titiler.xarray)、
  cogeotiff.github.io/rio-tiler(XarrayReader)、pypi.org/project/rasterio(wheel 自带 GDAL)
- COG:gdal.org COG driver 文档、cogeo.org developers-guide、github.com/cogeotiff/cog-spec
- maplibre:maplibre.org GL JS 文档(v6 ESM/worker)、PR #7745(bundle 体积表)
- deck.gl:deck.gl/docs "Building Apps"(官方 bundle size 表)、BitmapLayer/TileLayer 文档
- PhotoSwipe:photoswipe.com(v5 ESM/体积)、npm registry(dist 文件尺寸)
- Canvas pan/zoom:stackoverflow.com/questions/5189968(scaleAt 公式与 setTransform 写法)、
  harrisonmilbradt.com canvas panning 教程、web.dev/articles/canvas-hidipi、
  MDN(devicePixelRatio、imageSmoothingEnabled、setPointerCapture、Pointer Events)
- wheel/手势:danburzo.ro/dom-gestures(deltaMode/pinch 全景)、tigerabrodi.blog(ctrlKey 捏合
  与 delta 夹紧)、MDN WheelEvent.deltaMode
- `<dialog>` 焦点陷阱:accessibility.build/guides/accessible-dialog、css-tricks.com
  "There is No Need to Trap Focus on a Dialog Element"(W3C APA 结论)、chyshkala.com、bbioon.com
- 惯性:ariya.io "JavaScript Kinetic Scrolling Part 2"、stackoverflow.com/questions/59008427
- CSS transform 重栅格化:developer.chrome.com/blog/re-rastering-composite、
  googlechrome.github.io will-change 示例、github.com/timmywil/panzoom
- 卷帘:effect-labs.com、dev.to(clip-path + range 模式)、codefronts.com
- srcset:MDN Responsive images、web.dev/learn/design/responsive-images、sushi.dev srcset 指南
- matplotlib:matplotlib.org(savefig/figure API、image_thumbnail 示例)
- 同步视图:github.com/jieter/Leaflet.Sync、github.com/locomo/openseadragon-curtain-sync
