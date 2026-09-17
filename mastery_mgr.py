# mastery_mgr.py
"""掌握度判定：复述质量四维评分、默写覆盖率与达标结论。

这个模块解决一个教学效度问题：教师端此前的「薄弱概念」来自模型对学习总结的
自我判断，而不是学生的实际表现。这里把「学生说不出、写不全」变成可计算的东西。

三层证据，全部可核验：
1. 复述质量四维评分（概念覆盖 / 准确性 / 逻辑与边界 / 通俗化表达），0-3 分每维，
   由模型初评、程序校验：分数越界会被改写，每维依据必须是学生复述里的原句。
2. 默写覆盖率：把学生凭记忆写的内容与教材原文比对，纯本地计算，不调用模型。
3. 间隔复习重测结果：来自 review_mgr 的 quality。

三条里过两条才算「已达标」，其余为「已学习」。判定规则写死在代码里，
避免不同班级出现不同口径。
"""

import json
import os
import re
import time

import app_paths

MASTERY_DIR = os.path.join(app_paths.get_data_dir(), "mastery")

DIMENSIONS = (
    ("coverage", "概念覆盖"),
    ("accuracy", "准确性"),
    ("reasoning", "逻辑与边界"),
    ("expression", "通俗化表达"),
)
DIM_KEYS = tuple(key for key, _ in DIMENSIONS)
DIM_LABELS = dict(DIMENSIONS)
MAX_PER_DIM = 3
FULL_SCORE = MAX_PER_DIM * len(DIMENSIONS)

MASTERY_MIN_TOTAL = 7          # 复述达标线（满分 12）
DICTATION_MIN_COVERAGE = 0.5   # 默写覆盖率达标线
REVIEW_MIN_QUALITY = 2         # 间隔复习重测达标线（1-3）

HIGH_COPY_RATIO = 0.55         # 复述与原文重合超过这个比例，视为照读教材
COPY_PENALTY_CAP = 1           # 照读时「通俗化表达」封顶分

NGram = 5
SENTENCE_MIN_CHARS = 12
SENTENCE_HIT_RATIO = 0.4

MAX_RETELL_CHARS = 4000
MAX_CHAPTER_CHARS = 8000
FIELD_MAX_CHARS = 160

STATUS_NOT_STARTED = "未开始"
STATUS_LEARNED = "已学习"
STATUS_MASTERED = "已达标"

SYSTEM_PROMPT = (
    "你是一位严格但有耐心的学科教师，正在评价学生用自己的话复述一个概念的录音或文字。"
    "你只依据给定的教材原文和学生复述原文评分，不得编造学生说过的话。"
    "只输出 JSON，不要任何解释文字。"
)

OUTPUT_SCHEMA = """{
  "scores": {
    "coverage": {"score": 2, "evidence": "只能照抄学生复述里的原句", "comment": "一句话点评"},
    "accuracy": {"score": 2, "evidence": "...", "comment": "..."},
    "reasoning": {"score": 1, "evidence": "...", "comment": "..."},
    "expression": {"score": 2, "evidence": "...", "comment": "..."}
  },
  "summary": "三四句话的整体评价，先说好的地方，再指出最该补的一个缺口"
}"""


# ---------------------------------------------------------------- 文本比对

def normalize_text(text):
    """去掉空白与标点，只保留中英文数字，便于比对。"""
    return re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", text or "")


def _ngrams(text, size=NGram):
    clean = normalize_text(text)
    if len(clean) < size:
        return {clean} if clean else set()
    return {clean[i:i + size] for i in range(len(clean) - size + 1)}


def copy_ratio(retell_text, chapter_text):
    """复述中有多少比例与教材原文重合；越接近 1 越像照读教材。"""
    retell = _ngrams(retell_text)
    if not retell:
        return 0.0
    chapter = _ngrams(chapter_text)
    if not chapter:
        return 0.0
    return round(len(retell & chapter) / len(retell), 3)


def coverage_ratio(dictation_text, chapter_text):
    """默写覆盖率：教材原文里有多少比例的句子被学生写到了。

    纯本地计算，不调用模型：把原文切成句子，每句拆成 5-gram，
    只要有四成以上出现在学生的默写里，就算这句话被覆盖到。
    """
    student = _ngrams(dictation_text)
    if not student:
        return 0.0
    sentences = [
        s.strip()
        for s in re.split(r"[。！？!?；;\n]", chapter_text or "")
        if len(normalize_text(s)) >= SENTENCE_MIN_CHARS
    ]
    if not sentences:
        return 0.0
    hit = 0
    for sentence in sentences:
        grams = _ngrams(sentence)
        if not grams:
            continue
        if len(grams & student) / len(grams) >= SENTENCE_HIT_RATIO:
            hit += 1
    return round(hit / len(sentences), 3)


# ---------------------------------------------------------------- 复述评分

def build_messages(chapter_title, chapter_text, concepts, retell_text, phase=""):
    concept_lines = "\n".join(
        "- " + str(item.get("term") if isinstance(item, dict) else item)
        for item in (concepts or [])[:12]
    ) or "（未提供概念清单）"
    phase_text = phase or "未标注"
    prompt = (
        "请按四个维度给下面这段学生复述打分，每个维度 0 到 3 分（整数）。\n\n"
        "评分标准：0 分=完全没有做到；1 分=提到了但有明显错误或严重缺失；"
        "2 分=基本正确但不完整或仍依赖背诵；3 分=完整、准确、用自己的话讲清楚并能指出边界。\n\n"
        f"【章节】{chapter_title or '未命名章节'}（复述阶段：{phase_text}）\n\n"
        f"【本章核心概念】\n{concept_lines}\n\n"
        f"【教材原文（评分基准）】\n{(chapter_text or '（本次没有教材原文，请只依据概念清单判断）')[:MAX_CHAPTER_CHARS]}\n\n"
        f"【学生复述原文（不得改写）】\n{(retell_text or '')[:MAX_RETELL_CHARS]}\n\n"
        "【输出要求】\n"
        "1. 每个维度给出 score、evidence、comment；evidence 必须整句照抄上面的学生复述原文，"
        "学生确实没说到的维度就留空字符串；\n"
        "2. comment 不超过 40 字，指出这一点为什么得这个分；\n"
        "3. 如果学生基本在照读教材原文、没有用自己的话，通俗化表达最多给 1 分；\n"
        "4. 严格按照下面的 JSON 结构输出，不要输出 Markdown 代码块标记：\n"
        f"{OUTPUT_SCHEMA}"
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]


def parse_scores_json(reply):
    text = (reply or "").strip()
    if not text:
        return None
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _clean(text, limit=FIELD_MAX_CHARS):
    if not isinstance(text, str):
        return ""
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _quote_in_retell(quote, retell_text):
    """依据必须是学生复述里的原句，否则不采信。"""
    target = normalize_text(quote)
    if not target:
        return False
    return target in normalize_text(retell_text)


def validate_scores(raw, retell_text, chapter_text=""):
    """清洗模型评分：分数取整并限制在 0-3，总分由程序相加，依据必须可核验。"""
    issues = []
    scores = {}
    raw_scores = (raw or {}).get("scores") or {}
    for key in DIM_KEYS:
        item = raw_scores.get(key) if isinstance(raw_scores, dict) else None
        item = item if isinstance(item, dict) else {}
        value = item.get("score")
        try:
            number = int(round(float(value)))
        except (TypeError, ValueError):
            number = 0
            issues.append(f"score_missing:{key}")
        if number < 0 or number > MAX_PER_DIM:
            issues.append(f"score_clamped:{key}:{value}->{max(0, min(MAX_PER_DIM, number))}")
            number = max(0, min(MAX_PER_DIM, number))
        evidence = _clean(item.get("evidence"))
        if evidence and not _quote_in_retell(evidence, retell_text):
            issues.append(f"evidence_dropped:{key}")
            evidence = ""
        scores[key] = {
            "label": DIM_LABELS[key],
            "score": number,
            "evidence": evidence,
            "comment": _clean(item.get("comment"), 80),
        }

    ratio = copy_ratio(retell_text, chapter_text) if chapter_text else 0.0
    if chapter_text and ratio >= HIGH_COPY_RATIO and scores["expression"]["score"] > COPY_PENALTY_CAP:
        issues.append(f"copy_penalty:expression->{COPY_PENALTY_CAP}")
        scores["expression"]["score"] = COPY_PENALTY_CAP
        if not scores["expression"]["comment"]:
            scores["expression"]["comment"] = "内容与教材原文高度重合，请用自己的话重讲一遍。"

    total = sum(item["score"] for item in scores.values())
    return (
        {
            "scores": scores,
            "total": total,
            "full_score": FULL_SCORE,
            "copy_ratio": ratio,
            "summary": _clean((raw or {}).get("summary"), 300),
        },
        issues,
    )


def score_retell(chapter_title, chapter_text, concepts, retell_text, call_fn,
                 phase="", max_attempts=2):
    """复述评分：调用模型 → 解析 → 校验 → 失败重试 → 失败即不产出分数。"""
    meta = {"attempts": 0, "issues": [], "degraded": False}
    text = _clean(retell_text, MAX_RETELL_CHARS)
    if len(normalize_text(text)) < 10:
        meta["degraded"] = True
        meta["issues"].append("retell_too_short")
        return None, meta

    reminder = ""
    for attempt in range(1, max(1, max_attempts) + 1):
        meta["attempts"] = attempt
        messages = build_messages(chapter_title, chapter_text, concepts, text, phase)
        if reminder:
            messages[1]["content"] += f"\n\n【补充要求】{reminder}"
        try:
            reply = call_fn(messages, stream=False)
        except TypeError:
            reply = call_fn(messages)
        except Exception as exc:
            meta["issues"].append(f"call_failed:{exc}")
            break

        parsed = parse_scores_json(reply)
        if not parsed:
            meta["issues"].append("invalid_json")
            reminder = "上一次输出不是合法 JSON，请只输出 JSON 对象本身。"
            continue

        result, issues = validate_scores(parsed, text, chapter_text)
        meta["issues"].extend(issues)
        result["at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        result["phase"] = _clean(phase, 20)
        result["text_length"] = len(text)
        return result, meta

    meta["degraded"] = True
    return None, meta


# ---------------------------------------------------------------- 存储

def _sanitize(name):
    text = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", (name or "").strip())
    return text.strip(" ._")[:80] or "untitled"


def record_path(book_name, chapter_title):
    base = os.path.join(MASTERY_DIR, f"{_sanitize(book_name)}_{_sanitize(chapter_title)}.json")
    return base


def _load(book_name, chapter_title):
    path = record_path(book_name, chapter_title)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("book_name", book_name)
    data.setdefault("chapter_title", chapter_title)
    data.setdefault("retells", [])
    data.setdefault("dictations", [])
    return data


def _save(data):
    path = record_path(data.get("book_name", ""), data.get("chapter_title", ""))
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
        return True
    except OSError:
        return False


def save_retell(book_name, chapter_title, entry):
    data = _load(book_name, chapter_title)
    data["retells"].append(entry)
    data["updated_at"] = entry.get("at") or time.strftime("%Y-%m-%d %H:%M:%S")
    return _save(data)


def save_dictation(book_name, chapter_title, entry):
    data = _load(book_name, chapter_title)
    data["dictations"].append(entry)
    data["updated_at"] = entry.get("at") or time.strftime("%Y-%m-%d %H:%M:%S")
    return _save(data)


def latest_retell(book_name, chapter_title):
    items = _load(book_name, chapter_title).get("retells") or []
    return items[-1] if items else None


def latest_dictation(book_name, chapter_title):
    items = _load(book_name, chapter_title).get("dictations") or []
    return items[-1] if items else None


def retell_trend(book_name, chapter_title, limit=6):
    items = (_load(book_name, chapter_title).get("retells") or [])[-limit:]
    return [
        {"at": item.get("at", ""), "total": item.get("total", 0), "phase": item.get("phase", "")}
        for item in items
    ]


def record_dictation(book_name, chapter_title, dictation_text, chapter_text=""):
    """记录一次默写，覆盖率本地计算。"""
    entry = {
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "coverage": coverage_ratio(dictation_text, chapter_text),
        "text_length": len(dictation_text or ""),
    }
    save_dictation(book_name, chapter_title, entry)
    return entry


# ---------------------------------------------------------------- 达标判定

def mastery_verdict(retell=None, dictation=None, review_quality=None):
    """三条证据过两条即为达标，规则固定，全项目统一口径。"""
    checks = {
        "复述质量": bool(retell) and int(retell.get("total") or 0) >= MASTERY_MIN_TOTAL,
        "默写覆盖": bool(dictation)
        and float(dictation.get("coverage") or 0) >= DICTATION_MIN_COVERAGE,
        "复习重测": isinstance(review_quality, int) and review_quality >= REVIEW_MIN_QUALITY,
    }
    passed = [name for name, ok in checks.items() if ok]
    missing = [name for name, ok in checks.items() if not ok]
    if not any(checks.values()):
        status = STATUS_LEARNED
    elif len(passed) >= 2:
        status = STATUS_MASTERED
    else:
        status = STATUS_LEARNED
    return {
        "status": status,
        "passed": passed,
        "missing": missing,
        "rule": "复述质量、默写覆盖、复习重测三项过两项",
        "checks": checks,
    }


def chapter_mastery(book_name, chapter_title, review_quality=None):
    """读取本地记录，给出这一章的达标状态与三项证据明细。"""
    retell = latest_retell(book_name, chapter_title)
    dictation = latest_dictation(book_name, chapter_title)
    verdict = mastery_verdict(retell, dictation, review_quality)
    return {
        "verdict": verdict,
        "retell": retell,
        "dictation": dictation,
        "trend": retell_trend(book_name, chapter_title),
        "thresholds": {
            "retell_total": MASTERY_MIN_TOTAL,
            "dictation_coverage": DICTATION_MIN_COVERAGE,
            "review_quality": REVIEW_MIN_QUALITY,
        },
    }
