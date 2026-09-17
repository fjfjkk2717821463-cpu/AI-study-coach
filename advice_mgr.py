# advice_mgr.py
"""M6：教师侧 AI 教学建议的提示词、解析与数字校验。

职责划分：
- classroom_mgr 负责"数据从哪来"；
- 本模块负责"怎么问模型"和"怎么校验它没胡说"，全部是纯函数，便于单元测试；
- app.py 只负责把两者接起来，不掺教学逻辑。

设计底线（对应申报书 6.4 与 M6.2）：
1. 模型输出里的每个学生人数，一律以统计结果为准，不一致就直接改写；
2. 概念必须来自"待巩固概念清单"，编造的概念整张卡片丢弃；
3. 学生原话证据只能照抄统计里的原句，对不上就留空并标注"依据：概念频次"；
4. 解析或校验失败时重试一次，仍失败则降级为纯统计版本，绝不编造内容。
"""

import json
import re

DEFAULT_TOP_N = 3
MAX_TOP_N = 5
MIN_REPORTED_STUDENTS = 3

MAX_CHAPTER_CHARS = 12000
MAX_EVIDENCE_PER_CONCEPT = 3
FIELD_MAX_CHARS = 120
ACTION_MAX_CHARS = 200
MAX_CLASS_ACTIONS = 3
MAX_ATTEMPTS = 2

NOTE = "本建议仅依据已上报的学习摘要生成，教师可自行调整。"
CAVEAT = "若课堂真实表现与上述判断不一致，以课堂表现与教材为准。"

SYSTEM_PROMPT = (
    "你是一位严谨的教学设计助手，服务对象是任课教师。"
    "你的任务是把班级学情数据转换成下一次课可以直接执行的教学动作。"
    "铁律：只依据给定的统计数据与教材原文作答；"
    "不得编造学生人数、学生原话、概念名称或统计数据；"
    "只输出 JSON，不要任何解释文字。"
)

OUTPUT_SCHEMA = """{
  "chapter": "本次涉及的章节名称",
  "concepts": [
    {
      "name": "概念名称（必须来自待巩固概念清单）",
      "struggling_count": 12,
      "misconception": "学生的典型误区，一句话",
      "evidence": [{"quote": "只能照抄给定的学生原话，不得改写"}],
      "explain_action": "教师讲解时的切入动作，一句话",
      "activity": "课堂上可以做的活动，一句话",
      "check_question": "当堂检验是否讲通的追问，一句话"
    }
  ],
  "class_actions": ["面向全班的一句话提醒，最多 3 条"]
}"""


# ---------------------------------------------------------------- 提示词

def _trim_stats(stats):
    """只把模型真正需要的信息送进上下文，减少噪声与 token 消耗。"""
    stats = stats or {}
    return {
        "class_name": (stats.get("class") or {}).get("name", ""),
        "students_total": stats.get("students_total", 0),
        "students_reported": stats.get("students_reported", 0),
        "avg_minutes": stats.get("avg_minutes", 0),
        "assignments": [
            {
                "book_name": item.get("book_name", ""),
                "chapters": item.get("chapters") or [],
                "completed_count": item.get("completed_count", 0),
                "pending_count": item.get("pending_count", 0),
            }
            for item in stats.get("assignments") or []
        ],
        "weak_concepts": [
            {
                "term": item.get("term", ""),
                "struggling_count": item.get("count", 0),
                "chapters": item.get("chapters") or [],
            }
            for item in stats.get("weak_concepts") or []
        ],
    }


def _trim_evidence(stats):
    lines = []
    for item in stats.get("weak_concepts") or []:
        term = item.get("term", "")
        for ev in item.get("evidence") or []:
            quote = ev.get("quote") or ""
            if term and quote:
                lines.append(f'- {term}："{quote}"')
    return lines


def build_messages(stats, chapter_text="", top_n=DEFAULT_TOP_N, reminder=""):
    """组装发给模型的 messages。"""
    top_n = max(1, min(int(top_n or DEFAULT_TOP_N), MAX_TOP_N))
    evidence_lines = _trim_evidence(stats)
    evidence_block = "\n".join(evidence_lines) if evidence_lines else "（本次没有可用的学生原话证据）"

    prompt = (
        "请为下面这个班级生成下一次课的教学建议。\n\n"
        f"【班级学情统计】\n{json.dumps(_trim_stats(stats), ensure_ascii=False, indent=2)}\n\n"
        f"【待巩固概念清单（只能从中选择，按卡住人数降序最多选 {top_n} 个）】\n"
        + "\n".join(
            f'- {item.get("term", "")}：{item.get("count", 0)} 人'
            for item in stats.get("weak_concepts") or []
        )[:4000]
        + f"\n\n【学生原话证据（只能整句照抄，不得改写；没有就留空数组）】\n{evidence_block}\n\n"
        f"【教材原文（节选，用于判断误区与设计活动）】\n{(chapter_text or '（本次没有提供教材原文）')[:MAX_CHAPTER_CHARS]}\n\n"
        "【输出要求】\n"
        f"1. concepts 只能从上方的待巩固概念清单里选，最多 {top_n} 个，按卡住人数从多到少排列；\n"
        "2. struggling_count 必须照抄统计里的数字，不得推测；\n"
        "3. evidence 只能整句照抄上方给出的学生原话；该概念没有证据时填空数组；\n"
        "4. 每个概念的 misconception / explain_action / activity / check_question "
        "都要具体、可执行，每项不超过 60 字，用中文；\n"
        "5. class_actions 最多 3 条，只在与上方统计一致时才提到人数；\n"
        "6. 只输出 JSON，且严格符合下面的结构，不要输出 Markdown 代码块标记：\n"
        f"{OUTPUT_SCHEMA}"
    )
    if reminder:
        prompt += f"\n\n【补充要求】{reminder}"

    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]


# ---------------------------------------------------------------- 解析

def parse_advice_json(reply):
    """从模型输出里稳健地提取 JSON；失败返回 None。"""
    text = (reply or "").strip()
    if not text:
        return None
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


# ---------------------------------------------------------------- 校验

def _clean_text(value, limit=FIELD_MAX_CHARS):
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value).strip()[:limit]


def _normalize_quote(value):
    return re.sub(r"\s+", "", value or "")


def _check_evidence(raw_evidence, stat_item):
    """只保留统计里真实存在的学生原句，并以其原文为准。

    返回 (证据列表, 依据说明)。证据对不上时依据改为"概念频次"，
    这样教师在看到空证据时能立刻知道这条建议的依据是什么。
    """
    allowed = [
        ev.get("quote") or "" for ev in (stat_item.get("evidence") or []) if ev.get("quote")
    ]
    if not allowed:
        return [], "概念频次"

    kept = []
    for raw in raw_evidence or []:
        quote = raw.get("quote") if isinstance(raw, dict) else raw
        target = _normalize_quote(quote)
        if not target:
            continue
        for original in allowed:
            normalized = _normalize_quote(original)
            if normalized and (target in normalized or normalized in target):
                if original not in kept:
                    kept.append(original)
                break
        if len(kept) >= MAX_EVIDENCE_PER_CONCEPT:
            break
    return kept, ("学生原话" if kept else "概念频次")


def _check_class_actions(raw_actions, stats, issues):
    """班级提醒里只要出现人数，就必须与统计一致，否则整条丢弃。"""
    pending_total = sum(
        int(item.get("pending_count") or 0) for item in stats.get("assignments") or []
    )
    result = []
    for raw in raw_actions or []:
        text = _clean_text(raw, ACTION_MAX_CHARS)
        if not text:
            continue
        claims = re.findall(r"(\d+)\s*人\s*(?:未完成|未开始|未提交|没完成)", text)
        if claims and any(int(value) != pending_total for value in claims):
            issues.append(f"action_count_mismatch:{'/'.join(claims)}->{pending_total}")
            continue
        result.append(text)
        if len(result) >= MAX_CLASS_ACTIONS:
            break
    return result


def _true_based_on(stats, concept_count):
    return {
        "class_name": (stats.get("class") or {}).get("name", ""),
        "students_total": int(stats.get("students_total") or 0),
        "students_reported": int(stats.get("students_reported") or 0),
        "weak_concept_total": len(stats.get("weak_concepts") or []),
        "concepts_advised": concept_count,
        "generated_at": stats.get("generated_at") or "",
    }


def build_default_actions(stats):
    """不依赖模型也能给出一条可靠的班级提醒。"""
    pending_total = sum(
        int(item.get("pending_count") or 0) for item in stats.get("assignments") or []
    )
    actions = []
    if pending_total:
        actions.append(f"本班 {pending_total} 人未完成任务，建议课前提醒。")
    return actions


def validate_advice(advice, stats, top_n=DEFAULT_TOP_N):
    """把模型输出对齐到真实统计，返回 (清洗后的建议, 问题清单)。"""
    issues = []
    weak_list = [
        item for item in stats.get("weak_concepts") or [] if item.get("term")
    ]
    weak_map = {item["term"]: item for item in weak_list}

    concepts = []
    seen = set()
    for raw in (advice or {}).get("concepts") or []:
        if not isinstance(raw, dict):
            continue
        name = _clean_text(raw.get("name"), 60)
        if not name or name in seen:
            continue
        stat_item = weak_map.get(name)
        if stat_item is None:
            issues.append(f"unknown_concept:{name}")
            continue
        seen.add(name)

        claimed = raw.get("struggling_count")
        true_count = int(stat_item.get("count") or 0)
        if claimed != true_count:
            issues.append(f"count_fixed:{name}:{claimed}->{true_count}")

        evidence, basis = _check_evidence(raw.get("evidence"), stat_item)
        concepts.append(
            {
                "name": name,
                "struggling_count": true_count,
                "misconception": _clean_text(raw.get("misconception")),
                "evidence": evidence,
                "evidence_basis": basis,
                "explain_action": _clean_text(raw.get("explain_action")),
                "activity": _clean_text(raw.get("activity")),
                "check_question": _clean_text(raw.get("check_question")),
                "chapters": list(stat_item.get("chapters") or []),
            }
        )

    concepts.sort(key=lambda item: (-item["struggling_count"], item["name"]))
    top_n = max(1, min(int(top_n or DEFAULT_TOP_N), MAX_TOP_N))
    if len(concepts) > top_n:
        issues.append(f"truncated_to_top_n:{len(concepts)}->{top_n}")
        concepts = concepts[:top_n]

    class_actions = _check_class_actions((advice or {}).get("class_actions"), stats, issues)
    if not class_actions:
        class_actions = build_default_actions(stats)

    cleaned = {
        "chapter": _clean_text((advice or {}).get("chapter"), 120)
        or _guess_scope_label(stats),
        "based_on": _true_based_on(stats, len(concepts)),
        "note": NOTE,
        "concepts": concepts,
        "class_actions": class_actions,
        "caveat": CAVEAT,
        "degraded": False,
        "degraded_reason": "",
    }
    return cleaned, issues


def _guess_scope_label(stats):
    chapters = stats.get("chapters") or []
    if chapters:
        return "、".join(chapters[:3])
    for item in stats.get("weak_concepts") or []:
        if item.get("chapters"):
            return "、".join(item["chapters"][:3])
    return stats.get("book_name") or ""


# ---------------------------------------------------------------- 降级

def build_fallback_advice(stats, top_n=DEFAULT_TOP_N, reason="model_unavailable"):
    """不调用模型也能交付的版本：只呈现统计与证据，不编造教学建议。"""
    top_n = max(1, min(int(top_n or DEFAULT_TOP_N), MAX_TOP_N))
    concepts = []
    for item in (stats.get("weak_concepts") or [])[:top_n]:
        evidence = [
            ev.get("quote") or ""
            for ev in (item.get("evidence") or [])
            if ev.get("quote")
        ][:MAX_EVIDENCE_PER_CONCEPT]
        concepts.append(
            {
                "name": item.get("term") or "",
                "struggling_count": int(item.get("count") or 0),
                "misconception": "",
                "evidence": evidence,
                "evidence_basis": "学生原话" if evidence else "概念频次",
                "explain_action": "",
                "activity": "",
                "check_question": "",
                "chapters": list(item.get("chapters") or []),
            }
        )
    return {
        "chapter": _guess_scope_label(stats),
        "based_on": _true_based_on(stats, len(concepts)),
        "note": NOTE,
        "concepts": concepts,
        "class_actions": build_default_actions(stats),
        "caveat": CAVEAT,
        "degraded": True,
        "degraded_reason": reason,
    }


# ---------------------------------------------------------------- 编排

def generate_advice(stats, chapter_text, call_fn, top_n=DEFAULT_TOP_N, max_attempts=MAX_ATTEMPTS):
    """生成教学建议：调用模型 → 解析 → 校验 → 失败重试 → 降级。

    call_fn 由调用方注入（app.py 传 coach_v4._request_chat），
    这样本模块不依赖网络，可以直接用假函数做单元测试。
    返回 (建议, 元信息)，元信息里记录了尝试次数、降级原因与校验问题。
    """
    meta = {"attempts": 0, "degraded": False, "issues": [], "usage": {}}
    reminder = ""

    for attempt in range(1, max(1, max_attempts) + 1):
        meta["attempts"] = attempt
        messages = build_messages(stats, chapter_text, top_n, reminder)
        try:
            reply = call_fn(messages, stream=False)
        except TypeError:
            reply = call_fn(messages)
        except Exception as exc:  # 网络/鉴权等异常统一走降级
            meta["issues"].append(f"call_failed:{exc}")
            break

        parsed = parse_advice_json(reply)
        if not parsed:
            meta["issues"].append("invalid_json")
            reminder = "上一次输出不是合法 JSON。请只输出 JSON 对象本身，不要代码块标记。"
            continue

        cleaned, issues = validate_advice(parsed, stats, top_n)
        meta["issues"].extend(issues)
        if cleaned["concepts"]:
            return cleaned, meta
        reminder = "上一次输出里没有任何有效的待巩固概念，请严格从清单中选择。"

    meta["degraded"] = True
    reason = meta["issues"][-1] if meta["issues"] else "model_unavailable"
    return build_fallback_advice(stats, top_n, reason=reason), meta
