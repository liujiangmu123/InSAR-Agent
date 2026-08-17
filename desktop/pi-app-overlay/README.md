# pi-app overlay

pi Desktop 壳(`E:\SoftApp\pi-app`,justhil/pi-app v0.5.7)不受本仓库 git 管。凡改壳,同一切片必须把文件镜像进本目录,随本仓库提交。

## 镜像规则

- **相对路径 1:1**:overlay 内路径(相对本目录)等于壳内路径(相对 `PiAppRoot`)。
  - overlay:`desktop/pi-app-overlay/src/main/side-panel-registry.ts`
  - 壳:`E:\SoftApp\pi-app\src\main\side-panel-registry.ts`
- **清单以本目录实际文件为准**,`scripts/sync-pi-app-overlay.ps1` 不硬编码文件名。
- **根目录 `README.md` 不受管**(本说明,不会去比对/覆盖壳的 README)。
- 比对是**逐字节**(SHA256)。源码按 UTF-8 落盘,UTF-8 文本即按文件字节比较,不归一化换行。

默认壳根:`E:\SoftApp\pi-app`。可用 `-PiAppRoot` 或环境变量 `INSAR_PI_DESKTOP_ROOT`。

## 同步脚本

在仓库根执行:

```powershell
pwsh -File scripts/sync-pi-app-overlay.ps1 -Check
pwsh -File scripts/sync-pi-app-overlay.ps1 -Pull
pwsh -File scripts/sync-pi-app-overlay.ps1 -Push
pwsh -File scripts/sync-pi-app-overlay.ps1 -Check -PiAppRoot E:\SoftApp\pi-app
```

| 开关 | 方向 | 行为 |
|---|---|---|
| `-Check` | 只读 | 每个受管文件与壳同相对路径逐字节比。不一致列出并失败。无受管文件时成功,打印 `无受管文件,一致`;全部合上则打印 `N 个文件一致`。 |
| `-Pull` | 壳 → overlay | 把壳里**已存在**、且 overlay **已有**的相对路径拷进 overlay(更新镜像)。壳缺失则跳过并警告。 |
| `-Pull -Path <rel>` | 壳 → overlay | 只拷指定相对路径(用于把新改的壳文件纳入 overlay)。不刷新其余受管文件。 |
| `-Pull -Path <rel> -IncludeAllManaged` | 壳 → overlay | 指定路径 + overlay 已有的全部受管文件一并更新。overlay 已有文件时也可用 `-Pull -IncludeAllManaged`(等价于无 `-Path` 的 `-Pull`)。 |
| `-Push` | overlay → 壳 | 覆盖写入壳。新机器复原、或升级壳版本后重铺补丁用。 |

改壳切片的完成定义包含:`-Pull` 回本仓库且 `-Check` 通过。不要手写一长串 `Copy-Item`。

## 升级壳版本(上游漂移)

壳是本地克隆。升级(换 tag / 新 clone)时以 overlay 为补丁源:

1. 检出新壳到 `PiAppRoot`(保留或换目录,用 `-PiAppRoot` 指向)。
2. `pwsh -File scripts/sync-pi-app-overlay.ps1 -Push` 重铺 overlay。
3. 在壳里手工核对冲突、跑 `npm run typecheck` 与 `npm run test:unit`(本脚本不跑 npm、不杀 Electron / 8873)。
4. 若上游改动需要吸收:先改壳,再 `-Pull`,把新镜像收回本仓库。

adapter 声明在本仓库 `.pi/desktop/adapters/`,不进 overlay。

## 当前受管文件

以目录为准;脚本枚举 overlay 内除本 README 外的全部文件。典型集合:

- `src/main/side-panel-registry.ts` — `http-json` + `insar-read` 聚合 provider
- `src/main/index.ts` — 打包版 CSP `img-src` 放行 `http://127.0.0.1:*`;冷启动 `resolveStartupWorkspace`
- `src/main/app-brand.ts` / `window.ts` / `tray.ts` — 可见名 InSAR Agent(不改 electron-builder productName)
- `src/main/startup-workspace.ts` — `INSAR_DESKTOP_PROJECT` > 磁盘 currentProject > recent
- `src/renderer/index.html` — 文档标题 InSAR Agent
- `src/renderer/src/lib/boot-workspace-state.ts` / `ensure-workspace-worker.ts` — 启动读 settings.currentProject,磁盘项目起 Worker
- `src/main/ipc/handlers/adapter-panels.ts` — `resolveSidePanelState` await;openPath 传入 cwd
- `packages/shared/right-panels.ts` — `defaultActive` 默认 Tab 排序
- `src/extension-compat/adapter-schema.ts` / `side-panel-catalog.ts` — sidePanel 透传
- `src/renderer/src/components/app/top-bar.tsx` — InSAR 工作台副题 chip
- `src/renderer/src/locales/{zh,en}/common.json` — `app.name` = InSAR Agent; `topbar.insarWorkbench`
- `src/extension-compat/adapter-backend.ts` — 真实 `shell.openPath`
- `src/extension-compat/adapter-open-path.ts` — openPath 路径解析(无 Electron)
- `src/extension-compat/json-path.ts` — 工具结果 image 内容块
- `src/worker/worker-timeline.ts` — 历史时间线保留 image
- `src/renderer/src/features/side-panels/insar-pipeline-side-panel.tsx` — InSAR 工作台容器(流水线/数据/图件)
- `src/renderer/src/features/side-panels/insar-panel.css` / `insar-panel-model.ts` / `insar-rail.tsx` / `insar-datasets-section.tsx` / `insar-figures-section.tsx`
- `src/renderer/src/features/side-panels/side-panel-registry.tsx` — 注册 `insar-pipeline`
- `src/renderer/src/features/side-panels/side-panel-host.tsx` — adapter 面板查表走 registry
- `src/renderer/src/features/timeline/tool-card-templates.tsx` — media 卡 dataUrl 内联
- `src/renderer/src/stores/apply-app-event-tool.ts` — 直播时间线合并 image
- `src/renderer/src/lib/live-session-timeline-cache.ts` — 缓存快照同样合并 image
- 以及同路径 `*.test.ts` / `*.test.tsx`
