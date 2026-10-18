"""播放器控制条：可拖拽进度条 + 播放控制按钮 + 时间标签。

进度条用 Canvas 自绘（ttk.Scale 样式不可控且不支持按下即跳转），
绘制内容包括：已播放段、拖拽预览、当前播放头。
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .theme import ACCENT, FG as HEAD
from .timeutil import hhmmss_short as hhmmss_ms

# 进度条专用配色（只在播放器控制条用，不进 theme 以免污染全局）
ACCENT_DIM = "#9db8e8"
TRACK = "#dfe4ea"
TRACK_BG = "#c8ccd4"

# 控制条各部分高度（像素）。显式固定，避免两行都被外层拉高、看起来错位。
BAR_ROW_H = 22        # 进度条行高（含时间标签）
BTN_ROW_H = 34        # 按钮行高
TRACK_H = 20# 进度条 Canvas 高度


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

        self.canvas = tk.Canvas(self, height=TRACK_H, bg=TRACK_BG,
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
    """完整播放控制条：上下两行（照主流播放器布局）。

    行 0：进度条横跨整行，两端是时间标签
    行 1：播放/停止/快退快进在左，倍速在右

    关于权重：Tk 的 column 权重是**整列共享**的，不分行。
    行 0 的进度条在 column 1，行 1 的按钮也在 column 1~5，
    所以权重必须给 column 1（进度条列），行 1 靠 sticky="w"
    让按钮保持左对齐、右侧留白 —— 这正是播放器的样子。
    """
    """完整播放控制条：播放/停止/快退快进 + 进度条 + 时间 + 速度。

    布局说明（照主流播放器改成上下两行）：
    行 0：进度条占满整个宽度（左右各留一点边距），两端显示时间
    行 1：播放/停止/快退快进在左，倍速在右

    早期版本把进度条和按钮放在同一行，两侧都是按钮，
    中间的进度条被挤得很短，手动拖动很难定位 —— 这就是改版的原因。
    """

    def __init__(self, master, app):
        super().__init__(master, style="Card.TFrame")
        self.app = app
        # 两行用嵌套 Frame 隔离：Tk 的 grid 列宽是跨行共享的，
        # 行 1 的按钮撑宽某列会连带把行 0 的进度条挤窄。
        # 另外两行都关闭几何传播并显式定高，避免被外层纵向拉高、
        # 看起来像错位（进度条被撑成一个高盒子、按钮被拉成瘦高条）。

        # ========== 行 0：时间 + 进度条 ==========
        top = ttk.Frame(self, style="Card.TFrame", height=BAR_ROW_H)
        top.pack(fill="x")
        top.pack_propagate(False)          # 固定高度，不被子控件撑开
        top.columnconfigure(1, weight=1)   # 权重给进度条列

        self.lbl_time_left = ttk.Label(top, text="--:--", style="Muted.TLabel",
                                       width=7, anchor="w")
        self.lbl_time_left.grid(row=0, column=0, sticky="w", padx=(0, 8))

        self.seek = SeekBar(top, on_seek=app.on_seek_to,
                            on_scrub=app.on_scrub)
        self.seek.grid(row=0, column=1, sticky="ew")

        self.lbl_time_right = ttk.Label(top, text="--:--", style="Muted.TLabel",
                                        width=7, anchor="e")
        self.lbl_time_right.grid(row=0, column=2, sticky="w", padx=(8, 0))
        self._last_time_text = "--:-- / --:--"

        # ========== 行 1：控制按钮 ==========
        # 按钮顺序：«30s «5s ▶ ■ 5s» 30s»，倍速靠最右。
        # 这是主流播放器的排列 —— 越靠中间时间跨度越小，便于精调。
        bottom = ttk.Frame(self, style="Card.TFrame", height=BTN_ROW_H)
        bottom.pack(fill="x", pady=(2, 0))
        bottom.pack_propagate(False)
        bottom.columnconfigure(6, weight=1)   # 纯占位列，按钮不碰

        def mk(txt: str, cmd, width: int = 5):
            b = ttk.Button(bottom, text=txt, width=width, command=cmd)
            return b

        self.btn_back30 = mk("« 30s", lambda: app.on_seek_delta(-30), 6)
        self.btn_back30.grid(row=0, column=0, sticky="w", padx=(0, 2))

        self.btn_back5 = mk("« 5s", lambda: app.on_seek_delta(-5))
        self.btn_back5.grid(row=0, column=1, sticky="w", padx=2)

        self.btn_play = ttk.Button(bottom, text="▶", width=3,
                                   command=app.on_toggle_play)
        self.btn_play.grid(row=0, column=2, sticky="w", padx=2)

        self.btn_stop = ttk.Button(bottom, text="■", width=3,
                                   command=app.on_stop_play)
        self.btn_stop.grid(row=0, column=3, sticky="w", padx=2)

        self.btn_fwd5 = mk("5s »", lambda: app.on_seek_delta(5))
        self.btn_fwd5.grid(row=0, column=4, sticky="w", padx=2)

        self.btn_fwd30 = mk("30s »", lambda: app.on_seek_delta(30), 6)
        self.btn_fwd30.grid(row=0, column=5, sticky="w", padx=2)

        # --- 倍速下拉（整体最右侧）---
        # 只提供固定档位，不允许手动输入（state="readonly"）。
        # 用纯字符串做值，不用 DoubleVar：之前用 DoubleVar 配浮点 values
        # 却 set("1x")，类型不匹配会导致下拉框显示异常、选择事件不触发。
        self._speed_labels = ("0.25x", "0.5x", "1x", "2x", "3x")
        self.var_speed = tk.StringVar(value="1x")
        self.cmb_speed = ttk.Combobox(bottom, textvariable=self.var_speed,
                                      values=self._speed_labels,
                                      width=5, state="readonly")
        self.cmb_speed.grid(row=0, column=7, sticky="e")
        self.cmb_speed.bind("<<ComboboxSelected>>", self._on_speed)
        # 不额外加「倍速」文字标签：下拉框里的 "1x" 已自解释，
        # 且多一个标签会把下拉框往左推，破坏「倍速在最右端」的视觉对齐。

        self._enable_all(False)

    def _enable_all(self, on: bool) -> None:
        st = "normal" if on else "disabled"
        for w in (self.btn_play, self.btn_stop, self.btn_back5, self.btn_fwd5,
                  self.btn_back30, self.btn_fwd30):
            w.config(state=st)
        # 倍速下拉必须保持 readonly：state="normal" 会变成可编辑输入框，
        # 用户能手动敲速度值，导致下拉框失去约束。
        self.cmb_speed.config(state="readonly" if on else "disabled")

    def set_ready(self, ready: bool) -> None:
        self._enable_all(bool(ready))

    def set_playing(self, playing: bool) -> None:
        """播放中显示暂停图标，暂停时显示播放图标。"""
        self.btn_play.config(text="❚❚" if playing else "▶")

    def update_time(self, pos: float, dur: float | None) -> None:
        """更新进度条与两侧时间标签（主流播放器样式：当前时间 … 总时长）。"""
        self.seek.set_position(pos)
        left = hhmmss_ms(pos)
        right = hhmmss_ms(dur)
        self.lbl_time_left.config(text=left)
        self.lbl_time_right.config(text=right)
        # 兼容旧引用：整体时间串（如 "00:12 / 03:36"）
        self._last_time_text = f"{left} / {right}"

    @property
    def lbl_time(self):
        """向后兼容的占位 Label。

        旧代码与测试通过 player_bar.lbl_time 读取整体时间串；
        现在时间拆到了左右两个标签，这个属性保留一个隐藏 Label 承载组合文本，
        避免外部引用 AttributeError。
        """
        if not hasattr(self, "_lbl_time_compat"):
            self._lbl_time_compat = ttk.Label(self, text="--:-- / --:--",
                                               style="Muted.TLabel")
            self._lbl_time_compat.grid_remove()   # 不参与布局，仅承载值
        self._lbl_time_compat.config(text=self._last_time_text)
        return self._lbl_time_compat

    def _on_speed(self, _e=None) -> None:
        """倍速下拉变更。state="readonly" 保证只有合法档位能进来。"""
        label = self.var_speed.get()
        try:
            speed = float(label.rstrip("x"))
        except (AttributeError, ValueError):
            speed = 1.0
        # 让下拉框立刻显示新值（readonly 下手动 set 也生效）
        self.cmb_speed.set(f"{speed:g}x")
        self.app.on_speed_change(speed)