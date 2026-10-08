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


# 解析一本大部头教材要几十秒，按（文件、粒度）缓存结果，避免重复计算。
_CHAPTER_CACHE = {}
_CHAPTER_CACHE_LIMIT = 4


def _cache_key(book_path, level):
    try:
        stat = os.stat(book_path)
        stamp = (stat.st_mtime, stat.st_size)
    except OSError:
        stamp = (0, 0)
    return (os.path.abspath(book_path), stamp, level)


def get_book_chapters(book_path, level="auto"):
    """统一入口：EPUB 按目录切分，其它格式按标题正则切分。

    结果会缓存：同一本书在不同章节粒度之间切换时不必重新解析。
    """
    key = _cache_key(book_path, level)
    if key in _CHAPTER_CACHE:
        return _CHAPTER_CACHE[key], None

    if not os.path.exists(book_path):
        return None, "文件不存在"
    if not _check_size(book_path):
        return None, f"文件过大（超过 {MAX_BOOK_BYTES // (1024 * 1024)}MB），暂不支持。"

    if os.path.splitext(book_path)[1].lower() == ".epub":
        chapters, err = _extract_epub_chapters(book_path, level=level)
        if err is not None:
            return None, err
        if chapters:
            _remember_chapters(key, chapters)
            return chapters, None

    text, err = load_book(book_path)
    if err:
        return None, err

    chapters = split_chapters(text, level=level)
    if not chapters:
        chapters = [("全文", text)]
    _remember_chapters(key, chapters)
    return chapters, None


def _remember_chapters(key, chapters):
    _CHAPTER_CACHE[key] = chapters
    if len(_CHAPTER_CACHE) > _CHAPTER_CACHE_LIMIT:
        try:
            _CHAPTER_CACHE.pop(next(iter(_CHAPTER_CACHE)))
        except (StopIteration, KeyError):
            pass


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

# 目录行：带点线、省略号或间隔号的条目，以及「第X章 标题 12」这种带页码的写法
_TOC_LINE_PATTERN = re.compile(r"(?:\.{2,}|…{2,}|·{2,})")
_CHAPTER_WITH_PAGE_PATTERN = re.compile(
    r"^\s*第\s*[一二三四五六七八九十百零〇\d]+\s*[章节讲部分篇].*?\s+\d{1,4}\s*$"
)
# 纯页码行（含 12 / -12- / —12— 这类页眉页脚）
_PAGE_NUMBER_PATTERN = re.compile(r"^\s*[-—–]?\s*\d{1,4}\s*[-—–]?\s*$")
_SENTENCE_END = "。！？；：!?;:"
_NOISE_PUNCTUATION = "。，、；：！？|"
# 节一级标题（第X节 / 第X讲）
_SECTION_TITLE_PATTERN = re.compile(
    r"^\s*第\s*[一二三四五六七八九十百零〇\d]+\s*[节讲]"
)
# 「第X章 / 第X节 …」这种结构前缀，清理标题时要把前缀与正文分开
_STRUCTURE_PREFIX = re.compile(
    r"^(第\s*[一二三四五六七八九十百零〇\d]+\s*[章节课讲部分篇])\s*"
)
# 目录页本身不作为章节
_TOC_TITLE_WORDS = {"目录", "目次", "总目录"}


def _clean_heading_text(text):
    """清理标题里的 Markdown / HTML 噪声，并去掉中文之间的空格。

    PDF 转换后常见的情况：`第三章 血 液`、`第一节 | 研究对象`、
    `<u>目录</u>`、`**Physiology**`，都要还原成人能读的标题。
    注意保留「第X章 / 第X节」与标题正文之间的那个空格。
    """
    cleaned = re.sub(r"<[^>]+>", " ", text or "")
    cleaned = re.sub(r"[*_`#]+", "", cleaned)
    cleaned = cleaned.replace("|", " ")
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()

    match = _STRUCTURE_PREFIX.match(cleaned)
    if match:
        prefix = re.sub(r"\s+", "", match.group(1))
        rest = cleaned[match.end():]
        rest = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", rest)
        return (prefix + " " + rest).strip()

    cleaned = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", cleaned)
    return cleaned.strip()


def _structure_levels(lines):
    """从文档自身的标题分布推断"章级/节级"分别是第几层。

    不同文件的写法差别很大：有的用 # 表示章、## 表示节，
    有的（尤其是 PDF 转出来的）把章放在 ##、把节放在 ######。
    这里取「最浅的、至少出现两次的那一层」作为章级，它的下一层作为节级。
    """
    counts = {}
    for line in lines:
        heading = _MD_HEADING_PATTERN.match(line.strip())
        if heading:
            size = len(heading.group(1))
            counts[size] = counts.get(size, 0) + 1

    candidates = sorted(level for level, count in counts.items() if count >= 2)
    if not candidates:
        return 1, 2
    chapter_level = candidates[0]
    deeper = [level for level in candidates if level > chapter_level]
    section_level = deeper[0] if deeper else chapter_level + 1
    return chapter_level, section_level


def _boundary_rank(level, title, chapter_level=1, section_level=2):
    """把候选标题分成两级：1=章级、2=节级、3=更小的点（不单独成讲）。

    不同 PDF 的标题层级很不一样：有的书把"章"转成二级标题、把"节"转成六级标题，
    所以不能只看 # 的数量，还要看标题本身的写法。
    """
    text = (title or "").strip()
    if level <= chapter_level or _MAJOR_CHAPTER_PATTERN.match(text):
        return 1
    if level <= section_level or _SECTION_TITLE_PATTERN.match(text):
        return 2
    return 3


def _normalize_title(text):
    """归一化标题，用于识别重复出现的页眉。"""
    return re.sub(r"[\s#*`_（）()【】\[\]<>]+", "", text or "")


def _looks_like_toc_line(line):
    text = (line or "").strip()
    if not text:
        return False
    if _TOC_LINE_PATTERN.search(text):
        return True
    return bool(_CHAPTER_WITH_PAGE_PATTERN.match(text))


def _is_boundary_candidate(line):
    """能否作为章节起点：要短、不以句号结尾、不是目录条目。

    这一条把「正文里提到第三章……」这类句子和目录页挡在章节之外。
    """
    text = (line or "").strip()
    if not text or len(text) > 40:
        return False
    if text[-1] in _SENTENCE_END:
        return False
    return not _looks_like_toc_line(text)


def _collect_running_headers(lines):
    """找出重复出现的短行（页眉/页脚），返回归一化文本集合。

    教材 PDF 每页都会印「第一章 绪论」这类页眉，如果不过滤，
    切分时会把每一页都当成新的章节起点。
    """
    counts = {}
    for line in lines:
        key = _normalize_title(line)
        if not key:
            continue
        counts[key] = counts.get(key, 0) + 1

    noise = set()
    for key, count in counts.items():
        if count < 5 or len(key) > 12:
            continue
        if any(ch in key for ch in _NOISE_PUNCTUATION):
            continue
        noise.add(key)
    return noise


def split_chapters(text, level="auto"):
    """
    按 Markdown 标题或常见章节标题分割文本。
    支持：# 到 ###### 标题，以及第X章/节/讲/部分/篇、Chapter/Part/Unit/Section 等。
    level: auto=自动、chapter=大章节、section=小章节。
    返回列表 [(title, content), ...]
    """
    lines = text.splitlines()
    chapter_level, section_level = _structure_levels(lines)
    raw_boundaries = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        heading = _MD_HEADING_PATTERN.match(stripped)
        if heading:
            source = heading.group(2).strip()
            title = _clean_heading_text(source)
            if title and title not in _TOC_TITLE_WORDS and _is_boundary_candidate(source):
                raw_boundaries.append(
                    (
                        index,
                        _boundary_rank(
                            len(heading.group(1)), title, chapter_level, section_level
                        ),
                        title,
                    )
                )
            continue
        if _MAJOR_CHAPTER_PATTERN.match(stripped) and _is_boundary_candidate(stripped):
            raw_boundaries.append((index, 1, _clean_heading_text(stripped)))
            continue
        if _CHAPTER_PATTERN.match(stripped) and _is_boundary_candidate(stripped):
            title = _clean_heading_text(stripped)
            # 走这个分支的都是「第X节 / Unit N」这类，除非标题本身是第X章
            raw_boundaries.append(
                (index, _boundary_rank(3, title, chapter_level, section_level), title)
            )

    # 页眉去重：同一标题在文中反复出现时，只保留"级别最高"的那一次作为章节起点
    # （章 > 节 > 小标题；同级别时取最靠前的一次）。
    title_counts = {}
    best_choice = {}
    for item in raw_boundaries:
        key = _normalize_title(item[2])
        title_counts[key] = title_counts.get(key, 0) + 1
        current = best_choice.get(key)
        if current is None or item[1] < current[1]:
            best_choice[key] = (item[1], item[0])

    boundaries = []
    for item in raw_boundaries:
        key = _normalize_title(item[2])
        if title_counts.get(key, 0) >= 3:
            # 注意：这里不要用 level / index 作为变量名，会覆盖上面的函数参数
            kept_level, kept_index = best_choice[key]
            if not (item[1] == kept_level and item[0] == kept_index):
                continue
        boundaries.append(item)

    if not boundaries:
        return [("全文", text)] if text.strip() else []

    if level == "chapter":
        chosen = [item for item in boundaries if item[1] == 1] or boundaries
    elif level == "section":
        chosen = [item for item in boundaries if item[1] <= 2] or boundaries
    else:
        chapter_items = [item for item in boundaries if item[1] == 1]
        chosen = chapter_items if len(chapter_items) >= 2 else boundaries

    chapters = []
    current_title = "前言/引言"
    current_content = []
    boundary_map = {item[0]: item for item in chosen}
    noise_lines = _collect_running_headers(lines)

    for index, line in enumerate(lines):
        if index in boundary_map:
            if current_content:
                chapters.append((current_title, "\n".join(current_content)))
            current_title = boundary_map[index][2]
            current_content = []
        else:
            if _PAGE_NUMBER_PATTERN.match(line):
                continue
            if _normalize_title(line) in noise_lines:
                continue
            current_content.append(line)

    if current_content:
        chapters.append((current_title, "\n".join(current_content)))

    # 丢掉内容为空的条目（例如标题后面紧跟着另一个标题）
    return [(title, content) for title, content in chapters if content.strip()]


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
