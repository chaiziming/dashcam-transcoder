# 记录仪视频转码工具 · dashcam-transcoder

把行车记录仪的 `.h264` 裸流转成可播放的 `mp4`，并遮挡 VIN 等隐私信息。

原本每次都要手敲三条 ffmpeg 命令，这个工具把它变成点几下鼠标。

![Windows](https://img.shields.io/badge/Windows-0078D4?logo=windows&logoColor=white)
![Python](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)
![License](https://img.shields.io/badge/license-MIT-green)

## 能做什么

- **转码**：`.h264` 裸流 → `mp4`，`-pix_fmt yuv420p` 保证微信/网页/播放器里不黑屏
- **一步到位**：转码与遮挡合成一条 ffmpeg 命令，不用中间文件，比两步快一倍
- **三种打码效果**：纯色遮挡 / 马赛克 / 高斯模糊，强度可调
- **播放预览**：处理前先看视频，▶ 播放 / ■ 停止归零，可快进快退、拖拽进度条、调倍速
- **合并分段视频**：行车记录仪分段录制，同一起事故被切成几个文件时可按顺序合并成一个
- **可视化框选**：在画面上拖动鼠标框出车牌等区域，坐标自动换算
- **隐私提醒**：内置 VIN 固定位置自动遮挡；画面里其他车牌需用户自行框选
- **可选时间段**：只导出需要的片段（如前 1 分钟）

## 截图

界面布局：左侧预览画面 + 播放控制条，右侧文件信息、遮挡设置、时间段、输出设置。

## 快速开始

### 直接用（推荐）

从 [Releases](https://github.com/chaiziming/dashcam-transcoder/releases) 下载
`dashcam-transcoder-x.y.z-setup.exe`，双击安装：

- 向导式安装界面，可自选安装目录
- 自动创建开始菜单和桌面快捷方式
- 内置 ffmpeg，**装完即用，无需另行安装**
- 可从「控制面板 → 应用和功能」正常卸载

安装包约 125 MB（ffmpeg 本体 208 MB，lzma2 压缩后大幅缩小）。

> 也可以跳过安装直接用绿色版：解压即跑。
> 绿色版需要系统已装 ffmpeg，或把 `ffmpeg.exe`/`ffprobe.exe` 放到程序同级 `ffmpeg/` 目录。

### 本地构建安装包（开发用）

**改完代码想马上试？不用等 GitHub Actions 跑完再下载。**

**最简单：双击 `build.bat`。** 它会自动找一个能用的 Python（含项目自带的 `.venv`），
缺什么依赖自动装。

首次使用建议用 [uv](https://docs.astral.sh/uv/) 建环境 —— 它把 Python
装在项目内 `.venv/`，**完全不碰系统 PATH**：

```bash
winget install --id astral-sh.uv   # 装 uv（只装一次）
uv sync                            # 建 .venv 并装依赖，约 1 分钟
```

之后日常命令：

```bash
python build_local.py             # 完整构建，约 2 分钟
python build_local.py --skip-exe  # 只改界面时代：复用已有 exe，约 100 秒
python build_local.py --version 1.2.3
python build_local.py --skip-inno  # 没装 Inno Setup 时只出免安装版
python build_local.py --clean      # 先清干净再构建

uv run python build_local.py       # 或者直接用 uv 跑（会自动准备环境）
```

产物在 `dist_installer/`：

- `dashcam-transcoder-<版本>-setup.exe` —— 与 GitHub Releases **完全一致**的安装包
- `dashcam-transcoder-<版本>-portable/` —— 免安装目录，解压即用

脚本会**自动复用本机已装的 ffmpeg**（`C:\Program Files\ffmpeg\bin` 等），
不用每次下载 100MB+。版本号默认取最近的 git tag，也可以用 `--version` 指定。

### 从源码运行

```bash
git clone https://github.com/chaiziming/dashcam-transcoder.git
cd dashcam-transcoder
pip install tkinterdnd2        # 文件拖放功能（可选，缺失时自动降级）

python main.py
```

### 自己构建安装包

需要 [Inno Setup 6](https://jrsoftware.org/isdl.php)，完整步骤见
[installer/build_installer.md](installer/build_installer.md)。

```bash
pip install pyinstaller
python -m PyInstaller build_exe.spec --clean --noconfirm
# 准备 build_assets/（exe + ffmpeg），再编译 .iss 脚本
```

## 使用方法

1. 双击 exe，或把 `.h264` 拖进窗口
2. 等几秒生成低清预览（首次约 8 秒，第二次打开同一文件秒开）
3. 播放到要遮挡的位置 → **在画面上拖动鼠标框选**（右键点框删除，拖动可调整）
4. 点「开始处理」

快捷键：`空格` 播放/暂停，`←/→` ±5 秒，`Ctrl+←/→` 或 `PageUp/Down` ±30 秒，`Home/End` 首尾。

## 相比手工 ffmpeg 命令改进了什么

| 项 | 手工命令 | 本工具 |
|---|---|---|
| 流程 | 两步（先转码再打码） | 一步，快一倍 |
| 遮挡 | 马赛克（缩小再放大） | 纯色 / 马赛克 / 模糊 三选一 |
| 裸流格式 | 靠扩展名猜 | 显式 `-f h264`，裸流不会失败 |
| 音频参数 | 硬加 `-c:a aac` | 无音轨时自动 `-an`（不少裸流没有音轨，加了会报错） |
| 画质 | 未指定（实际 ultrafast+CRF23，偏糊） | CRF21 + preset slow |
| 兼容性 | 未指定 | `-pix_fmt yuv420p`，多平台不黑屏 |
| 遮挡范围 | 手算坐标 | 可视化框选，自动按分辨率等比换算 |

## 安全提醒

**VIN 打码只挡住你自己车的车架号。** 行车记录仪画面里往往还有**其他车辆的车牌**，
这些是可识别信息。提交举报材料前，请务必在预览画面上把车牌框住。

> 车牌会随车辆移动，不同时间点位置不同。需要定位时可拖动进度条到车辆入画的那一秒再框。

## 已知限制

- 裸流没有容器索引，`ffprobe` 读不到总时长。程序通过统计帧数推算（只解复用不解码，很快）
- 生成预览需要完整解码一遍原视频，长视频请耐心等待
- 预览分辨率固定 640×360（这是流畅播放的实测最优值），但遮挡坐标始终按原视频分辨率记录

## 项目结构

```
.
├── main.py                    程序入口（含 VIN_PROBE 自检通道）
├── build_exe.spec             PyInstaller 打包配置
├── installer/
│   ├── dashcam_transcoder.iss   Inno Setup 安装脚本
│   ├── build_installer.md       构建安装包的完整步骤
│   └── make_icon.py             生成应用图标（纯标准库，无外部依赖）
├── app/
│   ├── main.py                 GUI 界面 + 播放调度
│   ├── player.py               代理预览生成 + PPM 帧管道
│   ├── player_bar.py           播放控制条 + 可拖拽进度条
│   ├── packager.py             转码命令生成 + 进度解析
│   ├── mask.py                 遮挡模型 + 三种效果的滤镜生成
│   ├── progress_bar.py         Win98 风格分段进度条
│   ├── ffmpeg_env.py           ffmpeg 定位 + 视频探测
│   └── config.py               配置持久化
└── tests/
    ├── sample_helper.py        测试样本定位（见下）
    ├── test_core.py            核心逻辑
    ├── test_e2e_real.py        端到端 + 像素级遮挡验证
    ├── test_gui_smoke.py       GUI 冒烟 + 布局重叠检查
    ├── test_player.py          播放 / seek / 快进 / 倍速
    └── test_idle_state.py      空闲状态界面检查
```

## 跑测试

```bash
python -m tests.test_core
python -m tests.test_e2e_real
python -m tests.test_gui_smoke
python -m tests.test_player
python -m tests.test_idle_state
```

部分测试需要**一段真实的 `.h264` 样本**才能完整执行（用于验证裸流探测、
像素级遮挡效果、播放管道等）。仓库不含任何视频样本 —— 行车记录仪 footage
属于隐私数据，绝不入库。

运行需要样本的测试时，二选一：

```bash
# 方式一：环境变量
set VIN_TEST_VIDEO=D:\videos\你的视频.h264
python -m tests.test_e2e_real

# 方式二：在项目根目录放一个 sample.h264
```

未提供样本时，相关用例会明确提示并跳过，不会让整个测试失败。

## 验证过的遮挡效果

以 1920×1080 的真实 h264 为样本，用像素亮度标准差衡量纹理丰富度：

| 区域 | 原始帧 | 输出帧 | 结论 |
|---|---|---|---|
| VIN | 89.66 | 0.68 | 下降 99.2%，完全不可读 |
| 车牌 | 12.37 | 0.02 | 完全遮挡 |
| 其他画面 | 25.28 | 20.78 | 完好，未误涂 |

## License

MIT