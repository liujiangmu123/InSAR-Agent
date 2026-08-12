# 深测波次跟踪清单(2026-08-12)

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

## P1

1. **引擎宿主可用性混淆**(取证确认):`ea58017` 把 WSL 探测并入环境探测后,
   snaphu(wsl) 被规划器当作可用方法,但执行后端仍是 LocalJobBackend,且沙箱
   无云端产物 → 第 6 步预检 `contract_broken` 快速失败(无作业启动,UI 显示
   占位码 exit -1)。修法:probe 结果区分 `engine@local` 与 `engine@wsl`;
   WSL 后端接线(分支 insar-wslwire)合并前,feasibility 只认 local;接线后
   按后端路由能力放开。回归用例:空工作区 + 地震场景 → 第 6 步应 skipped
   (云端已完成)或被可行性排除,不得进入执行失败。
2. **右侧面板内容区 0x0**(浏览器实测 P1-001):标签栏可点、选中态正常,
   tabpanel 尺寸 0x0 不可见(内容 HTML 存在,CSS 布局问题)。涉及
   prototype/css 或 dock.js;等 event-contract 分支合并后再修,避免冲突。

## P2(浏览器实测)

3. 确认执行后的大号对勾遮挡执行流卡片(P2-002)。
4. 失败态呈现弱:标题变"失败"但主界面仍是对勾,错误详情藏在底部小按钮,
   缺显眼的错误卡片 + 日志入口(P2-003)。
5. DOM 频繁重渲染导致元素引用失效(自动化/快速操作体验,P2-001)。

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
