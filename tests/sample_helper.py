"""测试用的样本视频定位。

这些测试需要一段真实的 .h264 才能跑完（要验证裸流探测、像素级遮挡效果、
播放管道等）。但仓库里不该提交任何用户的行车记录仪视频 —— 那是隐私数据。

所以按以下优先级找样本：

1. 环境变量 ``VIN_TEST_VIDEO`` 指向的文件（测试你自己的视频时用它）
2. 当前目录及上级目录下的 sample.h264（你放一个样本进来即可）
3. 找不到时：相关用例 **跳过**并说明原因，而不是让测试整体失败

用法示例（Windows）::

    set VIN_TEST_VIDEO=D:\videos\2026-01-29_204850_0180.h264
    python -m tests.test_e2e_real
"""
from __future__ import annotations

import os
from pathlib import Path

ENV_VAR = "VIN_TEST_VIDEO"
SAMPLE_NAMES = ("sample.h264", "sample.mp4")


def find_sample() -> Path | None:
    """定位测试样本视频；找不到返回 None。"""
    env = os.environ.get(ENV_VAR, "").strip().strip('"')
    if env:
        p = Path(env)
        if p.is_file():
            return p

    here = Path(__file__).resolve().parent
    for base in (here, *here.parents):
        for name in SAMPLE_NAMES:
            p = base / name
            if p.is_file():
                return p
    return None


def require_sample() -> Path | None:
    """定位样本；找不到时打印提示（由调用方决定跳过）。"""
    p = find_sample()
    if p is None:
        print(f"  [跳过] 未找到测试样本视频。\n"
              f"         用法：设置环境变量 {ENV_VAR} 指向一个 .h264 文件，\n"
              f"         或在项目根目录放一个 sample.h264。")
    return p