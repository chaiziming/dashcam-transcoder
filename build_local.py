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
import json
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


def resolve_python() -> str | None:
    """找一个真正能用的 Python 可执行文件。

    背景：Windows 会在 `PATH` 里塞一个 0 字节的
    `Microsoft\\WindowsApps\\python.exe` 占位程序，它执行任何脚本都
    直接返回 9009 且没有任何输出 —— 表现为「运行了但什么都没发生」。
    这里必须排除它，否则构建脚本会静默失败。
    """
    def usable(p: str) -> bool:
        try:
            if "WindowsApps" in p:
                return False
            r = subprocess.run([p, "-c", "print(1)"], capture_output=True,
                               text=True, timeout=15)
            return r.returncode == 0 and "1" in (r.stdout or "")
        except (OSError, subprocess.SubprocessError):
            return False

    # 0) 项目自带的 .venv（推荐，uv 管理，完全独立于系统）
    venv = ROOT / ".venv" / "Scripts" / "python.exe"
    if venv.is_file() and usable(str(venv)):
        return f'"{venv}"'

    # 1) py 启动器（官方安装包会装这个）
    launcher = shutil.which("py")
    if launcher:
        r = subprocess.run([launcher, "-3", "-c", "import sys;print(sys.executable)"],
                           capture_output=True, text=True, timeout=15)
        if r.returncode == 0 and (r.stdout or "").strip():
            return f'"{launcher}" -3'

    # 2) PATH 里的 python（跳过 WindowsApps 占位）
    for p in (shutil.which("python") or "", shutil.which("python3") or ""):
        if p and usable(p):
            return f'"{p}"'

    # 3) 常见安装位置
    roots = [
        Path.home() / "AppData/Local/Programs/Python",
        Path("C:/Program Files/Python313"), Path("C:/Program Files/Python312"),
        Path("C:/Python313"), Path("C:/Python312"),
        # 开发工具自带的运行时
        Path.home() / ".workbuddy/binaries/python/versions",
        Path.home() / ".local/bin",
    ]
    for root in roots:
        if not root.is_dir():
            continue
        if root.name == "versions":
            cands = [d / "python.exe" for d in sorted(root.iterdir(), reverse=True)
                     if d.is_dir()]
        else:
            cands = list(root.glob("Python3*/python.exe")) + [root / "python.exe"]
        for c in cands:
            if c.is_file() and usable(str(c)):
                return f'"{c}"'
    return None


def find_uv() -> str | None:
    """找 uv 可执行文件（用于 uv sync / uv run）。"""
    p = shutil.which("uv")
    if p and Path(p).stat().st_size > 1024:
        return p
    # winget 装的话在 Packages 目录下，且 Links 里是 0 字节占位符
    root = Path.home() / "AppData/Local/Microsoft/WinGet/Packages"
    if root.is_dir():
        for pkg in root.glob("astral-sh.uv*"):
            for exe in pkg.glob("uv.exe"):
                if exe.stat().st_size > 1024:
                    return str(exe)
    return None


def ensure_env() -> None:
    """没有可用 Python 时，用 uv 现场创建一个（不污染系统）。"""
    uv = find_uv()
    hint_install = (
        "没找到可用的 Python。\n\n"
        "推荐用 uv 管理（不往系统 PATH 塞任何东西）：\n"
        "    winget install --id astral-sh.uv\n"
        "    uv sync\n\n"
        "或者安装正式版 Python：\n"
        "    https://www.python.org/downloads/\n"
        "    （安装时勾选 Add python.exe to PATH）")
    if not uv:
        raise SystemExit(hint_install)

    print(f"[!] 没找到可用的 Python，尝试用 uv 自动创建环境…")
    print(f"    uv: {uv}")
    r = subprocess.run([uv, "sync"], cwd=ROOT)
    if r.returncode != 0:
        raise SystemExit(f"{hint_install}\n\nuv sync 执行失败（退出码 {r.returncode}）")
    venv = ROOT / ".venv" / "Scripts" / "python.exe"
    if not venv.is_file():
        raise SystemExit("uv sync 成功但没找到 .venv，请检查 pyproject.toml")
    print(f"[*] 环境已就绪：{venv}")


def find_running_instances() -> list[tuple[int, str]]:
    """查找正在运行的程序实例。

    程序开着时会锁住 exe 与工作目录下的文件，后面复制阶段就会报
    PermissionError(WinError 32)。与其失败在第 6 步，不如在这里提醒。

    实现用 PowerShell 的 CIM 查询而不是 tasklist：
      - tasklist 的IMAGENAME 过滤对中文长名不可靠，且输出是 GBK编码
      - CIM 能直接拿到 ExecutablePath，精确判断是不是本项目的程序
    """
    if os.name != "nt" or not APP_NAME:
        return []
    # 只匹配项目目录下的程序，避免误报其他同名进程
    script = (
        "Get-CimInstance Win32_Process -Filter \"Name='"
        + APP_NAME + ".exe'\" | "
        "Select-Object ProcessId, ExecutablePath | ConvertTo-Json -Compress"
    )
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, encoding="utf-8", errors="replace", timeout=20)
    except (OSError, subprocess.SubprocessError):
        return []
    if not out.stdout or not out.stdout.strip():
        return []
    try:
        data = json.loads(out.stdout)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return []
    root = str(ROOT).lower()
    found: list[tuple[int, str]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        path = str(item.get("ExecutablePath") or "")
        pid = item.get("ProcessId")
        # 路径为空（权限不足）或不在项目目录内 => 忽略
        if not path or root not in path.lower():
            continue
        try:
            pid_int = int(pid)
        except (TypeError, ValueError):
            continue
        found.append((pid_int, APP_NAME + ".exe"))
    return found


def check_env(*, skip_exe: bool) -> None:
    """构建前自检，把问题说清楚，别让用户对着闪退的窗口猜。"""
    print(f"    Python: {sys.version.split()[0]}  ({sys.executable})")

    # 微软商店的占位程序：能 import 但不是真 Python
    if "WindowsApps" in sys.executable:
        print("    [警告] 当前 Python 来自 WindowsApps，可能是微软商店的占位程序。")
        print("           建议改装 python.org 版本（勾选 Add to PATH）。")

    # 程序在运行会锁文件，导致后面复制阶段失败
    running = find_running_instances()
    if running:
        print(f"    [警告] 检测到 {APP_NAME}.exe 正在运行"
              f"（PID: {', '.join(str(p) for p, _ in running)}）。")
        print("           程序运行时会锁住文件，复制阶段可能失败。")
        print("           建议先关闭程序再构建；直接继续也可，"
              "构建脚本会自动重试被占用的文件。")

    missing: list[str] = []
    if not skip_exe:
        try:
            import PyInstaller  # noqa: F401
        except ImportError:
            missing.append("pyinstaller")
    try:
        import tkinterdnd2  # noqa: F401
    except ImportError:
        missing.append("tkinterdnd2")

    if missing:
        raise SystemExit(
            "缺少依赖：" + " ".join(missing) + "\n"
            f"请执行：{sys.executable} -m pip install " + " ".join(missing))

    if not find_iscc():
        print("    [警告] 未找到 Inno Setup，将只能产出免安装版。")
        print("           需要安装包请下载：https://jrsoftware.org/isdl.php")
    else:
        print(f"    Inno Setup: {find_iscc()}")


def main() -> int:
    # ---- Python 有效性自检（必须在最前面）----
    # 被 Microsoft Store 的 0 字节 python.exe 调用时，脚本会「执行了但毫无输出」。
    # 这里提前拦下来，并自动改用真正可用的 Python 重新执行。
    if "WindowsApps" in sys.executable:
        print("[!] 检测到当前 python 是微软商店的占位程序（0 字节，不可用）。")
        real = resolve_python()
        if real is None:
            raise SystemExit(
                "\n找不到真正可用的 Python。\n\n"
                "请到 https://www.python.org/downloads/ 安装，\n"
                "安装时务必勾选「Add python.exe to PATH」，装完重新运行本脚本。")
        # real 形如  '"C:\\path\\python.exe"'  或  'C:\\path\\python.exe'
        path = real.strip().strip('"')
        print(f"[*] 改用真正的 Python：{path}\n")
        cmd = [path, str(Path(__file__).resolve()), *sys.argv[1:]]
        raise SystemExit(subprocess.call(cmd))

    # ---- 没有可用解释器时，用 uv 现场创建环境 ----
    real = resolve_python()
    if real is None:
        ensure_env()
        path = real.strip().strip('"')
        cmd = [path, str(Path(__file__).resolve()), *sys.argv[1:]]
        raise SystemExit(subprocess.call(cmd))

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

    # 步骤总数：自检 + ffmpeg + 图标 + [打包exe] + 组装 + 免安装版 + [安装包]
    # （--clean 会多出「清理」一步，--skip-exe / --skip-inno 会少步骤）
    total = 5                      # 自检/ffmpeg/图标/组装/免安装版
    if not args.skip_exe:
        total += 1
    if not args.skip_inno:
        total += 1
    if args.clean:
        total += 1
    step = Step(total)

    dist = ROOT / "dist"
    assets = ROOT / "build_assets"
    outdir = ROOT / "dist_installer"

    if args.clean:
        step("清理旧产物")
        for d in (dist, outdir):
            shutil.rmtree(d, ignore_errors=True)
        shutil.rmtree(assets, ignore_errors=True)

    # ---- 0. 环境自检 ----
    step("环境自检")
    check_env(skip_exe=args.skip_exe)

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

    def copy_robust(src, dst) -> bool:
        """复制文件，遇占用则重试。

        Windows 上目标文件可能正被杀毒软件或资源管理器预览进程打开，
        直接 copy 会抛 PermissionError(WinError 32)。重试几次即可，
        实在不行才报错，避免整个构建因偶发占用而中断。
        """
        for attempt in range(4):
            try:
                shutil.copy2(src, dst)
                return True
            except PermissionError:
                if attempt == 3:
                    return False
                time.sleep(0.6 * (attempt + 1))
        return False

    def copy_dir_robust(src_dir: Path, dst_dir: Path) -> list[str]:
        """复制整个目录，返回未能复制的文件列表。"""
        missing: list[str] = []
        for f in sorted(src_dir.rglob("*")):
            rel = f.relative_to(src_dir)
            target = dst_dir / rel
            if f.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if not copy_robust(f, target):
                missing.append(str(rel))
        return missing

    problems: list[str] = []
    for item in (assets / f"{APP_NAME}.exe", assets / "ffmpeg"):
        if item.is_dir():
            for name in copy_dir_robust(item, portable / item.name):
                problems.append(f"{item.name}/{name}")
        elif item.is_file():
            if not copy_robust(item, portable / item.name):
                problems.append(item.name)
    readme = ROOT / "docs_使用说明.md"
    if readme.is_file() and not copy_robust(readme, portable / "使用说明.md"):
        problems.append("使用说明.md")

    if problems:
        # 不中断构建：明确列出问题，让用户决定是否重跑
        print(f"    [警告] {len(problems)} 个文件被占用未复制：")
        for name in problems[:8]:
            print(f"      - {name}")
        print("    通常是杀毒软件或资源管理器正在扫描目标目录，"
              "关闭后重新构建即可。")
    print(f"    免安装版：{portable}")

    # ---- 6. 安装包 ----
    # 记录本次真正产出的安装包路径；跳过或失败时保持 None，
    # 结尾只报告实际产物，不把目录里遗留的旧文件当成新产物。
    setup_exe: Path | None = None
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
        setup_exe = setups[0]
        print(f"    安装包：{setup_exe.name}  "
              f"{setup_exe.stat().st_size / 1048576:.1f} MB")

    print("\n" + "=" * 62)
    print("构建完成")
    print("=" * 62)
    # 只列出本次真正产出的文件，避免把上一次遗留的旧安装包
    # 也当成新产物报出来（--skip-inno 时尤其容易误导）。
    print(f"  免安装版  {portable.name}/")
    if setup_exe and setup_exe.is_file():
        print(f"  安装包    {setup_exe}")
        print("\n安装包可以直接双击安装测试；免安装版解压即用。")
    else:
        print("\n本次未生成安装包（如需安装包，去掉 --skip-inno 重新构建）。")
        print("免安装版解压即用。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())