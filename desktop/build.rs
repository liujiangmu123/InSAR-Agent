fn main() {
    // app_manifest.commands:为应用自定义 command 自动生成 ACL 权限
    // (allow-<command> / deny-<command>),供 capabilities/remote-ui.json 授权。
    // 实测依据(2026-08,见 INTEGRATION-commands.md §三):主窗口是远程 origin
    // (http://127.0.0.1:<port>),Tauri 2 对远程 origin 的 app command 默认拒绝
    // ("<cmd> not allowed. Plugin not found"),必须显式生成权限并在 capability
    // 中列出;不声明 app_manifest 时权限不存在,capability 也无从授权。
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&["pick_directory", "open_path", "app_info"]),
    ))
    .expect("tauri-build 失败");
}
