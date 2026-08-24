# 54 · 预览/插件/接入模式波次执行记录(2026-08-24 补记)

> 本波次落地 52 号研究 §8「产品外壳与操作员路径」补勘,开发完成后曾长期停留在
> 工作区未入库;按 53 号格式补记范围与验收,并记录 2026-08-24 的收口审计与修复。

## 波次范围(与提交切分对应)

| 块 | 内容 | 文件属地 |
|---|---|---|
| P1 预览子系统 | 右栏 12 类格式预览(表格/Excel/JSON/时序/HDF5/MAT/GeoTIFF/SAR 二进制/文本/ZIP/PDF/SHP),窗口参数 clamp,路径钉在 run 工作区内(与 /api/artifact-file 同穿越口径) | `src/insar_agent/preview/**`、`api/preview_router.py`、`tests/test_preview_*.py` |
| P2 插件目录 | 声明式 YAML 查看器 catalog(18 内置)+ 装载器(只读 YAML,不 exec);仓库根 `plugins/` 放人类约定与示例副本 | `src/insar_agent/plugins/**`、`api/plugin_router.py`、`plugins/**`、`tests/test_plugins.py` |
| P3 接入模式与分级 | A(全链)/B(HyP3 云端)/C(位移产品直接分析)徽章与诚实空态;产品分级 Basic/Calibrated/Ortho(对标 EGMS,52 §8.3) | `api/access_mode.py`、`report/product_level.py`、`tests/test_access_mode.py`、`tests/test_product_level.py` |
| P4 桌面配套 | 预览弹窗、设置 IPC、worker 池、i18n settings 命名空间(zh/en 各 558 键零缺失) | `desktop/pi-app-overlay/**`(与真身经 sync 脚本 1:1) |
| P5 工具面 | `insar_read_skill` 步号扩到 1-11 ∪ 20-28(SkillStepParam,run-step 工具不放宽) | `pi-insar/src/tools.ts` |

## 2026-08-24 收口审计发现与修复

1. **overlay ↔ 真身漂移**:`sync-pi-app-overlay.ps1 -Check` 报 12 文件不一致;
   `git diff --no-index --ignore-cr-at-eol` 逐个核实全部为**纯 CRLF/LF 行尾漂移**,
   无真实内容分歧。已按侧归一化为 LF(10 个真身侧、2 个 overlay 侧),
   复检 55 文件全一致。
2. **ruff 门禁失效**:检查报 35 处 E501(exit 1),其中 5 处为本波次新增代码
   (`preview/{array_png,hdf5_view,shp_view}.py`、`tests/test_scenario_packs.py`)。
   新增 5 处已改写换行;存量 30 处按 pyproject 既定"告警清单"策略逐行
   `# noqa: E501` 机器化;`ci.yml` 增加 lint job,从此新增违规在 CI 拦截。
3. **中文 Windows 编码坑**:`tests/test_bridge_export.py` 子进程 `text=True`
   未钉 encoding,GMT 输出含非 GBK 字节时读线程抛 UnicodeDecodeError 警告;
   已补 `encoding="utf-8", errors="replace"`。
4. **依赖通告**:pip-audit 实跑仅 pip 25.3 自身 6 条 PYSEC(运行时依赖零通告);
   venv pip 已升 26.2.1 清零。注意:pip-audit 在中文 Windows 需 `PYTHONUTF8=1`。
5. **仓库卫生**:`pi-insar/exports/` 复现包 zip(二进制)进 `.gitignore`。

## 验收(2026-08-24)

- pytest 非时序全量:全绿 exit 0(改动前后各一轮;本地 Python 3.14)
- `ruff check src tests scripts`:All checks passed(exit 0)
- pi-insar:`npx vitest run` 8 文件 94 passed / 1 skipped;`npx tsc --noEmit` 零错误
- pi-app 真身:`npx vitest run` 190 文件 890 passed
  (冷启动首跑曾见 session-tree 用例 2 项 flaky,复跑两轮全绿,与本波次文件无关)
- `sync-pi-app-overlay.ps1 -Check`:55 文件全一致

## 仍未闭环(需外部条件,同 53 号)

PS 链真实实测(R1)、GNSS 站数据(R3 calibrated 级)、第二轨道数据(升降轨分解)。
