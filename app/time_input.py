"""紧凑的时间输入控件：显示 HH:MM:SS，用 ↑↓ 与数字键编辑。

交互设计：
- **选中的是一整组两位数字**（时 / 分 / 秒各一组）。高亮用深蓝底 + 白字 +
  描边，一眼可见选了哪一组 —— 与 ↑↓ 的调整粒度保持一致：
  高亮「03」时按 ↑ 调整的是 03 整体，而不是其中某个数字。
- 输入数字时依次覆盖组内两位，写满后自动前进到下一组。
- **左右键可跨框移动**：在最后一组按 → 跳到下一个框的第一组；
  在第一组按 ← 退回上一个框的最后一组。
- 支持**跨框连续输入**：开始框的「秒」组填满后自动跳到结束框的「时」组，
  结束框填完最后一位停在原地。两个框在操作手感上像一体，
  但它们是两个独立控件（各管自己的 var）。
- Backspace 与正向输入对称：把当前位清零并退回上一位；
  在开始框最前面按 Backspace 会退到结束框的最后一位。
- ↑↓ 对当前组做 +1 / -1（时 0~99，分秒 0~59），循环取值，不会为负。
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
SEL_BG = "#2f6feb"# 选中组：深蓝底
SEL_EDGE = "#1d4ed8"        # 选中组描边
SEL_FG = "#ffffff"          # 选中组内文字转白，确保对比清晰

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

        # 选中范围是**一整组两位数字**（如「03」），而不是单个字符。
        # 高亮必须一眼可见：深蓝底 + 白字 + 描边，三重区分。
        if self._focus:
            x0 = pad + char_w * seg_start
            x1 = pad + char_w * seg_end
            c.create_rectangle(x0 + 1, pad + 2, x1 - 1, h - pad - 2,
                               fill=SEL_BG, outline=SEL_EDGE, width=2)

        for i, chx in enumerate(text):
            in_sel = self._focus and seg_start <= i < seg_end
            if chx == ":":
                c.create_text(pad + char_w * (i + 0.5), cy, text=":",
                              fill=FG_DIM, font=FONT)
            else:
                c.create_text(pad + char_w * (i + 0.5), cy, text=chx,
                              fill=SEL_FG if in_sel else FG, font=FONT)

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
        """左右键在段之间移动，到边界时跨到相邻的时间框。

        在最后一组按→ 跳到下一个框的第一组；在第一组按 ← 退回上一个框的最后一组。
        这样「开始 00:00:00 - 00:00:00 结束」在操作上连成一条线。
        """
        target = self._seg + step
        if 0 <= target <= 2:
            self._seg = target
            self._digit = 0
            self._render()
            return "break"

        # 越界 -> 跨框
        if step > 0 and self.next_link is not None:
            self.next_link._jump_seg(0)
        elif step < 0 and self.prev_input is not None:
            self.prev_input._jump_seg(2)
        return "break"

    # ---------- 兼容访问 ----------
    @property
    def _pos(self) -> int:
        """当前数字位在 8 字符文本中的下标（等价于 _seg/_digit 组合）。"""
        return SEG_SPAN[self._seg][0] + self._digit

    def _goto(self, idx: int) -> None:
        """把光标定位到指定字符所属的组（时/分/秒）。

        组内两位是一体的，所以点击组内任一位都选中整组 ——
        这与 ↑↓ 的调整粒度保持一致：用户看到高亮「03」，
        按 ↑ 时调整的也是 03，而不是其中某个数字。

        该组已有值时把编辑位落到组内第二位：这样 Backspace 能逐位
        往回退掉整组。否则永远只能退到组首，组内后一位清不掉。
        """
        if idx >= 8:
            idx = 7
        self._seg = 0 if idx < 3 else (1 if idx < 6 else 2)
        a, b = SEG_SLOTS[self._seg]
        digits = self._digits()
        self._digit = 1 if (digits[a] != "0" or digits[b] != "0") else 0
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
            # 已在第一位：退到上一个框的最后一位；
            # 没有上一个框就停在原地（当前位已在上方清零）
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
        self._jump_seg(0)

    def _jump_seg(self, seg: int) -> None:
        """被相邻框调用：接收焦点并停在指定段。"""
        self._focus = True
        self.canvas.focus_set()
        self._seg = seg
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