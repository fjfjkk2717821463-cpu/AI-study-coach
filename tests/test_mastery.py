import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app
import classroom_mgr
import coach_v4
import mastery_mgr

CHAPTER = (
    "需求价格弹性衡量的是需求量对价格变化的敏感程度，"
    "等于需求量变化的百分比除以价格变化的百分比。"
    "当需求完全无弹性时，弹性为零，需求量不随价格变化。"
)
OWN_WORDS = (
    "我理解弹性说的是价格一变，买的人反应有多大。"
    "它算的是百分比而不是绝对量，所以在曲线上的不同位置其实不一样。"
    "如果价格怎么变大家都照买，那就是完全没有弹性。"
)


def valid_reply(score_overrides=None):
    scores = {
        "coverage": {"score": 2, "evidence": "我理解弹性说的是价格一变，买的人反应有多大", "comment": "抓住了核心"},
        "accuracy": {"score": 2, "evidence": "它算的是百分比而不是绝对量", "comment": "口径正确"},
        "reasoning": {"score": 2, "evidence": "如果价格怎么变大家都照买，那就是完全没有弹性", "comment": "提到了边界"},
        "expression": {"score": 3, "evidence": "价格一变，买的人反应有多大", "comment": "用自己的话"},
    }
    scores.update(score_overrides or {})
    return json.dumps({"scores": scores, "summary": "整体讲清楚了，可以再补一个反例。"}, ensure_ascii=False)


class TextCompareTests(unittest.TestCase):
    def test_copy_ratio_high_when_reading_source(self):
        self.assertGreater(mastery_mgr.copy_ratio(CHAPTER, CHAPTER), 0.9)

    def test_copy_ratio_low_for_own_words(self):
        self.assertLess(mastery_mgr.copy_ratio(OWN_WORDS, CHAPTER), 0.2)

    def test_copy_ratio_without_source(self):
        self.assertEqual(mastery_mgr.copy_ratio(OWN_WORDS, ""), 0.0)

    def test_coverage_ratio_counts_covered_sentences(self):
        first_sentence = "需求价格弹性衡量的是需求量对价格变化的敏感程度，等于需求量变化的百分比除以价格变化的百分比。"
        self.assertEqual(mastery_mgr.coverage_ratio(first_sentence, CHAPTER), 0.5)
        self.assertEqual(mastery_mgr.coverage_ratio("", CHAPTER), 0.0)
        self.assertEqual(mastery_mgr.coverage_ratio(CHAPTER, CHAPTER), 1.0)


class ValidateTests(unittest.TestCase):
    def test_total_is_computed_by_program(self):
        raw = json.loads(valid_reply())
        result, issues = mastery_mgr.validate_scores(raw, OWN_WORDS, CHAPTER)
        self.assertEqual(result["total"], 9)
        self.assertEqual(result["full_score"], 12)
        self.assertEqual(issues, [])

    def test_scores_are_clamped(self):
        raw = json.loads(valid_reply({"coverage": {"score": 9}, "accuracy": {"score": -3}}))
        result, issues = mastery_mgr.validate_scores(raw, OWN_WORDS, CHAPTER)
        self.assertEqual(result["scores"]["coverage"]["score"], 3)
        self.assertEqual(result["scores"]["accuracy"]["score"], 0)
        self.assertTrue(any("score_clamped" in item for item in issues))

    def test_missing_score_becomes_zero(self):
        raw = {"scores": {"coverage": {"comment": "没给分"}}}
        result, issues = mastery_mgr.validate_scores(raw, OWN_WORDS, CHAPTER)
        self.assertEqual(result["scores"]["coverage"]["score"], 0)
        self.assertTrue(any("score_missing:coverage" in item for item in issues))

    def test_evidence_must_come_from_retell(self):
        raw = json.loads(
            valid_reply({"coverage": {"score": 3, "evidence": "学生说弹性等于斜率"}})
        )
        result, issues = mastery_mgr.validate_scores(raw, OWN_WORDS, CHAPTER)
        self.assertEqual(result["scores"]["coverage"]["evidence"], "")
        self.assertIn("evidence_dropped:coverage", issues)

    def test_reading_source_caps_expression(self):
        raw = json.loads(valid_reply())
        result, issues = mastery_mgr.validate_scores(raw, CHAPTER, CHAPTER)
        self.assertEqual(result["scores"]["expression"]["score"], mastery_mgr.COPY_PENALTY_CAP)
        self.assertTrue(any(item.startswith("copy_penalty") for item in issues))
        self.assertGreater(result["copy_ratio"], 0.5)

    def test_short_retell_rejected(self):
        result, meta = mastery_mgr.score_retell(
            "章", CHAPTER, [], "不知道", lambda messages, stream=False: valid_reply()
        )
        self.assertIsNone(result)
        self.assertTrue(meta["degraded"])
        self.assertIn("retell_too_short", meta["issues"])


class ScoreRetellTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._old = mastery_mgr.MASTERY_DIR
        mastery_mgr.MASTERY_DIR = os.path.join(self._tmp, "mastery")

    def tearDown(self):
        mastery_mgr.MASTERY_DIR = self._old

    def test_happy_path(self):
        result, meta = mastery_mgr.score_retell(
            "第三章 弹性", CHAPTER, [{"term": "需求价格弹性"}], OWN_WORDS,
            lambda messages, stream=False: valid_reply(),
        )
        self.assertEqual(result["total"], 9)
        self.assertFalse(meta["degraded"])
        self.assertEqual(meta["attempts"], 1)
        self.assertTrue(mastery_mgr.save_retell("书", "章", result))
        self.assertEqual(mastery_mgr.latest_retell("书", "章")["total"], 9)
        self.assertEqual(len(mastery_mgr.retell_trend("书", "章")), 1)

    def test_retry_then_success(self):
        calls = {"n": 0}

        def flaky(messages, stream=False):
            calls["n"] += 1
            return "不是 JSON" if calls["n"] == 1 else valid_reply()

        result, meta = mastery_mgr.score_retell("章", CHAPTER, [], OWN_WORDS, flaky)
        self.assertEqual(meta["attempts"], 2)
        self.assertIsNotNone(result)
        self.assertIn("invalid_json", meta["issues"])

    def test_gives_up_without_faking_scores(self):
        result, meta = mastery_mgr.score_retell(
            "章", CHAPTER, [], OWN_WORDS, lambda messages, stream=False: "{}"
        )
        self.assertIsNone(result)
        self.assertTrue(meta["degraded"])

    def test_call_failure_returns_no_score(self):
        def boom(messages, stream=False):
            raise RuntimeError("401")

        result, meta = mastery_mgr.score_retell("章", CHAPTER, [], OWN_WORDS, boom)
        self.assertIsNone(result)
        self.assertTrue(any("call_failed" in item for item in meta["issues"]))

    def test_dictation_roundtrip(self):
        entry = mastery_mgr.record_dictation(
            "书", "章", "需求价格弹性衡量的是需求量对价格变化的敏感程度，等于需求量变化的百分比除以价格变化的百分比。", CHAPTER
        )
        self.assertEqual(entry["coverage"], 0.5)
        self.assertEqual(mastery_mgr.latest_dictation("书", "章")["coverage"], 0.5)


class VerdictTests(unittest.TestCase):
    def test_two_of_three_passes(self):
        verdict = mastery_mgr.mastery_verdict(
            retell={"total": 8}, dictation={"coverage": 0.6}, review_quality=1
        )
        self.assertEqual(verdict["status"], mastery_mgr.STATUS_MASTERED)
        self.assertEqual(sorted(verdict["passed"]), ["复述质量", "默写覆盖"])
        self.assertEqual(verdict["missing"], ["复习重测"])

    def test_only_retell_is_not_enough(self):
        verdict = mastery_mgr.mastery_verdict(
            retell={"total": 9}, dictation={"coverage": 0.1}, review_quality=None
        )
        self.assertEqual(verdict["status"], mastery_mgr.STATUS_LEARNED)
        self.assertEqual(verdict["passed"], ["复述质量"])

    def test_retell_and_dictation_pass(self):
        verdict = mastery_mgr.mastery_verdict(
            retell={"total": 10}, dictation={"coverage": 0.6}, review_quality=None
        )
        self.assertEqual(verdict["status"], mastery_mgr.STATUS_MASTERED)

    def test_weak_retell_plus_dictation_passes(self):
        verdict = mastery_mgr.mastery_verdict(
            retell={"total": 4}, dictation={"coverage": 0.55}, review_quality=2
        )
        self.assertEqual(verdict["status"], mastery_mgr.STATUS_MASTERED)

    def test_nothing_passed(self):
        verdict = mastery_mgr.mastery_verdict()
        self.assertEqual(verdict["status"], mastery_mgr.STATUS_LEARNED)
        self.assertEqual(verdict["passed"], [])


class MasteryApiTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._old_dir = mastery_mgr.MASTERY_DIR
        mastery_mgr.MASTERY_DIR = os.path.join(self._tmp, "mastery")
        self._old_reviews = None
        import review_mgr

        self._old_reviews = review_mgr.REVIEWS_FILE
        review_mgr.REVIEWS_FILE = os.path.join(self._tmp, "reviews.json")
        self._old_request = coach_v4._request_chat
        app.app.config["TESTING"] = True
        self.client = app.app.test_client()
        app.SESSIONS.clear()
        app.SESSIONS["s1"] = {
            "mode": "book",
            "book_name": "西方经济学",
            "book_path": "",
            "chapter_text": CHAPTER,
            "subject": "第三章 弹性",
            "system_prompt": "system",
            "explain_level": "medium",
            "history": [],
            "save_path": None,
            "last_active": 0,
            "started_at": 0,
        }

    def tearDown(self):
        import review_mgr

        mastery_mgr.MASTERY_DIR = self._old_dir
        review_mgr.REVIEWS_FILE = self._old_reviews
        coach_v4._request_chat = self._old_request
        app.SESSIONS.clear()

    def test_score_endpoint_saves_evidence(self):
        coach_v4._request_chat = lambda messages, stream=False: valid_reply()
        resp = self.client.post(
            "/api/retell/score",
            json={"session_id": "s1", "text": OWN_WORDS, "phase": "学习后"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["result"]["total"], 9)
        self.assertEqual(data["mastery"]["status"], "已学习")
        self.assertEqual(mastery_mgr.latest_retell("西方经济学", "第三章 弹性")["total"], 9)

    def test_score_endpoint_rejects_short_text(self):
        resp = self.client.post(
            "/api/retell/score", json={"session_id": "s1", "text": "还行"}
        )
        self.assertEqual(resp.status_code, 400)

    def test_score_endpoint_degrades_without_faking(self):
        coach_v4._request_chat = lambda messages, stream=False: "模型今天不配合"
        data = self.client.post(
            "/api/retell/score", json={"session_id": "s1", "text": OWN_WORDS}
        ).get_json()
        self.assertFalse(data["ok"])
        self.assertTrue(data["degraded"])
        self.assertIsNone(mastery_mgr.latest_retell("西方经济学", "第三章 弹性"))

    def test_mastery_endpoint(self):
        mastery_mgr.save_retell(
            "西方经济学", "第三章 弹性", {"total": 9, "at": "now", "scores": {}}
        )
        data = self.client.get(
            "/api/mastery?book_name=西方经济学&chapter_title=第三章 弹性"
        ).get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["verdict"]["status"], "已学习")
        self.assertIn("retell_total", data["thresholds"])

    def test_dictation_is_recorded_even_if_review_model_fails(self):
        """默写覆盖率是本地计算，模型点评失败也必须留下证据。"""
        old_stream = coach_v4.stream_chat

        def boom(messages):
            raise RuntimeError("网络中断")
            yield ""  # pragma: no cover

        coach_v4.stream_chat = boom
        try:
            resp = self.client.post(
                "/api/session/compare",
                json={"session_id": "s1", "reconstruction": CHAPTER},
            )
            self.assertEqual(resp.status_code, 200)
            self.assertIn("error", resp.get_data(as_text=True))
        finally:
            coach_v4.stream_chat = old_stream

        entry = mastery_mgr.latest_dictation("西方经济学", "第三章 弹性")
        self.assertIsNotNone(entry)
        self.assertGreaterEqual(entry["coverage"], 0.9)


class MasteryAggregationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._old_dir = classroom_mgr.CLASSROOM_DIR
        self._old_classes = classroom_mgr.CLASSES_FILE
        self._old_identity = classroom_mgr.IDENTITY_FILE
        classroom_mgr.CLASSROOM_DIR = os.path.join(self._tmp, "classroom")
        classroom_mgr.CLASSES_FILE = os.path.join(classroom_mgr.CLASSROOM_DIR, "classes.json")
        classroom_mgr.IDENTITY_FILE = os.path.join(self._tmp, "identity.json")
        self.class_item = classroom_mgr.create_class("计科 2401")

    def tearDown(self):
        classroom_mgr.CLASSROOM_DIR = self._old_dir
        classroom_mgr.CLASSES_FILE = self._old_classes
        classroom_mgr.IDENTITY_FILE = self._old_identity

    def _report(self, student_id, mastered, minutes=10, effective=8):
        snapshot = classroom_mgr.build_snapshot(
            "西方经济学",
            "第三章 弹性",
            [{"term": "弹性", "level": "待巩固"}],
            minutes=minutes,
            minutes_effective=effective,
            rounds=2,
            retell={"total": 9 if mastered else 4, "at": "now"},
            mastery={"status": "已达标" if mastered else "已学习", "passed": ["复述质量"]},
        )
        classroom_mgr.report_record(self.class_item["id"], student_id, student_id, snapshot)

    def test_mastery_rate_and_effective_minutes(self):
        classroom_mgr.add_assignment(self.class_item["id"], "西方经济学", ["第三章 弹性"])
        self._report("s_1", True)
        self._report("s_2", False, minutes=30, effective=5)

        stats = classroom_mgr.aggregate_class(self.class_item["id"])
        self.assertEqual(stats["students_total"], 2)
        self.assertEqual(stats["students_mastered"], 1)
        self.assertEqual(stats["mastery_rate"], 50)
        self.assertEqual(stats["avg_minutes_effective"], 6.5)
        self.assertIn("三项过两项", stats["mastery_rule"])

        assignment = stats["assignments"][0]
        self.assertEqual(assignment["mastered_count"], 1)
        self.assertEqual(assignment["unmastered_count"], 1)
        self.assertEqual(assignment["unmastered_students"][0]["student_id"], "s_2")

        chapter = stats["chapter_stats"][0]
        self.assertEqual(chapter["mastered_students"], 1)
        self.assertEqual(chapter["retell_avg"], 6.5)
        self.assertEqual(chapter["retell_scored"], 2)

    def test_student_status_without_assignment(self):
        self._report("s_1", True)
        stats = classroom_mgr.aggregate_class(self.class_item["id"])
        self.assertEqual(stats["students_mastered"], 1)
        self.assertEqual(stats["students"][0]["mastery_status"], "已达标")


if __name__ == "__main__":
    unittest.main()
