# bookshelf_mgr.py
import json
import os

import app_paths

SHELF_FILE = app_paths.migrate_file("my_bookshelf.json")

def load_shelf():
    """加载书架数据，返回列表"""
    if not os.path.exists(SHELF_FILE):
        return []
    with open(SHELF_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_shelf(shelf):
    """保存书架数据"""
    with open(SHELF_FILE, 'w', encoding='utf-8') as f:
        json.dump(shelf, f, ensure_ascii=False, indent=2)

def add_book(book_name, book_path):
    """向书架添加一本书，避免重复"""
    shelf = load_shelf()
    for book in shelf:
        if book['path'] == book_path:
            return shelf  # 已存在
    shelf.append({"name": book_name, "path": book_path})
    save_shelf(shelf)
    return shelf

def remove_book(book_path):
    """从书架移除指定书籍"""
    shelf = load_shelf()
    shelf = [b for b in shelf if b['path'] != book_path]
    save_shelf(shelf)
    return shelf

def get_book_by_index(shelf, index):
    """根据序号获取书籍信息"""
    if 0 <= index < len(shelf):
        return shelf[index]
    return None
