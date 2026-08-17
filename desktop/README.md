# desktop

当前桌面产品是 **pi Desktop**（源码在 `E:\SoftApp\pi-app`）。本目录只保留补丁镜像：

`pi-app-overlay/` — 与壳 1:1 的 InSAR 工作台改动，用仓库根 `scripts/sync-pi-app-overlay.ps1` 同步。

旧 Tauri 壳（Cargo / WebView2 / PyInstaller 冻结包）已从本工作副本移除。GitHub 历史里仍有，不需要本地再占几 GB 的 `target/`。
