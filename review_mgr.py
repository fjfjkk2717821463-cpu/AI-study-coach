import json
import os
import time

import app_paths

REVIEWS_FILE = os.path.join(app_paths.get_data_dir(), "review_schedule.json")
INTERVALS_DAYS = [1, 3, 7, 14, 30]
DAY_SECONDS = 24 * 3600


def _load():
    try:
        with open(REVIEWS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save(data):
    try:
        with open(REVIEWS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def _key(book_name, chapter_title):
    return f"{book_name}\x1f{chapter_title}"


def get_entry(book_name, chapter_title):
    return _load().get(_key(book_name, chapter_title))


def ensure_entry(book_name, chapter_title):
    """为某章节建立复习计划；已存在则原样返回。"""
    data = _load()
    key = _key(book_name, chapter_title)
    if key in data:
        return data[key]
    entry = {
        "book_name": book_name,
        "chapter_title": chapter_title,
        "stage": 0,
        "review_count": 0,
        "last_reviewed_at": None,
        "next_review_at": int(time.time()) + INTERVALS_DAYS[0] * DAY_SECONDS,
    }
    data[key] = entry
    _save(data)
    return entry


def record_review(book_name, chapter_title, quality):
    """记录一次复习。quality: 1=忘了 2=模糊 3=掌握。"""
    entry = ensure_entry(book_name, chapter_title)
    if quality == 3:
        entry["stage"] = min(entry.get("stage", 0) + 1, len(INTERVALS_DAYS) - 1)
    elif quality == 1:
        entry["stage"] = 0
    else:
        entry["stage"] = max(entry.get("stage", 0) - 1, 0)

    entry["review_count"] = entry.get("review_count", 0) + 1
    entry["last_reviewed_at"] = int(time.time())
    entry["next_review_at"] = (
        int(time.time()) + INTERVALS_DAYS[entry["stage"]] * DAY_SECONDS
    )

    data = _load()
    data[_key(book_name, chapter_title)] = entry
    _save(data)
    return entry


def due_entries(now=None):
    """返回所有到期待复习的章节。"""
    now = now if now is not None else int(time.time())
    items = []
    for entry in _load().values():
        if entry.get("next_review_at", 0) <= now:
            items.append(entry)
    items.sort(key=lambda item: item.get("next_review_at", 0))
    return items
