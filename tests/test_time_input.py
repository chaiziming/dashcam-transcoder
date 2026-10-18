"""验证时间输入控件与 timeutil。

重点验证「可填也可点」：
  - 键入 1-3 段都能解析，失焦后规范化为 HH:MM:SS
  - ↑↓ 微调当前段
  - 点选能打开面板并写入数值
  - 非法输入给出明确报错而不是静默吞掉
"""
from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.timeutil import (clamp_range, format_hhmmss, hhmmss,  # noqa: E402
                           hhmmss_short, parse_hhmmss)

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


def test_timeutil() -> None:
    print("\n== 1. timeutil 解析 ==")
    check("00:01:00 -> 60", parse_hhmmss("00:01:00") == 60)
    check("01:00 -> 60（MM:SS 也接受）", parse_hhmmss("01:00") == 60)
    check("90（纯秒数） -> 90", parse_hhmmss("90") == 90)
    check("空串 -> None", parse_hhmmss("") is None)
    check("None -> None", parse_hhmmss(None) is None)
    check("点号分隔 00.01.00 -> 60", parse_hhmmss("00.01.00") == 60,
          "容忍用户打成点号")
    check("全角冒号 00：01：00 -> 60", parse_hhmmss("00：01：00") == 60)
    for bad in ("abc", "1:2:3:4", "12:ab"):
        try:
            parse_hhmmss(bad)
            check(f"非法输入 {bad!r} 应报错", False, "未抛异常")
        except ValueError:
            check(f"非法输入 {bad!r} 报错", True)

    print("\n== 2. timeutil 格式化 ==")
    check("60 -> 00:01:00", format_hhmmss(60) == "00:01:00")
    check("3661 -> 01:01:01", format_hhmmss(3661) == "01:01:01")
    check("None -> 00:00:00", format_hhmmss(None) == "00:00:00")
    check("hhmmss(3661) -> 01:01:01（完整格式）",
          hhmmss(3661) == "01:01:01", hhmmss(3661))
    check("hhmmss(None) -> --:--:--", hhmmss(None) == "--:--:--")
    check("hhmmss_short(90) -> 01:30", hhmmss_short(90) == "01:30")
    check("hhmmss_short(3661) -> 1:01:01（短格式，不补零）",
          hhmmss_short(3661) == "1:01:01", hhmmss_short(3661))

    print("\n== 3. clamp_range 边界 ==")
    s, e = clamp_range(-5, 9999, 200.0)
    check("start 负数归零", s == 0.0, f"{s}")
    check("end 超出时长被夹住", e <= 199.95, f"{e}")
    s, e = clamp_range(100.0, 50.0, 200.0)
    check("end < start 时自动对齐", s == e, f"{s} vs {e}")


def test_time_input() -> None:
    from app.time_input import TimeInput

    print("\n== 4. TimeInput 键入与规范化 ==")
    root = tk.Tk()
    var = tk.StringVar()
    ti = TimeInput(root, var, width=10)
    root.update_idletasks()

    for raw, expect in (("00:01:00", "00:01:00"),
                        ("01:00", "00:01:00"),
                        ("90", "00:01:30"),
                        ("0:0:5", "00:00:05"),
                        ("00.02.00", "00:02:00")):
        var.set(raw)
        ti._normalize()
        check(f"输入 {raw!r} -> {expect}", var.get() == expect, var.get())

    print("\n== 5. ↑↓ 微调 ==")
    # 每步都从确定的起点开始，避免「基于上一步结果」的累计误差
    var.set("00:01:00")
    ti._seg = 2                      # 选中「秒」
    ti._bump(30)
    check("秒 +30 -> 00:01:30", var.get() == "00:01:30", var.get())

    var.set("00:01:00")
    ti._seg = 2
    ti._bump(-60)                    # 秒 0-60 -> 回绕到 0，分钟不变（不做借位）
    check("秒 -60 回绕（分钟不变）-> 00:01:00", var.get() == "00:01:00",
          f"实际 {var.get()}")

    var.set("00:00:30")
    ti._seg = 2
    ti._bump(-1)
    check("秒 -1 -> 00:00:29", var.get() == "00:00:29", var.get())

    var.set("00:01:00")
    ti._seg = 1                      # 选中「分」
    ti._bump(1)
    check("分 +1 -> 00:02:00", var.get() == "00:02:00", var.get())

    var.set("00:00:00")
    ti._seg = 0                      # 选中「时」
    ti._bump(1)
    check("时 +1 -> 01:00:00", var.get() == "01:00:00", var.get())

    var.set("00:59:00")
    ti._seg = 1
    ti._bump(1)
    check("分 +1 进位不溢出（保持 00:59->00:59+1=01:00 段内回绕）",
          var.get() == "00:00:00", f"实际 {var.get()}（分上限 60，回绕到 0）")

    print("\n== 6. 点选面板 ==")
    var.set("00:00:00")
    ti._open_menu()
    check("点框能打开选择面板", ti._menu is not None)
    if ti._menu is not None:
        ti._pick(1, 45)               # 分 -> 45
        check("选「分=45」-> 00:45:00", var.get() == "00:45:00", var.get())
        ti._open_menu()
        ti._pick(2, 30)               # 秒 -> 30
        check("选「秒=30」-> 00:45:30", var.get() == "00:45:30", var.get())
        check("选完后面板关闭", ti._menu is None)

    print("\n== 7. 光标位置决定选中段 ==")
    var.set("00:01:30")
    ti.entry.icursor(1)
    ti._sync_seg()
    check("光标在时 -> _seg=0", ti._seg == 0, f"seg={ti._seg}")
    ti.entry.icursor(4)
    ti._sync_seg()
    check("光标在分 -> _seg=1", ti._seg == 1, f"seg={ti._seg}")
    ti.entry.icursor(8)
    ti._sync_seg()
    check("光标在秒 -> _seg=2", ti._seg == 2, f"seg={ti._seg}")

    print("\n== 8. 失焦规范化保留非法输入可见 ==")
    var.set("abc")
    ti._normalize()
    check("非法输入不被静默清空（用户能看到问题）", var.get() == "abc", var.get())
    ti._close_menu()

    root.destroy()


def main() -> int:
    print("=" * 58)
    print("时间输入控件验证")
    print("=" * 58)
    test_timeutil()
    test_time_input()
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


if __name__ == "__main__":
    raise SystemExit(main())