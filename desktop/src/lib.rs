// lib 目标:让 tray / singleton 两个交付模块在 main.rs 尚未接线时
// 也始终参与 `cargo build` 编译检查(本工程的可执行入口仍是 src/main.rs,
// 它由集成方负责,见 INTEGRATION-tray.md)。
//
// main.rs 集成时直接 `mod tray;` / `mod singleton;` 包含同一份源文件即可,
// 与本 lib 目标互不干扰。

pub mod singleton;
pub mod tray;
