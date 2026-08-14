import os
import sys
import warnings
import numpy as np
import rasterio

warnings.filterwarnings("ignore", category=DeprecationWarning)

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

PARENT_DIR = sys.argv[1] if len(sys.argv) > 1 else r"G:\isce2_gpu\ASF"


def process_folder(folder_path):
    folder_name = os.path.basename(folder_path)
    velocity_path = os.path.join(folder_path, "velocity.tif")
    mask_candidates = ["maskTempCoh.tif", "geo_maskTempCoh.tif"]
    mask_temp_path = None
    for candidate in mask_candidates:
        p = os.path.join(folder_path, candidate)
        if os.path.exists(p):
            mask_temp_path = p
            break
    water_mask_path = os.path.join(folder_path, "waterMask.tif")
    output_path = os.path.join(folder_path, f"vel_{folder_name}.tif")

    if os.path.exists(output_path):
        print(f"[已存在] {output_path}")
        return "skip"

    missing = []
    if not os.path.exists(velocity_path):
        missing.append("velocity.tif")
    if mask_temp_path is None:
        missing.append("maskTempCoh/geo_maskTempCoh")
    if missing:
        print(f"[缺文件] {folder_path}: {', '.join(missing)}")
        return "miss"

    with rasterio.open(velocity_path) as src_vel:
        velocity = np.array(src_vel.read(1), dtype=np.float32)
        profile = src_vel.profile.copy()

    with rasterio.open(mask_temp_path) as src_mask:
        mask = np.array(src_mask.read(1))
    mask_bool = mask == 0

    if os.path.exists(water_mask_path):
        with rasterio.open(water_mask_path) as src_wm:
            water_mask = np.array(src_wm.read(1))
        mask_bool |= (water_mask == 0)

    velocity[mask_bool] = np.nan
    profile.update(dtype="float32", nodata=np.nan)

    with rasterio.open(output_path, "w", **profile) as dst:
        dst.write(velocity, 1)

    print(f"[已处理] {output_path}")
    return "done"


def main():
    if not os.path.isdir(PARENT_DIR):
        print(f"错误: 路径不存在或不是文件夹: {PARENT_DIR}")
        return

    subdirs = [
        d for d in os.listdir(PARENT_DIR)
        if os.path.isdir(os.path.join(PARENT_DIR, d))
    ]

    if not subdirs:
        print(f"没有找到子文件夹: {PARENT_DIR}")
        return

    print(f"在 {PARENT_DIR} 下找到 {len(subdirs)} 个子文件夹\n")

    stats = {"done": 0, "skip": 0, "miss": 0}

    for sub in sorted(subdirs):
        folder_path = os.path.join(PARENT_DIR, sub)
        result = process_folder(folder_path)
        stats[result] += 1

    print(f"\n处理完成: {stats['done']} 个, 已跳过: {stats['skip']} 个, 缺文件: {stats['miss']} 个")


if __name__ == "__main__":
    main()
