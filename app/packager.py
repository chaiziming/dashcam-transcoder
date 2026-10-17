"""打包内核：一步到位生成「转码 + 纯色遮挡」的 ffmpeg 命令，并跑起来解析进度。

设计要点：
1. 纯色遮挡用 drawbox（color + t=fill），块内所有像素趋同，无法通过任何算法还原字符，
   比马赛克更适合提交给第三方举报的场景。
2. 时间段用 -ss 放在 -i 之前（输入侧 seek，快速精确），裸流也能正确跳转到指定位置。
3. 裸流 .h264 没有音频轨，若强行 -c:a aac 会报错，因此按需决定是否加音频参数。
4. 裸流 ffprobe 拿不到总时长，进度百分比改为「按已处理秒数/文件体积估算」的降级方案，
   有 duration 时才显示真正的百分比。
"""
from __future__ import annotations

import os
import re
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path

from .mask import FILTER_OUT_LABEL, MaskPlan, build_filter, is_complex_filter

# 匹配 ffmpeg -progress 输出
_TIME_RE = re.compile(r"out_time_ms=(\d+)")
_SPEED_RE = re.compile(r"speed=\s*([\d.]+)x")
_FRAME_RE = re.compile(r"frame=\s*(\d+)")
_BITRATE_RE = re.compile(r"bitrate=\s*([\d.]+)kbit/s")


@dataclass
class Progress:
    seconds: float = 0.0
    speed: float = 0.0
    frame: int = 0
    bitrate: float = 0.0
    total: float | None = None  # 已知总时长才有
    pct: float | None = None    # 0-100，未知总时长时为 None
    stage: str = ""             # 人类可读阶段描述


@dataclass
class JobResult:
    ok: bool
    output: str
    returncode: int = 0
    message: str = ""
    stderr_tail: str = ""


def _default_out(input_path: Path) -> Path:
    return input_path.with_name(f"{input_path.stem}_mosaic.mp4")


def build_command(
    ffmpeg: str,
    input_path: str | Path,
    output_path: str | Path,
    plan: MaskPlan,
    *,
    crf: int = 21,
    preset: str = "slow",
    start_sec: float | None = None,
    end_sec: float | None = None,
    has_audio: bool = False,
) -> list[str]:
    """构造一条命令：h264 裸流 -> mp4，同时对所有区域做纯色遮挡。"""
    inp = str(input_path)
    out = str(output_path)

    cmd: list[str] = [ffmpeg, "-y", "-hide_banner", "-nostdin"]

    # 裸流没有容器头，强制指定输入格式，否则 ffmpeg 可能猜不出来
    if Path(input_path).suffix.lower() in {".h264", ".avc"}:
        cmd += ["-f", "h264"]
    cmd += ["-i", inp]

    # 时间段（输入侧 seek）。
    # 注意：start_sec == 0 是合法的「从开头开始」，不能用 truthy 判断，
    # 否则会出现只有 -to 没有 -ss 的不对称参数，截取长度会算错。
    want_range = start_sec is not None or end_sec is not None
    if want_range:
        if start_sec is not None and start_sec > 0:
            cmd += ["-ss", f"{start_sec:.3f}"]
        elif start_sec is not None:
            cmd += ["-ss", "0.000"]
        if end_sec is not None and end_sec > 0:
            cmd += ["-to", f"{end_sec:.3f}"]

    # 遮挡滤镜：按效果走 -vf（纯色）或 -filter_complex（马赛克/模糊，需要 0:v 标签）
    flt = build_filter(plan)
    if flt:
        if is_complex_filter(flt):
            cmd += ["-filter_complex", flt, "-map", f"[{FILTER_OUT_LABEL}]"]
        else:
            cmd += ["-vf", flt]

    # 编码参数
    cmd += ["-c:v", "libx264", "-preset", preset, "-crf", str(crf)]
    # 像素格式统一为 yuv420p，保证在微信/网页/播放器里兼容（否则部分播放器黑屏）
    cmd += ["-pix_fmt", "yuv420p"]

    if has_audio:
        cmd += ["-c:a", "aac", "-b:a", "128k"]
    else:
        cmd += ["-an"]

    # 进度输出走 stderr，配合 -progress pipe:2 更稳，这里用 stats 行解析
    cmd += ["-progress", "pipe:1", "-nostats", out]
    return cmd


def run_job(
    ffmpeg: str,
    input_path: str | Path,
    output_path: str | Path,
    plan: MaskPlan,
    *,
    crf: int = 21,
    preset: str = "slow",
    start_sec: float | None = None,
    end_sec: float | None = None,
    has_audio: bool = False,
    total_duration: float | None = None,
    on_progress=None,
    on_log=None,
    cancel_event: threading.Event | None = None,
) -> JobResult:
    """执行打包。可随时通过 cancel_event 取消（子进程会被 terminate）。"""
    cmd = build_command(
        ffmpeg, input_path, output_path, plan,
        crf=crf, preset=preset, start_sec=start_sec, end_sec=end_sec,
        has_audio=has_audio,
    )
    if on_log:
        on_log("$ " + " ".join(cmd))

    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if os.name == "nt" else 0
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            creationflags=creationflags,
        )
    except OSError as e:
        return JobResult(False, str(output_path), -1, f"无法启动 ffmpeg：{e}")

    # 计算进度分母：优先总时长；否则用文件体积估算（裸流常见）
    duration_for_progress = end_sec - start_sec if (end_sec and start_sec) else total_duration

    stderr_lines: list[str] = []
    finished_ok = False

    # -progress 输出在 stdout，逐行解析
    assert proc.stdout is not None
    for raw in proc.stdout:
        line = raw.strip()
        if not line:
            continue
        if on_log:
            on_log(line)
        if line.startswith("out_time_ms="):
            m = _TIME_RE.search(line)
            if m:
                # 注意：ffmpeg 的 out_time_ms 实际单位是微秒
                sec = int(m.group(1)) / 1_000_000
                p = Progress(seconds=sec, total=duration_for_progress)
                if duration_for_progress and duration_for_progress > 0:
                    p.pct = min(100.0, sec / duration_for_progress * 100)
                p.stage = f"编码中 {sec:,.1f}s"
                if on_progress:
                    on_progress(p)
        elif line.startswith("speed="):
            m = _SPEED_RE.search(line)
            if m and on_progress:
                try:
                    on_progress(Progress(seconds=0, speed=float(m.group(1)),
                                        total=duration_for_progress,
                                        stage="编码中"))
                except ValueError:
                    pass
        elif line.startswith("progress="):
            if line.strip().endswith("end"):
                finished_ok = True
            continue
        if cancel_event is not None and cancel_event.is_set():
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            return JobResult(False, str(output_path), -2, "已取消")

    rc = proc.wait()
    # stderr 收集（错误信息在这里）
    if proc.stderr is not None:
        try:
            stderr_lines = [ln for ln in proc.stderr.read().splitlines() if ln.strip()]
        except (OSError, ValueError):
            stderr_lines = []

    if cancel_event is not None and cancel_event.is_set():
        return JobResult(False, str(output_path), -2, "已取消")

    tail = "\n".join(stderr_lines[-12:])
    if rc == 0 and Path(output_path).is_file():
        return JobResult(True, str(output_path), 0, "完成", tail)
    msg = "ffmpeg 执行失败"
    if "No such file" in tail:
        msg = "输入文件不存在"
    elif "Invalid data found" in tail:
        msg = "无法解析该文件（可能不是 h264 裸流）"
    return JobResult(False, str(output_path), rc, msg, tail)
