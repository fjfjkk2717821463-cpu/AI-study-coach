import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import advice_mgr
import app
import classroom_mgr
import coach_v4


def sample_stats():
    return {
        "class": {"id": "c_1", "name": "计科2401", "code": "ABC123"},
        "book_name": "西方经济学",
        "chapters": ["第三章 弹性"],
        "students_total": 4,
        "students_reported": 4,
        "avg_minutes": 12,
        "avg_rounds": 2,
        "assignments": [
            {
                "id": "a_1",
                "book_name": "西方经济学",
                "chapters": ["第三章 弹性"],
                "completed_count": 2,
                "pending_count": 2,
            }
        ],
        "weak_concepts": [
            {
                "term": "弹性",
                "count": 3,
                "students": [{"student_id": "s_1", "display_name": "小明"}],
                "evidence": [
                    {
                        "student_id": "s_1",
                        "display_name": "小明",
                        "quote": "弹性是价格变化引起需求量变化的程度",
                    }
                ],
                "chapters": ["第三章 弹性"],
            },
            {"term": "税负", "count": 2, "students": [], "evidence": [], "chapters": []},
            {"term": "均衡", "count": 1, "students": [], "evidence": [], "chapters": []},
            {"term": "边际效用", "count": 1, "students": [], "evidence": [], "chapters": []},
        ],
        "chapter_stats": [],
        "students": [],
        "generated_at": "2026-09-20 10:00:00",
    }


def model_advice(**overrides):
    payload = {
        "chapter": "第三章 弹性",
        "concepts": [
            {
                "name": "弹性",
                "struggling_count": 3,
                "misconception": "把弹性当成斜率",
                "evidence": [{"quote": "弹性是价格变化引起需求量变化的程度"}],
                "explain_action": "先问价格翻倍后需求量怎么变",
                "activity": "给三个场景判断弹性大小",
                "check_question": "弹性大就代表需求量大吗？",
            }
        ],
        "class_actions": ["本班 2 人未完成任务，建议课前提醒。"],
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


class ParseTests(unittest.TestCase):
    def test_plain_json(self):
        self.assertEqual(advice_mgr.parse_advice_json('{"concepts": []}'), {"concepts": []})

    def test_fenced_json(self):
        text = '```json\n{"concepts": []}\n```'
        self.assertEqual(advice_mgr.parse_advice_json(text), {"concepts": []})

    def test_json_with_surrounding_text(self):
        text = '好的，建议如下：{"concepts": []} 以上。'
        self.assertEqual(advice_mgr.parse_advice_json(text), {"concepts": []})

    def test_invalid_returns_none(self):
        for text in ("", None, "not json", "[1,2,3]", "{ broken"):
            self.assertIsNone(advice_mgr.parse_advice_json(text), text)


class ValidateTests(unittest.TestCase):
    def setUp(self):
        self.stats = sample_stats()

    def test_count_is_forced_to_truth(self):
        advice, issues = advice_mgr.validate_advice(
            {"concepts": [{"name": "弹性", "struggling_count": 99}]}, self.stats
        )
        self.assertEqual(advice["concepts"][0]["struggling_count"], 3)
        self.assertTrue(any("count_fixed:弹性" in item for item in issues))

    def test_unknown_concept_dropped(self):
        advice, issues = advice_mgr.validate_advice(
            {
                "concepts": [
                    {"name": "完全竞争市场", "struggling_count": 5},
                    {"name": "弹性", "struggling_count": 3},
                ]
            },
            self.stats,
        )
        self.assertEqual([item["name"] for item in advice["concepts"]], ["弹性"])
        self.assertIn("unknown_concept:完全竞争市场", issues)

    def test_fabricated_evidence_dropped(self):
        advice, _ = advice_mgr.validate_advice(
            {
                "concepts": [
                    {
                        "name": "弹性",
                        "struggling_count": 3,
                        "evidence": [{"quote": "学生说弹性等于需求量的变化率"}],
                    }
                ]
            },
            self.stats,
        )
        concept = advice["concepts"][0]
        self.assertEqual(concept["evidence"], [])
        self.assertEqual(concept["evidence_basis"], "概念频次")

    def test_evidence_rewritten_to_original_quote(self):
        advice, _ = advice_mgr.validate_advice(
            {
                "concepts": [
                    {
                        "name": "弹性",
                        "struggling_count": 3,
                        "evidence": [{"quote": "弹性是价格变化引起需求量变化的程度。"}],
                    }
                ]
            },
            self.stats,
        )
        concept = advice["concepts"][0]
        self.assertEqual(concept["evidence"], ["弹性是价格变化引起需求量变化的程度"])
        self.assertEqual(concept["evidence_basis"], "学生原话")

    def test_sorted_and_truncated(self):
        advice, issues = advice_mgr.validate_advice(
            {
                "concepts": [
                    {"name": "均衡", "struggling_count": 1},
                    {"name": "边际效用", "struggling_count": 1},
                    {"name": "弹性", "struggling_count": 3},
                    {"name": "税负", "struggling_count": 2},
                ]
            },
            self.stats,
            top_n=2,
        )
        self.assertEqual([item["name"] for item in advice["concepts"]], ["弹性", "税负"])
        self.assertTrue(any(item.startswith("truncated_to_top_n") for item in issues))

    def test_class_action_number_must_match(self):
        advice, issues = advice_mgr.validate_advice(
            {
                "concepts": [{"name": "弹性", "struggling_count": 3}],
                "class_actions": ["本班 7 人未完成任务，建议课前提醒。", "下次课重点讲弹性。"],
            },
            self.stats,
        )
        self.assertEqual(advice["class_actions"], ["下次课重点讲弹性。"])
        self.assertTrue(any("action_count_mismatch" in item for item in issues))

    def test_class_action_matching_number_kept(self):
        advice, _ = advice_mgr.validate_advice(
            {
                "concepts": [{"name": "弹性", "struggling_count": 3}],
                "class_actions": ["本班 2 人未完成任务，建议课前提醒。"],
            },
            self.stats,
        )
        self.assertEqual(len(advice["class_actions"]), 1)

    def test_default_action_used_when_model_silent(self):
        advice, _ = advice_mgr.validate_advice(
            {"concepts": [{"name": "弹性", "struggling_count": 3}]}, self.stats
        )
        self.assertEqual(advice["class_actions"], ["本班 2 人未完成任务，建议课前提醒。"])

    def test_based_on_uses_true_numbers(self):
        advice, _ = advice_mgr.validate_advice(
            {
                "based_on": {"students_total": 999, "students_reported": 999},
                "concepts": [{"name": "弹性", "struggling_count": 3}],
            },
            self.stats,
        )
        self.assertEqual(advice["based_on"]["students_total"], 4)
        self.assertEqual(advice["based_on"]["students_reported"], 4)
        self.assertEqual(advice["based_on"]["concepts_advised"], 1)

    def test_long_fields_truncated(self):
        advice, _ = advice_mgr.validate_advice(
            {
                "concepts": [
                    {
                        "name": "弹性",
                        "struggling_count": 3,
                        "misconception": "很长" * 500,
                    }
                ]
            },
            self.stats,
        )
        self.assertLessEqual(
            len(advice["concepts"][0]["misconception"]), advice_mgr.FIELD_MAX_CHARS
        )


class GenerateTests(unittest.TestCase):
    def setUp(self):
        self.stats = sample_stats()

    def test_happy_path(self):
        advice, meta = advice_mgr.generate_advice(
            self.stats, "教材原文", lambda messages, stream=False: model_advice()
        )
        self.assertFalse(meta["degraded"])
        self.assertEqual(meta["attempts"], 1)
        self.assertEqual(advice["concepts"][0]["name"], "弹性")

    def test_retry_after_bad_json(self):
        calls = {"n": 0}

        def flaky(messages, stream=False):
            calls["n"] += 1
            return "这不是 JSON" if calls["n"] == 1 else model_advice()

        advice, meta = advice_mgr.generate_advice(self.stats, "", flaky)
        self.assertEqual(meta["attempts"], 2)
        self.assertFalse(meta["degraded"])
        self.assertIn("invalid_json", meta["issues"])

    def test_falls_back_after_repeated_failure(self):
        advice, meta = advice_mgr.generate_advice(
            self.stats, "", lambda messages, stream=False: "{}", max_attempts=2
        )
        self.assertTrue(meta["degraded"])
        self.assertTrue(advice["degraded"])
        self.assertEqual(
            [item["name"] for item in advice["concepts"]], ["弹性", "税负", "均衡"]
        )
        self.assertEqual(advice["concepts"][0]["evidence_basis"], "学生原话")
        self.assertEqual(advice["concepts"][0]["explain_action"], "")

    def test_falls_back_when_call_raises(self):
        def boom(messages, stream=False):
            raise RuntimeError("429 rate limit")

        advice, meta = advice_mgr.generate_advice(self.stats, "", boom)
        self.assertTrue(meta["degraded"])
        self.assertTrue(any("call_failed" in item for item in meta["issues"]))
        self.assertEqual(advice["class_actions"], ["本班 2 人未完成任务，建议课前提醒。"])

    def test_all_invented_concepts_falls_back(self):
        reply = json.dumps({"concepts": [{"name": "不存在的概念", "struggling_count": 9}]})
        advice, meta = advice_mgr.generate_advice(
            self.stats, "", lambda messages, stream=False: reply, max_attempts=2
        )
        self.assertTrue(meta["degraded"])
        self.assertTrue(any("unknown_concept" in item for item in meta["issues"]))
        self.assertEqual([item["name"] for item in advice["concepts"]], ["弹性", "税负", "均衡"])

    def test_prompt_contains_scope_and_evidence(self):
        messages = advice_mgr.build_messages(self.stats, "教材原文", top_n=2)
        prompt = messages[1]["content"]
        self.assertIn("弹性", prompt)
        self.assertIn("3 人", prompt)
        self.assertIn("只能整句照抄", prompt)
        self.assertIn("教材原文", prompt)


class AdviceApiTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._old_dir = classroom_mgr.CLASSROOM_DIR
        self._old_classes = classroom_mgr.CLASSES_FILE
        self._old_identity = classroom_mgr.IDENTITY_FILE
        classroom_mgr.CLASSROOM_DIR = os.path.join(self._tmp, "classroom")
        classroom_mgr.CLASSES_FILE = os.path.join(
            classroom_mgr.CLASSROOM_DIR, "classes.json"
        )
        classroom_mgr.IDENTITY_FILE = os.path.join(self._tmp, "identity.json")

        self._old_request = coach_v4._request_chat
        self.calls = {"n": 0}

        app.app.config["TESTING"] = True
        self.client = app.app.test_client()
        self.class_item = classroom_mgr.create_class("计科2401")
        self.class_id = self.class_item["id"]

    def tearDown(self):
        classroom_mgr.CLASSROOM_DIR = self._old_dir
        classroom_mgr.CLASSES_FILE = self._old_classes
        classroom_mgr.IDENTITY_FILE = self._old_identity
        coach_v4._request_chat = self._old_request

    def _fake_model(self, reply):
        def fake(messages, stream=False):
            self.calls["n"] += 1
            return reply if isinstance(reply, str) else reply(messages)

        coach_v4._request_chat = fake

    def _seed(self, students=3):
        for index in range(students):
            snapshot = classroom_mgr.build_snapshot(
                "西方经济学",
                "第三章 弹性",
                [
                    {"term": "弹性", "level": "待巩固"},
                    {"term": "税负", "level": "待巩固"},
                ],
                minutes=10 + index,
                rounds=2,
                evidence=[{"term": "弹性", "quote": f"学生{index}说弹性是价格变化的反应程度"}],
            )
            classroom_mgr.report_record(
                self.class_id, f"s_{index}", f"学生{index}", snapshot
            )

    def test_create_class_and_overview(self):
        resp = self.client.post("/api/teacher/classes", json={"name": "软工2402"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.get_json()["class"]["code"]), 6)

        self._seed(2)
        overview = self.client.get(
            f"/api/teacher/overview?class_id={self.class_id}"
        ).get_json()
        self.assertEqual(overview["stats"]["students_reported"], 2)
        self.assertEqual(overview["stats"]["weak_concepts"][0]["count"], 2)

    def test_overview_requires_class(self):
        resp = self.client.get("/api/teacher/overview?class_id=c_nope")
        self.assertEqual(resp.status_code, 400)

    def test_advice_end_to_end_and_cache(self):
        self._seed(3)
        self._fake_model(model_advice())

        first = self.client.post(
            "/api/teacher/advice", json={"class_id": self.class_id}
        ).get_json()
        self.assertTrue(first["ok"])
        self.assertFalse(first["cached"])
        self.assertFalse(first["degraded"])
        self.assertEqual(first["advice"]["concepts"][0]["name"], "弹性")
        self.assertEqual(first["advice"]["based_on"]["students_reported"], 3)
        self.assertEqual(self.calls["n"], 1)

        second = self.client.post(
            "/api/teacher/advice", json={"class_id": self.class_id}
        ).get_json()
        self.assertTrue(second["cached"])
        self.assertEqual(self.calls["n"], 1, "命中缓存时不应重复调用模型")

        third = self.client.post(
            "/api/teacher/advice", json={"class_id": self.class_id, "refresh": True}
        ).get_json()
        self.assertFalse(third["cached"])
        self.assertEqual(self.calls["n"], 2)

    def test_advice_repairs_model_numbers(self):
        self._seed(4)
        self._fake_model(
            model_advice(
                concepts=[
                    {
                        "name": "弹性",
                        "struggling_count": 99,
                        "evidence": [{"quote": "这句是模型编的"}],
                        "misconception": "把弹性当成斜率",
                    }
                ]
            )
        )
        data = self.client.post(
            "/api/teacher/advice", json={"class_id": self.class_id}
        ).get_json()
        concept = data["advice"]["concepts"][0]
        self.assertEqual(concept["struggling_count"], 4)
        self.assertEqual(concept["evidence"], [])
        self.assertEqual(concept["evidence_basis"], "概念频次")
        self.assertIn("count_fixed:弹性:99->4", data["meta"]["issues"])

    def test_advice_falls_back_when_model_is_garbage(self):
        self._seed(3)
        self._fake_model("模型今天不太配合")
        data = self.client.post(
            "/api/teacher/advice", json={"class_id": self.class_id}
        ).get_json()
        self.assertTrue(data["degraded"])
        self.assertTrue(data["advice"]["degraded"])
        self.assertEqual(data["advice"]["concepts"][0]["name"], "弹性")

    def test_insufficient_samples_skips_model(self):
        self._seed(2)
        self._fake_model(model_advice())
        data = self.client.post(
            "/api/teacher/advice", json={"class_id": self.class_id}
        ).get_json()
        self.assertTrue(data["degraded"])
        self.assertIn("不足", data["message"])
        self.assertEqual(self.calls["n"], 0)
        self.assertEqual(data["advice"]["degraded_reason"], "insufficient_samples")

    def test_advice_requires_class(self):
        resp = self.client.post("/api/teacher/advice", json={"class_id": "c_nope"})
        self.assertEqual(resp.status_code, 400)


class ClassApiTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._old_dir = classroom_mgr.CLASSROOM_DIR
        self._old_classes = classroom_mgr.CLASSES_FILE
        self._old_identity = classroom_mgr.IDENTITY_FILE
        classroom_mgr.CLASSROOM_DIR = os.path.join(self._tmp, "classroom")
        classroom_mgr.CLASSES_FILE = os.path.join(
            classroom_mgr.CLASSROOM_DIR, "classes.json"
        )
        classroom_mgr.IDENTITY_FILE = os.path.join(self._tmp, "identity.json")
        app.app.config["TESTING"] = True
        self.client = app.app.test_client()

    def tearDown(self):
        classroom_mgr.CLASSROOM_DIR = self._old_dir
        classroom_mgr.CLASSES_FILE = self._old_classes
        classroom_mgr.IDENTITY_FILE = self._old_identity

    def test_join_report_and_tasks(self):
        class_item = classroom_mgr.create_class("计科2401")
        classroom_mgr.add_assignment(
            class_item["id"], "西方经济学", ["第三章 弹性"], due_at="2026-09-25"
        )

        joined = self.client.post(
            "/api/class/join",
            json={"code": class_item["code"], "display_name": "小明"},
        ).get_json()
        self.assertTrue(joined["ok"])
        self.assertEqual(joined["class"]["id"], class_item["id"])

        tasks = self.client.get(
            f"/api/class/my_tasks?class_id={class_item['id']}"
        ).get_json()["tasks"]
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["done_chapters"], [])

        report = self.client.post(
            "/api/class/report",
            json={
                "class_id": class_item["id"],
                "book_name": "西方经济学",
                "chapter_title": "第三章 弹性",
                "concepts": [
                    {"term": "弹性", "level": "待巩固"},
                    {"term": "均衡", "level": "掌握"},
                ],
                "recall_texts": ["弹性是价格变化引起需求量变化的程度。"],
                "minutes": 12,
                "rounds": 3,
            },
        ).get_json()
        self.assertTrue(report["ok"])
        self.assertEqual(report["weak_concepts"], ["弹性"])

        record = classroom_mgr.load_record(class_item["id"], joined["student_id"])
        self.assertEqual(len(record["chapters"]), 1)
        self.assertEqual(record["chapters"][0]["evidence"][0]["term"], "弹性")

        tasks = self.client.get(
            f"/api/class/my_tasks?class_id={class_item['id']}"
        ).get_json()["tasks"]
        self.assertEqual(tasks, [])

    def test_join_with_bad_code(self):
        resp = self.client.post("/api/class/join", json={"code": "ZZZZZZ"})
        self.assertEqual(resp.status_code, 400)

    def test_report_requires_chapter(self):
        class_item = classroom_mgr.create_class("计科2401")
        resp = self.client.post(
            "/api/class/report", json={"class_id": class_item["id"], "chapter_title": ""}
        )
        self.assertEqual(resp.status_code, 400)

    def test_status_returns_identity(self):
        data = self.client.get("/api/class/status").get_json()
        self.assertTrue(data["student_id"].startswith("s_"))

    def test_student_detail(self):
        class_item = classroom_mgr.create_class("计科2401")
        snapshot = classroom_mgr.build_snapshot(
            "书", "章", [{"term": "弹性", "level": "待巩固"}]
        )
        classroom_mgr.report_record(class_item["id"], "s_1", "小明", snapshot)

        found = self.client.get(
            f"/api/teacher/student/s_1?class_id={class_item['id']}"
        )
        self.assertEqual(found.status_code, 200)
        self.assertEqual(found.get_json()["record"]["chapters"][0]["chapter_title"], "章")

        missing = self.client.get(
            f"/api/teacher/student/s_9?class_id={class_item['id']}"
        )
        self.assertEqual(missing.status_code, 400)


if __name__ == "__main__":
    unittest.main()
