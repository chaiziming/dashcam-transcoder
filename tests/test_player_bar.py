"""验证播放器控制条的双行布局（照主流播放器改版）。

改版动机：旧布局把进度条和按钮挤在同一行，进度条被两端按钮压得很短，
用户手拖动很难定位。新布局：行 0 进度条全宽，行 1 按钮。

本测试实测几何尺寸，重点是进度条宽度必须显著大于旧版。
"""
from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.player_bar import PlayerBar  # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


class FakeApp:
    def on_toggle_play(self) -> None: pass
    def on_stop_play(self) -> None: pass
    def on_seek_delta(self, d: float) -> None: pass
    def on_seek_to(self, p: float) -> None: pass
    def on_scrub(self, p: float) -> None: pass
    def on_speed_change(self, s: float) -> None: pass


def main() -> int:
    root = tk.Tk()
    # 复现 main.py 的容器结构（card Frame + grid sticky="ew"）
    card = tk.Frame(root, bg="#ffffff")
    card.pack(fill="both", expand=True)
    card.columnconfigure(0, weight=1)
    bar = PlayerBar(card, FakeApp())
    bar.grid(row=0, column=0, sticky="ew")
    root.geometry("1000x420")
    root.update_idletasks()
    root.update()

    print("\n== 1. 双行结构（两行是嵌套 Frame，各自独立 grid）==")
    # 进度条与按钮不在同一个 grid 里，用 master 判断是否分层
    check("进度条与按钮分属不同容器",
          bar.seek.master is not bar.btn_play.master,
          f"seek.master={type(bar.seek.master).__name__}, "
          f"btn.master={type(bar.btn_play.master).__name__}")
    # 垂直位置：进度条应在按钮上方
    sy = bar.seek.winfo_rooty()
    by = bar.btn_play.winfo_rooty()
    check("进度条在按钮上方", sy < by, f"seek.y={sy} < btn.y={by}")
    check("两行不重叠", bar.seek.winfo_rooty() + bar.seek.winfo_height()
          <= bar.btn_play.winfo_rooty() + 1,
          f"seek底={sy + bar.seek.winfo_height()}, btn顶={by}")

    print("\n== 2. 进度条随窗口拉伸（关键指标）==")
    widths = []
    ratios = []
    for win_w in (800, 1200, 1600):
        root.geometry(f"{win_w}x420")
        root.update_idletasks()
        root.update()
        seek_w = bar.seek.winfo_width()
        total = bar.winfo_width()
        # 必须在同一轮刷新里取两个值：先读 control 会触发重算，
        # 再读 seek 才拿到新尺寸，顺序反了会拿到上一轮的残留值
        ratios.append(seek_w / total if total else 0)
        widths.append(seek_w)
        print(f"     窗口 {win_w}px -> 进度条 {seek_w}px（占 {ratios[-1] * 100:.0f}%）")
    check("进度条宽度 > 60% 控制条", ratios[0] > 0.6, f"{ratios[0] * 100:.0f}%")
    check("进度条随窗口变宽（1200 > 800）", widths[1] > widths[0],
          f"{widths[0]} -> {widths[1]}")
    check("随窗口继续变宽（1600 > 1200）", widths[2] > widths[1],
          f"{widths[1]} -> {widths[2]}")
    root.geometry("1000x420")
    root.update_idletasks()
    root.update()

    print("\n== 3. 按钮互不重叠 ==")
    btns = [("播放", bar.btn_play), ("停止", bar.btn_stop),
            ("«5s", bar.btn_back5), ("5s»", bar.btn_fwd5),
            ("«30s", bar.btn_back30), ("30s»", bar.btn_fwd30)]

    def overlaps(a, b):
        ax, ay, aw, ah = (a.winfo_rootx(), a.winfo_rooty(),
                          a.winfo_width(), a.winfo_height())
        bx, by, bw, bh = (b.winfo_rootx(), b.winfo_rooty(),
                          b.winfo_width(), b.winfo_height())
        return not (ax + aw <= bx or bx + bw <= ax
                    or ay + ah <= by or by + bh <= ay)

    for i in range(len(btns) - 1):
        n1, b1 = btns[i]
        n2, b2 = btns[i + 1]
        check(f"{n1} 与 {n2} 不重叠", not overlaps(b1, b2),
              f"{b1.winfo_width()}px / {b2.winfo_width()}px")

    print("\n== 4. 倍速在按钮行最右 ==")
    check("倍速在所有按钮右侧",
          bar.cmb_speed.winfo_rootx() > bar.btn_fwd30.winfo_rootx(),
          f"倍速x={bar.cmb_speed.winfo_rootx()} vs 末按钮x={bar.btn_fwd30.winfo_rootx()}")
    check("倍速与按钮同一行（垂直对齐）",
          abs(bar.cmb_speed.winfo_rooty() - bar.btn_play.winfo_rooty()) < 40,
          f"倍速y={bar.cmb_speed.winfo_rooty()} btn.y={bar.btn_play.winfo_rooty()}")

    print("\n== 5. 时间标签在进度条两端 ==")
    lx = bar.lbl_time_left.winfo_rootx() + bar.lbl_time_left.winfo_width()
    skx = bar.seek.winfo_rootx()
    rx = bar.lbl_time_right.winfo_rootx()
    check("当前时间在进度条左侧", lx <= skx, f"左时间右边={lx} <= 进度条={skx}")
    check("总时长在进度条右侧", rx >= skx + bar.seek.winfo_width() - 2,
          f"右时间={rx} vs 进度条右边={skx + bar.seek.winfo_width()}")

    print("\n== 6. update_time 同时更新两侧 ==")
    bar.update_time(75.0, 200.0)
    root.update_idletasks()
    check("左侧显示当前时间", bar.lbl_time_left.cget("text") == "01:15",
          bar.lbl_time_left.cget("text"))
    check("右侧显示总时长", bar.lbl_time_right.cget("text") == "03:20",
          bar.lbl_time_right.cget("text"))
    check("旧属性 lbl_time 仍可用（兼容）",
          bar.lbl_time.cget("text") == "01:15 / 03:20",
          bar.lbl_time.cget("text"))

    print("\n== 7. 全部控件可见 ==")
    for name, widget in [("播放", bar.btn_play), ("停止", bar.btn_stop),
                         ("«5s", bar.btn_back5), ("5s»", bar.btn_fwd5),
                         ("«30s", bar.btn_back30), ("30s»", bar.btn_fwd30),
                         ("倍速", bar.cmb_speed), ("进度条", bar.seek)]:
        w = widget.winfo_width()
        h = widget.winfo_height()
        check(f"{name} 尺寸足够", w >= 12 and h >= 12, f"{w}x{h}")

    root.destroy()
    passed = sum(1 for _, ok, _ in results if ok)
    total_n = len(results)
    print("\n" + "=" * 56)
    print(f"结果：{passed}/{total_n} 通过")
    if passed != total_n:
        print("\n失败项：")
        for n, ok, d in results:
            if not ok:
                print(f"  - {n}  {d}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())