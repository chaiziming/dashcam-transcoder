"""播放功能测试：真跑播放循环 / seek / 快进 / 倍速。

要点：必须用 app.update() 驱动事件循环，并用 time.sleep 节流模拟真实帧率。
如果无节流地狂调 update()，会一次性把排队的 after 回调全跑完，
导致测出来的播放速度和真实使用不符（曾因此误判为"播放失控"）。

需要一段真实 .h264 样本（定位方式见 tests/sample_helper.py）。
没有样本时本文件会跳过并返回 0，不算失败。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import main as gm  # noqa: E402
from tests.sample_helper import require_sample  # noqa: E402

PASSED: list[bool] = []
FAILED: list[bool] = []


def ck(name: str, cond: bool, detail: str = "") -> None:
    PASSED.append(bool(cond))
    tag = "\033[92mPASS\033[0m" if cond else "\033[91mFAIL\033[0m"
    suffix = f" -> {detail}" if detail else ""
    print(f"  [{tag}] {name}{suffix}")


def main() -> int:
    sample = require_sample()
    if sample is None:
        print("[跳过] 播放测试需要真实 h264 样本。"
              "设置 VIN_TEST_VIDEO 或在项目根放 sample.h264。")
        return 0

    app = gm.MosaicApp()
    app.update_idletasks()
    app.update()

    def spin(sec: float) -> None:
        """驱动事件循环 sec 秒，并按真实帧率节流。"""
        end = time.time() + sec
        while time.time() < end:
            app.update()
            time.sleep(0.005)

    app.load_file(sample)
    ck("文件已载入", app.src is not None, app.src.name if app.src else "None")
    ck("坐标基准为视频真实分辨率",
       app.canvas_box.video_w == app.info.width
       and app.canvas_box.video_h == app.info.height,
       f"{app.canvas_box.video_w}x{app.canvas_box.video_h}")

    print("\n-- 代理预览 --")
    t0 = time.time()
    while app.proxy is None and time.time() - t0 < 120:
        app.update()
        time.sleep(0.03)
    ck("代理预览已就绪", app.proxy is not None, f"{time.time() - t0:.1f}s")
    ck("播放控件已启用",
       str(app.player_bar.btn_play.cget("state")) == "normal")

    print("\n-- 定位 --")
    app.on_seek_to(10.0)
    spin(0.3)
    ck("seek 后定位正确", abs(app.position - 10.0) < 0.1, str(app.position))
    ck("seek 后有画面", getattr(app, "_ppm_cache", None) is not None)

    print("\n-- 播放 --")
    app.on_toggle_play()
    ck("进入播放态", app.playing)
    p0 = app.position
    t0 = time.time()
    frames = 0
    last_key = None
    while time.time() - t0 < 3.0:
        app.update()
        time.sleep(0.006)
        cur = getattr(app, "_ppm_cache", None)
        if cur is not None and id(cur) != last_key:
            frames += 1
            last_key = id(cur)
    elapsed = time.time() - t0
    rate = (app.position - p0) / elapsed
    ck("位置按实时推进", 0.5 < rate < 1.6,
       f"{p0:.1f}s -> {app.position:.1f}s = {rate:.2f}x")
    ck("画面持续刷新", frames >= 8,
       f"{frames} 帧 / {elapsed:.1f}s = {frames / elapsed:.0f} fps")
    ck("未误冲到片尾", app.position < app.info.duration,
       f"{app.position:.1f} < {app.info.duration:.1f}")

    app.on_toggle_play()
    ck("暂停", not app.playing)
    paused = app.position
    spin(0.3)
    ck("暂停后位置不变", abs(app.position - paused) < 0.05,
       f"{paused:.2f} -> {app.position:.2f}")

    print("\n-- 播放中快进 --")
    app.on_seek_to(30.0)
    spin(0.4)
    app.on_toggle_play()
    spin(0.5)
    ck("播放中", app.playing)
    ck("快进前位置已推进", app.position > 30.0, str(app.position))
    app.on_seek_delta(10)
    spin(0.5)
    ck("快进后位置≈40", abs(app.position - 40.0) < 2.5, str(app.position))
    ck("快进后仍在播放", app.playing)
    ck("快进后未冲片尾", app.position < app.info.duration, str(app.position))
    app.on_toggle_play()
    spin(0.2)
    ck("可再次暂停", not app.playing)

    print("\n-- 倍速 --")
    app.on_seek_to(10.0)
    spin(0.3)
    app.on_speed_change(2.0)
    app.on_toggle_play()
    spin(0.5)
    p0 = app.position
    t0 = time.time()
    while time.time() - t0 < 2.0:
        app.update()
        time.sleep(0.006)
    elapsed = time.time() - t0
    fast = (app.position - p0) / elapsed
    ck("2 倍速确实更快", fast > 1.3, f"{fast:.2f}x")
    app.on_toggle_play()
    spin(0.2)
    app.on_speed_change(1.0)

    print("\n-- 边界 --")
    app.on_seek_to(0.0)
    spin(0.2)
    ck("跳到开头", app.position < 0.01, str(app.position))
    app.on_seek_delta(-99)
    spin(0.2)
    ck("不会退到负数", app.position >= -0.01, str(app.position))
    app.on_seek_to(9999)
    spin(0.3)
    ck("不会超出总长",
       app.position <= app.info.duration + 0.01,
       f"{app.position:.1f} <= {app.info.duration:.1f}")

    app.on_close()
    ok = sum(PASSED)
    print(f"\n结果 {ok}/{len(PASSED) + len(FAILED)}")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    raise SystemExit(main())