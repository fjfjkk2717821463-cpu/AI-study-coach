import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app
import coach_v4
import review_mgr
import settings_mgr
import summary_mgr


class ReviewScheduleTests(unittest.TestCase):
    def setUp(self):
        self._old = review_mgr.REVIEWS_FILE
        review_mgr.REVIEWS_FILE = os.path.join(tempfile.mkdtemp(), "reviews.json")

    def tearDown(self):
        review_mgr.REVIEWS_FILE = self._old

    def test_ensure_and_due(self):
        entry = review_mgr.ensure_entry("微观经济学", "第1章")
        self.assertEqual(entry["stage"], 0)
        self.assertTrue(review_mgr.due_entries(now=entry["next_review_at"]) )
        self.assertFalse(review_mgr.due_entries(now=entry["next_review_at"] - 1))

    def test_quality_updates_interval(self):
        entry = review_mgr.record_review("书", "章", 3)
        self.assertEqual(entry["stage"], 1)
        self.assertEqual(entry["review_count"], 1)
        reset = review_mgr.record_review("书", "章", 1)
        self.assertEqual(reset["stage"], 0)


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self._old = settings_mgr.SETTINGS_FILE
        settings_mgr.SETTINGS_FILE = os.path.join(tempfile.mkdtemp(), "settings.json")

    def tearDown(self):
        settings_mgr.SETTINGS_FILE = self._old

    def test_defaults_and_roundtrip(self):
        self.assertFalse(settings_mgr.load_settings()["spaced_review"])
        settings_mgr.save_settings({"spaced_review": True, "price_per_mtok": 2.5})
        loaded = settings_mgr.load_settings()
        self.assertTrue(loaded["spaced_review"])
        self.assertEqual(loaded["price_per_mtok"], 2.5)


class SummaryConceptTests(unittest.TestCase):
    def setUp(self):
        self._old = summary_mgr.SUMMARIES_DIR
        summary_mgr.SUMMARIES_DIR = tempfile.mkdtemp()

    def tearDown(self):
        summary_mgr.SUMMARIES_DIR = self._old

    def test_concepts_roundtrip(self):
        summary_mgr.save_summary(
            "书", "章", "总结文本", [{"term": "弹性", "level": "待巩固"}]
        )
        full = summary_mgr.load_summary_full("书", "章")
        self.assertEqual(full["concepts"][0]["term"], "弹性")
        listed = summary_mgr.list_summaries()
        self.assertEqual(listed[0]["summaries"][0]["concepts"][0]["level"], "待巩固")


class HelperTests(unittest.TestCase):
    def test_extract_concepts(self):
        text = "## 总结\n- 需求弹性（掌握）\n- 消费者剩余（待巩固）\n- 无谓损失（掌握）"
        concepts = app._extract_concepts(text)
        self.assertEqual(len(concepts), 3)
        self.assertEqual(concepts[1]["level"], "待巩固")

    def test_parse_quiz_json(self):
        text = '```json\n{"questions":[{"type":"choice","question":"q","options":["A","B","C","D"]},{"type":"short","question":"q2"}]}\n```'
        questions = app._parse_quiz_json(text)
        self.assertEqual(len(questions), 2)
        self.assertEqual(questions[0]["type"], "choice")
        self.assertEqual(len(questions[0]["options"]), 4)

    def test_friendly_error(self):
        self.assertIn("模型设置", app._friendly_error("401 invalid api key"))
        self.assertIn("余额", app._friendly_error("402 insufficient balance"))
        self.assertIn("太频繁", app._friendly_error("429 rate limit"))


class VersionApiTests(unittest.TestCase):
    def setUp(self):
        app.app.config["TESTING"] = True
        self.client = app.app.test_client()

    def test_version(self):
        data = self.client.get("/api/version").get_json()
        self.assertEqual(data["version"], app.VERSION)
        self.assertIn("github.com", data["repo"])


class QuizApiTests(unittest.TestCase):
    def setUp(self):
        app.app.config["TESTING"] = True
        self.client = app.app.test_client()
        self._old_dir = summary_mgr.SUMMARIES_DIR
        summary_mgr.SUMMARIES_DIR = tempfile.mkdtemp()
        self._old_request = coach_v4._request_chat

    def tearDown(self):
        summary_mgr.SUMMARIES_DIR = self._old_dir
        coach_v4._request_chat = self._old_request

    def test_generate_quiz(self):
        summary_mgr.save_summary("书", "章", "总结文本，薄弱点：弹性")

        def fake_request(messages, stream=False):
            return '{"questions":[{"type":"choice","question":"弹性是什么","options":["A","B","C","D"]}]}'

        coach_v4._request_chat = fake_request
        resp = self.client.post(
            "/api/quiz/generate", json={"book_name": "书", "chapter_title": "章"}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["questions"][0]["type"], "choice")

    def test_quiz_requires_summary(self):
        resp = self.client.post(
            "/api/quiz/generate", json={"book_name": "没有", "chapter_title": "章"}
        )
        self.assertEqual(resp.status_code, 400)


if __name__ == "__main__":
    unittest.main()
