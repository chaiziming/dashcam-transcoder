"""Win98 风格分段进度条。

用 Canvas 自绘，而不是 ttk.Progressbar：
- ttk.Progressbar 在 Windows 上是连续色块（现代样式），且 indeterminate 模式
  只是一小块来回滑动，看不出进度
- 这里画成固定数量的方块，一格一格填满，就是 Win98 那种经典进度条

对外接口：
    set_progress(pct: float)   0~100，逐步填格
    set_indeterminate(True)    未知总时长时改为来回扫动
    reset()
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk


class SegmentedProgress(tk.Frame):
    """Win98 风格进度条：N 个小方块，已完成的填蓝绿色，未完成的填浅灰。"""

    def __init__(self, master, segments: int = 22, width: int = 300, height: int = 22):
        super().__init__(master, bg="#c8ccd4", highlightbackground="#8a8f98",
                         highlightthickness=1, bd=0)
        self.segments = segments
        self._pct = 0.0
        self._indeterminate = False
        self._scan_pos = 0

        self.canvas = tk.Canvas(
            self, width=width, height=height, bg="#c8ccd4",
            highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)

        self.canvas.bind("<Configure>", lambda e: self._redraw())
        self._anim_job: str | None = None
        self._redraw()

    # ---------- 内部绘制 ----------
    def _filled_count(self) -> int:
        """根据百分比算出应填满的格子数。"""
        return int(round((max(0.0, min(100.0, self._pct)) / 100.0) * self.segments))

    def _redraw(self) -> None:
        self.canvas.delete("all")
        w = max(1, self.canvas.winfo_width())
        h = max(1, self.canvas.winfo_height())
        pad = 3
        inner_w = w - pad * 2
        inner_h = h - pad * 2
        if inner_w <= 0 or inner_h <= 0:
            return

        gap = 2
        seg_w = (inner_w - gap * (self.segments - 1)) / self.segments
        if seg_w <= 0:
            return

        done = self._filled_count()

        for i in range(self.segments):
            x0 = pad + i * (seg_w + gap)
            x1 = x0 + seg_w

            if self._indeterminate:
                # 未知总时长：一格宽的"扫描块"来回移动
                if i == self._scan_pos % self.segments:
                    fill = ACCENT
                elif i == (self._scan_pos - 1) % self.segments:
                    fill = "#9db8e8"
                else:
                    fill = "#dfe4ea"
            else:
                fill = ACCENT if i < done else "#dfe4ea"

            self.canvas.create_rectangle(
                x0, pad, x1, pad + inner_h,
                fill=fill, outline="#a9b0ba", width=1)

    # ---------- 对外接口 ----------
    def set_progress(self, pct: float) -> None:
        """设置百分比（0~100）。"""
        self._pct = max(0.0, min(100.0, float(pct)))
        if self._indeterminate:
            self._indeterminate = False
            self._stop_anim()
        self._redraw()

    def set_indeterminate(self, on: bool = True) -> None:
        """切换到未知总时长的扫动模式。"""
        if on:
            if not self._indeterminate:
                self._indeterminate = True
                self._scan_pos = 0
                self._start_anim()
        else:
            self._indeterminate = False
            self._stop_anim()
        self._redraw()

    def reset(self) -> None:
        self._pct = 0.0
        self._indeterminate = False
        self._stop_anim()
        self._redraw()

    # ---------- 扫动动画 ----------
    def _start_anim(self) -> None:
        self._stop_anim()
        self._tick()

    def _stop_anim(self) -> None:
        if self._anim_job is not None:
            try:
                self.after_cancel(self._anim_job)
            except (tk.TclError, ValueError):
                pass
            self._anim_job = None

    def _tick(self) -> None:
        if not self._indeterminate:
            self._anim_job = None
            return
        self._scan_pos += 1
        self._redraw()
        # 约 220ms 走一格，视觉上不急不慢
        self._anim_job = self.after(220, self._tick)

    def destroy(self) -> None:
        self._stop_anim()
        super().destroy()


# 与 main.py 保持一致的配色
ACCENT = "#2f6feb"