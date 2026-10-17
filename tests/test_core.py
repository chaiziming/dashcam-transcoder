"""核心逻辑自测：命令生成、坐标缩放、ffprobe 裸流探测、真实打包。

用法: python -m tests.test_core   （在项目根目录下执行）
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import ffmpeg_env, packager
from tests.sample_helper import require_sample
from tests.sample_helper import require_sample
from app.mask import MaskPlan, MaskRegion, scale_box, VIN_PRESET_BOX

PASS, FAIL = "\033[92mPASS\033[0m", "\033[91mFAIL\033[0m"
_results: list[bool] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    _results.append(bool(cond))
    print(f"  [{PASS if cond else FAIL}] {name}" + (f"  -> {detail}" if detail else ""))


def test_find_binary():
    print("\n== 1. ffmpeg 定位 ==")
    ff = ffmpeg_env.find_binary("ffmpeg")
    fp = ffmpeg_env.find_binary("ffprobe")
    check("找到 ffmpeg", bool(ff) and Path(ff).is_file(), ff or "None")
    check("找到 ffprobe", bool(fp) and Path(fp).is_file(), fp or "None")


def test_scale():
    print("\n== 2. 坐标等比缩放 ==")
    same = scale_box(VIN_PRESET_BOX, (1920, 1080), (1920, 1080))
    check("同分辨率原样返回", same == VIN_PRESET_BOX, str(same))
    # 2560x1440 = 4/3 倍
    big = scale_box(VIN_PRESET_BOX, (1920, 1080), (2560, 1440))
    check("1440p 放大正确", big == (1945, 1357, 459, 27), str(big))
    # 1280x720 = 2/3 倍
    small = scale_box(VIN_PRESET_BOX, (1920, 1080), (1280, 720))
    check("720p 缩小正确", small == (973, 679, 229, 13), str(small))
    # 越界裁剪不能崩
    oob = scale_box((1900, 1070, 400, 100), (1920, 1080), (1920, 1080))
    check("越界被裁剪", oob[0] + oob[2] <= 1920 and oob[1] + oob[3] <= 1080, str(oob))


def test_build_command():
    print("\n== 3. 命令生成 ==")

    def has_seq(cmd: list[str], seq: list[str]) -> bool:
        """按 token 序列判断，规避 list.__contains__ 只做单元素精确匹配的问题。"""
        n = len(seq)
        return any(cmd[i:i + n] == seq for i in range(len(cmd) - n + 1))

    plan = MaskPlan(1920, 1080, use_vin_preset=True)
    plan.extra.append(MaskRegion(180, 870, 160, 90, label="车牌"))
    cmd = packager.build_command(
        "ffmpeg.exe", "a.h264", "out.mp4", plan,
        crf=21, preset="slow", start_sec=0, end_sec=60, has_audio=False,
    )
    s = " ".join(cmd)
    check("裸流强制 -f h264", has_seq(cmd, ["-f", "h264"]))
    check("libx264 编码", has_seq(cmd, ["-c:v", "libx264"]))
    check("CRF 21", has_seq(cmd, ["-crf", "21"]))
    check("preset slow", has_seq(cmd, ["-preset", "slow"]))
    check("yuv420p 兼容格式", has_seq(cmd, ["-pix_fmt", "yuv420p"]))
    check("无音频时禁用音频 (-an)", "-an" in cmd)
    check("时间段 -ss 存在", "-ss" in cmd)
    check("时间段 -to 存在", "-to" in cmd)
    # 边界回归：从第 0 秒开始截取时，-ss 0 也必须出现，避免参数不对称
    check("从0秒截取时 -ss 仍存在（回归）", "-ss" in cmd, s)
    # 遮挡滤镜应包含两个 drawbox
    vf = cmd[cmd.index("-vf") + 1]
    check("两个 drawbox 遮挡区", vf.count("drawbox") == 2, vf)
    check("VIN 坐标进入滤镜", "x=1459:y=1018:w=344:h=20" in vf)
    check("车牌坐标进入滤镜", "x=180:y=870:w=160:h=90" in vf)
    check("使用 t=fill 纯色填充", "t=fill" in vf)
    check("进度输出管道", has_seq(cmd, ["-progress", "pipe:1"]))

    # 不给时间段时不应出现 -ss/-to
    cmd_ns = packager.build_command("ffmpeg.exe", "a.h264", "out.mp4",
                                   MaskPlan(1920, 1080), start_sec=None, end_sec=None)
    check("未指定时间段时不加 -ss/-to", "-ss" not in cmd_ns and "-to" not in cmd_ns)

    # 有音频时应走 aac
    cmd2 = packager.build_command("ffmpeg.exe", "b.mp4", "o.mp4", MaskPlan(1920, 1080),
                                   has_audio=True)
    check("有音频时用 aac", has_seq(cmd2, ["-c:a", "aac"]))
    check("有音频时不再 -an", "-an" not in cmd2)
    # 非裸流不加 -f h264
    check("mp4 输入不强制裸流格式", not has_seq(cmd2, ["-f", "h264"]))
    # 关闭 VIN 预设后滤镜里不应再有 VIN 坐标
    plan_off = MaskPlan(1920, 1080, use_vin_preset=False)
    plan_off.extra.append(MaskRegion(10, 10, 20, 20))
    cmd_off = packager.build_command("ffmpeg.exe", "a.h264", "o.mp4", plan_off)
    vf_off = cmd_off[cmd_off.index("-vf") + 1]
    check("关闭 VIN 预设后只剩用户框", vf_off.count("drawbox") == 1
          and "x=1459" not in vf_off, vf_off)


def test_probe_real():
    ff = ffmpeg_env.find_binary("ffprobe")
    src = require_sample()
    print("\n== 4. 真实文件探测 ==")
    if not ff:
        check("ffmpeg 可用", False)
        return
    if src is None:
        check("测试样本可用", False, "未找到样本（设 VIN_TEST_VIDEO 或放 sample.h264）")
        return
    info = ffmpeg_env.probe_video(src, ff)
    check("探测成功", info is not None)
    if info:
        check("分辨率 1920x1080", (info.width, info.height) == (1920, 1080), info.size_str)
        check("帧率 25fps", abs(info.fps - 25.0) < 0.5, str(info.fps))
        check("识别为无音轨", info.has_audio is False, f"has_audio={info.has_audio}")
        # 裸流容器里没有 duration，必须靠数帧反推
        counted = ffmpeg_env.probe_duration_by_counting(ff, src, info.fps)
        check("裸流 duration 可由帧数反推", counted is not None and 200 < counted < 230,
              f"{counted:.1f}s (期望约216s)" if counted else "None")


def test_real_pack():
    ff = ffmpeg_env.find_binary("ffmpeg")
    src = require_sample()
    print("\n== 5. 真实打包（截取 10 秒验证效果） ==")
    if not ff or src is None:
        check("测试样本可用", False)
        return
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "test_out.mp4"
        plan = MaskPlan(1920, 1080, use_vin_preset=True)
        # 加一个覆盖车牌的区
        plan.extra.append(MaskRegion(170, 865, 190, 100, label="车牌"))
        seen: list = []
        res = packager.run_job(
            ff, src, out, plan, crf=23, preset="ultrafast",
            start_sec=5, end_sec=15, has_audio=False,
            total_duration=None,
            on_progress=lambda p: seen.append(p),
        )
        check("打包成功", res.ok, f"rc={res.returncode} {res.message}")
        check("输出文件已生成", out.is_file(),
              f"{out.stat().st_size/1e6:.1f}MB" if out.is_file() else "缺失")
        check("收到进度回调", len(seen) > 0, f"{len(seen)} 次")
        if out.is_file():
            info = ffmpeg_env.probe_video(out, ffmpeg_env.find_binary("ffprobe"))
            check("输出可被解析", info is not None)
            if info:
                check("输出约 10 秒", info.duration and 9 <= info.duration <= 11,
                      f"{info.duration}s")
                check("输出分辨率保持 1920x1080", (info.width, info.height) == (1920, 1080))
            # 抽帧验证遮挡
            frame = Path(td) / "check.png"
            ok = ffmpeg_env.extract_frame(out, ff, 2, frame)
            check("输出可抽帧", ok)
            if ok:
                dest = Path(td) / "check_zoom.png"
                # 放大 VIN 区域，确认已被纯色覆盖
                import subprocess
                subprocess.run(
                    [ff, "-y", "-hide_banner", "-loglevel", "error", "-i", str(frame),
                     "-vf", "crop=w=420:h=44:x=1420:y=1002,scale=1680:176:flags=neighbor",
                     str(dest)],
                    capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                check("放大图已生成", dest.is_file(), str(dest))
                print(f"     放大图路径: {dest}")


def main() -> int:
    test_find_binary()
    test_scale()
    test_build_command()
    test_probe_real()
    test_real_pack()
    ok = sum(_results)
    print(f"\n{'='*46}\n结果: {ok}/{len(_results)} 通过")
    return 0 if ok == len(_results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
