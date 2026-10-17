# 构建安装包

## 前置条件

1. **Inno Setup 6**（免费）
   - 下载：https://jrsoftware.org/isdl.php
   - 或 `winget install --id JRSoftware.InnoSetup -e`

2. **ffmpeg.exe / ffprobe.exe**
   - 从 https://www.gyan.dev/ffmpeg/builds/ 下载，解压后取出这两个文件

## 步骤

```bash
# 1) 打包 exe
pip install pyinstaller
python -m PyInstaller build_exe.spec --clean --noconfirm
cp dist/记录仪视频转码工具.exe build_assets/

# 2) 放置 ffmpeg
mkdir -p build_assets/ffmpeg
cp /path/to/ffmpeg/bin/ffmpeg.exe  build_assets/ffmpeg/
cp /path/to/ffmpeg/bin/ffprobe.exe build_assets/ffmpeg/

# 3) 生成图标（可选，纯标准库实现）
python installer/make_icon.py

# 4) 编译安装包
"C:/Program Files (x86)/Inno Setup 6/ISCC.exe" installer\dashcam_transcoder.iss

# 产物
dist_installer\dashcam-transcoder-1.0.0-setup.exe
```

## 注意

`installer/dashcam_transcoder.iss` 里的 `BuildAssets` 与 `OutputDir` 是**绝对路径**，
换机器构建时需改成你自己的路径。

`.iss` 文件必须是 **UTF-8 with BOM** 编码，否则 Inno Setup 无法正确解析中文。

调试编译问题可在命令后加 `/Qp`，它会输出带行号的详细错误；
不加这个参数只会显示「系统找不到指定的文件」，很难定位。
