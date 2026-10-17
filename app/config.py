"""配置持久化：把预设坐标、CRF、最近输出目录等存到 exe 同级 config.json。"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def _app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


CONFIG_PATH = _app_dir() / "config.json"

DEFAULTS = {
    "crf": 21,
    "preset": "slow",
    "use_vin_preset": True,
    "last_dir": "",
    "auto_open_dir": True,
    "preview_ts": 5.0,
    "vin_preset_1920x1080": [1459, 1018, 344, 20],
    # 打码效果：solid / mosaic / blur 及其强度
    "effect": "solid",
    "intensity": 21,
}


def load() -> dict:
    cfg = dict(DEFAULTS)
    try:
        if CONFIG_PATH.is_file():
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        pass
    return cfg


def save(cfg: dict) -> None:
    try:
        CONFIG_PATH.write_text(
            json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        pass
