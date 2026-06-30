"""Mermaid 图本地预览脚本

构造违规场景 + 真实项目依赖图，生成 HTML 预览页面，
用默认浏览器打开以确认跨层调用标红正确。

使用：
    python scripts/preview_mermaid.py
"""
from __future__ import annotations

import sys
import tempfile
import webbrowser
from pathlib import Path
from textwrap import dedent

# 让脚本能导入 agent 包
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from agent.observability.dependency_graph import DependencyGraphBuilder  # noqa: E402


def _build_violation_scenario(tmp_dir: Path) -> str:
    """构造带跨层违规的测试项目，返回其 Mermaid 字符串

    结构：
        agent/
        ├── orchestrator/core.py   # 违规：orchestrator → dao
        ├── cognitive/loop.py       # 违规：cognitive → server_routes
        ├── cognitive/repo_link.py  # 违规：cognitive → dao
        ├── tools/helper.py         # 违规：tools → dao
        ├── guardrails/guard.py     # 违规：guardrails → server_routes
        ├── data/repo.py            # 普通模块（dao 层）
        ├── server_routes/api.py    # 普通模块
        ├── utils/x.py              # tools → utils 跨层（非违规）
        └── lazy_loader_async.py    # 白名单文件
    """
    agent = tmp_dir / "agent"
    for sub in ["orchestrator", "cognitive", "data", "server_routes",
                "tools", "guardrails", "utils"]:
        (agent / sub).mkdir(parents=True)
        (agent / sub / "__init__.py").write_text("", encoding="utf-8")
    (agent / "__init__.py").write_text("", encoding="utf-8")

    # 违规场景 1: orchestrator → dao
    (agent / "orchestrator" / "core.py").write_text(dedent("""
        from agent.data.repo import Repository

        def run():
            return Repository()
    """), encoding="utf-8")

    # 违规场景 2: cognitive → server_routes
    (agent / "cognitive" / "loop.py").write_text(dedent("""
        from agent.server_routes.api import handle_request

        def loop():
            return handle_request()
    """), encoding="utf-8")

    # 违规场景 3: cognitive → dao
    (agent / "cognitive" / "repo_link.py").write_text(dedent("""
        import agent.data.repo as repo

        def get():
            return repo.Repository()
    """), encoding="utf-8")

    # 违规场景 4: tools → dao
    (agent / "tools" / "helper.py").write_text(dedent("""
        from agent.data.repo import Repository
        from agent.utils.x import helper

        def use():
            return Repository(), helper()
    """), encoding="utf-8")

    # 违规场景 5: guardrails → server_routes
    (agent / "guardrails" / "guard.py").write_text(dedent("""
        from agent.server_routes.api import handle_request

        def check():
            return handle_request()
    """), encoding="utf-8")

    # 普通模块
    (agent / "data" / "repo.py").write_text("# dao layer\n", encoding="utf-8")
    (agent / "server_routes" / "api.py").write_text("# routes\n", encoding="utf-8")
    (agent / "utils" / "x.py").write_text("def helper():\n    return 1\n", encoding="utf-8")

    # lazy_loader 白名单文件
    (agent / "lazy_loader_async.py").write_text(
        "registry.register('dynamic_module')\n", encoding="utf-8"
    )

    builder = DependencyGraphBuilder(root_dir=str(agent), trace_id="preview-violation")
    builder.build()
    return builder.to_mermaid()


def _build_real_project_mermaid() -> str:
    """构建真实项目依赖图的 Mermaid 字符串"""
    builder = DependencyGraphBuilder(
        root_dir="agent", trace_id="preview-real"
    )
    builder.build()
    return builder.to_mermaid()


def _build_html(violation_mermaid: str, real_mermaid: str) -> str:
    """构建包含两个 Mermaid 图的 HTML 预览页面

    说明：
    - 第一个图：违规场景（应能看到红色粗线和红色背景节点）
    - 第二个图：真实项目（无违规，应只看到灰色实线和黄色虚线）
    """
    # 从 markdown 中提取 mermaid 代码块
    def extract_mermaid_block(md: str) -> str:
        lines = md.split("\n")
        start = end = None
        for i, line in enumerate(lines):
            if line.strip() == "```mermaid":
                start = i + 1
            elif line.strip() == "```" and start is not None:
                end = i
                break
        if start is None or end is None:
            return md
        return "\n".join(lines[start:end])

    violation_block = extract_mermaid_block(violation_mermaid)
    real_block = extract_mermaid_block(real_mermaid)

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>架构依赖图 — Mermaid 标红预览</title>
    <script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
    <style>
        body {{
            font-family: -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif;
            margin: 0;
            padding: 24px;
            background: #f7f7fa;
            color: #222;
        }}
        h1 {{
            color: #c00;
            border-bottom: 2px solid #cc0000;
            padding-bottom: 8px;
        }}
        h2 {{
            margin-top: 32px;
            color: #444;
        }}
        .legend {{
            background: #fff3cd;
            border-left: 4px solid #ffc107;
            padding: 12px 16px;
            margin: 16px 0 24px;
            border-radius: 4px;
        }}
        .legend ul {{
            margin: 6px 0;
            padding-left: 20px;
        }}
        .legend code {{
            background: rgba(0,0,0,0.08);
            padding: 2px 6px;
            border-radius: 3px;
        }}
        .section {{
            background: #fff;
            border-radius: 8px;
            padding: 20px;
            margin: 16px 0;
            box-shadow: 0 1px 4px rgba(0,0,0,0.08);
        }}
        .badge {{
            display: inline-block;
            padding: 2px 8px;
            border-radius: 10px;
            font-size: 12px;
            font-weight: 600;
            margin-left: 8px;
        }}
        .badge-violation {{ background: #ff4444; color: #fff; }}
        .badge-ok {{ background: #28a745; color: #fff; }}
        .mermaid {{
            background: #fff;
            text-align: center;
            overflow-x: auto;
        }}
        .note {{
            font-size: 13px;
            color: #666;
            margin-top: 8px;
        }}
    </style>
</head>
<body>
    <h1>架构依赖图 — Mermaid 标红预览</h1>

    <div class="legend">
        <strong>图例说明：</strong>
        <ul>
            <li><code>--&gt;</code> 普通依赖（灰色实线）</li>
            <li><code>-.-&gt;</code> 跨层调用（允许但需关注，黄色虚线，目标节点黄色背景）</li>
            <li><code>==&gt;|违规|</code> 跨层违规调用（红色粗线，目标节点红色背景，需修复）</li>
        </ul>
        <div class="note">
            预期：违规场景图应包含 5 条红色粗线（违规边）和 2 个红色背景节点（违规目标节点去重：data.repo、server_routes.api）；
            真实项目图应无红色（0 违规），仅有少量黄色虚线（普通跨层调用）。
        </div>
    </div>

    <div class="section">
        <h2>1. 违规场景验证 <span class="badge badge-violation">预期 5 条违规</span></h2>
        <div class="note">构造的测试项目，包含 orchestrator→dao、cognitive→server_routes、cognitive→dao、tools→dao、guardrails→server_routes 五种违规。</div>
        <div class="mermaid">{violation_block}</div>
    </div>

    <div class="section">
        <h2>2. 真实项目依赖图 <span class="badge badge-ok">预期 0 违规</span></h2>
        <div class="note">云枢项目实际扫描结果：210 节点，420 边，0 违规（1 个豁免的循环依赖）。</div>
        <div class="mermaid">{real_block}</div>
    </div>

    <script>
        mermaid.initialize({{
            startOnLoad: true,
            theme: 'default',
            flowchart: {{
                useMaxWidth: true,
                htmlLabels: true,
                curve: 'basis'
            }}
        }});
    </script>
</body>
</html>
"""


def main() -> int:
    print("=" * 60)
    print("Mermaid 图本地预览 — 构造违规场景 + 真实项目对比")
    print("=" * 60)

    with tempfile.TemporaryDirectory(prefix="arch_preview_") as tmp:
        tmp_dir = Path(tmp)

        print("\n[1/3] 构造违规场景并生成 Mermaid...")
        violation_mermaid = _build_violation_scenario(tmp_dir)
        print(f"  ✓ 违规场景 Mermaid 长度: {len(violation_mermaid)} 字符")

        print("\n[2/3] 生成真实项目依赖图...")
        real_mermaid = _build_real_project_mermaid()
        print(f"  ✓ 真实项目 Mermaid 长度: {len(real_mermaid)} 字符")

        print("\n[3/3] 生成 HTML 预览并打开浏览器...")
        html = _build_html(violation_mermaid, real_mermaid)
        html_path = REPO_ROOT / "docs" / "architecture" / "mermaid_preview.html"
        html_path.parent.mkdir(parents=True, exist_ok=True)
        html_path.write_text(html, encoding="utf-8")
        print(f"  ✓ HTML 已生成: {html_path}")

        # 验证 HTML 内容包含预期的红色样式
        assert "fill:#ff4444" in html, "HTML 中未找到违规节点红色样式"
        assert "stroke:#ff0000" in html, "HTML 中未找到违规边红色样式"
        assert "==>|违规|" in html, "HTML 中未找到违规边粗线标记"
        print("  ✓ 红色样式断言通过：classDef violation + linkStyle 红色粗线")

        # 用默认浏览器打开
        webbrowser.open(html_path.as_uri())
        print(f"\n[完成] 浏览器应已打开：{html_path.as_uri()}")
        print("\n请在浏览器中确认：")
        print("  - 违规场景图：5 条红色粗线 + 2 个红色背景节点（去重）")
        print("  - 真实项目图：无红色，仅有灰色实线/黄色虚线")

    return 0


if __name__ == "__main__":
    sys.exit(main())
