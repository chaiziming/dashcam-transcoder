"""端到端验证播放/停止的真实行为（不是只查属性）。

用真实视频构建 App，走完整流程：
  载入文件 -> 代理就绪 -> 播放（图标应变暂停）-> 停止（应回到 0 秒）
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SAMPLE = os.environ.get("VIN_TEST_VIDEO")
if not SAMPLE or not Path(SAMPLE).is_file():
    print("[SKIP] 未找到样本（设 VIN_TEST_VIDEO）")
    raise SystemExit(0)

import tkinter as tk  # noqa: E402

from app import main as gm  # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


def pump(root: tk.Tk, seconds: float = 0.3) -> None:
    """跑 Tk 事件循环，让后台任务有机会完成。"""
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        root.update()
        time.sleep(0.02)


def main() -> int:
    root = tk.Tk()
    root.withdraw()
    app = gm.MosaicApp()

    print("\n== 1. 载入真实视频 ==")
    app.load_file(SAMPLE)
    for _ in range(60):
        root.update()
        if getattr(app, "proxy", None):
            break
        time.sleep(0.2)
    check("代理视频已生成", getattr(app, "proxy", None) is not None)
    check("播放控件已启用", str(app.player_bar.btn_play["state"]) == "normal")

    print("\n== 2. 点击播放 ==")
    start = app.position
    app.player_bar.btn_play.invoke()
    pump(root, 0.4)
    check("播放状态为真", app.playing is True, f"playing={app.playing}")
    check("按钮图标变为暂停", app.player_bar.btn_play.cget("text") == "❚❚",
          f"实际 '{app.player_bar.btn_play.cget('text')}'")

    print("\n== 3. 播放位置在推进 ==")
    pump(root, 1.6)
    advanced = app.position - start
    check("播放头已前进", advanced > 0.3,
          f"{start:.2f}s -> {app.position:.2f}s (+{advanced:.2f}s)")

    print("\n== 4. 点击停止 ==")
    app.player_bar.btn_stop.invoke()
    pump(root, 0.8)
    check("播放已停止", app.playing is False, f"playing={app.playing}")
    check("按钮图标恢复为播放", app.player_bar.btn_play.cget("text") == "▶",
          f"实际 '{app.player_bar.btn_play.cget('text')}'")
    check("位置已归零", app.position < 0.6, f"position={app.position:.2f}s")

    print("\n== 5. 停止后能再次播放 ==")
    app.player_bar.btn_play.invoke()
    pump(root, 0.4)
    check("可重新播放", app.playing is True, f"playing={app.playing}")
    check("图标再次变暂停", app.player_bar.btn_play.cget("text") == "❚❚")

    app.player_bar.btn_stop.invoke()
    pump(root, 0.3)

    try:
        root.destroy()
    except tk.TclError:
        pass

    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print("\n" + "=" * 52)
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