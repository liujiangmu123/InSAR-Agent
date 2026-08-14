"""MintPy 输出文件标准化命名工具
===============================
将 MintPy 输出目录下的文件重命名为标准格式。
直接放在 mintpy 目录旁边运行，或指定 --dir 参数。

用法:
  python standardize_mintpy.py                      # 当前目录下的 mintpy/
  python standardize_mintpy.py --dir /path/to/mintpy # 指定 mintpy 目录
  python standardize_mintpy.py --dir /path/to/task_dir  # 指定 task 目录（会自动找 mintpy/）

标准命名:
  velocity.tif              → vel_{path}_{frame}_{YYYYMM}_{YYYYMM}.tif
  velocityERA5.tif          → vel_E_{path}_{frame}_{YYYYMM}_{YYYYMM}.tif
  timeseries_ramp_demErr.h5 → cum_rd_{path}_{frame}_{YYYYMM}_{YYYYMM}.h5
  timeseries_ERA5_ramp_demErr.h5 → cum_rdE_{path}_{frame}_{YYYYMM}_{YYYYMM}.h5
  waterMask.tif             → water_{path}_{frame}_{YYYYMM}_{YYYYMM}.tif
  temporalCoherence.tif     → tc_{path}_{frame}_{YYYYMM}_{YYYYMM}.tif
"""

import argparse
import re
import shutil
import sys
from pathlib import Path


def find_in_dir(base: Path, *names: str):
    for name in names:
        p = base / name
        if p.is_file():
            return p
    return None


def extract_tag(mintpy_dir: Path):
    """Extract (path, frame, date_tag) from context."""
    task_dir = mintpy_dir.parent
    m = re.search(r'Path(\d+).*Frame(\d+)', task_dir.name, re.IGNORECASE)
    if not m:
        print(f'[WARN] 无法从目录名解析 path/frame: {task_dir.name}')
        return None, None, ''

    path_num = int(m.group(1))
    frame_num = int(m.group(2))

    start_ym = ''
    end_ym = ''
    for ts_pat in ['timeseries_ramp_demErr.h5', 'timeseries_ERA5_ramp_demErr.h5',
                   'timeseries.h5', 'timeseries_ERA5.h5']:
        ts_file = find_in_dir(mintpy_dir, ts_pat)
        if not ts_file:
            continue
        try:
            import h5py
            with h5py.File(str(ts_file), 'r') as f:
                ds = f.attrs.get('START_DATE')
                de = f.attrs.get('END_DATE')
                if ds is not None:
                    s = ds.decode('utf-8') if isinstance(ds, bytes) else str(ds)
                    start_ym = s.strip("b'").split('T')[0].strip()[:6]
                if de is not None:
                    e = de.decode('utf-8') if isinstance(de, bytes) else str(de)
                    end_ym = e.strip("b'").split('T')[0].strip()[:6]
            break
        except Exception:
            continue

    date_tag = f'{start_ym}_{end_ym}' if start_ym and end_ym else 'nodate'
    return path_num, frame_num, date_tag


def run(mintpy_dir: Path, dry_run: bool = False):
    if not mintpy_dir.is_dir():
        print(f'[ERROR] 目录不存在: {mintpy_dir}')
        return

    path_num, frame_num, date_tag = extract_tag(mintpy_dir)
    if path_num is None:
        print('[ERROR] 无法确定 path/frame，请在 PathXXX_FrameXXX 命名的 task 目录下运行')
        return

    tag = f'{path_num}_{frame_num}_{date_tag}'
    print(f'[INFO] Path={path_num} Frame={frame_num} 日期={date_tag}')
    print(f'[INFO] 目标目录: {mintpy_dir}')
    print()

    mappings = [
        # (src_pattern, dst_name, action)
        ('velocity.tif',           f'vel_{tag}.tif',        'rename'),
        ('velocityERA5.tif',       f'vel_E_{tag}.tif',      'rename'),
        ('waterMask.tif',          f'water_{tag}.tif',      'rename'),
        ('temporalCoherence.tif',  f'tc_{tag}.tif',         'rename'),
        ('timeseries_ramp_demErr.h5',  f'cum_rd_{tag}.h5',  'copy'),
        ('timeseries_ERA5_ramp_demErr.h5', f'cum_rdE_{tag}.h5', 'copy'),
    ]

    renamed = 0
    copied = 0
    skipped = 0

    for src_name, dst_name, action in mappings:
        src = find_in_dir(mintpy_dir, src_name)
        if not src:
            # Try timeseriesResidual_ramp_demErr.h5 as fallback for cum_rd
            if 'cum_rd' in dst_name:
                src = find_in_dir(mintpy_dir, 'timeseriesResidual_ramp_demErr.h5')
                if not src:
                    print(f'  SKIP {src_name} - 未找到')
                    skipped += 1
                    continue
            else:
                print(f'  SKIP {src_name} - 未找到')
                skipped += 1
                continue

        dst = mintpy_dir / dst_name
        if dst.is_file():
            print(f'  SKIP {src_name} - {dst_name} 已存在')
            skipped += 1
            continue

        if dry_run:
            print(f'  {action.upper():6s} {src.name} -> {dst_name}')
            if action == 'rename':
                renamed += 1
            else:
                copied += 1
        else:
            if action == 'rename':
                src.rename(dst)
                print(f'  RENAME  {src_name} -> {dst_name}')
                renamed += 1
            else:
                shutil.copy2(str(src), str(dst))
                print(f'  COPY    {src_name} -> {dst_name}')
                copied += 1

    print()
    if dry_run:
        print(f'[DRY RUN] 将重命名 {renamed} 个，复制 {copied} 个，跳过 {skipped} 个')
    else:
        print(f'[DONE] 重命名 {renamed} 个，复制 {copied} 个，跳过 {skipped} 个')


def main():
    parser = argparse.ArgumentParser(description='MintPy 输出文件标准化命名')
    parser.add_argument('--dir', '-d', help='mintpy 目录或 task 目录路径')
    parser.add_argument('--dry-run', '-n', action='store_true', help='仅预览，不实际执行')
    args = parser.parse_args()

    if args.dir:
        target = Path(args.dir)
    else:
        target = Path.cwd()

    # If user pointed to a task dir (not mintpy), look inside
    if target.is_dir() and (target / 'mintpy').is_dir():
        target = target / 'mintpy'

    run(target, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
