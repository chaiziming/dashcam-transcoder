"""验证紧凑时间输入控件（按需求规格）。

规格要点：
  1. 只显示 HH:MM:SS
  2. 禁止输入时间以外的字符（按键层拦截）
  3. 无下拉列表
  4. 光标在时分秒区域内，↑↓ 对该位 +1 / -1
  5. 时 0~99，分/秒 0~59，取值不能为负
  6. 循环：最大值 +1 -> 最小值；最小值 -1 -> 最大值
"""
from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.timeutil import format_hhmmss, parse_hhmmss  # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


def make() -> tuple[tk.Tk, tk.StringVar, object]:
    from app.time_input import TimeInput
    root = tk.Tk()
    root.geometry("400x300")     # 窗口可见，withdraw 状态量不到真实尺寸
    var = tk.StringVar(value="00:00:00")
    ti = TimeInput(root, var)
    ti.grid(row=0, column=0, sticky="w")
    root.update_idletasks()
    root.update()
    return root, var, ti


def main() -> int:
    print("=" * 58)
    print("时间输入控件验证（紧凑版）")
    print("=" * 58)

    print("\n== 1. 基本显示 ==")
    root, var, ti = make()
    root.update()# 窗口需可见，withdraw 状态量不到真实宽度
    check("默认显示 00:00:00", var.get() == "00:00:00", var.get())
    check("无下拉列表（不存在 _menu 属性或始终为 None）",
          getattr(ti, "_menu", None) is None)
    entry_w = ti.entry.winfo_width()
    check("输入区域宽度合理（80~130px）",
          80 <= entry_w <= 130, f"{entry_w}px")

    print("\n== 2. 禁止输入非数字字符 ==")
    for ch, name in (("a", "字母"), (" ", "空格"), ("/", "斜杠"),
                     ("-", "减号"), (".", "点号"), ("@", "符号")):
        ret = ti._on_key(_FakeEvent(ch))
        check(f"拦截{name} {ch!r}", ret == "break", f"返回 {ret!r}")
    check("冒号被放行（用于分段）",
          ti._on_key(_FakeEvent(":")) is None)
    check("数字被放行", ti._on_key(_FakeEvent("5")) is None)

    print("\n== 3. ↑↓ 微调当前段 ==")
    var.set("00:00:00")
    ti.entry.icursor(0)                 # 光标在「时」
    ti._bump(1)
    check("时 +1 -> 01:00:00", var.get() == "01:00:00", var.get())

    var.set("00:00:00")
    ti.entry.icursor(4)                 # 光标在「分」
    ti._bump(1)
    check("分 +1 -> 00:01:00", var.get() == "00:01:00", var.get())

    var.set("00:00:00")
    ti.entry.icursor(7)                 # 光标在「秒」
    ti._bump(1)
    check("秒 +1 -> 00:00:01", var.get() == "00:00:01", var.get())

    var.set("00:00:30")
    ti.entry.icursor(7)
    ti._bump(-1)
    check("秒 -1 -> 00:00:29", var.get() == "00:00:29", var.get())

    print("\n== 4. 循环取值 ==")
    var.set("99:59:59")
    ti.entry.icursor(0)
    ti._bump(1)
    check("时 99 +1 -> 00（回绕到最小）", var.get() == "00:59:59", var.get())

    var.set("00:00:00")
    ti.entry.icursor(0)
    ti._bump(-1)
    check("时 00 -1 -> 99（回绕到最大）", var.get() == "99:00:00", var.get())

    var.set("00:59:00")
    ti.entry.icursor(4)
    ti._bump(1)
    check("分 59 +1 -> 00（分不进位）", var.get() == "00:00:00", var.get())

    var.set("00:00:59")
    ti.entry.icursor(7)
    ti._bump(1)
    check("秒 59 +1 -> 00", var.get() == "00:00:00", var.get())

    print("\n== 5. 取值不能为负 ==")
    var.set("00:00:00")
    for pos, name in ((0, "时"), (4, "分"), (7, "秒")):
        ti.entry.icursor(pos)
        ti._bump(-1)
        v = var.get()
        check(f"{name} 00 -1 后仍非负", not v.startswith("-"), v)

    print("\n== 6. 边界上限 ==")
    var.set("99:59:59")
    check("最大可表示 99:59:59", var.get() == "99:59:59")
    ti.entry.icursor(4)
    ti._bump(1)
    check("分不会超过 59", parse_hhmmss(var.get()) is not None, var.get())

    print("\n== 7. 失焦规范化 ==")
    var.set("")
    ti._on_focus_out()
    check("空值失焦 -> 00:00:00", var.get() == "00:00:00", var.get())
    var.set("00:01:30")
    ti._on_focus_out()
    check("正常值失焦保持不变", var.get() == "00:01:30", var.get())

    print("\n== 8. 读写辅助方法 ==")
    ti.set_seconds(3661)
    check("set_seconds(3661) -> 01:01:01", var.get() == "01:01:01", var.get())
    check("get_seconds() -> 3661", ti.get_seconds() == 3661)

    root.destroy()

    print("\n== 9. 整段语义：00:00:00-00:00:00 ==")
    # 与 on_start 的判定保持一致
    def is_bad(s, e):
        both_zero = (s == 0.0 and (e is None or e == 0.0))
        return not both_zero and s is not None and e is not None and e <= s

    check("00:00:00 - 00:00:00 不算非法（= 整段）",
          not is_bad(0.0, 0.0))
    check("00:01:00 - 00:00:00 非法", is_bad(60.0, 0.0))
    check("00:00:00 - 00:02:00 合法", not is_bad(0.0, 120.0))
    check("00:01:00 - 00:02:00 合法", not is_bad(60.0, 120.0))

    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print("\n" + "=" * 58)
    print(f"结果：{passed}/{total} 通过")
    if passed != total:
        print("\n失败项：")
        for n, ok, d in results:
            if not ok:
                print(f"  - {n}  {d}")
        return 1
    return 0


class _FakeEvent:
    """模拟 tkinter KeyPress 事件。"""

    def __init__(self, char: str):
        self.char = char
        self.keysym = char


if __name__ == "__main__":
    raise SystemExit(main())