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

from .mask import MaskRegion
from .theme import ACCENT, BORDER, CARD


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

    # ---------- 绘制 ----------
    def _redraw(self) -> None:
        self.canvas.delete("all")
        self._fit()
        if not self.image:
            self.canvas.create_text(
                self.canvas.winfo_width() / 2, self.canvas.winfo_height() / 2,
                text="把 .h264 文件拖到这里\n或点左上角「选择视频文件…」",
                fill="#9aa5b4", font=("Microsoft YaHei UI", 11), justify="center")
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
        """右键删除点击到的框。"""
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
