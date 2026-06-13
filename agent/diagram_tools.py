"""架构图生成工具 — 云枢绘制系统架构图的能力

基于 Hermes architecture-diagram 模板生成美观的 HTML+SVG 架构图。
颜色方案：
  - frontend: 青色 (#22d3ee)
  - backend: 翡翠绿 (#34d399)
  - database: 紫色 (#a78bfa)
  - cloud: 琥珀色 (#fbbf24)
  - security: 玫瑰红 (#fb7185)
  - external: 灰色 (#94a3b8)
"""

import os
import json
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# 类型到颜色的映射
TYPE_COLORS = {
    "frontend": {"bg": "rgba(8, 51, 68, 0.4)", "stroke": "#22d3ee", "name": "Frontend", "dot": "cyan"},
    "backend": {"bg": "rgba(6, 78, 59, 0.4)", "stroke": "#34d399", "name": "Backend", "dot": "emerald"},
    "database": {"bg": "rgba(76, 29, 149, 0.4)", "stroke": "#a78bfa", "name": "Database", "dot": "violet"},
    "cloud": {"bg": "rgba(120, 53, 15, 0.3)", "stroke": "#fbbf24", "name": "Cloud Service", "dot": "amber"},
    "security": {"bg": "rgba(136, 19, 55, 0.4)", "stroke": "#fb7185", "name": "Security", "dot": "rose"},
    "external": {"bg": "rgba(30, 41, 59, 0.5)", "stroke": "#94a3b8", "name": "External", "dot": None},
}

# 布局常量
SVG_WIDTH = 1000
SVG_HEIGHT = 680
BOX_WIDTH = 180
BOX_HEIGHT = 56
MARGIN_X = 40
MARGIN_Y = 80
COL_GAP = 30
ROW_GAP = 30
COLS = 4  # 每行最多4个组件


def _get_color(type_name: str) -> dict:
    """根据组件类型获取颜色配置"""
    t = type_name.lower().replace(" ", "_")
    return TYPE_COLORS.get(t, TYPE_COLORS["external"])


def _render_svg_components(components: list) -> str:
    """将组件列表渲染为 SVG 元素字符串"""
    lines = []
    for i, comp in enumerate(components):
        name = comp.get("name", "")
        type_name = comp.get("type", "external")
        desc = comp.get("description", "")
        colors = _get_color(type_name)

        col = i % COLS
        row = i // COLS
        x = MARGIN_X + col * (BOX_WIDTH + COL_GAP)
        y = MARGIN_Y + row * (BOX_HEIGHT + ROW_GAP)
        cx = x + BOX_WIDTH // 2

        # 矩形框
        lines.append(
            f'<rect x="{x}" y="{y}" width="{BOX_WIDTH}" height="{BOX_HEIGHT}" '
            f'rx="6" fill="{colors["bg"]}" stroke="{colors["stroke"]}" stroke-width="1.5"/>'
        )

        # 名称文本
        name_font_size = 12 if len(name) <= 12 else 10
        lines.append(
            f'<text x="{cx}" y="{y + 22}" fill="white" font-size="{name_font_size}" '
            f'font-weight="600" text-anchor="middle" font-family="JetBrains Mono, monospace">'
            f'{_escape_xml(name)}</text>'
        )

        # 描述文本（或类型名）
        label = desc if desc else colors["name"]
        label_font_size = 9 if len(label) <= 20 else 8
        lines.append(
            f'<text x="{cx}" y="{y + 40}" fill="#94a3b8" font-size="{label_font_size}" '
            f'text-anchor="middle" font-family="JetBrains Mono, monospace">'
            f'{_escape_xml(label)}</text>'
        )

    return "\n        ".join(lines)


def _render_legend() -> str:
    """渲染图例 SVG 元素"""
    lines = []
    legend_x = SVG_WIDTH - 250
    legend_y = 30
    lines.append(f'<text x="{legend_x}" y="{legend_y}" fill="white" font-size="10" font-weight="600">Legend</text>')

    y_offset = legend_y + 12
    for type_key, colors in TYPE_COLORS.items():
        if type_key == "external":
            continue
        y_offset += 14
        lines.append(
            f'<rect x="{legend_x}" y="{y_offset}" width="16" height="10" rx="2" '
            f'fill="{colors["bg"]}" stroke="{colors["stroke"]}" stroke-width="1"/>'
        )
        lines.append(
            f'<text x="{legend_x + 22}" y="{y_offset + 9}" fill="#94a3b8" font-size="8" '
            f'font-family="JetBrains Mono, monospace">{colors["name"]}</text>'
        )

    return "\n        ".join(lines)


def _escape_xml(text: str) -> str:
    """转义 XML 特殊字符"""
    return (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&apos;"))


def generate_architecture_diagram(title: str, components: list, output_path: str) -> dict:
    """生成架构图 HTML+SVG 文件

    Args:
        title: 架构图标题
        components: 组件列表，每项含 name/type/description
        output_path: 输出 HTML 文件路径

    Returns:
        dict: {"ok": True/False, "path": "...", "error": "..."}
    """
    try:
        logger.info("生成架构图: %s -> %s", title, output_path)

        # 校验参数
        if not title:
            return {"ok": False, "error": "标题不能为空"}
        if not components:
            return {"ok": False, "error": "组件列表不能为空"}
        if not output_path:
            return {"ok": False, "error": "输出路径不能为空"}

        # 确保输出目录存在
        output_dir = os.path.dirname(output_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)

        # 渲染 SVG 组件和图例
        svg_components = _render_svg_components(components)
        legend_svg = _render_legend()

        # 计算 SVG 高度（按组件数量动态调整）
        n = len(components)
        rows = (n + COLS - 1) // COLS
        svg_height = max(SVG_HEIGHT, MARGIN_Y + rows * (BOX_HEIGHT + ROW_GAP) + 60)

        # 按类型分组用于卡片展示
        cards_html = _render_info_cards(components)

        current_year = datetime.now().strftime("%Y")

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{_escape_xml(title)} Architecture Diagram</title>
  <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">
  <style>
    * {{
      margin: 0;
      padding: 0;
      box-sizing: border-box;
    }}

    body {{
      font-family: 'JetBrains Mono', monospace;
      background: #020617;
      min-height: 100vh;
      padding: 2rem;
      color: white;
    }}

    .container {{
      max-width: 1200px;
      margin: 0 auto;
    }}

    .header {{
      margin-bottom: 2rem;
    }}

    .header-row {{
      display: flex;
      align-items: center;
      gap: 1rem;
      margin-bottom: 0.5rem;
    }}

    .pulse-dot {{
      width: 12px;
      height: 12px;
      background: #22d3ee;
      border-radius: 50%;
      animation: pulse 2s infinite;
    }}

    @keyframes pulse {{
      0%, 100% {{ opacity: 1; }}
      50% {{ opacity: 0.5; }}
    }}

    h1 {{
      font-size: 1.5rem;
      font-weight: 700;
      letter-spacing: -0.025em;
    }}

    .subtitle {{
      color: #94a3b8;
      font-size: 0.875rem;
      margin-left: 1.75rem;
    }}

    .diagram-container {{
      background: rgba(15, 23, 42, 0.5);
      border-radius: 1rem;
      border: 1px solid #1e293b;
      padding: 1.5rem;
      overflow-x: auto;
    }}

    svg {{
      width: 100%;
      min-width: 900px;
      display: block;
    }}

    .cards {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      gap: 1rem;
      margin-top: 2rem;
    }}

    .card {{
      background: rgba(15, 23, 42, 0.5);
      border-radius: 0.75rem;
      border: 1px solid #1e293b;
      padding: 1.25rem;
    }}

    .card-header {{
      display: flex;
      align-items: center;
      gap: 0.5rem;
      margin-bottom: 0.75rem;
    }}

    .card-dot {{
      width: 8px;
      height: 8px;
      border-radius: 50%;
    }}

    .card-dot.cyan {{ background: #22d3ee; }}
    .card-dot.emerald {{ background: #34d399; }}
    .card-dot.violet {{ background: #a78bfa; }}
    .card-dot.amber {{ background: #fbbf24; }}
    .card-dot.rose {{ background: #fb7185; }}

    .card h3 {{
      font-size: 0.875rem;
      font-weight: 600;
    }}

    .card ul {{
      list-style: none;
      color: #94a3b8;
      font-size: 0.75rem;
    }}

    .card li {{
      margin-bottom: 0.375rem;
    }}

    .footer {{
      text-align: center;
      margin-top: 1.5rem;
      color: #475569;
      font-size: 0.75rem;
    }}
  </style>
</head>
<body>
  <div class="container">
    <!-- Header -->
    <div class="header">
      <div class="header-row">
        <div class="pulse-dot"></div>
        <h1>{_escape_xml(title)} Architecture</h1>
      </div>
      <p class="subtitle">{_escape_xml(title)} - {len(components)} components</p>
    </div>

    <!-- Main Diagram -->
    <div class="diagram-container">
      <svg viewBox="0 0 {SVG_WIDTH} {svg_height}">
        <!-- Definitions -->
        <defs>
          <marker id="arrowhead" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto">
            <polygon points="0 0, 10 3.5, 0 7" fill="#64748b" />
          </marker>
          <pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse">
            <path d="M 40 0 L 0 0 0 40" fill="none" stroke="#1e293b" stroke-width="0.5"/>
          </pattern>
        </defs>

        <!-- Background Grid -->
        <rect width="100%" height="100%" fill="url(#grid)" />

        <!-- Components -->
        {svg_components}

        <!-- Legend -->
        {legend_svg}
      </svg>
    </div>

    <!-- Info Cards -->
    {cards_html}

    <!-- Footer -->
    <p class="footer">
      {_escape_xml(title)} &bull; Generated on {current_year}
    </p>
  </div>
</body>
</html>"""

        # 写入文件
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html)

        logger.info("架构图已生成: %s (%d 个组件)", output_path, len(components))
        return {
            "ok": True,
            "path": output_path,
            "component_count": len(components),
        }

    except Exception as e:
        logger.error("生成架构图失败: %s", e)
        return {"ok": False, "error": str(e)}


def _render_info_cards(components: list) -> str:
    """按类型分组渲染信息卡片"""
    # 按类型分组
    groups = {}
    for comp in components:
        t = comp.get("type", "external").lower().replace(" ", "_")
        if t not in groups:
            groups[t] = []
        groups[t].append(comp)

    cards = []
    for type_key, comps in groups.items():
        colors = _get_color(type_key)
        dot_class = colors.get("dot", "")
        type_label = colors["name"]

        items = []
        for comp in comps:
            desc = comp.get("description", "")
            line_parts = [f"<li>&bull; {_escape_xml(comp.get('name', ''))}"]

            items.append(line_parts[0])

        cards.append(f"""      <div class="card">
        <div class="card-header">
          <div class="card-dot {dot_class}"></div>
          <h3>{_escape_xml(type_label)} ({len(comps)})</h3>
        </div>
        <ul>
          {chr(10).join(f'          <li>&bull; {_escape_xml(c.get("name", ""))}</li>' for c in comps)}
        </ul>
      </div>""")

    return "\n" + "\n".join(cards) + "\n"
