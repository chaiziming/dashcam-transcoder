"""验证播放器控制条：按钮存在、可见、布局不被挤压。

起因：PlayerBar 把 grid 的弹性权重给了播放按钮那一列
（`columnconfigure(1, weight=1)`），窗口不够宽时播放按钮被压到看不见 ——
用户反馈「预览区只有 ±5s / ±30s，没有播放按钮」。

本测试实测每个按钮的实际几何尺寸，而不是只检查属性存在。
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
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {name}" + (f"  {detail}" if detail else ""))


class FakeApp:
    """PlayerBar 只用到这些回调，够用即可。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def on_toggle_play(self) -> None:
        self.calls.append("toggle")

    def on_stop_play(self) -> None:
        self.calls.append("stop")

    def on_seek_delta(self, d: float) -> None:
        self.calls.append(f"delta{d:+g}")

    def on_seek_to(self, pos: float) -> None:
        self.calls.append(f"seek{pos:.1f}")

    def on_scrub(self, pos: float) -> None:
        self.calls.append(f"scrub{pos:.1f}")

    def on_speed_change(self, s: float) -> None:
        self.calls.append(f"speed{s:g}")


def main() -> int:
    root = tk.Tk()
    root.withdraw()          # 不弹窗，但 widget 仍有真实几何尺寸
    app = FakeApp()
    bar = PlayerBar(root, app)

    # 用一个偏窄的宽度，模拟用户截图里的窗口尺寸
    root.update_idletasks()
    bar.update_idletasks()
    root.geometry("900x120")
    root.update_idletasks()

    print("\n== 1. 按钮存在性 ==")
    for attr, label in (("btn_play", "播放按钮"), ("btn_stop", "停止按钮"),
                        ("btn_back5", "«5s"), ("btn_fwd5", "5s»"),
                        ("btn_back30", "«30s"), ("btn_fwd30", "30s»")):
        check(f"{label} 已创建", hasattr(bar, attr))

    print("\n== 2. 按钮实际可见（关键回归点）==")
    # 根因：布局权重给错列，按钮宽度过小/为 0 → 看不见
    for attr, label in (("btn_play", "播放按钮"), ("btn_stop", "停止按钮"),
                        ("btn_back5", "«5s"), ("btn_fwd30", "30s»")):
        w = getattr(bar, attr)
        width = w.winfo_width()
        height = w.winfo_height()
        # ttk.Button width=3 约 30px+，宽于 12px 才谈得上可见
        check(f"{label} 宽度足够可见", width >= 12,
              f"实际 {width}x{height}")

    print("\n== 3. 播放/停止按钮不重叠 ==")
    pw = bar.btn_play.winfo_x() + bar.btn_play.winfo_width()
    sx = bar.btn_stop.winfo_x()
    check("播放与停止不重叠", sx >= pw, f"播放右边界={pw}, 停止左边界={sx}")

    print("\n== 4. 未载入文件时全部禁用 ==")
    bar.set_ready(False)
    check("播放按钮初始禁用", str(bar.btn_play["state"]) == "disabled")
    check("停止按钮初始禁用", str(bar.btn_stop["state"]) == "disabled")

    print("\n== 5. 载入后启用并可点击 ==")
    bar.set_ready(True)
    check("播放按钮已启用", str(bar.btn_play["state"]) == "normal")
    check("停止按钮已启用", str(bar.btn_stop["state"]) == "normal")

    bar.btn_play.invoke()
    bar.btn_stop.invoke()
    bar.btn_fwd5.invoke()
    check("点击回调正确", app.calls == ["toggle", "stop", "delta+5"],
          f"实际: {app.calls}")

    print("\n== 6. 图标随播放状态切换 ==")
    check("暂停时显示播放图标", bar.btn_play.cget("text") == "▶")
    bar.set_playing(True)
    check("播放中显示暂停图标", bar.btn_play.cget("text") == "❚❚",
          f"实际 '{bar.btn_play.cget('text')}'")
    bar.set_playing(False)
    check("停止后恢复播放图标", bar.btn_play.cget("text") == "▶")

    print("\n== 7. 弹性权重只给进度条 ==")
    weights = {i: bar.columnconfigure(i).get("weight")
               for i in range(10)}
    stretched = [i for i, w in weights.items() if w]
    check("仅一列有伸缩权重", stretched == [6], f"实际列: {stretched}")
    check("进度条在该列", stretched and stretched[0] == 6)

    root.destroy()

    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print("\n" + "=" * 50)
    print(f"结果：{passed}/{total} 通过")
    if passed != total:
        print("\n失败项：")
        for name, ok, detail in results:
            if not ok:
                print(f"  - {name}  {detail}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())