"""共享常量与颜色定义。

原先这些散落在 main.py 顶部，随着代码拆分被多个模块引用，
集中到这里避免「从 main.py import 常量」这种反向依赖
（main.py 依赖 canvas.py，canvas.py 又依赖 main.py → 循环）。
"""
from __future__ import annotations

# ---- 颜色 ----
CARD = "#ffffff"
BG = "#f4f6f8"
FG = "#1f2933"
MUTED = "#6b7785"
BORDER = "#d7dee7"
ACCENT = "#2f6feb"
ACCENT_DIM = "#9db8e8"
DANGER = "#d64545"
OK_GREEN = "#1f9d55"

# ---- 时间轴坐标基准（默认按 1920x1080 预设，用户可在config 覆盖）----
REF_W = 1920
REF_H = 1080
# 常见 VIN 位置（右下角）
VIN_PRESET_BOX = (1459, 1018, 344, 20)