# UI 细节审计(2026-08-12 傍晚,主线亲手巡检)

方法:对 http://127.0.0.1:8931(main@73bb8f6,含完成态 run 的会话)逐面板
CDP 巡检 + 源码核对。与 19 个子代理波次互补:代理做大项,这里盘细粒度。
标注 [代理] 的项已有分支在修,NEW 的项归下一轮。

## NEW · P1

1. **证据阶梯词汇分裂**:前端 `state.js` `LADDER = [runnable, checked,
   audited, calibrated, validated, publishable]`,后端 `audit/ladder.py` 正式
   语义是 `runnable, complete, consistent, audited, defensible, publishable`。
   审计面板显示"当前 audited/validated 需…"的级别判定完全由前端演示逻辑
   `evidenceCeiling()` 自算,与服务端真实证据级(/api/provenance 的
   evidence.level)脱钩——用户看到的证据声明可能与账本不符,伤核心卖点。
   修法:audit 面板消费 /api/provenance 的 evidence 字段(run done 后),
   词汇表统一为后端六级;离线回落演示时也用同款词汇 + "演示"标注。
   注意与证据阶梯语义分支(T5,正在改 ladder.py 输出结构)合并后再做。

## NEW · P2

2. **files 面板未接真实产物**:`filesView()` 用 `state.js fileTree()` 演示
   派生(指纹缩写也是假的),真实 run 的 artifacts(store 已有,/api/state
   可扩展或复用影像分支新端点)不可见。修法:done 后按 artifacts 表渲染
   (路径/指纹/大小/所属步骤),点击文件显示指纹三档与产出命令;离线回落
   演示 + 标注。依赖:U1 分支的 /api/figures 模式可平移。
3. **report 面板未接 /api/methods.md**:后端现成端点(brain.narrate 增强 +
   规则回落),`reportView()` 却是硬编码演示草稿(数字全是假的)。修法:
   run done 后拉真实 methods.md 渲染(markdown 轻量渲染或 pre 展示),
   附"下载 .md"按钮;离线回落演示 + 标注。
4. **tool 卡产物 chip 无联动**:执行流里 tool.end 的产物徽章不可点。修法:
   点击跳转 dock 对应面板(图像 → images 面板并高亮该图;数据 → files
   面板选中该行)。等 U1/U2 合并后做,联动设计参考 R2 调研结论。

## NEW · P3

5. 页面标题状态前缀("失败 ·"/"已完成 ·")由前端本地状态驱动,刷新后
   与服务端 run 状态可能短暂不一致;应以 /api/state 为准。
6. "从断点继续"按钮显隐由本地种子态驱动(空库时也显示);应绑服务端
   是否存在可续跑 run。
7. 流水线面板步骤行不可点:应展开该步参数/耗时/产物摘要,或跳转终端
   面板对应步骤日志。
8. 审计面板"N 个阈值未标定"文案未锚定到下方 THRESHOLDS 具体行(同屏
   有表但无滚动/高亮联动)。
9. web 面板(内置浏览器)纯演示无标注;加"演示"徽标,真实数据源接入
   前不误导。
10. dock 面板切换是全量 replaceChildren(轻微闪烁);增量渲染成本高,
    暂记不动。
11. 影像面板演示图元数据是假的("figure_journal · 2.4 MB")——U1 接真实
    产物后顺带消除;留此条防遗漏离线回落路径的标注。

## 已分派(本波代理正在修)

- [U1] 影像网格视图 + 产物文件端点 + lightbox
- [U2] 失败卡片一等公民化 + 确认对勾紧凑化
- [U3] 窄屏 dock 抽屉与断点整理
- [U4] env/term 面板真实化(静态演示数据下架)
- [U5] aria/焦点/对比度/快捷键帮助
- [T5] 证据阶梯语义(fork 继承/云端封顶)——本清单第 1 条的后端前置
