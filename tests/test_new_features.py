import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app
import coach_v4
import session_mgr


class SessionManageTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.mkdtemp()
        self._old = session_mgr.SESSIONS_DIR
        session_mgr.SESSIONS_DIR = self._dir

    def tearDown(self):
        session_mgr.SESSIONS_DIR = self._old

    def test_search_rename_delete(self):
        path = session_mgr.write_session(
            session_mgr.new_session_path("电子书", "热力学"),
            "电子书",
            "热力学",
            [{"role": "user", "content": "hi"}],
        )
        self.assertTrue(os.path.exists(path))
        self.assertEqual(len(session_mgr.search_sessions("热力")), 1)

        self.assertTrue(session_mgr.rename_session(path, "热力学第二定律"))
        self.assertEqual(
            session_mgr.search_sessions("第二定律")[0]["subject"], "热力学第二定律"
        )

        self.assertTrue(session_mgr.delete_session(path))
        self.assertFalse(os.path.exists(path))


class ModelConfigTests(unittest.TestCase):
    def setUp(self):
        self._old = coach_v4.MODEL_CONFIG_FILE
        coach_v4.MODEL_CONFIG_FILE = os.path.join(tempfile.mkdtemp(), "model_config.json")

    def tearDown(self):
        coach_v4.MODEL_CONFIG_FILE = self._old

    def test_defaults_to_deepseek(self):
        cfg = coach_v4.load_model_config()
        self.assertEqual(cfg["provider"], "deepseek")
        self.assertEqual(cfg["base_url"], "https://api.deepseek.com/chat/completions")
        self.assertEqual(cfg["model"], "deepseek-chat")

    def test_custom_roundtrip(self):
        coach_v4.save_model_config(
            {
                "provider": "custom",
                "base_url": "https://api.example.com/v1/chat/completions",
                "model": "example-model",
                "api_key": "sk-abc",
            }
        )
        cfg = coach_v4.load_model_config()
        self.assertEqual(cfg["model"], "example-model")
        self.assertEqual(cfg["api_key"], "sk-abc")
        self.assertEqual(cfg["base_url"], "https://api.example.com/v1/chat/completions")


class SettingsApiTests(unittest.TestCase):
    def setUp(self):
        app.app.config["TESTING"] = True
        self.client = app.app.test_client()
        self._old = coach_v4.MODEL_CONFIG_FILE
        coach_v4.MODEL_CONFIG_FILE = os.path.join(tempfile.mkdtemp(), "model_config.json")

    def tearDown(self):
        coach_v4.MODEL_CONFIG_FILE = self._old

    def test_settings_preset_and_validation(self):
        resp = self.client.post("/api/settings", json={"provider": "qwen"})
        self.assertEqual(resp.status_code, 200)

        data = self.client.get("/api/settings").get_json()
        self.assertEqual(data["provider"], "qwen")
        self.assertIn("qwen-plus", data["model"])

        bad = self.client.post(
            "/api/settings",
            json={"provider": "custom", "base_url": "ftp://bad", "model": "m"},
        )
        self.assertEqual(bad.status_code, 400)

    def test_regenerate_replaces_last_assistant(self):
        app.SESSIONS.clear()
        app.SESSIONS["t1"] = {
            "mode": "book",
            "book_name": "",
            "book_path": "",
            "subject": "测试章节",
            "system_prompt": "system",
            "explain_level": "medium",
            "history": [
                {"role": "user", "content": "你好"},
                {"role": "assistant", "content": "旧回答"},
            ],
            "save_path": None,
            "last_active": time.time(),
        }
        original = coach_v4.stream_chat

        def fake_stream(messages):
            yield "新的回答"

        coach_v4.stream_chat = fake_stream
        try:
            resp = self.client.post("/api/chat/regenerate", json={"session_id": "t1"})
            body = resp.get_data(as_text=True)
            self.assertIn("新的回答", body)
            self.assertEqual(app.SESSIONS["t1"]["history"][-1]["content"], "新的回答")
            self.assertEqual(len(app.SESSIONS["t1"]["history"]), 2)
        finally:
            coach_v4.stream_chat = original
            app.SESSIONS.pop("t1", None)


if __name__ == "__main__":
    unittest.main()
