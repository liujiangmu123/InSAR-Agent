"""批量标准化 {path}_{frame} 目录下的旧 MintPy 文件

用法:
  python batch_standardize.py --dir G:/isce2_gpu/ASF --dry-run   # 预览
  python batch_standardize.py --dir G:/isce2_gpu/ASF              # 执行
"""

import argparse
import re
import shutil
from pathlib import Path


def find_file(base: Path, *names: str):
    for name in names:
        p = base / name
        if p.is_file():
            return p
    return None


def process_dir(subdir: Path, dry_run: bool):
    m = re.match(r'^(\d+)_(\d+)$', subdir.name)
    if not m:
        return None, None
    path_num = int(m.group(1))
    frame_num = int(m.group(2))

    # Extract date from timeseries HDF5
    start_ym = ''
    end_ym = ''
    for ts_name in ['timeseries_ramp_demErr.h5', 'timeseries_ERA5_ramp_demErr.h5',
                     'timeseries.h5', 'timeseries_ERA5.h5']:
        ts_file = find_file(subdir, ts_name)
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
    tag = f'{path_num}_{frame_num}_{date_tag}'

    mappings = [
        (f'vel_{path_num}_{frame_num}.tif',       f'vel_{tag}.tif',        'rename'),
        (f'vel_{path_num}_{frame_num}_ERA5.tif',   f'vel_E_{tag}.tif',      'rename'),
        ('waterMask.tif',        f'water_{tag}.tif',       'rename'),
        ('temporalCoherence.tif', f'tc_{tag}.tif',          'rename'),
    ]
    h5_mappings = [
        (['timeseries_ramp_demErr.h5', 'timeseriesResidual_ramp_demErr.h5'],
         f'cum_rd_{tag}.h5'),
        (['timeseries_ERA5_ramp_demErr.h5'],
         f'cum_rdE_{tag}.h5'),
    ]

    results = {'path': path_num, 'frame': frame_num, 'date': date_tag,
               'renamed': 0, 'copied': 0, 'skipped': 0}

    for src_name, dst_name, action in mappings:
        src = find_file(subdir, src_name)
        if not src:
            print(f'  SKIP {src_name} - 未找到')
            results['skipped'] += 1
            continue
        dst = subdir / dst_name
        if dst.is_file():
            print(f'  SKIP {src_name} - {dst_name} 已存在')
            results['skipped'] += 1
            continue
        if dry_run:
            print(f'  {action.upper():6s} {src.name} -> {dst_name}')
        else:
            if action == 'rename':
                src.rename(dst)
                print(f'  RENAME  {src_name} -> {dst_name}')
            else:
                shutil.copy2(str(src), str(dst))
                print(f'  COPY    {src_name} -> {dst_name}')
        results['renamed' if action == 'rename' else 'copied'] += 1

    for src_names, dst_name in h5_mappings:
        src = None
        for name in src_names:
            src = find_file(subdir, name)
            if src:
                break
        if not src:
            print(f'  SKIP {"|".join(src_names)} - 未找到')
            results['skipped'] += 1
            continue
        dst = subdir / dst_name
        if dst.is_file():
            print(f'  SKIP {src.name} - {dst_name} 已存在')
            results['skipped'] += 1
            continue
        if dry_run:
            print(f'  COPY    {src.name} -> {dst_name}')
        else:
            shutil.copy2(str(src), str(dst))
            print(f'  COPY    {src.name} -> {dst_name}')
        results['copied'] += 1

    return results


def main():
    parser = argparse.ArgumentParser(description='批量标准化 {path}_{frame} 目录')
    parser.add_argument('--dir', '-d', required=True, help='包含 {path}_{frame} 子目录的根目录')
    parser.add_argument('--dry-run', '-n', action='store_true', help='仅预览')
    args = parser.parse_args()

    root = Path(args.dir)
    if not root.is_dir():
        print(f'[ERROR] 目录不存在: {root}')
        return

    dirs = sorted([d for d in root.iterdir() if d.is_dir() and re.match(r'^\d+_\d+$', d.name)])
    if not dirs:
        print(f'[ERROR] 未找到 {{path}}_{{frame}} 格式的子目录')
        return

    print(f'扫描到 {len(dirs)} 个子目录')
    if args.dry_run:
        print('[DRY RUN 模式] 仅预览，不实际修改\n')

    total_r, total_c, total_s = 0, 0, 0
    for d in dirs:
        print(f'\n[{d.name}]')
        r = process_dir(d, dry_run=args.dry_run)
        if r:
            total_r += r['renamed']
            total_c += r['copied']
            total_s += r['skipped']

    print(f'\n{"=" * 40}')
    if args.dry_run:
        print(f'[DRY RUN] 将重命名 {total_r} 个，复制 {total_c} 个，跳过 {total_s} 个')
    else:
        print(f'[DONE] 重命名 {total_r} 个，复制 {total_c} 个，跳过 {total_s} 个')


if __name__ == '__main__':
    main()
