# book_utils.py
import os
import re

import ebooklib
import PyPDF2
import pdfplumber
from bs4 import BeautifulSoup, NavigableString, Tag
from charset_normalizer import from_bytes
from ebooklib import epub
from markdownify import markdownify as html_to_md

MAX_BOOK_BYTES = 50 * 1024 * 1024


def _check_size(book_path):
    try:
        return os.path.getsize(book_path) <= MAX_BOOK_BYTES
    except OSError:
        return False


def _read_text_file(book_path):
    """检测编码后读取文本文件，避免 gb18030 兜底产生静默乱码。"""
    try:
        with open(book_path, "rb") as f:
            data = f.read()
    except OSError as exc:
        return None, str(exc)
    if not data:
        return "", None

    best = from_bytes(data).best()
    if best is not None:
        try:
            return str(best), None
        except Exception:
            pass

    for encoding in ("utf-8-sig", "utf-8", "gb18030", "big5"):
        try:
            return data.decode(encoding), None
        except UnicodeDecodeError:
            continue
    return None, "无法识别文件编码"


def _html_to_markdown(html):
    """把 HTML 转成保留标题、列表、表格和引用的 Markdown。"""
    try:
        text = html_to_md(
            str(html),
            heading_style="ATX",
            bullets="-",
            strip=["script", "style", "nav", "footer", "aside"],
        )
    except Exception:
        return ""
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _pdf_to_markdown(book_path):
    """尝试用 pymupdf4llm 把 PDF 转成 Markdown；不可用时返回 None。"""
    try:
        import pymupdf4llm

        return pymupdf4llm.to_markdown(book_path)
    except Exception:
        return None


def _extract_pdf_text(book_path):
    """优先 pymupdf4llm 转 Markdown，失败时降级 pdfplumber/PyPDF2。"""
    markdown_text = _pdf_to_markdown(book_path)
    if markdown_text and markdown_text.strip():
        return markdown_text, None

    text = ""
    try:
        with pdfplumber.open(book_path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
        if text.strip():
            return text, None
    except Exception:
        text = ""

    try:
        with open(book_path, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            for page in reader.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
    except Exception as exc:
        return None, f"PDF 解析失败：{exc}"

    if not text.strip():
        return None, "未能从 PDF 提取到文本，可能是扫描版 PDF，暂不支持 OCR。"
    return text, None


def _extract_epub_text(book_path):
    """读取 EPUB 电子书，按书脊顺序提取各章节 Markdown。"""
    try:
        book = epub.read_epub(book_path)
    except Exception as exc:
        return None, f"EPUB 解析失败：{exc}"

    spine_items = []
    for idref, _ in getattr(book, "spine", []) or []:
        item = book.get_item_with_id(idref)
        if item is not None:
            spine_items.append(item)

    if not spine_items:
        spine_items = list(book.get_items_of_type(ebooklib.ITEM_DOCUMENT))

    parts = []
    for item in spine_items:
        if item.get_type() != ebooklib.ITEM_DOCUMENT:
            continue
        if hasattr(item, "is_chapter") and not item.is_chapter():
            continue
        try:
            soup = BeautifulSoup(item.get_content(), "html.parser")
        except Exception:
            continue

        body = soup.find("body") or soup
        text = _html_to_markdown(body)
        if not text:
            continue
        parts.append(text)

    if not parts:
        return None, "未能从 EPUB 提取到文本内容。"
    return "\n\n".join(parts), None


def _flatten_toc(toc, result=None, depth=0):
    """把 EPUB 嵌套目录拍平成按阅读顺序排列的条目列表。"""
    if result is None:
        result = []
    for entry in toc or []:
        if isinstance(entry, tuple) and len(entry) == 2:
            section, children = entry
            if isinstance(section, (epub.Link, epub.Section)):
                result.append(
                    {
                        "title": (section.title or "").strip(),
                        "href": (section.href or "").strip(),
                        "depth": depth,
                    }
                )
            _flatten_toc(children, result, depth + 1)
        elif isinstance(entry, (epub.Link, epub.Section)):
            result.append(
                {
                    "title": (entry.title or "").strip(),
                    "href": (entry.href or "").strip(),
                    "depth": depth,
                }
            )
    return result


def _split_href(href):
    """把目录链接拆成文档路径和锚点两部分。"""
    href = (href or "").strip()
    anchor = ""
    if "#" in href:
        href, anchor = href.split("#", 1)
    href = href.replace("../", "").replace("./", "").lstrip("/")
    return href, anchor


def _find_item_by_name(book, doc_key):
    """按文件名匹配 EPUB 文档条目。"""
    target = (doc_key or "").replace("\\", "/").split("/")[-1].lower()
    if not target:
        return None
    for item in book.get_items():
        if item.get_type() != ebooklib.ITEM_DOCUMENT:
            continue
        name = (item.get_name() or "").replace("\\", "/").split("/")[-1].lower()
        if name == target:
            return item
    return None


def _parse_item(item):
    try:
        return BeautifulSoup(item.get_content(), "html.parser")
    except Exception:
        return None


def _element_text(element):
    return re.sub(r"\n{3,}", "\n\n", element.get_text("\n")).strip()


def _first_heading(soup):
    body = soup.find("body") or soup
    for tag in body.find_all(["h1", "h2", "h3"]):
        text = tag.get_text(" ", strip=True)
        if text:
            return text
    return ""


def _split_body_by_headings(body):
    """把单个 EPUB 文档按 h1-h3 标题切分成多个小节（递归遍历，兼容嵌套标签）。"""
    sections = []
    current_title = ""
    current_parts = []

    def flush():
        nonlocal current_title, current_parts
        if current_parts:
            content = re.sub(r"\n{3,}", "\n\n", "\n".join(current_parts)).strip()
            if content:
                sections.append((current_title or "前言/引言", content))
        current_title = ""
        current_parts = []

    def walk(node):
        nonlocal current_title, current_parts
        for child in node.children:
            if isinstance(child, NavigableString):
                text = str(child).strip()
                if text:
                    current_parts.append(text)
            elif isinstance(child, Tag):
                if child.name in ("h1", "h2", "h3"):
                    flush()
                    current_title = child.get_text(" ", strip=True)
                else:
                    walk(child)

    walk(body)
    flush()
    if not sections:
        return [("", _element_text(body))]
    return sections


def _iter_chapter_items(book):
    """按书脊顺序返回正文文档条目，跳过导航页。"""
    items = []
    for idref, _ in getattr(book, "spine", []) or []:
        item = book.get_item_with_id(idref)
        if item is not None:
            items.append(item)

    if not items:
        items = list(book.get_items_of_type(ebooklib.ITEM_DOCUMENT))

    for item in items:
        if item.get_type() != ebooklib.ITEM_DOCUMENT:
            continue
        if hasattr(item, "is_chapter") and not item.is_chapter():
            continue
        yield item


def _group_toc_chapters(entries):
    """按顶层目录条目把子条目合并成大章节。"""
    chapters = []
    current = None
    for entry in entries:
        if entry["depth"] == 0:
            current = {
                "title": entry["title"] or entry["href"] or "未命名章节",
                "refs": [],
            }
            chapters.append(current)
        if current is not None:
            current["refs"].append(entry)
    return chapters


def _collect_doc_markdown(book, refs):
    """收集一组目录条目对应的文档 Markdown，并按阅读顺序去重合并。"""
    parts = []
    seen = set()
    for ref in refs:
        doc_key, _ = _split_href(ref["href"])
        if not doc_key or doc_key in seen:
            continue
        item = _find_item_by_name(book, doc_key)
        if item is None:
            continue
        soup = _parse_item(item)
        if soup is None:
            continue
        body = soup.find("body") or soup
        text = _html_to_markdown(body)
        if text:
            parts.append(text)
        seen.add(doc_key)
    return "\n\n".join(parts)


def _split_by_toc_entries(book, entries):
    """按目录条目切分：一个文档对应一条目录则整篇，多条则按标题细分。"""
    groups = {}
    for entry in entries:
        doc_key, anchor = _split_href(entry["href"])
        groups.setdefault(doc_key, []).append(
            {"title": entry["title"], "anchor": anchor}
        )

    chapters = []
    for doc_key, items in groups.items():
        item = _find_item_by_name(book, doc_key)
        if item is None:
            continue
        soup = _parse_item(item)
        if soup is None:
            continue
        body = soup.find("body") or soup
        markdown_text = _html_to_markdown(body)

        if len(items) == 1:
            title = items[0]["title"] or _first_heading(soup) or doc_key
            chapters.append((title, markdown_text))
            continue

        sections = split_chapters(markdown_text, level="section")
        if len(sections) >= len(items):
            for index, item_entry in enumerate(items):
                heading, content = sections[index]
                chapters.append(
                    (item_entry["title"] or heading or doc_key, content)
                )
        else:
            chapters.append(
                (
                    items[0]["title"] or _first_heading(soup) or doc_key,
                    markdown_text,
                )
            )
    return chapters


def _extract_epub_chapters(book_path, level="auto"):
    """按 EPUB 原始目录结构切分章节，level 支持 auto/chapter/section。"""
    try:
        book = epub.read_epub(book_path)
    except Exception as exc:
        return None, f"EPUB 解析失败：{exc}"

    toc_entries = _flatten_toc(getattr(book, "toc", []) or [])
    chapters = []

    if toc_entries:
        if level == "chapter":
            for group in _group_toc_chapters(toc_entries):
                content = _collect_doc_markdown(book, group["refs"])
                if content:
                    chapters.append((group["title"], content))
        elif level == "section":
            chapters = _split_by_toc_entries(book, toc_entries)
        else:
            top_level = [entry for entry in toc_entries if entry["depth"] == 0]
            if len(top_level) >= 2:
                for group in _group_toc_chapters(toc_entries):
                    content = _collect_doc_markdown(book, group["refs"])
                    if content:
                        chapters.append((group["title"], content))
            else:
                chapters = _split_by_toc_entries(book, toc_entries)

    # 目录不可用或解析失败时，退回按书脊文档切分。
    if not chapters:
        for item in _iter_chapter_items(book):
            soup = _parse_item(item)
            if soup is None:
                continue
            body = soup.find("body") or soup
            text = _html_to_markdown(body)
            if not text:
                continue
            chapters.append(
                (_first_heading(soup) or item.get_name() or "", text)
            )

    if not chapters:
        text, err = _extract_epub_text(book_path)
        if err:
            return None, err
        chapters = split_chapters(text, level=level)

    return chapters, None


def get_book_chapters(book_path, level="auto"):
    """统一入口：EPUB 按目录切分，其它格式按标题正则切分。"""
    if not os.path.exists(book_path):
        return None, "文件不存在"
    if not _check_size(book_path):
        return None, f"文件过大（超过 {MAX_BOOK_BYTES // (1024 * 1024)}MB），暂不支持。"

    if os.path.splitext(book_path)[1].lower() == ".epub":
        chapters, err = _extract_epub_chapters(book_path, level=level)
        if err is not None:
            return None, err
        if chapters:
            return chapters, None

    text, err = load_book(book_path)
    if err:
        return None, err

    chapters = split_chapters(text, level=level)
    if not chapters:
        chapters = [("全文", text)]
    return chapters, None


def load_book(book_path):
    """自动检测格式并提取全部文本，返回 (text, error)。"""
    if not os.path.exists(book_path):
        return None, "文件不存在"
    if not _check_size(book_path):
        return None, f"文件过大（超过 {MAX_BOOK_BYTES // (1024 * 1024)}MB），暂不支持。"

    ext = os.path.splitext(book_path)[1].lower()
    if ext in (".txt", ".md", ".markdown"):
        text, err = _read_text_file(book_path)
        if err:
            return None, err
        if not text.strip():
            return None, "文本文件内容为空。"
        return text, None

    if ext == ".pdf":
        return _extract_pdf_text(book_path)

    if ext == ".epub":
        return _extract_epub_text(book_path)

    return None, f"暂不支持的格式: {ext}，目前支持 .txt、.md、.pdf 和 .epub"


_CN_DIGITS = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}


def _cn_num_to_int(text):
    """把常见中文数字（如“十二”“二十五”）转成整数；无法解析则返回 None。"""
    if not text:
        return None
    if text.isdigit():
        return int(text)

    if text == "十":
        return 10

    total = 0
    section = 0
    number = 0
    for ch in text:
        if ch in _CN_DIGITS:
            number = _CN_DIGITS[ch]
        elif ch == "十":
            section = number if number else 1
            total += section * 10
            number = 0
            section = 0
        else:
            return None
    total += number
    return total if total else None


_MD_HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


_MAJOR_CHAPTER_PATTERN = re.compile(
    r"^\s*(?:"
    r"第\s*[一二三四五六七八九十百零〇\d]+\s*[章部分篇]"
    r"|(?:Chapter|Part)\s*[\dIVXLCDM]+\b"
    r").*",
    re.IGNORECASE,
)

_CHAPTER_PATTERN = re.compile(
    r"^\s*(?:"
    r"第\s*[一二三四五六七八九十百零〇\d]+\s*[章节讲部分篇]"
    r"|(?:Chapter|Part|Unit|Section|Module)\s*[\dIVXLCDM]+\b"
    r").*",
    re.IGNORECASE,
)


def split_chapters(text, level="auto"):
    """
    按 Markdown 标题或常见章节标题分割文本。
    支持：# 到 ###### 标题，以及第X章/节/讲/部分/篇、Chapter/Part/Unit/Section 等。
    level: auto=自动、chapter=大章节、section=小章节。
    返回列表 [(title, content), ...]
    """
    lines = text.splitlines()
    boundaries = []
    for index, line in enumerate(lines):
        heading = _MD_HEADING_PATTERN.match(line.strip())
        if heading:
            boundaries.append(
                (index, len(heading.group(1)), heading.group(2).strip())
            )
            continue
        if _MAJOR_CHAPTER_PATTERN.match(line):
            boundaries.append((index, 1, line.strip()))
            continue
        if _CHAPTER_PATTERN.match(line):
            boundaries.append((index, 2, line.strip()))

    if not boundaries:
        return [("全文", text)] if text.strip() else []

    if level == "chapter":
        chosen = [item for item in boundaries if item[1] <= 1] or boundaries
    elif level == "section":
        chosen = boundaries
    else:
        major = [item for item in boundaries if item[1] <= 1]
        chosen = major if len(major) >= 2 else boundaries

    chapters = []
    current_title = "前言/引言"
    current_content = []
    boundary_map = {item[0]: item for item in chosen}

    for index, line in enumerate(lines):
        if index in boundary_map:
            if current_content:
                chapters.append((current_title, "\n".join(current_content)))
            current_title = boundary_map[index][2]
            current_content = []
        else:
            current_content.append(line)

    if current_content:
        chapters.append((current_title, "\n".join(current_content)))

    return chapters


def _title_contains_number(title, number):
    """判断章节标题是否包含指定数字（兼顾中文和阿拉伯数字）。"""
    variants = {str(number)}
    cn = _number_to_cn(number)
    if cn:
        variants.add(cn)

    for variant in variants:
        if f"第{variant}" in title or f"{variant}" in title:
            return True
    return False


def _number_to_cn(number):
    """把 1-99 的整数转成简单中文数字，用于标题匹配。"""
    if not isinstance(number, int) or number < 1 or number > 99:
        return ""
    if number <= 10:
        mapping = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五",
                   6: "六", 7: "七", 8: "八", 9: "九", 10: "十"}
        return mapping[number]
    tens = number // 10
    ones = number % 10
    result = ""
    if tens > 1:
        result += _number_to_cn(tens)
    result += "十"
    if ones:
        result += _number_to_cn(ones)
    return result


def get_chapter_text(chapters, query):
    """
    按关键词匹配章节标题：先精确匹配，再模糊包含匹配，最后尝试数字匹配。
    返回 (title, content) 或 (None, None)
    """
    query = (query or "").strip()
    if not query:
        return None, None

    query_lower = query.lower()

    for title, content in chapters:
        if query == title:
            return title, content

    for title, content in chapters:
        if query_lower in title.lower():
            return title, content

    number = _cn_num_to_int(query)
    if number is not None:
        for title, content in chapters:
            if _title_contains_number(title, number):
                return title, content

    return None, None
