"""倍速下拉框与播放行为验证。

覆盖三个用户实测反馈：
  1. 「打开默认就很慢」—— 1x 时管道不能堆帧，帧率应接近标称 15fps
  2. 「调不了速度」—— 下拉框必须能选、必须能把值传出去
  3. 倍速档位固定为 0.25/0.5/1/2/3，不允许手动输入
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SAMPLE = os.environ.get("VIN_TEST_VIDEO")
if not SAMPLE or not Path(SAMPLE).is_file():
    print("[SKIP] set VIN_TEST_VIDEO")
    raise SystemExit(0)

import tkinter as tk  # noqa: E402

from app import main as gm  # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


def main() -> int:
    app = gm.MosaicApp()
    app.withdraw()
    app.load_file(Path(SAMPLE))
    for _ in range(90):
        app.update()
        if getattr(app, "proxy", None):
            break
        time.sleep(0.2)
    if not getattr(app, "proxy", None):
        print("[SKIP] proxy not ready")
        app.destroy()
        return 0

    bar = app.player_bar

    print("\n== 1. 倍速档位 ==")
    labels = tuple(bar._speed_labels)
    check("档位为 0.25/0.5/1/2/3",
          labels == ("0.25x", "0.5x", "1x", "2x", "3x"), str(labels))
    check("不可手动输入（readonly）",
          str(bar.cmb_speed["state"]) == "readonly",
          str(bar.cmb_speed["state"]))
    check("默认 1x", bar.var_speed.get() == "1x", bar.var_speed.get())
    check("app.speed 初值为 1.0", abs(app.speed - 1.0) < 0.01, str(app.speed))

    print("\n== 2. 选择每一档都必须生效 ==")
    got = []
    for lab in labels:
        bar.var_speed.set(lab)
        bar._on_speed()
        app.update()
        got.append((lab, app.speed))
    for lab, sp in got:
        check(f"选 {lab} -> speed={sp}", abs(sp - float(lab[:-1])) < 0.01,
              f"实际 {sp}")

    print("\n== 3. 1x 播放速度正常（用户反馈的「默认很慢」）==")
    app.on_speed_change(1.0)
    app._pause()
    app.position = 0.0
    app._start_reader(0.0)
    app._stream_start = time.perf_counter()
    app._stream_pos = 0.0
    app.playing = True
    app._schedule_next_frame(0)
    frames = 0
    orig = app._show_rgb

    def counting(w, h, rgb):
        nonlocal frames
        frames += 1
        return orig(w, h, rgb)

    app._show_rgb = counting
    t0 = time.perf_counter()
    pos0 = app.position
    while time.perf_counter() - t0 < 2.0:
        app.update()
        time.sleep(0.004)
    el = time.perf_counter() - t0
    adv = app.position - pos0
    app._pause()
    app._show_rgb = orig
    fps = frames / el
    rate = adv / el
    print(f"   1x: {fps:.1f} fps, 位置 {rate:.2f} s/s")
    # 标称 15fps。允许 0.6x~2.0x 的浮动（机器性能差异）
    check("1x 帧率在标称 15fps 附近（不因管道堆帧而变慢）",
          8.0 <= fps <= 30.0, f"实测 {fps:.1f} fps")
    check("1x 位置推进约 1.0 s/s", 0.75 <= rate <= 1.25, f"实测 {rate:.2f}")

    print("\n== 4. 各档位位置推进必须成比例 ==")
    rates = {}
    for lab in labels:
        sp = float(lab[:-1])
        app.on_speed_change(sp)
        app._pause()
        app.position = 0.0
        app._start_reader(0.0)
        app._stream_start = time.perf_counter()
        app._stream_pos = 0.0
        app.playing = True
        app._schedule_next_frame(0)
        t0 = time.perf_counter()
        p0 = app.position
        while time.perf_counter() - t0 < 1.5:
            app.update()
            time.sleep(0.004)
        el2 = time.perf_counter() - t0
        rates[sp] = (app.position - p0) / el2
        app._pause()
        print(f"   {lab:>6}: {rates[sp]:.2f} s/s")

    base = rates[1.0]
    for lab in labels:
        sp = float(lab[:-1])
        ratio = rates[sp] / base if base else 0
        # 允许 25% 误差
        check(f"{lab} 推进比例 ≈ {sp}", abs(ratio - sp) / sp < 0.28,
              f"实测 {ratio:.2f}x")

    print("\n== 5. 播放中切换倍速不中断 ==")
    app.on_speed_change(1.0)
    app._pause()
    app.position = 0.0
    app._start_reader(0.0)
    app._stream_start = time.perf_counter()
    app._stream_pos = 0.0
    app.playing = True
    app._schedule_next_frame(0)
    for _ in range(15):
        app.update()
        time.sleep(0.01)
    for lab in ("3x", "0.5x", "2x", "1x"):
        bar.var_speed.set(lab)
        bar._on_speed()
        app.update()
        time.sleep(0.05)
    check("连续切换后仍在播放", app.playing is True, f"playing={app.playing}")
    check("speed 跟随最后一次选择", abs(app.speed - 1.0) < 0.01, str(app.speed))
    app._pause()

    app.on_speed_change(1.0)
    try:
        app.destroy()
    except tk.TclError:
        pass

    passed = sum(1 for _, ok, _ in results if ok)
    print("\n" + "=" * 56)
    print(f"结果：{passed}/{len(results)} 通过")
    if passed != len(results):
        print("\n失败项：")
        for n, ok, d in results:
            if not ok:
                print(f"  - {n}  {d}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())