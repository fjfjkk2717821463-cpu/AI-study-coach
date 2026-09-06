# app.py
import json
import os
import re
import uuid

import markdown
import requests
from flask import Flask, Response, jsonify, render_template_string, request, stream_with_context
from bs4 import BeautifulSoup
from markdownify import markdownify as html_to_md

import app_paths
import book_utils
import bookshelf_mgr
import coach_v4
import session_mgr
import summary_mgr

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024

BOOKS_DIR = os.path.join(app_paths.get_data_dir(), "books")
os.makedirs(BOOKS_DIR, exist_ok=True)

# 单用户本地工具，使用内存保存当前学习会话。
SESSIONS = {}


def _markdown_to_html(text):
    return markdown.markdown(
        text or "",
        extensions=["extra", "sane_lists"],
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


def _fetch_web_text(url):
    """抓取网页并提取标题与 Markdown 正文。"""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0 Safari/537.36"
        )
    }
    resp = requests.get(url, headers=headers, timeout=20)
    resp.raise_for_status()

    if not resp.encoding or resp.encoding.lower() in ("iso-8859-1", "ascii"):
        resp.encoding = resp.apparent_encoding

    soup = BeautifulSoup(resp.text, "html.parser")
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
        return CONFIG_PAGE
    return HTML_PAGE


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


@app.post("/api/session/resume")
def api_session_resume():
    data = request.get_json(silent=True) or {}
    saved = session_mgr.load_session(data.get("id"))
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
    if not book_path or not os.path.exists(book_path):
        return _json_error("书籍文件不存在，请回到书架重新添加。")

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
        if not book_path or not os.path.exists(book_path):
            return _json_error("书籍文件不存在，请回到书架重新添加。")

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
    return jsonify({"reply": reply, "html": _markdown_to_html(reply)})


@app.post("/api/chat/stream")
def api_chat_stream():
    data = request.get_json(silent=True) or {}
    session = SESSIONS.get(data.get("session_id"))
    if not session:
        return _json_error("学习会话不存在或已结束，请重新开始。", 404)

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

    return jsonify({"summary": summary, "html": _markdown_to_html(summary)})


@app.post("/api/session/end")
def api_session_end():
    data = request.get_json(silent=True) or {}
    session = SESSIONS.pop(data.get("session_id"), None)
    if not session:
        return _json_error("学习会话不存在或已结束。", 404)

    if session["history"]:
        mode_label = "电子书" if session["mode"] == "book" else "目录速建"
        meta = {
            "mode": session["mode"],
            "book_name": session.get("book_name", ""),
            "book_path": session.get("book_path", ""),
            "system_prompt": session["system_prompt"],
        }
        session_mgr.save_session(
            mode_label, session["subject"], session["history"], meta
        )
    return jsonify({"ok": True})


CONFIG_PAGE = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>设置 API Key</title>
  <style>
    :root { --accent: #4f46e5; --border: #e3e6ea; --muted: #6b7280; }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 24px;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
        "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
      background: #f6f7f9;
      color: #1f2328;
    }
    .card {
      width: 100%;
      max-width: 460px;
      background: #fff;
      border: 1px solid var(--border);
      border-radius: 16px;
      padding: 26px;
    }
    h1 { font-size: 22px; margin: 0 0 8px; }
    p { color: var(--muted); line-height: 1.7; margin: 0 0 18px; }
    label { display: block; margin-bottom: 7px; font-weight: 600; }
    input {
      width: 100%;
      padding: 11px 12px;
      border: 1px solid var(--border);
      border-radius: 10px;
      font-size: 15px;
      font-family: inherit;
    }
    button {
      margin-top: 14px;
      width: 100%;
      padding: 11px 16px;
      border: 0;
      border-radius: 10px;
      background: var(--accent);
      color: #fff;
      font-size: 15px;
      cursor: pointer;
    }
    button:disabled { opacity: 0.5; cursor: not-allowed; }
    .error { color: #b91c1c; margin-top: 10px; font-size: 14px; }
    .ok { color: #15803d; margin-top: 10px; font-size: 14px; }
  </style>
</head>
<body>
  <div class="card">
    <h1>设置 DeepSeek API Key</h1>
    <p>请粘贴你的 DeepSeek API Key，它会保存在本地，仅用于这个学习教练，不会上传到其它地方。</p>
    <label for="apiKeyInput">API Key</label>
    <input id="apiKeyInput" type="password" placeholder="sk-..." autocomplete="off">
    <button id="saveBtn" type="button">保存并开始使用</button>
    <p id="message" class="error"></p>
  </div>

  <script>
    const $ = (id) => document.getElementById(id);
    $("saveBtn").addEventListener("click", async () => {
      const key = $("apiKeyInput").value.trim();
      if (!key) {
        $("message").className = "error";
        $("message").textContent = "请输入 API Key。";
        return;
      }
      $("saveBtn").disabled = true;
      $("message").textContent = "";
      try {
        const resp = await fetch("/api/config", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ api_key: key }),
        });
        const data = await resp.json();
        if (!resp.ok) {
          $("message").className = "error";
          $("message").textContent = data.error || "保存失败。";
          return;
        }
        $("message").className = "ok";
        $("message").textContent = "已保存，正在进入……";
        location.reload();
      } catch (err) {
        $("message").className = "error";
        $("message").textContent = "保存失败：" + err.message;
      } finally {
        $("saveBtn").disabled = false;
      }
    });
  </script>
</body>
</html>
"""


HTML_PAGE = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>DFL Coach</title>
  <style>
    :root {
      --bg: #f6f7f9;
      --card: #ffffff;
      --text: #1f2328;
      --muted: #6b7280;
      --border: #e3e6ea;
      --accent: #4f46e5;
      --accent-hover: #4338ca;
      --user-bubble: #eef2ff;
      --assistant-bubble: #ffffff;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
        "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.7;
    }
    .app { max-width: 960px; margin: 0 auto; padding: 28px 16px 60px; }
    header { margin-bottom: 22px; }
    h1 { font-size: 28px; margin: 0 0 6px; }
    .sub { color: var(--muted); margin: 0; }
    .card {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 16px;
      padding: 20px;
      margin-bottom: 20px;
    }
    .tabs { display: flex; gap: 8px; margin-bottom: 18px; }
    .common-row { margin-bottom: 10px; }
    .common-row label { margin-top: 0; }
    .tabs button {
      flex: 1;
      padding: 10px 12px;
      border: 1px solid var(--border);
      background: #fff;
      border-radius: 10px;
      cursor: pointer;
      font-size: 15px;
    }
    .tabs button.active {
      background: var(--accent);
      color: #fff;
      border-color: var(--accent);
    }
    label { display: block; margin: 14px 0 6px; font-weight: 600; font-size: 14px; }
    input, select, textarea {
      width: 100%;
      padding: 10px 12px;
      border: 1px solid var(--border);
      border-radius: 10px;
      font-size: 15px;
      font-family: inherit;
      background: #fff;
    }
    textarea { min-height: 130px; resize: vertical; }
    .hidden { display: none !important; }
    button.primary {
      margin-top: 18px;
      padding: 11px 18px;
      background: var(--accent);
      color: #fff;
      border: 0;
      border-radius: 10px;
      font-size: 15px;
      cursor: pointer;
    }
    button.primary:hover { background: var(--accent-hover); }
    button.primary:disabled { opacity: 0.5; cursor: not-allowed; }
    .error { color: #b91c1c; margin-top: 12px; font-size: 14px; }
    .hint { color: var(--muted); font-size: 13px; margin: 8px 0 0; }

    #chatSection { display: flex; flex-direction: column; }
    #messages { min-height: 320px; max-height: 62vh; overflow-y: auto; padding: 6px 2px; }
    .msg { margin-bottom: 18px; }
    .msg .role {
      font-size: 12px;
      color: var(--muted);
      margin-bottom: 6px;
      font-weight: 600;
    }
    .msg .bubble {
      padding: 12px 14px;
      border-radius: 12px;
      background: var(--assistant-bubble);
      border: 1px solid var(--border);
      overflow-wrap: anywhere;
    }
    .msg.user .bubble { background: var(--user-bubble); border-color: #dfe5ff; }
    .bubble > :first-child { margin-top: 0; }
    .bubble > :last-child { margin-bottom: 0; }
    .bubble pre {
      background: #0f172a;
      color: #e2e8f0;
      padding: 12px;
      border-radius: 10px;
      overflow-x: auto;
    }
    .bubble code {
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 0.9em;
    }
    .bubble p code, .bubble li code {
      background: #eef1f5;
      padding: 1px 5px;
      border-radius: 5px;
    }
    .bubble blockquote {
      border-left: 3px solid var(--accent);
      margin: 0;
      padding: 4px 12px;
      color: var(--muted);
      background: #f8f9fb;
      border-radius: 0 8px 8px 0;
    }
    .bubble table { border-collapse: collapse; width: 100%; }
    .bubble th, .bubble td { border: 1px solid var(--border); padding: 7px 9px; text-align: left; }
    .composer {
      display: flex;
      gap: 10px;
      align-items: flex-end;
      border-top: 1px solid var(--border);
      padding-top: 14px;
      margin-top: 6px;
    }
    .composer textarea { flex: 1; min-height: 52px; max-height: 180px; }
    .composer button { padding: 10px 18px; white-space: nowrap; }
    .actions { display: flex; gap: 10px; margin-top: 12px; }
    .actions button {
      padding: 9px 14px;
      border: 1px solid var(--border);
      background: #fff;
      border-radius: 10px;
      cursor: pointer;
      font-size: 14px;
    }
    .summary-note {
      background: #fff7ed;
      border-color: #fed7aa;
    }
    .header-actions { display: flex; gap: 10px; margin-top: 14px; flex-wrap: wrap; }
    .header-actions button {
      padding: 8px 12px;
      border: 1px solid var(--border);
      background: #fff;
      border-radius: 10px;
      cursor: pointer;
      font-size: 14px;
    }
    .back-row { margin-bottom: 16px; }
    .back-row button {
      padding: 7px 12px;
      border: 1px solid var(--border);
      background: #fff;
      border-radius: 10px;
      cursor: pointer;
      font-size: 14px;
    }
    .book-group { margin-bottom: 22px; }
    .book-group h3 { margin: 0 0 10px; }
    .summary-item {
      border: 1px solid var(--border);
      border-radius: 10px;
      margin-bottom: 8px;
      background: #fff;
    }
    .summary-item summary {
      cursor: pointer;
      padding: 10px 12px;
      font-weight: 600;
      list-style: none;
    }
    .summary-item summary::-webkit-details-marker { display: none; }
    .summary-item .summary-body { padding: 0 12px 12px; }
    .shelf-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 10px 12px;
      border: 1px solid var(--border);
      border-radius: 10px;
      margin-bottom: 8px;
      background: #fff;
    }
    .shelf-row .shelf-name { font-weight: 600; word-break: break-all; }
    .shelf-row .shelf-path { color: var(--muted); font-size: 12px; word-break: break-all; }
    .shelf-row button {
      flex: 0 0 auto;
      padding: 6px 10px;
      border: 1px solid var(--border);
      background: #fff;
      border-radius: 8px;
      cursor: pointer;
      color: #b91c1c;
    }
    .add-book { margin-top: 18px; border-top: 1px solid var(--border); padding-top: 16px; }
    .web-import { margin-top: 0; border-top: 0; padding-top: 0; }
    .empty-hint { color: var(--muted); font-size: 14px; }
    @media (max-width: 640px) {
      .app { padding: 16px 10px 40px; }
      h1 { font-size: 24px; }
      .card { padding: 16px; }
      .header-actions { gap: 8px; }
      .header-actions button { flex: 1 1 auto; font-size: 13px; padding: 8px 6px; }
      input, select, textarea { font-size: 16px; }
      .composer { flex-direction: column; align-items: stretch; }
      .composer textarea { min-height: 48px; }
      .composer button { width: 100%; }
      .actions { flex-wrap: wrap; }
      .actions button { flex: 1 1 auto; }
      .shelf-row { flex-direction: column; align-items: stretch; }
      .shelf-row button { width: 100%; }
    }
  </style>
</head>
<body>
  <div class="app">
    <header>
      <h1>📚 DFL Coach</h1>
      <p class="sub">先带你准确学懂一个概念，再用费曼式复述、反例和默写重构来检测巩固，而不是只「看懂了」。</p>
      <div class="header-actions">
        <button id="historyBtn" type="button">🕘 继续上次学习</button>
        <button id="reviewBtn" type="button">📚 复习所有总结</button>
        <button id="shelfBtn" type="button">📖 管理书架</button>
      </div>
    </header>

    <section id="setup" class="card">
      <div class="tabs">
        <button id="bookTab" class="active" type="button">电子书模式</button>
        <button id="outlineTab" type="button">目录速建模式</button>
      </div>

      <div class="common-row">
        <label for="explainLevel">讲解强度</label>
        <select id="explainLevel">
          <option value="high" selected>多讲解（先教再问）</option>
          <option value="medium">适中（讲解与追问并重）</option>
          <option value="low">少讲解（以追问为主）</option>
        </select>
      </div>

      <div id="bookSetup">
        <label for="bookSelect">选择书架上的书籍</label>
        <select id="bookSelect"></select>
        <label for="chapterSelect">选择章节</label>
        <select id="chapterSelect"></select>
        <label for="chapterLevel">章节层级</label>
        <select id="chapterLevel">
          <option value="auto" selected>自动（推荐）</option>
          <option value="chapter">大章节</option>
          <option value="section">小章节</option>
        </select>
        <label for="chapterQuery">或输入章节关键词</label>
        <input id="chapterQuery" placeholder="例如：第四章 / 熵 / 4；留空则使用下拉选中的章节">
        <p class="hint">还没有书籍？点击上方「📖 管理书架」，直接导入 .txt / .md / .pdf / .epub 或网页链接。</p>
      </div>

      <div id="outlineSetup" class="hidden">
        <label for="outlineTitle">学习主题 / 章节标题</label>
        <input id="outlineTitle" placeholder="例如：热力学第二定律">
        <label for="outlineContent">章节目录或内容要点</label>
        <textarea id="outlineContent" placeholder="粘贴目录、大纲或你要掌握的知识点"></textarea>
      </div>

      <button id="startBtn" class="primary" type="button">开始学习</button>
      <p id="setupError" class="error"></p>
    </section>

    <section id="bookshelfSection" class="card hidden">
      <div class="back-row">
        <button id="bookshelfBackBtn" type="button">← 返回开始学习</button>
      </div>
      <h2>📖 管理书架</h2>
      <p class="hint">直接导入 .txt、.md、.pdf 或 .epub 书籍，文件会复制到本机数据目录中。</p>
      <div id="shelfList"></div>
      <div class="add-book">
        <label for="bookFileInput">选择书籍文件</label>
        <input id="bookFileInput" type="file" accept=".txt,.md,.pdf,.epub">
        <label for="bookNameInput">书籍名称（可选）</label>
        <input id="bookNameInput" placeholder="留空则使用文件名">
        <button id="addBookBtn" class="primary" type="button">添加到书架</button>
      </div>
      <div class="add-book web-import">
        <label for="webUrlInput">或从网页导入</label>
        <input id="webUrlInput" placeholder="https://example.com/article">
        <input id="webNameInput" placeholder="名称（可选，默认使用网页标题）">
        <button id="importWebBtn" class="primary" type="button">导入网页</button>
      </div>
      <p id="shelfError" class="error"></p>
    </section>

    <section id="historySection" class="card hidden">
      <div class="back-row">
        <button id="historyBackBtn" type="button">← 返回开始学习</button>
      </div>
      <h2>🕘 继续上次学习</h2>
      <p class="hint">选择一次已保存的会话继续。</p>
      <select id="sessionSelect"></select>
      <button id="resumeBtn" class="primary" type="button">继续学习</button>
      <p id="historyError" class="error"></p>
    </section>

    <section id="reviewSection" class="card hidden">
      <div class="back-row">
        <button id="reviewBackBtn" type="button">← 返回开始学习</button>
      </div>
      <h2>📚 学习总结复习中心</h2>
      <div id="reviewContent"></div>
    </section>

    <section id="chatSection" class="card hidden">
      <div id="messages"></div>
      <div class="composer">
        <textarea id="messageInput" placeholder="输入你的理解，教练会追问、给反例、检查逻辑……"></textarea>
        <button id="sendBtn" class="primary" type="button">发送</button>
      </div>
      <div class="actions">
        <button id="summaryBtn" type="button">生成总结</button>
        <button id="endBtn" type="button">结束并保存会话</button>
      </div>
    </section>
  </div>

  <script>
    let mode = "book";
    let sessionId = null;

    const $ = (id) => document.getElementById(id);

    function escapeHtml(text) {
      const div = document.createElement("div");
      div.textContent = text == null ? "" : String(text);
      return div.innerHTML;
    }

    function showSection(name) {
      ["setup", "chatSection", "bookshelfSection", "historySection", "reviewSection"].forEach((id) => {
        $(id).classList.toggle("hidden", id !== name);
      });
      window.scrollTo({ top: 0, behavior: "smooth" });
    }

    $("historyBtn").addEventListener("click", async () => {
      showSection("historySection");
      $("historyError").textContent = "";
      await loadSessions();
    });

    $("reviewBtn").addEventListener("click", async () => {
      showSection("reviewSection");
      await loadReview();
    });

    $("shelfBtn").addEventListener("click", async () => {
      showSection("bookshelfSection");
      $("shelfError").textContent = "";
      await loadShelfList();
    });

    $("historyBackBtn").addEventListener("click", () => showSection("setup"));
    $("reviewBackBtn").addEventListener("click", () => showSection("setup"));
    $("bookshelfBackBtn").addEventListener("click", () => showSection("setup"));

    function switchMode(nextMode) {
      mode = nextMode;
      $("bookTab").classList.toggle("active", mode === "book");
      $("outlineTab").classList.toggle("active", mode === "outline");
      $("bookSetup").classList.toggle("hidden", mode !== "book");
      $("outlineSetup").classList.toggle("hidden", mode !== "outline");
      $("setupError").textContent = "";
    }

    $("bookTab").addEventListener("click", () => switchMode("book"));
    $("outlineTab").addEventListener("click", () => switchMode("outline"));

    async function loadShelf() {
      try {
        const resp = await fetch("/api/shelf");
        const books = await resp.json();
        const select = $("bookSelect");
        select.innerHTML = "";
        if (!books.length) {
          const opt = document.createElement("option");
          opt.textContent = "书架为空，请先添加书籍";
          opt.disabled = true;
          select.appendChild(opt);
          return;
        }
        books.forEach((book) => {
          const opt = document.createElement("option");
          opt.value = JSON.stringify({ name: book.name, path: book.path });
          opt.textContent = book.name;
          select.appendChild(opt);
        });
        loadChapters(JSON.parse(select.value).path);
      } catch (err) {
        $("setupError").textContent = "读取书架失败：" + err.message;
      }
    }

    async function loadShelfList() {
      const list = $("shelfList");
      list.innerHTML = "";
      try {
        const resp = await fetch("/api/shelf");
        const books = await resp.json();
        if (!books.length) {
          list.innerHTML = '<p class="empty-hint">书架还是空的，请在下方导入书籍。</p>';
          return;
        }
        books.forEach((book) => {
          const row = document.createElement("div");
          row.className = "shelf-row";
          const info = document.createElement("div");
          info.innerHTML =
            '<div class="shelf-name"></div><div class="shelf-path"></div>';
          info.querySelector(".shelf-name").textContent = book.name;
          info.querySelector(".shelf-path").textContent = book.path;
          const remove = document.createElement("button");
          remove.type = "button";
          remove.textContent = "移除";
          remove.addEventListener("click", () => removeBook(book.path));
          row.appendChild(info);
          row.appendChild(remove);
          list.appendChild(row);
        });
      } catch (err) {
        $("shelfError").textContent = "读取书架失败：" + err.message;
      }
    }

    async function addBook() {
      const fileInput = $("bookFileInput");
      const nameInput = $("bookNameInput");
      if (!fileInput.files.length) {
        $("shelfError").textContent = "请先选择要导入的文件。";
        return;
      }
      const formData = new FormData();
      formData.append("file", fileInput.files[0]);
      formData.append("name", nameInput.value.trim());
      $("addBookBtn").disabled = true;
      $("shelfError").textContent = "正在导入，请稍候……";
      try {
        const resp = await fetch("/api/shelf", {
          method: "POST",
          body: formData,
        });
        const data = await resp.json();
        if (!resp.ok) {
          $("shelfError").textContent = data.error || "导入失败。";
          return;
        }
        $("shelfError").textContent = "";
        fileInput.value = "";
        nameInput.value = "";
        await Promise.all([loadShelfList(), loadShelf()]);
      } catch (err) {
        $("shelfError").textContent = "导入失败：" + err.message;
      } finally {
        $("addBookBtn").disabled = false;
      }
    }

    async function removeBook(path) {
      try {
        const resp = await fetch("/api/shelf/remove", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ path }),
        });
        const data = await resp.json();
        if (!resp.ok) {
          $("shelfError").textContent = data.error || "移除失败。";
          return;
        }
        await Promise.all([loadShelfList(), loadShelf()]);
      } catch (err) {
        $("shelfError").textContent = "移除失败：" + err.message;
      }
    }

    $("addBookBtn").addEventListener("click", addBook);

    async function importWebPage() {
      const url = $("webUrlInput").value.trim();
      if (!url) {
        $("shelfError").textContent = "请输入要导入的网页地址。";
        return;
      }
      $("importWebBtn").disabled = true;
      $("shelfError").textContent = "正在抓取网页，请稍候……";
      try {
        const resp = await fetch("/api/web/import", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            url,
            name: $("webNameInput").value.trim(),
          }),
        });
        const data = await resp.json();
        if (!resp.ok) {
          $("shelfError").textContent = data.error || "导入网页失败。";
          return;
        }
        $("shelfError").textContent = "";
        $("webUrlInput").value = "";
        $("webNameInput").value = "";
        await Promise.all([loadShelfList(), loadShelf()]);
      } catch (err) {
        $("shelfError").textContent = "导入网页失败：" + err.message;
      } finally {
        $("importWebBtn").disabled = false;
      }
    }

    $("importWebBtn").addEventListener("click", importWebPage);

    async function loadChapters(path) {
      const select = $("chapterSelect");
      select.innerHTML = "";
      $("chapterQuery").value = "";
      if (!path) return;
      try {
        const resp = await fetch("/api/books/chapters", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ path, level: $("chapterLevel").value }),
        });
        const data = await resp.json();
        if (!resp.ok) {
          const opt = document.createElement("option");
          opt.textContent = "无法读取章节";
          opt.disabled = true;
          select.appendChild(opt);
          return;
        }
        data.chapters.forEach((chapter) => {
          const opt = document.createElement("option");
          opt.value = chapter.title;
          opt.textContent = chapter.title;
          select.appendChild(opt);
        });
        $("chapterQuery").value = select.value || "";
      } catch (err) {
        const opt = document.createElement("option");
        opt.textContent = "读取章节失败";
        opt.disabled = true;
        select.appendChild(opt);
      }
    }

    $("bookSelect").addEventListener("change", () => {
      const selected = $("bookSelect").value;
      if (selected) loadChapters(JSON.parse(selected).path);
    });

    $("chapterSelect").addEventListener("change", () => {
      $("chapterQuery").value = $("chapterSelect").value;
    });

    $("chapterLevel").addEventListener("change", () => {
      const selected = $("bookSelect").value;
      if (selected) loadChapters(JSON.parse(selected).path);
    });

    $("explainLevel").addEventListener("change", () => {
      localStorage.setItem("explainLevel", $("explainLevel").value);
    });

    async function loadSessions() {
      const select = $("sessionSelect");
      select.innerHTML = "";
      try {
        const resp = await fetch("/api/sessions");
        const sessions = await resp.json();
        if (!sessions.length) {
          const opt = document.createElement("option");
          opt.textContent = "还没有已保存的会话";
          opt.disabled = true;
          select.appendChild(opt);
          return;
        }
        sessions.forEach((session) => {
          const opt = document.createElement("option");
          opt.value = session.id;
          const modeLabel = session.mode === "电子书" ? "电子书" : "目录";
          opt.textContent = `[${modeLabel}] ${session.subject} · ${session.saved_at}`;
          select.appendChild(opt);
        });
      } catch (err) {
        $("historyError").textContent = "读取会话失败：" + err.message;
      }
    }

    async function loadReview() {
      const box = $("reviewContent");
      box.innerHTML = "";
      try {
        const resp = await fetch("/api/summaries");
        const data = await resp.json();
        const books = data.books || [];
        if (!books.length) {
          box.innerHTML = '<p class="hint">还没有任何学习总结。</p>';
          return;
        }
        books.forEach((book) => {
          const group = document.createElement("div");
          group.className = "book-group";
          const title = document.createElement("h3");
          title.textContent = "📖 " + book.book_name;
          group.appendChild(title);

          book.summaries.forEach((item) => {
            const details = document.createElement("details");
            details.className = "summary-item";
            const summary = document.createElement("summary");
            summary.textContent = `${item.chapter_title} · ${item.updated_at}`;
            details.appendChild(summary);

            const body = document.createElement("div");
            body.className = "summary-body bubble";
            body.innerHTML = item.html || "";
            details.appendChild(body);
            group.appendChild(details);
          });
          box.appendChild(group);
        });
      } catch (err) {
        box.textContent = "读取总结失败：" + err.message;
      }
    }

    function showError(message) {
      $("setupError").textContent = message;
    }

    $("startBtn").addEventListener("click", async () => {
      $("startBtn").disabled = true;
      showError("");
      const payload = { mode };
      payload.explain_level = $("explainLevel").value;
      if (mode === "book") {
        const selected = $("bookSelect").value;
        if (!selected) {
          showError("请先在命令行中添加一本书。");
          $("startBtn").disabled = false;
          return;
        }
        const book = JSON.parse(selected);
        payload.book_name = book.name;
        payload.book_path = book.path;
        payload.chapter_query = $("chapterQuery").value.trim() || $("chapterSelect").value;
        payload.chapter_level = $("chapterLevel").value;
      } else {
        payload.outline_title = $("outlineTitle").value.trim();
        payload.outline_content = $("outlineContent").value.trim();
      }

      try {
        const resp = await fetch("/api/session/start", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        const data = await resp.json();
        if (!resp.ok) {
          showError(data.error || "启动失败");
          return;
        }
        sessionId = data.session_id;
        $("messages").innerHTML = "";
        $("setup").classList.add("hidden");
        $("chatSection").classList.remove("hidden");
        appendAssistant(data.opening_html);
        $("messageInput").focus();
      } catch (err) {
        showError("启动失败：" + err.message);
      } finally {
        $("startBtn").disabled = false;
      }
    });

    function appendUser(text) {
      const wrap = document.createElement("div");
      wrap.className = "msg user";
      wrap.innerHTML = '<div class="role">你</div><div class="bubble"></div>';
      wrap.querySelector(".bubble").textContent = text;
      $("messages").appendChild(wrap);
      scrollToBottom();
    }

    function appendAssistant(html) {
      const wrap = document.createElement("div");
      wrap.className = "msg assistant";
      wrap.innerHTML = '<div class="role">教练</div><div class="bubble"></div>';
      wrap.querySelector(".bubble").innerHTML = html;
      $("messages").appendChild(wrap);
      scrollToBottom();
    }

    function appendNote(text) {
      const wrap = document.createElement("div");
      wrap.className = "msg assistant";
      wrap.innerHTML = '<div class="role">系统</div><div class="bubble summary-note"></div>';
      wrap.querySelector(".bubble").textContent = text;
      $("messages").appendChild(wrap);
      scrollToBottom();
    }

    function scrollToBottom() {
      const box = $("messages");
      box.scrollTop = box.scrollHeight;
    }

    async function sendMessage() {
      const input = $("messageInput");
      const text = input.value.trim();
      if (!text || !sessionId) return;
      input.value = "";
      appendUser(text);
      $("sendBtn").disabled = true;

      const wrap = document.createElement("div");
      wrap.className = "msg assistant";
      wrap.innerHTML = '<div class="role">教练</div><div class="bubble"></div>';
      $("messages").appendChild(wrap);
      const bubble = wrap.querySelector(".bubble");
      scrollToBottom();

      let buffer = "";
      try {
        const resp = await fetch("/api/chat/stream", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ session_id: sessionId, message: text }),
        });
        if (!resp.ok || !resp.body) {
          bubble.textContent = "请求失败，请稍后重试。";
          return;
        }

        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let remainder = "";
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          remainder += decoder.decode(value, { stream: true });
          const lines = remainder.split("\n");
          remainder = lines.pop();
          for (const line of lines) {
            if (!line.startsWith("data: ")) continue;
            const payload = line.slice(6).trim();
            if (!payload) continue;
            let event;
            try {
              event = JSON.parse(payload);
            } catch {
              continue;
            }
            if (event.delta) {
              buffer += event.delta;
              bubble.textContent = buffer;
              scrollToBottom();
            } else if (event.done) {
              bubble.innerHTML = event.html;
              scrollToBottom();
            } else if (event.error) {
              bubble.textContent = "请求失败：" + event.error;
            }
          }
        }
      } catch (err) {
        bubble.textContent = "请求失败：" + err.message;
      } finally {
        $("sendBtn").disabled = false;
        $("messageInput").focus();
      }
    }

    $("sendBtn").addEventListener("click", sendMessage);
    $("messageInput").addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        sendMessage();
      }
    });

    $("resumeBtn").addEventListener("click", async () => {
      const id = $("sessionSelect").value;
      if (!id) return;
      $("resumeBtn").disabled = true;
      $("historyError").textContent = "";
      try {
        const resp = await fetch("/api/session/resume", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ id }),
        });
        const data = await resp.json();
        if (!resp.ok) {
          $("historyError").textContent = data.error || "继续失败";
          return;
        }
        sessionId = data.session_id;
        $("messages").innerHTML = "";
        (data.messages || []).forEach((message) => {
          if (message.role === "assistant") {
            appendAssistant(message.html || escapeHtml(message.content));
          } else {
            appendUser(message.content);
          }
        });
        showSection("chatSection");
        $("messageInput").focus();
      } catch (err) {
        $("historyError").textContent = "继续失败：" + err.message;
      } finally {
        $("resumeBtn").disabled = false;
      }
    });

    $("summaryBtn").addEventListener("click", async () => {
      if (!sessionId) return;
      $("summaryBtn").disabled = true;
      appendNote("正在生成总结……");
      try {
        const resp = await fetch("/api/summary", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ session_id: sessionId }),
        });
        const data = await resp.json();
        if (!resp.ok) {
          appendNote(data.error || "生成总结失败");
          return;
        }
        appendAssistant(data.html);
        appendNote("总结已保存，下次学习同一章节会自动回顾薄弱点。");
      } catch (err) {
        appendNote("生成总结失败：" + err.message);
      } finally {
        $("summaryBtn").disabled = false;
      }
    });

    $("endBtn").addEventListener("click", async () => {
      if (!sessionId) return;
      $("endBtn").disabled = true;
      try {
        const resp = await fetch("/api/session/end", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ session_id: sessionId }),
        });
        const data = await resp.json();
        if (!resp.ok) {
          appendNote(data.error || "结束失败");
          return;
        }
        appendNote("本次会话已保存，学习结束。");
        sessionId = null;
        setTimeout(() => {
          $("chatSection").classList.add("hidden");
          $("setup").classList.remove("hidden");
        }, 600);
      } catch (err) {
        appendNote("结束失败：" + err.message);
      } finally {
        $("endBtn").disabled = false;
      }
    });

    const savedExplainLevel = localStorage.getItem("explainLevel");
    if (savedExplainLevel) $("explainLevel").value = savedExplainLevel;

    loadShelf();
  </script>
</body>
</html>
"""


if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    app.run(host=host, port=port, debug=False, threaded=True)
