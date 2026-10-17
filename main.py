"""记录仪视频转码工具 —— 程序入口：python main.py 或打包后双击 exe。

环境变量 VIN_PROBE=1 时不进入交互界面，而是跑一遍自检并把结果写到
probe_result.txt（用于验证打包产物里的拖放 / ffmpeg 定位 / 布局是否正常，
不依赖人工点窗口 —— GUI 崩溃在无人值守环境里最难发现，所以留了这条自检通道）。
"""
from __future__ import annotations

import os
import sys
import time
import traceback
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _probe_sample() -> Path:
    """自检模式找一段样本视频。

    找的是 ffmpeg 自带的测试素材，或环境变量 VIN_TEST_VIDEO 指定的文件 ——
    绝不扫描用户目录，避免误用私人行车记录仪视频。
    """
    env = os.environ.get("VIN_TEST_VIDEO", "").strip().strip('"')
    if env:
        p = Path(env)
        if p.is_file():
            return p
    # ffmpeg 自带的示例素材
    for name in ("testsrc.mp4", "sample.mp4", "sample.h264"):
        for base in (Path(os.getcwd()), Path(__file__).resolve().parent):
            p = base / name
            if p.is_file():
                return p
    return Path("__none__")   # 哨兵：非存在的路径，is_file() 判定会跳过


def _run_probe(out_path: Path) -> None:
    """自检模式：建窗口、注册拖放、找 ffmpeg、检查布局与新功能，把结论写文件。"""
    lines: list[str] = []
    try:
        from app import main as gm

        lines.append("import_ok=True")
        lines.append(f"HAS_DND={gm._HAS_DND}")
        lines.append(f"frozen={getattr(sys, 'frozen', False)}")
        lines.append(f"ffmpeg={gm.ffmpeg_env.find_binary('ffmpeg')}")
        lines.append(f"ffprobe={gm.ffmpeg_env.find_binary('ffprobe')}")

        app = gm.MosaicApp()
        # 必须先 update() 让几何生效，否则 winfo_width 全是 1，布局检查会误判
        app.update_idletasks()
        app.update()
        lines.append("window_created=True")

        try:
            app.drop_target_register(gm.DND_FILES)
            lines.append("drop_target_register=OK")
        except Exception as e:  # noqa: BLE001
            lines.append(f"drop_target_register=FAIL:{type(e).__name__}:{e}")
        try:
            app.dnd_bind("<<Drop>>", lambda ev: None)
            lines.append("dnd_bind=OK")
        except Exception as e:  # noqa: BLE001
            lines.append(f"dnd_bind=FAIL:{type(e).__name__}:{e}")
        try:
            lines.append(f"tkdnd_version={app.tk.eval('package require tkdnd')}")
        except Exception as e:  # noqa: BLE001
            lines.append(f"tkdnd_version=FAIL:{e}")

        # 布局重叠回归（曾出现按钮被文件名标签遮挡）
        try:
            b, lbl = app.btn_choose, app.lbl_src
            bx2, bw = b.winfo_x(), b.winfo_width()
            lines.append(f"layout_btn_w={bw}")
            lines.append(f"layout_no_overlap={lbl.winfo_x() >= bx2 + bw}")
        except Exception as e:  # noqa: BLE001
            lines.append(f"layout_check=FAIL:{e}")

        # 三种打码效果可切换
        try:
            from app.mask import EFFECT_LABELS
            for key, label in EFFECT_LABELS.items():
                app.var_effect.set(label)
                app.on_effect_change()
                lines.append(
                    f"effect_{key}={'OK' if app._current_effect() == key else 'BAD'}")
        except Exception as e:  # noqa: BLE001
            lines.append(f"effect_switch=FAIL:{e}")

        # 播放器：控制条存在 + 代理生成 + 读帧（不开窗口，只验内核）
        try:
            lines.append(f"player_bar={'OK' if hasattr(app, 'player_bar') else 'MISSING'}")
            bar = app.player_bar
            for wname in ("btn_play", "btn_back5", "btn_fwd5",
                          "btn_back30", "btn_fwd30"):
                lines.append(f"{wname}=OK" if hasattr(bar, wname) else f"{wname}=MISSING")
            lines.append(f"playbar_seek={hasattr(bar, 'seek')}")
            from app.player import FrameReader, ProxySpec
            lines.append("player_modules=OK")

            # 有真实视频就跑一遍代理+读帧
            sample = _probe_sample()
            if sample.is_file() and gm.ffmpeg_env.find_binary("ffmpeg"):
                from app.player import ensure_proxy
                t0 = time.perf_counter()
                px = ensure_proxy(gm.ffmpeg_env.find_binary("ffmpeg"), sample, ProxySpec())
                lines.append(f"proxy_ready={px is not None and px.is_file()}")
                lines.append(f"proxy_build_sec={time.perf_counter() - t0:.1f}")
                if px:
                    rd = FrameReader(gm.ffmpeg_env.find_binary("ffmpeg"), px, fps=15)
                    lines.append(f"reader_size={rd.w}x{rd.h}")
                    if rd.start(start_sec=10.0):
                        fr = rd.read_frame()
                        ok = fr is not None and len(fr[2]) == fr[0] * fr[1] * 3
                        lines.append(f"read_frame={'OK' if ok else 'FAIL'}")
                    rd.stop()
        except Exception as e:  # noqa: BLE001
            lines.append(f"player_check=FAIL:{type(e).__name__}:{e}")

        # 分段进度条
        try:
            pr = app.progress
            pr.set_progress(50)
            lines.append(f"progress_50={pr._filled_count()}/{pr.segments}")
            pr.set_progress(100)
            lines.append(f"progress_100={pr._filled_count()}/{pr.segments}")
            pr.reset()
        except Exception as e:  # noqa: BLE001
            lines.append(f"progress=FAIL:{e}")

        app.destroy()
    except Exception as e:  # noqa: BLE001
        lines.append(f"ERROR:{type(e).__name__}:{e}")
        lines.append(traceback.format_exc()[:600])

    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text("\n".join(lines), encoding="utf-8")
    except OSError:
        pass


if __name__ == "__main__":
    if os.environ.get("VIN_PROBE") == "1":
        base = (Path(sys.executable).resolve().parent
                if getattr(sys, "frozen", False)
                else Path(__file__).resolve().parent.parent)
        _run_probe(Path(os.environ.get("VIN_PROBE_OUT", base / "probe_result.txt")))
    else:
        from app.main import main  # noqa: E402

        main()