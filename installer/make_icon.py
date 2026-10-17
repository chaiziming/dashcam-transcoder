"""生成应用图标 build_assets/app.ico。

不依赖 PIL —— 直接写 BMP 再套 ICO 容器，纯标准库实现，
这样构建安装包时不必额外装任何依赖。

图案：深蓝圆角底 + 白色摄像机轮廓 + 蓝色挡片，表示"视频 + 打码"。
"""
from __future__ import annotations

import struct
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "build_assets" / "app.ico"
SIZES = [16, 24, 32, 48, 64, 128, 256]

BG = (47, 111, 235, 255)       # 主题蓝
FG = (255, 255, 255, 255)      # 白色
SHIELD = (255, 209, 102, 255)  # 琥珀色挡片


def _draw(size: int) -> bytes:
    """画一个 size×size 的 RGBA 图标（自底向上 BGRA 像素）。"""
    px = [[(0, 0, 0, 0)] * size for _ in range(size)]
    pad = max(1, size // 16)
    radius = max(2, size // 5)

    def blend(x: int, y: int, color) -> None:
        if 0 <= x < size and 0 <= y < size:
            px[y][x] = color

    # 圆角矩形底
    for y in range(size):
        for x in range(size):
            dx = min(x, size - 1 - x)
            dy = min(y, size - 1 - y)
            if dx < radius and dy < radius:
                d = ((radius - dx) ** 2 + (radius - dy) ** 2) ** 0.5
                if d > radius:
                    continue
            blend(x, y, BG)

    # 摄像机机身（圆角矩形）
    bw, bh = size * 46 // 100, size * 34 // 100
    bx, by = (size - bw) // 2, size * 42 // 100
    r2 = max(1, size // 12)
    for y in range(by, by + bh):
        for x in range(bx, bx + bw):
            ddx = min(x - bx, bx + bw - 1 - x)
            ddy = min(y - by, by + bh - 1 - y)
            if ddx < r2 and ddy < r2:
                if ((r2 - ddx) ** 2 + (r2 - ddy) ** 2) ** 0.5 > r2:
                    continue
            blend(x, y, FG)

    # 镜头三角形（朝右）
    tx, ty0 = bx + bw, by + bh // 4
    for i in range(size * 14 // 100):
        for t in range(i):
            blend(tx + t, ty0 + i - t // 2, FG)
            blend(tx + t, by + bh - 1 - (ty0 - by) - i + t // 2 + 1, FG)

    # 琥珀色挡片（表示打码），压在机身右下
    sw, sh = bw // 2, max(1, bh // 5)
    sx, sy = bx + bw - sw - max(0, size // 32), by + bh - sh
    for y in range(sy, sy + sh):
        for x in range(sx, sx + sw):
            blend(x, y, SHIELD)

    # ICO 内 BMP 像素是 BGRA、自下而上
    out = bytearray()
    for y in range(size - 1, -1, -1):
        for x in range(size):
            r, g, b, a = px[y][x]
            out += bytes((b, g, r, a))
    # AND 掩码（全 0 表示都用 alpha）
    out += bytes(size * 4)
    # BMP 头：BITMAPINFOHEADER + 高度加倍（XOR+AND）
    header = struct.pack(
        "<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0,
        len(out) - size * 4, 0, 0, 0, 0)
    return header + bytes(out)


def build() -> Path:
    images = [_draw(s) for s in SIZES]
    # ICONDIR + ICONDIRENTRY + 各图像数据
    out = bytearray(struct.pack("<HHH", 0, 1, len(images)))
    offset = 6 + 16 * len(images)
    for s, data in zip(SIZES, images):
        out += struct.pack("<BBBBHHII", s if s < 256 else 0, s if s < 256 else 0,
                           0, 0, 1, 32, len(data), offset)
        offset += len(data)
    for data in images:
        out += data
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(bytes(out))
    return OUT


if __name__ == "__main__":
    p = build()
    print(f"图标已生成: {p}  ({p.stat().st_size} bytes, 尺寸 {SIZES})")