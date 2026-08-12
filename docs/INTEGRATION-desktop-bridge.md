# 前端桥接线说明(prototype/js/desktop.js)

> 本分支只交付桥文件本身,不改任何既有 prototype/js 文件;
> 本文列出将来接线的位置与建议写法。
> Rust 侧 command 与壳集成前提见 `desktop/INTEGRATION-commands.md`。

## 一、桥 API 一览(零依赖 ES Module)

```js
import { isDesktop, pickDirectory, openPath, appInfo } from './desktop.js';
```

| 函数 | 桌面壳内 | 浏览器 / 壳未集成 |
|---|---|---|
| `isDesktop()` | `true`(检测 `window.__TAURI__`) | `false` |
| `await pickDirectory(title?)` | 原生目录对话框,返回绝对路径;取消返回 `null` | 恒 `null` |
| `await openPath(path)` | 资源管理器打开目录(传文件则打开所在目录),返回 `true` | 恒 `false` |
| `await appInfo()` | `{ name, version, tauriVersion, platform, arch, debug, repoRoot }` | 恒 `null` |

全部函数在任何环境都绝不抛错(内部吞异常并 console.warn),
调用方不需要 try/catch,也不需要先判断环境再调用。

## 二、接线点 1:环境向导的数据目录输入(pickDirectory)

现状:`prototype/js/envdata.js` 以 `WORKSPACE = { path: null, hint: 'WSL 就绪后默认 /mnt/e/insar/<session>' }`
模拟工作目录,尚无真实输入控件。将来环境向导做「数据/工作目录」输入框时,
在输入框旁挂「浏览…」按钮(按钮仅桌面壳内出现,浏览器里只留手输框):

```js
import { isDesktop, pickDirectory } from './desktop.js';

const browseBtn = isDesktop()
  ? h('button', { type: 'button', onclick: async () => {
      const dir = await pickDirectory('选择数据工作目录');
      if (dir) {
        inputEl.value = dir;
        inputEl.dispatchEvent(new Event('change'));
      } else {
        inputEl.focus(); // 取消或降级 → 回到手输
      }
    } }, '浏览…')
  : null;
```

要点:`pickDirectory` 返回 `null`(浏览器环境 / 用户取消 / 调用失败)时
一律回退手输框,不弹任何错误。

## 三、接线点 2:文件面板「在资源管理器中打开」(openPath)

现状:`prototype/js/dock.js` 的 `filesView()` / `filePreview()` 渲染
「数据与产物」列表,`f.path` 目前是会话内相对路径的 mock。
接后端后在 `filePreview()` 的 tags 行追加按钮(仅桌面壳且文件已生成时显示):

```js
import { isDesktop, openPath } from './desktop.js';

isDesktop() && f?.exists
  ? h('button', { class: 'ghost', type: 'button',
      onclick: () => openPath(absPath) }, '在资源管理器中打开')
  : null
```

前提:后端文件清单需给出绝对路径(或前端用工作目录 / `appInfo().repoRoot`
拼接出 `absPath`);`openPath` 传文件路径时会自动定位到其所在目录。

## 四、接线点 3(顺带):关于面板 / 状态栏(appInfo)

`app.js` 启动时探测一次:结果非 `null` 则在状态栏显示「桌面版 v{version}」,
为 `null` 则显示「浏览器版」。纯展示,无回退逻辑:

```js
import { appInfo } from './desktop.js';
const info = await appInfo(); // 浏览器环境 = null
```

## 五、运行时前提

桥在壳内点亮,要求壳分支完成 `withGlobalTauri`、`build.rs` 的 app_manifest
权限生成、远程 origin capability 授权三处配置(见
`desktop/INTEGRATION-commands.md` §三,2026-08-12 已实机验证点亮,含实测
结论矩阵)。未配置时 `isDesktop()` 返回 `false`;若只缺 capability 授权,
`isDesktop()` 为 `true` 但 invoke 全部安全降级(pickDirectory/appInfo 返回
`null`、openPath 返回 `false`)—— 不会坏,只是不出现桌面能力。
