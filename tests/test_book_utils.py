import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import book_utils


class ChapterSplitTests(unittest.TestCase):
    def test_markdown_headings(self):
        text = "# 第一章 供给\n内容A\n## 1.1 需求\n内容B\n## 1.2 均衡\n内容C"
        chapters = book_utils.split_chapters(text, level="section")
        titles = [title for title, _ in chapters]
        self.assertEqual(titles, ["第一章 供给", "1.1 需求", "1.2 均衡"])

    def test_chinese_chapter_level(self):
        text = "第一章 总论\n内容1\n第二章 分论\n内容2\n第一节 小节\n内容3"
        chapters = book_utils.split_chapters(text, level="chapter")
        titles = [title for title, _ in chapters]
        self.assertEqual(titles, ["第一章 总论", "第二章 分论"])

    def test_nested_div_splitting(self):
        from bs4 import BeautifulSoup

        html = (
            "<body><div><h2>标题一</h2>"
            "<div><p>第一段</p><p>第二段</p></div>"
            "<h3>标题二</h3><p>第三段</p></div></body>"
        )
        body = BeautifulSoup(html, "html.parser").body
        sections = book_utils._split_body_by_headings(body)
        self.assertEqual([t for t, _ in sections], ["标题一", "标题二"])
        self.assertIn("第一段", sections[0][1])
        self.assertIn("第二段", sections[0][1])
        self.assertIn("第三段", sections[1][1])


class EncodingTests(unittest.TestCase):
    def test_gb18030_decoding(self):
        sample = "你好，世界。这是一段用于测试中文编码识别的文字，" * 20
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
            f.write(sample.encode("gb18030"))
            path = f.name
        try:
            text, err = book_utils._read_text_file(path)
            self.assertIsNone(err)
            self.assertEqual(text, sample)
        finally:
            os.remove(path)


class SizeLimitTests(unittest.TestCase):
    def test_size_check(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"x" * 100)
            path = f.name
        try:
            self.assertTrue(book_utils._check_size(path))
        finally:
            os.remove(path)

    def test_oversized_book_rejected(self):
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
            f.write(b"x" * (book_utils.MAX_BOOK_BYTES + 1))
            path = f.name
        try:
            text, err = book_utils.load_book(path)
            self.assertIsNone(text)
            self.assertIn("过大", err)
        finally:
            os.remove(path)


if __name__ == "__main__":
    unittest.main()
