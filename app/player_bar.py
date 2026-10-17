"""播放器控制条：可拖拽进度条 + 播放控制按钮 + 时间标签。

进度条用 Canvas 自绘（ttk.Scale 样式不可控且不支持按下即跳转），
绘制内容包括：已播放段、拖拽预览、当前播放头。
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

ACCENT = "#2f6feb"
ACCENT_DIM = "#9db8e8"
TRACK = "#dfe4ea"
TRACK_BG = "#c8ccd4"
HEAD = "#1f2933"


def hhmmss_ms(sec: float | None) -> str:
    """秒 -> MM:SS 或 HH:MM:SS。"""
    if sec is None or sec < 0:
        return "--:--"
    sec = float(sec)
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


class SeekBar(tk.Frame):
    """可拖拽进度条。

    用 Canvas 自绘，因为需要：
      - 按下即刻跳转（ttk.Scale 做不到）
      - 区分「拖拽中」与「播放中」两种显示
      - 控制条整体风格与其他控件一致
    """

    def __init__(self, master, on_seek=None, on_scrub=None):
        super().__init__(master, bg=TRACK_BG)
        self.on_seek = on_seek        # 松手时回调(秒)
        self.on_scrub = on_scrub      # 拖动中回调(秒)，用于实时预览
        self.duration: float | None = None
        self.position: float = 0.0
        self._dragging = False

        self.canvas = tk.Canvas(self, height=26, bg=TRACK_BG,
                                highlightthickness=0, bd=0, cursor="hand2")
        self.canvas.pack(fill="both", expand=True)

        self.canvas.bind("<Configure>", lambda e: self._redraw())
        self.canvas.bind("<Button-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)

    # ---------- 内部 ----------
    def _fraction(self) -> float:
        if not self.duration or self.duration <= 0:
            return 0.0
        return max(0.0, min(1.0, self.position / self.duration))

    def _redraw(self) -> None:
        self.canvas.delete("all")
        w = max(1, self.canvas.winfo_width())
        h = max(1, self.canvas.winfo_height())
        mid = h // 2
        bar_h = 6
        y0, y1 = mid - bar_h // 2, mid + bar_h // 2
        pad = 8
        usable = max(1, w - pad * 2)

        # 底槽
        self.canvas.create_rectangle(pad, y0, pad + usable, y1,
                                     fill=TRACK, outline="#a9b0ba")
        if self.duration and self.duration > 0:
            frac = self._fraction()
            # 已播放段
            self.canvas.create_rectangle(pad, y0, pad + usable * frac, y1,
                                         fill=ACCENT, outline="")
            # 播放头
            hx = pad + usable * frac
            r = 7 if self._dragging else 6
            self.canvas.create_oval(hx - r, mid - r, hx + r, mid + r,
                                    fill=HEAD, outline="#ffffff", width=1)
        # 无时长时只画空槽，不放文案。
        # 这里只在"已载入文件但总时长未知"时出现；
        # 真正的空界面由 PlayerBar.set_ready(False) + 整条隐藏来表达。
        else:
            self.canvas.create_line(pad, mid, pad + usable, mid,
                                    fill=TRACK, width=bar_h)

    def _pos_to_sec(self, x: float) -> float:
        if not self.duration or self.duration <= 0:
            return 0.0
        w = max(1, self.canvas.winfo_width())
        usable = max(1, w - 16)
        frac = max(0.0, min(1.0, (x - 8) / usable))
        return frac * self.duration

    # ---------- 事件 ----------
    def _on_press(self, e) -> None:
        if not self.duration:
            return
        self._dragging = True
        self.position = self._pos_to_sec(e.x)
        self._redraw()
        if self.on_scrub:
            self.on_scrub(self.position)

    def _on_drag(self, e) -> None:
        if not self._dragging:
            return
        self.position = self._pos_to_sec(e.x)
        self._redraw()
        if self.on_scrub:
            self.on_scrub(self.position)

    def _on_release(self, e) -> None:
        if not self._dragging:
            return
        self._dragging = False
        self.position = self._pos_to_sec(e.x)
        self._redraw()
        if self.on_seek:
            self.on_seek(self.position)

    # ---------- 对外 ----------
    def set_duration(self, d: float | None) -> None:
        self.duration = d
        self._redraw()

    def set_position(self, sec: float) -> None:
        """更新播放位置。拖拽中忽略外部更新，避免和用户操作打架。"""
        if self._dragging:
            return
        self.position = max(0.0, min(sec, self.duration or sec))
        self._redraw()


class PlayerBar(ttk.Frame):
    """完整播放控制条：后跳/播放/前跳 + 进度条 + 时间 + 速度。"""

    def __init__(self, master, app):
        super().__init__(master, style="Card.TFrame")
        self.app = app
        self.columnconfigure(1, weight=1)

        # --- 左侧控制按钮 ---
        self.btn_back5 = ttk.Button(self, text="« 5s", width=6,
                                    command=lambda: app.on_seek_delta(-5))
        self.btn_back5.grid(row=0, column=0, sticky="w", padx=(0, 3))

        self.btn_play = ttk.Button(self, text="▶", width=3,
                                   command=app.on_toggle_play)
        self.btn_play.grid(row=0, column=1, sticky="w", padx=3)

        self.btn_fwd5 = ttk.Button(self, text="5s »", width=6,
                                   command=lambda: app.on_seek_delta(5))
        self.btn_fwd5.grid(row=0, column=2, sticky="w", padx=3)

        self.btn_back30 = ttk.Button(self, text="« 30s", width=6,
                                     command=lambda: app.on_seek_delta(-30))
        self.btn_back30.grid(row=0, column=3, sticky="w", padx=(8, 3))

        self.btn_fwd30 = ttk.Button(self, text="30s »", width=6,
                                    command=lambda: app.on_seek_delta(30))
        self.btn_fwd30.grid(row=0, column=4, sticky="w", padx=3)

        # --- 进度条 ---
        self.seek = SeekBar(self, on_seek=app.on_seek_to,
                            on_scrub=app.on_scrub)
        self.seek.grid(row=0, column=5, sticky="ew", padx=10)

        # --- 时间与速度 ---
        self.lbl_time = ttk.Label(self, text="--:-- / --:--",
                                  style="Muted.TLabel", width=15, anchor="center")
        self.lbl_time.grid(row=0, column=6, sticky="e", padx=6)

        self.var_speed = tk.DoubleVar(value=1.0)
        self.cmb_speed = ttk.Combobox(self, textvariable=self.var_speed,
                                      values=(0.25, 0.5, 1.0, 1.5, 2.0, 4.0),
                                      width=5, state="readonly")
        self.cmb_speed.grid(row=0, column=7, sticky="e", padx=(0, 2))
        self.cmb_speed.bind("<<ComboboxSelected>>", self._on_speed)
        ttk.Label(self, text="倍速", style="Muted.TLabel").grid(
            row=0, column=8, sticky="e")

        # 速度下拉显示友好文案
        self._speed_labels = {"0.25": "0.25x", "0.5": "0.5x", "1.0": "1x",
                              "1.5": "1.5x", "2.0": "2x", "4.0": "4x"}
        self.cmb_speed.set("1x")

        self._enable_all(False)

    def _enable_all(self, on: bool) -> None:
        st = "normal" if on else "disabled"
        for w in (self.btn_back5, self.btn_play, self.btn_fwd5,
                  self.btn_back30, self.btn_fwd30, self.cmb_speed):
            w.config(state=st)

    def set_ready(self, ready: bool) -> None:
        self._enable_all(bool(ready))

    def set_playing(self, playing: bool) -> None:
        self.btn_play.config(text="❚❚" if playing else "▶")

    def update_time(self, pos: float, dur: float | None) -> None:
        self.seek.set_position(pos)
        self.lbl_time.config(text=f"{hhmmss_ms(pos)} / {hhmmss_ms(dur)}")

    def _on_speed(self, _e=None) -> None:
        label = self.var_speed.get()
        try:
            speed = float(label.rstrip("x"))
        except (AttributeError, ValueError):
            speed = 1.0
        self.app.on_speed_change(speed)