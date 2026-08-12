"""为 InSAR Agent 桌面端程序化生成正式图标(Pillow 纯离屏绘制,无弹窗)。

主题:「卫星 + 干涉条纹」
  - 深空蓝渐变的圆角方形底,缀少量星点;
  - 中央偏右下为同心弧形干涉条纹:彩虹色相沿半径循环,模拟 InSAR
    干涉图中缠绕的相位条纹;条纹圆心带暖白亮核与辉光(形变中心);
  - 左上角白色卫星剪影(本体 + 双太阳翼 + 碟形天线),向条纹中心
    发射半透明雷达波束,点出"星载雷达对地观测"的含义;
  - 彩虹条纹、近白卫星与浅色内描边均为亮色元素,深色任务栏上清晰。

产出(全部写入本目录,幂等可重跑;固定随机种子,像素级稳定):
  icon.png                    512x512 主图(RGBA)
  icon-16/32/48/128/256.png   各尺寸 RGBA PNG(2048 母版 LANCZOS 缩小)
  32x32.png / 128x128.png     tauri.conf.json bundle.icon 引用的同内容别名
  icon.ico                    16/32/48/128/256 多尺寸打包(BMP-in-ICO,
                              对 rc.exe 与 tauri 用的 ico crate 兼容性最好)
  preview.png                 自检拼板:256 版放大 2x + 深/浅底各尺寸对照

用法(项目根目录):
  .venv\\Scripts\\python.exe desktop\\icons\\make_icons.py
"""
from __future__ import annotations

import colorsys
import math
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

HERE = Path(__file__).resolve().parent

DESIGN = 512          # icon.png 逻辑尺寸
SS = 4                # 4x 超采样,缩小后抗锯齿
S = DESIGN * SS       # 实际绘制画布(2048)

PNG_SIZES = (16, 32, 48, 128, 256)

# ---- 配色 ----
BG_TOP = (16, 38, 79)         # 深空蓝(上)
BG_BOTTOM = (7, 16, 36)       # 深空蓝(下,近黑)
STAR = (230, 240, 255)        # 星点
CORE = (255, 240, 214)        # 条纹中心亮核
CORE_GLOW = (140, 196, 255)   # 中心辉光
SAT_BODY = (246, 250, 255)    # 卫星本体(近白)
SAT_PANEL = (209, 227, 252)   # 太阳翼
PANEL_LINE = (56, 104, 172)   # 太阳翼栅线
BEAM = (196, 232, 255)        # 雷达波束
BORDER = (222, 236, 255)      # 圆角内描边

# ---- 构图(相对画布的坐标/尺寸)----
FRINGE_C = (0.70, 0.76)       # 条纹圆心
SAT_C = (0.27, 0.245)         # 卫星中心
N_RINGS = 6                   # 条纹圈数
R0 = 0.115                    # 最内圈半径
DR = 0.095                    # 圈距
RING_W = 0.055                # 条纹线宽
CORNER = 0.225                # 圆角半径


def rel(p: tuple[float, float]) -> tuple[float, float]:
    """相对坐标 -> 画布像素坐标。"""
    return p[0] * S, p[1] * S


def rot_rect(cx: float, cy: float, w: float, h: float,
             deg: float) -> list[tuple[float, float]]:
    """以 (cx,cy) 为中心、旋转 deg 度的矩形四角。"""
    a = math.radians(deg)
    ca, sa = math.cos(a), math.sin(a)
    return [(cx + x * ca - y * sa, cy + x * sa + y * ca)
            for x, y in ((-w / 2, -h / 2), (w / 2, -h / 2),
                         (w / 2, h / 2), (-w / 2, h / 2))]


def hsv255(h: float, s: float, v: float) -> tuple[int, int, int]:
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, s, v)
    return round(r * 255), round(g * 255), round(b * 255)


def draw_background() -> Image.Image:
    """深空蓝竖向渐变 + 少量星点(固定种子,输出幂等)。"""
    img = Image.new("RGBA", (S, S))
    d = ImageDraw.Draw(img)
    for y in range(S):
        t = y / (S - 1)
        c = tuple(round(a + (b - a) * t) for a, b in zip(BG_TOP, BG_BOTTOM))
        d.line([(0, y), (S, y)], fill=c + (255,))

    rng = random.Random(20260812)
    sx, sy = rel(SAT_C)
    for _ in range(18):
        x, y = rng.uniform(0, S), rng.uniform(0, S * 0.72)
        if math.hypot(x - sx, y - sy) < 0.17 * S:
            continue  # 卫星附近留空,避免细节打架
        r = rng.uniform(0.0016, 0.0038) * S
        a = rng.randint(70, 160)
        d.ellipse([x - r, y - r, x + r, y + r], fill=STAR + (a,))
    return img


def draw_fringes(img: Image.Image) -> None:
    """同心弧形干涉条纹:色相沿半径循环(模拟缠绕相位)+ 柔光 + 亮核。"""
    cx, cy = rel(FRINGE_C)

    # 中心辉光,垫在条纹之下
    glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gr = 0.30 * S
    gd.ellipse([cx - gr, cy - gr, cx + gr, cy + gr], fill=CORE_GLOW + (70,))
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(0.07 * S)))

    soft = Image.new("RGBA", (S, S), (0, 0, 0, 0))   # 发光晕层
    crisp = Image.new("RGBA", (S, S), (0, 0, 0, 0))  # 条纹主体层
    sd, kd = ImageDraw.Draw(soft), ImageDraw.Draw(crisp)
    for i in range(N_RINGS):
        r = (R0 + i * DR) * S
        hue = 0.99 + i * 0.155  # 红->黄->绿->青->蓝->紫,hsv255 内取模缠绕
        color = hsv255(hue, 0.88, 1.0)
        box = [cx - r, cy - r, cx + r, cy + r]
        sd.ellipse(box, outline=color + (90,), width=round(RING_W * 1.9 * S))
        kd.ellipse(box, outline=color + (242,), width=round(RING_W * S))
    img.alpha_composite(soft.filter(ImageFilter.GaussianBlur(0.012 * S)))
    img.alpha_composite(crisp)

    d = ImageDraw.Draw(img)
    cr = 0.040 * S
    d.ellipse([cx - cr, cy - cr, cx + cr, cy + cr], fill=CORE + (255,))


def draw_satellite(img: Image.Image) -> None:
    """左上卫星剪影与指向条纹中心的半透明雷达波束。"""
    sx, sy = rel(SAT_C)
    cx, cy = rel(FRINGE_C)
    ang = math.atan2(cy - sy, cx - sx)      # 波束方向(卫星 -> 条纹心)
    ux, uy = math.cos(ang), math.sin(ang)
    px, py = -uy, ux                        # 翼展方向(与波束垂直)
    wing_deg = math.degrees(ang) + 90.0

    # 雷达波束:窄口在卫星、宽口罩向条纹,叠在条纹上、卫星下
    beam = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    bd = ImageDraw.Draw(beam)
    s0 = 0.085 * S
    s1 = math.hypot(cx - sx, cy - sy) - 0.055 * S
    w0, w1 = 0.014 * S, 0.150 * S
    quad = [(sx + ux * s0 + px * w0, sy + uy * s0 + py * w0),
            (sx + ux * s1 + px * w1, sy + uy * s1 + py * w1),
            (sx + ux * s1 - px * w1, sy + uy * s1 - py * w1),
            (sx + ux * s0 - px * w0, sy + uy * s0 - py * w0)]
    bd.polygon(quad, fill=BEAM + (44,))
    edge_w = round(0.004 * S)
    bd.line([quad[0], quad[1]], fill=BEAM + (110,), width=edge_w)
    bd.line([quad[3], quad[2]], fill=BEAM + (110,), width=edge_w)
    img.alpha_composite(beam)

    d = ImageDraw.Draw(img)
    body_w, body_h = 0.078 * S, 0.056 * S   # 本体:沿翼展 x 沿波束
    gap = 0.014 * S                         # 本体与翼的桁架间隙
    wing_l, wing_h = 0.105 * S, 0.046 * S   # 单侧太阳翼

    # 桁架
    for sgn in (+1, -1):
        x0 = sx + px * sgn * (body_w / 2)
        y0 = sy + py * sgn * (body_w / 2)
        x1 = sx + px * sgn * (body_w / 2 + gap + wing_l)
        y1 = sy + py * sgn * (body_w / 2 + gap + wing_l)
        d.line([(x0, y0), (x1, y1)], fill=SAT_BODY + (255,),
               width=round(0.008 * S))

    # 太阳翼 + 栅线
    for sgn in (+1, -1):
        off = body_w / 2 + gap + wing_l / 2
        wx, wy = sx + px * sgn * off, sy + py * sgn * off
        d.polygon(rot_rect(wx, wy, wing_l, wing_h, wing_deg),
                  fill=SAT_PANEL + (255,), outline=SAT_BODY + (255,),
                  width=round(0.004 * S))
        for t in (-wing_l / 6, wing_l / 6):
            lx, ly = wx + px * t, wy + py * t
            d.line([(lx - ux * wing_h / 2, ly - uy * wing_h / 2),
                    (lx + ux * wing_h / 2, ly + uy * wing_h / 2)],
                   fill=PANEL_LINE + (230,), width=round(0.005 * S))

    # 本体
    d.polygon(rot_rect(sx, sy, body_w, body_h, wing_deg),
              fill=SAT_BODY + (255,))

    # 碟形天线(朝波束方向)+ 馈源
    ax = sx + ux * (body_h / 2 + 0.013 * S)
    ay = sy + uy * (body_h / 2 + 0.013 * S)
    ar = 0.023 * S
    d.ellipse([ax - ar, ay - ar, ax + ar, ay + ar], fill=SAT_BODY + (255,))
    fx, fy = ax + ux * 0.029 * S, ay + uy * 0.029 * S
    d.line([(ax, ay), (fx, fy)], fill=SAT_BODY + (255,),
           width=round(0.005 * S))
    fr = 0.009 * S
    d.ellipse([fx - fr, fy - fr, fx + fr, fy + fr], fill=CORE + (255,))


def compose_master() -> Image.Image:
    """在 2048 画布上完成绘制,裁圆角、加内描边,返回 RGBA 母版。"""
    img = draw_background()
    draw_fringes(img)
    draw_satellite(img)

    radius = CORNER * S
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, S - 1, S - 1],
                                           radius=radius, fill=255)
    out = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)

    # 浅色内描边:深色任务栏上勾出轮廓
    bw = round(0.011 * S)
    ImageDraw.Draw(out).rounded_rectangle(
        [bw / 2, bw / 2, S - 1 - bw / 2, S - 1 - bw / 2],
        radius=radius - bw / 2, outline=BORDER + (95,), width=bw)
    return out


def build_preview(sized: dict[int, Image.Image]) -> None:
    """自检拼板:中央 256 版放大 2x(骑跨深浅分界),底部两排真实尺寸。"""
    w, h = 1120, 780
    board = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(board)
    d.rectangle([0, 0, w // 2 - 1, h], fill=(16, 18, 24))    # 深色半区
    d.rectangle([w // 2, 0, w, h], fill=(238, 241, 246))     # 浅色半区

    big = sized[256].resize((512, 512), Image.NEAREST)       # 256 版放大 2x
    board.paste(big, ((w - 512) // 2, 40), big)

    for x0 in (72, w // 2 + 72):                             # 深/浅底各一排
        x = x0
        for n in (128, 48, 32, 16):
            board.paste(sized[n], (x, 728 - n), sized[n])
            x += n + 28
    board.save(HERE / "preview.png")


def punch_small(im: Image.Image) -> Image.Image:
    """小尺寸补偿:提饱和/对比,防止缩到 16/32px 后发灰(alpha 保持不动)。"""
    rgb = ImageEnhance.Contrast(
        ImageEnhance.Color(im.convert("RGB")).enhance(1.30)).enhance(1.08)
    out = rgb.convert("RGBA")
    out.putalpha(im.getchannel("A"))
    return out


def main() -> None:
    master = compose_master()
    sized = {}
    for n in (*PNG_SIZES, DESIGN):
        im = master.resize((n, n), Image.LANCZOS)
        sized[n] = punch_small(im) if n <= 32 else im

    sized[DESIGN].save(HERE / "icon.png")
    for n in PNG_SIZES:
        sized[n].save(HERE / f"icon-{n}.png")
    # tauri.conf.json bundle.icon 引用的旧命名,写同内容别名
    sized[32].save(HERE / "32x32.png")
    sized[128].save(HERE / "128x128.png")

    sized[256].save(HERE / "icon.ico", format="ICO", bitmap_format="bmp",
                    sizes=[(n, n) for n in PNG_SIZES],
                    append_images=[sized[n] for n in PNG_SIZES if n != 256])

    build_preview(sized)
    names = sorted(p.name for p in HERE.iterdir()
                   if p.suffix in (".png", ".ico"))
    print("icons written:", ", ".join(names))


if __name__ == "__main__":
    main()
