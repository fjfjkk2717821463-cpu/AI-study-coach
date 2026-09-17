# classroom_mgr.py
"""班级、成员、任务与学情摘要的本地数据层（"师-生-机"协同中"机"的一侧）。

职责边界：
- 纯数据层，不依赖 Flask，便于单元测试；
- 所有函数都不抛异常：文件损坏、字段缺失一律降级为空结果；
- 一个班级一个目录，班级之间天然隔离，教师只能访问自己创建的班级；
- 学生上报只保存摘要、概念与少量原话证据，不保存完整对话。

目录结构（位于用户数据目录下）：
    classroom/
      classes.json                      班级列表
      classes/<class_id>/
        roster.json                     成员名单
        assignments.json                布置的任务
        records/<student_id>.json       学生上报的学习摘要快照
        reports/advice-<scope>.json     M6 生成的教学建议缓存
"""

import hashlib
import json
import os
import re
import secrets
import time

import app_paths

CLASSROOM_DIR = os.path.join(app_paths.get_data_dir(), "classroom")
CLASSES_FILE = os.path.join(CLASSROOM_DIR, "classes.json")
IDENTITY_FILE = os.path.join(app_paths.get_data_dir(), "classroom_identity.json")

# 班级码去掉容易混淆的 I / O / 0 / 1
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 6

MAX_EVIDENCE_PER_CONCEPT = 2
EVIDENCE_MAX_CHARS = 120
MAX_SUMMARY_EXCERPT_CHARS = 600

LEVEL_MASTERED = "掌握"
LEVEL_WEAK = "待巩固"

# 与 mastery_mgr 的达标口径保持一致：三条证据（复述质量、默写覆盖、复习重测）过两条。
MASTERY_STATUS_MASTERED = "已达标"
MASTERY_RULE_TEXT = "复述质量、默写覆盖、复习重测三项过两项"


# ---------------------------------------------------------------- 基础读写

def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _read_json(path, default):
    """读取 JSON；文件不存在、损坏或类型不符时返回默认值。"""
    fallback = default() if callable(default) else default
    if not path:
        return fallback
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return fallback
    return data if isinstance(data, type(fallback)) else fallback


def _write_json(path, data):
    if not path:
        return False
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
        return True
    except OSError:
        return False


def _safe_id(value, fallback=""):
    """把外部传入的标识清洗成安全的文件名片段，防止目录穿越。"""
    text = re.sub(r"[^0-9A-Za-z_-]", "_", (value or "").strip())
    text = text.strip("._-")
    return text[:64] or fallback


def _class_dir(class_id):
    safe = _safe_id(class_id)
    return os.path.join(CLASSROOM_DIR, "classes", safe) if safe else ""


def _roster_path(class_id):
    base = _class_dir(class_id)
    return os.path.join(base, "roster.json") if base else ""


def _assignments_path(class_id):
    base = _class_dir(class_id)
    return os.path.join(base, "assignments.json") if base else ""


def _record_path(class_id, student_id):
    base = _class_dir(class_id)
    safe_student = _safe_id(student_id)
    if not base or not safe_student:
        return ""
    return os.path.join(base, "records", f"{safe_student}.json")


def _reports_dir(class_id):
    base = _class_dir(class_id)
    return os.path.join(base, "reports") if base else ""


# ---------------------------------------------------------------- 班级

def _new_code(existing_codes):
    for _ in range(200):
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
        if code not in existing_codes:
            return code
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def list_classes():
    data = _read_json(CLASSES_FILE, dict)
    classes = data.get("classes") if isinstance(data, dict) else None
    return classes if isinstance(classes, list) else []


def save_classes(classes):
    return _write_json(CLASSES_FILE, {"classes": list(classes or [])})


def get_class(class_id):
    target = (class_id or "").strip()
    if not target:
        return None
    for item in list_classes():
        if item.get("id") == target:
            return item
    return None


def find_class_by_code(code):
    target = (code or "").strip().upper()
    if not target:
        return None
    for item in list_classes():
        if (item.get("code") or "").upper() == target:
            return item
    return None


def create_class(name, teacher_name=""):
    """新建班级并返回班级信息（含 6 位班级码）。"""
    class_name = (name or "").strip()
    if not class_name:
        return None
    classes = list_classes()
    code = _new_code({item.get("code") for item in classes})
    item = {
        "id": f"c_{secrets.token_hex(6)}",
        "name": class_name[:60],
        "code": code,
        "teacher": (teacher_name or "").strip()[:40],
        "created_at": _now(),
    }
    classes.append(item)
    if not save_classes(classes):
        return None
    os.makedirs(_class_dir(item["id"]), exist_ok=True)
    return item


def reset_class_code(class_id):
    """重置班级码（旧班级码立即失效）。"""
    classes = list_classes()
    code = _new_code({item.get("code") for item in classes})
    for item in classes:
        if item.get("id") == class_id:
            item["code"] = code
            if save_classes(classes):
                return code
            return None
    return None


def rename_class(class_id, name):
    classes = list_classes()
    for item in classes:
        if item.get("id") == class_id:
            item["name"] = (name or "").strip()[:60] or item.get("name")
            return save_classes(classes)
    return False


# ---------------------------------------------------------------- 成员

def list_roster(class_id):
    data = _read_json(_roster_path(class_id), dict)
    students = data.get("students") if isinstance(data, dict) else None
    return students if isinstance(students, list) else []


def save_roster(class_id, students):
    return _write_json(_roster_path(class_id), {"students": list(students or [])})


def ensure_student(class_id, student_id, display_name=""):
    """把学生加入名单；已存在则只更新显示名（不覆盖为空）。"""
    safe_student = _safe_id(student_id)
    if not safe_student:
        return None
    students = list_roster(class_id)
    for item in students:
        if _safe_id(item.get("student_id")) == safe_student:
            name = (display_name or "").strip()
            if name and name != item.get("display_name"):
                item["display_name"] = name[:40]
                save_roster(class_id, students)
            return item
    item = {
        "student_id": safe_student,
        "display_name": (display_name or "").strip()[:40] or safe_student[:8],
        "joined_at": _now(),
    }
    students.append(item)
    save_roster(class_id, students)
    return item


def join_class(code, student_id, display_name=""):
    """学生用班级码加入班级，返回 (ok, 结果或错误信息)。"""
    target = find_class_by_code(code)
    if not target:
        return False, "班级码不存在，请向老师确认。"
    student = ensure_student(target["id"], student_id, display_name)
    if not student:
        return False, "学生标识无效。"
    return True, {"class": target, "student": student}


def remove_student(class_id, student_id):
    target = _safe_id(student_id)
    if not target:
        return False
    students = [
        item for item in list_roster(class_id) if _safe_id(item.get("student_id")) != target
    ]
    save_roster(class_id, students)
    path = _record_path(class_id, target)
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass
    return True


# ---------------------------------------------------------------- 任务

def list_assignments(class_id):
    data = _read_json(_assignments_path(class_id), dict)
    items = data.get("assignments") if isinstance(data, dict) else None
    return items if isinstance(items, list) else []


def save_assignments(class_id, items):
    return _write_json(_assignments_path(class_id), {"assignments": list(items or [])})


def add_assignment(class_id, book_name, chapters, due_at="", requirement=""):
    """布置一项学习任务（一本书 + 若干章节）。"""
    if not get_class(class_id):
        return None
    chapter_list = [c.strip() for c in (chapters or []) if (c or "").strip()]
    if not chapter_list:
        return None
    item = {
        "id": f"a_{secrets.token_hex(5)}",
        "book_name": (book_name or "").strip(),
        "chapters": chapter_list,
        "due_at": (due_at or "").strip()[:20],
        "requirement": (requirement or "").strip()[:200],
        "created_at": _now(),
    }
    items = list_assignments(class_id)
    items.append(item)
    if not save_assignments(class_id, items):
        return None
    return item


# ---------------------------------------------------------------- 学生上报

def load_record(class_id, student_id):
    path = _record_path(class_id, student_id)
    data = _read_json(path, dict)
    if not data:
        return None
    data.setdefault("chapters", [])
    return data


def list_records(class_id):
    """读取班级下所有学生上报快照，返回 {student_id: record}。"""
    base = _class_dir(class_id)
    records_dir = os.path.join(base, "records") if base else ""
    if not records_dir or not os.path.isdir(records_dir):
        return {}
    result = {}
    for filename in os.listdir(records_dir):
        if not filename.endswith(".json"):
            continue
        data = _read_json(os.path.join(records_dir, filename), dict)
        if not data:
            continue
        student_id = _safe_id(data.get("student_id")) or os.path.splitext(filename)[0]
        data["student_id"] = student_id
        data.setdefault("chapters", [])
        result[student_id] = data
    return result


def _same_chapter(left, right):
    return (
        (left.get("book_name") or "") == (right.get("book_name") or "")
        and (left.get("chapter_title") or "") == (right.get("chapter_title") or "")
    )


def normalize_concepts(concepts):
    """清洗概念列表，只保留 {term, level}，level 非法时按"待巩固"处理。"""
    result = []
    seen = set()
    for item in concepts or []:
        if isinstance(item, str):
            term, level = item, LEVEL_WEAK
        elif isinstance(item, dict):
            term = (item.get("term") or "").strip()
            level = item.get("level")
        else:
            continue
        term = (term or "").strip()
        if not term or term in seen:
            continue
        seen.add(term)
        result.append(
            {
                "term": term[:60],
                "level": LEVEL_MASTERED if level == LEVEL_MASTERED else LEVEL_WEAK,
            }
        )
    return result


def normalize_evidence(evidence):
    """清洗证据片段，统一为 {term, quote}；原句照抄、不做改写。"""
    result = []
    for item in evidence or []:
        if not isinstance(item, dict):
            continue
        term = (item.get("term") or "").strip()
        quote = re.sub(r"\s+", " ", (item.get("quote") or "").strip())
        if not term or not quote:
            continue
        result.append({"term": term[:60], "quote": quote[:EVIDENCE_MAX_CHARS]})
    return result[: MAX_EVIDENCE_PER_CONCEPT * 8]


def extract_evidence_quotes(concepts, texts, max_per_term=MAX_EVIDENCE_PER_CONCEPT):
    """从学生复述/默写的原文里，为每个概念截取包含它的原句作为证据。

    只做"找到包含该概念的句子并原样截取"，不改写、不润色——这样教师看到的
    才是学生真正说过的话，也才便于人工核验。
    """
    sentences = []
    for text in texts or []:
        if not isinstance(text, str):
            continue
        for raw in re.split(r"[。！？!?\n；;]", text):
            line = re.sub(r"\s+", " ", raw).strip()
            if line:
                sentences.append(line)

    result = []
    for item in concepts or []:
        term = (item.get("term") if isinstance(item, dict) else item) or ""
        term = term.strip()
        if not term:
            continue
        for hit in [s for s in sentences if term in s][:max_per_term]:
            result.append({"term": term[:60], "quote": hit[:EVIDENCE_MAX_CHARS]})
    return result


def build_snapshot(book_name, chapter_title, concepts, minutes=0, rounds=0,
                   evidence=None, summary_excerpt="", review_stage=0, studied_at="",
                   minutes_effective=0, retell=None, dictation=None, mastery=None):
    """组装一条标准的上报快照（字段与教师端聚合保持一致）。

    minutes 是会话时长，minutes_effective 是有效学习时长（去掉挂机）；
    retell / dictation / mastery 是掌握度证据，教师端据此判断「已达标」而不是「看过了」。
    """
    try:
        minutes_value = max(0, int(minutes or 0))
    except (TypeError, ValueError):
        minutes_value = 0
    try:
        effective_value = max(0, int(minutes_effective or 0))
    except (TypeError, ValueError):
        effective_value = 0
    try:
        rounds_value = max(0, int(rounds or 0))
    except (TypeError, ValueError):
        rounds_value = 0
    try:
        stage_value = max(0, int(review_stage or 0))
    except (TypeError, ValueError):
        stage_value = 0
    return {
        "book_name": (book_name or "").strip(),
        "chapter_title": (chapter_title or "").strip(),
        "studied_at": (studied_at or "").strip() or _now(),
        "minutes": minutes_value,
        "minutes_effective": effective_value,
        "rounds": rounds_value,
        "concepts": normalize_concepts(concepts),
        "evidence": normalize_evidence(evidence),
        "retell": retell if isinstance(retell, dict) else None,
        "dictation": dictation if isinstance(dictation, dict) else None,
        "mastery": mastery if isinstance(mastery, dict) else None,
        "summary_excerpt": (summary_excerpt or "").strip()[:MAX_SUMMARY_EXCERPT_CHARS],
        "review_stage": stage_value,
    }


def report_record(class_id, student_id, display_name, entry):
    """写入或覆盖一名学生的一章学习摘要，返回 (ok, 错误信息)。"""
    if not get_class(class_id):
        return False, "班级不存在。"
    safe_student = _safe_id(student_id)
    if not safe_student:
        return False, "学生标识无效。"
    if not (entry.get("chapter_title") or "").strip():
        return False, "缺少章节名称。"

    ensure_student(class_id, safe_student, display_name)
    record = load_record(class_id, safe_student) or {
        "student_id": safe_student,
        "chapters": [],
    }
    record["student_id"] = safe_student
    record["display_name"] = (
        (display_name or "").strip()[:40] or record.get("display_name") or safe_student[:8]
    )
    record["updated_at"] = _now()
    chapters = [c for c in record.get("chapters") or [] if not _same_chapter(c, entry)]
    chapters.append(entry)
    record["chapters"] = chapters
    if not _write_json(_record_path(class_id, safe_student), record):
        return False, "上报失败：无法写入数据文件。"
    return True, None


# ---------------------------------------------------------------- 聚合（M3 看板与 M6 建议共用）

def _learned_titles(record, book_name=""):
    titles = set()
    for entry in record.get("chapters") or []:
        if book_name and (entry.get("book_name") or "") != book_name:
            continue
        title = (entry.get("chapter_title") or "").strip()
        if title:
            titles.add(title)
    return titles


def aggregate_class(class_id, book_name="", chapters=None):
    """汇总一个班级的学情。

    M3 看板与 M6 教学建议共用这一份统计结果，从而保证教师在建议里看到的
    每一个数字都能回到看板上核对。
    """
    target_class = get_class(class_id)
    if not target_class:
        return None

    book = (book_name or "").strip()
    chapter_filter = {c.strip() for c in (chapters or []) if (c or "").strip()}

    roster = list_roster(class_id)
    records = list_records(class_id)
    display_names = {
        _safe_id(item.get("student_id")): (item.get("display_name") or "").strip()
        for item in roster
    }

    student_ids = [sid for sid in display_names if sid]
    for student_id in records:
        if student_id not in student_ids:
            student_ids.append(student_id)

    concept_index = {}
    chapter_index = {}
    student_index = {}
    reported = set()
    student_mastered = {}
    student_effective = {}
    total_minutes = 0
    total_effective = 0
    effective_entries = 0
    total_rounds = 0

    for student_id in student_ids:
        record = records.get(student_id) or {}
        name = display_names.get(student_id) or record.get("display_name") or student_id[:8]
        studied_any = False
        weak_count = 0
        learned_count = 0

        for entry in record.get("chapters") or []:
            if book and (entry.get("book_name") or "") != book:
                continue
            title = (entry.get("chapter_title") or "").strip()
            if not title:
                continue
            if chapter_filter and title not in chapter_filter:
                continue

            studied_any = True
            learned_count += 1
            total_minutes += max(0, int(entry.get("minutes") or 0))
            total_rounds += max(0, int(entry.get("rounds") or 0))
            effective_minutes = max(0, int(entry.get("minutes_effective") or 0))
            if effective_minutes:
                total_effective += effective_minutes
                effective_entries += 1
                student_effective[student_id] = (
                    student_effective.get(student_id, 0) + effective_minutes
                )

            mastery = entry.get("mastery") if isinstance(entry.get("mastery"), dict) else {}
            is_mastered = (mastery.get("status") or "") == MASTERY_STATUS_MASTERED
            retell = entry.get("retell") if isinstance(entry.get("retell"), dict) else {}

            key = ((entry.get("book_name") or ""), title)
            slot = chapter_index.setdefault(
                key,
                {
                    "book_name": entry.get("book_name") or "",
                    "chapter_title": title,
                    "learned_count": 0,
                    "mastered_count": 0,
                    "weak_count": 0,
                    "mastered_students": 0,
                    "retell_total_sum": 0,
                    "retell_scored": 0,
                },
            )
            slot["learned_count"] += 1
            if is_mastered:
                slot["mastered_students"] += 1
                student_mastered.setdefault(student_id, set()).add(title)
            try:
                retell_total = int(retell.get("total") or 0)
            except (TypeError, ValueError):
                retell_total = 0
            if retell_total:
                slot["retell_total_sum"] += retell_total
                slot["retell_scored"] += 1

            evidence_map = {}
            for ev in entry.get("evidence") or []:
                term = (ev.get("term") or "").strip()
                if term and term not in evidence_map:
                    evidence_map[term] = ev.get("quote") or ""

            for concept in entry.get("concepts") or []:
                term = (concept.get("term") or "").strip()
                if not term:
                    continue
                if concept.get("level") == LEVEL_MASTERED:
                    slot["mastered_count"] += 1
                    continue

                slot["weak_count"] += 1
                weak_count += 1
                item = concept_index.setdefault(
                    term,
                    {
                        "term": term,
                        "count": 0,
                        "students": [],
                        "evidence": [],
                        "chapters": [],
                    },
                )
                item["count"] += 1
                item["students"].append(
                    {"student_id": student_id, "display_name": name}
                )
                if title not in item["chapters"]:
                    item["chapters"].append(title)
                quote = evidence_map.get(term)
                if quote and len(item["evidence"]) < MAX_EVIDENCE_PER_CONCEPT * 3:
                    item["evidence"].append(
                        {"student_id": student_id, "display_name": name, "quote": quote}
                    )

        if studied_any:
            reported.add(student_id)
        student_index[student_id] = {
            "student_id": student_id,
            "display_name": name,
            "chapters_learned": learned_count,
            "chapters_mastered": len(student_mastered.get(student_id, set())),
            "weak_concepts": weak_count,
            "minutes": sum(
                max(0, int(e.get("minutes") or 0)) for e in record.get("chapters") or []
            ),
            "minutes_effective": student_effective.get(student_id, 0),
            "last_active": record.get("updated_at") or "",
            "reported": studied_any,
        }

    assignments = []
    for item in list_assignments(class_id):
        if book and (item.get("book_name") or "") != book:
            continue
        required = {c for c in (item.get("chapters") or []) if c}
        completed = []
        pending = []
        mastered = []
        for student_id in student_ids:
            learned = _learned_titles(
                records.get(student_id) or {}, item.get("book_name") or ""
            )
            if required and required.issubset(learned):
                completed.append(student_id)
            else:
                pending.append(student_id)
            if required and required.issubset(student_mastered.get(student_id, set())):
                mastered.append(student_id)
        def _name_of(student_id):
            return (
                display_names.get(student_id)
                or (records.get(student_id) or {}).get("display_name")
                or student_id[:8]
            )
        assignments.append(
            {
                "id": item.get("id"),
                "book_name": item.get("book_name") or "",
                "chapters": list(item.get("chapters") or []),
                "due_at": item.get("due_at") or "",
                "requirement": item.get("requirement") or "",
                "completed_count": len(completed),
                "pending_count": len(pending),
                "mastered_count": len(mastered),
                "unmastered_count": len(student_ids) - len(mastered),
                "pending_students": [
                    {"student_id": sid, "display_name": _name_of(sid)}
                    for sid in pending
                ],
                "unmastered_students": [
                    {"student_id": sid, "display_name": _name_of(sid)}
                    for sid in student_ids
                    if sid not in mastered
                ],
            }
        )

    learned_students = len(reported)

    required_titles = set()
    for item in list_assignments(class_id):
        if book and (item.get("book_name") or "") != book:
            continue
        required_titles.update(c for c in (item.get("chapters") or []) if c)

    mastered_ids = []
    for student_id in student_ids:
        owned = student_mastered.get(student_id, set())
        if required_titles:
            ok = required_titles.issubset(owned)
        else:
            ok = bool(owned)
        if ok:
            mastered_ids.append(student_id)
        if student_id in student_index:
            student_index[student_id]["mastery_status"] = (
                MASTERY_STATUS_MASTERED if ok else "已学习"
            ) if student_index[student_id]["reported"] else "未开始"

    chapter_stats = []
    for slot in sorted(chapter_index.values(), key=lambda item: -item["learned_count"]):
        scored = slot.get("retell_scored") or 0
        chapter_stats.append(
            {
                "book_name": slot["book_name"],
                "chapter_title": slot["chapter_title"],
                "learned_count": slot["learned_count"],
                "mastered_count": slot["mastered_count"],
                "weak_count": slot["weak_count"],
                "mastered_students": slot["mastered_students"],
                "retell_avg": round(slot["retell_total_sum"] / scored, 1) if scored else 0,
                "retell_scored": scored,
            }
        )

    return {
        "class": {
            "id": target_class.get("id"),
            "name": target_class.get("name"),
            "code": target_class.get("code"),
        },
        "book_name": book,
        "chapters": sorted(chapter_filter),
        "students_total": len(student_ids),
        "students_reported": learned_students,
        "students_mastered": len(mastered_ids),
        "mastery_rate": (
            round(100 * len(mastered_ids) / len(student_ids)) if student_ids else 0
        ),
        "mastery_rule": MASTERY_RULE_TEXT,
        "avg_minutes": round(total_minutes / learned_students, 1) if learned_students else 0,
        "avg_minutes_effective": (
            round(total_effective / effective_entries, 1) if effective_entries else 0
        ),
        "avg_rounds": round(total_rounds / learned_students, 1) if learned_students else 0,
        "students": sorted(
            student_index.values(),
            key=lambda item: (-item["weak_concepts"], item["display_name"]),
        ),
        "chapter_stats": chapter_stats,
        "weak_concepts": sorted(
            concept_index.values(), key=lambda item: (-item["count"], item["term"])
        ),
        "assignments": assignments,
        "generated_at": _now(),
    }


# ---------------------------------------------------------------- 教学建议缓存

def advice_scope_key(book_name, chapters):
    raw = f"{book_name or ''}|{'|'.join(sorted(c for c in (chapters or []) if c))}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:10]


def advice_cache_path(class_id, book_name, chapters):
    base = _reports_dir(class_id)
    if not base:
        return ""
    return os.path.join(base, f"advice-{advice_scope_key(book_name, chapters)}.json")


def load_advice_cache(class_id, book_name, chapters):
    path = advice_cache_path(class_id, book_name, chapters)
    data = _read_json(path, dict)
    return data if data else None


def save_advice_cache(class_id, book_name, chapters, payload):
    return _write_json(advice_cache_path(class_id, book_name, chapters), payload)


# ---------------------------------------------------------------- 本机学生身份

def load_identity():
    return _read_json(IDENTITY_FILE, dict)


def save_identity(identity):
    return _write_json(IDENTITY_FILE, identity or {})


def get_or_create_student_identity():
    """学生端本机身份：首次调用生成稳定标识，用于上报与查看任务。"""
    identity = load_identity()
    if not identity.get("student_id"):
        identity["student_id"] = f"s_{secrets.token_hex(6)}"
    identity.setdefault("display_name", "")
    identity.setdefault("classes", [])
    save_identity(identity)
    return identity


def remember_class(class_id):
    identity = get_or_create_student_identity()
    classes = [c for c in identity.get("classes") or [] if c]
    if class_id and class_id not in classes:
        classes.append(class_id)
    identity["classes"] = classes
    save_identity(identity)
    return identity


def set_display_name(display_name):
    identity = get_or_create_student_identity()
    identity["display_name"] = (display_name or "").strip()[:40]
    save_identity(identity)
    return identity


def my_classes():
    identity = load_identity()
    result = []
    for class_id in identity.get("classes") or []:
        item = get_class(class_id)
        if item:
            result.append(
                {"id": item["id"], "name": item["name"], "code": item.get("code")}
            )
    return result
