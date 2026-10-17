"""ffmpeg / ffprobe 可执行文件定位与视频信息探测。

裸流 .h264 没有容器头，ffprobe 直接探测时 duration 为 N/A，
必须用 -f h264 强制指定格式才能拿到真实时长与帧率。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# 常见安装位置（当 exe 不在 PATH、也不在项目内时兜底）
_COMMON_FFMPEG_DIRS = [
    r"C:\Program Files\ffmpeg\bin",
    r"C:\ffmpeg\bin",
    r"C:\ProgramData\chocolatey\bin",
]


def _base_dir() -> Path:
    """程序运行时的基准目录。

    - PyInstaller 打包后是解包目录（sys._MEIPASS），但 exe 同级目录才是用户放 ffmpeg 的地方，
      所以两者都要检查，优先级：exe 同级 > 解包目录 > 项目根 > tools。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def find_binary(name: str) -> str | None:
    """按优先级查找 ffmpeg / ffprobe：exe 同级目录 -> 项目 tools -> PATH -> 常见安装位置。"""
    exe = f"{name}.exe" if os.name == "nt" else name
    candidates: list[Path] = []

    base = _base_dir()
    candidates += [
        base / "ffmpeg" / exe,
        base / "ffmpeg" / "bin" / exe,
        base / "tools" / exe,
        base / exe,
    ]
    if not getattr(sys, "frozen", False):
        # 源码运行时额外看 venv/Scripts 之类
        candidates.append(base / "bin" / exe)

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "ffmpeg" / exe)
        candidates.append(Path(meipass) / exe)

    for c in candidates:
        if c.is_file():
            return str(c)

    found = shutil.which(name)
    if found:
        return found

    for d in _COMMON_FFMPEG_DIRS:
        c = Path(d) / exe
        if c.is_file():
            return str(c)
    return None


@dataclass
class VideoInfo:
    width: int
    height: int
    fps: float
    duration: float | None  # 秒；裸流可能探测不到
    has_audio: bool
    codec: str

    @property
    def size_str(self) -> str:
        return f"{self.width}x{self.height}"


def _run_json(cmd: list[str]) -> dict | None:
    try:
        p = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=60,
            creationflags=_no_window_flag(),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if p.returncode != 0 or not p.stdout.strip():
        return None
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError:
        return None


def _no_window_flag() -> int:
    """Windows 下隐藏子进程黑框，否则每次调用都会闪一个 cmd 窗口。"""
    if os.name == "nt":
        return getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    return 0


def probe_video(path: str | Path, ffprobe: str) -> VideoInfo | None:
    """探测视频信息。对 .h264 裸流强制 -f h264，避免 duration=N/A。"""
    path = Path(path)
    base_cmd = [ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json"]

    attempts: list[list[str]] = []
    if path.suffix.lower() in {".h264", ".265", ".hevc", ".avc", ".h265", ".m4v"}:
        # 裸流：先按裸流试，失败再按 mp4 容器试（有些记录仪输出的是无扩展名的 mp4）
        attempts.append(base_cmd[:1] + ["-f", "h264", "-i", str(path)] + base_cmd[1:])
    attempts.append(base_cmd + [str(path)])

    for attempt in attempts:
        data = _run_json(attempt)
        if not data:
            continue
        vstreams = [s for s in data.get("streams", []) if s.get("codec_type") == "video"]
        if not vstreams:
            continue
        v = vstreams[0]
        astream = any(s.get("codec_type") == "audio" for s in data.get("streams", []))

        # 帧率：优先 avg_frame_rate，退化用 r_frame_rate，再退化为 25
        fps = 25.0
        for key in ("avg_frame_rate", "r_frame_rate"):
            raw = v.get(key, "")
            if raw and "/" in raw:
                num, _, den = raw.partition("/")
                try:
                    n, d = float(num), float(den)
                    if d:
                        fps = n / d
                        break
                except ValueError:
                    pass

        duration: float | None = None
        for src in (v.get("duration"), data.get("format", {}).get("duration")):
            try:
                duration = float(src)
                break
            except (TypeError, ValueError):
                continue
        # 裸流常见 duration="N/A"，视为未知
        if duration is not None and duration <= 0:
            duration = None

        return VideoInfo(
            width=int(v.get("width") or 0),
            height=int(v.get("height") or 0),
            fps=round(fps, 3),
            duration=duration,
            has_audio=astream,
            codec=v.get("codec_name", "?"),
        )
    return None


def probe_duration_by_counting(ffprobe: str, path: str | Path,
                               fps: float) -> float | None:
    """裸流专用：容器里没有时长，用 -count_packets 数总帧数再除以帧率。

    只做解复用不解码，167MB 的 216 秒片子约 0.5 秒出结果；
    相比解码整个视频快几十倍。返回秒数，失败时返回 None。
    """
    if not fps or fps <= 0:
        return None
    p = Path(path)
    cmd = [ffprobe, "-v", "error", "-f", "h264", "-count_packets",
           "-select_streams", "v:0", "-show_entries", "stream=nb_read_packets",
           "-of", "csv=p=0", str(p)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=120,
                           creationflags=_no_window_flag())
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    raw = r.stdout.strip().split(",")[0].strip()
    try:
        frames = float(raw)
    except ValueError:
        return None
    if frames <= 0:
        return None
    return frames / fps


def extract_frame(video: str | Path, ffmpeg: str, ts: float, out_png: str | Path,
                  *, force_raw: bool = False) -> bool:
    """抽一帧存成 PNG，供界面预览与拖框用。

    ts<0 表示取第一帧（裸流用 -ss 定位第一帧可能不准，直接不传 -ss 更稳）。
    """
    video = Path(video)
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]
    if force_raw or video.suffix.lower() in {".h264", ".265", ".hevc", ".avc", ".h265"}:
        cmd += ["-f", "h264"] if video.suffix.lower() in {".h264", ".avc", ".h265"} else []
        cmd += ["-i", str(video)]
    else:
        cmd += ["-i", str(video)]
    if ts and ts > 0:
        cmd += ["-ss", f"{ts:.3f}"]
    cmd += ["-frames:v", "1", str(out_png)]

    try:
        p = subprocess.run(cmd, capture_output=True, timeout=120,
                           creationflags=_no_window_flag())
        return p.returncode == 0 and Path(out_png).is_file()
    except (OSError, subprocess.SubprocessError):
        return False
