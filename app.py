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

import advice_mgr
import app_paths
import book_utils
import bookshelf_mgr
import classroom_mgr
import coach_v4
import mastery_mgr
import review_mgr
import session_mgr
import settings_mgr
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
VERSION = "1.1.0"
REPO_URL = "https://github.com/fjfjkk2717821463-cpu/AI-study-coach"

BOOKS_DIR = os.path.join(app_paths.get_data_dir(), "books")
os.makedirs(BOOKS_DIR, exist_ok=True)

# 单用户本地工具，使用内存保存当前学习会话。
SESSIONS = {}

CONCEPT_LINE_RE = re.compile(r"^\s*[-*•]\s*(.+?)[（(](掌握|待巩固)[）)]\s*$")


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
        "chapter_text": item.get("chapter_text", ""),
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


def _friendly_error(message):
    """把底层报错翻译成可操作的提示。"""
    text = (message or "").lower()
    if any(k in text for k in ("401", "invalid api key", "authentication", "unauthorized")):
        return "API Key 无效或已失效：请打开右上角「⚙️ 模型设置」检查密钥。"
    if any(k in text for k in ("402", "insufficient", "balance", "quota")):
        return "账户余额或额度不足：请充值，或在「⚙️ 模型设置」换一个可用的模型。"
    if any(k in text for k in ("429", "rate limit", "too many")):
        return "请求太频繁，请稍等十几秒再试。"
    if any(k in text for k in ("timeout", "timed out", "connection")):
        return "连接超时或网络异常：请检查网络后重试，或换个接口。"
    if any(k in text for k in ("500", "502", "503")):
        return "模型服务暂时出错：请稍后重试，或换一个模型。"
    return f"请求失败：{message}"


def _extract_concepts(text):
    """从总结文本的【概念清单】部分解析出概念和掌握程度。"""
    concepts = []
    for line in (text or "").splitlines():
        match = CONCEPT_LINE_RE.match(line.strip())
        if match:
            concepts.append({"term": match.group(1).strip(), "level": match.group(2)})
    return concepts


def _parse_quiz_json(text):
    """从模型输出中稳健地提取题目 JSON。"""
    text = (text or "").strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return []
    try:
        data = json.loads(text[start:end + 1])
    except (ValueError, json.JSONDecodeError):
        return []
    questions = data.get("questions") if isinstance(data, dict) else None
    if not isinstance(questions, list):
        return []
    clean = []
    for question in questions:
        if not isinstance(question, dict):
            continue
        qtype = question.get("type")
        qtext = (question.get("question") or "").strip()
        if not qtext:
            continue
        if qtype == "choice":
            options = [str(opt).strip() for opt in (question.get("options") or [])]
            if len(options) < 2:
                continue
            clean.append({"type": "choice", "question": qtext, "options": options[:6]})
        else:
            clean.append({"type": "short", "question": qtext})
        if len(clean) >= 5:
            break
    return clean


def _add_usage(session_dict, usage):
    usage = usage or {}
    bucket = session_dict.setdefault(
        "usage", {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    )
    for key in bucket:
        bucket[key] += int(usage.get(key, 0) or 0)
    return bucket


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


@app.get("/api/version")
def api_version():
    return jsonify({"version": VERSION, "repo": REPO_URL})


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
    app_settings = settings_mgr.load_settings()
    return jsonify(
        {
            "provider": cfg["provider"],
            "base_url": cfg["base_url"],
            "model": cfg["model"],
            "api_key_masked": _mask_key(active_key),
            "has_api_key": bool(active_key),
            "presets": coach_v4.MODEL_PRESETS,
            "spaced_review": app_settings["spaced_review"],
            "price_per_mtok": app_settings["price_per_mtok"],
            "version": VERSION,
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

    app_settings = settings_mgr.load_settings()
    if "spaced_review" in data:
        app_settings["spaced_review"] = bool(data["spaced_review"])
    if "price_per_mtok" in data:
        try:
            price = float(data["price_per_mtok"] or 0)
            if price >= 0:
                app_settings["price_per_mtok"] = price
        except (TypeError, ValueError):
            return _json_error("费率必须是数字。")
    if not settings_mgr.save_settings(app_settings):
        return _json_error("保存失败，请检查目录写入权限。", 500)

    return jsonify(
        {
            "ok": True,
            "api_key_masked": _mask_key(new_key or cfg["api_key"] or coach_v4.load_api_key()),
            "spaced_review": app_settings["spaced_review"],
            "price_per_mtok": app_settings["price_per_mtok"],
        }
    )


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
        "chapter_text": meta.get("chapter_text", ""),
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
    _touch_session(session)

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
            bucket = _add_usage(session, coach_v4.LAST_USAGE)
            _persist_session(session)
            yield _sse(
                {
                    "done": True,
                    "html": _markdown_to_html(full_text),
                    "usage": coach_v4.LAST_USAGE,
                    "session_usage": bucket,
                }
            )
        except Exception as exc:
            yield _sse({"error": _friendly_error(str(exc))})

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
            "chapter_text": chapter_text,
            "subject": chapter_title,
            "system_prompt": system_prompt,
            "explain_level": explain_level,
            "history": [],
            "save_path": session_mgr.new_session_path("电子书", chapter_title),
            "last_active": time.time(),
            "started_at": time.time(),
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
            "chapter_text": "",
            "subject": outline_title,
            "system_prompt": system_prompt,
            "explain_level": explain_level,
            "history": [],
            "save_path": session_mgr.new_session_path("目录速建", outline_title),
            "last_active": time.time(),
            "started_at": time.time(),
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
    _touch_session(session)

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
        return _json_error(_friendly_error(str(exc)), 502)

    session["history"].append({"role": "user", "content": message})
    session["history"].append({"role": "assistant", "content": reply})
    bucket = _add_usage(session, coach_v4.LAST_USAGE)
    _persist_session(session)
    return jsonify(
        {
            "reply": reply,
            "html": _markdown_to_html(reply),
            "usage": coach_v4.LAST_USAGE,
            "session_usage": bucket,
        }
    )


@app.post("/api/chat/stream")
def api_chat_stream():
    data = request.get_json(silent=True) or {}
    session = SESSIONS.get(data.get("session_id"))
    if not session:
        return _json_error("学习会话不存在或已结束，请重新开始。", 404)
    _touch_session(session)

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
            bucket = _add_usage(session, coach_v4.LAST_USAGE)
            _persist_session(session)
            yield _sse(
                {
                    "done": True,
                    "html": _markdown_to_html(full_text),
                    "usage": coach_v4.LAST_USAGE,
                    "session_usage": bucket,
                }
            )
        except Exception as exc:
            yield _sse({"error": _friendly_error(str(exc))})

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
    _touch_session(session)

    summary_prompt = """请基于本次完整对话，生成一份结构化学习总结，包含：
1. 已掌握的核心概念
2. 仍然存在的薄弱点和理解漏洞
3. 典型反例或边界情况复盘
4. 下一步复习建议
最后另起一行输出「【概念清单】」，每行格式为「- 概念名称（掌握）」或「- 概念名称（待巩固）」，列出 3~8 个本章最重要的概念。"""

    try:
        summary = coach_v4.generate_summary(
            session["history"], session["system_prompt"], summary_prompt
        )
    except Exception as exc:
        return _json_error(f"生成总结失败：{exc}", 502)

    if not summary:
        return _json_error("生成总结失败，请稍后重试。", 502)

    book_name = session["book_name"] if session["mode"] == "book" else "目录速建"
    concepts = _extract_concepts(summary)
    summary_mgr.save_summary(book_name, session["subject"], summary, concepts)
    review_mgr.ensure_entry(book_name, session["subject"])
    _add_usage(session, coach_v4.LAST_USAGE)
    _persist_session(session)
    classroom_sync = _sync_summary_to_classroom(session, book_name, concepts, summary)

    return jsonify(
        {
            "summary": summary,
            "html": _markdown_to_html(summary),
            "concepts": concepts,
            "usage": coach_v4.LAST_USAGE,
            "session_usage": session.get("usage", {}),
            "classroom": classroom_sync,
        }
    )


@app.post("/api/session/end")
def api_session_end():
    data = request.get_json(silent=True) or {}
    session = SESSIONS.pop(data.get("session_id"), None)
    if not session:
        return _json_error("学习会话不存在或已结束。", 404)

    _persist_session(session)
    return jsonify({"ok": True})




@app.post("/api/session/compare")
def api_session_compare():
    """默写比对：把用户凭记忆的重构与原文逐段对照点评。"""
    data = request.get_json(silent=True) or {}
    session = SESSIONS.get(data.get("session_id"))
    if not session:
        return _json_error("学习会话不存在或已结束，请重新开始。", 404)
    _touch_session(session)

    reconstruction = (data.get("reconstruction") or "").strip()
    if not reconstruction:
        return _json_error("默写内容不能为空。")
    if len(reconstruction) > coach_v4.MAX_CHAPTER_CHARS:
        return _json_error("默写内容过长，请精简后再试。")

    source = session.get("chapter_text") or ""
    # 默写覆盖率是本地计算的证据，不依赖模型点评是否成功，因此先记录。
    dictation_book = session["book_name"] if session["mode"] == "book" else "目录速建"
    dictation_entry = mastery_mgr.record_dictation(
        dictation_book, session.get("subject", ""), reconstruction, source
    )
    prompt = (
        "请对照【原文】逐段检查我的默写重构：列出遗漏、偏差和错误，"
        "并肯定写得准确、有洞察的地方。只输出检查结果。\n\n"
        f"【我的默写重构】\n{reconstruction}"
    )
    if source:
        prompt += f"\n\n【原文】\n{source}"

    def generate():
        parts = []
        try:
            messages = [
                {"role": "system", "content": "你是严格但鼓励的学习教练，负责对照原文点评默写重构。"},
                {"role": "user", "content": prompt},
            ]
            for delta in coach_v4.stream_chat(messages):
                parts.append(delta)
                yield _sse({"delta": delta})
            full_text = "".join(parts)
            session["history"].append({"role": "user", "content": f"【默写重构】\n{reconstruction}"})
            session["history"].append({"role": "assistant", "content": full_text})
            bucket = _add_usage(session, coach_v4.LAST_USAGE)
            _persist_session(session)
            yield _sse(
                {
                    "done": True,
                    "html": _markdown_to_html(full_text),
                    "usage": coach_v4.LAST_USAGE,
                    "session_usage": bucket,
                    "dictation": dictation_entry,
                }
            )
        except Exception as exc:
            yield _sse({"error": _friendly_error(str(exc))})

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
    )


@app.get("/api/session/source")
def api_session_source():
    """返回当前会话的原文，供默写模式展开对照。"""
    session = SESSIONS.get(request.args.get("session_id"))
    if not session:
        return _json_error("学习会话不存在或已结束。", 404)
    return jsonify(
        {
            "text": session.get("chapter_text") or "",
            "subject": session.get("subject", ""),
        }
    )


@app.post("/api/quiz/generate")
def api_quiz_generate():
    """基于学习总结（重点是薄弱点）出题。"""
    data = request.get_json(silent=True) or {}
    book_name = (data.get("book_name") or "").strip()
    chapter_title = (data.get("chapter_title") or "").strip()
    full = summary_mgr.load_summary_full(book_name, chapter_title)
    if not full or not full.get("summary"):
        return _json_error("还没有这份学习总结，请先学习并生成总结。")

    prompt = (
        "你是出题助手。基于下面的学习总结（重点是薄弱点），出 3 道题："
        "第 1 题为单选题并给出 4 个选项，第 2、3 题为简答题。"
        "只输出 JSON，不要任何解释，格式："
        '{"questions":[{"type":"choice","question":"...","options":["A...","B...","C...","D..."]},'
        '{"type":"short","question":"..."},{"type":"short","question":"..."}]}\n\n'
        f"学习总结：\n{full['summary'][:6000]}"
    )
    try:
        reply = coach_v4._request_chat(
            [{"role": "system", "content": "你是严谨的出题助手。"}, {"role": "user", "content": prompt}],
            stream=False,
        )
    except Exception as exc:
        return _json_error(_friendly_error(str(exc)), 502)

    questions = _parse_quiz_json(reply)
    if not questions:
        return _json_error("出题失败：模型输出格式异常，请重试。", 502)
    return jsonify({"questions": questions, "usage": coach_v4.LAST_USAGE})


@app.post("/api/quiz/grade")
def api_quiz_grade():
    """批改学生答案。"""
    data = request.get_json(silent=True) or {}
    questions = data.get("questions") or []
    answers = data.get("answers") or {}
    if not questions:
        return _json_error("题目不能为空。")

    lines = []
    for index, question in enumerate(questions, 1):
        answer = answers.get(str(index), "") or ""
        if question.get("type") == "choice":
            options = "；".join(question.get("options") or [])
            lines.append(f"第{index}题（单选）：{question.get('question')}\n选项：{options}\n学生答案：{answer}")
        else:
            lines.append(f"第{index}题（简答）：{question.get('question')}\n学生答案：{answer or '（未作答）'}")
    prompt = (
        "请逐题批改下面的学生答案：指出对错、遗漏和更完整的思路，最后给一句总体建议。"
        "用中文输出，简洁一点。\n\n" + "\n\n".join(lines)
    )
    try:
        reply = coach_v4._request_chat(
            [{"role": "system", "content": "你是耐心的批改老师。"}, {"role": "user", "content": prompt}],
            stream=False,
        )
    except Exception as exc:
        return _json_error(_friendly_error(str(exc)), 502)
    return jsonify({"html": _markdown_to_html(reply), "text": reply, "usage": coach_v4.LAST_USAGE})


@app.post("/api/concept-map")
def api_concept_map():
    """为某本书中还没有概念清单的总结补充提取概念。"""
    data = request.get_json(silent=True) or {}
    book_name = (data.get("book_name") or "").strip()
    if not book_name:
        return _json_error("书籍名称不能为空。")

    books = summary_mgr.list_summaries()
    target = next((book for book in books if book["book_name"] == book_name), None)
    if not target:
        return _json_error("这本书还没有学习总结。")

    updated = 0
    for item in target["summaries"]:
        if item.get("concepts"):
            continue
        prompt = (
            "从下面的学习总结中提炼 3~8 个最重要的概念，判断每个概念是「掌握」还是「待巩固」。"
            "每行输出一个，格式：- 概念名称（掌握）或 - 概念名称（待巩固）。"
            "只输出这些行，不要其他内容。\n\n学习总结：\n" + item["summary"][:6000]
        )
        try:
            reply = coach_v4._request_chat(
                [{"role": "system", "content": "你是概念提炼助手。"}, {"role": "user", "content": prompt}],
                stream=False,
            )
        except Exception as exc:
            return _json_error(_friendly_error(str(exc)), 502)
        concepts = _extract_concepts(reply)
        if concepts and summary_mgr.update_summary_concepts(book_name, item["chapter_title"], concepts):
            updated += 1

    return jsonify({"ok": True, "updated": updated, "usage": coach_v4.LAST_USAGE})


@app.get("/api/reviews/due")
def api_reviews_due():
    app_settings = settings_mgr.load_settings()
    return jsonify(
        {
            "enabled": app_settings["spaced_review"],
            "items": review_mgr.due_entries() if app_settings["spaced_review"] else [],
        }
    )


@app.post("/api/reviews/record")
def api_reviews_record():
    data = request.get_json(silent=True) or {}
    book_name = (data.get("book_name") or "").strip()
    chapter_title = (data.get("chapter_title") or "").strip()
    try:
        quality = int(data.get("quality") or 0)
    except (TypeError, ValueError):
        quality = 0
    if not book_name or not chapter_title or quality not in (1, 2, 3):
        return _json_error("参数格式错误。")
    entry = review_mgr.record_review(book_name, chapter_title, quality)
    return jsonify({"ok": True, "entry": entry})


# ---------------------------------------------------------------- 师-生-机：班级与学情
#
# 数据层在 classroom_mgr（纯数据、可测试），教学建议逻辑在 advice_mgr。
# 学生侧只上报"摘要 + 概念 + 少量原话"，不上报完整对话；教师侧只读取自己班级。


def _chapter_list(value):
    """把章节参数统一成列表，支持 JSON 数组或逗号/换行分隔的字符串。"""
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [part.strip() for part in re.split(r"[,\n;、]", value) if part.strip()]
    return []


def _class_chapters_text(book_name, chapters):
    """从书架取教材原文节选，供 M6 判断误区与设计课堂活动。

    取不到原文不是错误：教学建议会退化为"只有统计与证据"的版本，
    但不会假装读过教材。
    """
    if not book_name:
        return ""
    try:
        shelf = bookshelf_mgr.load_shelf()
    except Exception:
        shelf = []
    book_path = ""
    for item in shelf or []:
        if (item.get("name") or "").strip() == book_name:
            book_path = item.get("path") or ""
            break
    if not book_path or not _valid_book_path(book_path):
        return ""
    try:
        items, err = book_utils.get_book_chapters(book_path, level="auto")
    except Exception:
        return ""
    if err or not items:
        return ""
    wanted = {c for c in (chapters or []) if c}
    parts = []
    for title, content in items:
        if wanted and title not in wanted:
            continue
        parts.append(f"## {title}\n{content}")
    return "\n\n".join(parts)[: advice_mgr.MAX_CHAPTER_CHARS]


def _session_student_texts(session, limit=40):
    """取出学生在本次会话里自己说过的话（复述、默写），作为证据来源。"""
    texts = []
    for message in (session.get("history") or [])[-limit:]:
        if message.get("role") != "user":
            continue
        content = (message.get("content") or "").strip()
        if content:
            texts.append(content)
    return texts


IDLE_CAP_SECONDS = 120


def _touch_session(session):
    """累计有效学习时长：把「真的在学」和「挂着页面」分开。

    两次交互间隔在 IDLE_CAP_SECONDS 以内按真实间隔累计；超过上限的
    （去吃饭、切去别的窗口）只按上限计一次，避免时长虚高。
    """
    now = time.time()
    last = session.get("last_active") or session.get("started_at") or now
    try:
        gap = max(0.0, now - float(last))
    except (TypeError, ValueError):
        gap = 0.0
    session["active_seconds"] = float(session.get("active_seconds") or 0) + min(
        gap, IDLE_CAP_SECONDS
    )
    session["last_active"] = now
    return session["active_seconds"]


def _session_minutes(session):
    started = session.get("started_at") or session.get("last_active") or time.time()
    return max(0, int(round((time.time() - started) / 60)))


def _session_effective_minutes(session):
    """有效学习时长（分钟）；没有活动数据时返回 0，不冒充会话时长。"""
    return max(0, int(round(float(session.get("active_seconds") or 0) / 60)))


def _sync_summary_to_classroom(session, book_name, concepts, summary):
    """学习结束（生成总结）后，自动把学习摘要同步到学生已加入的班级。

    只上报摘要、概念与少量原话证据，不上报完整对话；学生没有加入班级时
    静默跳过，上报失败也不影响学习流程本身——教师端的数据全部来自这里。
    """
    identity = classroom_mgr.load_identity()
    student_id = identity.get("student_id") or ""
    class_ids = [c for c in identity.get("classes") or [] if c]
    if not student_id or not class_ids:
        return {"synced": 0, "classes": []}

    weak_concepts = [
        item
        for item in (concepts or [])
        if item.get("level") == classroom_mgr.LEVEL_WEAK
    ]
    chapter_title = session.get("subject", "")
    review_entry = review_mgr.get_entry(book_name, chapter_title) or {}
    state = mastery_mgr.chapter_mastery(
        book_name, chapter_title, review_entry.get("last_quality")
    )
    snapshot = classroom_mgr.build_snapshot(
        book_name,
        chapter_title,
        concepts,
        minutes=_session_minutes(session),
        minutes_effective=_session_effective_minutes(session),
        rounds=len(
            [m for m in session.get("history") or [] if m.get("role") == "user"]
        ),
        evidence=classroom_mgr.extract_evidence_quotes(
            weak_concepts, _session_student_texts(session)
        ),
        summary_excerpt=summary or "",
        retell=state.get("retell"),
        dictation=state.get("dictation"),
        mastery=state.get("verdict"),
    )

    synced = []
    for class_id in class_ids:
        ok, _ = classroom_mgr.report_record(
            class_id, student_id, identity.get("display_name", ""), snapshot
        )
        if ok:
            target = classroom_mgr.get_class(class_id)
            synced.append(target.get("name") if target else class_id)
    return {"synced": len(synced), "classes": synced, "mastery": state.get("verdict")}


@app.get("/teacher")
def teacher_page():
    """教师端总览页：教师只需浏览器打开网址，无需安装任何东西。"""
    return render_template("teacher.html")


@app.get("/api/mastery")
def api_mastery():
    """查询某本书某章节的掌握度证据：复述评分、默写覆盖、复习重测。"""
    book_name = (request.args.get("book_name") or "").strip()
    chapter_title = (request.args.get("chapter_title") or "").strip()
    if not chapter_title:
        return _json_error("章节不能为空。")
    review_entry = review_mgr.get_entry(book_name, chapter_title) or {}
    state = mastery_mgr.chapter_mastery(
        book_name, chapter_title, review_entry.get("last_quality")
    )
    return jsonify({"ok": True, **state})


@app.post("/api/retell/score")
def api_retell_score():
    """复述质量评分：四维各 0-3 分，校验后作为达标证据保存在本地。"""
    data = request.get_json(silent=True) or {}
    session = SESSIONS.get(data.get("session_id"))
    if not session:
        return _json_error("学习会话不存在或已结束，请重新开始。", 404)
    _touch_session(session)

    text = (data.get("text") or "").strip()
    if len(mastery_mgr.normalize_text(text)) < 15:
        return _json_error("复述太短了，请用自己的话多讲几句，并说清它成立的边界条件。")
    if len(text) > mastery_mgr.MAX_RETELL_CHARS:
        return _json_error("复述内容过长，请精简后再提交。")

    book_name = session["book_name"] if session["mode"] == "book" else "目录速建"
    chapter_title = session.get("subject", "")
    summary_full = summary_mgr.load_summary_full(book_name, chapter_title) or {}
    concepts = summary_full.get("concepts") or []

    result, meta = mastery_mgr.score_retell(
        chapter_title,
        session.get("chapter_text") or "",
        concepts,
        text,
        coach_v4._request_chat,
        phase=(data.get("phase") or "学习中"),
    )
    meta["usage"] = coach_v4.LAST_USAGE
    if result is None:
        return jsonify(
            {
                "ok": False,
                "degraded": True,
                "message": "这次没能完成评分，请稍后重试。评分不会凭空给分，也不会计入达标。",
                "meta": meta,
            }
        )

    mastery_mgr.save_retell(book_name, chapter_title, result)
    review_entry = review_mgr.get_entry(book_name, chapter_title) or {}
    state = mastery_mgr.chapter_mastery(
        book_name, chapter_title, review_entry.get("last_quality")
    )
    return jsonify(
        {"ok": True, "result": result, "mastery": state["verdict"], "meta": meta}
    )


@app.get("/api/class/status")
def api_class_status():
    """学生端：本机身份与已加入的班级。"""
    identity = classroom_mgr.get_or_create_student_identity()
    return jsonify(
        {
            "student_id": identity.get("student_id", ""),
            "display_name": identity.get("display_name", ""),
            "classes": classroom_mgr.my_classes(),
        }
    )


@app.post("/api/class/join")
def api_class_join():
    """学生端：用班级码加入班级。"""
    data = request.get_json(silent=True) or {}
    code = (data.get("code") or "").strip().upper()
    display_name = (data.get("display_name") or "").strip()
    if not code:
        return _json_error("请填写班级码。")

    identity = classroom_mgr.get_or_create_student_identity()
    ok, result = classroom_mgr.join_class(code, identity.get("student_id"), display_name)
    if not ok:
        return _json_error(result)

    target_class = result["class"]
    classroom_mgr.remember_class(target_class["id"])
    if display_name:
        classroom_mgr.set_display_name(display_name)
    return jsonify(
        {
            "ok": True,
            "student_id": identity.get("student_id", ""),
            "display_name": display_name or identity.get("display_name", ""),
            "class": {
                "id": target_class.get("id"),
                "name": target_class.get("name"),
                "code": target_class.get("code"),
            },
        }
    )


@app.post("/api/class/report")
def api_class_report():
    """学生端：上报一章的学习摘要（只上报摘要、概念与少量原话）。"""
    data = request.get_json(silent=True) or {}
    class_id = (data.get("class_id") or "").strip()
    book_name = (data.get("book_name") or "").strip()
    chapter_title = (data.get("chapter_title") or "").strip()
    if not class_id or not chapter_title:
        return _json_error("缺少班级或章节信息。")

    concepts = classroom_mgr.normalize_concepts(data.get("concepts"))
    evidence = classroom_mgr.normalize_evidence(data.get("evidence"))
    if not evidence:
        evidence = classroom_mgr.extract_evidence_quotes(concepts, data.get("recall_texts"))

    snapshot = classroom_mgr.build_snapshot(
        book_name,
        chapter_title,
        concepts,
        minutes=data.get("minutes"),
        rounds=data.get("rounds"),
        evidence=evidence,
        summary_excerpt=data.get("summary_excerpt") or "",
        review_stage=data.get("review_stage") or 0,
        studied_at=data.get("studied_at") or "",
    )

    identity = classroom_mgr.get_or_create_student_identity()
    display_name = (data.get("display_name") or identity.get("display_name") or "").strip()
    ok, error = classroom_mgr.report_record(
        class_id, identity.get("student_id"), display_name, snapshot
    )
    if not ok:
        return _json_error(error)
    return jsonify(
        {
            "ok": True,
            "student_id": identity.get("student_id", ""),
            "chapter_title": snapshot["chapter_title"],
            "weak_concepts": [
                item["term"] for item in snapshot["concepts"] if item["level"] == classroom_mgr.LEVEL_WEAK
            ],
        }
    )


@app.get("/api/class/my_tasks")
def api_class_my_tasks():
    """学生端：查看自己未完成的任务。"""
    class_id = (request.args.get("class_id") or "").strip()
    if not class_id:
        identity = classroom_mgr.get_or_create_student_identity()
        classes = classroom_mgr.my_classes()
        class_id = classes[0]["id"] if classes else ""
    if not class_id:
        return jsonify({"tasks": []})

    record = classroom_mgr.load_record(class_id, classroom_mgr.get_or_create_student_identity()["student_id"]) or {}
    learned = {
        (entry.get("chapter_title") or "").strip()
        for entry in record.get("chapters") or []
    }
    tasks = []
    for item in classroom_mgr.list_assignments(class_id):
        required = [c for c in (item.get("chapters") or []) if c]
        done = [c for c in required if c in learned]
        if len(done) >= len(required):
            continue
        tasks.append(
            {
                "id": item.get("id"),
                "book_name": item.get("book_name", ""),
                "chapters": required,
                "done_chapters": done,
                "due_at": item.get("due_at", ""),
                "requirement": item.get("requirement", ""),
            }
        )
    return jsonify({"tasks": tasks})


@app.get("/api/teacher/classes")
def api_teacher_classes():
    return jsonify({"classes": classroom_mgr.list_classes()})


@app.post("/api/teacher/classes")
def api_teacher_class_create():
    data = request.get_json(silent=True) or {}
    item = classroom_mgr.create_class(data.get("name"), data.get("teacher"))
    if not item:
        return _json_error("班级名称不能为空。")
    return jsonify({"ok": True, "class": item})


@app.get("/api/teacher/overview")
def api_teacher_overview():
    """教师端：班级总览（指标、薄弱概念排行、章节掌握、任务进度）。"""
    class_id = (request.args.get("class_id") or "").strip()
    book_name = (request.args.get("book_name") or "").strip()
    chapters = _chapter_list(request.args.get("chapters"))
    stats = classroom_mgr.aggregate_class(class_id, book_name, chapters)
    if not stats:
        return _json_error("班级不存在。")
    return jsonify({"ok": True, "stats": stats})


@app.get("/api/teacher/assignments")
def api_teacher_assignments():
    class_id = (request.args.get("class_id") or "").strip()
    if not classroom_mgr.get_class(class_id):
        return _json_error("班级不存在。")
    return jsonify({"assignments": classroom_mgr.list_assignments(class_id)})


@app.post("/api/teacher/assignments")
def api_teacher_assignment_create():
    data = request.get_json(silent=True) or {}
    class_id = (data.get("class_id") or "").strip()
    item = classroom_mgr.add_assignment(
        class_id,
        data.get("book_name"),
        _chapter_list(data.get("chapters")),
        due_at=data.get("due_at") or "",
        requirement=data.get("requirement") or "",
    )
    if not item:
        return _json_error("布置任务失败：请检查班级与章节。")
    return jsonify({"ok": True, "assignment": item})


@app.get("/api/teacher/students")
def api_teacher_students():
    class_id = (request.args.get("class_id") or "").strip()
    stats = classroom_mgr.aggregate_class(class_id)
    if not stats:
        return _json_error("班级不存在。")
    return jsonify(
        {
            "ok": True,
            "class": stats["class"],
            "students": stats["students"],
            "students_total": stats["students_total"],
            "students_reported": stats["students_reported"],
        }
    )


@app.get("/api/teacher/student/<student_id>")
def api_teacher_student_detail(student_id):
    class_id = (request.args.get("class_id") or "").strip()
    if not classroom_mgr.get_class(class_id):
        return _json_error("班级不存在。")
    record = classroom_mgr.load_record(class_id, student_id)
    if not record:
        return _json_error("该学生还没有上报学习摘要。")
    return jsonify({"ok": True, "record": record})


@app.post("/api/teacher/advice")
def api_teacher_advice():
    """M6：把班级学情统计转成下一次课的教学动作。

    输出里的每个数字都会先与统计核对，模型编造的概念与证据会被剔除；
    校验不过就降级为纯统计版本，绝不把没有依据的内容交给教师。
    """
    data = request.get_json(silent=True) or {}
    class_id = (data.get("class_id") or "").strip()
    book_name = (data.get("book_name") or "").strip()
    chapters = _chapter_list(data.get("chapters"))
    try:
        top_n = int(data.get("top_n") or advice_mgr.DEFAULT_TOP_N)
    except (TypeError, ValueError):
        top_n = advice_mgr.DEFAULT_TOP_N
    top_n = max(1, min(top_n, advice_mgr.MAX_TOP_N))
    refresh = bool(data.get("refresh"))

    stats = classroom_mgr.aggregate_class(class_id, book_name, chapters)
    if not stats:
        return _json_error("班级不存在。")

    if not refresh:
        cached = classroom_mgr.load_advice_cache(class_id, book_name, chapters)
        if cached and cached.get("advice"):
            return jsonify(
                {
                    "ok": True,
                    "cached": True,
                    "stats": stats,
                    "advice": cached.get("advice"),
                    "meta": cached.get("meta") or {},
                    "generated_at": cached.get("generated_at", ""),
                }
            )

    payload = {"ok": True, "cached": False, "stats": stats, "meta": {}}
    reported = stats.get("students_reported", 0)
    if reported < advice_mgr.MIN_REPORTED_STUDENTS:
        payload.update(
            {
                "degraded": True,
                "message": (
                    f"已上报学习摘要的学生不足 {advice_mgr.MIN_REPORTED_STUDENTS} 人，"
                    "暂不生成教学建议；可先展示学情统计。"
                ),
                "advice": advice_mgr.build_fallback_advice(
                    stats, top_n, reason="insufficient_samples"
                ),
            }
        )
        return jsonify(payload)

    chapter_text = _class_chapters_text(book_name, chapters)
    advice, meta = advice_mgr.generate_advice(
        stats, chapter_text, coach_v4._request_chat, top_n=top_n
    )
    meta["usage"] = coach_v4.LAST_USAGE
    meta["has_chapter_text"] = bool(chapter_text)

    if not meta.get("degraded"):
        classroom_mgr.save_advice_cache(
            class_id,
            book_name,
            chapters,
            {
                "advice": advice,
                "meta": meta,
                "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            },
        )

    payload.update(
        {
            "degraded": bool(meta.get("degraded")),
            "advice": advice,
            "meta": meta,
        }
    )
    return jsonify(payload)


if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    app.run(host=host, port=port, debug=False, threaded=True)
