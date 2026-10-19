"""紧凑的时间输入控件：显示 HH:MM:SS，可用 ↑↓ 与数字键编辑。

设计要点：
- **文字完全自绘在 Canvas 上**，不用Entry。
  这样才能精确画出「当前选中位」的高亮块 —— 用 Entry 时，
  文字居中渲染且不可控，做不出分段高亮，还容易出现
  `00:00:012` 这类插入型脏数据。
- 文本恒为固定 8 字符 `HH:MM:SS`，用户只能改其中6 个数字位，冒号不可编辑。
- 点击某个数字位即选中该位（蓝底白字 + 上下高亮条），输入数字会**覆盖**该位，
  填满 2 位后自动前进到下一段，直到秒位为止。
- ↑↓ 对当前位 +1 / -1，循环取值（小时 0~99，分秒 0~59），不会出现负数。
- `00:00:00 - 00:00:00` 表示整段视频。

另外实现上不用 tk.Entry 还有一个好处：readonly 的 Entry 在 Windows 上
仍然能通过粘贴等方式塞进非法字符，自绘则彻底没有这个入口。
"""
from __future__ import annotations

import tkinter as tk

from .theme import ACCENT
from .timeutil import format_hhmmss, parse_hhmmss

BORDER = "#c8ccd4"
BORDER_FOCUS = ACCENT
FG = "#1f2933"
FG_DIM = "#9aa3af"          # 冒号颜色，比数字浅
SEL_BG = "#dbe8ff"         # 选中位底色
SEL_FG = "#123a80"# 选中位文字色
BAR = ACCENT                # 选中位上下指示条

# 边框线宽（像素）
BORDER_W = 2

# 每段的最大值（含）。小时 0~99，分/秒 0~59。
MAX = (99, 59, 59)

# 每段在 8 字符文本中的起止下标（左闭右开）。
SEG_SPAN = ((0, 2), (3, 5), (6, 8))

# 可被选中的数字位下标（跳过冒号）
DIGIT_POS = (0, 1, 3, 4, 6, 7)

# 文本字体
FONT = ("Consolas", 11)


def blank_text() -> str:
    return "00:00:00"


class TimeInput(tk.Frame):
    """HH:MM:SS 编辑框（数字位可编辑，冒号不可编辑，选中位有高亮）。"""

    def __init__(self, master, var: tk.StringVar, width: int = 108,
                 height: int = 26, on_change=None):
        super().__init__(master, bg=BORDER, width=width, height=height)
        # 关闭几何传播：宽高由参数固定，不被子控件反向撑变形。
        self.pack_propagate(False)
        self.var = var
        self.on_change = on_change
        self._focus = False
        self._pos = 0              # 当前选中的数字位下标
        self._width = width
        self._height = height

        # 控件一创建就填满默认值：结束时间框未点击时也应有 00:00:00
        if not self._valid(self.var.get()):
            self.var.set(blank_text())

        self.canvas = tk.Canvas(self, width=width, height=height, bg=BORDER,
                                highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)

        self.canvas.bind("<Configure>", lambda e: self._render())
        self.canvas.bind("<FocusIn>", self._on_focus_in)
        self.canvas.bind("<FocusOut>", self._on_focus_out)
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<Left>", lambda e: self._move(-1))
        self.canvas.bind("<Right>", lambda e: self._move(1))
        self.canvas.bind("<Up>", lambda e: self._bump(1))
        self.canvas.bind("<Down>", lambda e: self._bump(-1))
        self.canvas.bind("<Home>", lambda e: self._goto(0))
        self.canvas.bind("<End>", lambda e: self._goto(7))
        self.canvas.bind("<KeyPress>", self._on_key)
        self.canvas.bind("<BackSpace>", self._on_backspace)
        self.var.trace_add("write", lambda *_: self._render())
        self._render()

    # ---------- 文本工具 ----------
    @staticmethod
    def _valid(text: str) -> bool:
        """文本是否已经是合法的 HH:MM:SS。"""
        return (isinstance(text, str) and len(text) == 8
                and text[2] == ":" and text[5] == ":"
                and all(c.isdigit() for c in text[:2] + text[3:5] + text[6:8]))

    def _digits(self) -> str:
        """取出 6 个数字位（不含冒号）。"""
        t = self.var.get() if self._valid(self.var.get()) else blank_text()
        return t[0:2] + t[3:5] + t[6:8]

    def _put_digits(self, digits: str) -> None:
        """把 6 个数字位写回 HH:MM:SS 格式。"""
        h, m, s = digits[0:2], digits[2:4], digits[4:6]
        self.var.set(f"{h}:{m}:{s}")

    def _slot(self, pos: int) -> int:
        """数字位下标 -> 在 6 位数字串里的下标。"""
        return {0: 0, 1: 1, 3: 2, 4: 3, 6: 4, 7: 5}[pos]

    def _seg_of(self, pos: int) -> int:
        return 0 if pos < 3 else (1 if pos < 6 else 2)

    # ---------- 渲染 ----------
    def _render(self) -> None:
        """重绘整个控件：边框 + 选中高亮 + 文字。

        不能对 Canvas 用 delete("all") 之外的方式保留旧元素 ——
        这里每帧都重建，所以必须一次删干净再画。
        """
        c = self.canvas
        c.delete("all")
        w = max(1, c.winfo_width())
        h = max(1, c.winfo_height())

        # 外边框
        color = BORDER_FOCUS if self._focus else BORDER
        c.create_rectangle(
            BORDER_W / 2, BORDER_W / 2, w - BORDER_W / 2, h - BORDER_W / 2,
            outline=color, width=BORDER_W, fill="")

        text = self.var.get() if self._valid(self.var.get()) else blank_text()
        pad = BORDER_W + 6
        cw = w - pad * 2
        ch = h - pad * 2
        char_w = cw / 8.0          # 每个字符占的宽度
        cy = h // 2

        # 选中位高亮（仅聚焦时显示）
        if self._focus:
            x0 = pad + char_w * self._pos
            x1 = pad + char_w * (self._pos + 1)
            c.create_rectangle(x0, pad, x1, h - pad, fill=SEL_BG, outline="")
            # 上下各一条指示条，让选中边界一眼可见
            c.create_rectangle(x0, pad, x1, pad + 2, fill=BAR, outline="")
            c.create_rectangle(x0, h - pad - 2, x1, h - pad, fill=BAR,
                               outline="")

        # 文字
        for i, chx in enumerate(text):
            if chx == ":":
                c.create_text(pad + char_w * (i + 0.5), cy, text=":",
                              fill=FG_DIM, font=FONT)
                continue
            selected = self._focus and i == self._pos
            c.create_text(
                pad + char_w * (i + 0.5), cy, text=chx,
                fill=SEL_FG if selected else FG,
                font=FONT,
                # 加粗描一下，未聚焦时也能看出段与段的边界
                width=max(1, int(char_w * 0.12)) if not selected else 0)

    # ---------- 焦点 ----------
    def _on_focus_in(self, _e=None) -> None:
        self._focus = True
        self.canvas.focus_set()
        if not self._valid(self.var.get()):
            self.var.set(blank_text())
        self._render()

    def _on_focus_out(self, _e=None) -> None:
        self._focus = False
        if not self._valid(self.var.get()):
            self.var.set(blank_text())
        self._render()

    # ---------- 光标定位 ----------
    def _on_click(self, e) -> str:
        """点击某个数字位即选中该位。"""
        self.canvas.focus_set()
        self._focus = True
        w = max(1, self.canvas.winfo_width())
        pad = BORDER_W + 6
        char_w = (w - pad * 2) / 8.0
        idx = int((e.x - pad) / char_w) if char_w > 0 else 0
        idx = max(0, min(7, idx))
        # 点在冒号上时，归到该冒号左侧的那一段末位
        if idx in (2, 5):
            idx = idx - 1
        self._goto(idx)
        return "break"

    def _goto(self, idx: int) -> None:
        """把选中位移到最接近 idx 的数字位上。"""
        if idx <= 1:
            self._pos = max(0, min(1, idx))
        elif idx <= 4:
            self._pos = 3 + max(0, min(1, idx - 3))
        else:
            self._pos = 6 + max(0, min(1, idx - 6))
        self._render()

    def _move(self, step: int) -> str:
        """左右键在 6 个数字位之间移动。"""
        cur = DIGIT_POS.index(self._pos) if self._pos in DIGIT_POS else 0
        nxt = max(0, min(len(DIGIT_POS) - 1, cur + step))
        self._pos = DIGIT_POS[nxt]
        self._render()
        return "break"

    # ---------- 输入 ----------
    def _on_key(self, e: tk.Event) -> str:
        """只接受数字：覆盖当前位，填满 2 位后自动前进。"""
        if e.keysym in ("BackSpace", "Delete"):
            return "break"       # 交给各自的绑定处理，避免被此处吞掉
        ch = e.char
        if len(ch) != 1 or not ch.isdigit():
            return "break"       # 包括 ':'，冒号不可编辑

        digits = list(self._digits())
        digits[self._slot(self._pos)] = ch
        self._put_digits("".join(digits))

        seg = self._seg_of(self._pos)
        start, end = SEG_SPAN[seg]
        if self._pos == end - 1:              # 刚填的是本段最后一位
            if seg < 2:
                self._pos = end + 1            # 跳过冒号，落到下一段首位
        else:                                   # 填的是本段第一位
            self._pos = end - 1
        self._render()
        if self.on_change:
            self.on_change()
        return "break"

    def _on_backspace(self, _e=None) -> str:
        """Backspace：把当前位归零，并退到前一个数字位。

        行为类似手机时间选择器：清掉当前位，再回到上一位继续编辑。
        已在最前一位时，把该位置零即可（不再回退）。
        """
        digits = list(self._digits())
        digits[self._slot(self._pos)] = "0"
        self._put_digits("".join(digits))

        # 回退到前一个数字位（最前一位则原地清零）
        if self._pos in DIGIT_POS:
            cur = DIGIT_POS.index(self._pos)
            if cur > 0:
                self._pos = DIGIT_POS[cur - 1]
        self._render()
        if self.on_change:
            self.on_change()
        return "break"

    def _bump(self, delta: int) -> str:
        """↑↓ 对当前位 +1 / -1，循环取值（不会为负）。"""
        sec = parse_hhmmss(self.var.get()) or 0.0
        parts = [int(sec // 3600) % 100, int(sec // 60) % 60, int(sec) % 60]
        seg = self._seg_of(self._pos)
        parts[seg] = (parts[seg] + delta) % (MAX[seg] + 1)
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