"""生成桌面壳占位图标(纯标准库,无 Pillow 依赖,不下载任何东西)。

产出:
  32x32.png / 128x128.png —— RGBA PNG(tauri.conf.json bundle.icon 引用)
  icon.ico                —— 单张 32x32 经典 BMP 格式 ICO:
                             Windows 上 tauri-build 必须有它才能生成 exe 资源,
                             BMP-in-ICO 对 rc.exe 与 tauri 代码生成用的 `ico`
                             crate 兼容性最好(PNG-in-ICO 常规上只用于 256px)。

用法:python make_icons.py(任意 cwd 均可,输出写到脚本所在目录)
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent

BG = (15, 43, 76, 255)        # 深蓝底
STRIPE = (47, 129, 247, 255)  # 亮蓝对角条纹(示意雷达干涉条纹)
BORDER = (232, 240, 254, 255)


def pixels(size: int) -> bytes:
    """占位图案:深蓝底 + 两道对角条纹 + 1px 亮边框,RGBA 自顶向下。"""
    stripe_w = max(2, size // 8)
    buf = bytearray()
    for y in range(size):
        for x in range(size):
            if x in (0, size - 1) or y in (0, size - 1):
                c = BORDER
            elif (abs((x + y) - size) < stripe_w
                  or abs((x + y) - size // 2) < max(1, stripe_w // 2)):
                c = STRIPE
            else:
                c = BG
            buf += bytes(c)
    return bytes(buf)


def png_bytes(size: int, rgba: bytes) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    stride = size * 4
    raw = b"".join(b"\x00" + rgba[y * stride:(y + 1) * stride]
                   for y in range(size))
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)  # 8bit RGBA
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def ico_bytes(size: int, rgba: bytes) -> bytes:
    """经典 BMP-in-ICO:BITMAPINFOHEADER + 自底向上 BGRA + 全 0 AND 掩码。"""
    stride = size * 4
    xor = bytearray()
    for y in range(size - 1, -1, -1):          # DIB 行序自底向上
        row = rgba[y * stride:(y + 1) * stride]
        for x in range(size):
            r, g, b, a = row[x * 4:x * 4 + 4]
            xor += bytes((b, g, r, a))          # BGRA
    and_stride = ((size + 31) // 32) * 4
    and_mask = bytes(and_stride * size)         # 全 0 = 不透明(alpha 通道说了算)
    bih = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0,
                      len(xor) + len(and_mask), 0, 0, 0, 0)
    image = bih + bytes(xor) + and_mask
    entry = struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32,
                        len(image), 6 + 16)
    return struct.pack("<HHH", 0, 1, 1) + entry + image


def main() -> None:
    for size, name in ((32, "32x32.png"), (128, "128x128.png")):
        (HERE / name).write_bytes(png_bytes(size, pixels(size)))
    (HERE / "icon.ico").write_bytes(ico_bytes(32, pixels(32)))
    print("icons written: 32x32.png, 128x128.png, icon.ico")


if __name__ == "__main__":
    main()
