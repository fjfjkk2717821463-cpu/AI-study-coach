import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import classroom_mgr


class ClassroomTestCase(unittest.TestCase):
    """把数据目录指到临时目录，避免测试污染真实用户数据。"""

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

    def tearDown(self):
        classroom_mgr.CLASSROOM_DIR = self._old_dir
        classroom_mgr.CLASSES_FILE = self._old_classes
        classroom_mgr.IDENTITY_FILE = self._old_identity

    def _class(self, name="计科2401"):
        return classroom_mgr.create_class(name, teacher_name="张老师")

    def _report(self, class_id, student_id, name, concepts, minutes=10, rounds=2,
                evidence=None, chapter="第三章 弹性"):
        snapshot = classroom_mgr.build_snapshot(
            "西方经济学",
            chapter,
            concepts,
            minutes=minutes,
            rounds=rounds,
            evidence=evidence or [],
        )
        return classroom_mgr.report_record(class_id, student_id, name, snapshot)


class ClassLifecycleTests(ClassroomTestCase):
    def test_create_and_lookup(self):
        item = self._class()
        self.assertTrue(item["code"])
        self.assertEqual(len(item["code"]), classroom_mgr.CODE_LENGTH)
        self.assertEqual(len(classroom_mgr.list_classes()), 1)
        self.assertEqual(classroom_mgr.get_class(item["id"])["name"], "计科2401")
        self.assertEqual(
            classroom_mgr.find_class_by_code(item["code"].lower())["id"], item["id"]
        )

    def test_codes_are_unique(self):
        codes = {self._class(f"班级{i}")["code"] for i in range(8)}
        self.assertEqual(len(codes), 8)

    def test_empty_name_rejected(self):
        self.assertIsNone(classroom_mgr.create_class("   "))

    def test_reset_code_changes_code(self):
        item = self._class()
        new_code = classroom_mgr.reset_class_code(item["id"])
        self.assertNotEqual(new_code, item["code"])
        self.assertIsNone(classroom_mgr.find_class_by_code(item["code"]))


class RosterTests(ClassroomTestCase):
    def test_join_with_wrong_code(self):
        ok, message = classroom_mgr.join_class("ZZZZZZ", "s_1", "小明")
        self.assertFalse(ok)
        self.assertIn("班级码", message)

    def test_join_and_deduplicate(self):
        item = self._class()
        ok, result = classroom_mgr.join_class(item["code"], "s_1", "小明")
        self.assertTrue(ok)
        self.assertEqual(result["student"]["display_name"], "小明")
        classroom_mgr.join_class(item["code"], "s_1", "小明")
        self.assertEqual(len(classroom_mgr.list_roster(item["id"])), 1)

    def test_display_name_updated_not_cleared(self):
        item = self._class()
        classroom_mgr.join_class(item["code"], "s_1", "小明")
        classroom_mgr.ensure_student(item["id"], "s_1", "")
        roster = classroom_mgr.list_roster(item["id"])
        self.assertEqual(roster[0]["display_name"], "小明")

    def test_remove_student_drops_record(self):
        item = self._class()
        self._report(item["id"], "s_1", "小明", [{"term": "弹性", "level": "待巩固"}])
        classroom_mgr.remove_student(item["id"], "s_1")
        self.assertEqual(classroom_mgr.list_roster(item["id"]), [])
        self.assertIsNone(classroom_mgr.load_record(item["id"], "s_1"))


class RecordTests(ClassroomTestCase):
    def test_report_requires_class(self):
        ok, message = self._report("c_missing", "s_1", "小明", [])
        self.assertFalse(ok)
        self.assertIn("班级不存在", message)

    def test_report_requires_chapter(self):
        item = self._class()
        snapshot = classroom_mgr.build_snapshot("书", "", [])
        ok, message = classroom_mgr.report_record(item["id"], "s_1", "小明", snapshot)
        self.assertFalse(ok)
        self.assertIn("章节", message)

    def test_same_chapter_overwritten(self):
        item = self._class()
        self._report(item["id"], "s_1", "小明", [{"term": "弹性", "level": "待巩固"}])
        self._report(item["id"], "s_1", "小明", [{"term": "弹性", "level": "掌握"}])
        record = classroom_mgr.load_record(item["id"], "s_1")
        self.assertEqual(len(record["chapters"]), 1)
        self.assertEqual(record["chapters"][0]["concepts"][0]["level"], "掌握")

    def test_student_id_cannot_escape_directory(self):
        item = self._class()
        self._report(item["id"], "../../evil", "小明", [{"term": "弹性"}])
        records_dir = os.path.join(
            classroom_mgr.CLASSROOM_DIR, "classes", item["id"], "records"
        )
        files = os.listdir(records_dir)
        self.assertEqual(len(files), 1)
        self.assertNotIn("..", files[0])
        self.assertTrue(
            os.path.realpath(os.path.join(records_dir, files[0])).startswith(
                os.path.realpath(records_dir)
            )
        )

    def test_concepts_normalized(self):
        concepts = classroom_mgr.normalize_concepts(
            ["", "弹性", {"term": "边际效用"}, {"term": "弹性"}, {"term": "税负", "level": "掌握"}]
        )
        self.assertEqual([c["term"] for c in concepts], ["弹性", "边际效用", "税负"])
        self.assertEqual(concepts[0]["level"], classroom_mgr.LEVEL_WEAK)
        self.assertEqual(concepts[2]["level"], classroom_mgr.LEVEL_MASTERED)


class EvidenceTests(unittest.TestCase):
    def test_extract_picks_sentences_with_term(self):
        texts = [
            "需求价格弹性是指价格变化引起需求量变化的程度。它跟替代品有关。",
            "我还不太清楚弹性在什么情况下不成立",
        ]
        result = classroom_mgr.extract_evidence_quotes([{"term": "弹性"}], texts)
        self.assertEqual(len(result), 2)
        self.assertTrue(all(item["term"] == "弹性" for item in result))
        self.assertIn("需求价格弹性", result[0]["quote"])

    def test_extract_caps_per_term(self):
        texts = ["弹性一。弹性二。弹性三。弹性四。"]
        result = classroom_mgr.extract_evidence_quotes([{"term": "弹性"}], texts, max_per_term=2)
        self.assertEqual(len(result), 2)

    def test_extract_truncates_long_quote(self):
        texts = ["弹性" + "很" * 500]
        result = classroom_mgr.extract_evidence_quotes([{"term": "弹性"}], texts)
        self.assertLessEqual(len(result[0]["quote"]), classroom_mgr.EVIDENCE_MAX_CHARS)

    def test_extract_ignores_non_string(self):
        self.assertEqual(classroom_mgr.extract_evidence_quotes([{"term": "弹性"}], [None, 5]), [])


class AggregateTests(ClassroomTestCase):
    def setUp(self):
        super().setUp()
        self.item = self._class()
        self.class_id = self.item["id"]

    def test_weak_concept_ranking_and_evidence(self):
        self._report(
            self.class_id, "s_1", "小明",
            [{"term": "弹性", "level": "待巩固"}, {"term": "均衡", "level": "掌握"}],
            evidence=[{"term": "弹性", "quote": "弹性是价格变化的比例"}],
        )
        self._report(
            self.class_id, "s_2", "小红", [{"term": "弹性", "level": "待巩固"}]
        )
        self._report(
            self.class_id, "s_3", "小刚", [{"term": "税负", "level": "待巩固"}]
        )

        stats = classroom_mgr.aggregate_class(self.class_id)
        self.assertEqual(stats["students_total"], 3)
        self.assertEqual(stats["students_reported"], 3)
        self.assertEqual(stats["weak_concepts"][0]["term"], "弹性")
        self.assertEqual(stats["weak_concepts"][0]["count"], 2)
        self.assertEqual(stats["weak_concepts"][0]["evidence"][0]["quote"], "弹性是价格变化的比例")
        self.assertEqual({item["term"] for item in stats["weak_concepts"]}, {"弹性", "税负"})
        self.assertEqual(stats["chapter_stats"][0]["learned_count"], 3)
        self.assertEqual(stats["chapter_stats"][0]["mastered_count"], 1)

    def test_filters_by_chapter(self):
        self._report(self.class_id, "s_1", "小明", [{"term": "弹性"}], chapter="第三章 弹性")
        self._report(self.class_id, "s_1", "小明", [{"term": "税负"}], chapter="第四章 税负")
        stats = classroom_mgr.aggregate_class(self.class_id, chapters=["第四章 税负"])
        self.assertEqual([item["term"] for item in stats["weak_concepts"]], ["税负"])

    def test_avg_minutes(self):
        self._report(self.class_id, "s_1", "小明", [{"term": "弹性"}], minutes=10)
        self._report(self.class_id, "s_2", "小红", [{"term": "弹性"}], minutes=20)
        stats = classroom_mgr.aggregate_class(self.class_id)
        self.assertEqual(stats["avg_minutes"], 15.0)

    def test_assignment_progress(self):
        classroom_mgr.add_assignment(
            self.class_id, "西方经济学", ["第三章 弹性"], due_at="2026-09-25"
        )
        self._report(self.class_id, "s_1", "小明", [{"term": "弹性"}])
        stats = classroom_mgr.aggregate_class(self.class_id)
        assignment = stats["assignments"][0]
        self.assertEqual(assignment["completed_count"], 1)
        self.assertEqual(assignment["pending_count"], 0)

        self._report(self.class_id, "s_2", "小红", [{"term": "税负"}], chapter="第四章 税负")
        stats = classroom_mgr.aggregate_class(self.class_id)
        self.assertEqual(stats["assignments"][0]["pending_count"], 1)

    def test_missing_class_returns_none(self):
        self.assertIsNone(classroom_mgr.aggregate_class("c_nope"))

    def test_corrupt_record_ignored(self):
        records_dir = os.path.join(
            classroom_mgr.CLASSROOM_DIR, "classes", self.class_id, "records"
        )
        os.makedirs(records_dir, exist_ok=True)
        with open(os.path.join(records_dir, "broken.json"), "w", encoding="utf-8") as handle:
            handle.write("{ not json")
        self._report(self.class_id, "s_1", "小明", [{"term": "弹性"}])
        stats = classroom_mgr.aggregate_class(self.class_id)
        self.assertEqual(stats["students_reported"], 1)


class StorageToleranceTests(ClassroomTestCase):
    def test_corrupt_classes_file(self):
        os.makedirs(classroom_mgr.CLASSROOM_DIR, exist_ok=True)
        with open(classroom_mgr.CLASSES_FILE, "w", encoding="utf-8") as handle:
            handle.write("oops")
        self.assertEqual(classroom_mgr.list_classes(), [])
        self.assertIsNotNone(classroom_mgr.create_class("新班级"))

    def test_advice_cache_roundtrip(self):
        item = self._class()
        payload = {"advice": {"concepts": []}, "meta": {}}
        self.assertTrue(
            classroom_mgr.save_advice_cache(item["id"], "书", ["第一章"], payload)
        )
        self.assertEqual(
            classroom_mgr.load_advice_cache(item["id"], "书", ["第一章"])["advice"],
            payload["advice"],
        )
        self.assertIsNone(classroom_mgr.load_advice_cache(item["id"], "书", ["第二章"]))


class IdentityTests(ClassroomTestCase):
    def test_identity_is_stable(self):
        first = classroom_mgr.get_or_create_student_identity()
        second = classroom_mgr.get_or_create_student_identity()
        self.assertEqual(first["student_id"], second["student_id"])
        self.assertTrue(first["student_id"].startswith("s_"))

    def test_remember_class(self):
        item = self._class()
        classroom_mgr.remember_class(item["id"])
        classroom_mgr.remember_class(item["id"])
        classes = classroom_mgr.my_classes()
        self.assertEqual(len(classes), 1)
        self.assertEqual(classes[0]["name"], "计科2401")

    def test_missing_identity_file(self):
        self.assertEqual(classroom_mgr.my_classes(), [])


if __name__ == "__main__":
    unittest.main()
