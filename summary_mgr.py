# summary_mgr.py
import json
import os
import re
import time

import app_paths

SUMMARIES_DIR = app_paths.migrate_dir("summaries")


def _sanitize_filename(name):
    """清理非法文件名字符并限制长度，避免跨平台路径问题。"""
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", name or "")
    name = name.strip(" ._")
    return name[:80] or "untitled"


def get_summary_path(book_name, chapter_title):
    """生成基于书名和章节的唯一文件名。"""
    safe_book = _sanitize_filename(book_name)
    safe_chapter = _sanitize_filename(chapter_title)
    return os.path.join(SUMMARIES_DIR, f"{safe_book}_{safe_chapter}.json")


def save_summary(book_name, chapter_title, summary_text):
    """保存总结到本地，并附带更新时间。"""
    path = get_summary_path(book_name, chapter_title)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "summary": summary_text,
                "book_name": book_name,
                "chapter_title": chapter_title,
                "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            },
            f,
            ensure_ascii=False,
            indent=2,
        )


def load_summary(book_name, chapter_title):
    """读取总结，若无则返回空字符串。兼容旧版只保存 summary 的格式。"""
    path = get_summary_path(book_name, chapter_title)
    if not os.path.exists(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return ""
    return data.get("summary", "") if isinstance(data, dict) else ""


def list_summaries():
    """读取全部学习总结，并按书籍名称分组返回。"""
    if not os.path.isdir(SUMMARIES_DIR):
        return []

    grouped = {}
    for filename in os.listdir(SUMMARIES_DIR):
        if not filename.endswith(".json"):
            continue
        path = os.path.join(SUMMARIES_DIR, filename)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue

        if not isinstance(data, dict):
            continue

        book_name = data.get("book_name") or "未分类"
        item = {
            "chapter_title": data.get("chapter_title") or filename,
            "summary": data.get("summary") or "",
            "updated_at": data.get("updated_at") or "",
        }
        grouped.setdefault(book_name, []).append(item)

    result = []
    for book_name in sorted(grouped):
        summaries = sorted(
            grouped[book_name],
            key=lambda item: item.get("updated_at", ""),
            reverse=True,
        )
        result.append({"book_name": book_name, "summaries": summaries})
    return result
