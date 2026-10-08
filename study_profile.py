# study_profile.py
"""个人学习版的数据准备：导入示例讲义、沿用已有的 API Key。

源码运行和打包后的应用共用这一份逻辑，可以重复执行（已导入的不会重复添加）。
"""

import json
import os
import shutil
import sys


def base_dir():
    """返回资源根目录：源码运行时是项目目录，打包后是应用内部目录。"""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


def corpus_dir():
    return os.path.join(base_dir(), "语料库示例")


SKIP_FILES = {"语料库统计报告.md", "语料库说明.md"}
SUPPORTED = (".md", ".markdown", ".txt")


def prepare(data_dir, default_data_dir=None, quiet=False):
    """把示例讲义放进书架；如果还没有 API Key，就从原应用复制一份。

    返回 (新增讲义列表, 是否复制了密钥)。
    """
    books_dir = os.path.join(data_dir, "books")
    os.makedirs(books_dir, exist_ok=True)

    shelf_path = os.path.join(data_dir, "my_bookshelf.json")
    try:
        with open(shelf_path, "r", encoding="utf-8") as handle:
            shelf = json.load(handle)
        if not isinstance(shelf, list):
            shelf = []
    except (OSError, json.JSONDecodeError):
        shelf = []

    existing_paths = {
        item.get("path") for item in shelf if isinstance(item, dict) and item.get("path")
    }
    added = []
    source_dir = corpus_dir()
    if os.path.isdir(source_dir):
        for name in sorted(os.listdir(source_dir)):
            if not name.lower().endswith(SUPPORTED) or name in SKIP_FILES:
                continue
            source = os.path.join(source_dir, name)
            target = os.path.join(books_dir, name)
            try:
                if not os.path.exists(target):
                    shutil.copy2(source, target)
            except OSError:
                continue
            if target in existing_paths:
                continue
            shelf.append({"name": os.path.splitext(name)[0], "path": target})
            existing_paths.add(target)
            added.append(name)

    if added:
        try:
            with open(shelf_path, "w", encoding="utf-8") as handle:
                json.dump(shelf, handle, ensure_ascii=False, indent=2)
        except OSError:
            pass

    key_copied = False
    study_env = os.path.join(data_dir, ".env")
    if not os.path.exists(study_env) and default_data_dir:
        source_env = os.path.join(default_data_dir, ".env")
        if os.path.exists(source_env):
            try:
                shutil.copy2(source_env, study_env)
                key_copied = True
            except OSError:
                pass

    if not quiet:
        print("学习版数据目录：", data_dir)
        print("新增讲义：", "、".join(added) if added else "无（已经导入过）")
        print("书架共", len(shelf), "份讲义")
        if key_copied:
            print("已沿用你在原应用里配置的 API Key（只复制到学习版数据目录）")
    return added, key_copied


def main():
    import app_paths

    default_dir = os.path.join(
        os.path.expanduser("~/Library/Application Support"), "AiStudyCoach"
    )
    prepare(app_paths.get_data_dir(), default_data_dir=default_dir)


if __name__ == "__main__":
    main()
