import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app
import session_mgr


class MarkdownSanitizeTests(unittest.TestCase):
    def test_event_handler_stripped(self):
        html = app._markdown_to_html('<img src="x" onerror="alert(1)">')
        self.assertNotIn("onerror", html)
        self.assertNotIn("alert", html)

    def test_script_tag_stripped(self):
        html = app._markdown_to_html("<script>alert(1)</script>**hello**")
        self.assertNotIn("<script", html.lower())
        self.assertIn("<strong>hello</strong>", html)

    def test_plain_markdown_kept(self):
        html = app._markdown_to_html("## 标题\n\n- 项目一\n- 项目二")
        self.assertIn("<h2>标题</h2>", html)
        self.assertIn("<li>项目一</li>", html)


class PublicUrlTests(unittest.TestCase):
    def test_private_addresses_rejected(self):
        for url in (
            "http://127.0.0.1/admin",
            "http://10.0.0.8/x",
            "http://192.168.1.1/x",
            "http://169.254.169.254/latest/meta-data",
            "http://[::1]/x",
        ):
            self.assertFalse(app._is_public_url(url), url)

    def test_public_address_accepted(self):
        self.assertTrue(app._is_public_url("https://8.8.8.8/"))

    def test_bad_scheme_rejected(self):
        self.assertFalse(app._is_public_url("file:///etc/passwd"))


class PathValidationTests(unittest.TestCase):
    def setUp(self):
        self.book_root = tempfile.mkdtemp()
        self.session_root = tempfile.mkdtemp()
        self._old_books = app.BOOKS_DIR
        self._old_sessions = session_mgr.SESSIONS_DIR
        app.BOOKS_DIR = self.book_root
        session_mgr.SESSIONS_DIR = self.session_root

    def tearDown(self):
        app.BOOKS_DIR = self._old_books
        session_mgr.SESSIONS_DIR = self._old_sessions

    def test_book_inside_dir_accepted(self):
        path = os.path.join(self.book_root, "demo.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write("hi")
        self.assertTrue(app._valid_book_path(path))

    def test_book_outside_dir_rejected(self):
        outside = os.path.join(tempfile.mkdtemp(), "demo.txt")
        with open(outside, "w", encoding="utf-8") as f:
            f.write("hi")
        self.assertFalse(app._valid_book_path(outside))
        self.assertFalse(app._valid_book_path(os.path.join(self.book_root, "..", "x.txt")))

    def test_session_path_requires_json(self):
        good = os.path.join(self.session_root, "a.json")
        with open(good, "w", encoding="utf-8") as f:
            f.write("{}")
        self.assertTrue(app._valid_session_path(good))
        self.assertFalse(
            app._valid_session_path(os.path.join(self.session_root, "..", "a.json"))
        )
        self.assertFalse(app._valid_session_path("/etc/passwd"))


if __name__ == "__main__":
    unittest.main()
