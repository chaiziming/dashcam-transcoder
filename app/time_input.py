"""紧凑的时间输入控件：显示 HH:MM:SS，可用 ↑↓ 与数字键编辑。

设计要点：
- **Entry 只作显示**（state="readonly"），所有字符变化都由本控件写入。
  若让Entry 直接接收按键，数字会「插入」而不是「覆盖」，
  会出现 `00:00:012` 这种多出一位的脏数据。
- 文本恒为固定 8 字符 `HH:MM:SS`，用户只能改其中6 个数字位，
  冒号不可编辑。
- 点击某一数字位即选中该位；输入数字会**覆盖**该位，
  填满 2 位后光标自动前进到下一段，直到秒位为止。
- ↑↓ 对当前位 +1 / -1，循环取值（小时 0~99，分秒 0~59），不会出现负数。
- `00:00:00 - 00:00:00` 表示整段视频。

绘制：tk.Frame + Canvas 自绘边框（ttk 只能描边不能只描边不填充），
Entry 通过 create_window 嵌入。注意不能对 Canvas 用 delete("all")，
否则会把嵌入的 Entry 一起删掉。
"""
from __future__ import annotations

import tkinter as tk

from .theme import ACCENT
from .timeutil import format_hhmmss, parse_hhmmss

BORDER = "#c8ccd4"
BORDER_FOCUS = ACCENT
FG = "#1f2933"

# 边框线宽（像素）。Entry 必须内缩这么多，否则会被描边压住。
BORDER_W = 2

# 每段的最大值（含）。小时 0~99，分/秒 0~59。
MAX = (99, 59, 59)
# 每段占的数字位数量。
WIDTHS = (2, 2, 2)

# 各段在 8 字符文本中的起止下标（左闭右开）。
#   时 -> [0, 2)、分 -> [3, 5)、秒 -> [6, 8)
SEG_SPAN = ((0, 2), (3, 5), (6, 8))


def blank_text() -> str:
    return "00:00:00"


class TimeInput(tk.Frame):
    """HH:MM:SS 编辑框（数字位可编辑，冒号不可编辑）。"""

    def __init__(self, master, var: tk.StringVar, width: int = 96,
                 on_change=None):
        super().__init__(master, bg=BORDER, width=width, height=24)
        # 关闭几何传播：宽高由width/height 参数固定，
        # 不被子控件（Entry 的请求尺寸）反向撑变形。
        self.pack_propagate(False)
        self.var = var
        self.on_change = on_change
        self._focus = False
        self._pos = 0              # 光标所在数字位的下标（0..7，跳过冒号）
        self._width = width

        # 控件一创建就填满默认值：结束时间框未点击时也应有 00:00:00
        if not self._valid(self.var.get()):
            self.var.set(blank_text())

        self.canvas = tk.Canvas(self, width=width, height=24, bg=BORDER,
                                highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)

        # Entry 只作显示：readonly 阻止任何直接编辑，保证文本格式永远合法
        self.entry = tk.Entry(
            self.canvas, textvariable=self.var, justify="center",
            state="readonly", bd=0, highlightthickness=0,
            bg=BORDER, fg=FG, disabledbackground=BORDER,
            readonlybackground=BORDER, font=("Segoe UI", 10))
        self.win = self.canvas.create_window(0, 0, anchor="center",
                                             window=self.entry,
                                             width=width - BORDER_W * 2,
                                             height=24 - BORDER_W * 2)

        self.canvas.bind("<Configure>", self._layout)
        self.entry.bind("<FocusIn>", self._on_focus_in)
        self.entry.bind("<FocusOut>", self._on_focus_out)
        self.entry.bind("<Button-1>", self._on_click, add="+")
        self.entry.bind("<Left>", lambda e: self._move(-1))
        self.entry.bind("<Right>", lambda e: self._move(1))
        self.entry.bind("<Up>", lambda e: self._bump(1))
        self.entry.bind("<Down>", lambda e: self._bump(-1))
        self.entry.bind("<Home>", lambda e: self._goto(0))
        self.entry.bind("<End>", lambda e: self._goto(7))
        self.entry.bind("<KeyPress>", self._on_key)
        self.var.trace_add("write", lambda *_: self._draw())

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

    # ---------- 绘制 ----------
    def _layout(self, _e=None) -> None:
        """Entry 尺寸与定位：严格内缩在边框线以内。

        Canvas 的描边以坐标为中心向外延伸：画
        create_rectangle(BORDER_W/2, ..., w - BORDER_W/2) 配 width=BORDER_W，
        实际线宽刚好覆盖 x=0..w-1。所以 Entry 要放在内侧区间里。
        """
        w = max(1, self.canvas.winfo_width())
        h = max(1, self.canvas.winfo_height())
        ew = max(20, min(self._width - BORDER_W * 2, w - BORDER_W * 2))
        eh = max(10, h - BORDER_W * 2)
        self.canvas.itemconfigure(self.win, width=ew, height=eh)
        self.canvas.coords(self.win, w // 2, h // 2)
        self._draw()

    def _draw(self) -> None:
        """画边框线（只描边不填充）。

        两个要点：
        1. 只删带 border 标签的元素。**绝不能用 delete("all")** ——
           那会把 create_window 嵌入的 Entry 窗口一起删掉，
           表现为 Entry 尺寸变成 1x1、整框看不见内容。
        2. 只描边、不填充。填充会盖住 Entry，
           而 Entry 背景是半透明的，边框线会从文字中间穿过。
        """
        self.canvas.delete("border")
        w = max(1, self.canvas.winfo_width())
        h = max(1, self.canvas.winfo_height())
        color = BORDER_FOCUS if self._focus else BORDER
        self.canvas.create_rectangle(
            BORDER_W / 2, BORDER_W / 2, w - BORDER_W / 2, h - BORDER_W / 2,
            outline=color, width=BORDER_W, fill="", tags="border")

    # ---------- 焦点 ----------
    def _on_focus_in(self, _e=None) -> None:
        self._focus = True
        self.entry.config(readonlybackground="#ffffff")
        if not self._valid(self.var.get()):
            self.var.set(blank_text())
        self._draw()

    def _on_focus_out(self, _e=None) -> None:
        self._focus = False
        self.entry.config(readonlybackground=BORDER)
        if not self._valid(self.var.get()):
            self.var.set(blank_text())
        self._draw()

    # ---------- 光标定位 ----------
    def _on_click(self, e) -> str:
        """点击某个数字位即选中该位（并让 Entry 获得焦点）。"""
        self.entry.focus_set()
        idx = self.entry.index(f"@{e.x}")
        self._goto(idx)
        return "break"          # 阻止 Entry 自己处理点击

    def _goto(self, idx: int) -> None:
        """把光标移到最近的数字位上。"""
        if idx <= 2:
            self._pos = min(max(0, idx), 1)
        elif idx <= 5:
            self._pos = 3 + min(max(0, idx - 3), 1)
        else:
            self._pos = 6 + min(max(0, idx - 6), 1)
        self._sync_seg()
        self._show_caret()

    def _move(self, step: int) -> str:
        """左右键在 6 个数字位之间移动。"""
        # 可选位置集合：0,1,3,4,6,7
        slots = (0, 1, 3, 4, 6, 7)
        cur = slots.index(self._pos) if self._pos in slots else 0
        nxt = max(0, min(len(slots) - 1, cur + step))
        self._pos = slots[nxt]
        self._sync_seg()
        self._show_caret()
        return "break"

    def _sync_seg(self) -> None:
        """由光标所在位推出段号：0=时 1=分 2=秒。"""
        self._seg = 0 if self._pos < 3 else (1 if self._pos < 6 else 2)

    def _show_caret(self) -> None:
        """把插入光标挪到当前位，并滚动到可见区。

        Entry 是 readonly，插入符只用于定位，不承载任何输入。
        """
        try:
            self.entry.icursor(self._pos)
            self.entry.xview_moveto(0)
        except tk.TclError:
            pass

    # ---------- 输入 ----------
    def _on_key(self, e: tk.Event) -> str:
        """只接受数字：覆盖当前位，填满 2 位后自动前进。

        Entry 是 readonly，本身不会插入任何字符；这里统一拦截所有按键，
        仅对数字做定点覆盖，其余（冒号、退格、粘贴…）全部拒绝。
        """
        ch = e.char
        if not ch.isdigit():
            return "break"       # 包括 ':'，冒号不可编辑
        if len(ch) != 1:
            return "break"

        digits = list(self._digits())
        # 数字位下标 -> _digits 里的下标
        slot = {0: 0, 1: 1, 3: 2, 4: 3, 6: 4, 7: 5}[self._pos]
        digits[slot] = ch
        self._put_digits("".join(digits))

        # 前进规则：
        #   - 当前段的第二位被填 -> 跳到下一段的第一位（时->分，分->秒）
        #   - 当前段的第一位被填 -> 停在本段的第二位
        #   - 秒段的第二位填完 -> 停住，不再前进
        seg = self._seg
        start, end = SEG_SPAN[seg]
        if self._pos == end - 1:              # 刚填的是本段最后一位
            if seg < 2:
                self._pos = end + 1            # 跳过冒号，落到下一段首位
            # 秒段末位填完则保持不动
        else:                                   # 填的是本段第一位
            self._pos = end - 1
        self._sync_seg()
        self._show_caret()
        if self.on_change:
            self.on_change()
        return "break"

    def _bump(self, delta: int) -> str:
        """↑↓ 对当前位 +1 / -1，循环取值（不会为负）。"""
        self._sync_seg()
        d = self._digits()
        sec = parse_hhmmss(self.var.get()) or 0.0
        parts = [int(sec // 3600) % 100, int(sec // 60) % 60, int(sec) % 60]
        hi = MAX[self._seg]
        parts[self._seg] = (parts[self._seg] + delta) % (hi + 1)
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