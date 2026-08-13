// lib 目标:让交付模块在 main.rs 尚未接线时也始终参与 `cargo build` 编译检查。
// main.rs 集成时直接 `mod xxx;` 包含同一份源文件即可,与本 lib 目标互不干扰。

pub mod commands;
pub mod opendata;
pub mod singleton;
pub mod tray;
