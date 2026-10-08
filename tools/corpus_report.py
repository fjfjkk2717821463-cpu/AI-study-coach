# corpus_report.py
"""把示例语料库跑一遍真实处理流程，输出统计报告。

用途有两个：
1. 参赛材料里"自建数据语料库"需要可展示的规模与加工结果，这份报告就是证据；
2. 换用学校自己的教材时，先跑一遍这个脚本，能提前发现章节切分不理想的文件。

用法：
    python tools/corpus_report.py            # 扫描「语料库示例」目录
    python tools/corpus_report.py 某个目录    # 扫描指定目录
"""

import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

import book_utils

DEFAULT_DIR = os.path.join(BASE_DIR, "语料库示例")
SUPPORTED = (".md", ".markdown", ".txt", ".pdf", ".epub")
REPORT_NAME = "语料库统计报告.md"
# 说明文档不算教学语料，统计时跳过
SKIP_NAMES = {REPORT_NAME, "语料库说明.md"}


def _structure_hits(text):
    """检查结构是否被保留：标题、列表、表格。"""
    lines = text.splitlines()
    headings = sum(1 for line in lines if line.lstrip().startswith("#"))
    bullets = sum(1 for line in lines if line.lstrip().startswith(("-", "*", "+")))
    tables = sum(1 for line in lines if line.lstrip().startswith("|"))
    return headings, bullets, tables


def scan(directory):
    rows = []
    for name in sorted(os.listdir(directory)):
        path = os.path.join(directory, name)
        if not os.path.isfile(path) or not name.lower().endswith(SUPPORTED):
            continue
        if name in SKIP_NAMES:
            continue
        text, err = book_utils.load_book(path)
        if err:
            rows.append({"name": name, "error": err})
            continue

        auto_chapters, _ = book_utils.get_book_chapters(path, level="auto")
        big_chapters, _ = book_utils.get_book_chapters(path, level="chapter")
        small_chapters, _ = book_utils.get_book_chapters(path, level="section")

        auto_chapters = auto_chapters or []
        lengths = [len(content or "") for _, content in auto_chapters]
        headings, bullets, tables = _structure_hits(text)
        rows.append(
            {
                "name": name,
                "chars": len(text),
                "headings": headings,
                "bullets": bullets,
                "table_rows": tables,
                "auto": len(auto_chapters),
                "big": len(big_chapters or []),
                "small": len(small_chapters or []),
                "avg_len": round(sum(lengths) / len(lengths)) if lengths else 0,
                "min_len": min(lengths) if lengths else 0,
                "max_len": max(lengths) if lengths else 0,
                "titles": [title for title, _ in auto_chapters][:20],
            }
        )
    return rows


def render(rows, directory):
    lines = []
    lines.append("# 语料库统计报告")
    lines.append("")
    lines.append(f"扫描目录：`{os.path.relpath(directory, BASE_DIR)}`")
    lines.append("")
    lines.append("报告由 `tools/corpus_report.py` 调用项目自身的文档处理模块（`book_utils`）生成，")
    lines.append("统计的就是学习流程实际使用的语料，不是另外测算的数字。")
    lines.append("")
    lines.append("| 语料文件 | 字符数 | 标题数 | 列表项 | 表格行 | 自动切分 | 大章节 | 小章节 | 平均章节长度 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    total_chars = 0
    total_chapters = 0
    for row in rows:
        if row.get("error"):
            lines.append(f"| {row['name']} | 读取失败：{row['error']} | | | | | | | |")
            continue
        total_chars += row["chars"]
        total_chapters += row["auto"]
        lines.append(
            "| {name} | {chars} | {headings} | {bullets} | {table_rows} | {auto} | {big} | {small} | {avg_len} |".format(
                **row
            )
        )
    lines.append("")
    lines.append(f"合计：**{len([r for r in rows if not r.get('error')])} 个语料文件、"
                 f"{total_chars} 字、切分为 {total_chapters} 个可学习章节**。")
    lines.append("")
    for row in rows:
        if not row.get("titles"):
            continue
        lines.append(f"## {row['name']} 的章节清单")
        lines.append("")
        for title in row["titles"]:
            lines.append(f"- {title}")
        lines.append("")
    return "\n".join(lines)


def main():
    directory = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DIR
    if not os.path.isdir(directory):
        print("目录不存在：", directory)
        return
    rows = scan(directory)
    if not rows:
        print("目录里没有可处理的语料文件。")
        return
    report = render(rows, directory)
    out_path = os.path.join(directory, "语料库统计报告.md")
    with open(out_path, "w", encoding="utf-8") as handle:
        handle.write(report + "\n")
    print(report)
    print("\n已写入：", out_path)


if __name__ == "__main__":
    main()
