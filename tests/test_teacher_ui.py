import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app
import classroom_mgr
import coach_v4
import review_mgr
import summary_mgr


class TeacherPageTests(unittest.TestCase):
    def setUp(self):
        app.app.config["TESTING"] = True
        self.client = app.app.test_client()
        self._old_key = coach_v4.load_api_key

    def tearDown(self):
        coach_v4.load_api_key = self._old_key

    def test_teacher_page_renders(self):
        resp = self.client.get("/teacher")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        for element in ("metrics", "weakList", "taskList", "adviceList", "classSelect"):
            self.assertIn('id="' + element + '"', html)
        self.assertIn("教师端", html)

    def test_student_page_has_class_entry(self):
        coach_v4.load_api_key = lambda: "sk-test"
        html = self.client.get("/").get_data(as_text=True)
        self.assertIn('id="classBtn"', html)
        self.assertIn('id="classSection"', html)
        self.assertIn('id="joinClassBtn"', html)


class SummaryAutoReportTests(unittest.TestCase):
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

        self._old_summaries = summary_mgr.SUMMARIES_DIR
        summary_mgr.SUMMARIES_DIR = os.path.join(self._tmp, "summaries")
        self._old_reviews = review_mgr.REVIEWS_FILE
        review_mgr.REVIEWS_FILE = os.path.join(self._tmp, "reviews.json")

        self._old_generate = coach_v4.generate_summary
        coach_v4.generate_summary = lambda history, system_prompt, prompt: (
            "本章小结。\n【概念清单】\n- 弹性（待巩固）\n- 均衡（掌握）\n"
        )

        app.app.config["TESTING"] = True
        self.client = app.app.test_client()
        app.SESSIONS.clear()

    def tearDown(self):
        classroom_mgr.CLASSROOM_DIR = self._old_dir
        classroom_mgr.CLASSES_FILE = self._old_classes
        classroom_mgr.IDENTITY_FILE = self._old_identity
        summary_mgr.SUMMARIES_DIR = self._old_summaries
        review_mgr.REVIEWS_FILE = self._old_reviews
        coach_v4.generate_summary = self._old_generate
        app.SESSIONS.clear()

    def _session(self):
        app.SESSIONS["t1"] = {
            "mode": "book",
            "book_name": "西方经济学",
            "book_path": "",
            "chapter_text": "",
            "subject": "第三章 弹性",
            "system_prompt": "system",
            "explain_level": "medium",
            "history": [
                {"role": "user", "content": "弹性是价格变化引起需求量变化的程度"},
                {"role": "assistant", "content": "很好，继续说边界条件"},
            ],
            "save_path": None,
            "last_active": time.time(),
            "started_at": time.time() - 600,
        }

    def test_summary_syncs_to_joined_class(self):
        class_item = classroom_mgr.create_class("计科2401")
        classroom_mgr.remember_class(class_item["id"])
        classroom_mgr.set_display_name("小明")
        self._session()

        data = self.client.post("/api/summary", json={"session_id": "t1"}).get_json()
        self.assertEqual(data["classroom"]["synced"], 1)
        self.assertEqual(data["classroom"]["classes"], ["计科2401"])

        identity = classroom_mgr.load_identity()
        record = classroom_mgr.load_record(class_item["id"], identity["student_id"])
        self.assertIsNotNone(record)
        chapter = record["chapters"][0]
        self.assertEqual(chapter["chapter_title"], "第三章 弹性")
        self.assertEqual(chapter["book_name"], "西方经济学")
        self.assertEqual(chapter["rounds"], 1)
        self.assertGreaterEqual(chapter["minutes"], 9)
        self.assertEqual(
            [item["level"] for item in chapter["concepts"]], ["待巩固", "掌握"]
        )
        self.assertEqual(chapter["evidence"][0]["term"], "弹性")
        self.assertIn("弹性是价格变化引起需求量变化的程度", chapter["evidence"][0]["quote"])

    def test_summary_without_class_skips_sync(self):
        self._session()
        data = self.client.post("/api/summary", json={"session_id": "t1"}).get_json()
        self.assertEqual(data["classroom"]["synced"], 0)
        self.assertEqual(data["classroom"]["classes"], [])

    def test_synced_data_reaches_teacher_overview(self):
        class_item = classroom_mgr.create_class("计科2401")
        classroom_mgr.remember_class(class_item["id"])
        self._session()
        self.client.post("/api/summary", json={"session_id": "t1"})

        stats = self.client.get(
            f"/api/teacher/overview?class_id={class_item['id']}"
        ).get_json()["stats"]
        self.assertEqual(stats["students_reported"], 1)
        self.assertEqual(stats["weak_concepts"][0]["term"], "弹性")
        self.assertEqual(stats["weak_concepts"][0]["count"], 1)


if __name__ == "__main__":
    unittest.main()
