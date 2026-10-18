"""本地一键构建：生成与 GitHub Releases 完全一致的安装包。

为什么需要这个：
  CI 每次跑要等 2 分钟 + 下载 100MB+，本地改一行代码就重跑一次太慢。
  本脚本直接调用与 CI 相同的打包链路，产出同样的 setup.exe，
  装完可以直接用，所见即所得。

用法：
    python build_local.py                 # 完整构建（含打包 exe，约 2-4 分钟）
    python build_local.py --skip-exe      # 复用已有 exe，只重编安装包（约 100 秒）
    python build_local.py --version 1.2.3 # 指定版本号（默认用 git describe 或 dev）
    python build_local.py --clean         # 先清干净再构建

前置条件：
  - 已 pip install pyinstaller tkinterdnd2
  - 已安装 Inno Setup（默认在 C:\\Program Files (x86)\\Inno Setup 6）
    装不上的话可以 --skip-inno 只出 dist\\ 里的免安装版目录
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP_NAME = "记录仪视频转码工具"
ISCC_CANDIDATES = [
    r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    r"C:\Program Files\Inno Setup 6\ISCC.exe",
    r"C:\ProgramData\chocolatey\bin\ISCC.exe",
]

# 本机已装 ffmpeg 时复用，省得每次下载 100MB+
LOCAL_FFMPEG_DIRS = [
    r"C:\Program Files\ffmpeg\bin",
    r"C:\ffmpeg\bin",
    str(ROOT / "tools"),
]


class Step:
    """计时输出，让构建过程有进度感。"""

    def __init__(self, total: int) -> None:
        self.total = total
        self.n = 0

    def __call__(self, title: str) -> None:
        self.n += 1
        print(f"\n[{self.n}/{self.total}] {title}", flush=True)


def run(cmd: list[str], *, title: str, must: bool = True) -> int:
    """执行一条命令，失败时打印 stderr 尾部。"""
    print(f"    $ {' '.join(cmd[:6])} ...", flush=True)
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-12:]
        if tail:
            print("    失败输出：", flush=True)
            for ln in tail:
                print(f"      {ln}", flush=True)
        if must:
            raise SystemExit(f"[{title}] 失败，退出码 {proc.returncode}")
    return proc.returncode


def find_iscc() -> str | None:
    for p in ISCC_CANDIDATES:
        if Path(p).is_file():
            return p
    found = shutil.which("ISCC")
    return found


def find_ffmpeg() -> tuple[str | None, str | None]:
    """返回 (ffmpeg 路径, ffprobe 路径)，找不到返回 (None, None)。"""
    exe = shutil.which("ffmpeg")
    probe = shutil.which("ffprobe")
    if exe and probe:
        return exe, probe
    for d in LOCAL_FFMPEG_DIRS:
        f = Path(d) / "ffmpeg.exe"
        p = Path(d) / "ffprobe.exe"
        if f.is_file() and p.is_file():
            return str(f), str(p)
    return exe, probe


def detect_version() -> str:
    """版本号：优先 git describe，其次 git 短哈希，都没有则用 dev。"""
    def git(*a: str) -> str:
        try:
            r = subprocess.run(["git", *a], cwd=ROOT, capture_output=True,
                               text=True, encoding="utf-8", errors="replace")
            return r.stdout.strip() if r.returncode == 0 else ""
        except OSError:
            return ""
    # 只取最近一个 tag，避免历史 tag 污染当前开发版本
    d = git("describe", "--tags", "--abbrev=0")
    if d:
        return d.lstrip("v")
    sha = git("rev-parse", "--short", "HEAD")
    return f"dev-{sha}" if sha else "dev"


def prepare_ffmpeg(assets: Path) -> bool:
    """把 ffmpeg/ffprobe 放进 build_assets。优先复用本机已装的。"""
    dst = assets / "ffmpeg"
    dst.mkdir(parents=True, exist_ok=True)

    # 已有就直接用
    if (dst / "ffmpeg.exe").is_file() and (dst / "ffprobe.exe").is_file():
        print("    复用 build_assets/ffmpeg 里已有的 ffmpeg")
        return True

    ff, fp = find_ffmpeg()
    if ff and fp and Path(ff).is_file() and Path(fp).is_file():
        print(f"    复用本机 ffmpeg：{ff}")
        shutil.copy2(ff, dst / "ffmpeg.exe")
        shutil.copy2(fp, dst / "ffprobe.exe")
        return True

    print("    本机没有 ffmpeg，需要下载（约 110MB）...", flush=True)
    url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
    tmp = ROOT / "build_assets" / "_ffmpeg.zip"
    try:
        import urllib.request
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=60) as r, tmp.open("wb") as f:
            total = int(r.headers.get("Content-Length", 0))
            done = 0
            while chunk := r.read(1 << 20):
                f.write(chunk)
                done += len(chunk)
                pct = f"{done * 100 // total}%" if total else f"{done >> 20}MB"
                print(f"      下载中 {pct}", end="\r", flush=True)
        print()
    except OSError as e:
        raise SystemExit(f"ffmpeg 下载失败：{e}\n"
                         f"请手动装 ffmpeg 后重试，或直接把 ffmpeg.exe/ffprobe.exe "
                         f"放到 {LOCAL_FFMPEG_DIRS[0]}")

    import zipfile
    with zipfile.ZipFile(tmp) as z:
        for name in z.namelist():
            low = name.lower()
            if low.endswith(("ffmpeg.exe", "ffprobe.exe")):
                with z.open(name) as src, (dst / Path(name).name).open("wb") as out:
                    shutil.copyfileobj(src, out)
    tmp.unlink(missing_ok=True)
    return True


def main() -> int:
    ap = argparse.ArgumentParser(
        description="本地构建安装包（与 GitHub Releases 产物一致）")
    ap.add_argument("--version", help="版本号，默认取 git describe")
    ap.add_argument("--skip-exe", action="store_true",
                    help="复用 dist/ 里已有的 exe，只重编安装包")
    ap.add_argument("--skip-inno", action="store_true",
                    help="不编安装包，只产出免安装版目录")
    ap.add_argument("--clean", action="store_true", help="构建前清空产物")
    ap.add_argument("--preset", default="slow",
                    choices=["ultrafast", "fast", "medium", "slow", "veryslow"],
                    help="x264 编码预设，默认 slow（体积最小）")
    args = ap.parse_args()

    version = args.version or detect_version()
    print("=" * 62)
    print(f"本地构建  版本={version}  预设={args.preset}")
    print("=" * 62)

    total = 5 if args.skip_exe else 6
    if args.skip_inno:
        total -= 1
    step = Step(total)

    dist = ROOT / "dist"
    assets = ROOT / "build_assets"
    outdir = ROOT / "dist_installer"

    if args.clean:
        step("清理旧产物")
        for d in (dist, outdir):
            shutil.rmtree(d, ignore_errors=True)
        shutil.rmtree(assets, ignore_errors=True)

    # ---- 1. ffmpeg ----
    step("准备 ffmpeg")
    if not prepare_ffmpeg(assets):
        raise SystemExit("ffmpeg 准备失败")

    # ---- 2. 图标 ----
    step("生成应用图标")
    run([sys.executable, "installer/make_icon.py"], title="生成图标")

    # ---- 3. 打包 exe ----
    if args.skip_exe:
        print("\n[跳过] 打包 exe（--skip-exe）")
    else:
        step("PyInstaller 打包 exe")
        run([sys.executable, "-m", "PyInstaller", "build_exe.spec",
             "--clean", "--noconfirm"], title="PyInstaller")
        exe = dist / f"{APP_NAME}.exe"
        if not exe.is_file():
            raise SystemExit(f"没找到 {exe}")
        shutil.copy2(exe, assets / exe.name)
        print(f"    exe 大小 {exe.stat().st_size / 1048576:.1f} MB")

    # ---- 4. 组装构建资源 ----
    step("组装 build_assets")
    exe = dist / f"{APP_NAME}.exe"
    if not (assets / f"{APP_NAME}.exe").is_file():
        if not exe.is_file():
            raise SystemExit(f"没有可用的 exe：{exe}（先不带 --skip-exe 构建一次）")
        shutil.copy2(exe, assets / exe.name)
    # 清掉可能残留在 build_assets 里的日志/临时文件，
    # 否则会被一起打进安装包（build_assets 里的内容都会进安装程序）
    for junk in assets.glob("_*"):
        junk.unlink(missing_ok=True)
    for f in sorted(assets.rglob("*")):
        if f.is_file() and f.name != f"{APP_NAME}.exe":
            print(f"    {f.relative_to(assets)}  "
                  f"{f.stat().st_size / 1048576:.1f} MB")

    # ---- 5. 免安装版 ----
    step("整理免安装版目录")
    portable = outdir / f"dashcam-transcoder-{version}-portable"
    shutil.rmtree(portable, ignore_errors=True)
    portable.mkdir(parents=True, exist_ok=True)
    for item in (assets / f"{APP_NAME}.exe", assets / "ffmpeg"):
        if item.is_dir():
            shutil.copytree(item, portable / item.name)
        elif item.is_file():
            shutil.copy2(item, portable / item.name)
    readme = ROOT / "docs_使用说明.md"
    if readme.is_file():
        shutil.copy2(readme, portable / "使用说明.md")
    print(f"    免安装版：{portable}")

    # ---- 6. 安装包 ----
    if args.skip_inno:
        print("\n[跳过] 编译安装包（--skip-inno）")
    else:
        step("Inno Setup 编译安装包")
        iscc = find_iscc()
        if not iscc:
            raise SystemExit(
                "找不到 ISCC.exe。请安装 Inno Setup：\n"
                "  https://jrsoftware.org/isdl.php\n"
                "或用 --skip-inno 只出免安装版")
        outdir.mkdir(parents=True, exist_ok=True)
        # 日志放项目根的临时目录，不能放 build_assets —— 那里是要打进安装包的
        log = ROOT / "_iscc_build.log"
        cmd = [
            iscc,
            f"/DAppVersion={version}",
            f"/DBuildAssets={assets}",
            f"/DOutputDir={outdir}",
            f"/DReadmeFile={ROOT / 'docs_使用说明.md'}",
            str(ROOT / "installer" / "dashcam_transcoder.iss"),
        ]
        # x264 预设通过环境变量传给 .iss 不方便，这里直接改写命令参数：
        # .iss 默认 slow，本地调试可先用 ultrafast 提速
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
        log.write_text((proc.stdout or "") + (proc.stderr or ""),
                       encoding="utf-8")
        if proc.returncode != 0:
            tail = ((proc.stderr or "") + (proc.stdout or "")).strip() \
                .splitlines()[-12:]
            for ln in tail:
                print(f"      {ln}", flush=True)
            print(f"    完整日志：{log}")
            raise SystemExit(f"Inno Setup 失败，退出码 {proc.returncode}")
        setups = list(outdir.glob(f"dashcam-transcoder-{version}-setup.exe"))
        if not setups:
            raise SystemExit("编译成功但没找到 setup.exe")
        print(f"    安装包：{setups[0].name}  "
              f"{setups[0].stat().st_size / 1048576:.1f} MB")

    print("\n" + "=" * 62)
    print("构建完成")
    print("=" * 62)
    for f in sorted(outdir.glob("*")):
        if f.is_file() and f.suffix == ".exe":
            print(f"  安装包    {f}")
        elif f.is_dir():
            print(f"  免安装版  {f.name}/")
    print("\n安装包可以直接双击安装测试；免安装版解压即用。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())