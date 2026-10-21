"""预览画布：一个画布 = 一段视频（多个画布上下排布，互不干扰）。

与旧版「一个大画布内平分 N 个槽位」的区别：
  - 旧版：N 段视频挤在同一块画布里，槽位只是画布上的子区域
  - 现在：N 个**独立**画布，每个画布有自己的画面、自己的拖拽交互

坐标约定（三层，必须分清）：
  - 视频像素坐标：视频真实分辨率下的像素
  - 全局遮挡坐标（canonical）：app.regions 里存的坐标，基准分辨率是
    app.canon_w × app.canon_h（通常是第一段的真实分辨率）。
    所有段**共用这一套框**，所以必须有统一基准。
  - 显示坐标：Canvas 上的实际像素

换算链（显示 → 全局）：
    显示 px --/scale--> 代理图像素 --×(video/img)--> 视频像素
          --×(canon/video)--> 全局坐标
反向同理。视频分辨率与基准分辨率的比例会互相抵消，
所以同一个全局框在任意画布上都落在**相同的相对位置**上。

关键：显示用的图是 640x360 的代理，但遮挡坐标必须记在真实分辨率基准上，
否则导出时坐标全错。
"""
from __future__ import annotations

import tkinter as tk
from pathlib import Path

from .mask import MaskRegion
from .theme import ACCENT, BORDER, CARD

# 画布底色：深色「黑布」区
SLOT_BG = "#1a1d21"
# 未激活画布的边框
EDGE = "#d7dee7"
# 拖拽悬停高亮（琥珀色，与激活态的蓝色区分）
DROP_EDGE = "#f0a020"
# 画布内文字
SLOT_TEXT = "#c3cbd6"
SLOT_SUB = "#8b95a3"

# 画布的**最小**高度（像素）。实际高度由 main._layout_segments 按段数
# 动态分配：1 段时铺满整个左区，2 段时各占一半，都不够时才用这个下限并滚动。
#
# 下限不能太低：16:9 的视频在 645px 宽的左栏里按宽铺满需要约 363px 高，
# 低于 200px 时 VIN 条那一条就没法点中了。
CANVAS_HEIGHT = 200

# 画布之间的垂直间距（像素）。必须与 main.add_segment 里 grid 的 pady 一致，
# 否则分配高度时会把间距算漏，导致内容总高略微超出视口而误弹滚动条。
SEG_GAP = 8


class MaskCanvas(tk.Frame):
    """一段视频的预览画面 + 拖框遮挡。

    画面按比例缩放显示，内部坐标（全局遮挡坐标）与显示坐标之间做双向换算。

    这里刻意继承 tk.Frame 而非 ttk.Frame：需要 -bg / -highlightbackground
    这类 tkinter 专有选项，ttk.Frame 不支持（会抛 TclError）。
    """

    def __init__(self, master, app, index: int = 0,
                 height: int = CANVAS_HEIGHT):
        # 画布外框线：每个画布是独立的一块，边框用来区分「哪块是激活的」
        super().__init__(master, bg=CARD, highlightbackground=EDGE,
                         highlightthickness=1)
        self.app = app
        self.index = index
        self.image: tk.PhotoImage | None = None
        self.img_w = 0
        self.img_h = 0
        self.scale = 1.0
        self.offset_x = 0
        self.offset_y = 0

        self._drag_from: tuple[float, float] | None = None
        self._drag_now: tuple[float, float] | None = None
        self._active_idx: int | None = None
        self._drop_hint = False
        self._is_active = False
        # 关键：显示用的图可能是 640x360 的代理，但遮挡坐标必须记在
        # 基准分辨率（app.canon_w × app.canon_h）上，否则导出时坐标全错。
        self.video_w = 0
        self.video_h = 0

        self.canvas = tk.Canvas(self, bg=SLOT_BG, highlightthickness=0,
                                height=height, cursor="crosshair")
        # 内层画布用 grid 铺满外框内容区（外框 highlightthickness 恒为 1，
        # 两段画布的内容区宽度完全一致，遮挡框才能像素级对齐）。
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        self.canvas.bind("<Configure>", lambda e: self._redraw())
        self.canvas.bind("<Button-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Button-3>", self._on_right_click)

    # ---------- 外部状态（都是全局共享的）----------
    @property
    def regions(self) -> list[MaskRegion]:
        """全局共享的遮挡框列表（所有画布共用一套）。"""
        return self.app.regions

    @regions.setter
    def regions(self, value: list[MaskRegion]) -> None:
        self.app.regions = list(value)

    @property
    def canon_w(self) -> int:
        return self.app.canon_w

    @property
    def canon_h(self) -> int:
        return self.app.canon_h

    # ---------- 图像装载 ----------
    def load_image(self, path: Path) -> None:
        self.image = tk.PhotoImage(file=str(path))
        self.img_w = self.image.width()
        self.img_h = self.image.height()
        self.canvas.config(cursor="crosshair")
        self._redraw()

    def set_thumbnail(self, path: Path) -> None:
        """显示首帧静帧（非激活段用它占位）。"""
        self.load_image(path)

    def clear_image(self) -> None:
        self.image = None
        self.img_w = self.img_h = 0
        # 没图时给出明确可点的光标，而不是让用户以为坏了
        self.canvas.config(cursor="hand2")
        self._redraw()

    def set_frame_image(self, img, w: int, h: int) -> None:
        """显示一帧（播放用）。坐标基准不变，仍是全局遮挡坐标。"""
        self.image = img
        self.img_w, self.img_h = w, h
        self.canvas.config(cursor="crosshair")
        self._redraw()

    def set_video_size(self, w: int, h: int) -> None:
        self.video_w, self.video_h = w, h
        self._redraw()

    def set_active(self, active: bool) -> None:
        """标记是否为当前激活段（激活段的边框高亮，且只有它会被播放）。"""
        if self._is_active == active:
            return
        self._is_active = active
        self._apply_edge()
        self._redraw()

    def set_drop_highlight(self, on: bool) -> None:
        """拖拽悬停时高亮边框，给出"松手就能放进来"的视觉反馈。"""
        if self._drop_hint == on:
            return
        self._drop_hint = on
        self._apply_edge()
        self._redraw()

    def _apply_edge(self) -> None:
        """更新外框线。

        注意：highlightthickness 必须**恒定**。它是画在控件内侧的，
        厚度一变就吃掉内层画布的像素（实测 1px vs 2px 会让内层画布差 2px），
        而遮挡框是按像素定位的，同一套框在两段画布上就会错位。
        激活/拖入态的区分改用「颜色 + 画布内侧描边」表达。
        """
        if self._drop_hint:
            edge = DROP_EDGE
        elif self._is_active:
            edge = ACCENT
        else:
            edge = EDGE
        self.configure(highlightbackground=edge, highlightthickness=1)

    # ---------- 坐标换算 ----------
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

    def _to_canon(self, dx: float, dy: float) -> tuple[int, int]:
        """显示坐标 -> 全局遮挡坐标。

        显示的是代理图（640x360），而全局坐标记在基准分辨率上，
        所以这里要把「代理图 -> 视频 -> 基准」两级比例都乘上。
        """
        ix = (dx - self.offset_x) / self.scale
        iy = (dy - self.offset_y) / self.scale
        if self.video_w and self.img_w:
            ix *= self.video_w / self.img_w
            iy *= self.video_h / self.img_h
        if self.canon_w and self.video_w:
            ix *= self.canon_w / self.video_w
            iy *= self.canon_h / self.video_h
        return max(0, int(ix)), max(0, int(iy))

    def _canon_len_to_display(self, cx: float, cy: float) -> tuple[float, float]:
        """全局坐标长度 -> 显示长度（返回的是「代理图像素」尺度）。

        canon --×(video/canon)--> video --×(img/video)--> img
        两级比例相乘等于 img/canon，所以这里不需要知道视频分辨率。
        """
        if not self.img_w or not self.canon_w:
            return cx, cy
        return cx * self.img_w / self.canon_w, cy * self.img_h / self.canon_h

    # ---------- 绘制 ----------
    def _redraw(self) -> None:
        self.canvas.delete("all")
        self._fit()
        if not self.image:
            self._draw_empty_state()
            self._draw_state_border()
            return

        x0, y0 = self._to_display(0, 0)
        x1, y1 = self._to_display(self.img_w, self.img_h)
        self.canvas.create_image(x0, y0, image=self.image,
                                 anchor="nw", tags="frame")

        for r in self.regions:
            self._draw_region(r)

        # 正在拖拽的临时框
        if self._drag_from and self._drag_now:
            ax, ay = self._drag_from
            bx, by = self._drag_now
            rx0, ry0 = self._canon_to_display(min(ax, ay), min(ay, by))
            rx1, ry1 = self._canon_to_display(max(ax, bx), max(ay, by))
            self.canvas.create_rectangle(
                rx0, ry0, rx1, ry1, outline="#ffd166", width=2, dash=(4, 3))

        # 选中框高亮描边
        if self._active_idx is not None and self._active_idx < len(self.regions):
            self._draw_region(self.regions[self._active_idx], selected=True)

        self._draw_state_border()

    def _draw_state_border(self) -> None:
        """画布内侧的状态描边。

        外框线的粗细必须恒定（见 _apply_edge 的说明），
        所以「哪块是激活的」用内侧描边表达：
        激活 = 蓝色实线，拖入悬停 = 琥珀色虚线。
        """
        if not (self._is_active or self._drop_hint):
            return
        w = max(1, self.canvas.winfo_width())
        h = max(1, self.canvas.winfo_height())
        if self._drop_hint:
            self.canvas.create_rectangle(1, 1, w - 2, h - 2,
                                         outline=DROP_EDGE, width=2,
                                         dash=(6, 3))
        else:
            self.canvas.create_rectangle(1, 1, w - 2, h - 2,
                                         outline=ACCENT, width=2)

    def _canon_to_display(self, cx: float, cy: float) -> tuple[float, float]:
        dx, dy = self._canon_len_to_display(cx, cy)
        return self._to_display(dx, dy)

    def _draw_empty_state(self) -> None:
        """空画布：明确告诉用户「把视频拖到这里」。"""
        cx = self.canvas.winfo_width() / 2
        cy = self.canvas.winfo_height() / 2
        self.canvas.create_text(
            cx, cy - 20, text="把视频拖到这里",
            fill="#9aa5b4", font=("Microsoft YaHei UI", 12))
        if len(getattr(self.app, "segments", [])) <= 1:
            self.canvas.create_text(
                cx, cy + 14, text="或点左上角「添加视频」，再开一个画布",
                fill="#6b7684", font=("Microsoft YaHei UI", 9))

    def _draw_region(self, r: MaskRegion, selected: bool = False) -> None:
        # r 是全局坐标，画在代理图上要按比例缩放
        dx, dy = self._canon_len_to_display(r.x, r.y)
        dw, dh = self._canon_len_to_display(r.w, r.h)
        ax, ay = self._to_display(dx, dy)
        bx, by = self._to_display(dx + dw, dy + dh)
        # 半透明黑 + 描边，直观表示「这里会被纯色盖住」
        self.canvas.create_rectangle(ax, ay, bx, by, fill="#000000",
                                     stipple="gray50", outline="#ffd166", width=1)
        if selected:
            self.canvas.create_rectangle(ax - 2, ay - 2, bx + 2, by + 2,
                                         outline="#ffd166", width=2)
        if bx - ax > 40:
            self.canvas.create_text(ax + 5, ay - 8, text=r.label, anchor="w",
                                    fill="#ffd166", font=("Microsoft YaHei UI", 8, "bold"))

    # ---------- 鼠标交互 ----------
    def _on_press(self, event) -> None:
        # 点击画布 = 切到这一段（只有激活段会被播放）
        if hasattr(self.app, "set_active_segment"):
            self.app.set_active_segment(self.index)

        if not self.image:
            return
        # 点在已有框上则选中，否则开始新框
        for i, r in enumerate(self.regions):
            ax, ay, bx, by = self._region_display_rect(r)
            if ax <= event.x <= bx and ay <= event.y <= by:
                self._active_idx = i
                self._drag_from = self._to_canon(event.x, event.y)
                self._drag_now = self._drag_from
                self._redraw()
                return
        self._active_idx = None
        self._drag_from = self._to_canon(event.x, event.y)
        self._drag_now = self._drag_from
        self._redraw()

    def _region_display_rect(self, r: MaskRegion) -> tuple[float, float, float, float]:
        dx, dy = self._canon_len_to_display(r.x, r.y)
        dw, dh = self._canon_len_to_display(r.w, r.h)
        ax, ay = self._to_display(dx, dy)
        bx, by = self._to_display(dx + dw, dy + dh)
        return ax, ay, bx, by

    def _on_drag(self, event) -> None:
        if self._drag_from is None:
            return
        self._drag_now = self._to_canon(event.x, event.y)
        self._redraw()

    def _on_release(self, event) -> None:
        if self._drag_from is None:
            return
        start = self._drag_from
        end = self._to_canon(event.x, event.y)
        self._drag_from = self._drag_now = None

        x0, x1 = sorted((start[0], end[0]))
        y0, y1 = sorted((start[1], end[1]))
        # 夹到基准分辨率范围内，避免因代理/原图分辨率差异产生越界坐标
        if self.canon_w:
            x0, x1 = max(0, min(x0, self.canon_w - 1)), max(0, min(x1, self.canon_w))
            y0, y1 = max(0, min(y0, self.canon_h - 1)), max(0, min(y1, self.canon_h))
        # 小于 4 像素视为误触，忽略
        if x1 - x0 < 4 or y1 - y0 < 4:
            self._redraw()
            return

        # 已选中已有框 -> 视为移动该框
        if self._active_idx is not None and self._active_idx < len(self.regions):
            r = self.regions[self._active_idx]
            lim_w = self.canon_w or self.video_w
            lim_h = self.canon_h or self.video_h
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
        """右键：删除点到的遮挡框。"""
        for i, r in enumerate(self.regions):
            ax, ay, bx, by = self._region_display_rect(r)
            if ax <= event.x <= bx and ay <= event.y <= by:
                self.regions.pop(i)
                self._redraw()
                self.app.on_regions_changed()
                return


class SegmentCard(tk.Frame):
    """一个分段卡片 = 标题条 + 一块独立画布。

    标题条显示「第 N 段」与文件名，并带一个 ✕ 移除按钮。
    画布本身（MaskCanvas）不认识分段的概念，它只管画面与拖框。
    """

    def __init__(self, master, app, index: int):
        super().__init__(master, bg=CARD, highlightbackground=BORDER,
                         highlightthickness=1)
        self.app = app
        self.index = index
        self.columnconfigure(0, weight=1)

        # --- 标题条 ---
        head = tk.Frame(self, bg=CARD)
        head.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        head.columnconfigure(1, weight=1)

        self.lbl_index = tk.Label(
            head, text=f"第 {index + 1} 段", bg=CARD, fg=ACCENT,
            font=("Microsoft YaHei UI", 9, "bold"))
        self.lbl_index.grid(row=0, column=0, sticky="w", padx=(0, 8))

        self.lbl_name = tk.Label(
            head, text="（空 — 把视频拖进来）", bg=CARD, fg="#8b95a3",
            font=("Microsoft YaHei UI", 9), anchor="w")
        self.lbl_name.grid(row=0, column=1, sticky="ew")

        self.btn_remove = tk.Label(
            head, text="✕", bg=CARD, fg="#8b95a3",
            font=("Segoe UI", 10, "bold"), cursor="hand2")
        self.btn_remove.grid(row=0, column=2, sticky="e", padx=(8, 0))
        self.btn_remove.bind("<Button-1>", lambda e: self.app.remove_segment(self.index))
        self.btn_remove.bind("<Enter>", lambda e: self.btn_remove.config(fg="#c0392b"))
        self.btn_remove.bind("<Leave>", lambda e: self.btn_remove.config(fg="#8b95a3"))

        # --- 画面 ---
        self.canvas = MaskCanvas(self, app, index)
        self.canvas.grid(row=1, column=0, sticky="nsew")
        self.rowconfigure(1, weight=1, minsize=CANVAS_HEIGHT)
        # 标题条固定高，画面吃掉剩余空间
        self.rowconfigure(0, weight=0)

    def set_file_name(self, name: str | None) -> None:
        if name:
            self.lbl_name.config(text=name, fg="#1f2933")
        else:
            self.lbl_name.config(text="（空 — 把视频拖进来）", fg="#8b95a3")

    def set_index(self, index: int) -> None:
        self.index = index
        self.lbl_index.config(text=f"第 {index + 1} 段")
        self.canvas.index = index

    # ---------- 高度分配 ----------
    def head_height(self) -> int:
        """标题条占的高度（含与画面之间的间距）。"""
        try:
            return self.lbl_index.winfo_reqheight() + 4
        except tk.TclError:
            return 22

    def min_height(self) -> int:
        """这张卡片的最小高度 = 标题条 + 画面下限。"""
        return self.head_height() + CANVAS_HEIGHT
