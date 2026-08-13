# 发布就绪审计(release readiness)— 2026-08-13

只读审计,零代码/配置改动。范围:当前工作树全文 + git 全历史(140 个提交的
`git log -p` 全量补丁扫描,非抽查)+ 依赖许可证核查(pyproject / requirements /
desktop Cargo + 外部引擎,许可证信息经网络检索一手来源核实)。

**结论先行(TL;DR)**:

1. **无凭据泄露**:全历史扫描 API key / token / 私钥 / AWS 凭据模式零命中,
   凭据纪律(env/netrc/不落盘)在代码与文档层面一致执行。可放心。
2. **个人信息 = 3 处路径 + 1 个 git 作者身份**:`E:\01所有项目\06定职讲师`
   类个人目录出现在 2 个文件(docs/OPTIMIZATION.md、scripts/real_ridgecrest.py)
   与 git 历史;全部 140 个提交作者为 `jiang <liujiang@cmo.com>`。
   公开前二选一:改写这几处后 squash 重置历史,或接受历史公开(见 §1.5/§3.4)。
3. **仓库自身无 LICENSE(核实属实)**:全仓无 LICENSE/COPYING;pyproject.toml
   与 desktop/Cargo.toml 均无 license 字段。当前状态 = 保留所有权利,
   他人法律上不可复制分发。发布前必须补。
4. **GPL 无传染**:MintPy(GPL-3.0-or-later)、PyAPS3(GPL-3.0+)、SNAPHU
   (Stanford 自定义 + CS2 严格非商用)全部经 **subprocess 命令行调用**、
   跑在用户自装的独立 conda/WSL 环境,项目不打包不分发它们;自有代码逐文件
   核实无引擎代码复制、无 `import mintpy`(库级)。**自有代码许可自由度完整**,
   Apache-2.0 / MIT / 闭源二进制均可行。推荐 **Apache-2.0**(§3.1)。
5. **最大分发红线在"引擎预打包"场景**:若将来把装好 ISCE2+SNAPHU 的 WSL
   镜像/离线包一起分发,才会触碰 SNAPHU"禁收费再分发"+ CS2"严格非商用"
   + GPL 源码义务 + ISCE2 EAR99 出口分类(§2.4)。当前"脚本引导用户自装"
   形态无此问题。

---

## 1. 敏感信息清单

### 1.1 A 级:个人目录/个人项目路径(发布前必须处理)

个人信息载体是目录名(`01所有项目\06定职讲师` 暴露个人事务分类与职业信息;
`InSAR-Pro` 暴露另一个未公开项目的存在与内部结构)。

| 位置(文件:行) | 内容 | 建议动作 |
|---|---|---|
| `scripts/real_ridgecrest.py:27` | `INSAR_HYP3_SOURCE` 默认值 = `E:\01所有项目\06定职讲师\InSAR-Pro\insar-pro\backend\data\real_data\RidgecrestSenDT71` | 删除默认值:改为环境变量必填,缺失时报错并提示如何配置(现已支持 env 覆盖,只需把 `setdefault` 的字面量去掉) |
| `docs/OPTIMIZATION.md:76` | "父目录 `E:\01所有项目\06定职讲师` 是大杂烩仓库" | 该节是 git 工作流决议的历史记录;公开版删除本行或改为"父目录是另一个无关仓库" |
| `docs/VALIDATION-isce2-wsl.md:11` | 数据来源 `E:\00kaifaxiangmu\InSAR-Pro\insar-pro\data\test_datasets\gmtsar_alos_baja_eq\extracted` | 改为 "GMTSAR 官方示例包 ALOS Baja EQ(本地解压目录)",保留数据集身份、去掉本机路径 |
| git 历史(3 处 blob) | 上述三行同样存在于历史提交补丁中 | 与 §1.5 的历史决策一并处理(改当前文件不会从历史消失) |

另有一类"提及 InSAR-Pro 项目"的内部对标内容(非路径):
`reference/AGENT_PRODUCTS_LEARNING.md`、`reference/COMPARISON_LEARNING.md`、
`docs/AGENT-DESIGN.md` 等多处引用 `InSAR-Pro/insar-pro/backend/app/engines/mintpy.py:290,556,...`
式的文件行号做 novelty 论证。路径对外不可解析、无泄密风险,但暴露一个未公开
前作的存在。**评估:保留有价值(设计决策的证据链),建议统一改称
"内部前作原型"并去掉盘符级路径,保留相对路径与行号。**

### 1.2 B 级:本机环境细节(发布前参数化/泛化;非隐私,是"公开品相"问题)

硬编码 `E:\miniforge3` / `E:\wsl` 这类作者机安装位,对公开用户是噪音甚至误导
(多数机器无 E: 盘)。以下按"代码(行为)"与"文档(描述)"分列。

**代码与脚本(影响行为,优先处理):**

| 位置 | 内容 | 建议 |
|---|---|---|
| `src/insar_agent/runtime/probe.py:109` | `_KNOWN_ENV_ROOTS` 首项 `E:\miniforge3\envs`(隐式 conda 扫描位;110-112 行的 `~/miniforge3`、`~/mambaforge`、`C:\miniforge3` 是通用项) | 移除 `E:\` 项或改由环境变量 `INSAR_CONDA_ROOTS` 追加;注释里的"本机 README 验证过的安装位"(107 行)与 158 行注释同步改 |
| `src/insar_agent/api/setup_router.py:45-46` | conda 探测首选 `E:\miniforge3\condabin\conda.bat` / `Scripts\conda.exe` | 同上:通用候选(`~/miniforge3`、`%LOCALAPPDATA%`、PATH)+ 可配置 |
| `src/insar_agent/api/setup_router.py:187,197,277,293` | 面向用户的引导文案写死 "建议安装到 E:\\miniforge3"、示例 prefix `E:\miniforge3\envs\insar` | 文案改占位符(如 `<conda 安装目录>\envs\insar`)或跟随实际探测结果渲染 |
| `scripts/real_ridgecrest.py:24` | `INSAR_ENGINE_PREFIX` 默认 `E:\miniforge3\envs\insar` | 默认值删除,交给 probe.py 的隐式探测(该机制已存在) |
| `scripts/agent_baja_rerun.py:64-65` | `DEFAULT_HOME = E:\wsl\baja_agent_home`、`DEFAULT_ENGINE_PREFIX = E:\miniforge3\envs\insar` | CLI 已可覆盖,把默认值改为必填参数或 `~/insar-agent-baja` 类中性路径 |
| `scripts/build_desktop.ps1:21` | 注释建议 `$env:CARGO_TARGET_DIR = 'E:\cargo-target-desktop-bundle'` | 改为"磁盘紧张时可将构建目录指向其他盘"的通用表述 |
| `desktop/.cargo/config.toml:5` | 注释:"本机 Windows 系统代理指向 127.0.0.1:7897(本地代理软件)" | 回环地址无泄密,但属本机细节;注释改为通用表述("显式禁用系统代理,避免继承失效代理")。另见 §4.1 rsproxy 决策 |

**文档(描述性,批量占位符化即可):**

| 位置 | 内容 |
|---|---|
| `README.md:116` | 验收环境 `E:\miniforge3\envs\insar`(另 131 行含 CPU 型号 i9-13900K——排障记录有技术价值,建议保留,措辞改"作者验证环境") |
| `docs/AGENT-DESIGN.md:59` | 宿主 `C:\Python314\python.exe`;`:849` `wsl --import Ubuntu E:\wsl\Ubuntu`(含"483 G 可用"本机磁盘信息) |
| `docs/WSL-SETUP.md:12,17,31` | `E:\wsl\insar` 落盘位;`.wslconfig` memory=40GB/processors=20/swap=16GB(本机硬件配置) |
| `docs/DESKTOP.md:49,94,191` | `C:\Python314\python.exe` 三级探测描述(**与实现不符**:`desktop/src/sidecar.rs` 实际探测链是 冻结exe→INSAR_PYTHON→.venv→PATH,无任何硬编码盘符——文档过时,顺手修正);94 行 `E:\...\00insaragent\.venv...` 示例 |
| `docs/INTEGRATION-setup.md:49,55,67,78,82,86,94,106-107,111` | 示例 JSON 里成片的 `E:\miniforge3`、`E:\...\RidgecrestSenDT71`(与 setup_router 行为联动,代码改完后同步) |
| `docs/VALIDATION-isce2-wsl.md:36,59` | `E:\wsl\baja_out\*.png`、`E:\wsl\baja_prep.sh`(实测记录,见 §1.4 处置原则) |
| `docs/RELEASE-CHECKLIST.md:65` | `E:\cargo-target-desktop-bundle`(同 build_desktop.ps1) |

### 1.3 C 级:确认无害(不必处理)

- `prototype/js/setup.js:222,226`——输入框占位示例 `D:\miniconda3\envs\insar-engine`、`E:\insar_data\hyp3`(通用示例)。
- `tests/test_engines.py:99,104`(`E:\data\Ridgecrest`)、`tests/test_wsl_backend.py:218-276`(`E:\ws\demo`、`c:\a b\c`)——测试 fixture,不指向真实资产。
- `docs/AGENT-DESIGN.md:37` `C:\Windows\system32\wsl.exe`——系统标准路径。
- `desktop/gen/schemas/*.json` 的 `:\` 命中——JSON 转义 `\n` 的正则误报。
- WSL 发行版名 `insar`、`INSAR_WSL_DISTRO` 默认值——产品约定,非个人信息。

### 1.4 凭据 / 身份 / 机器指纹扫描结论

对当前树全文与 140 个提交的全量补丁(约 4.4 MB 文本)执行了模式扫描:

| 模式 | 结果 |
|---|---|
| `sk-*` / `ghp_*` / `gho_*` / `AKIA*` / `xox[baprs]-` / `BEGIN * PRIVATE KEY` | **零命中**(当前树与全历史) |
| 邮箱 | 全历史仅 2 个,均在提交元数据、文件内容中零邮箱:作者 `liujiang@cmo.com`;大量提交带 `Co-authored-by: Cursor <cursoragent@cursor.com>` 尾注(AI 协作署名,见 §1.5) |
| 外部 IP | 零命中(全部为 127.0.0.1/localhost;唯一外露端口信息是本地代理 7897,见 §1.2) |
| 机器名 / 用户名(`DESKTOP-*`、`Administrator`、hostname) | 文件内容零命中 |
| 历史中被删除的文件 | 仅 2 个(旧 CI workflow、Rust 冒烟 bin),无敏感内容 |
| 代码中的 `credentials=...` | 均为 `{"earthdata": False, ...}` 布尔探测标志,非凭据 |

凭据纪律的正面证据(发布时可写进 SECURITY.md):`engines/hyp3.py` 明示
"凭据只从环境变量/`~/.netrc` 读取,命令行里不出现";`brain/provider.py` LLM key
只走 `INSAR_LLM_API_KEY` 环境变量,无默认端点;`tests/test_provider_http.py`
用本地 mock server "零真实 key";`docs/RELEASE-CHECKLIST.md` 明示 updater
"私钥不入库";`.gitignore` 含 `.env` / `*.key`。

### 1.5 git 作者身份(公开前必须决策)

全部 140 个提交:`jiang <liujiang@cmo.com>`。两个问题:
(a) 若这是占位邮箱,`cmo.com` 是他人持有的真实域名,公开等于把提交挂到别人
域上(GitHub 还会按邮箱错误归属头像/账号);(b) 若是真实邮箱则属个人信息。
另:多数提交带 `Co-authored-by: Cursor <cursoragent@cursor.com>` 尾注,
公开即披露"AI 协作开发"——保留是诚实署名(GitHub 会显示共同作者),
不想作为公开叙事则只有 squash/重写能移除。

选项(与 §1.1 的历史路径问题同解):

- **方案 A(推荐,若走公开 GitHub)**:公开时 **squash 成单个初始提交**
  ("Initial public release"),用正式的 GitHub noreply 邮箱;完整开发历史
  留在私有镜像。一并消灭历史中的 3 处个人路径与 140 条内部工作流提交语
  (提交语大量出现"用户实测反馈/三波 16 代理闭环"等内部协作细节,无泄密
  但非公开叙事)。代价:公开仓失去逐步演进史。
- **方案 B**:`git filter-repo` 重写作者邮箱 + 抹除 3 处路径,保留完整历史。
  代价:全部 commit id 变化,工作量与校验成本高于 A,收益仅是保留历史叙事。
- **校内分发(仅装置包/源码压缩包,不公开 git 仓)**:无需动历史,
  只需完成 §1.1/§1.2 的当前树清理。

### 1.6 reference/ 报告的本机细节评估(该留 vs 该脱敏)

逐文件核实:**reference/ 全目录无盘符路径**(此前疑似命中为 URL 正则误报),
本机细节仅两类:

| 内容 | 位置 | 评估 |
|---|---|---|
| "本机对境外源不稳"、"本机 git 默认走 127.0.0.1:7897 本地代理(未运行)" | `reference/AUDIT-deps-2026-08-12.md:33,41` | 回环地址,技术无害;建议脱敏措辞(去掉端口号,改"本地代理未运行导致 github 不可达"),或整段保留亦可接受 |
| "本机重型计算管控规则"的多处提法 | `AGENT_PRODUCTS_LEARNING.md:177`、`USAGE_GUIDE.md:200`、`RESEARCH-workflow-ui:266`、`RESEARCH-agent-ux:236,241` | 属工程纪律描述,**保留**——它解释了执行器限线程/降优先级等设计决策的动机,是文档资产不是隐私 |
| InSAR-Pro 前作的内部文件行号引用 | `AGENT_PRODUCTS_LEARNING.md`、`COMPARISON_LEARNING.md` 多处 | 见 §1.1 末段:保留内容、改称"内部前作原型"、去盘符 |

`docs/VALIDATION-isce2-wsl.md` 处置原则(它是实测报告,价值在教训不在路径):
数据集身份、事件参数、耗时表、三条教训、复跑指引的命令形态**全部保留**;
仅把 `E:\...` 前缀换成 `<DATA_ROOT>` / `<OUT_DIR>` 类占位符(§1.1/§1.2 已列行号)。

---

## 2. 许可证审计

### 2.1 项目自身:无任何许可声明(核实属实)

- 全仓(含子目录)无 `LICENSE` / `LICENSE.*` / `COPYING*`;
- `pyproject.toml` `[project]` 无 `license` 字段、无 License classifier;
- `desktop/Cargo.toml` `[package]` 无 `license` 字段(cargo 打包会告警);
- `README.md` 无许可章节;源文件无版权头(全仓无 `Copyright (c)` 声明)。

**法律现状:默认保留所有权利。** 校内分发口头授权勉强可行,任何形式的
公开(GitHub/网盘/安装包外发)都需要先补许可证。选型见 §3.1。

### 2.2 Python 依赖许可矩阵

**运行时依赖(会被 PyInstaller 冻结进桌面安装包 → 触发再分发义务):**

| 包 | 声明位置 | 许可证 | 兼容性/义务 |
|---|---|---|---|
| fastapi | pyproject dependencies | MIT | 保留版权与许可文本 |
| uvicorn | 同上 | BSD-3-Clause | 同上 |
| pyyaml | 同上 | MIT | 同上 |
| numpy | pyproject [raster] | BSD-3-Clause | 同上 |
| h5py | 同上 | BSD-3-Clause | 同上(其内嵌 HDF5 库为 BSD 式 HDF5 许可,The HDF Group) |

冻结包实际还会带入的关键传递组件(THIRD-PARTY-NOTICES 必须覆盖):
starlette(BSD-3)、pydantic / pydantic-core(MIT)、anyio(MIT)、
click(BSD-3)、h11(MIT)、idna(BSD-3)、sniffio(MIT or Apache-2.0)、
typing-extensions(PSF-2.0)、**CPython 运行时 python3xx.dll(PSF-2.0)**、
OpenBLAS(BSD-3,随 numpy)、OpenSSL 3.x libcrypto(Apache-2.0)。
全部许可兼容,义务统一为"随分发提供版权声明与许可文本"。

**开发/构建依赖(不进任何分发物,无再分发义务):**

| 包 | 许可证 | 备注 |
|---|---|---|
| pytest | MIT | dev |
| httpx | BSD-3-Clause | dev |
| hypothesis | **MPL-2.0**(文件级 copyleft) | dev-only 且 PyInstaller spec 已显式 `excludes=["hypothesis"]`(打包实测防混入)——无义务 |
| ruff | MIT | dev 工具 |
| pip-audit | Apache-2.0 | dev 工具 |
| pillow | HPND/MIT-CMU(宽松) | 仅 `desktop/icons/make_icons.py` 构建期用 |
| **pyinstaller 6.22.0** | **GPL-2.0-or-later WITH Bootloader-exception** | 例外条款明确:**用它打包的产物可按任意许可分发,不受 GPL 约束**。工具本身不进产物 |

### 2.3 Rust / 桌面壳许可矩阵

直接依赖(desktop/Cargo.toml,版本见 reference/AUDIT-deps-2026-08-12.md):

| crate | 许可证 |
|---|---|
| tauri 2.x | Apache-2.0 OR MIT |
| tauri-build | Apache-2.0 OR MIT |
| tauri-plugin-single-instance / global-shortcut / updater | Apache-2.0 OR MIT |
| rfd 0.15 | MIT |
| serde / serde_json | MIT OR Apache-2.0 |

传递依赖共 536 crate(Cargo.lock):Rust 生态以 MIT/Apache 双许可为绝对主流,
**已知无 GPL crate 进入 Windows 目标依赖图**。两点说明:

- Cargo.lock 里的 glib/gtk 家族(LGPL 绑定)是 **Linux 目标专属**(lockfile
  平台无关全量解),当前只出 Windows 产物,不被编译不被分发;将来若出 Linux
  包,webkit2gtk/GTK 属系统库动态链接,LGPL 动态链接不传染,亦无障碍。
- 发布前用 `cargo-about` 或 `cargo-license` 生成全量清单归档(§4.5),
  代替人工核对 536 项。

**打包器与运行时**:NSIS(zlib 许可,产物安装包无义务);WebView2 运行时经
`downloadBootstrapper` 在用户机安装时由微软渠道下载,**项目不再分发**,
无许可负担(比 embed 模式干净)。

### 2.4 外部引擎(不打包、不分发,仅命令行编排)——重点核查项

**架构事实(GPL 分析的根基,逐文件核实):** `engines/` 全部模块只做
"渲染配置文件 + 构建 argv"(`mintpy.py` 生成 cfg 后调
`python -m mintpy.cli.smallbaselineApp`;`isce2.py` 渲染 XML 后调
`stripmapApp.py/topsApp.py`;`snaphu.py` 生成 conf+shell 循环调 `snaphu`;
`bridges/prep_isce.py` 调官方 `prep_isce.py`/`prep_hyp3.py` CLI)。
引擎跑在用户自装的 conda/WSL 环境(`scripts/wsl_setup.sh` 引导从
conda-forge 镜像安装),**仓库内无一行引擎源码拷贝,宿主代码无库级
`import mintpy/pyaps/isce`**;生成的 qa/figures 脚本仅 import
matplotlib(Matplotlib License,BSD 式)/ h5py / numpy / cmcrameri(MIT)。

| 引擎 | 许可证(已核实一手来源) | 对本项目的约束 |
|---|---|---|
| **ISCE2** | **Apache-2.0**(Copyright Caltech;LICENSE 附 **EAR99 NLR** 美国出口管制分类声明:对禁运国家/受限最终用途需许可) | 不分发则零义务。注意:isce2 源树 contrib 含 Stanford 许可组件(snaphu 绑定等),官方在 PR #313 中自认 CS2 再分发存在问题——这是**上游与 conda-forge 的责任**,不是本项目的;但"预打包镜像"场景会把责任拉到自己身上(见下) |
| **MintPy 1.6.x** | **GPL-3.0-or-later**(Yunjun & Fattahi) | subprocess 调用 = GPL FAQ 认可的独立程序聚合,**不传染**;唯一红线:不得把 MintPy 代码复制进仓库、不得进程内 import 后再以 GPL 不兼容许可分发组合物 |
| **PyAPS3** | **GPL-3.0+**(insarlab) | 同上(且仅被 MintPy 间接调用) |
| **SNAPHU 2.x** | **Stanford 自定义许可**:允许任意目的使用/修改/分发,须保留版权与许可声明,**禁止对再分发收费**;其内嵌 **CS2 MCF 求解器(IG Systems 版权)仅限严格非商用/评估用途**,商用需向 igsys 购买许可 | 不分发则零义务(用户经 conda-forge 自装)。**snaphu_mcf 方法正是走 CS2 初始化**——商用部署场景要意识到这层 |
| GDAL | MIT | 无 |
| pysolid(MintPy 依赖) | GPL-3.0 | 同 MintPy |
| asf_search / hyp3_sdk | BSD-3-Clause(ASF) | 无 |
| cmcrameri(科学色标) | MIT(色标数据同源;论文中引用 Crameri 是学术规范,非许可义务) | 无 |
| snaphu-py(conda-forge `snaphu` 包) | (Apache-2.0 OR BSD-3) AND **LicenseRef-SNAPHU**(下层 C 代码仍是 Stanford 条款) | 同 SNAPHU |

**"引擎预打包"红线清单**(当前不适用;若将来分发装好引擎的 WSL 镜像 /
离线 conda 包 / all-in-one 安装器,逐条核对):

1. SNAPHU:分发物**不得收费**(哪怕成本费,Stanford 条款写明 "no fee is
   charged for further distribution");保留 README 版权声明。
2. CS2:仅严格非商用场景可含 MCF 组件;商业化产品须用 `-D NO_CS2` 构建
   (失去 MCF 初始化)或购 IG Systems 许可。
3. MintPy/PyAPS3(GPL-3.0):随分发提供许可文本 + 对应源码(或书面源码
   提供承诺;指向 conda-forge feedstock 通常可接受,严格做法是自留源码副本)。
4. ISCE2(EAR99 NLR):面向禁运国家/实体的分发需出口合规评估——校内分发
   无碍,公网分发建议在 README 转述该分类声明。
5. Miniforge/conda-forge 包本身:BSD-3 与各包许可,镜像批量再分发注意
   Anaconda ToS 不适用于 conda-forge(社区渠道),但整镜像重打包属于
   "redistribution of packages",逐包许可义务仍在。

### 2.5 数据与文献(模型/场景包)

| 数据/内容 | 条款 | 评估 |
|---|---|---|
| Sentinel-1 / HyP3 干涉产品 | Copernicus 数据条款(免费、可再分发、须标注 "contains modified Copernicus Sentinel data [year]");HyP3 产品免费公开 | 项目不内置数据,仅编排下载/导入,零风险;报告模板建议自动带上述标注(methods.py 可加) |
| ERA5(PyAPS 大气校正) | Copernicus C3S 许可(免费注册,使用须标注) | 同上;场景包"ERA5 缓存"是用户本地文件,不入库不分发 |
| ALOS PALSAR(Baja 验证数据) | ©JAXA/METI,经 ASF 分发协议提供;**原始数据不得再分发** | 验证数据未入库(核实:.gitignore 拦 data/、历史无大二进制)✓;保持现状,**永远不要把 IMG/LED 文件提交进仓库** |
| GMTSAR 示例包 | 教学示例数据(内含 ALOS 原始数据,同上条约束) | 文档引用其名称与来源,合规 |
| 场景包/contract.yaml 引用文献 | 引用形态 = 参数值 + 作者-年份-期刊-章节出处(如 "Berardino et al. 2002 §V" 一句带引号短引文) | **合理使用的教科书形态**:事实性数据不受版权保护,短引文有完整归属;无需任何许可动作。保持"引用均按一手来源核对"的纪律即可 |
| 竞品调研报告(reference/) | 引用竞品代码的文件名/行号/机制描述,未成段复制源码 | 无版权风险;开源竞品名称与结论属评论性使用 |

### 2.6 THIRD-PARTY-NOTICES 草案

发布时建议新建 `THIRD-PARTY-NOTICES.md`,骨架如下(<> 处发版时用工具生成
精确清单:Python 侧 `pip-licenses --with-license-file`,Rust 侧 `cargo-about`):

```markdown
# Third-Party Notices

insar-agent 桌面安装包内含以下第三方组件(按许可证分组;完整许可文本见
licenses/ 目录):

## Python 运行时(PyInstaller 冻结)
- CPython <版本> — PSF-2.0 — © Python Software Foundation
- FastAPI — MIT © Sebastián Ramírez;Starlette — BSD-3-Clause © Encode OSS
- Uvicorn — BSD-3-Clause © Encode OSS;h11 — MIT;click — BSD-3-Clause
- pydantic / pydantic-core — MIT © Samuel Colvin 等
- PyYAML — MIT © Ingy döt Net & Kirill Simonov
- NumPy — BSD-3-Clause © NumPy Developers(内含 OpenBLAS — BSD-3-Clause)
- h5py — BSD-3-Clause © Andrew Collette 等(内含 HDF5 — © The HDF Group)
- anyio — MIT;idna — BSD-3-Clause;sniffio — MIT/Apache-2.0;
  typing-extensions — PSF-2.0;OpenSSL — Apache-2.0
- <pip-licenses 全量清单补齐>

## 桌面壳(Rust)
- Tauri 2 及官方插件(single-instance / global-shortcut / updater)
  — Apache-2.0 OR MIT © Tauri Programme within The Commons Conservancy
- rfd — MIT;serde / serde_json — MIT OR Apache-2.0
- 全部 crate 依赖清单及许可文本:<cargo-about 生成页>
- 安装器由 NSIS(zlib 许可)构建;WebView2 运行时由 Microsoft 分发渠道
  在安装时下载,遵微软许可条款,本包不包含其二进制。

## 本产品不包含但可编排调用的外部软件(由用户自行安装)
insar-agent 通过命令行调用下列独立程序,不包含、不分发其任何部分;
各软件版权与许可归其作者,用户应遵守相应条款:
- ISCE2 — Apache-2.0 © California Institute of Technology(出口分类 EAR99 NLR)
- MintPy — GPL-3.0-or-later © Zhang Yunjun, Heresh Fattahi 等
- PyAPS3 — GPL-3.0+ © insarlab;pysolid — GPL-3.0
- SNAPHU — © Board of Trustees, Leland Stanford Jr. University
  (自定义许可;内嵌 CS2 求解器 © IG Systems, Inc.,严格非商用)
- GDAL — MIT © Frank Warmerdam 及 GDAL 贡献者
- asf_search / hyp3_sdk — BSD-3-Clause © Alaska Satellite Facility
- cmcrameri / Scientific colour maps — MIT © Fabio Crameri

## 数据源声明
- 处理 Sentinel-1 数据的产物包含 modified Copernicus Sentinel data。
- ERA5 数据 © ECMWF,经 Copernicus Climate Change Service 提供。
- ALOS PALSAR 数据 © JAXA/METI,经 ASF 提供,不可再分发原始数据。
```

---

## 3. 发布形态建议

### 3.1 自有代码许可选型:推荐 Apache-2.0(MIT 为可接受备选)

推荐 **Apache-2.0**,理由按权重排序:

1. **显式专利授权 + 专利报复条款**:科研工程软件的算法实现(指纹/失效传播/
   证据阶梯这类可能被论文化的机制)给机构用户与校内推广更强的法律确定性;
   MIT 对专利只字未提。
2. **生态一致性**:ISCE2、snaphu-py、dolphin 等 isce-framework 系全是
   Apache-2.0;同许可减少下游组合的心智负担,也向该社区示好。
3. **NOTICE 机制**:Apache-2.0 §4(d) 的 NOTICE 文件正好承载 §2.6 的第三方
   声明与项目署名,分发义务的执行有章可循。
4. **单向兼容 GPL-3.0**:哪怕将来某个组件不得不进程内组合 GPL 代码
   (例如直接 import MintPy 写桥),Apache-2.0 源文件可并入 GPL-3.0 组合物
   分发,自有文件许可不用换。MIT 也满足这一条,GPL-2.0-only 场景才有差异。
5. 贡献条款(§5:提交即按本许可授权)内置,小项目可不另立 CLA/DCO。

选 MIT 的唯一实质理由是"最短最熟"。两者对"校内分发 + 未来开源 + 保留
商业化可能"都够用;**不建议 GPL/AGPL**(会限制未来桌面版闭源分发选项,
且本项目定位是基础设施,宽松许可更利采用)。

落地三件套:根目录 `LICENSE`(Apache-2.0 全文)+ `NOTICE`(一行版权 +
指向 THIRD-PARTY-NOTICES)+ `pyproject.toml` 补 `license = "Apache-2.0"`、
`desktop/Cargo.toml` 补 `license = "Apache-2.0"`。源文件头不必逐个加
(Apache-2.0 不强制),入口文件与 README 声明即可。

### 3.2 源码开源 vs 仅二进制:约束对比

**自有代码的许可自由度是完整的**(§2.4 论证:引擎外置 subprocess、桌面包
内组件全部宽松许可、PyInstaller 有 bootloader 例外),因此两条路都通:

| 形态 | 义务与约束 | 备注 |
|---|---|---|
| **源码开源(推荐)** | 补 LICENSE/NOTICE/THIRD-PARTY-NOTICES;完成 §1 清理 | 与"可复现科学工作流"的项目气质自洽;审计本身(provenance、诚实降级)是卖点,开源放大可信度 |
| **仅二进制(NSIS 安装包)分发** | 同样要附 LICENSE(给用户的使用授权)+ THIRD-PARTY-NOTICES(冻结组件的再分发义务照样触发);SmartScreen 无签名警告(检查单已知) | 合法可行;但注意 PyInstaller onedir 的 .pyc 极易反编译,"仅二进制"不构成实质源码保护 |
| 二进制 + 引擎预打包 | 触发 §2.4 红线清单(snaphu 禁收费/CS2 非商用/GPL 源码义务/EAR99) | 校内免费学术场景可做,须附全套声明;对外或收费场景不要做 |

### 3.3 模型/数据层

项目不内置任何权重或数据集(Brain 是外接 OpenAI 兼容 API,
场景包是自写知识文本 + 规范文献引用),无独立的模型/数据许可负担。
场景包文献引用维持现状即可(§2.5)。

### 3.4 历史与身份的发布决策(与 §1.5 呼应)

公开 GitHub → **方案 A(squash 单提交)**:一次性解决 3 处历史路径、
作者占位邮箱、140 条内部提交语三件事,成本最低;`git log` 类内部证据
(如 REVIEW 报告引用的提交号)在公开仓语境下本来也失效。
校内分发 → 不动历史,只清当前树。

---

## 4. 发布检查单

### 4.1 仓库卫生

- [ ] **历史体积:无需清洗**。全历史最大 blob = `desktop/icons/icon.ico`
  342 KB,仓库对象总量 4.02 MiB,140 提交/271 个跟踪文件;无大二进制、
  无数据文件混入历史,不需要 BFG/filter-repo 做体积治理。
- [ ] 删除跟踪中的备份文件:`docs/AGENT-DESIGN.bak`(64 KB,与 .md 并存)、
  `prototype/v2-backup/index.v2.html`(93 KB 旧版 UI)。历史中可留
  (squash 方案下自然消失)。
- [ ] `desktop/gen/schemas/*.json`(4 个,Tauri 构建自动生成,累计 ~300 KB):
  加入 .gitignore 或保留均可,建议移出版本控制减少 diff 噪音。
- [ ] `.gitignore` 补 `.hypothesis/`(hypothesis 测试库的示例数据库目录,
  当前为空所以状态干净,跑过 fuzz 测试后会出现)。
- [ ] `desktop/.cargo/config.toml` 的 rsproxy 镜像决策:公开仓会让海外
  贡献者默认走中国镜像。建议:删除该文件、把镜像配置写进
  `docs/DESKTOP.md` 的"国内网络"小节(CI 已经用 `--config` 覆盖回官方源,
  删除后 CI 反而更简单);`scripts/wsl_setup.sh` 的清华镜像同理——
  改成 `APT_MIRROR`/`CONDA_FORGE_MIRROR` 变量可覆盖(现已是变量,
  加一层 env 透传即可),默认值是否留镜像由目标受众定(校内分发留,
  国际开源换官方源+文档给镜像选项)。
- [ ] git 作者身份与历史:执行 §3.4 决策。
- [ ] 发版例行:`pip-audit` + `cargo audit`(或 OSV 等价扫描)复扫
  (流程已在 reference/AUDIT-deps-2026-08-12.md §3,纳入检查单固化)。

### 4.2 敏感信息清理(汇总 §1 的动作项)

- [ ] A 级 3 处个人路径:`scripts/real_ridgecrest.py:27`、
  `docs/OPTIMIZATION.md:76`、`docs/VALIDATION-isce2-wsl.md:11`。
- [ ] B 级参数化:`probe.py:109`、`setup_router.py:45-46,187,197,277,293`、
  `real_ridgecrest.py:24`、`agent_baja_rerun.py:64-65`、
  `build_desktop.ps1:21`、`desktop/.cargo/config.toml:5`。
- [ ] B 级文档占位符化:README:116、AGENT-DESIGN.md:59,849、
  WSL-SETUP.md:12,17,31、DESKTOP.md:49,94,191(顺带修正与 sidecar.rs
  实现不符的过时描述)、INTEGRATION-setup.md 示例、
  VALIDATION-isce2-wsl.md:36,59、RELEASE-CHECKLIST.md:65。
- [ ] InSAR-Pro 前作引用统一改称(§1.1 末段)。
- [ ] `reference/AUDIT-deps-2026-08-12.md:41` 本地代理表述脱敏(可选)。

### 4.3 README 公开化改造点

- [ ] 徽章接线:`README.md:4` 的 `OWNER/REPO` 占位符换真实仓库路径
  (ci.yml / desktop.yml 已就位,推上去即点亮);加 License 徽章;
  可选加 desktop.yml 构建徽章。
- [ ] 新增 License 章节(Apache-2.0 声明 + 指向 THIRD-PARTY-NOTICES +
  外部引擎"不包含、需自装、遵各自许可"一句话 + Sentinel 数据标注义务提示)。
- [ ] "快速开始"补前置条件小节:Python ≥3.11;引擎功能需 conda 环境
  (指向 WSL-SETUP.md);无引擎也可跑模拟模式(现有卖点,放显眼处)。
- [ ] 本机化措辞泛化:`E:\miniforge3\envs\insar`(116 行)改"作者验证环境";
  "215 项测试(2026-08-12 全绿)"这类快照数字改由 CI 徽章背书,
  避免公开后数字漂移。
- [ ] 面向国际受众(可选):英文 README 或顶部一段英文摘要;
  仓库描述/topic 规划(insar, sar, workflow, reproducibility, agent)。
- [ ] "当前边界(诚实声明)"章节保留——这是项目公信力资产。

### 4.4 新增治理文件

- [ ] `LICENSE`(Apache-2.0 全文,版权行:`Copyright 2026 <署名主体>`——
  个人真名/笔名/课题组名,发布前定夺,与 git 身份决策联动)。
- [ ] `NOTICE`(Apache-2.0 机制:项目名 + 版权行 + "includes third-party
  software, see THIRD-PARTY-NOTICES.md")。
- [ ] `THIRD-PARTY-NOTICES.md`(§2.6 草案 + 工具生成的全量清单)。
- [ ] `CONTRIBUTING.md` 模板要点:开发环境(venv + `pip install -e ".[dev]"`)
  ;门禁三件套(pytest / `scripts/test_js.py` / ruff,与 ci.yml 对齐);
  pre-commit 安装(配置已在仓);提交规范(现仓是中文提交语,公开后
  中/英文政策明示);"引擎相关改动需声明验证环境"(呼应可复现契约);
  PR 须过 CI 三 job。
- [ ] `SECURITY.md` 模板要点:支持版本表;私下报告渠道(GitHub Security
  Advisories 或邮箱);范围声明——本服务默认绑定 127.0.0.1 **无鉴权**,
  不设计为公网暴露(这一条务必写,是本项目真实的威胁模型边界);
  凭据处理声明(env/netrc,不落盘,§1.4 的纪律成文)。
- [ ] `CITATION.cff`(科研软件,给校内引用与将来 JOSS 类投稿铺路)。
- [ ] `pyproject.toml` 补 `license`、`authors`、`urls` 字段;
  `desktop/Cargo.toml` 补 `license`/`authors`;`tauri.conf.json` 的
  `publisher`("InSAR-Agent 项目组")与署名主体统一。

### 4.5 CI 增强(现状已好,增量小)

- [ ] 现状核实:ci.yml(python 双平台矩阵 + js + rust clippy `-D warnings`)
  与 desktop.yml(tag 触发 release 构建)结构完善,无 secrets 依赖,
  可直接公开;首推后按真实日志微调超时(文件头已自注)。
- [ ] 增加许可合规步骤(低成本高价值):Rust job 加 `cargo-deny check licenses`
  (拦 GPL/未知许可 crate 混入);Python job 加 `pip-licenses --fail-on GPL`
  类断言(dev 依赖排除)。
- [ ] 发布产物流程:desktop.yml 目前只出裸 exe 存档;正式发版沿用
  RELEASE-CHECKLIST.md 的本地 NSIS 流程,Release 页记得附 LICENSE 与
  THIRD-PARTY-NOTICES(NSIS 安装包内也放一份,tauri bundle resources 可带)。
- [ ] 徽章:README 已有 ci 占位;推荐补 license 徽章;
  覆盖率/版本徽章可选,不阻塞。

### 4.6 桌面分发附加项

- [ ] 安装包内嵌 LICENSE + THIRD-PARTY-NOTICES(bundle.resources 加一条映射)。
- [ ] 无代码签名的 SmartScreen 影响声明(RELEASE-CHECKLIST §5.2 已列,保持)。
- [ ] updater 若启用:`latest.json` 托管域与公钥固化;私钥离库保管
  (现纪律已明示,保持)。

---

## 附:本次审计的扫描方法(可复跑)

- 当前树:ripgrep 全文,模式含个人路径(`01所有项目|06定职讲师|00kaifaxiangmu`)、
  盘符路径(`[A-Za-z]:\\`)、邮箱、IP、凭据特征
  (`sk-|ghp_|gho_|AKIA|xox[baprs]-|BEGIN.*PRIVATE`)、机器名(`DESKTOP-*` 等)。
- git 历史:`git log -p --all --stat` 全量导出(cmd 重定向保 UTF-8 原始字节,
  PowerShell `>` 会转码破坏中文匹配)后对补丁全文跑同一组模式;
  `git rev-list --objects` + `cat-file --batch-check` 排大 blob;
  `git log --diff-filter=D` 列历史删除文件;作者身份 `git log --format='%an %ae'`。
- 许可证:pyproject/requirements/Cargo.toml 逐项 + 网络检索一手来源
  (ISCE2 LICENSE 原文、SNAPHU 官方 README 原文、MintPy/PyAPS pyproject、
  conda-forge feedstock 许可标签、isce2 PR #313 维护者关于 CS2 的表态)。
