"""时间输入控件：像 Web 的 `<input type="time">` 一样可填也可点。

为什么需要它：
原来的 ttk.Entry 只能手打 "00:01:00"，打错格式要到点「开始处理」
才知道报错；打错分隔符（比如用点号）也常常是整段被忽略。
用户明确要求「可填也可点」。

交互设计：
- 可直接键入：00 / 00:01 / 00:01:00 三种粒度都接受，1-3 段均可
- 可点击：点框弹出滚轮式面板（时 / 分 / 秒三列），点数字即选中
- 键盘微调：↑↓ 调选中位，PageUp/PageDown 跳 5，Tab/左右键换位
- 失焦自动规范化成 HH:MM:SS
- 焦点态用蓝色描边，和整体风格一致
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .timeutil import format_hhmmss, parse_hhmmss

ACCENT = "#2f6feb"
BORDER = "#c8ccd4"
BORDER_FOCUS = ACCENT
BG = "#ffffff"
FG = "#1f2933"
MUTED = "#6b7280"

# 滚轮面板每次滚动的步长
_STEP = {"hour": 1, "min": 1, "sec": 1}
_BIG_STEP = 5


class TimeInput(tk.Frame):
    """可填可点的时间输入框（HH:MM:SS）。

    用 tk.Frame 而非 ttk.Frame：ttk 的样式必须预先注册，
    而本控件外观完全由内层 Canvas 自绘，不需要 ttk 主题。
    """

    def __init__(self, master, var: tk.StringVar, width: int = 10,
                 on_change=None):
        # 用 tk.Frame 而不是 ttk.Frame：ttk 需要预先注册自定义样式，
        # 这里样式完全由内层 Canvas 绘制，不依赖 ttk 主题。
        super().__init__(master, bg=BORDER)
        self.var = var
        self.on_change = on_change
        self._seg = 1          # 当前选中段：0=时 1=分 2=秒
        self._menu = None
        self._focus = False

        # 外层用 Canvas 画边框，Entry 本身透明叠加 —— ttk.Entry 无法直接描边
        self.canvas = tk.Canvas(self, height=26, bg=BORDER,
                                highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)

        self.entry = tk.Entry(self.canvas, textvariable=self.var,
                              justify="center", bd=0, highlightthickness=0,
                              bg=BG, fg=FG, insertbackground=ACCENT,
                              font=("Segoe UI", 10))
        self.win = self.canvas.create_window(0, 0, anchor="w",
                                             window=self.entry,
                                             width=width * 8, height=24)

        self.canvas.bind("<Configure>", self._layout)
        self.entry.bind("<FocusIn>", self._on_focus_in)
        self.entry.bind("<FocusOut>", self._on_focus_out)
        self.entry.bind("<Button-1>", self._on_click, add="+")
        self.entry.bind("<Button-3>", self._on_right_click)
        self.entry.bind("<KeyPress>", self._on_key)
        self.entry.bind("<Up>", lambda e: self._bump(1))
        self.entry.bind("<Down>", lambda e: self._bump(-1))
        self.entry.bind("<Prior>", lambda e: self._bump(_BIG_STEP))
        self.entry.bind("<Next>", lambda e: self._bump(-_BIG_STEP))
        self.var.trace_add("write", lambda *_: self._draw())

    # ---------- 绘制 ----------
    def _layout(self, _e=None) -> None:
        h = self.canvas.winfo_height()
        w = self.canvas.winfo_width()
        self.canvas.coords(self.win, max(1, (w - self.entry.winfo_width()) // 2),
                           h // 2)
        self._draw()

    def _draw(self) -> None:
        """重画外框：聚焦时用蓝色，非聚焦用灰色。"""
        self.canvas.delete("border")
        h = max(1, self.canvas.winfo_height())
        w = max(1, self.canvas.winfo_width())
        color = BORDER_FOCUS if self._focus else BORDER
        self.canvas.create_rectangle(0, 0, w, h, outline=color, width=2,
                                     tags="border")

    # ---------- 焦点 ----------
    def _on_focus_in(self, _e=None) -> None:
        self._focus = True
        self._draw()
        self._sync_seg()

    def _on_focus_out(self, _e=None) -> None:
        self._focus = False
        self._close_menu()
        self._normalize()
        self._draw()

    def _sync_seg(self) -> None:
        """根据光标位置判断当前在改哪一段。"""
        try:
            idx = self.entry.index("insert")
        except tk.TclError:
            idx = len(self.var.get())
        # HH:MM:SS 中分隔符在 2、5；光标 >=5 -> 秒，>=2 -> 分，否则 -> 时
        if idx >= 6:
            self._seg = 2
        elif idx >= 3:
            self._seg = 1
        else:
            self._seg = 0

    def _normalize(self) -> None:
        """失焦时把 HH:MM / MM:SS / 纯秒数统一成 HH:MM:SS；非法则清空。"""
        raw = self.var.get().strip()
        if not raw:
            return
        try:
            sec = parse_hhmmss(raw)
        except ValueError:
            return          # 保留原样，让用户自己看到问题，不要静默吞掉
        self.var.set(format_hhmmss(sec))

    # ---------- 点击 ----------
    def _on_click(self, _e=None) -> None:
        self.entry.focus_set()
        self._sync_seg()
        if self.var.get().strip():
            self._open_menu()

    def _on_right_click(self, _e=None) -> None:
        self._open_menu()

    # ---------- 滚轮菜单 ----------
    def _open_menu(self) -> None:
        if self._menu is not None:
            return
        sec = parse_hhmmss(self.var.get()) or 0.0
        vals = self._parts(sec)
        top = self.winfo_rooty() + self.winfo_height()
        left = self.winfo_rootx()
        self._menu = tk.Toplevel(self)
        self._menu.overrideredirect(True)
        self._menu.geometry(f"+{left}+{top}")

        frm = tk.Frame(self._menu, bg="#ffffff", bd=1, relief="solid")
        frm.pack()
        names = ("时", "分", "秒")
        # 每列若干可选值（围绕当前值上下各若干个），点一下即选中
        for i, name in enumerate(names):
            col = tk.Frame(frm, bg="#ffffff")
            col.pack(side="left", padx=2, pady=2)
            tk.Label(col, text=name, bg="#ffffff", fg=MUTED,
                     font=("Segoe UI", 8)).pack()
            base = vals[i]
            rng = range(10) if i == 0 else range(60)
            # 只列出当前值附近的 7 个，避免一列 60 项太长
            span = 7
            start = max(0, min(max(0, base - span // 2),
                               max(rng) - span + 1))
            shown = [v % (100 if i == 0 else 60) for v in
                     range(start, start + span)]
            for v in shown:
                sel = v == base
                b = tk.Label(
                    col, text=f"{v:02d}",
                    bg=ACCENT if sel else "#ffffff",
                    fg="#ffffff" if sel else FG,
                    width=3, cursor="hand2",
                    font=("Segoe UI", 9, "bold" if sel else "normal"))
                b.pack(pady=1)
                b.bind("<Button-1>",
                       lambda _e, seg=i, val=v: self._pick(seg, val))
                b.bind("<Enter>", lambda _e, w=b: w.configure(bg="#e8eefc"))
                b.bind("<Leave>", lambda _e, w=b, s=sel: w.configure(
                    bg=ACCENT if s else "#ffffff"))
        # 点外面关闭
        self._menu.bind("<FocusOut>", lambda _e: self._close_menu())

    def _close_menu(self) -> None:
        if self._menu is not None:
            try:
                self._menu.destroy()
            except tk.TclError:
                pass
            self._menu = None

    def _parts(self, sec: float) -> list[int]:
        """秒 -> [时, 分, 秒]。"""
        return [int(sec // 3600) % 100, int(sec // 60) % 60, int(sec) % 60]

    def _pick(self, seg: int, val: int) -> None:
        parts = self._parts(parse_hhmmss(self.var.get()) or 0.0)
        parts[seg] = val
        self.var.set(format_hhmmss(parts[0] * 3600 + parts[1] * 60 + parts[2]))
        self._seg = seg
        self._close_menu()
        if self.on_change:
            self.on_change()

    # ---------- 键盘 ----------
    def _on_key(self, e: tk.Event) -> None:
        ch = e.char
        if ch.isdigit():
            self._close_menu()
            self._auto_advance(len(self.var.get()))
            return
        if ch in (":", ".", "：", " ", "-"):
            self._close_menu()
            self._advance_seg()
            return
        if ch == "\b" or e.keysym == "BackSpace":
            self._close_menu()
            return
        if ch == "\x7f":      # Delete
            self.var.set("")
            self._close_menu()
            return
        self._close_menu()

    def _auto_advance(self, cur_len: int) -> None:
        """输入到 2 位且当前段未满时自动跳到下一段。"""
        if cur_len in (2, 5):
            self._advance_seg()

    def _advance_seg(self) -> None:
        if self._seg < 2:
            self._seg += 1
            # 把光标放到下一段开头
            pos = {0: 3, 1: 6, 2: 8}[self._seg]
            try:
                self.entry.icursor(min(pos, len(self.var.get())))
            except tk.TclError:
                pass

    def _bump(self, delta: int) -> str:
        """↑↓ 微调当前段。

        不在这里重算 _seg：调用方（按键绑定）已经把当前段定好了，
        重算会把用户刚选中的段覆盖掉。
        """
        self._close_menu()
        parts = self._parts(parse_hhmmss(self.var.get()) or 0.0)
        limit = 100 if self._seg == 0 else 60
        parts[self._seg] = (parts[self._seg] + delta) % limit
        self.var.set(format_hhmmss(parts[0] * 3600 + parts[1] * 60 + parts[2]))
        if self.on_change:
            self.on_change()
        return "break"