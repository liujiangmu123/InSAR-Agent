# 41 · 附录 A — 完成验收清单(从头顺序执行,全部通过 = 开发完美完成)

> 任何一条不过:停下,回到括号里标注的 Phase 修复,再从该条重跑。标 ⚠️ 的条目是重型计算,**必须先获用户明确批准**。

```powershell
# ============ 1. 静态与单元/集成(TS)============(Phase 01-09)
cd E:\01所有项目\06定职讲师\00insaragent\pi-insar
npx tsc --noEmit          # 期望:零错误
npx vitest run            # 期望:全绿;工具计数断言 = 31(含 memory 则 32)
#    覆盖:guard/mode/sidebar/provider/theme/journal/技能字数与工具全名点到/
#          集成后端上的 31 工具真实调用

# ============ 2. 后端全量(Python)============(Phase 09-15)
cd E:\01所有项目\06定职讲师\00insaragent
.venv\Scripts\python.exe -m pytest -q
# 期望:全绿。重点新增面:
#   注册表:core 恰 11 步 + analysis 9 步(20-28);group 过滤;分析参数进指纹
#   引擎:mintpy cfg 校正渲染 + 数据面逐字节回归;mintpy_post 命令构建;
#         passthrough 物化;predict 数学正确性与预测纪律;桥导出四格式
#   报告:数值可溯断言(报告中每个数字能对回 provenance)

# ============ 3. pi 链路冒烟 ============(Phase 01/02/04)
pi --version                                        # 0.84.2
pwsh scripts\insar-pi.ps1 --list-models             # 见 insar-llm/<chat_model>
#   交互开一次:主题为 insar-dark,侧栏出现,/quit 退出

# ============ 4. 真实后端健康 ============(Phase 03)
pwsh scripts\insar-backend-real.ps1                 # 窗口 A
curl.exe http://127.0.0.1:8873/api/health           # {"ok":true,...}
curl.exe http://127.0.0.1:8873/api/doctor           # mintpy 引擎 ok=true
curl.exe "http://127.0.0.1:8873/api/registry"       # 16 步(11 core + 5 analysis 首批)以上

# ============ 5. 真实数据端到端:读回面(轻型,无重型计算)============(Phase 05-08)
# 窗口 B:pwsh scripts\insar-pi.ps1,对真实 realtest HOME 逐项走查:
#   list_sessions 见 real;run_status:11 步 done、evidence=audited、simulated=false
#   timeseries_point(震中):数值与 curl 直查逐位一致
#   view_figure:内联显示真实速度场图;figure_caption:图注数字可溯
#   export_product(gtiff):产物在 QGIS/gdalinfo 可读且带地理参考
#   report(full):每个数值可对回 provenance;repro_bundle:manifest 哈希可验
#   strict 模式:自由 bash 被拦且给出引导;journal:本次操作已入台账外日志
#   run_trace:显示真实 run 的五阶段时间线

# ============ 6. 分析链端到端(轻型:分钟级,基于既有 audited run)============(Phase 10-14)
# pi 里自然语言:"基于 realtest 的 run,做掩膜后算区域平均速度,并出一张速度图"
#   期望:分析 run 规划出 20(register)→21(mask)→22(passthrough)→24(stats)→25(figure)
#   全部 done;measure.json 数值与 MintPy 直跑一致;图件 sidecar 完整
# pi 里:"预测这个点未来 6 个月的形变(给不确定度)"
#   期望:26/27 执行;prediction.json 带 ±1σ/±2σ 与 validity;回答含免责声明
# pi 里:"导出 GBIS 反演输入包"
#   期望:28 步产 .mat + README;scipy.io.loadmat 可读回

# ============ 7. ⚠️ 重型(须用户明确批准,二选一)============(Phase 03/16)
# .venv\Scripts\python.exe scripts\real_ridgecrest.py   # 基准复跑,期望 status=done
# 或 pi 内新会话 plan+execute 全链,期望 done + evidence=audited + run_ok 全真
# 升降轨分解(23 步 asc_desc)若无第二轨道真实数据:如实记 PENDING,不造数据

# ============ 8. 仓库状态 ============
git status                # 干净
# 00-README.md 进度表:Phase 01-16 全勾 + 提交号已填
```

## 判定

- 全部通过(重型条目获批执行或如实 PENDING)= **交付完成**。
- 第 5/6 节是"工业级"的直接证据:同一份真实数据,从查询、分析、出图、预测到反演交接与复现包,全程在 pi 对话内完成,且每个数字可溯源。
