//! 库编译目标:让 `commands` 模块脱离 main.rs 参与 `cargo build` 编译自检。
//!
//! main.rs(另一分支维护)是独立的 bin crate root,不引用本 lib、也不受
//! 本 lib 影响;正式集成时 main.rs 直接 `mod commands;` 把同一份源文件编进
//! bin crate 即可,写法见 desktop/INTEGRATION-commands.md。

pub mod commands;
