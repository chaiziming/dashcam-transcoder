"""时间字符串与秒数互转。

从 main.py 抽出来的独立模块：原先 hhmmss / parse_hhmmss 定义在
main.py 顶部，被 UI、packager、player 多处使用，又和窗口构建代码混在一起，
既不好复用也不好测试。

统一在这里定义：
- format_hhmmss(sec)  -> "HH:MM:SS"
- parse_hhmmss(text)  -> 秒（float），空串返回 None
- hhmmss(sec)         -> 短格式 "H:MM:SS" 或 "MM:SS"（进度条用）
"""
from __future__ import annotations


def format_hhmmss(sec: float | None) -> str:
    """秒 -> HH:MM:SS（不足一小时也会补成 00:MM:SS）。"""
    if sec is None or sec < 0:
        return "00:00:00"
    total = int(round(float(sec)))
    return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"


def parse_hhmmss(text: str) -> float | None:
    """解析 HH:MM:SS / MM:SS / 纯秒数。空串返回 None（表示不限制）。

    容忍点号、空格等常见分隔符写法，因为用户更可能打成 00.01.00。
    """
    raw = (text or "").strip()
    if not raw:
        return None
    for sep in ("：", " "):
        raw = raw.replace(sep, ":")
    raw = raw.replace(".", ":")
    parts = raw.split(":")
    if len(parts) > 3:
        raise ValueError(f"时间格式错误：{text}（应为 HH:MM:SS）")
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        raise ValueError(f"时间格式错误：{text}（应为 HH:MM:SS）")
    total = 0.0
    for n in nums:
        total = total * 60 + n
    return total


def hhmmss(sec: float | None) -> str:
    """秒 -> HH:MM:SS（固定两位数小时）。

    注意与 player_bar.hhmmss_ms 的区别：那个是短格式（H:MM:SS 或 MM:SS），
    用于进度条这种空间紧张的地方；这个是完整格式，用于日志与文件信息展示。
    """
    if sec is None or sec < 0:
        return "--:--:--"
    sec = int(round(float(sec)))
    return f"{sec // 3600:02d}:{(sec % 3600) // 60:02d}:{sec % 60:02d}"


def hhmmss_short(sec: float | None) -> str:
    """秒 -> H:MM:SS 或 MM:SS（短格式，进度条/时间标签用）。"""
    if sec is None or sec < 0:
        return "--:--"
    sec = int(round(float(sec)))
    m, s = divmod(sec, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def clamp_range(start: float | None, end: float | None,
                duration: float | None) -> tuple[float | None, float | None]:
    """把开始/结束时间夹到合法范围，并保证 start <= end。"""
    if duration is not None and duration > 0:
        cap = max(0.0, duration - 0.05)
        if start is not None:
            start = max(0.0, min(start, cap))
        if end is not None:
            end = max(0.0, min(end, cap))
    else:
        if start is not None:
            start = max(0.0, start)
        if end is not None:
            end = max(0.0, end)
    if start is not None and end is not None and end < start:
        end = start
    return start, end