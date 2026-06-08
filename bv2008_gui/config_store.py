from __future__ import annotations

import json
from pathlib import Path


CONFIG_PATH = Path.home() / ".bv2008_config.json"
CONFIG_KEYS = ("token", "activity_id", "post_id", "org_id")


def empty_config() -> dict[str, str]:
    return {key: "" for key in CONFIG_KEYS}


def load_config() -> dict[str, str]:
    cfg = empty_config()
    if not CONFIG_PATH.exists():
        return cfg
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return cfg
    for key in CONFIG_KEYS:
        cfg[key] = str(data.get(key, ""))
    return cfg


def save_config(cfg: dict[str, str]) -> None:
    data = empty_config()
    data.update({key: str(cfg.get(key, "")) for key in CONFIG_KEYS})
    CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

