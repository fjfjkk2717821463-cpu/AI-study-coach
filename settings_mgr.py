import json
import os

import app_paths

SETTINGS_FILE = os.path.join(app_paths.get_data_dir(), "app_settings.json")
DEFAULTS = {"spaced_review": False, "price_per_mtok": 0.0}


def load_settings():
    """读取应用设置，缺失或损坏时返回默认值。"""
    settings = dict(DEFAULTS)
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            saved = json.load(f)
            if isinstance(saved, dict):
                if isinstance(saved.get("spaced_review"), bool):
                    settings["spaced_review"] = saved["spaced_review"]
                try:
                    price = float(saved.get("price_per_mtok") or 0)
                    if price >= 0:
                        settings["price_per_mtok"] = price
                except (TypeError, ValueError):
                    pass
    except (OSError, json.JSONDecodeError):
        pass
    return settings


def save_settings(settings):
    """保存应用设置。"""
    data = {
        "spaced_review": bool(settings.get("spaced_review", DEFAULTS["spaced_review"])),
        "price_per_mtok": settings.get("price_per_mtok", DEFAULTS["price_per_mtok"]),
    }
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except OSError:
        return False
