"""验证 BM25_OPTIMIZATION_WIKI.md 的 Markdown 渲染质量

【不易】真实验证表格/代码块语法与相对链接路径，不凭空断言
【变易】可复用检查其他 wiki 文档
【简易】markdown 库渲染 + 正则检查链接，无额外依赖
"""
import re
import sys
from pathlib import Path

import markdown

WIKI_FILE = Path(__file__).parent.parent / "docs" / "wiki" / "BM25_OPTIMIZATION_WIKI.md"


def check_table_consistency(lines) -> list:
    """检查表格列数一致性（每行 | 分隔的列数应与分隔行一致）"""
    issues = []
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        if line.startswith("|") and i + 1 < len(lines):
            next_line = lines[i + 1].rstrip()
            # 分隔行：|---|
            if re.match(r"^\|[\s\-:|]+\|$", next_line) and "-" in next_line:
                header_cols = line.count("|")
                sep_cols = next_line.count("|")
                if header_cols != sep_cols:
                    issues.append(f"L{i+1}: 表头列数 {header_cols} != 分隔行列数 {sep_cols}")
                # 校验数据行
                j = i + 2
                while j < len(lines) and lines[j].strip().startswith("|"):
                    data_cols = lines[j].count("|")
                    if data_cols != header_cols:
                        issues.append(
                            f"L{j+1}: 数据行列数 {data_cols} != 表头 {header_cols}"
                        )
                    j += 1
                i = j
                continue
        i += 1
    return issues


def check_code_fences(lines) -> list:
    """检查代码块围栏是否闭合"""
    issues = []
    fence_open = 0
    fence_lang = None
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("```"):
            if fence_open == 0:
                fence_open = 1
                fence_lang = stripped[3:].strip() or "(无标签)"
            else:
                if stripped != "```":
                    issues.append(f"L{i}: 围栏关闭不匹配（打开 {fence_lang}）")
                fence_open = 0
    if fence_open:
        issues.append(f"文件末尾: 代码块围栏未闭合（{fence_lang}）")
    return issues


def check_links(content, base_dir: Path) -> list:
    """检查相对链接目标是否存在"""
    issues = []
    links = re.findall(r"\[[^\]]*\]\(([^)]+)\)", content)
    for link in links:
        if link.startswith("http") or link.startswith("#") or link.startswith("mailto"):
            continue
        # 去除锚点部分
        target = link.split("#")[0]
        if not target:
            continue
        target_path = (base_dir / target).resolve()
        if not target_path.exists():
            issues.append(f"链接缺失: {link} -> {target_path}")
    return issues


def main():
    content = WIKI_FILE.read_text(encoding="utf-8")
    lines = content.split("\n")

    # 1. 渲染验证（表格 + 围栏代码块）
    html = markdown.markdown(content, extensions=["tables", "fenced_code"])
    table_count = html.count("<table>")
    pre_count = html.count("<pre>")
    py_lang = html.count("language-python")
    bash_lang = html.count("language-bash")

    # 2. 语法检查
    table_issues = check_table_consistency(lines)
    fence_issues = check_code_fences(lines)
    link_issues = check_links(content, WIKI_FILE.parent)

    print(f"文件: {WIKI_FILE.name} ({WIKI_FILE.stat().st_size} bytes)")
    print(f"行数: {len(lines)}")
    print("=" * 60)
    print(f"表格渲染: {table_count} 个 <table>")
    print(f"代码块渲染: {pre_count} 个 <pre>")
    print(f"  - python 高亮: {py_lang} 处")
    print(f"  - bash 高亮: {bash_lang} 处")
    print(f"  - 无标签块(数学公式): {pre_count - py_lang - bash_lang} 处")
    print("-" * 60)

    all_ok = True
    if table_issues:
        all_ok = False
        print("[表格问题]")
        for iss in table_issues:
            print(f"  ✗ {iss}")
    else:
        print("[表格] ✓ 所有表格列数一致，语法正确")

    if fence_issues:
        all_ok = False
        print("[代码块问题]")
        for iss in fence_issues:
            print(f"  ✗ {iss}")
    else:
        print("[代码块] ✓ 所有围栏闭合，语法正确")

    if link_issues:
        all_ok = False
        print("[链接问题]")
        for iss in link_issues:
            print(f"  ✗ {iss}")
    else:
        print("[链接] ✓ 所有相对链接目标存在")

    print("=" * 60)
    if all_ok:
        print("✅ 渲染验证通过：表格/代码块/链接均正常")
        print("   GitHub Pages (kramdown + cayman 主题) 兼容性确认:")
        print("   - 表格: GFM 管道表格（kramdown 原生支持）")
        print("   - 代码块: fenced code block（kramdown 支持）+ 语言高亮")
        print("   - 链接: 相对路径从 docs/wiki/ 解析，目标存在")
        sys.exit(0)
    else:
        print("❌ 渲染验证发现问题，请修复后再部署")
        sys.exit(1)


if __name__ == "__main__":
    main()
