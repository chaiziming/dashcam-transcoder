"""端到端真实文件验证：跑完整流程，并用像素级检查确认遮挡真的生效。

不靠肉眼判断，直接统计遮挡区内外的像素方差：
- 遮挡区（纯色填充）内方差应该极低（趋近纯色）
- 遮挡区外（原始 VIN 文字）方差高（有笔画结构）
若两者都高，说明遮挡没生效。
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import ffmpeg_env, packager
from tests.sample_helper import require_sample
from tests.sample_helper import require_sample
from app.mask import MaskPlan, MaskRegion

FF = ffmpeg_env.find_binary("ffmpeg")
FP = ffmpeg_env.find_binary("ffprobe")
SRC = require_sample()  # 见 sample_helper.py

PASS, FAIL = "\033[92mPASS\033[0m", "\033[91mFAIL\033[0m"
_r: list[bool] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    _r.append(bool(cond))
    print(f"  [{PASS if cond else FAIL}] {name}" + (f"  -> {detail}" if detail else ""))


def _frame_stddev(video: Path, ff: str, ts: float, crop: str | None = None,
                  *, raw_h264: bool = False) -> float:
    """抽帧并返回指定区域的亮度标准差（衡量纹理丰富度）。-1.0 表示取样失败。

    注意 seek 方向：裸流没有索引，只能从第 0 帧顺序解码到目标位置，
    因此 -ss 必须放在 -i 之后（输出侧）。放前面会导致解复用器跳不出帧而取样失败。
    """
    vf = (f"crop={crop}," if crop else "") + "scale=480:-2,format=gray"
    cmd = [ff, "-y", "-hide_banner", "-loglevel", "error"]
    if raw_h264:
        cmd += ["-f", "h264"]
    cmd += ["-i", str(video), "-ss", str(ts), "-vf", vf,
            "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    p = subprocess.run(cmd, capture_output=True, timeout=120,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    data = p.stdout
    if len(data) < 16:  # 数据过小 = 取样失败，不是"纯色"
        return -1.0
    n = len(data)
    mean = sum(data) / n
    var = sum((b - mean) ** 2 for b in data) / n
    return var ** 0.5


def main() -> int:
    print("=" * 60)
    print("端到端验证：真实 h264 -> mp4 + VIN/车牌遮挡")
    print("=" * 60)
    if not FF:
        check("ffmpeg 可用", False, "未找到 ffmpeg，请安装或加入 PATH")
        return 1
    if SRC is None:
        print("\n[跳过] 需要真实 h264 样本才能验证像素级遮挡效果。")
        print("       设置环境变量 VIN_TEST_VIDEO 或放 sample.h264 到项目根目录。")
        return 0

    info = ffmpeg_env.probe_video(SRC, FP)
    check("源文件探测", info is not None)
    if not info:
        return 1
    if info.duration is None:
        info.duration = ffmpeg_env.probe_duration_by_counting(FP, SRC, info.fps)
    print(f"  源：{SRC.name} {info.size_str} @{info.fps:g}fps  {hhmmss(info.duration)}  "
          f"音轨={info.has_audio}")

    td = Path(tempfile.mkdtemp(prefix="e2e_"))
    out = td / "e2e_out.mp4"

    # VIN 用预设坐标；车牌区域按前面探测到的实际位置手工加一个框
    plan = MaskPlan(info.width, info.height, use_vin_preset=True)
    plan.extra.append(MaskRegion(170, 865, 190, 105, label="车牌"))

    print("\n-- 执行打包（截取 5s-20s，CRF21 preset slow）--")
    seen: list = []
    res = packager.run_job(
        FF, SRC, out, plan, crf=21, preset="slow",
        start_sec=5, end_sec=20, has_audio=info.has_audio,
        total_duration=info.duration,
        on_progress=lambda p: seen.append(p),
    )
    check("打包成功", res.ok, f"{res.message} rc={res.returncode}")
    if not res.ok:
        print(res.stderr_tail)
        return 1
    check("输出文件存在", out.is_file(),
          f"{out.stat().st_size/1e6:.1f}MB")
    check("进度回调有内容", len(seen) > 0, f"{len(seen)} 次")
    pct = [p.pct for p in seen if p.pct is not None]
    check("进度百分比有效", len(pct) > 0 and max(pct) > 0,
          f"最大 {max(pct):.0f}%" if pct else "无")

    oinfo = ffmpeg_env.probe_video(out, FP)
    check("输出可解析", oinfo is not None)
    if oinfo:
        check("时长符合 15 秒", oinfo.duration and 14 <= oinfo.duration <= 16,
              f"{oinfo.duration}s")
        check("分辨率保持", (oinfo.width, oinfo.height) == (info.width, info.height),
              oinfo.size_str)
        check("编码为 h264", oinfo.codec == "h264", oinfo.codec)

    print("\n-- 像素级遮挡验证（标准差 = 纹理丰富度）--")
    # 对照组直接解码原始裸流同一时刻。
    # 注意：不能用 -c copy 抽裸流片段做对照（裸流无索引，copy 出来的 mp4 可能
    # 无有效流，取样为空会被误判成"已遮挡"而形成假阳性）。
    vin_crop = "w=300:h=20:x=1480:y=1018"
    sd_orig = _frame_stddev(SRC, FF, 12.0, crop=vin_crop, raw_h264=True)
    sd_masked = _frame_stddev(out, FF, 12.0, crop=vin_crop)
    check("对照帧取样成功", sd_orig > 0, f"原始帧标准差 {sd_orig:.2f}")
    check("输出帧取样成功", sd_masked > -1, f"输出帧标准差 {sd_masked:.2f}")
    print(f"     VIN区原始帧标准差 : {sd_orig:.2f}  (文字笔画，纹理丰富)")
    print(f"     VIN区输出帧标准差 : {sd_masked:.2f}  (纯色覆盖，纹理应几乎消失)")
    check("VIN 已被纯色覆盖", 0 <= sd_masked < sd_orig * 0.25,
          f"{sd_masked:.2f} vs 原 {sd_orig:.2f}")

    # 车牌只在部分时间点入画（7 秒帧已放大确认可见），取该时刻验证。
    # sd<0 表示取样失败（该时刻是纯路面/无纹理），此时只报告不判定失败。
    plate_crop = "w=170:h=90:x=180:y=870"
    plate_ts = 7.0
    sd_po = _frame_stddev(SRC, FF, plate_ts, crop=plate_crop, raw_h264=True)
    sd_pm = _frame_stddev(out, FF, plate_ts, crop=plate_crop)
    print(f"     车牌区原始帧标准差 : {sd_po:.2f}")
    print(f"     车牌区输出帧标准差 : {sd_pm:.2f}")
    check("车牌已被纯色覆盖", sd_po > 0 and 0 <= sd_pm <= max(1.0, sd_po * 0.3),
          f"{sd_pm:.2f} vs 原 {sd_po:.2f}")

    # 画面其它部分必须保持原样（不能整屏都被涂黑）
    outside_crop = "w=400:h=200:x=600:y=400"
    sd_out_side = _frame_stddev(out, FF, 12.0, crop=outside_crop)
    sd_out_ref = _frame_stddev(SRC, FF, 12.0, crop=outside_crop, raw_h264=True)
    check("非遮挡区域画面完好", sd_out_side > 8.0,
          f"输出 {sd_out_side:.2f} / 原 {sd_out_ref:.2f}")
    check("非遮挡区与原片接近（未被误涂）",
          sd_out_ref > 0 and abs(sd_out_side - sd_out_ref) / max(1, sd_out_ref) < 0.3,
          f"输出 {sd_out_side:.2f} vs 原 {sd_out_ref:.2f}")

    # 视觉留档：原始 / 输出 各存一张放大图，供人工对照复核。
    # 裸流无索引，-ss 必须放在 -i 之后（输出侧）才能取到帧。
    for tag, box in (("vin", (1420, 1002, 420, 44)), ("plate", (150, 850, 250, 130))):
        w, h = box[2], box[3]
        vf = f"crop=w={w}:h={h}:x={box[0]}:y={box[1]},scale={w*4}:{h*4}:flags=neighbor"
        subprocess.run(
            [FF, "-y", "-hide_banner", "-loglevel", "error", "-ss", "12",
             "-i", str(out), "-vf", vf, "-frames:v", "1",
             str(td / f"final_{tag}.png")],
            capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        subprocess.run(
            [FF, "-y", "-hide_banner", "-loglevel", "error", "-f", "h264",
             "-i", str(SRC), "-ss", "12", "-vf", vf, "-frames:v", "1",
             str(td / f"orig_{tag}.png")],
            capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    print(f"\n人工复核图（原始 / 输出 对照）：\n"
          f"  VIN  原 {td / 'orig_vin.png'}\n       出 {td / 'final_vin.png'}\n"
          f"  车牌 原 {td / 'orig_plate.png'}\n       出 {td / 'final_plate.png'}")

    ok = sum(_r)
    print(f"\n{'=' * 60}\n结果：{ok}/{len(_r)} 通过")
    return 0 if ok == len(_r) else 1


def hhmmss(sec):
    if sec is None:
        return "--:--:--"
    sec = int(sec)
    return f"{sec//3600:02d}:{(sec%3600)//60:02d}:{sec%60:02d}"


if __name__ == "__main__":
    raise SystemExit(main())
