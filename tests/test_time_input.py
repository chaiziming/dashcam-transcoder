"""验证时间输入的「定点覆盖 + 自动前进」行为。

核心需求（用户原话）：
  「鼠标点击的位置输入数字，应该从当前光标处覆盖后面的数字，
    比如在"时"上输入 00，会覆盖"时"上的已有输入然后光标自动移动到"分"上，
    继续输入就继续后移，直到"秒"上的最后，且只能输入数字，
    也能只能显示数字0-9，用户能改的只有数字，"："不能编辑」
"""
from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.time_input import TimeInput  # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


class Ev:
    def __init__(self, ch: str):
        self.char = ch
        # 模拟 tkinter 事件对象的 keysym：可打印字符与其本身同名
        self.keysym = ch if ch else "BackSpace"


def key(ti, ch: str) -> str | None:
    """模拟按一个键。"""
    return ti._on_key(Ev(ch))


def main() -> int:
    root = tk.Tk()
    root.geometry("400x300")

    print("== 1. 默认值（未点击也应有内容）==")
    vs = tk.StringVar()          # 故意不给初值
    tin_s = TimeInput(root, vs)
    tin_s.grid(row=0, column=0, sticky="w", pady=4)
    ve = tk.StringVar()           # 结束时间：同样故意不给初值
    tin_e = TimeInput(root, ve, prev_input=tin_s)
    tin_e.grid(row=1, column=0, sticky="w", pady=4)
    # 串成连续输入流，与 main.py 的接法一致
    tin_s.next_link = tin_e
    root.update_idletasks()
    root.update()
    check("开始框创建后即为 00:00:00", vs.get() == "00:00:00", vs.get())
    check("结束框创建后即为 00:00:00（不必点击）", ve.get() == "00:00:00",
          ve.get())
    check("不使用 Entry（文字自绘，无法被粘贴灌入非法字符）",
          not hasattr(tin_e, "entry"), "已改为 Canvas 自绘")

    print("\n== 2. 在「时」位输入 00，覆盖并自动前进 ==")
    ve.set("00:00:00")
    tin_e._goto(0)                # 光标定位到「时」第一位
    check("定位后 _pos=0（时位）", tin_e._pos == 0, f"_pos={tin_e._pos}")
    key(tin_e, "0")
    check("输入第 1 个 0 -> 00:00:00", ve.get() == "00:00:00", ve.get())
    check("光标前进到「时」第二位（_pos=1）", tin_e._pos == 1,
          f"_pos={tin_e._pos}")
    key(tin_e, "0")
    check("输入第 2 个 0 -> 00:00:00", ve.get() == "00:00:00", ve.get())
    check("时位填满，光标跳到「分」第一位（_pos=3）", tin_e._pos == 3,
          f"_pos={tin_e._pos}")
    key(tin_e, "1")
    key(tin_e, "2")
    check("继续在分位输入 12 -> 00:12:00", ve.get() == "00:12:00", ve.get())
    check("分位填满，光标跳到「秒」第一位（_pos=6）", tin_e._pos == 6,
          f"_pos={tin_e._pos}")

    print("\n== 3. 覆盖已有值，不产生多余位数 ==")
    ve.set("12:34:56")
    tin_e._goto(0)
    key(tin_e, "0")
    key(tin_e, "0")
    check("12:34:56 的时位被 00 覆盖 -> 00:34:56", ve.get() == "00:34:56",
          ve.get())
    check("文本长度始终为 8", len(ve.get()) == 8, f"len={len(ve.get())}")

    print("\n== 4. 连续输入 6 位应正好填满 ==")
    ve.set("00:00:00")
    tin_e._goto(0)
    for ch in "123456":
        key(tin_e, ch)
    check("依次输入 123456 -> 12:34:56", ve.get() == "12:34:56", ve.get())
    check("6 位填满后光标停在秒位末（_pos=7）", tin_e._pos == 7,
          f"_pos={tin_e._pos}")
    key(tin_e, "7")
    check("第 7 位覆盖秒位末位 -> 12:34:57（不新增位数）",
          ve.get() == "12:34:57", ve.get())
    check("文本长度仍为 8", len(ve.get()) == 8, f"len={len(ve.get())}")

    print("\n== 5. 冒号不可编辑 ==")
    ve.set("00:00:00")
    check("冒号键被拦截", key(tin_e, ":") == "break")
    check("值未变化", ve.get() == "00:00:00", ve.get())

    print("\n== 6. 只接受数字 ==")
    ve.set("00:00:00")
    for ch in ("a", " ", "-", "/", ".", "@", "Z"):
        check(f"{ch!r} 被拦截", key(tin_e, ch) == "break")
    check("中文被拦截", key(tin_e, "秒") == "break")
    check("退格被拦截（只能改数字）", key(tin_e, "\b") == "break")
    check("删除被拦截", key(tin_e, "\x7f") == "break")
    check("多字符按键被拦截", key(tin_e, "12") == "break")
    check("值始终未变", ve.get() == "00:00:00", ve.get())

    print("\n== 7. 点击定位到对应位 ==")
    ve.set("00:00:00")
    for x, expect, name in ((0, 0, "最左=时首"), (1, 1, "时末"),
                             (3, 3, "分首"), (4, 4, "分末"),
                             (6, 6, "秒首"), (7, 7, "秒末")):
        tin_e._goto(x)
        check(f"定位 {x} -> {name}（_pos={expect}）",
              tin_e._pos == expect, f"_pos={tin_e._pos}")

    print("\n== 8. 段内前进逻辑（一位一位填）==")
    ve.set("00:00:00")
    tin_e._goto(0)
    key(tin_e, "1")
    check("时位输入 1 -> _pos 前进到时末（1）", tin_e._pos == 1,
          f"_pos={tin_e._pos}")
    check("文本为 10:00:00", ve.get() == "10:00:00", ve.get())
    key(tin_e, "2")
    check("时位输入 2 后跳到分首（3）", tin_e._pos == 3, f"_pos={tin_e._pos}")
    check("文本为 12:00:00", ve.get() == "12:00:00", ve.get())

    print("\n== 9. ↑↓ 微调 ==")
    ve.set("00:00:00")
    tin_e._goto(0)
    tin_e._bump(1)
    check("时 +1 -> 01:00:00", ve.get() == "01:00:00", ve.get())

    ve.set("00:00:00")
    tin_e._goto(3)                # 分位
    tin_e._bump(-1)
    check("分位 00 -1 回绕 -> 00:59:00", ve.get() == "00:59:00", ve.get())

    ve.set("00:59:00")
    tin_e._goto(6)                # 秒位
    tin_e._bump(1)
    check("秒位 00 +1 -> 00:59:01", ve.get() == "00:59:01", ve.get())

    ve.set("99:59:59")
    tin_e._goto(0)
    tin_e._bump(1)
    check("时 99 +1 回绕 -> 00:59:59", ve.get() == "00:59:59", ve.get())

    ve.set("00:00:00")
    tin_e._goto(0)
    tin_e._bump(-1)
    check("时 00 -1 回绕 -> 99:00:00", ve.get() == "99:00:00", ve.get())

    print("\n== 10. 文本格式恒定合法 ==")
    ve.set("99:59:59")
    tin_e._goto(0)
    for ch in "990000":
        key(tin_e, ch)
    check("循环输入后仍为 8 字符", len(ve.get()) == 8, ve.get())
    check("格式合法（位置2/5 为冒号）",
          ve.get()[2] == ":" and ve.get()[5] == ":", ve.get())
    check("全部字符为数字或冒号",
          all(c.isdigit() for c in ve.get()[:2] + ve.get()[3:5] + ve.get()[6:]),
          ve.get())

    print("\n== 11. Backspace：当前位归零并逐位回退 ==")
    ve.set("12:34:56")
    tin_e._goto(7)                       # 秒位末
    tin_e._on_backspace()
    check("秒位末归零 -> 12:34:50", ve.get() == "12:34:50", ve.get())
    check("回退到秒位首（_pos=6）", tin_e._pos == 6, f"_pos={tin_e._pos}")
    tin_e._on_backspace()
    check("秒位首归零 -> 12:34:00", ve.get() == "12:34:00", ve.get())
    check("回退到分位末（_pos=4）", tin_e._pos == 4, f"_pos={tin_e._pos}")
    tin_e._on_backspace()
    check("分位末归零 -> 12:30:00", ve.get() == "12:30:00", ve.get())
    check("回退到分位首（_pos=3）", tin_e._pos == 3, f"_pos={tin_e._pos}")
    for _ in range(6):
        tin_e._on_backspace()
    check("一路回退并逐位清零 -> 00:00:00", ve.get() == "00:00:00", ve.get())
    check("在最前一位时不再回退（_pos=0）", tin_e._pos == 0, f"_pos={tin_e._pos}")
    tin_e._on_backspace()
    check("最前位再按仍是 00:00:00", ve.get() == "00:00:00", ve.get())

    print("\n== 12. 选中位是单个数字的轻量背景块 ==")
    from app.time_input import SEL_BG

    def sel_box() -> tuple | None:
        tin_e._focus = True
        tin_e._render()
        root.update()
        for i in tin_e.canvas.find_all():
            if tin_e.canvas.type(i) == "rectangle" \
                    and tin_e.canvas.itemcget(i, "fill") == SEL_BG:
                return tin_e.canvas.bbox(i)
        return None

    check("绘制了选中位背景块", sel_box() is not None)
    # 单个字符宽度约 1/8 控件宽，块宽应接近它（而不是两倍）
    ctrl_w = tin_e.canvas.winfo_width()
    from app.time_input import BORDER_W as _BW
    one_char = (ctrl_w - (_BW + 5) * 2) / 8.0
    box = sel_box()
    box_w = box[2] - box[0]
    check("选中块宽度约等于一个字符宽（单选，非整段）",
          box_w <= one_char * 1.5, f"块宽 {box_w:.0f} vs 字符宽 {one_char:.0f}")
    check("选中块未描边（视觉克制）",
          str(tin_e.canvas.itemcget(
              [i for i in tin_e.canvas.find_all()
               if tin_e.canvas.type(i) == "rectangle"
               and tin_e.canvas.itemcget(i, "fill") == SEL_BG][0],
              "outline")) == "",
          "outline 应为空")

    xs = []
    for pos in (0, 1, 3, 4, 6, 7):
        tin_e._goto(pos)
        b = sel_box()
        xs.append(b[0])
    check("6 个数字位的高亮位置互不相同", len(set(xs)) == 6, f"x={xs}")
    check("高亮从左到右严格递增", xs == sorted(xs), f"x={xs}")

    tin_e._focus = False
    tin_e._render()
    root.update()
    check("失焦后无选中底色",
          SEL_BG not in [tin_e.canvas.itemcget(i, "fill")
                         for i in tin_e.canvas.find_all()
                         if tin_e.canvas.type(i) == "rectangle"],
          "失焦应无高亮")

    print("\n== 12b. 跨框连续输入 ==")
    vs.set("00:00:00")
    ve.set("00:00:00")
    tin_s._goto(0)
    for ch in "010203":
        tin_s._on_key(Ev(ch))
    check("开始框输入 6 位后 -> 01:02:03", vs.get() == "01:02:03", vs.get())
    check("开始框填满后焦点转到结束框", tin_e._focus is True,
          f"tin_e._focus={tin_e._focus}")
    check("结束框停在「时」段第一位", tin_e._pos == 0, f"_pos={tin_e._pos}")
    for ch in "040506":
        tin_e._on_key(Ev(ch))
    check("继续输入自动写入结束框 -> 04:05:06", ve.get() == "04:05:06", ve.get())
    check("开始框内容不再变化", vs.get() == "01:02:03", vs.get())
    check("结束框填满后停在最后一位", tin_e._pos == 7, f"_pos={tin_e._pos}")
    tin_e._on_backspace()
    check("结束框 Backspace 归零末位 -> 04:05:00", ve.get() == "04:05:00",
          ve.get())
    for _ in range(6):
        tin_e._on_backspace()
    check("一路回退结束框 -> 00:00:00", ve.get() == "00:00:00", ve.get())
    check("再按 Backspace 退到开始框最后一位",
          tin_s._pos == 7, f"tin_s._pos={tin_s._pos}")
    check("开始框拿到焦点", tin_s._focus is True, f"{tin_s._focus}")
    tin_s._on_backspace()
    check("开始框末位归零 -> 01:02:00", vs.get() == "01:02:00", vs.get())

    print("\n== 13. 辅助方法 ==")
    tin_e.set_seconds(3661)
    check("set_seconds(3661) -> 01:01:01", ve.get() == "01:01:01", ve.get())
    check("get_seconds() -> 3661", tin_e.get_seconds() == 3661)

    root.destroy()

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