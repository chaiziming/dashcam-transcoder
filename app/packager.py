"""打包内核：一步到位生成「转码 + 纯色遮挡」的 ffmpeg 命令，并跑起来解析进度。

设计要点：
1. 纯色遮挡用 drawbox（color + t=fill），块内所有像素趋同，无法通过任何算法还原字符，
   比马赛克更适合提交给第三方举报的场景。
2. 时间段用 -ss/-to 放在 -i 之后（输出侧 seek）。
   注意：绝不能把 -ss 放到 -i 之前 —— 对 h264 裸流会产出几乎空白的输出文件
   （实测只有 261 字节），因为裸流没有可快速定位的索引。
3. 合并多个分段用「多输入 + filter_complex concat」，不能用 concat demuxer；
   裸流没有文件级元数据，demuxer 会直接报 "No start code is found"。
4. 裸流 .h264 没有音频轨，若强行 -c:a aac 会报错，因此按需决定是否加音频参数。
5. 裸流 ffprobe 拿不到总时长，进度百分比改为「按已处理秒数/文件体积估算」的降级方案，
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


def build_merge_command(
    ffmpeg: str,
    inputs: list[str | Path],
    output_path: str | Path,
    plan: MaskPlan | None = None,
    *,
    crf: int = 21,
    preset: str = "slow",
    fps: float = 25.0,
    out_size: tuple[int, int] | None = None,
) -> list[str]:
    """构造「按顺序拼接多个视频，并顺带做遮挡」的 ffmpeg 命令。

    背景：行车记录仪按时间分段录制，同一起事故常落在两个文件里，
    需要拼成一个连续视频才能看清完整过程。

    实现要点（每条都是实测踩出来的）：
    1. 必须用「多输入 + filter_complex concat」，不能用 concat demuxer。
       裸流 .h264 没有文件级元数据（无 start code / 容器头），
       demuxer 会报 "No start code is found" 直接失败。
    2. 每段都要 fps=25 归一。裸流的 r_frame_rate 是假的
       （ffprobe 会报 1200000/1），不归一会让 concat 输出只有几百字节的空文件。
    3. 每段都要 setpts=PTS-STARTPTS 重置时间戳，否则第二段的时间戳接不上。
    4. setsar=1 统一像素宽高比，避免拼接后画面被拉伸。
    5. **每段都要 scale 到同一尺寸**。concat 滤镜要求所有输入尺寸一致，
       分辨率不同的两段直接拼接会报 "Dimensions mismatch" 而失败。
       out_size 指定目标尺寸（通常取第一段的分辨率）。
    6. 遮挡滤镜排在 concat 之后 —— 先拼完整段，再统一遮挡，
       这样遮挡区域只需在输出分辨率上算一次，不必逐段换算。
    """
    if len(inputs) < 2:
        raise ValueError("合并至少需要 2 个输入文件")

    out = str(output_path)
    cmd: list[str] = [ffmpeg, "-y", "-hide_banner", "-nostdin"]

    # 每个文件作为独立输入；裸流要显式声明格式，否则 ffmpeg 猜不出
    for p in inputs:
        if Path(p).suffix.lower() in {".h264", ".avc"}:
            cmd += ["-f", "h264"]
        cmd += ["-i", str(p)]

    # 逐段归一：帧率、时间戳、尺寸、像素宽高比
    parts = []
    for i in range(len(inputs)):
        chain = [f"fps={fps:g}", "setpts=PTS-STARTPTS"]
        if out_size:
            chain.append(f"scale={out_size[0]}:{out_size[1]}")
        chain.append("setsar=1")
        parts.append(f"[{i}:v]" + ",".join(chain) + f"[v{i}]")

    # concat 把所有段串起来，输出标签固定叫 [cat]
    labels = "".join(f"[v{i}]" for i in range(len(inputs)))
    parts.append(f"{labels}concat=n={len(inputs)}:v=1:a=0[cat]")

    mask = ""
    if plan is not None:
        raw = build_filter(plan)
        if raw:
            if raw.startswith("[0:v]"):
                # 马赛克 / 模糊：自带动输出标签 [outv]，只需换输入源
                mask = raw.replace("[0:v]", "[cat]", 1)
            else:
                # 纯色 drawbox：既无输入标签也无输出标签，需要手工接上
                mask = f"[cat]{raw}[{FILTER_OUT_LABEL}]"

    if mask:
        parts.append(mask)
        cmd += ["-filter_complex", ";".join(parts),
                "-map", f"[{FILTER_OUT_LABEL}]"]
    else:
        cmd += ["-filter_complex", ";".join(parts), "-map", "[cat]"]

    cmd += ["-c:v", "libx264", "-preset", preset, "-crf", str(crf)]
    cmd += ["-pix_fmt", "yuv420p"]
    # 分段录制的裸流没有音频，拼接后也没有，统一不加音频参数
    cmd += ["-an"]
    cmd += ["-progress", "pipe:1", "-nostats", out]
    return cmd


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
    # 计算进度分母：优先总时长；否则用文件体积估算（裸流常见）
    duration_for_progress = end_sec - start_sec if (end_sec and start_sec) else total_duration
    return _execute(cmd, output_path, duration_for_progress,
                    on_progress, on_log, cancel_event)


def run_merge_job(
    cmd: list[str],
    output_path: str | Path,
    *,
    total_duration: float | None = None,
    on_progress=None,
    on_log=None,
    cancel_event: threading.Event | None = None,
) -> JobResult:
    """执行合并。命令由 build_merge_command 生成，这里只负责跑和解析进度。"""
    return _execute(cmd, output_path, total_duration,
                    on_progress, on_log, cancel_event)


def _execute(
    cmd: list[str],
    output_path: str | Path,
    duration_for_progress: float | None,
    on_progress=None,
    on_log=None,
    cancel_event: threading.Event | None = None,
) -> JobResult:
    """执行一条已构造好的 ffmpeg 命令：解析 -progress、收集 stderr、支持取消。

    单文件打包和视频合并共用这一套执行逻辑，避免两份代码行为不一致。
    """
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
    elif "No start code" in tail or "Error splitting the input into NAL units" in tail:
        msg = ("读取失败：视频分段不完整或编码格式不一致。"
               "请确认所有分段来自同一台设备、没有被截断。")
    elif "Dimensions mismatch" in tail or "Parameter" in tail:
        msg = "所选视频的分辨率或帧率不一致，无法合并"
    elif "Filtergraph" in tail:
        msg = "滤镜链出错，请尝试重新选择视频文件"
    return JobResult(False, str(output_path), rc, msg, tail)
