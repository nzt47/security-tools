"""Office 文档的纯结构处理（E3-2 纯库路线的一部分）。

这里只放**不依赖 LibreOffice / python-docx** 的能力：.docx 本质是 zip + OOXML，
正文与表格文字用标准库就能取出来。渲染/转换/公式重算依赖 LibreOffice，不在本包。
"""

__all__: list = []
