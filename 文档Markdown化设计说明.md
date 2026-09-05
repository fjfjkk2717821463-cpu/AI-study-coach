# 文档 Markdown 化设计说明

## 1. 背景与问题

早期版本在导入书籍、网页后，只保留纯文本。这样虽然简单，但会丢失大量结构信息：

- 标题层级被打平，章节切分只能依赖「第一章 / Chapter」这类正则猜测；
- 列表、表格、引用、代码块被拆散；
- 原文比对时无法引用「哪个标题下的哪一点」；
- 大模型对无结构长文本的理解不如对结构化文本稳定。

因此决定：**在把文档送入学习流程之前，先尽量转换成 Markdown**，让结构信息一路保留到提示词、章节切分和原文比对中。

## 2. 分格式决策

| 格式 | 是否转 Markdown | 方式 | 理由 |
| --- | --- | --- | --- |
| EPUB | 是 | HTML 源转 Markdown | 本来就是 HTML，转换无损且收益最大 |
| 网页 | 是 | 提取正文容器后转 Markdown | 保留标题、列表、表格、链接 |
| PDF | 是（尽力而为） | `pymupdf4llm` 优先，失败退回 pdfplumber/PyPDF2 | 专门工具能保留标题和表格；硬转反而会混入噪声 |
| TXT | 否 | 保持纯文本 | 本身没有结构，转换不会凭空产生信息 |
| MD | 直接使用 | 按文本读取 | 已具备结构 |

## 3. 实现方案

### 3.1 HTML → Markdown

使用 `markdownify`，设置：

- `heading_style="ATX"`：标题输出为 `#` 形式；
- `bullets="-"`：统一无序列表符号；
- `strip`：移除 script、style、nav、footer、aside 等噪声。

### 3.2 章节切分升级

`split_chapters` 现在同时识别：

- Markdown 标题 `#` 到 `######`，并用 `#` 数量表示层级；
- 原有的中文/英文章节正则，中文「章/部分/篇」视为一级，「节/讲/Section/Unit」视为二级。

层级选择逻辑：

- `chapter`（大章节）：只在一级标题处切分；
- `section`（小章节）：在所有标题处切分；
- `auto`：一级标题数 ≥ 2 时用大章节，否则用小章节。

### 3.3 EPUB 目录切分与 Markdown 结合

- 按书脊顺序读取正文文档，每篇转成 Markdown；
- 大章节模式按原书目录顶层条目合并子章节的 Markdown；
- 小章节模式一个文档对应多个目录条目时，按 Markdown 标题进一步切分；
- 目录不可用时，退回「按文档 + 标题」的切分。

### 3.4 PDF 的降级链

`pymupdf4llm.to_markdown()` → 失败时 `pdfplumber` 文本提取 → 再失败时 `PyPDF2`。任何一层成功都返回可用内容，避免因为一个格式问题让整本书不可用。

### 3.5 网页导入

网页导入现在保存为 `.md` 文件：标题写入 `# 标题`，正文写入保留结构的 Markdown，然后走与 EPUB 相同的章节切分逻辑。

## 4. 权衡与边界

- **数学公式**：暂不做公式到 LaTeX 的转换；未来可针对 `\(...\)`、`$...$` 做保留处理。
- **图片**：转换时会丢弃图片，仅保留文字；对图表密集型 PDF 效果有限。
- **PDF 噪声**：页眉、页脚、分栏可能残留；如果 `pymupdf4llm` 效果不好，会自动退回旧逻辑，不影响可用性。
- **Token 成本**：Markdown 增加少量符号，开销可忽略，但结构带来的理解收益更明显。

## 5. 涉及的代码位置

- `book_utils.py`：`_html_to_markdown`、`_pdf_to_markdown`、`_extract_epub_text`、`_extract_epub_chapters`、`split_chapters`、`load_book`。
- `app.py`：`_fetch_web_text` 返回 Markdown；网页导入保存为 `.md`；文件导入接受 `.md`。
- `requirements.txt` / `requirements-cloud.txt`：新增 `markdownify`、`pymupdf4llm`。

## 6. 验证记录

- Markdown 标题的大章节/小章节/自动三种切分均通过；
- EPUB 中的列表和表格能完整保留为 Markdown；
- 网页正文能转成带标题和链接的 Markdown；
- 缺少 `pymupdf4llm` 或转换失败时，PDF 能自动降级。
