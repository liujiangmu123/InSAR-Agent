"""数据集目录识别器(数据集管理的第一步:让系统知道本机有哪些 InSAR 数据)。

给定扫描根目录列表,把每个根的直接子目录当作候选数据集,只凭文件名模式与
stat 元数据(大小/mtime)判型 —— 绝不读取文件内容:InSAR 数据动辄 GB 级,
识别必须秒级完成且对数据零 IO 压力(硬约束)。

识别规则(优先级从上到下,先命中先定型;同一目录可能同时含 DEM 等辅助
文件,按主数据判型 —— 如 GMTSAR ALOS 包含 raw/IMG+LED 与 topo/dem.grd,
应识别为 alos_raw 而非 dem):

  kind         判据(文件名模式,候选目录下钻 ≤2 层)              detail 字段
  hyp3         *_unw_phase*.tif / *_corr*.tif(HyP3 产品)         pairs/unw/corr
  alos_raw     IMG-* 与 LED-* 成对(ALOS PALSAR L1 原始包)        scenes/img/led
  gamma        *.par 且 (*.diff / *.mli / *.rslc);先于 slc_stack par/diff/mli
               (有 .slc 也不改判;不单靠 .slc)
  slc_stack    *.slc 文件或 *.SAFE 目录(SLC 栈)                  slc/safe
  dem          *.dem / *.wgs84 / *.grd(数字高程)                 dem_files
  nisar        名含 GUNW/GSLC/GOFF,或 *GUNW*.h5/*.nc;            gunw/h5
               或多个 .h5/.hdf5/.nc 且名字含 nisar
  displacement .tif/.tiff/.csv 名含 disp/egms/velocity/          tif/csv
               deform/los/vert;或纯 csv 点表(≥1 csv,无 hyp3)
  unknown      其余目录(列出但标未知,不静默吞掉用户的数据)      —

日期范围从文件名解析(YYYYMMDD 后随 T+时刻或分隔符的形态,HyP3 granule 与
Sentinel SAFE/BURST 名内嵌;ALOS 的 IMG/LED 名只有轨道号,解析不出为 None)。
单数据集扫描上限 max_entries(默认 5000 项,文件与目录都计入)防失控,
超限中止遍历并在 detail.truncated 标记。

真实数据形态参考:InSAR-Pro 完整开发测试数据说明(gmtsar ALOS Baja 的
raw/IMG+LED+topo/dem.grd、HyP3 Sentinel-1 产品对目录、Burst SLC tiff)与
engines/localdata.py 的 HyP3 目录消费口径(*unw_phase_clipped.tif 变体)。
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
from pathlib import Path

#: 单数据集扫描项数上限(文件+目录都计入):防止把根加到海量目录上时失控
MAX_ENTRIES = 5000

#: 候选目录下钻深度:2 层覆盖既有真实布局(raw/IMG-*、topo/dem.grd、
#: HyP3 产品对子目录/*.tif),再深属于个别布局,不为其付全树遍历成本
_SCAN_DEPTH = 2

#: 数据集类型闭集(声明序;判型时 gamma 插在 slc_stack 前,避免 .slc/.rslc 抢判)
KINDS = ("hyp3", "alos_raw", "slc_stack", "dem", "nisar", "gamma",
         "displacement", "unknown")

# 文件名模式(全部大小写不敏感;只匹配名字,绝不读内容)
_HYP3_UNW_RE = re.compile(r"_unw_phase[\w-]*\.tiff?$", re.IGNORECASE)
_HYP3_CORR_RE = re.compile(r"_corr[\w-]*\.tiff?$", re.IGNORECASE)
_SLC_RE = re.compile(r"\.slc$", re.IGNORECASE)
_SAFE_RE = re.compile(r"\.safe$", re.IGNORECASE)
_DEM_RE = re.compile(r"\.(dem|wgs84|grd)$", re.IGNORECASE)
_PAR_RE = re.compile(r"\.par$", re.IGNORECASE)
_DIFF_RE = re.compile(r"\.diff$", re.IGNORECASE)
_MLI_RE = re.compile(r"\.mli$", re.IGNORECASE)
_RSLC_RE = re.compile(r"\.rslc$", re.IGNORECASE)
_NISAR_PRODUCT_RE = re.compile(r"GUNW|GSLC|GOFF", re.IGNORECASE)
_NISAR_GUNW_RE = re.compile(r"GUNW", re.IGNORECASE)
_NISAR_NAME_RE = re.compile(r"nisar", re.IGNORECASE)
_H5_RE = re.compile(r"\.(h5|hdf5|nc)$", re.IGNORECASE)
_TIF_RE = re.compile(r"\.tiff?$", re.IGNORECASE)
_CSV_RE = re.compile(r"\.csv$", re.IGNORECASE)
_DISP_NAME_RE = re.compile(r"disp|egms|velocity|deform|los|vert", re.IGNORECASE)

#: 文件名内嵌日期:YYYYMMDD 且后随 T+6 位时刻或分隔符/结尾(前面不能是数字)。
#: 双重防误配:ALOS 轨道号(如 ALPSRP207600640)后随数字,不会命中。
_DATE_RE = re.compile(r"(?<!\d)((?:19|20)\d{6})(?=T\d{6}|[_\-.]|$)")


def dataset_id(path: Path | str) -> str:
    """路径 → 稳定短哈希 id(API 寻址用,避免把磁盘路径放进 URL)。

    归一口径:resolve 后统一正斜杠;Windows 文件系统大小写不敏感,
    再统一小写,同一目录的不同写法得到同一 id。
    """
    norm = str(Path(path).resolve()).replace("\\", "/")
    if sys.platform == "win32":
        norm = norm.lower()
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:12]


def _bounded_walk(root: Path, max_entries: int,
                  max_depth: int = _SCAN_DEPTH) -> tuple[list[dict], list[str], bool]:
    """深度 ≤max_depth 的有界广度遍历(只 scandir/stat,不读内容)。

    返回 (文件清单, 目录相对路径清单, 是否截断)。文件项:
    {"path": 相对路径(正斜杠), "name": 文件名, "size": 字节, "mtime": epoch 秒}。
    stat 失败的单条目跳过(扫描窗口内被删/无权限,不拖垮整个识别);
    符号链接不跟随(联接的 HyP3 树按目录项本身计,防环)。
    """
    files: list[dict] = []
    dirs: list[str] = []
    truncated = False
    count = 0
    queue: list[tuple[Path, str, int]] = [(root, "", 0)]
    while queue and not truncated:
        cur, rel, depth = queue.pop(0)
        try:
            it = os.scandir(cur)
        except OSError:
            continue
        with it:
            for entry in it:
                if count >= max_entries:
                    truncated = True
                    break
                count += 1
                erel = f"{rel}/{entry.name}" if rel else entry.name
                try:
                    if entry.is_dir(follow_symlinks=False):
                        dirs.append(erel)
                        if depth + 1 < max_depth:
                            queue.append((Path(entry.path), erel, depth + 1))
                    else:
                        st = entry.stat(follow_symlinks=False)
                        files.append({"path": erel, "name": entry.name,
                                      "size": st.st_size, "mtime": st.st_mtime})
                except OSError:
                    continue
    files.sort(key=lambda f: f["path"])
    return files, dirs, truncated


def _valid_date(s: str) -> bool:
    """YYYYMMDD 的月/日合法性(挡掉轨道号等 8 位数字的误配)。"""
    return 1 <= int(s[4:6]) <= 12 and 1 <= int(s[6:8]) <= 31


def _dates_in(names: list[str]) -> list[str]:
    """从一组文件/目录名解析全部内嵌日期,返回升序 ISO 日期表。"""
    found: set[str] = set()
    for name in names:
        for m in _DATE_RE.finditer(name):
            s = m.group(1)
            if _valid_date(s):
                found.add(f"{s[:4]}-{s[4:6]}-{s[6:8]}")
    return sorted(found)


def _date_range(names: list[str]) -> dict | None:
    dates = _dates_in(names)
    if not dates:
        return None
    return {"start": dates[0], "end": dates[-1]}


def _classify(files: list[dict], dirs: list[str]) -> tuple[str, dict, dict | None]:
    """按文件名模式判型:返回 (kind, detail, date_range)。"""
    unw = [f for f in files if _HYP3_UNW_RE.search(f["name"])]
    corr = [f for f in files if _HYP3_CORR_RE.search(f["name"])]
    if unw or corr:
        # 产品对数:granule 前缀去重(同一产品的 unw/corr/clipped 变体只计一对)
        pairs = {re.split(r"_unw_phase|_corr", f["name"], maxsplit=1)[0]
                 for f in unw + corr}
        names = [f["name"] for f in unw + corr]
        return ("hyp3",
                {"pairs": len(pairs), "unw": len(unw), "corr": len(corr)},
                _date_range(names))

    img = [f["name"] for f in files if f["name"].startswith("IMG-")]
    led = [f["name"] for f in files if f["name"].startswith("LED-")]
    if img and led:
        # 场景配对:IMG-<极化>-<场景标识> 与 LED-<场景标识> 按场景标识对齐
        img_keys = {n.split("-", 2)[2] for n in img if n.count("-") >= 2}
        led_keys = {n.split("-", 1)[1] for n in led}
        return ("alos_raw",
                {"scenes": len(img_keys & led_keys),
                 "img": len(img), "led": len(led)},
                _date_range(img + led))

    # gamma 必须先于 slc_stack:.rslc 也匹配 *.slc;有 .slc 时仍以 par+干涉产物为准
    par = [f["name"] for f in files if _PAR_RE.search(f["name"])]
    diff = [f["name"] for f in files if _DIFF_RE.search(f["name"])]
    mli = [f["name"] for f in files if _MLI_RE.search(f["name"])]
    rslc = [f["name"] for f in files if _RSLC_RE.search(f["name"])]
    if par and (diff or mli or rslc):
        return ("gamma",
                {"par": len(par), "diff": len(diff), "mli": len(mli)},
                _date_range(par + diff + mli + rslc))

    slc = [f["name"] for f in files if _SLC_RE.search(f["name"])]
    safe = [d for d in dirs if _SAFE_RE.search(d)]
    if slc or safe:
        return ("slc_stack",
                {"slc": len(slc), "safe": len(safe)},
                _date_range(slc + [d.rsplit("/", 1)[-1] for d in safe]))

    dem = [f["name"] for f in files if _DEM_RE.search(f["name"])]
    if dem:
        return ("dem", {"dem_files": sorted(dem)[:8]}, None)

    gunw = [f["name"] for f in files if _NISAR_GUNW_RE.search(f["name"])]
    nisar_prod = [f["name"] for f in files if _NISAR_PRODUCT_RE.search(f["name"])]
    h5 = [f["name"] for f in files if _H5_RE.search(f["name"])]
    nisar_named = any(_NISAR_NAME_RE.search(n) for n in
                      [f["name"] for f in files] + dirs)
    if nisar_prod or (len(h5) >= 2 and nisar_named):
        return ("nisar", {"gunw": len(gunw), "h5": len(h5)},
                _date_range(nisar_prod + h5))

    tif = [f["name"] for f in files if _TIF_RE.search(f["name"])]
    csv = [f["name"] for f in files if _CSV_RE.search(f["name"])]
    disp_hits = [n for n in tif + csv if _DISP_NAME_RE.search(n)]
    pure_csv = bool(csv) and all(_CSV_RE.search(f["name"]) for f in files)
    if disp_hits or pure_csv:
        return ("displacement", {"tif": len(tif), "csv": len(csv)},
                _date_range(disp_hits + csv))

    return ("unknown", {}, None)


def identify_dataset(path: Path, *, max_entries: int = MAX_ENTRIES,
                     depth: int = _SCAN_DEPTH) -> dict:
    """识别单个候选目录,返回数据集条目:
    {id, path, name, kind, size_bytes, file_count, date_range, detail}。

    size_bytes/file_count 是有界遍历所见的合计(截断时为下界,
    detail.truncated=True 如实标注,绝不为凑总数做全树遍历)。
    depth 参数给「根目录本身浅判」用(scan_roots 传 1:只看直接子文件)。
    """
    files, dirs, truncated = _bounded_walk(path, max_entries, max_depth=depth)
    kind, detail, date_range = _classify(files, dirs)
    detail["truncated"] = truncated
    return {
        "id": dataset_id(path),
        "path": str(path),
        "name": path.name,
        "kind": kind,
        "size_bytes": sum(f["size"] for f in files),
        "file_count": len(files),
        "date_range": date_range,
        "detail": detail,
    }


def list_files(path: Path, *, limit: int = 200,
               max_entries: int = MAX_ENTRIES) -> tuple[list[dict], bool]:
    """数据集详情的文件清单:前 limit 项(路径升序)+ 是否还有更多。

    与识别同一套有界遍历(只 stat),清单项 {path, name, size, mtime}。
    """
    files, _dirs, truncated = _bounded_walk(path, max_entries)
    return files[:limit], truncated or len(files) > limit


def scan_roots(roots: list[Path], *, max_entries: int = MAX_ENTRIES) -> list[dict]:
    """扫描全部根目录,返回数据集条目列表(根序 → 目录名序,按 id 去重)。

    候选口径:每个根的直接子目录;根目录本身若在第 1 层就有数据信号
    (如 INSAR_DATA_DIR 直接指向一个 HyP3 产品目录),也作为数据集列出。
    根不存在/不可读 → 静默跳过(数据源下线不应炸掉整个清单)。
    """
    out: list[dict] = []
    seen: set[str] = set()

    def push(info: dict) -> None:
        if info["id"] not in seen:
            seen.add(info["id"])
            out.append(info)

    for root in roots:
        try:
            subdirs = sorted(
                (e.name for e in os.scandir(root) if e.is_dir(follow_symlinks=False)),
            )[:max_entries]
        except OSError:
            continue
        # 根本身的浅判(深度 1):只看直接子文件,避免与子目录候选重复计数
        self_info = identify_dataset(root, max_entries=max_entries, depth=1)
        if self_info["kind"] != "unknown":
            push(self_info)
        for name in subdirs:
            push(identify_dataset(root / name, max_entries=max_entries))
    return out
