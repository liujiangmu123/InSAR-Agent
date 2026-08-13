#!/usr/bin/env bash
# =============================================================================
# scripts/wsl_setup.sh —— WSL 发行版(默认 insar)内的引擎环境安装。root 运行,幂等可重跑。
#
# 调用方式:
#   宿主侧:scripts/wsl_setup.ps1 经 `wsl.exe -d insar -u root --exec bash -c ...` 调用
#           (经 /tmp 中转剥 CRLF,见 ps1 注释)
#   WSL 内:bash scripts/wsl_setup.sh
#
# 幂等设计:
#   - 每步成功后写哨兵文件 /opt/.insar_setup_done_N(N=1..4),重跑时看哨兵直接跳过;
#   - 哨兵之外每步还做实态复查(conda 可执行/env 目录是否真在),哨兵丢了也不会重装;
#   - 需要强制重跑某步:删除对应哨兵文件即可,如 rm /opt/.insar_setup_done_3。
#
# 步骤:
#   1  apt 源切清华镜像(noble,deb822)+ 基础工具(wget/bzip2/ca-certificates)
#      + snaphu 二进制(universe 2.0.6;conda-forge `snaphu` 是 snaphu-py 包装器,
#      不往 PATH 装可执行,而探测契约与解缠引擎需要 `snaphu` 在位)
#   2  Miniforge(Linux x86_64,清华镜像下载)→ /opt/miniforge3
#   3  conda env `insar`(清华 conda-forge 镜像,--override-channels):
#      python=3.11 isce2=2.6.5 mintpy snaphu gdal pyaps3;并校验 libblas 为 openblas
#      (isce2 钉定 2.6.5:2026-08-13 现网实测版本,支持 S1C/S1D,防未来重装漂移;
#      升级须先跑 docs/VALIDATION-isce2-wsl.md 的验证矩阵再改这里)
#   4  工作区 /home/insar/work(Linux fs,AGENT-DESIGN §4.8)+ /etc/profile.d/insar.sh
#      (HDF5_USE_FILE_LOCKING=FALSE、INSAR_ENGINE_PREFIX、引擎 PATH)
#
# 退出码分级:
#   0   全部成功
#   1   前置失败(非 root / 非 Linux)
#   10  apt 源配置或 apt-get update 失败
#   11  apt 基础包安装失败
#   20  Miniforge 下载失败
#   21  Miniforge 安装失败
#   30  conda env 创建失败(或检测到残缺 env,需先 conda env remove)
#   31  libblas 非 openblas 实现(只校验不改动;修复命令见报错与 docs/WSL-SETUP.md)
#   40  工作区/profile 配置失败
# =============================================================================
set -uo pipefail

APT_MIRROR="https://mirrors.tuna.tsinghua.edu.cn/ubuntu/"
MINIFORGE_URL="https://mirrors.tuna.tsinghua.edu.cn/github-release/conda-forge/miniforge/LatestRelease/Miniforge3-Linux-x86_64.sh"
CONDA_FORGE_MIRROR="https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud/conda-forge/"
MINIFORGE_PREFIX="/opt/miniforge3"
ENV_NAME="insar"
ENV_PREFIX="${MINIFORGE_PREFIX}/envs/${ENV_NAME}"
WORKSPACE="/home/insar/work"
PROFILE_FILE="/etc/profile.d/insar.sh"
SENTINEL_PREFIX="/opt/.insar_setup_done_"

log()  { printf '[wsl_setup] %s\n' "$*"; }
die()  { local code="$1"; shift; log "失败(exit=${code}):$*"; exit "$code"; }
mark_done() { date -Is > "${SENTINEL_PREFIX}$1" || true; log "步骤 $1 完成,哨兵 ${SENTINEL_PREFIX}$1"; }
is_done()   { [ -f "${SENTINEL_PREFIX}$1" ]; }

[ "$(id -u)" = "0" ] || die 1 "必须以 root 运行(wsl.exe -d insar -u root)"
[ "$(uname -s)" = "Linux" ] || die 1 "只能在 WSL/Linux 内运行"
export DEBIAN_FRONTEND=noninteractive
log "开始:$(date -Is)  内核:$(uname -r)"

# --- 步骤 1:apt 源(清华镜像)+ 基础工具 -------------------------------------
if is_done 1; then
  log "步骤 1 已完成,跳过(apt 源与基础工具)"
else
  CODENAME="$(. /etc/os-release && echo "${VERSION_CODENAME:-noble}")"
  [ "$CODENAME" = "noble" ] || log "警告:检测到 codename=${CODENAME}(预期 noble/Ubuntu 24.04),按检测值继续"
  SRC=/etc/apt/sources.list.d/ubuntu.sources
  # 24.04 默认 deb822 格式;原文件只备份一次(.insar_bak 已存在则不覆盖)
  if [ -f "$SRC" ] && [ ! -f "${SRC}.insar_bak" ]; then
    cp "$SRC" "${SRC}.insar_bak" || die 10 "备份 ${SRC} 失败"
  fi
  cat > "$SRC" <<EOF || die 10 "写入 ${SRC} 失败"
# 由 insar-agent scripts/wsl_setup.sh 生成(原文件备份:ubuntu.sources.insar_bak)
Types: deb
URIs: ${APT_MIRROR}
Suites: ${CODENAME} ${CODENAME}-updates ${CODENAME}-backports ${CODENAME}-security
Components: main restricted universe multiverse
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg
EOF
  # 旧格式 sources.list 若仍有生效条目,注释掉,避免双源冲突
  if [ -s /etc/apt/sources.list ] && grep -qE '^[[:space:]]*deb ' /etc/apt/sources.list; then
    sed -i.insar_bak 's/^[[:space:]]*deb /# insar_setup 注释: deb /' /etc/apt/sources.list \
      || die 10 "注释旧 sources.list 失败"
  fi
  apt-get update || die 10 "apt-get update 失败(检查网络与镜像可达性)"
  # snaphu 走 apt(universe,2.0.6):conda-forge `snaphu` 已是 snaphu-py 包装器,
  # 不提供 PATH 上的可执行;现网(2026-08-13 实测)即 /usr/bin/snaphu
  apt-get install -y --no-install-recommends wget bzip2 ca-certificates snaphu \
    || die 11 "基础包安装失败(wget/bzip2/ca-certificates/snaphu)"
  mark_done 1
fi

# --- 步骤 2:Miniforge → /opt/miniforge3 --------------------------------------
if is_done 2 && [ -x "${MINIFORGE_PREFIX}/bin/conda" ]; then
  log "步骤 2 已完成,跳过(Miniforge 已在 ${MINIFORGE_PREFIX})"
else
  if [ -x "${MINIFORGE_PREFIX}/bin/conda" ]; then
    log "检测到 ${MINIFORGE_PREFIX} 已存在,跳过安装"
  else
    installer=/tmp/miniforge3_installer.sh
    log "下载 Miniforge:${MINIFORGE_URL}"
    wget -q --tries=3 --timeout=60 -O "$installer" "$MINIFORGE_URL" \
      || die 20 "Miniforge 下载失败(镜像:${MINIFORGE_URL})"
    bash "$installer" -b -p "$MINIFORGE_PREFIX" || die 21 "Miniforge 安装失败"
    rm -f "$installer"
  fi
  mark_done 2
fi

# --- 步骤 3:conda env insar(ISCE2 + MintPy + SNAPHU + GDAL + pyAPS3) --------
CONDA="${MINIFORGE_PREFIX}/bin/conda"
if is_done 3 && [ -x "${ENV_PREFIX}/bin/python" ]; then
  log "步骤 3 已完成,跳过(env 已在 ${ENV_PREFIX})"
else
  [ -x "$CONDA" ] || die 30 "找不到 conda(步骤 2 未完成?)"
  if [ -d "$ENV_PREFIX" ] && [ ! -x "${ENV_PREFIX}/bin/python" ]; then
    die 30 "检测到残缺 env(${ENV_PREFIX} 存在但无 python);请先 ${CONDA} env remove -n ${ENV_NAME} 再重跑"
  fi
  if [ -x "${ENV_PREFIX}/bin/python" ]; then
    log "检测到 ${ENV_PREFIX} 已存在,跳过创建"
  else
    log "创建 conda env ${ENV_NAME}(python=3.11 isce2=2.6.5 mintpy snaphu gdal pyaps3;镜像:${CONDA_FORGE_MIRROR})"
    # isce2 钉定 2.6.5(S1C/S1D 支持;2026-08-13 现网实测版本,防重装漂移)
    "$CONDA" create -y -n "$ENV_NAME" \
      -c "$CONDA_FORGE_MIRROR" --override-channels \
      python=3.11 isce2=2.6.5 mintpy snaphu gdal pyaps3 \
      || die 30 "conda env 创建失败(网络中断可直接重跑本脚本;残缺时先 conda env remove -n ${ENV_NAME})"
  fi
  # libblas 校验:Linux conda-forge 默认即 openblas,这里只校验不切换(任务约定)
  blas_line="$("$CONDA" list -n "$ENV_NAME" '^libblas$' 2>/dev/null | grep -E '^libblas[[:space:]]' || true)"
  log "libblas:${blas_line:-<未找到>}"
  echo "$blas_line" | grep -q "openblas" \
    || die 31 "libblas 不是 openblas 实现;修复:${CONDA} install -y -n ${ENV_NAME} -c ${CONDA_FORGE_MIRROR} --override-channels 'libblas=*=*openblas'"
  mark_done 3
fi

# --- 步骤 4:工作区 + /etc/profile.d/insar.sh ---------------------------------
if is_done 4 && [ -f "$PROFILE_FILE" ]; then
  log "步骤 4 已完成,跳过(工作区与 profile)"
else
  # 工作区必须在 WSL 的 Linux 文件系统上,不是 /mnt/*(AGENT-DESIGN §4.8 性能红线);
  # 作业目录 $WORK/.jobs 一并建好(§4.7 作业目录契约,绝不放 /tmp)
  install -d -m 0775 "$WORKSPACE" "${WORKSPACE}/.jobs" || die 40 "创建工作区 ${WORKSPACE} 失败"
  cat > "$PROFILE_FILE" <<PROFILE || die 40 "写入 ${PROFILE_FILE} 失败"
# insar-agent 引擎环境(scripts/wsl_setup.sh 生成,重跑会覆盖)
# HDF5 文件锁在 9p/网络文件系统上不可靠,关闭之(MintPy 官方建议)
export HDF5_USE_FILE_LOCKING=FALSE
# 引擎 conda env 前缀:runtime/probe.py 与 runtime/wsl_probe.py 都认这个变量
export INSAR_ENGINE_PREFIX=${ENV_PREFIX}
export PATH="${ENV_PREFIX}/bin:${MINIFORGE_PREFIX}/bin:\$PATH"
# PROJ/GDAL 数据路径:不 conda activate 时缺省解析失败(2026-08 ALOS Baja 实测:
# 缺 PROJ_DATA 则 gdal_translate/geocode 报 proj_create_from_database 打不开 proj.db)
export PROJ_DATA=${ENV_PREFIX}/share/proj
export PROJ_LIB=${ENV_PREFIX}/share/proj
export GDAL_DATA=${ENV_PREFIX}/share/gdal
# ISCE2 应用脚本(topsApp.py 等)在 site-packages/isce/applications,不在 env bin。
# sort -V 取最高真实版本:isce2 conda 包会留下 python3.1 -> python3.11 兼容符号链接,
# 字典序第一条会选中它,ISCE_HOME 就会带着易误读的 python3.1 路径(2026-08-13 实测)
_isce_apps="\$(ls -d ${ENV_PREFIX}/lib/python3.*/site-packages/isce/applications 2>/dev/null | sort -V | tail -n 1)"
if [ -n "\$_isce_apps" ]; then
  export ISCE_HOME="\${_isce_apps%/applications}"
  export PATH="\$_isce_apps:\$PATH"
fi
unset _isce_apps
PROFILE
  chmod 0644 "$PROFILE_FILE" || die 40 "chmod ${PROFILE_FILE} 失败"
  mark_done 4
fi

# --- 汇总:引擎可见性自检(轻量 command -v,不跑计算) ------------------------
# shellcheck disable=SC1090
. "$PROFILE_FILE"
for exe in topsApp.py smallbaselineApp.py snaphu gdalinfo; do
  if p="$(command -v "$exe" 2>/dev/null)"; then
    log "引擎可见:${exe} -> ${p}"
  else
    log "警告:PATH 中未见 ${exe}(登录 shell 下重试或检查步骤 3/4)"
  fi
done
log "全部完成:$(date -Is)"
exit 0
