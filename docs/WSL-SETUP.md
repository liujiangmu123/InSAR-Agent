# WSL 引擎环境安装手册

insar-agent 的计算引擎(ISCE2 / MintPy / SNAPHU)运行在 WSL2 内,宿主侧只做编排与探测
(设计约束见 `docs/AGENT-DESIGN.md` §4.7–§4.9)。本手册覆盖:发行版导入(主线操作)、
安装脚本用法、验证步骤、WSL 三个破坏性行为的处置、故障排查。

## 目标环境

| 项 | 值 |
|---|---|
| 发行版 | `insar`(Ubuntu 24.04 noble rootfs 导入,root 用户,无交互) |
| 发行版磁盘 | `E:\wsl\insar`(ext4.vhdx 必须落 E: —— C: 空间不足,§0.5.3/§4.8) |
| conda | Miniforge3 → `/opt/miniforge3`(清华镜像下载) |
| 引擎 env | `/opt/miniforge3/envs/insar`:python=3.11 + **isce2=2.6.5(钉定,防重装漂移)** + mintpy + snaphu + gdal + pyaps3(清华 conda-forge 镜像,`--override-channels`;libblas 为 openblas,Linux conda-forge 默认即是,脚本只校验) |
| snaphu 二进制 | `/usr/bin/snaphu`(apt universe 2.0.6-2,步骤 1 安装)。conda-forge 的 `snaphu` 包是 snaphu-py 包装器(0.4.x),**不往 PATH 装可执行**,探测契约与解缠引擎依赖的是 apt 这份 |
| 工作区 | `/home/insar/work`(Linux fs,含 `.jobs/` 作业目录;绝不放 `/mnt/*` 或 `/tmp`) |
| 环境变量 | `/etc/profile.d/insar.sh`:`HDF5_USE_FILE_LOCKING=FALSE`、`INSAR_ENGINE_PREFIX`、引擎 PATH(含 ISCE2 applications 目录) |
| 宿主全局配置 | `%USERPROFILE%\.wslconfig`:`[wsl2]` memory=40GB / processors=20 / swap=16GB / swapFile=`E:\wsl\swap.vhdx` |

## 1. 前置:导入发行版(主线操作,脚本不代劳)

`scripts/wsl_setup.ps1` 检测到发行版缺失时只报错退出(退出码 3),导入由主线执行:

```powershell
# 1) 确认 WSL2 可用(建议 Store 版:wsl --update)
wsl --version

# 2) 下载 Ubuntu 24.04 的 WSL rootfs(示例源,任选可达者)
#    https://cloud-images.ubuntu.com/wsl/noble/current/ubuntu-noble-wsl-amd64-wsl.rootfs.tar.gz

# 3) 导入(显式落 E: 盘,--version 2;导入后默认 root,无交互,正合本项目要求)
wsl --import insar E:\wsl\insar <rootfs.tar.gz 路径> --version 2

# 4) 验证
wsl -l -q          # 应包含 insar
wsl -d insar -u root -- uname -a
```

## 2. 安装脚本

### 2.1 宿主侧编排:`scripts/wsl_setup.ps1`

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\wsl_setup.ps1
# 可选参数:-Distro insar -SkipWslConfig -SkipSetup -SkipProbe -LogDir <目录>
```

做四件事(幂等,可反复重跑;全程无窗,不用 Start-Process):

1. **前置检查**:`wsl.exe` 可用性与版本、发行版是否已导入;
2. **`.wslconfig` 合并**:只增改 `[wsl2]` 段的 memory/processors/swap/swapFile 四键,
   其余段与键原样保留;有实际改动才先备份(`.wslconfig.bak.<时间戳>`)再写回;
   **变更需手动 `wsl --shutdown` 后生效**,脚本绝不自动执行(避免打断正在运行的 WSL 任务);
3. **WSL 内安装**:经 `wsl.exe -d insar -u root` 执行 `scripts/wsl_setup.sh`
   (先 `tr -d '\r'` 中转到 `/tmp` 再执行,免疫 Windows 检出的 CRLF),
   输出实时写入 `logs\wsl_setup_<时间戳>.log`;
4. **验证**:调 `wsl_probe` 打印引擎清单(见 §3)。

退出码:`0` 成功;`2` wsl.exe 不可用;`3` 发行版未导入;`4` wsl_setup.sh 失败;`5` 验证失败。

### 2.2 WSL 内安装:`scripts/wsl_setup.sh`

通常由 ps1 调用;也可在 WSL 内直接跑:`wsl -d insar -u root -- bash /mnt/<盘>/<仓库>/scripts/wsl_setup.sh`。

四个步骤,每步成功后写哨兵文件 `/opt/.insar_setup_done_N`(N=1..4),重跑自动跳过;
哨兵之外还做实态复查(conda 可执行、env 里有 python 才算数),哨兵丢失也不会重装。
**强制重跑某步:删对应哨兵**,如 `rm /opt/.insar_setup_done_3`。

| 步骤 | 内容 | 失败退出码 |
|---|---|---|
| 1 | apt 源切清华镜像(noble,deb822 格式,原文件备份 `.insar_bak`)+ 装 wget/bzip2/ca-certificates/**snaphu**(snaphu 二进制只有 apt 这份,见 §目标环境) | 10(源/update)/ 11(装包) |
| 2 | Miniforge(清华镜像)→ `/opt/miniforge3`,已存在跳过 | 20(下载)/ 21(安装) |
| 3 | `conda create -n insar -c <清华conda-forge> --override-channels python=3.11 isce2=2.6.5 mintpy snaphu gdal pyaps3`(isce2 钉定 2.6.5,升级须先过验证矩阵),已存在跳过;校验 libblas=openblas | 30(创建/残缺 env)/ 31(blas 校验) |
| 4 | 工作区 `/home/insar/work`(含 `.jobs/`)+ `/etc/profile.d/insar.sh` | 40 |

前置失败(非 root / 非 Linux)退出码 1;全部成功 0,并在末尾做一次 `command -v` 引擎自检。

## 3. 验证

### 3.1 wsl_probe(宿主侧,ps1 第 4 步自动调用)

```powershell
.venv\Scripts\python.exe -m insar_agent.runtime.wsl_probe --distro insar
# 机器可读:加 --json;单条命令超时:--timeout 60
```

预期输出(全绿;2026-08-13 现网实测原样):

```
WSL 引擎探测  distro=insar  可达
  conda env: /opt/miniforge3/envs/insar
  [ok] isce2   2.6.5          /opt/miniforge3/envs/insar/lib/python3.11/site-packages/isce/applications/topsApp.py
  [ok] mintpy  1.6.4          /opt/miniforge3/envs/insar/bin/smallbaselineApp.py
  [ok] snaphu  2.0.6          /usr/bin/snaphu
```

退出码:`0` 全部就绪;`1` WSL/发行版不可达;`2` 可达但引擎不全。

探测契约:命令经 `wsl.exe -d insar -u root --exec bash -lc` 执行(登录 shell 才加载
`/etc/profile.d/insar.sh`);`INSAR_ENGINE_PREFIX` 优先、回落 `/opt/miniforge3/envs/insar`。
`merge_wsl_probe(probe, wsl_result)` 把结果并进 `ProbeResult`:引擎键带 ` (wsl)` 后缀
(如 `isce2 (wsl)` → `"2.6.5"`),元数据挂 `probe.wsl["engine_probe"]`;
**probe.py 本体未改,由后续在 `probe_environment` 处接线**。

### 3.2 手动抽查(WSL 内)

```bash
wsl -d insar -u root -- bash -lc 'topsApp.py --help >/dev/null 2>&1; echo isce2=$?'
wsl -d insar -u root -- bash -lc 'smallbaselineApp.py -v'
wsl -d insar -u root -- bash -lc 'snaphu 2>&1 | head -n 1'
wsl -d insar -u root -- bash -lc 'echo $INSAR_ENGINE_PREFIX; ls /home/insar/work'
```

### 3.3 环境快照(2026-08-13 实测,isce2 版本锁定基线)

现网 `insar` 发行版关键包版本(`conda list -n insar`,渠道均为清华 conda-forge 镜像):

| 包 | 版本 | 备注 |
|---|---|---|
| python | 3.11.15 | |
| **isce2** | **2.6.5** | `py311h916084f_0`;S1C/S1D 支持(v2.6.5,2026-06-30 社区版);**`wsl_setup.sh` 已钉定 `isce2=2.6.5`**,升级须先过下方验证矩阵 |
| mintpy | 1.6.4 | |
| snaphu(conda) | 0.4.1 | snaphu-py 包装器,不含 PATH 可执行 |
| snaphu(apt) | 2.0.6-2 | `/usr/bin/snaphu`,探测契约与解缠引擎用的这份 |
| gdal | 3.10.3 | `gdal_translate --version` → GDAL 3.10.3, 2025/04/01 |
| pyaps3 | 0.3.7 | |
| numpy | 1.26.4 | isce2 的 numpy<2 钉死约束(上游不修,ROADMAP-isce3 §1.6) |
| scipy | 1.17.1 | |
| libblas | 3.11.0 (openblas) | 脚本步骤 3 校验项 |
| proj / hdf5 / h5py | 9.6.2 / 1.14.6 / 3.16.0 | `PROJ_DATA` 指向 env share/proj,`proj.db` 在位 |

同日验证矩阵(全绿):`stripmapApp.py`/`topsApp.py` 模块级 import 通过、
`fixImageXml.py` 在 PATH、`source /etc/profile.d/insar.sh` 后 `gdal_translate --version`
正常(PROJ_DATA 解析无误)、`snaphu` 自报 v2.0.6、宿主侧 `wsl_probe` 退出码 0。

历史备注:发行版最初为手工装配(无哨兵、profile 缺 `INSAR_ENGINE_PREFIX`);
2026-08-13 重跑 `wsl_setup.sh` 收敛 —— 哨兵 1-4 补齐,profile 由模板重写
(`INSAR_ENGINE_PREFIX` 补上,PROJ/GDAL 三变量保留)。isce2 conda 包会在 env lib 下留
`python3.1 -> python3.11` 兼容符号链接,profile 模板已用 `sort -V` 取最高真实版本,
避免 `ISCE_HOME` 带着易误读的 `python3.1` 路径。

## 4. §4.8 三个 WSL 行为的处置

| 行为 | 后果 | 处置 |
|---|---|---|
| **空闲自动关机**:最后一个进程退出约 8 秒后 VM 被回收 | 两步之间无进程 → 内存态与 `/tmp` 尽失 | run 期间由 `WslJobBackend.ensure_keepalive()` 维持 `wsl.exe -d insar --exec sleep infinity` 保活进程,run 结束 `release_keepalive()`;安装脚本因此**绝不把状态放 `/tmp`**(哨兵在 `/opt`,作业在 `$WORK/.jobs`) |
| **外部关机不可预防**:`wsl --shutdown`、系统休眠、蓝屏、OOM kill | 作业进程消失且 `job.rc` 永不出现 | 判活走 `job.pid`+`job.start`(/proc starttime 防 PID 复用),检出 `orphaned` 后标 FAILED 但**保留 job.log 与已生成产物**,UI 说明"WSL 停止导致中断,非计算失败";所有中间状态落 WSL 内持久位置(`/home/insar/work/.jobs`) |
| **跨 9p 访问慢**(`/mnt/*`、`\\wsl.localhost`) | 遍历目录/读栅格慢到不可用 | 工作区必须在 Linux fs(`/home/insar/work`),发行版 vhdx 落 E:;宿主只读小文件(job.log/job.rc/小 JSON);遍历、指纹、栅格读取全部在 WSL 内做后回传结果;`/mnt/*` 只读冷数据(归档 SLC) |

`.wslconfig` 的 memory/processors/swap 上限约束 vmmem,防 WSL 吃满宿主(本机也是日常开发机)。

## 5. 故障排查

| 症状 | 原因与处理 |
|---|---|
| `wsl -l -q` 输出乱码/夹 NUL | wsl.exe 消息默认 UTF-16。脚本已设 `WSL_UTF8=1` 并剔除 NUL;手动排查时先 `$env:WSL_UTF8='1'` |
| ps1 退出码 3 | 发行版未导入 → 按 §1 主线导入;或 `-Distro` 指定了错误名称 |
| ps1 退出码 4,日志尾部 `失败(exit=10/11)` | apt 源或网络问题:WSL 内 `curl -I https://mirrors.tuna.tsinghua.edu.cn` 检查可达性;代理环境注意 WSL 默认 NAT 不继承宿主代理 |
| `失败(exit=20/21)` | Miniforge 下载/安装失败:重跑即可(断点从哨兵续);或手动下载放 `/tmp/miniforge3_installer.sh` |
| `失败(exit=30)` | conda create 中断可直接重跑;报"残缺 env"则 `conda env remove -n insar` 后重跑 |
| `失败(exit=31)` | libblas 非 openblas(异常情况):按报错给出的 `conda install ... 'libblas=*=*openblas'` 修复后重跑 |
| `.wslconfig` 改了不生效 | 需 `wsl --shutdown`(手动,选无任务空闲时执行)后下次启动生效;确认改的是 `%USERPROFILE%\.wslconfig` 而非发行版内 `/etc/wsl.conf` |
| `topsApp.py` 找不到但 env 已装 | ISCE2 应用脚本在 `site-packages/isce/applications`,不在 env bin;`/etc/profile.d/insar.sh` 已追加该目录 —— 必须走**登录 shell**(`bash -lc`),`wsl_probe` 与作业 wrapper 均如此 |
| MintPy 报 HDF5 文件锁错误 | 确认 `bash -lc 'echo $HDF5_USE_FILE_LOCKING'` 输出 FALSE(profile 已设;9p/网络 fs 上 HDF5 锁不可靠) |
| `wslpath` 转换失败(ps1 退出码 4) | 发行版禁用了 automount(`/etc/wsl.conf` 的 `[automount]`);恢复默认或手动在 WSL 内跑 wsl_setup.sh |
| 处理性能极差 | 检查工作区是否误放 `/mnt/*`(9p 红线,§4.8/§4.9);必须在 `/home/insar/work` |
| E: 盘不存在/空间不足 | 调整 `.wslconfig` 的 `swapFile` 与导入目标盘;引擎链全跑约需 300–400 GB(§4.10) |

## 6. 相关文件

| 文件 | 作用 |
|---|---|
| `scripts/wsl_setup.ps1` | 宿主侧编排(检查 → .wslconfig → 安装 → 验证) |
| `scripts/wsl_setup.sh` | WSL 内幂等安装(哨兵跳步,退出码分级) |
| `src/insar_agent/runtime/wsl_probe.py` | 引擎探测(runner 可注入)+ `merge_wsl_probe` 并入 `ProbeResult` |
| `tests/test_wsl_probe.py` | 假 runner 全覆盖 + merge 语义 + 脚本静态检查 |
| `src/insar_agent/runtime/wsl.py` | 作业后端(已有,未改):作业目录契约 / wrapper / 判活 |
