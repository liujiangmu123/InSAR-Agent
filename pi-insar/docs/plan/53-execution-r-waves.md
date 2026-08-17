# 53 · R 波次执行分工(依据 52 号研究,2026-08-17)

> 规划/设计:Fable 5(主会话);开发:Grok 4.6 xhigh 子代理,分波并行。
> 纪律全文见 `01-execution-rules.md`;三条红线:禁假数据 / 科学步骤走 `insar_*` 闭集 / 重型计算须用户批准。
> 子代理一律**不做 git 提交**,由主会话统一验收提交。

## 波次与属地(避免同文件冲突的分区依据)

| 波 | 代理 | 任务 | 文件属地(只许动这些区域) | 验收 |
|---|---|---|---|---|
| A1 | 桌面品牌收尾 | 图标接线核验 + 用户可见 "pi" 残留清扫 + overlay 镜像 | `e:\SoftApp\pi-app\src\**`、`desktop/pi-app-overlay/**` | pi-app `npx vitest run`、`npx tsc --noEmit`(如有) |
| A2 | R1 PS 桥+交叉验证 | `engines/bridges/` 包实现(prep_isce + isce2_to_pystamps)+ qa.py crossval 分支 | `src/insar_agent/engines/bridges/**`、`engines/qa.py`、`engines/pystamps.py`、`tests/`(新增) | `.venv\Scripts\python.exe -m pytest -q` |
| A3 | 场景包扩展 | `teaching`(R6)+ `lt1_gamma`(R2c)两个场景包 | `src/insar_agent/registry/scenario_packs/**`、`tests/test_scenario_packs.py` | 同上(场景包相关测试) |
| B1 | 注册表扩展 | R2a nisar_import、R2b 模式 C(register_sources 吃 GeoTIFF/CSV)、R3 gnss_compare、R4 dolphin_ps_ds、R5-lite 产品分级标注 | `registry/capabilities.py`、`engines/passthrough.py`、`engines/`(新增 gnss/dolphin)、`report/**`、`tests/` | 同上(全量) |
| C | 主会话收口 | 全量测试 + overlay 一致性 + 提交 | — | pytest 全量 + pi-insar vitest/tsc |

依赖:A1/A2/A3 并行(属地互斥);B1 等 A 波全部完成后单独跑(capabilities.py 是共享热点,不并行)。

## 明确不做(52 §6 不做清单)

自造 GNSS 平差/形变源反演、DL 解缠上线、ISCE3 迁移、偏移量追踪自研;
本轮也不跑任何真实重型链(PS/MintPy/Dolphin 全链留待用户批准后另行实测)。

## 状态

- [ ] A1 桌面品牌收尾
- [ ] A2 R1 PS 桥 + crossval
- [ ] A3 teaching / lt1_gamma 场景包
- [ ] B1 注册表扩展(R2/R3/R4/R5-lite)
- [ ] C 收口验收(测试结果以实际运行为准,完成后回填)
