# pdf

> LLM 整理，逐条经**原文引用校验**：verified 29 / 29（未通过的已丢弃）。

## 何时使用

- 需要全面处理 PDF 时使用：提取文本和表格、创建新 PDF、合并/分割文档以及处理表单。
- 需要填写 PDF 表单，或大规模地以编程方式处理、生成、分析 PDF 文档时使用。
- 需要填写 PDF 表单时，应阅读 forms.md 并按其说明操作。

## 步骤

- 用 pypdf 读取 PDF 并统计页数。
- 遍历各页调用 extract_text 提取文本。
- 合并多个 PDF：逐个读取文件并把每页加入 PdfWriter，再写出。
- 分割 PDF：逐页新建 PdfWriter 并分别写出为单独文件。
- 读取 PDF 元数据（标题、作者、主题、创建者）。
- 旋转页面：对页面调用 rotate 后加入 writer 并写出。
- 用 pdfplumber 按布局提取文本。
- 用 pdfplumber 提取表格并逐行打印。
- 高级表格提取：把表格转为 DataFrame，合并后导出 Excel。
- 用 reportlab 的 canvas 创建基础 PDF 并保存。
- 用 reportlab 的 SimpleDocTemplate 与 Platypus 创建多页 PDF。
- 用 pdftotext 提取文本，可用 -layout 保留布局、用 -f/-l 指定页范围。
- 用 qpdf 合并、分割、旋转页面及解密。
- 用 pdftk 合并、分割（burst）和旋转。
- 扫描版 PDF 先转图像再用 pytesseract 做 OCR。
- 添加水印：读取水印页并对每页 merge_page 后写出。
- 用 pdfimages 提取 PDF 中的图像。
- 为 PDF 添加密码保护。

## 参数

- rotate 的参数为旋转角度，示例为顺时针 90 度。
- pdftotext 的 -f 与 -l 指定起止页，示例为第 1 到 5 页。
- qpdf 旋转参数格式为 --rotate=+90:1，表示第 1 页旋转 90 度。
- encrypt 接收用户密码与所有者密码两个参数。

## 约束 / 反例

- OCR 扫描件需先安装 pytesseract 与 pdf2image。
- pdftk 仅在可用时使用。
- 高级功能、JavaScript 库与详细示例需参阅 reference.md。

## 示例

- 分割 PDF 的完整示例代码。
