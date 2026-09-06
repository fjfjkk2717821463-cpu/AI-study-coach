# app.py
import hmac
import ipaddress
import json
import os
import re
import secrets
import socket
import sys
import time
import uuid
import urllib.parse

import bleach
import markdown
import requests
from charset_normalizer import from_bytes
from flask import Flask, Response, jsonify, render_template, request, session, stream_with_context
from bs4 import BeautifulSoup
from markdownify import markdownify as html_to_md

import app_paths
import book_utils
import bookshelf_mgr
import coach_v4
import session_mgr
import summary_mgr

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    BASE_DIR = sys._MEIPASS

app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, "templates"),
    static_folder=os.path.join(BASE_DIR, "static"),
)
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024

WEB_MAX_BYTES = 5 * 1024 * 1024
SESSION_TTL_SECONDS = 12 * 3600

BOOKS_DIR = os.path.join(app_paths.get_data_dir(), "books")
os.makedirs(BOOKS_DIR, exist_ok=True)

# 单用户本地工具，使用内存保存当前学习会话。
SESSIONS = {}


def _read_or_create_file(path, factory):
    """读取一个持久化的小文件；不存在时生成并写入。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            value = f.read().strip()
            if value:
                return value
    except OSError:
        pass
    value = factory()
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(value + "\n")
    except OSError:
        pass
    return value


def _get_secret_key():
    return _read_or_create_file(
        os.path.join(app_paths.get_data_dir(), ".secret_key"),
        lambda: uuid.uuid4().hex,
    )


def _get_app_password():
    """远程访问密码：优先环境变量，否则在本机数据目录生成并持久化。"""
    env_password = os.environ.get("APP_PASSWORD", "").strip()
    if env_password:
        return env_password
    return _read_or_create_file(
        os.path.join(app_paths.get_data_dir(), ".app_password"),
        lambda: secrets.token_urlsafe(9),
    )


app.secret_key = _get_secret_key()


def _is_loopback():
    return request.remote_addr in ("127.0.0.1", "::1")


@app.before_request
def _require_auth_for_remote():
    """本机访问免密；局域网/云端访问需要密码（登录 Cookie 或 Bearer Token）。"""
    if _is_loopback():
        return None
    if request.path in ("/login", "/api/login"):
        return None
    if session.get("authed") is True:
        return None

    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer ") and hmac.compare_digest(
        auth[7:].strip(), _get_app_password()
    ):
        return None

    if request.path.startswith("/api/"):
        return _json_error("未登录或密码错误。", 401)
    return render_template("login.html"), 200


def _is_within(root, path):
    try:
        root_real = os.path.realpath(root)
        path_real = os.path.realpath(path)
    except OSError:
        return False
    return path_real.startswith(root_real + os.sep)


def _valid_book_path(path):
    return bool(path) and _is_within(BOOKS_DIR, path) and os.path.isfile(path)


def _valid_session_path(path):
    return (
        bool(path)
        and _is_within(session_mgr.SESSIONS_DIR, path)
        and path.lower().endswith(".json")
    )


def _sweep_sessions():
    """清理长时间无活动的内存会话，避免长期运行内存膨胀。"""
    now = time.time()
    stale = [
        session_id
        for session_id, item in SESSIONS.items()
        if now - item.get("last_active", 0) > SESSION_TTL_SECONDS
    ]
    for session_id in stale:
        SESSIONS.pop(session_id, None)


def _persist_session(item):
    """把会话写入磁盘：每条消息后自动保存，崩溃也不丢进度。"""
    save_path = item.get("save_path")
    if not save_path or not item.get("history"):
        return ""
    mode_label = "电子书" if item["mode"] == "book" else "目录速建"
    meta = {
        "mode": item["mode"],
        "book_name": item.get("book_name", ""),
        "book_path": item.get("book_path", ""),
        "system_prompt": item["system_prompt"],
        "explain_level": item.get("explain_level", "medium"),
    }
    return session_mgr.write_session(
        save_path, mode_label, item["subject"], item["history"], meta
    )


def _is_public_url(url):
    """判断 URL 是否可安全抓取：拒绝本地、内网、链路本地和保留地址。"""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    try:
        ip = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        ip = None
    if ip is not None:
        return not (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        )
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(parsed.hostname, port, proto=socket.IPPROTO_TCP)
    except OSError:
        return False
    for info in infos:
        try:
            addr = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if (
            addr.is_private
            or addr.is_loopback
            or addr.is_link_local
            or addr.is_reserved
            or addr.is_multicast
            or addr.is_unspecified
        ):
            return False
    return True


ALLOWED_TAGS = {
    "h1", "h2", "h3", "h4", "h5", "h6", "p", "br", "hr",
    "strong", "b", "em", "i", "u", "s", "del", "code", "pre", "blockquote",
    "ul", "ol", "li", "table", "thead", "tbody", "tr", "th", "td", "a", "img",
}
ALLOWED_ATTRIBUTES = {
    "a": ["href", "title"],
    "img": ["src", "alt", "title"],
    "th": ["align"],
    "td": ["align"],
}


def _markdown_to_html(text):
    raw_html = markdown.markdown(
        text or "",
        extensions=["extra", "sane_lists"],
    )
    return bleach.clean(
        raw_html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        protocols=["http", "https", "mailto"],
        strip=True,
    )


def _json_error(message, status=400):
    return jsonify({"error": message}), status


def _sse(payload):
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _save_api_key(key):
    """把 API Key 写入本地 .env 文件，避免让使用者手动编辑文件。"""
    key = (key or "").strip()
    if not key:
        return False
    try:
        with open(coach_v4.ENV_FILE, "w", encoding="utf-8") as f:
            f.write(f"DEEPSEEK_API_KEY={key}\n")
        return True
    except OSError:
        return False


def _mask_key(key):
    key = (key or "").strip()
    if not key:
        return ""
    if len(key) <= 8:
        return "****"
    return f"{key[:4]}****{key[-4:]}"


def _fetch_web_text(url):
    """抓取网页并提取标题与 Markdown 正文。"""
    if not _is_public_url(url):
        raise ValueError("不允许访问本地、内网或保留地址。")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0 Safari/537.36"
        )
    }
    resp = requests.get(
        url,
        headers=headers,
        timeout=20,
        stream=True,
        allow_redirects=False,
    )
    if resp.status_code in (301, 302, 303, 307, 308):
        resp.close()
        raise ValueError("该地址发生了跳转，请直接填写最终网址。")
    resp.raise_for_status()

    chunks = []
    total = 0
    try:
        for chunk in resp.iter_content(chunk_size=65536):
            if not chunk:
                continue
            total += len(chunk)
            if total > WEB_MAX_BYTES:
                raise ValueError("网页内容过大（超过 5MB），暂不支持导入。")
            chunks.append(chunk)
    finally:
        resp.close()

    body_bytes = b"".join(chunks)
    text = None
    if resp.encoding and resp.encoding.lower() not in ("iso-8859-1", "ascii"):
        try:
            text = body_bytes.decode(resp.encoding, errors="replace")
        except (LookupError, UnicodeDecodeError):
            text = None
    if text is None:
        best = from_bytes(body_bytes).best()
        text = body_bytes.decode(best.encoding if best else "utf-8", errors="replace")

    soup = BeautifulSoup(text, "html.parser")
    title = ""
    if soup.title:
        title = soup.title.get_text(" ", strip=True)

    for tag in soup(["script", "style", "nav", "footer", "aside", "form", "noscript"]):
        tag.decompose()

    container = soup.find("article") or soup.find("main") or soup.body or soup
    try:
        text = html_to_md(
            str(container),
            heading_style="ATX",
            bullets="-",
            strip=["script", "style", "nav", "footer", "aside"],
        )
    except Exception:
        text = container.get_text("\n")
    text = re.sub(r"\n{3,}", "\n\n", text or "").strip()
    return title, text


@app.get("/")
def index():
    if not coach_v4.load_api_key():
        return render_template("setup.html")
    return render_template("index.html")


@app.get("/login")
def login_page():
    if _is_loopback():
        return index()
    return render_template("login.html")


@app.post("/api/login")
def api_login():
    data = request.get_json(silent=True) or {}
    password = (data.get("password") or "").strip()
    if not password or not hmac.compare_digest(password, _get_app_password()):
        return _json_error("密码错误。", 401)
    session["authed"] = True
    return jsonify({"ok": True})


@app.get("/api/config")
def api_config():
    return jsonify({"has_key": bool(coach_v4.load_api_key())})


@app.post("/api/config")
def api_config_save():
    data = request.get_json(silent=True) or {}
    key = (data.get("api_key") or "").strip()
    if not key:
        return _json_error("API Key 不能为空。")
    if not _save_api_key(key):
        return _json_error("保存失败，请检查目录写入权限。", 500)
    return jsonify({"ok": True})


@app.get("/api/settings")
def api_settings_get():
    cfg = coach_v4.load_model_config()
    active_key = cfg.get("api_key") or coach_v4.load_api_key()
    return jsonify(
        {
            "provider": cfg["provider"],
            "base_url": cfg["base_url"],
            "model": cfg["model"],
            "api_key_masked": _mask_key(active_key),
            "has_api_key": bool(active_key),
            "presets": coach_v4.MODEL_PRESETS,
        }
    )


@app.post("/api/settings")
def api_settings_save():
    data = request.get_json(silent=True) or {}
    provider = (data.get("provider") or "deepseek").strip()
    if provider not in coach_v4.MODEL_PRESETS:
        provider = "custom"

    preset = coach_v4.MODEL_PRESETS[provider]
    base_url = (data.get("base_url") or "").strip() or preset["base_url"]
    model = (data.get("model") or "").strip() or preset["model"]
    if not base_url.startswith(("http://", "https://")):
        return _json_error("接口地址必须以 http:// 或 https:// 开头。")
    if not model:
        return _json_error("模型名称不能为空。")

    cfg = coach_v4.load_model_config()
    new_key = (data.get("api_key") or "").strip()
    cfg.update(
        {
            "provider": provider,
            "base_url": base_url,
            "model": model,
            "api_key": new_key or cfg.get("api_key", ""),
        }
    )
    if not coach_v4.save_model_config(cfg):
        return _json_error("保存失败，请检查目录写入权限。", 500)
    return jsonify({"ok": True, "api_key_masked": _mask_key(new_key or cfg["api_key"] or coach_v4.load_api_key())})


@app.get("/api/shelf")
def api_shelf():
    books = bookshelf_mgr.load_shelf()
    return jsonify([{"name": b["name"], "path": b["path"]} for b in books])


@app.post("/api/shelf")
def api_shelf_add():
    file = request.files.get("file")
    if not file or not file.filename:
        return _json_error("请选择要导入的书籍文件。")

    name = (request.form.get("name") or "").strip()
    if not name:
        name = os.path.splitext(file.filename)[0].strip()
    if not name:
        return _json_error("书籍名称不能为空。")

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in (".txt", ".md", ".pdf", ".epub"):
        return _json_error("目前只支持 .txt、.md、.pdf 和 .epub 文件。")

    stored_name = f"{uuid.uuid4().hex}{ext}"
    stored_path = os.path.join(BOOKS_DIR, stored_name)
    try:
        file.save(stored_path)
    except OSError as exc:
        return _json_error(f"保存文件失败：{exc}", 500)

    if os.path.getsize(stored_path) > book_utils.MAX_BOOK_BYTES:
        try:
            os.remove(stored_path)
        except OSError:
            pass
        return _json_error(
            f"文件过大（超过 {book_utils.MAX_BOOK_BYTES // (1024 * 1024)}MB），暂不支持。"
        )

    text, err = book_utils.load_book(stored_path)
    if err:
        try:
            os.remove(stored_path)
        except OSError:
            pass
        return _json_error(f"无法读取该文件：{err}")

    bookshelf_mgr.add_book(name, stored_path)
    return jsonify({"ok": True, "book": {"name": name, "path": stored_path}})


@app.post("/api/shelf/remove")
def api_shelf_remove():
    data = request.get_json(silent=True) or {}
    path = (data.get("path") or "").strip()
    if not path:
        return _json_error("缺少书籍路径。")
    bookshelf_mgr.remove_book(path)
    return jsonify({"ok": True})


@app.post("/api/web/import")
def api_web_import():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        return _json_error("请输入以 http:// 或 https:// 开头的网页地址。")

    name = (data.get("name") or "").strip()
    try:
        title, text = _fetch_web_text(url)
    except Exception as exc:
        return _json_error(f"抓取网页失败：{exc}", 502)

    if not text.strip():
        return _json_error("没有从网页中提取到正文内容。")
    if not name:
        name = title or url

    stored_name = f"{uuid.uuid4().hex}.md"
    stored_path = os.path.join(BOOKS_DIR, stored_name)
    try:
        with open(stored_path, "w", encoding="utf-8") as f:
            f.write(f"# {title}\n\n{text}" if title else text)
    except OSError as exc:
        return _json_error(f"保存文件失败：{exc}", 500)

    bookshelf_mgr.add_book(name, stored_path)
    return jsonify({"ok": True, "book": {"name": name, "path": stored_path}})


@app.get("/api/sessions")
def api_sessions():
    return jsonify(session_mgr.list_sessions())


@app.get("/api/sessions/search")
def api_sessions_search():
    return jsonify(session_mgr.search_sessions(request.args.get("q", "")))


@app.post("/api/sessions/rename")
def api_sessions_rename():
    data = request.get_json(silent=True) or {}
    session_path = (data.get("id") or "").strip()
    if not _valid_session_path(session_path):
        return _json_error("会话路径无效。", 400)
    new_subject = (data.get("subject") or "").strip()
    if not new_subject:
        return _json_error("新标题不能为空。")
    if not session_mgr.rename_session(session_path, new_subject):
        return _json_error("重命名失败，请稍后重试。", 500)
    return jsonify({"ok": True, "subject": new_subject})


@app.post("/api/sessions/delete")
def api_sessions_delete():
    data = request.get_json(silent=True) or {}
    session_path = (data.get("id") or "").strip()
    if not _valid_session_path(session_path):
        return _json_error("会话路径无效。", 400)
    if not session_mgr.delete_session(session_path):
        return _json_error("删除失败，请稍后重试。", 500)
    return jsonify({"ok": True})


@app.post("/api/session/resume")
def api_session_resume():
    data = request.get_json(silent=True) or {}
    session_path = (data.get("id") or "").strip()
    if not _valid_session_path(session_path):
        return _json_error("会话不存在或已损坏。", 404)
    saved = session_mgr.load_session(session_path)
    if not saved:
        return _json_error("会话不存在或已损坏。", 404)

    meta = saved.get("meta") or {}
    system_prompt = meta.get("system_prompt")
    if not system_prompt:
        return _json_error("这个旧会话缺少上下文信息，无法继续。")

    new_session_id = str(uuid.uuid4())
    history = saved.get("history") or []
    SESSIONS[new_session_id] = {
        "mode": meta.get("mode") or saved.get("mode") or "book",
        "book_name": meta.get("book_name", ""),
        "book_path": meta.get("book_path", ""),
        "subject": saved.get("subject", ""),
        "system_prompt": system_prompt,
        "history": history,
        "save_path": session_path,
        "last_active": time.time(),
    }

    messages = []
    for item in history:
        role = item.get("role")
        content = item.get("content", "")
        if role == "assistant":
            messages.append(
                {"role": role, "content": content, "html": _markdown_to_html(content)}
            )
        else:
            messages.append({"role": role, "content": content})

    return jsonify(
        {
            "session_id": new_session_id,
            "subject": saved.get("subject", ""),
            "messages": messages,
        }
    )


@app.post("/api/chat/regenerate")
def api_chat_regenerate():
    data = request.get_json(silent=True) or {}
    session = SESSIONS.get(data.get("session_id"))
    if not session:
        return _json_error("学习会话不存在或已结束，请重新开始。", 404)
    session["last_active"] = time.time()

    history = session["history"]
    if not history or history[-1].get("role") != "assistant":
        return _json_error("没有可重新生成的回答。")
    history.pop()
    if not history or history[-1].get("role") != "user":
        return _json_error("没有可重新生成的回答。")

    def generate():
        parts = []
        try:
            messages = [{"role": "system", "content": session["system_prompt"]}] + history
            for delta in coach_v4.stream_chat(messages):
                parts.append(delta)
                yield _sse({"delta": delta})
            full_text = "".join(parts)
            session["history"].append({"role": "assistant", "content": full_text})
            _persist_session(session)
            yield _sse({"done": True, "html": _markdown_to_html(full_text)})
        except Exception as exc:
            yield _sse({"error": str(exc)})

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
    )


@app.get("/api/summaries")
def api_summaries():
    books = summary_mgr.list_summaries()
    for book in books:
        for item in book["summaries"]:
            item["html"] = _markdown_to_html(item["summary"])
    return jsonify({"books": books})


@app.post("/api/books/chapters")
def api_books_chapters():
    data = request.get_json(silent=True) or {}
    book_path = (data.get("path") or "").strip()
    if not _valid_book_path(book_path):
        return _json_error("书籍文件不存在，请回到书架重新添加。")
    if os.path.getsize(book_path) > book_utils.MAX_BOOK_BYTES:
        return _json_error("文件过大，无法加载。")

    level = data.get("level") or "auto"
    if level not in ("auto", "chapter", "section"):
        level = "auto"

    chapters, err = book_utils.get_book_chapters(book_path, level=level)
    if err:
        return _json_error(f"加载书籍失败：{err}")

    return jsonify(
        {
            "chapters": [
                {"title": title, "length": len(content)}
                for title, content in chapters[:50]
            ]
        }
    )


@app.post("/api/session/start")
def api_session_start():
    _sweep_sessions()
    data = request.get_json(silent=True) or {}
    mode = data.get("mode")
    session_id = str(uuid.uuid4())

    if mode == "book":
        book_name = (data.get("book_name") or "").strip()
        book_path = (data.get("book_path") or "").strip()
        query = (data.get("chapter_query") or "").strip()
        level = data.get("chapter_level") or "auto"
        if level not in ("auto", "chapter", "section"):
            level = "auto"
        explain_level = data.get("explain_level") or "medium"
        if explain_level not in ("low", "medium", "high"):
            explain_level = "medium"
        if not _valid_book_path(book_path):
            return _json_error("书籍文件不存在，请回到书架重新添加。")
        if os.path.getsize(book_path) > book_utils.MAX_BOOK_BYTES:
            return _json_error("文件过大，无法加载。")

        chapters, err = book_utils.get_book_chapters(book_path, level=level)
        if err:
            return _json_error(f"加载书籍失败：{err}")

        if query:
            chapter_title, chapter_text = book_utils.get_chapter_text(chapters, query)
        else:
            chapter_title, chapter_text = chapters[0]

        if chapter_text is None:
            chapter_title = "自定义章节"
            full_text, _ = book_utils.load_book(book_path)
            chapter_text = full_text[: coach_v4.MAX_CHAPTER_CHARS] if full_text else ""
        if len(chapter_text) > coach_v4.MAX_CHAPTER_CHARS:
            chapter_text = chapter_text[: coach_v4.MAX_CHAPTER_CHARS]

        review = summary_mgr.load_summary(book_name, chapter_title)
        system_prompt = coach_v4.build_system_prompt_book(
            chapter_title, chapter_text, review, intensity=explain_level
        )
        opening = (
            f"我们开始学习《{book_name}》的【{chapter_title}】。"
            "第一阶段我会先把本章概念讲清楚；请回复“开始”，或直接告诉我你想从哪个概念学起。"
        )
        if review:
            opening = "我检测到上次学习总结，会先针对你的薄弱点提问检测。\n\n" + opening

        SESSIONS[session_id] = {
            "mode": "book",
            "book_name": book_name,
            "book_path": book_path,
            "subject": chapter_title,
            "system_prompt": system_prompt,
            "explain_level": explain_level,
            "history": [],
            "save_path": session_mgr.new_session_path("电子书", chapter_title),
            "last_active": time.time(),
        }
        return jsonify(
            {
                "session_id": session_id,
                "mode": mode,
                "subject": chapter_title,
                "opening": opening,
                "opening_html": _markdown_to_html(opening),
            }
        )

    if mode == "outline":
        outline_title = (data.get("outline_title") or "").strip()
        outline_content = (data.get("outline_content") or "").strip()
        explain_level = data.get("explain_level") or "medium"
        if explain_level not in ("low", "medium", "high"):
            explain_level = "medium"
        if not outline_title or not outline_content:
            return _json_error("学习主题和目录内容都不能为空。")
        if len(outline_content) > coach_v4.MAX_CHAPTER_CHARS:
            outline_content = outline_content[: coach_v4.MAX_CHAPTER_CHARS]

        review = summary_mgr.load_summary("目录速建", outline_title)
        system_prompt = coach_v4.build_system_prompt_outline(
            outline_title, review, intensity=explain_level
        )
        system_prompt += f"\n\n【用户提供的章节目录/要点】\n{outline_content}"

        opening = (
            f"让我们开始学习【{outline_title}】。"
            "第一阶段我会先讲解概念；请回复“开始”，我会先给出知识地图。"
        )
        if review:
            opening = "我检测到上次学习总结，会先针对你的薄弱点提问检测。\n\n" + opening

        SESSIONS[session_id] = {
            "mode": "outline",
            "book_name": "",
            "subject": outline_title,
            "system_prompt": system_prompt,
            "explain_level": explain_level,
            "history": [],
            "save_path": session_mgr.new_session_path("目录速建", outline_title),
            "last_active": time.time(),
        }
        return jsonify(
            {
                "session_id": session_id,
                "mode": mode,
                "subject": outline_title,
                "opening": opening,
                "opening_html": _markdown_to_html(opening),
            }
        )

    return _json_error("未知的学习模式。")


@app.post("/api/chat")
def api_chat():
    data = request.get_json(silent=True) or {}
    session = SESSIONS.get(data.get("session_id"))
    if not session:
        return _json_error("学习会话不存在或已结束，请重新开始。", 404)
    session["last_active"] = time.time()

    message = (data.get("message") or "").strip()
    if not message:
        return _json_error("消息不能为空。")

    messages = (
        [{"role": "system", "content": session["system_prompt"]}]
        + session["history"]
        + [{"role": "user", "content": message}]
    )
    try:
        reply = coach_v4._request_chat(messages, stream=False)
    except Exception as exc:
        return _json_error(f"请求失败：{exc}", 502)

    session["history"].append({"role": "user", "content": message})
    session["history"].append({"role": "assistant", "content": reply})
    _persist_session(session)
    return jsonify({"reply": reply, "html": _markdown_to_html(reply)})


@app.post("/api/chat/stream")
def api_chat_stream():
    data = request.get_json(silent=True) or {}
    session = SESSIONS.get(data.get("session_id"))
    if not session:
        return _json_error("学习会话不存在或已结束，请重新开始。", 404)
    session["last_active"] = time.time()

    message = (data.get("message") or "").strip()
    if not message:
        return _json_error("消息不能为空。")

    messages = (
        [{"role": "system", "content": session["system_prompt"]}]
        + session["history"]
        + [{"role": "user", "content": message}]
    )

    def generate():
        parts = []
        try:
            for delta in coach_v4.stream_chat(messages):
                parts.append(delta)
                yield _sse({"delta": delta})
            full_text = "".join(parts)
            session["history"].append({"role": "user", "content": message})
            session["history"].append({"role": "assistant", "content": full_text})
            _persist_session(session)
            yield _sse({"done": True, "html": _markdown_to_html(full_text)})
        except Exception as exc:
            yield _sse({"error": str(exc)})

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
    )


@app.post("/api/summary")
def api_summary():
    data = request.get_json(silent=True) or {}
    session = SESSIONS.get(data.get("session_id"))
    if not session:
        return _json_error("学习会话不存在或已结束，请重新开始。", 404)
    session["last_active"] = time.time()

    summary_prompt = """请基于本次完整对话，生成一份结构化学习总结，包含：
1. 已掌握的核心概念
2. 仍然存在的薄弱点和理解漏洞
3. 典型反例或边界情况复盘
4. 下一步复习建议
只输出总结内容。"""

    try:
        summary = coach_v4.generate_summary(
            session["history"], session["system_prompt"], summary_prompt
        )
    except Exception as exc:
        return _json_error(f"生成总结失败：{exc}", 502)

    if not summary:
        return _json_error("生成总结失败，请稍后重试。", 502)

    if session["mode"] == "book":
        summary_mgr.save_summary(session["book_name"], session["subject"], summary)
    else:
        summary_mgr.save_summary("目录速建", session["subject"], summary)
    _persist_session(session)

    return jsonify({"summary": summary, "html": _markdown_to_html(summary)})


@app.post("/api/session/end")
def api_session_end():
    data = request.get_json(silent=True) or {}
    session = SESSIONS.pop(data.get("session_id"), None)
    if not session:
        return _json_error("学习会话不存在或已结束。", 404)

    _persist_session(session)
    return jsonify({"ok": True})




if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    app.run(host=host, port=port, debug=False, threaded=True)
