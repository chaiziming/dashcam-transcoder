"""时间输入控件：紧凑的 HH:MM:SS 微调框。

交互规则（按需求定，不做多余功能）：
  - 显示固定格式 HH:MM:SS，只有时分秒三位
  - 禁止输入非数字字符：按键层面拦截，不靠事后校验
  - 不提供下拉列表
  - 光标停在时分秒区域内时，↑↓ 分别对该位 +1 / -1
  - 取值范围：时 0~99，分/秒 0~59（用户说的 60 是「上限前一档」，
    实际 60 秒就是 1 分，归一化后显示 01:00）
  - 循环：最大值 +1 -> 最小值；最小值 -1 -> 最大值

绘制：tk.Frame + Canvas 自绘（ttk 无法只描边不填充），
Entry 叠在 Canvas 上，保证只有输入框区域有白底。
"""
from __future__ import annotations

import tkinter as tk

from .theme import ACCENT
from .timeutil import format_hhmmss, parse_hhmmss

BORDER = "#c8ccd4"
BORDER_FOCUS = ACCENT
FG = "#1f2933"

# 单段最大值（含）：时最大 99，分/秒最大 59
MAX = (99, 59, 59)
# 三段的字符区间：[起, 止) —— 用于把光标位置映射到段
SEG_SPAN = ((0, 2), (3, 5), (6, 8))


class TimeInput(tk.Frame):
    """HH:MM:SS 微调输入框。"""

    def __init__(self, master, var: tk.StringVar, width: int = 96,
                 on_change=None):
        super().__init__(master, bg=BORDER, width=width, height=24)
        # 关闭几何传播：宽高由下面的 width/height 参数固定，
        # 不被子控件（Entry 的请求尺寸）反向撑变形。
        self.pack_propagate(False)
        self.var = var
        self.on_change = on_change
        self._focus = False
        self._seg = 1              # 当前段：0=时 1=分 2=秒
        self._width = width

        self.canvas = tk.Canvas(self, width=width, height=24, bg=BORDER,
                                highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)

        # Entry 完全透明（bg 同边框色），只有 focus 态才画白底
        self.entry = tk.Entry(
            self.canvas, textvariable=self.var, justify="center",
            bd=0, highlightthickness=0, bg=BORDER, fg=FG,
            insertbackground=ACCENT, disabledbackground=BORDER,
            font=("Segoe UI", 10))
        # 注意：create_window 的 width/height 只是「初始请求尺寸」，
        # 若不在 <Configure> 里用 itemconfigure 重新设置，窗口实际会是 1x1。
        self.win = self.canvas.create_window(0, 0, anchor="center",
                                             window=self.entry,
                                             width=width - 2, height=22)

        self.canvas.bind("<Configure>", self._layout)
        self.entry.bind("<FocusIn>", self._on_focus_in)
        self.entry.bind("<FocusOut>", self._on_focus_out)
        self.entry.bind("<KeyPress>", self._on_key)
        self.entry.bind("<Up>", lambda e: self._bump(1))
        self.entry.bind("<Down>", lambda e: self._bump(-1))
        self.var.trace_add("write", lambda *_: self._draw())
        self._draw()

    # ---------- 绘制 ----------
    def _layout(self, _e=None) -> None:
        w = max(1, self.canvas.winfo_width())
        h = max(1, self.canvas.winfo_height())
        # Entry 宽度跟随 Canvas，但不超过设定值（防止被拉得过宽）
        ew = min(self._width - 2, max(60, w - 2))
        self.canvas.itemconfigure(self.win, width=ew, height=h - 2)
        self.canvas.coords(self.win, max(1, (w - ew) // 2), h // 2)
        self._draw()

    def _draw(self) -> None:
        """聚焦时画白底（输入框区域），非聚焦时与背景融为一体。

        注意：只删带border 标签的元素，**绝不能用 delete("all")** ——
        那会把 create_window 嵌入的 Entry 窗口一起删掉，
        表现为 Entry 尺寸变成 1x1、整框看不见内容。
        """
        self.canvas.delete("border")
        w = max(1, self.canvas.winfo_width())
        h = max(1, self.canvas.winfo_height())
        if self._focus:
            self.canvas.create_rectangle(1, 1, w - 2, h - 2, fill="#ffffff",
                                         outline=BORDER_FOCUS, width=2,
                                         tags="border")
        else:
            self.canvas.create_rectangle(0, 0, w, h, fill=BORDER, outline="",
                                         tags="border")
        # 边框画在 Entry 之下，避免遮住文字
        self.canvas.tag_lower("border")

    # ---------- 焦点 ----------
    def _on_focus_in(self, _e=None) -> None:
        self._focus = True
        self.entry.config(bg="#ffffff")
        # 空值时先填 00:00:00，避免焦点落在空框上不知当前是哪一段
        if not self.var.get().strip():
            self.var.set("00:00:00")
        self._draw()
        self._sync_seg()

    def _on_focus_out(self, _e=None) -> None:
        self._focus = False
        self.entry.config(bg=BORDER)
        self._normalize()
        self._draw()

    def _normalize(self) -> None:
        """把任意可接受的写法统一成 HH:MM:SS；空则补 00:00:00。"""
        raw = self.var.get().strip()
        if not raw:
            self.var.set("00:00:00")
            return
        try:
            sec = parse_hhmmss(raw)
        except ValueError:
            return            # 输入层已拦掉非数字，这里只兜底
        if sec is None:
            self.var.set("00:00:00")
            return
        parts = [int(sec // 3600) % 100, int(sec // 60) % 60, int(sec) % 60]
        self.var.set(format_hhmmss(parts[0] * 3600 + parts[1] * 60 + parts[2]))

    # ---------- 键盘 ----------
    def _on_key(self, e: tk.Event) -> str | None:
        ch = e.char
        # 只放行数字；冒号用于分隔段，也允许（用户可能直接打 00:01:00）
        if ch.isdigit() or ch == ":":
            self._auto_advance()
            return None
        # 其余一律拦截：字母、空格、符号、以及退格/删除
        return "break"

    def _auto_advance(self) -> None:
        """输入到某段末尾（2 位）后自动跳到下一段。"""
        try:
            n = len(self.var.get().rstrip(":"))
        except tk.TclError:
            return
        if n in (2, 5):
            self._advance_seg()

    def _advance_seg(self) -> None:
        pos = {0: 3, 1: 6}.get(self._seg)
        if self._seg < 2 and pos is not None:
            self._seg += 1
            try:
                self.entry.icursor(min(pos, len(self.var.get())))
            except tk.TclError:
                pass

    def _sync_seg(self) -> None:
        """光标落在哪一段，就把该段设为当前段。"""
        try:
            idx = self.entry.index("insert")
        except tk.TclError:
            idx = 0
        self._seg = 2 if idx >= 6 else (1 if idx >= 3 else 0)

    def _bump(self, delta: int) -> str:
        """↑↓ 对当前段 +1 / -1，循环取值。"""
        self._sync_seg()
        try:
            sec = parse_hhmmss(self.var.get()) or 0.0
        except ValueError:
            sec = 0.0
        parts = [int(sec // 3600) % 100, int(sec // 60) % 60, int(sec) % 60]
        hi = MAX[self._seg]
        parts[self._seg] = (parts[self._seg] + delta) % (hi + 1)
        self.var.set(format_hhmmss(parts[0] * 3600 + parts[1] * 60 + parts[2]))
        if self.on_change:
            self.on_change()
        return "break"

    # ---------- 供测试/外部使用 ----------
    def set_seconds(self, sec: float) -> None:
        self.var.set(format_hhmmss(sec))

    def get_seconds(self) -> float | None:
        try:
            return parse_hhmmss(self.var.get())
        except ValueError:
            return None