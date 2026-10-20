"""预览播放器内核。

为什么用「常驻 ffmpeg 进程 + PPM 管道」而不是每次调用一次 ffmpeg：
  - 实测每次单独 seek 一次要 200~800ms（进程启动开销），逐帧调用绝不可能流畅
  - 实测常驻进程连续喂帧可达 ~145 fps / 98 MB/s，远高于 15fps 的播放需求

PPM(P6) 格式：magic 行 `P6`、尺寸行 `W H`、最大值行 `255`，随后是 W*H*3 字节 RGB。
注意尺寸是**一行两个数**，不是一行三个 token（这是实测踩过的坑）。
"""
from __future__ import annotations

import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

# 支持的最大倍速。与 player_bar 的倍速下拉选项（最大 4x）保持一致。
# 管道要按「标称帧率 × MAX_SPEED」输出，否则高速倍速会被限流。
MAX_SPEED = 4.0


@dataclass
class ProxySpec:
    """代理预览文件规格。"""
    width: int = 640
    height: int = 360
    crf: int = 30
    preset: str = "ultrafast"
    gop: int = 25          # 每 25 帧一个关键帧，让 seek 足够快

    @property
    def key(self) -> str:
        return f"{self.width}x{self.height}_crf{self.crf}_{self.preset}"


def proxy_path_for(source: Path, spec: ProxySpec) -> Path:
    """代理文件放临时目录，按「源文件名 + 规格 + 源文件大小+修改时间」命名。"""
    import hashlib
    import tempfile
    try:
        st = source.stat()
        sig = f"{source.name}|{st.st_size}|{int(st.st_mtime)}|{spec.key}"
    except OSError:
        sig = f"{source.name}|{spec.key}"
    h = hashlib.md5(sig.encode("utf-8")).hexdigest()[:12]
    d = Path(tempfile.gettempdir()) / "vin_mosaic_proxy"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{source.stem}_{spec.key}_{h}.mp4"


def ensure_proxy(
    ffmpeg: str,
    source: Path,
    spec: ProxySpec,
    *,
    on_progress=None,
    cancel_event: threading.Event | None = None,
    timeout: float = 1800.0,
) -> Path | None:
    """确保代理文件存在并完整，必要时重新生成。

    通过先写 .part 再原子改名的方式避免「半截文件被当成可用代理」。
    """
    out = proxy_path_for(source, spec)
    if out.is_file() and out.stat().st_size > 4096:
        return out

    part = out.with_suffix(".part.mp4")
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]
    if source.suffix.lower() in {".h264", ".avc"}:
        cmd += ["-f", "h264"]
    cmd += [
        "-i", str(source),
        "-vf", f"scale={spec.width}:{spec.height}",
        "-c:v", "libx264", "-preset", spec.preset, "-crf", str(spec.crf),
        "-g", str(spec.gop), "-keyint_min", str(spec.gop),
        "-sc_threshold", "0",       # 固定 GOP，避免不可预期的关键帧
        "-pix_fmt", "yuv420p", "-an",
        "-progress", "pipe:1", "-nostats",
        str(part),
    ]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if os.name == "nt" else 0
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding="utf-8", errors="replace",
                                bufsize=1, creationflags=flags)
    except OSError:
        return None

    start = time.perf_counter()
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.strip()
        if line.startswith("out_time_us=") and on_progress:
            try:
                sec = float(line.split("=", 1)[1]) / 1_000_000
            except ValueError:
                continue
            on_progress(sec, time.perf_counter() - start)
        if cancel_event is not None and cancel_event.is_set():
            proc.terminate()
            proc.wait(timeout=5)
            part.unlink(missing_ok=True)
            return None

    rc = proc.wait(timeout=timeout if timeout else None)
    if rc != 0 or not part.is_file() or part.stat().st_size < 1024:
        part.unlink(missing_ok=True)
        return None
    try:
        part.replace(out)   # 原子替换，避免半成品被复用
    except OSError:
        part.unlink(missing_ok=True)
        return None
    return out


def extract_thumbnail(ffmpeg: str, source: Path, out_path: Path,
                      at_sec: float = 0.0) -> Path | None:
    """从视频里抽一帧存成 PNG（缩略图）。

    用途：非激活的画布要显示「各自的首帧静帧」。
    用 PNG 而不是原始尺寸，是因为代理图本身就是 640x360 的小图，
    抽出来的首帧也按 640x360 存，显示时缩放开销小。

    注意 tk.PhotoImage(file=...) 在 Tk 8.6 下原生支持 PNG，
    所以这里不需要额外引入 Pillow。
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]
    if Path(source).suffix.lower() in {".h264", ".avc"}:
        cmd += ["-f", "h264"]
    if at_sec > 0:
        cmd += ["-ss", f"{at_sec:.3f}"]
    cmd += ["-i", str(source), "-frames:v", "1", "-vf", "scale=640:360",
            "-pix_fmt", "rgb24", str(out_path)]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if os.name == "nt" else 0
    try:
        proc = subprocess.run(cmd, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=120,
                              creationflags=flags)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0 or not out_path.is_file():
        return None
    return out_path


def thumb_path_for(source: Path) -> Path:
    """缩略图存放路径：临时目录 + 源文件名哈希。"""
    import hashlib
    import tempfile
    try:
        st = source.stat()
        sig = f"{source.name}|{st.st_size}|{int(st.st_mtime)}"
    except OSError:
        sig = source.name
    h = hashlib.md5(sig.encode("utf-8")).hexdigest()[:12]
    d = Path(tempfile.gettempdir()) / "vin_mosaic_thumb"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{source.stem}_{h}.png"


class FrameReader:
    """从代理文件持续读 PPM 帧的读取器（配合 FrameStream 使用）。"""

    def __init__(self, ffmpeg: str, proxy: Path, fps: float = 15.0):
        self.ffmpeg = ffmpeg
        self.proxy = Path(proxy)
        self.fps = max(1.0, float(fps))
        self.proc: subprocess.Popen | None = None
        self.w = 0
        self.h = 0
        # 同一管道的并发 read() 会互相消费字节流，导致某一方提前读到 EOF
        # （表现为"播放突然跳到片尾"）。这里加锁保证一次只有一个读者。
        self._read_lock = threading.Lock()
        # 代理文件已按 spec 尺寸生成，这里直接读取实际尺寸
        from .ffmpeg_env import probe_video
        info = probe_video(self.proxy, _probe_path(ffmpeg))
        if info:
            self.w, self.h = info.width, info.height

    def start(self, start_sec: float = 0.0, speed: float = 1.0) -> bool:
        self.stop()
        if self.w <= 0 or self.h <= 0:
            return False
        # -ss 放 -i 之后：代理是 mp4 有索引，输入侧 seek 更快，用输入侧
        # 这里用输出侧以保证第一帧精确（代理很小，代价可忽略）
        #
        # 关于 speed 与 fps：
        #   - speed 完全不用（倍速由主循环调度实现，见 main.py）
        #   - fps 固定为标称值，不放大
        #
        # 踩过的坑：曾把这里改成 fps=self.fps*MAX_SPEED（=60）想「给高速倍速备帧」，
        # 结果管道以 60fps 灌数据、主循环只按 15fps 读，管道里堆满帧，
        # read_frame 变慢，表现为「默认播放就很慢」。已回退。
        vf = f"fps={self.fps}"
        cmd = [self.ffmpeg, "-hide_banner", "-loglevel", "error",
               "-i", str(self.proxy)]
        if start_sec > 0:
            cmd += ["-ss", f"{start_sec:.3f}"]
        cmd += ["-vf", vf, "-f", "image2pipe", "-vcodec", "ppm", "-"]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if os.name == "nt" else 0
        try:
            self.proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                creationflags=flags, bufsize=1024 * 512)
        except OSError:
            self.proc = None
            return False
        return True

    def read_frame(self) -> tuple[int, int, bytes] | None:
        """读一帧，返回 (w, h, rgb_bytes)；结束或出错返回 None。"""
        p = self.proc
        if p is None or p.stdout is None:
            return None
        f = p.stdout
        try:
            with self._read_lock:
                magic = f.readline().strip()
                if magic != b"P6":
                    return None
                dim = f.readline().strip()
                while dim.startswith(b"#"):
                    dim = f.readline().strip()
                parts = dim.split()
                if len(parts) < 2:
                    return None
                w, h = int(parts[0]), int(parts[1])
                maxv = f.readline().strip()
                if int(maxv) != 255:
                    return None
                need = w * h * 3
                data = f.read(need)
                if data is None or len(data) < need:
                    return None
                return w, h, data
        except (OSError, ValueError):
            return None

    def stop(self) -> None:
        with self._read_lock:
            p, self.proc = self.proc, None
        if p is None:
            return
        try:
            if p.stdout is not None:
                p.stdout.close()
        except OSError:
            pass
        try:
            p.terminate()
            p.wait(timeout=2)
        except (subprocess.TimeoutExpired, OSError):
            try:
                p.kill()
            except OSError:
                pass


def _probe_path(ffmpeg: str) -> str:
    """由 ffmpeg 路径推导出同目录的 ffprobe 路径。"""
    cand = Path(ffmpeg).with_name("ffprobe.exe" if os.name == "nt" else "ffprobe")
    if cand.is_file():
        return str(cand)
    from .ffmpeg_env import find_binary
    return find_binary("ffprobe") or ffmpeg