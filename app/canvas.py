"""预览画布：显示代理画面 + 拖拽框选遮挡区域。

从 main.py 抽出的独立模块。
原main.py 里它和 MosaicApp 混在一起，导致 main.py 超过 1600 行；
这里单独成文件后，main.py 只剩应用装配与事件处理。

坐标约定（重要）：
  - 视频像素坐标：遮挡框实际存放的坐标，必须按「视频真实分辨率」记录，
    否则导出时坐标全错。
  - 显示坐标：Canvas 上的实际像素，用于鼠标交互。
  两者通过 scale / offset 双向换算。
"""
from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass, field
from pathlib import Path

from .mask import MaskRegion
from .theme import ACCENT, BORDER, CARD

# 槽位卡配色
SLOT_BG = "#23282f"
SLOT_EMPTY_BG = "#2b3138"
SLOT_EDGE = "#4a5460"
SLOT_TEXT = "#c3cbd6"
SLOT_SUB = "#8b95a3"
SLOT_FILLED = "#1f4d33"       # 已填文件的卡片底色
SLOT_FILLED_EDGE = "#3f9e6b"


@dataclass
class SlotCard:
    """一个待合并视频的槽位（画面上的一片区域）。

    点「添加视频」开出一个空槽，把文件拖到它上面就填进来。
    file 为 None 表示空槽。
    """

    index: int                       # 第几个（0 起）
    file: Path | None = None
    # 显示坐标（Canvas 像素）。每帧按画布大小重算，见 _layout_slots。
    x: float = 0.0
    y: float = 0.0
    w: float = 0.0
    h: float = 0.0

    @property
    def filled(self) -> bool:
        return self.file is not None


class MaskCanvas(tk.Frame):
    """预览画面 + 拖框遮挡。

    画面按比例缩放显示，内部坐标（视频像素）与显示坐标之间做双向换算。

    这里刻意继承 tk.Frame 而非 ttk.Frame：需要 -bg / -highlightbackground
    这类 tkinter 专有选项，ttk.Frame 不支持（会抛 TclError）。
    """

    def __init__(self, master, app: "MosaicApp"):
        super().__init__(master, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        self.app = app
        self.image: tk.PhotoImage | None = None
        self.img_w = 0
        self.img_h = 0
        self.scale = 1.0
        self.offset_x = 0
        self.offset_y = 0

        self.regions: list[MaskRegion] = []
        # 待合并视频的槽位卡：点「添加视频」就在画面上开一片空白区域，
        # 把文件拖到某张卡上即加入该槽位。比弹框选文件更直观，
        # 也能一眼看出「第几段」。
        self.slots: list[SlotCard] = []
        self._drag_from: tuple[float, float] | None = None
        self._drag_now: tuple[float, float] | None = None
        self._active_idx: int | None = None
        self._drop_hint = False
        # 关键：显示用的图可能是 640x360 的代理，但遮挡坐标必须记在原视频分辨率
        # （如 1920x1080）上，否则导出时坐标全错。
        self.video_w = 0
        self.video_h = 0

        # 光标说明：
        # 有预览图 -> crosshair（框选）；无图 -> hand2（提示去选文件）。
        # 之前固定 crosshair，在空白处按住拖会像"禁止"一样没反应。
        self.canvas = tk.Canvas(self, bg="#1a1d21", highlightthickness=0,
                                height=340, cursor="crosshair")
        self.canvas.pack(fill="both", expand=True, padx=8, pady=8)

        self.canvas.bind("<Configure>", lambda e: self._redraw())
        self.canvas.bind("<Button-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Button-3>", self._on_right_click)

    # ---------- 图像装载 ----------
    def load_image(self, path: Path) -> None:
        self.image = tk.PhotoImage(file=str(path))
        self.img_w = self.image.width()
        self.img_h = self.image.height()
        self.canvas.config(cursor="crosshair")
        self._redraw()

    def clear_image(self) -> None:
        self.image = None
        self.img_w = self.img_h = 0
        self.regions = []
        # 没图时给出明确可点的光标，而不是让用户以为坏了
        self.canvas.config(cursor="hand2")
        self._redraw()

    def set_frame_image(self, img, w: int, h: int) -> None:
        """显示一帧（播放用）。坐标基准不变，仍是原视频分辨率。"""
        self.image = img
        self.img_w, self.img_h = w, h
        self.canvas.config(cursor="crosshair")
        self._redraw()

    def set_video_size(self, w: int, h: int) -> None:
        self.video_w, self.video_h = w, h
        self._redraw()

    def set_drop_highlight(self, on: bool) -> None:
        """拖拽悬停时高亮边框，给出"松手就能放进来"的视觉反馈。"""
        if self._drop_hint == on:
            return
        self._drop_hint = on
        self.configure(highlightbackground=ACCENT if on else BORDER,
                       highlightthickness=2 if on else 1)
        self._redraw()

    def _fit(self) -> None:
        """计算缩放比例与居中偏移。"""
        cw = max(1, self.canvas.winfo_width())
        ch = max(1, self.canvas.winfo_height())
        if not self.img_w or not self.img_h:
            self.scale, self.offset_x, self.offset_y = 1.0, 0, 0
            return
        self.scale = min(cw / self.img_w, ch / self.img_h)
        self.offset_x = (cw - self.img_w * self.scale) / 2
        self.offset_y = (ch - self.img_h * self.scale) / 2

    def _to_display(self, x: float, y: float) -> tuple[float, float]:
        return self.offset_x + x * self.scale, self.offset_y + y * self.scale

    def _to_video(self, dx: float, dy: float) -> tuple[int, int]:
        """显示坐标 -> 视频真实像素坐标。

        画布上的图可能是代理分辨率（640x360），而遮挡必须记在原视频分辨率
        （1920x1080），所以这里要乘上比例。
        """
        ix = (dx - self.offset_x) / self.scale
        iy = (dy - self.offset_y) / self.scale
        if self.video_w and self.img_w:
            ix *= self.video_w / self.img_w
            iy *= self.video_h / self.img_h
        return max(0, int(ix)), max(0, int(iy))

    def _video_to_display_len(self, vx: float, vy: float) -> tuple[float, float]:
        """视频坐标长度 -> 显示长度。"""
        if self.video_w and self.img_w:
            return vx * (self.img_w / self.video_w), vy * (self.img_h / self.video_h)
        return vx, vy

    # ---------- 槽位卡 ----------
    def _layout_slots(self) -> None:
        """按画布大小重排所有槽位卡的位置。

        有预览图时压在画面下方一行；无图时铺成 2x2 网格，
        最大化可放置面积。
        """
        cw = max(1, self.canvas.winfo_width())
        ch = max(1, self.canvas.winfo_height())
        n = len(self.slots)
        if not n:
            return

        if self.image:
            # 有画面：在画面下方留一条，卡片按数量均分宽度
            band_h = max(56.0, ch * 0.18)
            gap = 8.0
            cw_each = (cw - gap * (n + 1)) / n
            cw_each = max(72.0, min(cw_each, 240.0))
            total = cw_each * n + gap * (n - 1)
            x0 = (cw - total) / 2
            y0 = ch - band_h
            for i, s in enumerate(self.slots):
                s.w = cw_each
                s.h = band_h - 10
                s.x = x0 + i * (cw_each + gap)
                s.y = y0 + 5
        else:
            # 无画面：2x2 网格居中
            cols = 2 if n > 1 else 1
            rows = (n + cols - 1) // cols
            gap = 14.0
            cw_each = min((cw - gap * (cols + 1)) / cols, 300.0)
            ch_each = min((ch - gap * (rows + 1)) / rows, 190.0)
            grid_w = cw_each * cols + gap * (cols - 1)
            grid_h = ch_each * rows + gap * (rows - 1)
            x0 = (cw - grid_w) / 2
            y0 = (ch - grid_h) / 2
            for i, s in enumerate(self.slots):
                r, c = divmod(i, cols)
                s.w = cw_each
                s.h = ch_each
                s.x = x0 + c * (cw_each + gap)
                s.y = y0 + r * (ch_each + gap)

    def slot_at(self, x: float, y: float) -> SlotCard | None:
        """命中测试：坐标落在哪张槽位卡上。"""
        for s in reversed(self.slots):
            if s.x <= x <= s.x + s.w and s.y <= y <= s.y + s.h:
                return s
        return None

    def add_slot(self, file: Path | None = None) -> SlotCard:
        """新增一个槽位并返回。"""
        s = SlotCard(index=len(self.slots), file=file)
        self.slots.append(s)
        self._redraw()
        return s

    def remove_slot(self, slot: SlotCard) -> None:
        if slot in self.slots:
            self.slots.remove(slot)
        for i, s in enumerate(self.slots):
            s.index = i
        self._redraw()

    def clear_slots(self) -> None:
        self.slots.clear()
        self._redraw()

    def fill_slot(self, slot: SlotCard, file: Path) -> None:
        """把文件放进指定槽位（若已满则返回 False）。"""
        slot.file = file
        self._redraw()

    def _draw_slots(self) -> None:
        """画所有槽位卡。"""
        if not self.slots:
            return
        for s in self.slots:
            bg = SLOT_FILLED if s.filled else SLOT_EMPTY_BG
            edge = SLOT_FILLED_EDGE if s.filled else SLOT_EDGE
            self.canvas.create_rectangle(
                s.x, s.y, s.x + s.w, s.y + s.h,
                fill=bg, outline=edge, width=2,
                dash=None if s.filled else (6, 4),
                tags="slot")
            label = f"第 {s.index + 1} 段"
            self.canvas.create_text(
                s.x + s.w / 2, s.y + s.h / 2 - 12, text=label,
                fill=SLOT_TEXT, font=("Microsoft YaHei UI", 10, "bold"))
            if s.filled:
                name = s.file.name if s.file else ""
                # 文件名可能很长，尾部省略避免溢出卡片
                shown = name if len(name) <= 18 else name[:8] + "…" + name[-8:]
                self.canvas.create_text(
                    s.x + s.w / 2, s.y + s.h / 2 + 12, text=shown,
                    fill="#8fd6ae", font=("Microsoft YaHei UI", 9))
                self.canvas.create_text(
                    s.x + s.w / 2, s.y + s.h - 12, text="右键移除",
                    fill=SLOT_SUB, font=("Microsoft YaHei UI", 8))
            else:
                self.canvas.create_text(
                    s.x + s.w / 2, s.y + s.h / 2 + 14, text="把视频拖到这里",
                    fill=SLOT_SUB, font=("Microsoft YaHei UI", 9))

    # ---------- 绘制 ----------
    def _redraw(self) -> None:
        self.canvas.delete("all")
        self._fit()
        if not self.image and not self.slots:
            self.canvas.create_text(
                self.canvas.winfo_width() / 2, self.canvas.winfo_height() / 2,
                text="把视频拖到这里",
                fill="#9aa5b4", font=("Microsoft YaHei UI", 11), justify="center")
            return
        if not self.image:
            # 还没有预览帧，但已经有槽位了 —— 只画卡片即可
            self._layout_slots()
            self._draw_slots()
            return

        x0, y0 = self._to_display(0, 0)
        x1, y1 = self._to_display(self.img_w, self.img_h)
        self.canvas.create_image(x0, y0, image=self.image,
                                 anchor="nw", tags="frame")

        for r in self.regions:
            self._draw_region(r, label=r.label)

        # 正在拖拽的临时框
        if self._drag_from and self._drag_now:
            ax, ay = self._drag_from
            bx, by = self._drag_now
            rx0, ry0 = self._to_display(min(ax, bx), min(ay, by))
            rx1, ry1 = self._to_display(max(ax, bx), max(ay, by))
            self.canvas.create_rectangle(
                rx0, ry0, rx1, ry1, outline="#ffd166", width=2, dash=(4, 3))

        # 选中框高亮描边
        if self._active_idx is not None and self._active_idx < len(self.regions):
            r = self.regions[self._active_idx]
            dx, dy = self._video_to_display_len(r.x, r.y)
            dw, dh = self._video_to_display_len(r.w, r.h)
            ax, ay = self._to_display(dx, dy)
            bx, by = self._to_display(dx + dw, dy + dh)
            self.canvas.create_rectangle(ax - 2, ay - 2, bx + 2, by + 2,
                                         outline="#ffd166", width=2)

        # 槽位卡画在最上层，否则会被画面或遮挡框盖住
        self._layout_slots()
        self._draw_slots()

    def _draw_region(self, r: MaskRegion, label: str) -> None:
        # r 的坐标是视频真实像素，画在代理图上要按比例缩放
        dx, dy = self._video_to_display_len(r.x, r.y)
        dw, dh = self._video_to_display_len(r.w, r.h)
        ax, ay = self._to_display(dx, dy)
        bx, by = self._to_display(dx + dw, dy + dh)
        # 半透明黑 + 描边，直观表示「这里会被纯色盖住」
        self.canvas.create_rectangle(ax, ay, bx, by, fill="#000000",
                                     stipple="gray50", outline="#ffd166", width=1)
        if bx - ax > 40:
            self.canvas.create_text(ax + 5, ay - 8, text=label, anchor="w",
                                    fill="#ffd166", font=("Microsoft YaHei UI", 8, "bold"))

    # ---------- 鼠标交互 ----------
    def _on_press(self, event) -> None:
        if not self.image:
            return
        # 点在已有框上则选中，否则开始新框
        for i, r in enumerate(self.regions):
            dx, dy = self._video_to_display_len(r.x, r.y)
            dw, dh = self._video_to_display_len(r.w, r.h)
            ax, ay = self._to_display(dx, dy)
            bx, by = self._to_display(dx + dw, dy + dh)
            if ax <= event.x <= bx and ay <= event.y <= by:
                self._active_idx = i
                self._drag_from = self._to_video(event.x, event.y)
                self._drag_now = self._drag_from
                self._redraw()
                return
        self._active_idx = None
        self._drag_from = self._to_video(event.x, event.y)
        self._drag_now = self._drag_from
        self._redraw()

    def _on_drag(self, event) -> None:
        if self._drag_from is None:
            return
        self._drag_now = self._to_video(event.x, event.y)
        self._redraw()

    def _on_release(self, event) -> None:
        if self._drag_from is None:
            return
        start = self._drag_from
        end = self._to_video(event.x, event.y)
        self._drag_from = self._drag_now = None

        x0, x1 = sorted((start[0], end[0]))
        y0, y1 = sorted((start[1], end[1]))
        # 夹到视频真实范围内，避免因代理/原图分辨率差异产生越界坐标
        if self.video_w:
            x0, x1 = max(0, min(x0, self.video_w - 1)), max(0, min(x1, self.video_w))
            y0, y1 = max(0, min(y0, self.video_h - 1)), max(0, min(y1, self.video_h))
        # 小于 4 像素视为误触，忽略
        if x1 - x0 < 4 or y1 - y0 < 4:
            self._redraw()
            return

        # 已选中已有框 -> 视为移动/缩放该框
        if self._active_idx is not None and self._active_idx < len(self.regions):
            r = self.regions[self._active_idx]
            lim_w = self.video_w or self.img_w
            lim_h = self.video_h or self.img_h
            nx = max(0, min(r.x + (x0 - start[0]), lim_w - r.w))
            ny = max(0, min(r.y + (y0 - start[1]), lim_h - r.h))
            r.x, r.y = nx, ny
            self._active_idx = None
            self._redraw()
            self.app.on_regions_changed()
            return

        self.regions.append(MaskRegion(x0, y0, x1 - x0, y1 - y0, label="遮挡"))
        self._redraw()
        self.app.on_regions_changed()

    def _on_right_click(self, event) -> None:
        """右键：优先删槽位卡，其次删遮挡框。

        槽位画在上层，所以要先判槽位 —— 否则点卡片会误删下面的遮挡框。
        """
        slot = self.slot_at(event.x, event.y)
        if slot is not None:
            self.remove_slot(slot)
            if hasattr(self.app, "on_slot_removed"):
                self.app.on_slot_removed(slot)
            return

        for i, r in enumerate(self.regions):
            dx, dy = self._video_to_display_len(r.x, r.y)
            dw, dh = self._video_to_display_len(r.w, r.h)
            ax, ay = self._to_display(dx, dy)
            bx, by = self._to_display(dx + dw, dy + dh)
            if ax <= event.x <= bx and ay <= event.y <= by:
                self.regions.pop(i)
                self._redraw()
                self.app.on_regions_changed()
                return
