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


class ChapterSplitNoiseTests(unittest.TestCase):
    """教材 PDF 的页眉、目录、页码不应该被当成章节，也不该留在正文里。"""

    def test_running_headers_collapse_into_one_chapter(self):
        # 每页页眉都印着「第一章 绪论」的典型情况
        lines = []
        for index in range(6):
            lines.append("第一章 绪论")
            lines.append("第 %d 页的正文内容" % index)
        lines.append("第二章 细胞的基本功能")
        lines.append("这一章讲细胞的基本功能。")
        chapters = book_utils.split_chapters("\n".join(lines), level="chapter")
        titles = [title for title, _ in chapters]
        self.assertEqual(titles, ["第一章 绪论", "第二章 细胞的基本功能"])
        self.assertNotIn("第一章 绪论", chapters[0][1])  # 页眉已从正文中清掉
        self.assertIn("这一章讲细胞的基本功能。", chapters[1][1])

    def test_toc_lines_are_not_treated_as_chapters(self):
        text = (
            "目录\n"
            "第一章 绪论……1\n"
            "第二章 细胞的基本功能……25\n"
            "第一章 绪论\n"
            "这里是第一章的正文。"
        )
        chapters = book_utils.split_chapters(text, level="chapter")
        titles = [title for title, _ in chapters]
        # 目录条目不会成为章节；正文里的真标题才是
        self.assertEqual(titles, ["前言/引言", "第一章 绪论"])
        self.assertIn("目录", chapters[0][1])
        self.assertIn("这里是第一章的正文。", chapters[1][1])

    def test_chapter_line_with_page_number_is_toc(self):
        text = "第三章 血液 45\n第三章 血液\n正文内容"
        chapters = book_utils.split_chapters(text, level="chapter")
        titles = [title for title, _ in chapters]
        # 带页码的那一行是目录条目，真正的章节只有正文里那一个
        self.assertEqual(titles.count("第三章 血液"), 1)
        self.assertEqual(titles[-1], "第三章 血液")
        self.assertIn("正文内容", chapters[-1][1])

    def test_page_numbers_removed_from_content(self):
        text = "第一章 总论\n正文一\n- 12 -\n正文二\n\n345\n正文三"
        chapters = book_utils.split_chapters(text, level="chapter")
        content = chapters[0][1]
        self.assertIn("正文一", content)
        self.assertIn("正文三", content)
        self.assertNotIn("- 12 -", content)
        self.assertNotIn("345", content)

    def test_long_sentence_starting_with_chapter_word_is_not_boundary(self):
        text = "第一章 总论\n正文\n第三章讨论了血液的组成，这里展开说明一下它的作用。\n更多正文"
        chapters = book_utils.split_chapters(text, level="chapter")
        self.assertEqual([title for title, _ in chapters], ["第一章 总论"])

    def test_section_headers_still_split(self):
        text = "第一章 总论\n正文A\n第一节 概述\n正文B\n第二节 细节\n正文C"
        chapters = book_utils.split_chapters(text, level="section")
        titles = [title for title, _ in chapters]
        self.assertIn("第一节 概述", titles)
        self.assertIn("第二节 细节", titles)

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
