# app_paths.py
import os
import shutil
import sys

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR_NAME = "AiStudyCoach"


def get_data_dir():
    """返回适合持久化用户数据的目录，兼容 macOS / Windows / Linux。"""
    if sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    elif os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser(
            "~/.local/share"
        )

    data_dir = os.path.join(base, DATA_DIR_NAME)
    os.makedirs(data_dir, exist_ok=True)
    return data_dir


def migrate_file(name):
    """把旧版程序目录里的文件迁移到用户数据目录，并返回新路径。"""
    legacy = os.path.join(APP_DIR, name)
    target = os.path.join(get_data_dir(), name)
    if os.path.exists(legacy) and not os.path.exists(target):
        try:
            shutil.copy2(legacy, target)
        except OSError:
            pass
    return target


def migrate_dir(name):
    """把旧版程序目录里的文件夹迁移到用户数据目录，并返回新路径。"""
    legacy = os.path.join(APP_DIR, name)
    target = os.path.join(get_data_dir(), name)
    if os.path.isdir(legacy) and not os.path.exists(target):
        try:
            shutil.copytree(legacy, target)
        except OSError:
            pass
    return target
