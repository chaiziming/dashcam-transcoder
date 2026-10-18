"""验证 build_merge_command 生成的命令能被 ffmpeg 真正执行。

只测「命令长得对」是不够的 —— 必须真跑，否则 concat 的
标签接错、参数顺序错这类问题只能等 CI 上才发现。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.mask import MaskPlan  # noqa: E402
from app.packager import build_merge_command  # noqa: E402
from app import ffmpeg_env  # noqa: E402

FF = ffmpeg_env.find_binary("ffmpeg")
PROBE = ffmpeg_env.find_binary("ffprobe")

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


def count_frames(path: Path) -> int:
    """数视频帧数，用来验证拼接结果确实是两段之和。"""
    r = subprocess.run(
        [PROBE, "-v", "error", "-select_streams", "v:0",
         "-count_frames", "-show_entries", "stream=nb_read_frames",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True)
    try:
        return int(r.stdout.strip().strip(","))
    except ValueError:
        return -1


def main() -> int:
    sample = __import__("os").environ.get("VIN_TEST_VIDEO")
    if not sample:
        print("[SKIP] 未设置 VIN_TEST_VIDEO")
        return 0
    src = Path(sample)
    if not src.is_file():
        print(f"[SKIP] 样本不存在: {src}")
        return 0
    if not FF or not PROBE:
        print("[SKIP] 找不到 ffmpeg")
        return 0

    out_dir = Path(__file__).resolve().parent.parent / "build_assets" / "mergetest"
    out_dir.mkdir(parents=True, exist_ok=True)

    print("\n== 1. 参数校验 ==")
    try:
        build_merge_command(FF, [src], out_dir / "x.mp4")
        check("单文件应报错", False, "未抛异常")
    except ValueError:
        check("单文件应报错", True, "已抛 ValueError")

    cmd0 = build_merge_command(FF, [src, src], out_dir / "x.mp4")
    check("裸流自动补 -f h264",
          cmd0.count("-f") >= 2 and cmd0[cmd0.index("-f") + 1] == "h264")

    print("\n== 2. 无遮挡：两段拼接 ==")
    out_plain = out_dir / "plain.mp4"
    cmd = build_merge_command(FF, [src, src], out_plain,
                              plan=None, preset="ultrafast")
    print("      命令片段:", " ".join(cmd[6:12]), "...")
    # 只跑前 40 秒，避免测试太久（用 -t 需要改命令，这里直接全量但用 ultrafast）
    cmd_fast = cmd[:]
    if "-t" not in cmd_fast:
        cmd_fast = cmd_fast[:-1] + ["-t", "20", out_plain]
    else:
        cmd_fast[-1] = str(out_plain)
    r = subprocess.run(cmd_fast, capture_output=True, text=True)
    check("ffmpeg 退出码为 0", r.returncode == 0,
          f"exit={r.returncode} {r.stderr.strip().splitlines()[-1][:80] if r.stderr.strip() else ''}")

    single = out_dir / "single.mp4"
    r2 = subprocess.run(
        [FF, "-y", "-hide_banner", "-loglevel", "error", "-f", "h264",
         "-i", str(src), "-t", "10", "-c:v", "libx264", "-preset", "ultrafast",
         "-crf", "23", "-pix_fmt", "yuv420p", "-an", str(single)],
        capture_output=True, text=True)
    check("基准单文件可转码", r2.returncode == 0, f"exit={r2.returncode}")

    # 拼接 2 段共 20 秒，单段 10 秒 → 帧数应约为单段的 2 倍
    n_merged = count_frames(out_plain)
    n_single = count_frames(single)
    check("帧数为 0（说明输出有效非空）", n_merged > 0, f"{n_merged} 帧")
    check("拼接时长约为单段 2 倍",
          n_single > 0 and n_merged > n_single * 1.7,
          f"合并 {n_merged} 帧 vs 单段 {n_single} 帧")

    print("\n== 3. 三种遮挡效果 + 拼接 ==")
    for eff in ("solid", "mosaic", "blur"):
        out_eff = out_dir / f"mask_{eff}.mp4"
        plan = MaskPlan(1920, 1080, use_vin_preset=True, effect=eff)
        cmd = build_merge_command(FF, [src, src], out_eff, plan=plan,
                                  preset="ultrafast")
        cmd = cmd[:-1] + ["-t", "8", str(out_eff)]
        rr = subprocess.run(cmd, capture_output=True, text=True)
        n = count_frames(out_eff) if rr.returncode == 0 else -1
        check(f"{eff} 拼接成功", rr.returncode == 0 and n > 0,
              f"exit={rr.returncode} {n} 帧"
              + ("" if rr.returncode == 0
                 else f" | {rr.stderr.strip().splitlines()[-1][:90]}"))

    print("\n== 4. 三段拼接 ==")
    out3 = out_dir / "three.mp4"
    cmd = build_merge_command(FF, [src, src, src], out3, plan=None,
                              preset="ultrafast")
    cmd = cmd[:-1] + ["-t", "6", str(out3)]
    r3 = subprocess.run(cmd, capture_output=True, text=True)
    n3 = count_frames(out3) if r3.returncode == 0 else -1
    check("三段拼接成功", r3.returncode == 0 and n3 > 0,
          f"exit={r3.returncode} {n3} 帧")

    # 清理
    for p in out_dir.glob("*.mp4"):
        p.unlink()
    try:
        out_dir.rmdir()
    except OSError:
        pass

    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print("\n" + "=" * 54)
    print(f"结果：{passed}/{total} 通过")
    if passed != total:
        print("\n失败项：")
        for name, ok, detail in results:
            if not ok:
                print(f"  - {name}  {detail}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())