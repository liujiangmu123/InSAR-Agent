# 设计令牌审计与收口台账(DESIGN-TOKENS-audit)

> 快照:2026-08-13 · 扫描范围 `prototype/css/*.css` 共 20 个文件
> (含 main 合并进来的 11 个新增文件:bridge / doctorpanel / figcompare /
> llmsettings / plandiff / provview / responsive / skillpanel / slash / states / tour)。
> 本轮唯一系统性改动的文件:`tokens.css` + `base.css`(全站基础层,即任务书所称
> "styles.css" 在本仓的实际形态);其余 css 只记录不改,按本台账接力收口。

---

## 1. 分层与命名规则

| 层 | 位置 | 命名 | 说明 |
| --- | --- | --- | --- |
| 字面值层(旧名) | `tokens.css` `:root` / `[data-theme='dark']` | `--bg`/`--text`/`--ok`/`--stale`… | **必须持有字面十六进制**。`scripts/check_a11y_contrast.py` 与 `tests/test_a11y_dom.py` 按「旧名 = hex」正则解析做 WCAG 硬门禁,别名方向不可倒置(把旧名改成 `var()` 引用会让硬门禁空表失败) |
| 语义层(收口层) | 同上,`--c-*` 段 | `--c-bg`/`--c-ok-fg`/`--c-focus-ring`… | 新代码与接力改造一律引用 `--c-*`;取值全部是对字面值层的 `var()` 引用,零重复、零漂移 |
| 标尺层 | 同上 | `--sp-*`/`--r-*`/`--fs-*`/`--sh-*` | 与既有命名空间一致,本轮只做"补档",未改任何既有取值 |
| 断点层 | `base.css` `:root` | `--bp-*` | 全项目唯一事实源(`check_css_syntax.py` 门禁);main 合并新增 `--bp-sider-squeeze`/`--bp-single-col`,合法 |

**Token 总账**:`tokens.css` 改造前 61 个定义 → 现 **144 个**(新增 83:
`--c-*` 71 个 = 41 镜像别名 + 20 状态五件套 + 9 新字面色 + 1 焦点环色;
标尺补档 10 = `--sp-05/15/25/35/45`、`--r-xs/xl/round`、`--fs-2xs/ui`;
焦点几何 2 = `--focus-ring-w/offset`)。另有 `base.css` 断点 5 个,全站合计 149。

新增标尺补档的取值全部来自全库扫描高频字面量(见下文各表),本轮仅在
`base.css` 内做**等值替换**(值相同,视觉零变化)。

---

## 2. 状态语义五件套(ok / warn / bad / info / running)

每语义四枚 token:`-bg` 弱底 / `-fg` 前景 / `-bd` 边框 / `-solid` 实心指示。

| 语义 | 来源族 | light(bg / fg / bd / solid) | dark(bg / fg / bd / solid) |
| --- | --- | --- | --- |
| `--c-ok-*` 成功 | `--ok*` | `#f0fdf4` / `#15803d` / `#bbf7d0` / `#159a46` | `#0f2418` / `#86efac` / `#1b4030` / `#4ade80` |
| `--c-warn-*` 警示 | `--stale*`(本项目"失效"橙) | `#fff7ed` / `#9a3412` / `#fed7aa` / `#ca4c0a` | `#2a1a0d` / `#fdba74` / `#4a2c15` / `#fb923c` |
| `--c-bad-*` 失败 | `--bad*` | `#fef2f2` / `#b91c1c` / `#fecaca` / `#dc2626` | `#2a1215` / `#fca5a5` / `#4d1f24` / `#f87171` |
| `--c-info-*` 信息 | `--accent*` | `#eff6ff` / `#1d4ed8` / `#bfdbfe` / `#2563eb` | `#12233d` / `#93c5fd` / `#1e3a63` / `#60a5fa` |
| `--c-run-*` 运行中 | `--accent*`(与 info 同源) | 同 info | 同 info |

使用规范:

1. **徽章 / 横幅 / 状态卡** = 三件套:`background: var(--c-X-bg); color: var(--c-X-fg); border-color: var(--c-X-bd)`(fg 落 bg 两主题均 ≥ 4.5:1,已入门禁)。
2. **圆点 / 左缘边条 / 进度填充** = `-solid`(落 `--c-bg`/`--c-bg-sub`/`--c-bg-mute` 均 ≥ 3:1,已入门禁)。
3. **solid 底上的对勾 / 步骤数字** 用 `--c-text-inv`(图形指示 ≥ 3:1 已入门禁;真实正文文本落 solid 时旧门禁按 4.5 管 accent/stale/bad 三色按钮面)。
4. running 与 info 同色,**区分不靠颜色**:running 必须叠加呼吸/脉冲动效 + 文字(既有 `.tag.is-run .led` 动画约定;`prefers-reduced-motion` 下动画降级,仍有文字兜底)。
5. 语义三重表达原则不变:状态 = 颜色 + 图标 + 文字(色盲友好)。
6. **禁用态豁免**(WCAG 1.4.3 例外):`.btn:disabled`/`.cbtn.send:disabled`/`.opt:disabled`/`.plr-chip.zero` 等以 opacity(.35–.55)表达的禁用态不参与对比度判定。
7. 接力注意:`dock.css` `.ladder .lv.cur` 是 **9.5px 真实文本落 `--ok` 实心底**(白字 3.66:1,只够图形级)。建议接力改为反白方案 `--c-ok-bg`+`--c-ok-fg`,或将该行文本视作图标级并补 aria 文字。

---

## 3. 对比度审计与修复清单

门禁双轨:

- `scripts/check_a11y_contrast.py`(旧,字面值层 54 项,信息性,硬门禁在 `tests/test_a11y_dom.py`)→ **54 项全过**;
- `scripts/check_contrast.py`(**本轮新增**,语义层硬门禁,未达标退出 1):33 组真实使用组合 × 2 主题 = 66 项对比 + 142 项 `--c-*` 别名链完整性 = **208 项全过**。已注册进 `check_frontend.py` 套件 `py:contrast_tokens`。

本轮修复(色相不变、仅明度微调;其余 205 项原值即达标):

| # | Token(主题) | 旧值 → 新值 | 触发组合 | 对比度变化 | 连带复验 |
| --- | --- | --- | --- | --- | --- |
| 1 | `--ok`(light) | `#16a34a` → `#159a46`(色相 142° 不变) | 磁盘/进度条填充落轨道底 `--bg-mute`(`.diskbar i`/`.prog > i`) | **2.94 → 3.26**(≥3.0) | 落 `--bg` 3.30→3.66、落 `--bg-sub` 3.08→3.42、白字落 solid 3.30→3.66,全部仍达标 |
| 2 | `--text-3`(light) | `#656e7c` → `#616a78`(色相 216° 不变) | 悬停底上的辅助文本(`.row:hover .mt`/`.hs` 小徽标) | **4.43 → 4.70**(≥4.5) | 落 `--bg`/`--bg-sub`/`--bg-mute` 提升至 5.5/5.1/4.9 一线,旧门禁 54 项复验绿 |
| 3 | `--text-3`(dark) | `#7c8796` → `#86919f`(色相 214° 不变) | 同上组合的暗主题 | **4.23 → 4.81**(≥4.5) | 同上,全部提升 |

新门禁组合口径(与旧脚本同哲学:按真实选择器整理,非笛卡尔积):
五件套 fg×bg、fg×卡面/页面底、solid×三级底、text-inv×solid(图形 3.0)、
焦点环×三级底(3.0)、悬停底×三级文本(4.5)。禁用态不入表(见 §2.6)。

---

## 4. 焦点可见性(:focus-visible)

统一环规范(`base.css` 全局,本轮 token 化):

```css
:focus-visible {
  outline: var(--focus-ring-w) solid var(--border-focus);  /* 2px */
  outline-offset: var(--focus-ring-offset);                 /* 2px */
  border-radius: var(--r-sm);
}
:focus:not(:focus-visible) { outline: none; }  /* 鼠标不显环 */
```

环色 `--border-focus`(别名 `--c-focus-ring`)落页面/浅灰/卡面三级底,
两主题 6 项全部 ≥ 3:1(light 3.68/3.44/3.68,dark 7.44/7.00/7.00,已入门禁)。

全文件覆盖体检(2026-08-13):

| 文件 | 状况 | 处置 |
| --- | --- | --- |
| base.css | 全局统一环(token 化)+ 鼠标豁免 | ✅ 本轮完成;全局选择器天然覆盖一切未自定义元素,**无缺环控件** |
| setup / gallery / bridge / states / tour / skillpanel / provview / figcompare(`.fcp-toggle`) | 自定义环与统一规范同色同宽(`2px solid var(--border-focus)`),offset 取 1 / 2 / −2(内嵌式用于满宽表头) | ✅ 一致,offset 差异属组件语境,记录即可 |
| **figcompare.css:144** | `.fcp-btn:focus-visible { outline: 2px solid #7cc0ff }` **硬编码** | ⚠ 接力:灯箱常暗语境,建议新增 `--c-lb-focus: #7cc0ff` 进灯箱专用组并引用 |
| **llmsettings.css:39** | `outline: 2px solid var(--acc, #3b82f6)` 用私有 `--acc` 兜底值 | ⚠ 接力:改 `var(--border-focus)`(该面板随主题,不该自带兜底) |
| figcompare.css:226 | `.fcp-range:focus-visible { opacity: .12 }` 以可见反馈面代环(透明滑杆) | 记录:功能性替代,可接受 |
| stream(`.reasonbox input`)/ dock(`.field input`/`.term-tools input`/`.web .addr input`)/ setup(`.setup-field input`)| `outline:none` + `border-color: var(--accent)` 边框替代 | 记录:聚焦有可见变化;接力时建议统一补环以对齐规范 |
| tour(`.tour-card`)/ setup(`.setup-card`) | 容器程序化聚焦 `outline:none` | 记录:非键盘交互目标,合规 |
| cmdk(`.cmdk-input`) | `outline:none`,由面板整体呈现聚焦语境 | 记录:接力时评估补环 |

---

## 5. 颜色字面量台账(20 文件,46 个不同值 · 约 110 处)

### 5.1 本轮已收口(base.css → token,等值)

| 字面量 | 原位置 | 归 token |
| --- | --- | --- |
| `#3b82f6`,`#1d4ed8`(.brand 渐变) | base | `--c-brand-a/b` |
| `#0c1e3a`,`#1e40af`(.who .av 渐变) | base | `--c-brand-deep-a/b` |
| `#fff`(.brand/.who .av 渐变上文字) | base ×2 | `--c-on-brand`(恒白:渐变不随主题变) |
| `#fff`(.btn-pri/wrn/dng) | base ×3 | `var(--text-inv)`(与下方 a11y 覆写同值,计算值不变) |
| `rgba(10,15,28,.32)`(.scrim) | base | `--c-scrim` |
| `rgba(10,15,28,.45)`(.kbd-help .mask) | base | `--c-scrim-strong` |
| `rgba(37,99,235,.45)/(…,0)`(pulseRing) | base ×2 | `--c-ring-pulse`/`--c-ring-pulse-out` |

### 5.2 接力清单(只记录,未改)

**A. 常暗灯箱/预览面板族**(gallery / figcompare / dock 灯箱、网页预览:刻意不随主题翻转的深色 UI)——建议新建灯箱专用组 `--c-lb-*`(固定值,集中一段定义):

| 字面量(出现次数) | 语义 | 提案 |
| --- | --- | --- |
| `#fff`×19(台面底/按钮字) | 看图台面恒白底、灯箱按钮文字 | `--c-lb-stage`(台面)/`--c-lb-fg-max` |
| `rgba(8,11,18,.72/.78/.82/.93)`×6、`#0a0f1c`×1 | 灯箱底幕四档 | `--c-lb-veil-1..4` |
| `rgba(20,24,31,.65/.72/.78)`×6、`rgba(50,58,72,.85)`×3 | 工具条底/悬停 | `--c-lb-tool`/`--c-lb-tool-hover` |
| `rgba(255,255,255,.22/.12/.08)`×9 | 灯箱按钮边/veil | `--c-lb-border`/`--c-lb-veil-inv` |
| `#e2e8f0`×2、`#e6e9ee`×6、`#cfd6df`×2、`#a2acbb`×3、`#94a3b8`×1、`#aab4c2`×2、`#7f8a99`×1、`#8fd0a8`×2 | 灯箱前景明度阶梯 | `--c-lb-fg-1..4`/`--c-lb-meta` |
| `#7cc0ff`×5、`#ffb84d`×4、`#2f7fd6`×2、`#c2711d`×2 | 对比模式 A/B 选中环与角标 | `--c-lb-cmp-a`/`--c-lb-cmp-b`(+ 阴影环用同名) |
| `#2563eb`×2、`#3b82f6`×2(aria-pressed 态) | 灯箱内按下态 | 直接换 `var(--accent)`/`--c-lb-focus` |
| `#b45309`×1(figcompare) | = light `--think` 值的复制 | 换 `var(--think)` |

**B. llmsettings.css 漂移族(高优先接力:硬编码复制了旧 token 值)**

| 字面量 | 证据 | 提案 |
| --- | --- | --- |
| `#16a34a` | = 旧 `--ok`(本轮 `--ok` 已调暗为 `#159a46`,该处**已经脱钩**) | 换 `var(--ok)` |
| `#dc2626` / `#d97706` | = `--bad` / 2024 版 `--think`(后者早已因 a11y 调深为 `#b45309`,双重漂移) | 换 `var(--bad)` / `var(--think)` |
| `#d6dbe3`×4、`#5b6472`×3、`#1a2029`×2、`#0b0e14`×1 | 面板私有中性阶 | 就近归 `--border-strong`/`--text-2`/`--bg-mute`(暗)等,需视觉复核 |
| `rgba(0,0,0,.28)`(0 18px 48px 阴影)、`rgba(127,127,127,.12)` | 大浮层阴影/中性 veil | 提案 `--sh-4: 0 18px 48px rgba(16,24,40,.20)` 系列化;veil 归 `--bg-hover` |
| `var(--acc, #3b82f6)` | 私有强调色带兜底 | 换 `var(--accent)`(见 §4) |

**C. 其他**:`rgba(10,15,28,.45)`×1(cmdk 面纱)→ `var(--c-scrim-strong)` 直换;
`rgba(0,0,0,.16)`(rail popover 阴影)→ 与 `--sh-3` 仅差 2px 扩散,接力评审并档;
`rgba(0,0,0,.6/.65)`(gallery/figcompare 卷帘杆阴影)→ 提案 `--sh-divider`;
`#000`×2(skillpanel)→ 归 scrim/阴影族;`stream` 的 `rgba(255,255,255,.22)`
(用户气泡内 code 底)→ 提案 `--c-onaccent-veil`。

---

## 6. 间距字面量台账(margin / padding / gap)

标尺:`--sp-05:2 · --sp-1:4 · --sp-15:6 · --sp-2:8 · --sp-25:10 · --sp-3:12 ·
--sp-35:14 · --sp-4:16 · --sp-45:20 · --sp-5:24`(本轮补 05/15/25/35/45 五档)。

| 值 | 总次数(top 分布) | 处置 |
| --- | --- | --- |
| 6px | 93(dock21 stream19 provview10) | `--sp-15`;base 已换,余接力 |
| 8px | 88(dock16 setup10 stream9) | `--sp-2`;同上 |
| **7px** | 84(dock24 stream22) | **光学微调,保留字面量**;接力归一到 6/8 需视觉评审(非等值) |
| **9px** | 76(stream26 dock23) | 同上(候选归 8/10) |
| 12px | 70(stream31 setup14) | `--sp-3` |
| 10px | 61(dock9 setup8) | `--sp-25` |
| **5px** | 57(dock19) | 光学保留(候选归 4/6) |
| 2px | 49 | `--sp-05` |
| **11px** | 43(dock18) | 光学保留(候选归 10/12) |
| 4px | 44 | `--sp-1` |
| 1px / 3px | 36 / 34 | 微调,保留 |
| 14px | 15 | `--sp-35` |
| 16px | 14 | `--sp-4` |
| **13px** | 10 | 光学保留(候选归 12/14) |
| 20px | 9 | `--sp-45` |
| 0px×7(responsive) | 合法零值 | 保留 |
| 18/22/24/26/28/30/44/96/116/120px | 各 1–5 | 结构性一次值(composer 兜底高、浮层顶距等),保留并逐处注释 |

方法论:top/left/bottom 等定位偏移不算间距(如 toast-host `bottom:92px`);
`@media` 条件内的像素是 `--bp-*` 清单的手工镜像(CSS 限制,`check_css_syntax.py` 门禁核对),不入本表。
**base.css 本轮 42 处间距字面量已等值替换为 token,剩余 5/7/9/13/15px 属光学微调(已注记)。**

## 7. 圆角字面量台账

| 值 | 次数 | 处置 |
| --- | --- | --- |
| 50% | 17 | `--r-round`;base 已换 4 处(av/led/typing/pip),余接力 |
| 4px | 11 | `--r-xs`;base 已换(kbd),余接力 |
| 12px(bridge×3) | 3 | = `--r-lg` **直换即等值**(接力) |
| 14px(stream 气泡) | 3 | `--r-xl`(已定义,接力:`14px 14px 4px 14px` → `var(--r-xl) var(--r-xl) var(--r-xs) var(--r-xl)`) |
| 10px(llmsettings/tour) | 2 | 介于 `--r`(9)与 `--r-lg`(12),接力评审并档 |
| 6px(llmsettings) | 2 | = `--r-sm` 直换(接力;base 滚动条已换) |
| 15px / 2px / 3px | 各 1 | 一次性(hero 大标/迷你条/终端行),保留 |

## 8. 阴影字面量台账

健康形态(保留):`color-mix(in srgb, var(--X) N%, transparent)` 系 12 处
(states 呼吸环/tour 聚光/setup 脉冲等,已随 token 变色)。字面 rgba 阴影:

| 字面值 | 位置 | 提案 |
| --- | --- | --- |
| `0 10px 28px rgba(0,0,0,.16)` | rail popover | ≈`--sh-3`,接力并档 |
| `0 18px 48px rgba(0,0,0,.28)` | llmsettings 大浮层 | 提案新档 `--sh-4` |
| `0 0 4px rgba(0,0,0,.6/.65)` | gallery/figcompare 卷帘杆 | 提案 `--sh-divider` |
| `0 0 0 1px #2f7fd6` / `#c2711d` | figcompare A/B 选中环 | 归 `--c-lb-cmp-a/b`(§5.2A) |
| base.css 脉冲环 rgba×2 | (本轮已换) | ✅ `--c-ring-pulse(-out)` |

## 9. 字号字面量台账

| 值 | 次数 | 处置 |
| --- | --- | --- |
| 10px | 37 | `--fs-2xs`(已定义;接力直换) |
| **10.5px** | 26 | 半像素族,**接力归一评审**(候选 10/11,非等值) |
| 11.5px | 17 | 同上(候选 11/12) |
| 13px | 14 | `--fs-ui`(已定义;base 3 处已换,余接力直换) |
| 11px / 12px | 13 / 7 | = `--fs-xs` / `--fs-sm` 直换(base 已换 2 处) |
| 13.5px(dock×1) | 1 | = `--fs` 直换(接力) |
| 9.5 / 12.5px | 4 | 半像素族评审 |
| 14 / 15 / 17 / 18 / 20 / 22 / 24 / 7 / 9px | 各 1–3 | 一次性展示字号(kpi 大数/灯箱字形/SVG 内字),保留注释 |

---

## 10. base.css 等值替换回执(本轮 68 处)

颜色 13 · 间距 42 · 圆角 6 · 字号 5 · 焦点环几何 2。全部为「同值换名」:
计算样式不变(`.btn-pri` 等三处 `#fff`→`var(--text-inv)` 依赖既有 a11y 段
同值覆写,计算值不变)。保留的字面量与理由:

- `5/7/9/13/15px` 内衬与 `gap`(光学微调,§6);
- `bottom: 92px`(toast 与 composer 的结构联动量)、`min-width:138px`、控件尺寸(`width/height`)、`border` 宽(1/1.5/2/3px)、`letter-spacing`;
- 滚动条 `width:10px`/`border:3px`(WebKit 结构量);
- `@media` 断点数值(`--bp-*` 镜像,工具链约束)。

## 11. 接力优先级建议

1. **P0** llmsettings 漂移族(§5.2B:`#16a34a` 已与 `--ok` 脱钩)+ 两处焦点环分叉(§4);
2. **P1** cmdk 面纱、bridge `12px`→`--r-lg`、dock/stream 品牌渐变与 `13px`→`--fs-ui`、`10px` 字号→`--fs-2xs` 等**纯直换**项(零视觉风险);
3. **P2** 灯箱 `--c-lb-*` 专用组立项(§5.2A,一次定义三文件受益);
4. **P3** 半像素字号与奇数间距归一(需视觉评审,建议配合截图回归)。

验证命令(全部纳入一键门禁):

```powershell
.venv\Scripts\python.exe scripts\check_frontend.py            # 29 套件全绿
.venv\Scripts\python.exe scripts\check_contrast.py --fails    # 语义层 208 项
.venv\Scripts\python.exe scripts\check_a11y_contrast.py --fails  # 字面层 54 项
```
