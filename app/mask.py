"""遮挡区域模型与坐标换算。

记录仪画面的 VIN 是固定叠加层，但分辨率可能随设备/固件变化，
因此预设坐标以 1920x1080 为基准，实际使用时按比例映射到真实分辨率。
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

# 以 1920x1080 为基准的 VIN 预设坐标（已用真实帧验证：LFZ63AN58RD023292 完全被覆盖）
REF_W, REF_H = 1920, 1080
VIN_PRESET_BOX = (1459, 1018, 344, 20)  # x, y, w, h


def scale_box(box: tuple[int, int, int, int], src: tuple[int, int],
              dst: tuple[int, int]) -> tuple[int, int, int, int]:
    """把 (x,y,w,h) 从 src 分辨率等比映射到 dst 分辨率，并做边界裁剪。"""
    sw, sh = src
    dw, dh = dst
    if not sw or not sh or not dw or not dh:
        return box
    x, y, w, h = box
    rx = dw / sw
    ry = dh / sh
    nx = round(x * rx)
    ny = round(y * ry)
    nw = max(1, round(w * rx))
    nh = max(1, round(h * ry))
    # 裁剪到画布内
    nx = max(0, min(nx, dw - 1))
    ny = max(0, min(ny, dh - 1))
    nw = min(nw, dw - nx)
    nh = min(nh, dh - ny)
    return nx, ny, nw, nh


@dataclass
class MaskRegion:
    """一个遮挡区。坐标始终存储为「真实视频分辨率」下的像素值。"""
    x: int
    y: int
    w: int
    h: int
    color: str = "black"
    label: str = "自定义"

    def normalized(self) -> tuple[float, float, float, float]:
        return self.x, self.y, self.w, self.h

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class MaskPlan:
    """整段视频的遮挡方案。"""
    width: int
    height: int
    use_vin_preset: bool = True
    vin_box: tuple[int, int, int, int] = VIN_PRESET_BOX
    extra: list[MaskRegion] = field(default_factory=list)
    # 打码效果：solid=纯色遮挡 / mosaic=马赛克 / blur=高斯模糊
    effect: str = "solid"
    # 马赛克强度：目标缩略宽度（越小块越大）。模糊强度：boxblur 半径
    intensity: int = 0

    def regions(self) -> list[MaskRegion]:
        out: list[MaskRegion] = []
        if self.use_vin_preset:
            out.append(MaskRegion(*self.vin_box, label="VIN"))
        out.extend(self.extra)
        return out

    def to_dict(self) -> dict:
        return {
            "width": self.width,
            "height": self.height,
            "use_vin_preset": self.use_vin_preset,
            "vin_box": list(self.vin_box),
            "extra": [r.to_dict() for r in self.extra],
            "effect": self.effect,
            "intensity": self.intensity,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "MaskPlan":
        return cls(
            width=int(d.get("width", REF_W)),
            height=int(d.get("height", REF_H)),
            use_vin_preset=bool(d.get("use_vin_preset", True)),
            vin_box=tuple(d.get("vin_box", VIN_PRESET_BOX)),  # type: ignore[arg-type]
            extra=[MaskRegion(**r) for r in d.get("extra", [])],
            effect=str(d.get("effect", "solid")),
            intensity=int(d.get("intensity", 0)),
        )


# ---------- 打码效果：生成 ffmpeg 滤镜 ----------

# filter_complex 的统一输出标签（调用方据此 -map）
FILTER_OUT_LABEL = "outv"
EFFECT_LABELS = {
    "solid": "纯色遮挡（最安全）",
    "mosaic": "马赛克（像素化）",
    "blur": "高斯模糊",
}


def _mosaic_chain(regions: list[MaskRegion], block_w: int) -> str:
    """马赛克：crop 出区域 -> 缩到很小 -> 放大回原尺寸 -> 逐个 overlay 回去。

    必须逐个串行 overlay：一次 split 多路再多次 overlay 会让滤镜图膨胀且
    容易超出 ffmpeg 的滤镜连接数上限，串行链更稳。
    """
    filters: list[str] = []
    prev = "0:v"
    for i, r in enumerate(regions):
        # 缩略宽度按区域大小自适应，保证既糊掉又不过度损失分辨率
        tw = max(2, min(block_w, r.w))
        th = max(1, min(max(1, r.h // 8), max(1, r.h)))
        # 按原始宽高比算缩略高度，避免把字符压成一条线
        th = max(1, round(r.h * tw / max(1, r.w)))
        filters.append(f"[{prev}]split=2[base{i}][sub{i}]")
        filters.append(
            f"[sub{i}]crop={r.w}:{r.h}:{r.x}:{r.y},"
            f"scale={tw}:{th}:flags=neighbor,"
            f"scale={r.w}:{r.h}:flags=neighbor[ms{i}]"
        )
        filters.append(f"[base{i}][ms{i}]overlay={r.x}:{r.y}[out{i}]")
        prev = f"out{i}"
    # 统一输出标签，调用方固定 -map [outv]
    filters.append(f"[{prev}]null[outv]")
    return ";".join(filters)


def _blur_chain(regions: list[MaskRegion], radius: int) -> str:
    """高斯模糊：crop -> boxblur -> overlay 回原位。

    boxblur 比 gblur 更适合小区域：gblur 对 20px 高的 VIN 条会溢出到邻域，
    boxblur 只在区域内扩散。
    """
    filters: list[str] = []
    prev = "0:v"
    for i, r in enumerate(regions):
        # boxblur 的三个平面半径各有约束，且 chroma 平面尺寸是亮度的一半
        # （yuv420p），所以色度半径上限只有亮度的 1/2。
        # 亮度：radius 必须 < w/2 且 < h/2；色度：必须 < w/4 且 < h/4。
        lr = max(0, min(radius, max(0, r.w // 2 - 1), max(0, r.h // 2 - 1)))
        cr = max(0, min(radius // 2, max(0, r.w // 4 - 1), max(0, r.h // 4 - 1)))
        ar = max(0, min(radius // 2, max(0, r.w // 4 - 1), max(0, r.h // 4 - 1)))
        filters.append(f"[{prev}]split=2[bbase{i}][bsub{i}]")
        filters.append(
            f"[bsub{i}]crop={r.w}:{r.h}:{r.x}:{r.y},"
            f"boxblur=luma_radius={lr}:luma_power=2:"
            f"chroma_radius={cr}:chroma_power=2:"
            f"alpha_radius={ar}:alpha_power=2[bl{i}]"
        )
        filters.append(f"[bbase{i}][bl{i}]overlay={r.x}:{r.y}[bout{i}]")
        prev = f"bout{i}"
    filters.append(f"[{prev}]null[outv]")
    return ";".join(filters)


def is_complex_filter(flt: str | None) -> bool:
    """滤镜串是否引用了输入标签（0:v）。引用了就必须走 -filter_complex + -map。"""
    return bool(flt) and "0:v" in (flt or "")


def build_filter(plan: MaskPlan) -> str | None:
    """按效果生成 -vf / -filter_complex 滤镜串。没有遮挡区时返回 None。"""
    regions = plan.regions()
    if not regions:
        return None

    if plan.effect == "mosaic":
        block = plan.intensity if plan.intensity > 0 else 21
        return _mosaic_chain(regions, block)

    if plan.effect == "blur":
        radius = plan.intensity if plan.intensity > 0 else max(3, plan.height // 90)
        return _blur_chain(regions, radius)

    # 默认纯色：drawbox 最快最稳，且块内像素趋同无法还原
    return ",".join(
        f"drawbox=x={r.x}:y={r.y}:w={r.w}:h={r.h}:color={r.color}@1:t=fill"
        for r in regions
    )
