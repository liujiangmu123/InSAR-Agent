//! 编译自检 bin(任务纪律:只 `cargo build`,不 `cargo run`)。
//!
//! main.rs 归属其它分支、不声明新模块,所以这里通过 `#[path]` 把两个新模块
//! 编入本 bin crate,并用函数指针把公开接口的签名钉死——`cargo build` 即完成
//! 两个模块的全量类型检查,不创建窗口、不运行任何 Tauri 逻辑。

#[path = "../shortcuts.rs"]
mod shortcuts;
#[path = "../window_state.rs"]
mod window_state;

fn main() {
    let _restore: fn(&tauri::WebviewWindow) = window_state::restore;
    let _track: fn(&tauri::WebviewWindow) = window_state::track;
    let _attach: fn(&tauri::AppHandle) = window_state::attach_when_ready;
    let _setup: fn(&tauri::AppHandle) -> Result<(), Box<dyn std::error::Error>> =
        shortcuts::setup_shortcuts;
    println!("window_check ok:window_state + shortcuts 通过编译自检");
}
