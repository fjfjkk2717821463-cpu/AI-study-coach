# session_mgr.py
import json
import os
import re
import time

import app_paths

SESSIONS_DIR = app_paths.migrate_dir("sessions")


def _sanitize_filename(name):
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", name or "")
    name = name.strip(" ._")
    return name[:60] or "untitled"


def new_session_path(mode, subject):
    """生成一个新会话文件的路径（不写文件）。"""
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    safe_subject = _sanitize_filename(subject)
    filename = f"{mode}_{safe_subject}_{timestamp}.json"
    return os.path.join(SESSIONS_DIR, filename)


def write_session(path, mode, subject, history, meta=None):
    """把会话内容写入指定路径，用于自动保存和覆盖更新。"""
    if not history:
        return ""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "mode": mode,
                "subject": subject,
                "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "history": history,
                "meta": meta or {},
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    return path


def save_session(mode, subject, history, meta=None):
    """把本次学习对话保存为新 JSON 记录，避免退出后历史丢失。"""
    if not history:
        return ""
    path = new_session_path(mode, subject)
    return write_session(path, mode, subject, history, meta)


def list_sessions():
    """列出已保存的学习会话，按保存时间倒序。"""
    if not os.path.isdir(SESSIONS_DIR):
        return []

    sessions = []
    for filename in os.listdir(SESSIONS_DIR):
        if not filename.endswith(".json"):
            continue
        path = os.path.join(SESSIONS_DIR, filename)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue

        sessions.append(
            {
                "id": path,
                "filename": filename,
                "mode": data.get("mode", ""),
                "subject": data.get("subject", ""),
                "saved_at": data.get("saved_at", ""),
                "message_count": len(data.get("history", [])),
            }
        )

    sessions.sort(key=lambda item: item.get("saved_at", ""), reverse=True)
    return sessions


def load_session(path):
    """读取单个会话文件；不存在或损坏时返回 None。"""
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def search_sessions(query):
    """按标题、模式或文件名过滤会话列表。"""
    query = (query or "").strip().lower()
    sessions = list_sessions()
    if not query:
        return sessions
    return [
        item
        for item in sessions
        if query in item.get("subject", "").lower()
        or query in item.get("mode", "").lower()
        or query in item.get("filename", "").lower()
    ]


def rename_session(path, new_subject):
    """重命名会话标题，保留原内容与元信息。"""
    new_subject = (new_subject or "").strip()
    if not new_subject:
        return False
    data = load_session(path)
    if not data:
        return False
    data["subject"] = new_subject
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except OSError:
        return False


def delete_session(path):
    """删除会话文件；调用方需先校验路径在会话目录内。"""
    try:
        os.remove(path)
        return True
    except OSError:
        return False
