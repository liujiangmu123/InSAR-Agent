# 深测波次跟踪清单(2026-08-12)

## 第三波(16 代理,晚间)闭环记录

- UI-DETAILS-AUDIT #1-4 全部闭环:审计面板消费服务端证据链、files/report 面板
  真实化(/api/artifacts、/api/methods.md)、产物 chip 跳转联动。
- state.js 两处中危 + 空值校验 + params 镜像 → 修复(JS 测试 46→60→76+)。
- REVIEW-r2 五项接缝 P1 → 主线全部修复(5a58fd4,4 项回归锁;P1-5 租约自停
  的确定性测试难构造,语义注释说明)。
- 回合三条含混语义(末步 steer/done 空转/待重跑明示)→ 设计落地。
- contract.yaml:min_coherence 与 max_temporal_baseline 解除 PENDING(5→3)。
- 工程化:CI 三 job + ruff + pre-commit + 依赖审计零漏洞;首个 NSIS 安装包
  实测闭环(28.9MB,装/起/卸全绿)。
- 新增能力:时序点查询(点图出曲线)、灯箱对比四模式、依赖轨道/失效
  popover/重跑影响确认、桌面通知与会话分组、工具卡聚合、排队 chip。
- Baja 代理编排:预检 25 项全绿,--launch 待用户批准(33 分钟重型计算)。
- 遗留(下一波):REVIEW-r2 的 14 项 P2;done-run-可含-pending 已由 W8 决策三
  处理(收尾明示);ISCE3 路线图按 docs/ROADMAP-isce3.md 分期;~~WSL isce2
  2.6.3→2.6.5 升级~~(✅ 2026-08-13:实测现网已是 2.6.5,wsl_setup.sh 已钉定
  isce2=2.6.5,快照见 docs/WSL-SETUP.md §3.3);正式图标/签名/updater 密钥
  (RELEASE-CHECKLIST)。

## 20 代理波次(傍晚)闭环记录

- 清单 #6/#7/#8(jobs TOCTOU/尾行冲刷/wrapper 显式化)与 WSL P2 组(keepalive
  释放/失败分诊/判活节流)→ 执行层清仓分支全部修复。
- fuzz 波次两缺陷(session 孤代理 500、NaN 体 500)→ 主线修复,xfail 已翻正。
- a11y 遗留 P1(深色主题语义底色白字徽章)→ 主线修复(--text-inv)。
- 浏览器实测 P2-002/P2-003(对勾遮挡/失败态弱)→ 失败卡分支修复。
- 终验新发现并修复:目录型 FIGURE 产物(products/figures)不被 /api/figures
  枚举 → 画廊对真实链空转;已支持目录成员枚举 + file 子参数(带越界防护)。
- 仍开放:UI-DETAILS-AUDIT 的 1-4(审计面板消费服务端 evidence、files/report
  面板真实化、产物 chip 联动)与 T1 报告的 state.js 两处中等缺陷
  (syncServerSteps 清脏标记语义、workSummary 桶优先级)。

合并波次结束后统一处理;来源:浏览器 UI 实测 + 沙箱数据库取证 + 各分支报告。

## 已修(合并波次中由主线处理,见 driver/store/ledger 同日提交)

- REVIEW P1-1 reattach 形同虚设:execute 待跑集合补 `running`,接回语义生效。
- REVIEW P1-2 溯源跨 run 污染:pending_actions 加 run_id 列(迁移),账本只导出
  归属本 run 的干预。
- REVIEW P1-3 next_run 覆写静默丢失:有 overrides 时强制重新规划,不再复用旧 run。
- REVIEW P1-4 并发无锁:execute 加 run 级租约守卫(60s 心跳续租,崩溃可接管),
  并发执行回合被拒绝而非 StageConflict 崩流。
- 清单 #9 执行期事件断流:executor 细节事件经 pump 队列泵入回合 NDJSON 流,
  契约测试断言已反转。
- 清单 #10 干预跨 run 互吞(store/driver 侧):due/consume_actions 支持 run_id
  过滤(NULL 兼容旧行),driver 全部消费点已传 run_id。**API 层 push_action 尚未
  传 run_id(等 API 分支合并后接线)**。

## P1(均已闭环,2026-08-12 晚)

1. ~~引擎宿主可用性混淆~~ **已闭环**:深入取证推翻了最初假设——后端计划本就把
   第 6 步标 skipped(云端已完成),真正根因是**前端 S.steps 停留在 mock 种子
   状态**,把种子里"失败"的第 6 步塞进显式执行列表([6,7,...,11],工具卡标题
   [06/11] 佐证),后端对显式列表又不过滤 skipped。已修两边:执行器防御显式
   skipped 重跑(`58ce61c`/`e3c7d7e`),前端回合后同步服务端步骤镜像。浏览器
   复测通过:审批卡显示 [1,7,8,9,10,11],全链 done,刷新后徽标 11✓。
   另:WSL 接线合并后,probe(wsl) 与执行后端已一致(snaphu 真会路由到 WSL)。
2. ~~右侧面板内容区 0x0~~ **不复现**:桌面视口(1680px)下九个面板全部可见
   (轨迹 377×450 服务端真数据、终端显步骤日志、环境 362×1277)。根因判定:
   实测环境是 IDE 内嵌浏览器约 375px 宽 → 触发窄屏响应式(dock 隐藏/浮层),
   量到的是隐藏面板。遗留 P3:窄屏模式下面板可达性可再打磨。

## P2(浏览器实测,未处理)

3. 确认执行后的大号对勾遮挡执行流卡片(P2-002)。
4. 失败态呈现弱:标题变"失败"但主界面仍是对勾,错误详情藏在底部小按钮,
   缺显眼的错误卡片 + 日志入口(P2-003)。
5. DOM 频繁重渲染导致元素引用失效(自动化/快速操作体验,P2-001)。
5b. 环境面板混有静态演示数据(envdata.js:"WSL2 未安装发行版 · 2026-08-10
    实测"与真实环境不符);模拟引擎产物过 runok 时报"artifact 无法解析,跳过
    NaN 检查"warn(合成产物非真 h5,诚实降级,但可让 simulated 模式跳过该检查)。

## P1(WSL 接线代理发现,api/ 范围未动)

13. **admin 外部终结对 WSL 作业判活错误**:`api/admin_router.py` 默认用
    LocalJobBackend 判活,WSL 作业无本地 `job.hb` 会被误判 orphaned(合成结算
    但不 touch `job.cancel`),Linux 侧进程继续跑。修法:按 job_dir 归属
    (`\\wsl.localhost` 前缀)分发后端。连带 P2:keepalive 无 run 结束释放钩子;
    WSL `prepare/launch` 的 RuntimeError 越过失败分诊;产物经 /mnt 写回的 9p
    开销(重型链应迁 Linux 工作区);follow_job 对 WSL 每 0.5s spawn 一次
    wsl.exe 判活宜节流。

## P1(存储加固代理发现,涉及 loop/driver.py)

10. **干预动作跨 run 互吞**:`due_actions`/`consume_actions` 只按 `deliver_as`
    过滤,driver 消费时也未按 target 过滤——两个 run 并发时 steer/follow_up
    会被对方吞掉。store 侧已有 `target` 字段可作隔离依据(测试验证逐条 CAS
    消费可行),修复需改 driver 或加过滤参数。连带:`scope='step'` 的动作
    target 只存裸 step_id 无 run 归属,跨 run 无法归队(需约定编码或加列)。

## P1(审计代理发现,需显式设计,已有守护测试固定现状)

11. **fork 证据继承缺失**:ladder 不感知 fork——fork run 零产物/零指标也能
    空洞过 L2(过宽),父 run 的 gnss/crossval 指标又不继承(过严),fork run
    永远停在 audited。需决策:L2 是否沿祖先链核对复用步骤产物指纹、父指标是
    否要求 fork 后重新 verify。
12. **云端跳过不进证据阶梯**:全跳过(HyP3)run 无本地证据也能爬到 audited;
    `cloud_evidence` 只进账本展示,ladder 不消费。连带 P2:引擎侧尚无环节真正
    落 `hyp3_manifest.json`;verify.py 指标重解析不支持 h5(与 runok 已不对称)。

## P2/P3(混沌测试代理按纪律未修,jobs.py 归 wslwire 分支管辖)

6. `LocalJobBackend.state()` TOCTOU:`hb.exists()` 与 `stat()` 之间文件被删抛
   `FileNotFoundError`;`job.rc` exists→read 被独占同理。stream 侧已兜住不崩,
   根治应在 jobs.py 内部捕获(P2)。
7. `read_new_lines` 只提交完整行:作业末尾无换行的半行永久丢失,finished 后
   最终 drain 应强制冲刷余量(P2)。
8. wrapper 取消分支 `proc.kill()` 后 `wait(timeout)` 遇不可杀进程抛
   `TimeoutExpired`,wrapper 崩溃不写 rc、上层按 orphaned 处置——路径合理但
   语义隐式,应显式化(P3)。

## P1(事件契约代理发现,修复需改 loop/driver.py,合并波次后处理)

9. **执行期细节事件到不了 UI**:executor 的 `step.stage` 与执行期 `tool.log`
   (真实作业日志行)只发 EventBus(SSE),不进回合 NDJSON 流;前端 SSE 侧只认
   5 类带外条目且 busy 时跳过 → 真实执行时工具卡看不到滚动日志。修法:driver
   执行期把总线事件泵入 yield 流(契约测试④已固化现状,改后同步改测试)。
   连带 P2:`tool.progress`/`budget` 后端从不发;`gate_stop` 调用点未传
   `step_id`;`core/actions.py` 直发 `intervention` 字面量缺 `mode`(KILL 应
   传 mode='steer');前端 `report` 分支硬编码 6 项(mock 遗留)。

## 未覆盖(下轮浏览器实测补)

- fork 改参数影响预览、执行中取消、刷新会话持久化(本轮因面板缺陷中断)。
