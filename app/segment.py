"""一段待合并视频的数据模型。

配合「N 个独立画布」的布局：每个画布对应一个 Segment，
段与段之间共享同一套遮挡框（见 app.mask / app.canvas 的说明）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .ffmpeg_env import VideoInfo


@dataclass
class Segment:
    """一个待合并段（= 一个独立画布）。

    字段说明：
      file     该段的视频文件；None 表示这个画布还空着（等拖文件进来）
      info     视频信息（探测后填充）
      position 该段自己的播放位置（秒）——切段时各自保留进度
      proxy    该段的代理预览文件（首次播放该段时才生成，之后复用）
      thumb    该段的首帧静帧 PNG（非激活时显示的就是它）
    """

    index: int
    file: Path | None = None
    info: VideoInfo | None = None
    position: float = 0.0
    proxy: Path | None = None
    thumb: Path | None = None
    proxy_pending: bool = False

    @property
    def filled(self) -> bool:
        return self.file is not None
