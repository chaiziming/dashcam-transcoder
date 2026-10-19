"""紧凑的时间输入控件：显示 HH:MM:SS，用 ↑↓ 与数字键编辑。

交互设计：
- **选中的是单个数字位**。高亮只用一块淡蓝背景表示，不描边、
  不加上下指示条、不给文字换重色 —— 够用且不抢眼。
- 输入数字时依次覆盖光标所在位，写满两位后自动前进到下一段。
- 支持**跨框连续输入**：开始框的「秒」段填满后自动跳到结束框的「时」段，
  结束框填完最后一位停在原地。两个框在操作手感上像一体，
  但它们是两个独立控件（各管自己的 var）。
- Backspace 与上述流程相反：把当前位清零并退回上一位；
  在开始框最前面按 Backspace 会退到结束框的最后一位。
- ↑↓ 对当前位所属的段做 +1 / -1（时 0~99，分秒 0~59），循环取值，不会为负。
- 文本恒为 8 字符 `HH:MM:SS`，冒号不可编辑，只有数字位可改。

渲染：全部画在 Canvas 上。不用 tk.Entry 的原因是它文字居中且不可控，
画不出按位高亮；而且 readonly 的 Entry 在 Windows 上仍可能被粘贴
灌进非法字符。
"""
from __future__ import annotations

import tkinter as tk

from .theme import ACCENT
from .timeutil import format_hhmmss, parse_hhmmss

BORDER = "#c8ccd4"
BORDER_FOCUS = ACCENT
FG = "#1f2933"
FG_DIM = "#8b95a3"          # 冒号颜色，比数字浅
SEL_BG = "#cfe0fb"          # 选中数字位的背景块

BORDER_W = 2
# 控件尺寸
CTRL_W = 104
CTRL_H = 26

# 每段的最大值（含）。小时 0~99，分/秒 0~59。
MAX = (99, 59, 59)
# 每段在 8 字符文本中的起止下标（左闭右开）
SEG_SPAN = ((0, 2), (3, 5), (6, 8))
# 段内每个数字位在「6位数字串」里的下标
SEG_SLOTS = ((0, 1), (2, 3), (4, 5))

FONT = ("Consolas", 11)


def blank_text() -> str:
    return "00:00:00"


class TimeInput(tk.Frame):
    """HH:MM:SS 编辑框（按段选中，支持跨框连续输入）。"""

    def __init__(self, master, var: tk.StringVar, *, prev_input=None,
                 width: int = CTRL_W, height: int = CTRL_H, on_change=None):
        """prev_input / next_link: 相邻的 TimeInput，用于跨框跳转。"""
        super().__init__(master, bg=BORDER, width=width, height=height)
        self.pack_propagate(False)
        self.var = var
        self.prev_input = prev_input      # 上一个框（Backspace 往回跳时用）
        self.next_link = None# 下一个框（正向输入用）
        self.on_change = on_change
        self._focus = False
        self._seg = 0                   # 当前所在段：0=时 1=分 2=秒
        self._digit = 0                 # 段内已填到第几位：0=未填 1=填了1位
        # 注意：不要用 self._w / self._h —— tkinter 用 _w 存控件路径，
        # 覆盖它会导致创建子控件时报 "int + str" 的 TypeError。
        self._ctrl_w = width
        self._ctrl_h = height

        if not self._valid(self.var.get()):
            self.var.set(blank_text())

        self.canvas = tk.Canvas(self, width=width, height=height, bg=BORDER,
                                highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)

        self.canvas.bind("<Configure>", lambda e: self._render())
        self.canvas.bind("<FocusIn>", self._on_focus_in)
        self.canvas.bind("<FocusOut>", self._on_focus_out)
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<Right>", lambda e: self._move_seg(1))
        self.canvas.bind("<Left>", lambda e: self._move_seg(-1))
        self.canvas.bind("<Up>", lambda e: self._bump(1))
        self.canvas.bind("<Down>", lambda e: self._bump(-1))
        self.canvas.bind("<KeyPress>", self._on_key)
        self.canvas.bind("<BackSpace>", self._on_backspace)
        self.var.trace_add("write", lambda *_: self._render())
        self._render()

    # ---------- 文本工具 ----------
    @staticmethod
    def _valid(text: str) -> bool:
        return (isinstance(text, str) and len(text) == 8
                and text[2] == ":" and text[5] == ":"
                and all(c.isdigit() for c in text[:2] + text[3:5] + text[6:8]))

    def _digits(self) -> str:
        t = self.var.get() if self._valid(self.var.get()) else blank_text()
        return t[0:2] + t[3:5] + t[6:8]

    def _put_digits(self, digits: str) -> None:
        h, m, s = digits[0:2], digits[2:4], digits[4:6]
        self.var.set(f"{h}:{m}:{s}")

    # ---------- 渲染 ----------
    def _render(self) -> None:
        c = self.canvas
        c.delete("all")
        w = max(1, c.winfo_width())
        h = max(1, c.winfo_height())

        color = BORDER_FOCUS if self._focus else BORDER
        c.create_rectangle(
            BORDER_W / 2, BORDER_W / 2, w - BORDER_W / 2, h - BORDER_W / 2,
            outline=color, width=BORDER_W, fill="")

        text = self.var.get() if self._valid(self.var.get()) else blank_text()
        pad = BORDER_W + 5
        char_w = (w - pad * 2) / 8.0
        cy = h // 2
        seg_start, seg_end = SEG_SPAN[self._seg]

        # 选中段用背景块表示（不描粗、不换重色），视觉克制
        if self._focus:
            # 当前数字位下标（由 _seg 与 _digit 组合得出）
            cur = SEG_SPAN[self._seg][0] + self._digit
            # 单个数字位的轻量选中：只加一个淡蓝背景块，不描边、
            # 不加上下指示条、文字保持原色 —— 视觉克制但仍能看出选了哪一位。
            x0 = pad + char_w * cur
            x1 = pad + char_w * (cur + 1)
            c.create_rectangle(x0 + 1, pad + 3, x1 - 1, h - pad - 3,
                               fill=SEL_BG, outline="")

        for i, chx in enumerate(text):
            if chx == ":":
                c.create_text(pad + char_w * (i + 0.5), cy, text=":",
                              fill=FG_DIM, font=FONT)
            else:
                c.create_text(pad + char_w * (i + 0.5), cy, text=chx,
                              fill=FG, font=FONT)

    # ---------- 焦点 ----------
    def _on_focus_in(self, _e=None) -> None:
        self._focus = True
        self.canvas.focus_set()
        if not self._valid(self.var.get()):
            self.var.set(blank_text())
        self._digit = 0
        self._render()

    def _on_focus_out(self, _e=None) -> None:
        self._focus = False
        self._digit = 0
        if not self._valid(self.var.get()):
            self.var.set(blank_text())
        self._render()

    # ---------- 定位 ----------
    def _on_click(self, e) -> str:
        """点击落到哪一段就选中哪一段。"""
        self.canvas.focus_set()
        self._focus = True
        w = max(1, self.canvas.winfo_width())
        pad = BORDER_W + 5
        char_w = (w - pad * 2) / 8.0
        idx = int((e.x - pad) / char_w) if char_w > 0 else 0
        self._goto(max(0, min(7, idx)))
        return "break"

    def _move_seg(self, step: int) -> str:
        """左右键在段之间移动。"""
        self._seg = max(0, min(2, self._seg + step))
        self._digit = 0
        self._render()
        return "break"

    # ---------- 兼容访问 ----------
    @property
    def _pos(self) -> int:
        """当前数字位在 8 字符文本中的下标（等价于 _seg/_digit 组合）。"""
        return SEG_SPAN[self._seg][0] + self._digit

    def _goto(self, idx: int) -> None:
        """把光标定位到指定字符下标；点在冒号上则归到左侧那一位。"""
        if idx >= 8:
            idx = 7
        if idx <= 1:
            self._seg, self._digit = 0, idx
        elif idx <= 4:
            self._seg, self._digit = 1, idx - 3
        else:
            self._seg, self._digit = 2, idx - 6
        self._digit = max(0, min(1, self._digit))
        self._render()

    # ---------- 输入 ----------
    def _on_key(self, e: tk.Event) -> str:
        """只接受数字：写入当前段，填满两位后前进。"""
        if e.keysym in ("BackSpace", "Delete"):
            return "break"
        ch = e.char
        if len(ch) != 1 or not ch.isdigit():
            return "break"

        digits = list(self._digits())
        # _digit 越界时兜底到段内最后一位，避免外部误调用导致 IndexError
        digit = 0 if self._digit < 0 else (1 if self._digit > 1 else self._digit)
        slot = SEG_SLOTS[self._seg][digit]
        digits[slot] = ch
        self._put_digits("".join(digits))
        self._digit = digit + 1

        if self._digit >= 2:
            # 本段填满 -> 下一段；秒段填满 -> 跳到下一个框
            if self._seg < 2:
                self._seg += 1
                self._digit = 0
                self._render()
            else:
                # 秒段已满：接下一个框；若没有后续框则停在最后一位，
                # 这样再输入只会覆盖该位，不会写错位置
                nxt = self.next_link
                if nxt is not None:
                    nxt._jump_in()      # 跨框接着输入
                else:
                    self._digit = 1
                    self._render()
        else:
            self._render()
        if self.on_change:
            self.on_change()
        return "break"

    def _on_backspace(self, _e=None) -> str:
        """Backspace：把当前位清零，并退回前一位。

        与正向输入严格对称：正向是一位一位往后填，这里就一位一位往前退。
        在本框最前面按 Backspace 会退到上一个框的最后一位。
        """
        digits = list(self._digits())
        a, _b = SEG_SLOTS[self._seg]
        digits[a + self._digit] = "0"
        self._put_digits("".join(digits))

        # 退回前一个数字位
        cur = self._seg * 2 + self._digit      # 0..5
        if cur > 0:
            self._seg = (cur - 1) // 2
            self._digit = (cur - 1) % 2
            self._render()
        else:
            prev = self.prev_input
            if prev is not None:
                prev._jump_back()
            else:
                self._render()
        if self.on_change:
            self.on_change()
        return "break"

    def _jump_in(self) -> None:
        """被上一个框调用：接收焦点并停在「时」段，等待输入。"""
        self._focus = True
        self.canvas.focus_set()
        self._seg = 0
        self._digit = 0
        self._render()

    def _jump_back(self) -> None:
        """被下一个框调用（Backspace）：停在最后一位，位置对齐退过来的方向。"""
        self._focus = True
        self.canvas.focus_set()
        self._seg = 2
        self._digit = 1
        self._render()

    def _bump(self, delta: int) -> str:
        """↑↓ 对当前段 +1 / -1，循环取值（不会为负）。"""
        sec = parse_hhmmss(self.var.get()) or 0.0
        parts = [int(sec // 3600) % 100, int(sec // 60) % 60, int(sec) % 60]
        parts[self._seg] = (parts[self._seg] + delta) % (MAX[self._seg] + 1)
        self._put_digits(f"{parts[0]:02d}{parts[1]:02d}{parts[2]:02d}")
        if self.on_change:
            self.on_change()
        return "break"

    # ---------- 供测试 / 外部调用 ----------
    def set_seconds(self, sec: float) -> None:
        self.var.set(format_hhmmss(sec))

    def get_seconds(self) -> float | None:
        try:
            return parse_hhmmss(self.var.get())
        except ValueError:
            return None