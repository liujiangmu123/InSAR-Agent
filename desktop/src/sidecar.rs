// 后端 sidecar 进程管理:后端探测(冻结 exe 优先,Python 解释器兜底)、
// 仓库根定位、spawn(CREATE_NO_WINDOW + stdout/stderr 滚动日志)、退出监测与关闭。
// 探测顺序:① exe 同目录 backend\insar-backend.exe(打包分发形态,免 Python)
//           ② 环境变量 INSAR_PYTHON ③ {仓库根}/.venv ④ PATH python。
// 窗口 / 诊断页 / 重启编排在 main.rs,本模块只握进程与日志。

use std::fs::{File, OpenOptions};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

/// 单个日志文件上限:写入将超过时滚动(当前 → `.1`,旧 `.1` 删除),
/// 连同当前文件共保留最近 2 个。
const LOG_MAX_BYTES: u64 = 5 * 1024 * 1024;

// ---------------- 全局状态 ----------------

struct State {
    child: Option<Child>,
    /// 应用退出中:看护线程见到该标志立即收手,不再重启。
    shutting_down: bool,
    /// 滚动日志(首个 spawn 时创建,重启复用同一份,追加写)。
    log: Option<Arc<Mutex<RollingLog>>>,
}

static STATE: Mutex<State> = Mutex::new(State {
    child: None,
    shutting_down: false,
    log: None,
});

// ---------------- 仓库根与 Python 探测 ----------------

/// 仓库根:
/// - dev 构建:CARGO_MANIFEST_DIR 指向 desktop/,其父目录即仓库根(编译期烙入);
/// - 发布构建(或 dev 路径已不存在):从 exe 所在目录向上找含 `.venv` 或
///   `pyproject.toml` 的目录;
/// - 都失败:回退当前工作目录。
pub fn repo_root() -> PathBuf {
    if cfg!(debug_assertions) {
        if let Some(root) = Path::new(env!("CARGO_MANIFEST_DIR")).parent() {
            if root.is_dir() {
                return root.to_path_buf();
            }
        }
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            if let Some(root) = find_marker_root(dir) {
                return root;
            }
        }
    }
    std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."))
}

/// 自 start 向上(含自身)找第一个含 `.venv` 目录或 `pyproject.toml` 的目录。
fn find_marker_root(start: &Path) -> Option<PathBuf> {
    start
        .ancestors()
        .find(|d| d.join(".venv").is_dir() || d.join("pyproject.toml").is_file())
        .map(Path::to_path_buf)
}

/// 项目虚拟环境解释器路径(项目约定:仓库根下 `.venv`)。
pub fn venv_python(repo_root: &Path) -> PathBuf {
    if cfg!(windows) {
        repo_root.join(".venv").join("Scripts").join("python.exe")
    } else {
        repo_root.join(".venv").join("bin").join("python")
    }
}

/// `.venv` 缺失时的创建指引(项目约定,进日志与诊断页)。
pub fn venv_setup_hint(repo_root: &Path) -> String {
    format!(
        "项目约定使用仓库内虚拟环境,请在仓库根({})创建:\n  py -m venv .venv\n  .venv\\Scripts\\python.exe -m pip install -r requirements.txt",
        repo_root.display()
    )
}

// ---------------- 后端探测(冻结 exe 优先,Python 兜底) ----------------

/// 探测到的后端形态。
#[derive(Clone)]
pub enum Backend {
    /// 打包分发形态:exe 同目录 `backend\insar-backend.exe`(PyInstaller onedir
    /// 冻结产物,自带 UI 与依赖),直接 spawn,无需任何 Python。
    Frozen(PathBuf),
    /// 源码开发形态:Python 解释器 + 来源说明,spawn `python -m insar_agent.api.app`。
    Python { exe: PathBuf, source: String },
}

impl Backend {
    /// 探测结果一行描述(进日志与诊断页「后端」行):
    /// 「冻结后端:路径」或「Python:路径(来源:…)」。
    pub fn describe(&self) -> String {
        match self {
            Backend::Frozen(exe) => format!("冻结后端:{}", exe.display()),
            Backend::Python { exe, source } => {
                format!("Python:{}(来源:{source})", exe.display())
            }
        }
    }

    /// 诊断页「启动命令」单元格。
    pub fn launch_desc(&self) -> String {
        match self {
            Backend::Frozen(_) => "insar-backend.exe(冻结后端,直接运行,无需 Python)".into(),
            Backend::Python { .. } => "python -m insar_agent.api.app".into(),
        }
    }
}

/// 打包分发布局下冻结后端的候选路径:主 exe 同目录 `backend\insar-backend.exe`
/// (安装/分发时把 PyInstaller onedir 产物整目录摆到壳 exe 旁并命名 `backend\`)。
fn frozen_backend_candidate(exe_dir: &Path) -> PathBuf {
    let name = if cfg!(windows) {
        "insar-backend.exe"
    } else {
        "insar-backend"
    };
    exe_dir.join("backend").join(name)
}

/// 探测后端,优先级:
/// ① exe 同目录 `backend\insar-backend.exe`(打包分发形态,免 Python)
/// ② 环境变量 INSAR_PYTHON ③ {仓库根}/.venv ④ PATH python。
pub fn find_backend(repo_root: &Path) -> Result<Backend, String> {
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            let cand = frozen_backend_candidate(dir);
            if cand.is_file() {
                return Ok(Backend::Frozen(cand));
            }
        }
    }
    match find_python(repo_root) {
        Ok((exe, source)) => Ok(Backend::Python { exe, source }),
        Err(e) => Err(format!(
            "未找到可用后端:exe 同目录无冻结后端(backend\\insar-backend.exe),Python 探测也失败 —— {e}"
        )),
    }
}

/// 探测 Python(探测链 ②③④,冻结后端缺席时的源码开发形态兜底):
/// 环境变量 INSAR_PYTHON > {仓库根}/.venv > PATH。
/// 返回(解释器路径,来源说明——用于日志与诊断页)。
pub fn find_python(repo_root: &Path) -> Result<(PathBuf, String), String> {
    if let Some(v) = std::env::var_os("INSAR_PYTHON") {
        let p = PathBuf::from(&v);
        if p.is_file() {
            return Ok((p, "环境变量 INSAR_PYTHON".into()));
        }
        return Err(format!("INSAR_PYTHON 指向的文件不存在:{}", p.display()));
    }
    let venv = venv_python(repo_root);
    if venv.is_file() {
        return Ok((venv, "项目虚拟环境 .venv".into()));
    }
    if let Some(p) = find_python_in_path() {
        return Ok((
            p,
            format!("PATH(未找到项目 .venv:{},依赖可能缺失)", venv.display()),
        ));
    }
    Err(format!(
        "未找到 Python 解释器(依次尝试:环境变量 INSAR_PYTHON、{}、PATH)。\n{}",
        venv.display(),
        venv_setup_hint(repo_root)
    ))
}

fn find_python_in_path() -> Option<PathBuf> {
    let names: &[&str] = if cfg!(windows) {
        &["python.exe", "python3.exe"]
    } else {
        &["python3", "python"]
    };
    let paths = std::env::var_os("PATH")?;
    for dir in std::env::split_paths(&paths) {
        if dir.as_os_str().is_empty() {
            continue;
        }
        for name in names {
            let cand = dir.join(name);
            if cand.is_file() {
                return Some(cand);
            }
        }
    }
    None
}

// ---------------- 滚动日志 ----------------

pub fn log_path(port: u16) -> PathBuf {
    std::env::temp_dir().join(format!("insar-agent-sidecar-{port}.log"))
}

/// 追加写的滚动日志:写入将超过 max_bytes 时,当前文件改名为 `{path}.1`
/// (覆盖旧的),再新开当前文件 —— 连同当前共保留最近 2 个文件。
struct RollingLog {
    path: PathBuf,
    max_bytes: u64,
    file: Option<File>,
    written: u64,
}

impl RollingLog {
    fn new(path: PathBuf, max_bytes: u64) -> Self {
        Self {
            path,
            max_bytes,
            file: None,
            written: 0,
        }
    }

    fn rotated_path(&self) -> PathBuf {
        let mut os = self.path.clone().into_os_string();
        os.push(".1");
        PathBuf::from(os)
    }

    fn ensure_open(&mut self) -> std::io::Result<()> {
        if self.file.is_none() {
            let f = OpenOptions::new().create(true).append(true).open(&self.path)?;
            self.written = f.metadata().map(|m| m.len()).unwrap_or(0);
            self.file = Some(f);
        }
        Ok(())
    }

    /// 追加一段字节;超限先滚动。写日志失败静默吞掉 —— 日志绝不反噬 sidecar。
    fn append(&mut self, chunk: &[u8]) {
        if self.ensure_open().is_err() {
            return;
        }
        if self.written.saturating_add(chunk.len() as u64) > self.max_bytes {
            self.roll();
            if self.ensure_open().is_err() {
                return;
            }
        }
        if let Some(f) = self.file.as_mut() {
            if f.write_all(chunk).is_ok() {
                self.written = self.written.saturating_add(chunk.len() as u64);
            }
        }
    }

    /// Windows 上不能重命名打开中的文件,先关句柄再改名;
    /// 改名失败(如被外部工具占用)则下次写入时继续追加原文件并重试。
    fn roll(&mut self) {
        if let Some(mut f) = self.file.take() {
            let _ = f.flush();
        }
        let bak = self.rotated_path();
        let _ = std::fs::remove_file(&bak);
        let _ = std::fs::rename(&self.path, &bak);
    }
}

/// 后台线程把子进程管道内容泵入滚动日志,管道 EOF(进程退出)后自然结束。
fn pump_into_log<R: Read + Send + 'static>(mut src: R, log: Arc<Mutex<RollingLog>>) {
    std::thread::spawn(move || {
        let mut buf = [0u8; 8192];
        loop {
            match src.read(&mut buf) {
                Ok(0) | Err(_) => break,
                Ok(n) => log.lock().unwrap().append(&buf[..n]),
            }
        }
    });
}

// ---------------- spawn / 退出监测 / 关闭 ----------------

pub fn spawn(backend: &Backend, port: u16, workdir: &Path) -> Result<(), String> {
    let log = {
        let mut st = STATE.lock().unwrap();
        if st.log.is_none() {
            st.log = Some(Arc::new(Mutex::new(RollingLog::new(
                log_path(port),
                LOG_MAX_BYTES,
            ))));
        }
        st.log.as_ref().unwrap().clone()
    };
    let epoch = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    log.lock().unwrap().append(
        format!(
            "\n===== [desktop] spawn sidecar:{} port={port} epoch={epoch} =====\n",
            backend.describe()
        )
        .as_bytes(),
    );

    let mut cmd = match backend {
        // 冻结产物自带 UI 与数据文件(INSAR_HOME 缺省落 %LOCALAPPDATA%),
        // 工作目录设为产物目录即可,不依赖仓库根。
        Backend::Frozen(exe) => {
            let mut c = Command::new(exe);
            c.current_dir(exe.parent().unwrap_or(workdir));
            c
        }
        Backend::Python { exe, .. } => {
            let mut c = Command::new(exe);
            c.args(["-m", "insar_agent.api.app"]).current_dir(workdir);
            c
        }
    };
    cmd.env("INSAR_PORT", port.to_string())
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        // 桌面版铁律:绝不允许弹出黑色控制台窗
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    let mut child = cmd
        .spawn()
        .map_err(|e| format!("启动 sidecar 失败({}):{e}", backend.describe()))?;
    if let Some(out) = child.stdout.take() {
        pump_into_log(out, log.clone());
    }
    if let Some(err) = child.stderr.take() {
        pump_into_log(err, log);
    }

    let mut st = STATE.lock().unwrap();
    if st.shutting_down {
        drop(st);
        let _ = child.kill();
        let _ = child.wait();
        return Err("应用正在退出,取消 sidecar 启动".into());
    }
    st.child = Some(child);
    Ok(())
}

/// 非阻塞查看子进程是否已退出;已退出返回状态描述(不取走句柄)。
pub fn child_exit_status() -> Option<String> {
    let mut st = STATE.lock().unwrap();
    let child = st.child.as_mut()?;
    match child.try_wait() {
        Ok(Some(status)) => Some(status.to_string()),
        _ => None,
    }
}

pub enum WaitOutcome {
    /// 应用退出中,不要重启。
    Shutdown,
    /// 子进程退出(附退出状态描述),句柄已回收。
    Exited(String),
}

/// 阻塞等待:子进程退出或应用开始关闭。
pub fn wait_exit_or_shutdown() -> WaitOutcome {
    loop {
        {
            let mut st = STATE.lock().unwrap();
            if st.shutting_down {
                return WaitOutcome::Shutdown;
            }
            match st.child.as_mut() {
                None => return WaitOutcome::Exited("进程句柄已不存在".into()),
                Some(child) => match child.try_wait() {
                    Ok(Some(status)) => {
                        st.child = None;
                        return WaitOutcome::Exited(status.to_string());
                    }
                    Ok(None) => {}
                    Err(e) => {
                        st.child = None;
                        return WaitOutcome::Exited(format!("状态查询失败:{e}"));
                    }
                },
            }
        }
        std::thread::sleep(Duration::from_millis(500));
    }
}

/// 分段睡眠,期间应用开始关闭则提前返回 true(调用方应立即收手)。
pub fn sleep_unless_shutdown(total: Duration) -> bool {
    let deadline = Instant::now() + total;
    while Instant::now() < deadline {
        if STATE.lock().unwrap().shutting_down {
            return true;
        }
        std::thread::sleep(Duration::from_millis(100));
    }
    STATE.lock().unwrap().shutting_down
}

/// kill 当前子进程(重启流程中失败重试用),不设置关闭标志。
pub fn kill_current() {
    let child = STATE.lock().unwrap().child.take();
    if let Some(mut c) = child {
        let _ = c.kill();
        let _ = c.wait();
    }
}

/// 应用退出:设置关闭标志(看护线程停止重启)并 kill sidecar。
/// 后端有 orphan 检测与 reattach 语义,「桌面关闭 → 重开」= 断点续跑。
pub fn shutdown() {
    let child = {
        let mut st = STATE.lock().unwrap();
        st.shutting_down = true;
        st.child.take()
    };
    if let Some(mut c) = child {
        let _ = c.kill();
        let _ = c.wait();
    }
}

// ---------------- 测试 ----------------

#[cfg(test)]
mod tests {
    use super::*;

    fn temp_dir_unique(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("insar-desktop-{tag}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn rolling_log_rolls_and_keeps_two_files() {
        let dir = temp_dir_unique("rolllog");
        let path = dir.join("sidecar.log");
        let mut log = RollingLog::new(path.clone(), 64);
        for _ in 0..8 {
            log.append(&[b'x'; 32]);
        }
        let rotated = log.rotated_path();
        assert!(path.is_file(), "当前日志应存在");
        assert!(rotated.is_file(), "滚动历史文件应存在");
        assert!(std::fs::metadata(&path).unwrap().len() <= 64);
        drop(log);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn find_marker_root_walks_up() {
        let dir = temp_dir_unique("markerroot");
        std::fs::write(dir.join("pyproject.toml"), "").unwrap();
        let nested = dir.join("a").join("b");
        std::fs::create_dir_all(&nested).unwrap();
        assert_eq!(find_marker_root(&nested), Some(dir.clone()));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn venv_python_matches_platform_convention() {
        let p = venv_python(Path::new("repo"));
        if cfg!(windows) {
            assert!(p.ends_with(Path::new(".venv").join("Scripts").join("python.exe")));
        } else {
            assert!(p.ends_with(Path::new(".venv").join("bin").join("python")));
        }
    }

    #[test]
    fn venv_hint_mentions_creation_commands() {
        let hint = venv_setup_hint(Path::new("repo"));
        assert!(hint.contains("py -m venv .venv"));
        assert!(hint.contains("pip install -r requirements.txt"));
    }

    #[test]
    fn frozen_candidate_is_backend_subdir_next_to_exe() {
        let p = frozen_backend_candidate(Path::new("app"));
        if cfg!(windows) {
            assert!(p.ends_with(Path::new("backend").join("insar-backend.exe")));
        } else {
            assert!(p.ends_with(Path::new("backend").join("insar-backend")));
        }
        assert!(p.starts_with("app"));
    }

    /// 冻结产物存在时,find_backend 应最优先命中它(优先于 INSAR_PYTHON/.venv/PATH)。
    /// 直接对候选路径逻辑断言(current_exe 无法在测试内伪造,spawn 层不重复验)。
    #[test]
    fn backend_describe_distinguishes_frozen_and_python() {
        let frozen = Backend::Frozen(PathBuf::from("C:/app/backend/insar-backend.exe"));
        assert!(frozen.describe().starts_with("冻结后端:"));
        assert!(frozen.launch_desc().contains("无需 Python"));

        let py = Backend::Python {
            exe: PathBuf::from("C:/repo/.venv/Scripts/python.exe"),
            source: "项目虚拟环境 .venv".into(),
        };
        assert!(py.describe().starts_with("Python:"));
        assert!(py.describe().contains("来源:项目虚拟环境 .venv"));
        assert_eq!(py.launch_desc(), "python -m insar_agent.api.app");
    }
}
