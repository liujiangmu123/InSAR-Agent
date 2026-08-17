# 43 · 附录 C — pi 升级章程(0.84.2 → 未来版本)

> 原则:锚定 0.84.2,只在有明确需求时升级;devDependency 与全局安装**永远同版本**;所有定制都走官方扩展点,升级检查面收敛为下面 7 步。

1. 改 `pi-insar/package.json` devDependency 到目标版,`npm i`。
2. `npx tsc --noEmit`:扩展 API 破坏性变化会在此暴露(ToolDefinition / 事件签名 / registerProvider 形态 / 工具结果 image 内容块形状)。
3. `npx vitest run`:行为回归(guard、mode、sidebar、tools、provider、theme、journal 全套)。
4. Diff 新版 `packages/coding-agent/docs/themes.md` 必需 token 清单(以 node_modules 内新版文档为准;`pi-insar/reference/pi` 是 0.84.2 快照,升级后仅作历史参考)→ 同步 `pi-insar/themes/insar-dark.json` 与 `pi-insar/test/theme.test.ts` 的 `REQUIRED_TOKENS`。
5. Diff `custom-provider.md`(registerProvider 字段)与 `extensions.md`(tool_call/tool_result 事件字段、ui.setWidget/setStatus 签名)。
6. 手动冒烟:`pi -e pi-insar/src/index.ts --list-models`、一回合 `-p`、侧栏渲染、strict 拦截、`insar_view_figure` 内联图。
7. 全绿后再 `npm i -g @earendil-works/pi-coding-agent@<新版>`,更新 `00-README.md` 与 `01-execution-rules.md` 的版本基线,单独提交:

```powershell
git add pi-insar/package.json pi-insar/package-lock.json pi-insar/docs/plan/
git commit -m "chore(pi-insar): pi 升级至 <版本> —— typecheck/测试/主题 token 全绿"
```

回滚:任一步失败且无法当场修复,恢复 devDependency 版本号并 `npm i`,不留半升级状态。

## Desktop SDK(与 CLI 分轨,2026-08-15)

- CLI / `pi-insar` devDependency:**0.84.2**(不变)。
- pi Desktop 壳 v0.5.7 bundled:**0.83.0**。D0–D3 允许 builtin 0.83.0 跑本仓库 `.pi/extensions`。
- **不要**为对齐去改壳 `package.json` 的 `pi-coding-agent` 版本。
- D4 正式对齐:Desktop 设置 → SDK → global 0.84.2 → 重启 worker。回退:同一页切回 builtin。
- 破坏性差异逐条记本附录,不 patch `src/main/sdk-loader.ts`。
- 执行记录:切换是用户机器 userData 里的 `sdk/current.json`,不进本仓库。走查清单见 `pi-insar/docs/desktop-walkthrough.md` D4。
