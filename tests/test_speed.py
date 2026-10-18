"""实测倍速是否真的影响播放节奏与位置推进。

背景：原先倍速只作用于「位置推进量」，帧间隔仍按标称 fps 计算，
画面节奏不变、只有进度条变慢 —— 用户看着像没生效。

本测试按真实时间测量：
  - 播放 1.5 秒后，位置推进了多少秒
  - 0.25x 与 2x 的推进速度之比
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SAMPLE = os.environ.get("VIN_TEST_VIDEO")
if not SAMPLE or not Path(SAMPLE).is_file():
    print("[SKIP] set VIN_TEST_VIDEO first")
    raise SystemExit(0)

import tkinter as tk  # noqa: E402

from app import main as gm  # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


def pump(root: tk.Tk, seconds: float) -> None:
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        root.update()
        time.sleep(0.005)


def measure(app, root, speed: float, run_for: float = 1.5) -> tuple[float, int]:
    """播放 run_for 秒，返回 (推进的媒体秒数, 实际显示的帧数)。"""
    app.speed = speed
    app._pause()
    app.position = 0.0
    app._start_reader(0.0)
    app._stream_start = time.perf_counter()
    app.playing = True
    app._schedule_next_frame(0)

    # 记录播放前的时间基
    t0 = time.perf_counter()
    pos0 = app.position
    frames = 0
    orig_show = app._show_rgb

    def counting_show(w, h, rgb):
        nonlocal frames
        frames += 1
        return orig_show(w, h, rgb)

    app._show_rgb = counting_show
    pump(root, run_for)
    elapsed = time.perf_counter() - t0
    advanced = app.position - pos0
    app._pause()
    app._show_rgb = orig_show
    return advanced, frames, elapsed


def main() -> int:
    app = gm.MosaicApp()
    app.withdraw()
    app.load_file(Path(SAMPLE))
    for _ in range(80):
        app.update()
        if getattr(app, "proxy", None):
            break
        time.sleep(0.2)
    if not getattr(app, "proxy", None):
        print("[SKIP] proxy not ready")
        app.destroy()
        return 0

    print(f"\nsample duration: {app.info.duration:.1f}s, play_fps={app.play_fps}")

    print("\n== 1. 位置推进速度应与倍速成正比 ==")
    slow_adv, slow_frames, slow_t = measure(app, app, 0.25, 1.5)
    fast_adv, fast_frames, fast_t = measure(app, app, 2.0, 1.5)
    print(f"  0.25x: 推进 {slow_adv:.2f}s / 实耗 {slow_t:.2f}s  (帧 {slow_frames})")
    print(f"  2.00x: 推进 {fast_adv:.2f}s / 实耗 {fast_t:.2f}s  (帧 {fast_frames})")

    # 理论上 2.0x / 0.25x = 8 倍
    ratio = (fast_adv / fast_t) / max(1e-6, slow_adv / slow_t)
    check("倍速比例接近 8:1 (2.0x vs 0.25x)", 5.0 < ratio < 12.0,
          f"实测 {ratio:.1f}:1")

    print("\n== 2. 帧率应随倍速变化（画面节奏）==")
    # 关键修复点：帧间隔除以倍速后，2x 应该显示更多帧
    fps_slow = slow_frames / slow_t
    fps_fast = fast_frames / fast_t
    print(f"  0.25x 实测帧率: {fps_slow:.1f} fps")
    print(f"  2.00x 实测帧率: {fps_fast:.1f} fps")
    check("2x 的显示帧率高于 0.25x（画面节奏真的变快）",
          fps_fast > fps_slow * 1.5, f"{fps_slow:.1f} -> {fps_fast:.1f}")

    print("\n== 3. 暂停状态下改倍速，再播放应生效 ==")
    app._pause()
    app.speed = 4.0
    adv4, fr4, t4 = measure(app, app, 4.0, 1.0)
    print(f"  4.00x: 推进 {adv4:.2f}s / 实耗 {t4:.2f}s  (帧 {fr4})")
    check("4x 明显快于 0.25x", adv4 / t4 > slow_adv / slow_t * 3,
          f"{adv4/t4:.2f} vs {slow_adv/slow_t:.2f}")

    app._pause()
    try:
        app.destroy()
    except tk.TclError:
        pass

    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print("\n" + "=" * 54)
    print(f"result: {passed}/{total} passed")
    if passed != total:
        for n, ok, d in results:
            if not ok:
                print(f"  - {n}  {d}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())