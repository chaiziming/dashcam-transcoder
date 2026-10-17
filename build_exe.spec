# -*- mode: python ; coding: utf-8 -*-
"""记录仪视频转码工具 —— PyInstaller 打包配置。

不打进 ffmpeg（440MB 太大且体积翻倍），改为把 ffmpeg/ffprobe 放在 exe 同级的
ffmpeg/ 子目录，程序启动时会自动按同级 -> PATH -> 常见路径的顺序查找。

tkinterdnd2 是拖放功能的硬依赖，必须显式声明：
1. hiddenimports 里写它，避免被静态分析漏掉；
2. 它自带 tkdnd 的 Tcl 扩展（Tcl 库 + 平台 dll），PyInstaller 没有内置 hook，
   必须通过 datas 显式带上，否则打包后 import 成功但 drop_target_register 抛错。
"""
import tkinterdnd2
from pathlib import Path

# 找到 tkinterdnd2 自带的 Tcl 目录（内含 tkdnd 扩展）
_TKDND_DIR = Path(tkinterdnd2.__file__).parent / "tkdnd"
if not _TKDND_DIR.is_dir():
    raise SystemExit(f"找不到 tkinterdnd2 的 tkdnd 目录：{_TKDND_DIR}")

_datas = [(_TKDND_DIR, "tkinterdnd2/tkdnd")]

block_cipher = None

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=_datas,
    # 关键：tkinterdnd2 必须显式声明，否则打包后拖放会失效
    hiddenimports=["tkinterdnd2", "tkinterdnd2.tkdnd"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["numpy", "PIL", "matplotlib", "pytest", "setuptools", "pip"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="记录仪视频转码工具",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # 无控制台窗口
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)