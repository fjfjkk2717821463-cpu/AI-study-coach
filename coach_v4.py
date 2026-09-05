# coach_v4.py
import json
import os
import sys
import time

import requests

import app_paths
import book_utils
import bookshelf_mgr
import session_mgr
import summary_mgr

# ========== 配置区 ==========
# 推荐使用环境变量 DEEPSEEK_API_KEY，也可以在当前目录创建 .env 文件：
#   DEEPSEEK_API_KEY=sk-xxx
BASE_URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-chat"
TEMPERATURE = 0.7
REQUEST_TIMEOUT = 90
MAX_RETRIES = 3
MAX_CHAPTER_CHARS = 12000
# ============================

ENV_FILE = app_paths.migrate_file(".env")


class ApiError(RuntimeError):
    """带是否可重试标记的 API 错误。"""

    def __init__(self, message, retryable=False):
        super().__init__(message)
        self.retryable = retryable


def load_api_key():
    """从环境变量或本地 .env 文件读取 API Key，避免把密钥硬编码进源码。"""
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if key:
        return key

    if os.path.exists(ENV_FILE):
        try:
            with open(ENV_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    name, value = line.split("=", 1)
                    if name.strip() == "DEEPSEEK_API_KEY":
                        key = value.strip().strip('"').strip("'")
                        if key:
                            return key
        except OSError:
            pass
    return ""


# ---------- 提示词模板 ----------
SYSTEM_PROMPT_BOOK = """
你是「刻意摩擦学习法」私人学习教练，当前为**电子书模式**。
你的教学必须分为两个阶段，严格按顺序推进，不能颠倒：
【阶段一：概念精讲】先基于原文把本章内容准确、通俗地教一遍；
【阶段二：费曼检测】确认我初步掌握后，再用费曼式输出检验并加深。

你正在辅导的章节：{chapter_title}
{review_section}
【总原则】
- 提问必须建立在讲解之后：未经阶段一讲解，不得进入阶段二的考试式追问。
- 阶段一以「讲解」为主，阶段二以「我的输出」为主，两个阶段分工清晰，不要混在一起。
- 讲解要基于原文、保证准确：用定义、直觉、多个例子、反例或边界、常见误区把每个概念讲透，并给出原文出处。
- 每次回复开头标注阶段和环节，例如【阶段一·概念精讲：熵的定义】或【阶段二·费曼检测：复述】。
- 不要一次抛出多个问题；每个环节只给 1 个问题或 1 个指令。

【阶段一：概念精讲】
1. 知识地图：先给出本章概念清单和逻辑主线。
2. 逐个概念讲解：定义 → 直觉 → 2-3 个例子 → 1 个反例或边界情况 → 常见误区 → 原文出处。
3. 每讲完一个概念，只问一个轻量确认问题，例如「这部分能跟上吗？要我换个角度再讲一遍、多给一个例子，还是继续下一个概念？」
4. 节奏由我决定：我可以随时要求「再讲一遍」「多给一个例子」「继续」。在我明确说「可以了 / 开始检测」之前，不要进入阶段二。

【阶段二：费曼检测】
只有在我表示初步掌握后，才依次进行：
- 步骤一 复述：我合上材料用大白话复述，你指出遗漏和偏差。
- 步骤二 反例与边界：你给我一个反例，追问「什么情况下不成立」。
- 步骤三 逻辑显形：我画逻辑图，你审查逻辑链并建议更通俗的表达。
- 步骤四 多维对撞：你扮演考官连续追问，并提醒我对玩偶出声讲解、录音复盘。
- 步骤五 默写比对：我凭记忆重构，你逐段对照下方原文，指出遗漏、偏差和精彩点。

【讲解强度】
{intensity_rule}

【章节原文】（只有电子书模式才有，请精准引用）
---
{chapter_text}
---
"""

SYSTEM_PROMPT_OUTLINE = """
你是「刻意摩擦学习法」私人学习教练，当前为**目录速建模式**（无原文参考）。
你的教学必须分为两个阶段，严格按顺序推进，不能颠倒：
【阶段一：概念精讲】先基于领域知识把主题准确、通俗地教一遍；
【阶段二：费曼检测】确认我初步掌握后，再用费曼式输出检验并加深。

你正在辅导的主题：{outline_title}
{review_section}
【总原则】
- 提问必须建立在讲解之后：未经阶段一讲解，不得进入阶段二的考试式追问。
- 阶段一以「讲解」为主，阶段二以「我的输出」为主，两个阶段分工清晰，不要混在一起。
- 讲解要基于你的领域知识并保持准确：用定义、直觉、多个例子、反例或边界、常见误区讲透每个概念；不确定的地方明确说明。
- 每次回复开头标注阶段和环节，例如【阶段一·概念精讲：定义】或【阶段二·费曼检测：反例】。
- 不要一次抛出多个问题；每个环节只给 1 个问题或 1 个指令。

【阶段一：概念精讲】
1. 知识地图：先给出主题概念清单和逻辑主线。
2. 逐个概念讲解：定义 → 直觉 → 2-3 个例子 → 1 个反例或边界情况 → 常见误区。
3. 每讲完一个概念，只问一个轻量确认问题，例如「这部分能跟上吗？要我换个角度再讲一遍、多给一个例子，还是继续下一个概念？」
4. 节奏由我决定：我可以随时要求「再讲一遍」「多给一个例子」「继续」。在我明确说「可以了 / 开始检测」之前，不要进入阶段二。

【阶段二：费曼检测】
只有在我表示初步掌握后，才依次进行：
- 步骤一 复述：我合上材料用大白话复述，你指出遗漏和偏差。
- 步骤二 反例与边界：你给我一个反例，追问「什么情况下不成立」。
- 步骤三 逻辑显形：我画逻辑图，你审查逻辑链并建议更通俗的表达。
- 步骤四 多维对撞：你扮演考官连续追问，并提醒我对玩偶出声讲解、录音复盘。
- 步骤五 默写重构：我凭记忆重构，你根据你的知识指出可能的遗漏或逻辑矛盾。

【讲解强度】
{intensity_rule}
"""


INTENSITY_RULES = {
    "low": "阶段一讲解精炼，只讲定义和核心直觉；阶段二检测更密集、追问更严格。",
    "medium": "阶段一讲解完整：定义、直觉和至少 2 个例子；阶段二按标准费曼流程适度检测。",
    "high": "阶段一讲解充分详细：每个概念给 2-3 个例子、1 个反例和常见误区；阶段二检测温和、以鼓励和补漏为主。",
}


def build_system_prompt_book(chapter_title, chapter_text, review_text, intensity="medium"):
    review_section = ""
    if review_text:
        review_section = f"【上次学习总结与薄弱点】\n{review_text}\n\n请在学习开始时先针对这些薄弱点提问检测。"
    intensity_rule = INTENSITY_RULES.get(intensity, INTENSITY_RULES["medium"])
    return SYSTEM_PROMPT_BOOK.format(
        chapter_title=chapter_title,
        review_section=review_section,
        chapter_text=chapter_text,
        intensity_rule=intensity_rule,
    )


def build_system_prompt_outline(outline_title, review_text, intensity="medium"):
    review_section = ""
    if review_text:
        review_section = f"【上次学习总结与薄弱点】\n{review_text}\n\n请在学习开始时先针对这些薄弱点提问检测。"
    intensity_rule = INTENSITY_RULES.get(intensity, INTENSITY_RULES["medium"])
    return SYSTEM_PROMPT_OUTLINE.format(
        outline_title=outline_title,
        review_section=review_section,
        intensity_rule=intensity_rule,
    )


def _extract_reply(data):
    """安全地从非流式响应中提取助手回复。"""
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None


def _parse_stream_line(line):
    """解析一行 SSE 数据，返回增量文本；[DONE] 返回 None。"""
    if not line or line.startswith(":"):
        return ""
    line = line.strip()
    if not line.startswith("data:"):
        return ""
    data = line[len("data:"):].strip()
    if data == "[DONE]":
        return None
    try:
        payload = json.loads(data)
        return payload["choices"][0]["delta"].get("content", "") or ""
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        return ""


def _request_chat(messages, stream=False):
    """发送请求，处理 HTTP 状态码、响应解析、超时/网络重试和流式输出。"""
    payload = {
        "model": MODEL,
        "messages": messages,
        "stream": stream,
        "temperature": TEMPERATURE,
    }
    headers = {
        "Authorization": f"Bearer {load_api_key()}",
        "Content-Type": "application/json",
    }

    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.post(
                BASE_URL,
                headers=headers,
                json=payload,
                timeout=REQUEST_TIMEOUT,
                stream=stream,
            )

            if resp.status_code != 200:
                raise ApiError(
                    f"API 请求失败（HTTP {resp.status_code}）：{resp.text[:500]}",
                    retryable=resp.status_code >= 500,
                )

            if stream:
                parts = []
                for raw_line in resp.iter_lines(decode_unicode=True):
                    delta = _parse_stream_line(raw_line)
                    if delta is None:
                        break
                    if delta:
                        parts.append(delta)
                        print(delta, end="", flush=True)
                return "".join(parts)

            try:
                data = resp.json()
            except ValueError as exc:
                raise ApiError(
                    f"响应不是合法 JSON：{resp.text[:300]}",
                    retryable=False,
                ) from exc
            reply = _extract_reply(data)
            if reply is None:
                raise ApiError(
                    f"响应格式异常：{json.dumps(data, ensure_ascii=False)[:500]}",
                    retryable=False,
                )
            return reply

        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
            last_error = exc
        except ApiError as exc:
            last_error = exc
            if not exc.retryable:
                break
        except requests.exceptions.RequestException as exc:
            last_error = exc
            break

        if attempt < MAX_RETRIES:
            wait = 2 ** attempt
            print(f"\n⚠️ 请求失败，正在第 {attempt}/{MAX_RETRIES} 次重试（等待 {wait}s）...", file=sys.stderr)
            time.sleep(wait)

    raise RuntimeError(str(last_error) if last_error else "未知错误")


def stream_chat(messages):
    """流式返回助手回复的增量文本，适合网页端 SSE 使用。"""
    payload = {
        "model": MODEL,
        "messages": messages,
        "stream": True,
        "temperature": TEMPERATURE,
    }
    headers = {
        "Authorization": f"Bearer {load_api_key()}",
        "Content-Type": "application/json",
    }

    resp = requests.post(
        BASE_URL,
        headers=headers,
        json=payload,
        timeout=REQUEST_TIMEOUT,
        stream=True,
    )
    if resp.status_code != 200:
        raise ApiError(
            f"API 请求失败（HTTP {resp.status_code}）：{resp.text[:500]}",
            retryable=False,
        )

    for raw_line in resp.iter_lines(decode_unicode=True):
        delta = _parse_stream_line(raw_line)
        if delta is None:
            break
        if delta:
            yield delta


def chat_with_coach(user_input, history, system_prompt, stream=True):
    """核心对话：发送请求并返回助手回复，同时更新 history。"""
    if not user_input:
        return ""
    history.append({"role": "user", "content": user_input})
    messages = [{"role": "system", "content": system_prompt}] + history

    try:
        if stream:
            print("教练：", end="")
            reply = _request_chat(messages, stream=True)
            print()
        else:
            reply = _request_chat(messages, stream=False)
        history.append({"role": "assistant", "content": reply})
        return reply
    except Exception as exc:
        print(f"\n【系统错误】{exc}")
        return ""


def generate_summary(history, system_prompt, summary_prompt):
    """生成总结，但不污染主对话 history。"""
    messages = [{"role": "system", "content": system_prompt}] + history + [
        {"role": "user", "content": summary_prompt}
    ]
    try:
        return _request_chat(messages, stream=False)
    except Exception as exc:
        print(f"\n【生成总结失败】{exc}")
        return ""


def start_learning_session_book(book_name, book_path, chapters):
    """电子书模式学习流程"""
    print(f"\n📖 当前电子书：《{book_name}》")
    print("可用章节（前20个）：")
    for i, (title, _) in enumerate(chapters[:20], 1):
        print(f"  {i}. {title}")
    if len(chapters) > 20:
        print("  ...")

    while True:
        query = input("\n请输入章节关键词（如：第四章 / 熵 / 4）：").strip()
        if query:
            break
        print("关键词不能为空，请重新输入。")

    chapter_title, chapter_text = book_utils.get_chapter_text(chapters, query)
    if chapter_text is None:
        print("⚠️ 未找到匹配章节，将使用全文前一部分作为学习材料。")
        chapter_title = "自定义章节"
        full_text, _ = book_utils.load_book(book_path)
        chapter_text = full_text[:MAX_CHAPTER_CHARS] if full_text else ""

    if len(chapter_text) > MAX_CHAPTER_CHARS:
        chapter_text = chapter_text[:MAX_CHAPTER_CHARS]
        print(f"⚠️ 章节较长，已截取前 {MAX_CHAPTER_CHARS} 字用于本轮学习；建议拆成更细的小节。")

    print(f"✅ 定位章节：{chapter_title}，字数：{len(chapter_text)}")

    review = summary_mgr.load_summary(book_name, chapter_title)
    if review:
        print("📝 检测到上次学习总结，教练将先进行薄弱点检测。")

    system_prompt = build_system_prompt_book(chapter_title, chapter_text, review)
    conversation_history = []

    print(f"\n教练【{chapter_title}】：准备就绪。输入“生成总结”保存进度，输入“退出”结束。\n")
    print(f"教练：我们先进入【阶段一：概念精讲】。请回复“开始”，或直接告诉我你想从哪个概念学起。")

    while True:
        user_msg = input("\n你：").strip()
        if not user_msg:
            continue
        if user_msg.lower() == "退出":
            if conversation_history:
                session_mgr.save_session(
                    "电子书",
                    chapter_title,
                    conversation_history,
                    {
                        "mode": "book",
                        "book_name": book_name,
                        "book_path": book_path,
                        "system_prompt": system_prompt,
                    },
                )
            print("教练：今天先到这里，本次对话已自动保存。")
            break
        if user_msg.lower() == "生成总结":
            summary_prompt = """请基于本次完整对话，生成一份结构化学习总结，包含：
1. 已掌握的核心概念
2. 仍然存在的薄弱点和理解漏洞
3. 典型反例或边界情况复盘
4. 下一步复习建议
只输出总结内容。"""
            reply = generate_summary(conversation_history, system_prompt, summary_prompt)
            if reply:
                summary_mgr.save_summary(book_name, chapter_title, reply)
                print(f"\n教练：✅ 总结已保存。下次学习本章节时将自动加载。")
            else:
                print("\n教练：⚠️ 总结未能生成，请稍后重试。")
            continue

        chat_with_coach(user_msg, conversation_history, system_prompt, stream=True)


def start_learning_session_outline():
    """目录速建模式学习流程"""
    while True:
        outline_title = input("\n请输入学习主题/章节标题：").strip()
        if outline_title:
            break
        print("标题不能为空，请重新输入。")

    while True:
        outline_content = input("请粘贴该章节的目录或内容要点：").strip()
        if outline_content:
            break
        print("目录内容不能为空，请重新输入。")

    if len(outline_content) > MAX_CHAPTER_CHARS:
        outline_content = outline_content[:MAX_CHAPTER_CHARS]
        print(f"⚠️ 目录内容较长，已截取前 {MAX_CHAPTER_CHARS} 字。")

    print(f"✅ 已接收目录，字数：{len(outline_content)}")

    review = summary_mgr.load_summary("目录速建", outline_title)
    if review:
        print("📝 检测到上次学习总结，教练将先进行薄弱点检测。")

    system_prompt = build_system_prompt_outline(outline_title, review)
    system_prompt += f"\n\n【用户提供的章节目录/要点】\n{outline_content}"

    conversation_history = []
    print(f"\n教练【{outline_title}】：让我们开始。输入“生成总结”保存进度，输入“退出”结束。")
    print(f"教练：我们先进入【阶段一：概念精讲】。请回复“开始”，我会先给出知识地图，再逐条讲解概念。")

    while True:
        user_msg = input("\n你：").strip()
        if not user_msg:
            continue
        if user_msg.lower() == "退出":
            if conversation_history:
                session_mgr.save_session(
                    "目录速建",
                    outline_title,
                    conversation_history,
                    {
                        "mode": "outline",
                        "book_name": "",
                        "book_path": "",
                        "system_prompt": system_prompt,
                    },
                )
            print("教练：今天的学习结束，本次对话已自动保存。")
            break
        if user_msg.lower() == "生成总结":
            summary_prompt = """请基于本次对话生成结构化学习总结，包含：
1. 已掌握的核心概念
2. 仍存在的薄弱点
3. 反例/边界复盘
4. 下一步建议
只输出总结。"""
            reply = generate_summary(conversation_history, system_prompt, summary_prompt)
            if reply:
                summary_mgr.save_summary("目录速建", outline_title, reply)
                print(f"\n教练：✅ 总结已保存。")
            else:
                print("\n教练：⚠️ 总结未能生成，请稍后重试。")
            continue

        chat_with_coach(user_msg, conversation_history, system_prompt, stream=True)


def manage_bookshelf():
    """书架管理界面"""
    while True:
        shelf = bookshelf_mgr.load_shelf()
        print("\n===== 书架管理 =====")
        if not shelf:
            print("书架目前是空的。")
        else:
            for i, book in enumerate(shelf):
                print(f"{i+1}. {book['name']}   ({book['path']})")
        print("a. 添加书籍")
        print("d. 删除书籍")
        print("q. 返回主菜单")
        choice = input("选择操作：").strip().lower()
        if choice == "q":
            break
        if choice == "a":
            path = input("请输入书籍文件路径：").strip().strip('"')
            if os.path.exists(path):
                name = input("请输入书籍名称（自定义）：").strip()
                bookshelf_mgr.add_book(name, path)
                print("✅ 已添加。")
            else:
                print("❌ 文件不存在。")
        elif choice == "d":
            idx = input("请输入要删除的书籍序号：").strip()
            if idx.isdigit():
                idx = int(idx) - 1
                shelf = bookshelf_mgr.load_shelf()
                if 0 <= idx < len(shelf):
                    bookshelf_mgr.remove_book(shelf[idx]["path"])
                    print("✅ 已删除。")
                else:
                    print("❌ 序号无效。")
            else:
                print("❌ 请输入数字。")
        else:
            print("无效选项。")


def main():
    print("=" * 50)
    print("    📚 刻意摩擦学习教练 V4（多模式·书架·记忆）")
    print("=" * 50)

    if not load_api_key():
        print("⚠️ 请先设置 DeepSeek API 密钥：")
        print("   1. 设置环境变量：export DEEPSEEK_API_KEY=sk-xxx")
        print("   2. 或在程序目录创建 .env 文件，内容为 DEEPSEEK_API_KEY=sk-xxx")
        return

    while True:
        shelf = bookshelf_mgr.load_shelf()
        print("\n===== 主菜单 =====")
        print("1. 选择书架上的书开始学习")
        print("2. 使用目录速建模式（无电子书）")
        print("3. 管理书架")
        print("q. 退出")
        option = input("请输入选项：").strip().lower()

        if option == "q":
            print("再见！")
            break
        if option == "3":
            manage_bookshelf()
            continue
        if option == "2":
            start_learning_session_outline()
            continue
        if option == "1":
            if not shelf:
                print("书架是空的，请先添加书籍（选择3进入书架管理）。")
                continue
            print("\n已保存的书籍：")
            for i, book in enumerate(shelf):
                print(f"{i+1}. {book['name']}")
            print("0. 返回")
            choice = input("请选择书籍序号：").strip()
            if choice == "0":
                continue
            if choice.isdigit():
                idx = int(choice) - 1
                book = bookshelf_mgr.get_book_by_index(shelf, idx)
                if book:
                    print(f"正在加载《{book['name']}》...")
                    chapters, err = book_utils.get_book_chapters(book["path"])
                    if err:
                        print(f"❌ 加载失败：{err}")
                        continue
                    print(f"✅ 加载完成，共检测到 {len(chapters)} 个章节。")
                    start_learning_session_book(book["name"], book["path"], chapters)
                else:
                    print("❌ 无效选择。")
            else:
                print("请输入数字。")
        else:
            print("无效选项，请重新输入。")


if __name__ == "__main__":
    main()
