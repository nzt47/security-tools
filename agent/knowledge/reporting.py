"""《知识库健康报告》HTML 渲染工具模块（任务5 · 治理层，独立于 lint 流程）。

其他模块可直接调用：

    from agent.knowledge.reporting import render_html_report

只需传入一个「健康报告形状」的对象（字段：checked_at / total_cards /
health_score / orphans / broken_links / index_drift / stale_cards /
unresolved_conflicts / suggestions），**无需先运行 lint_all 巡检**。
标准用法传入 agent.knowledge.lint.HealthReport（仅类型标注，运行时不导入）。

图表实现（零外部依赖，离线可开）：
- 健康分环形仪表盘：内联 SVG（stroke-dasharray 圆环，按 score/100 比例）。
- 五类问题条形图：纯 CSS 横向条形（宽度按问题数/最大值比例）。

颜色分级：score >= 90 绿(健康)、>= 70 黄(关注)、其余红(危险)。

【不易】
- 纯函数无副作用：给定输入恒产生相同 HTML 字符串，便于单测精确断言。
- 运行期零依赖：仅使用标准库（html），不 import lint/card 等业务模块。
"""

from __future__ import annotations

from html import escape
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 仅类型标注，避免运行时导入 lint（解耦巡检流程）
    from agent.knowledge.lint import HealthReport


def score_style(score: float) -> tuple[str, str]:
    """健康分颜色分级：>=90 绿(健康)、>=70 黄(关注)、其余红(危险)。

    返回 (颜色, 标签)，供 SVG 仪表盘与 CSS 条形图共用。
    """
    if score >= 90:
        return "#2e7d32", "健康"
    if score >= 70:
        return "#f9a825", "关注"
    return "#c62828", "危险"


def gauge_svg(score: float, color: str, label: str) -> str:
    """生成健康分环形仪表盘 SVG（r=52，stroke-dasharray 按 score/100 比例）。

    - 圆周长 = 2πr ≈ 326.73；填充弧长 = 周长 × score/100。
    - rotate(-90) 使起始点位于正上方。
    """
    r = 52
    circumference = round(2 * 3.14159 * r, 2)
    filled = round(circumference * score / 100, 2)
    return (
        '<svg width="150" height="150" viewBox="0 0 120 120" '
        f'role="img" aria-label="健康分 {score}">'
        f'<circle cx="60" cy="60" r="{r}" fill="none" stroke="#e0e0e0" stroke-width="12"/>'
        f'<circle cx="60" cy="60" r="{r}" fill="none" stroke="{color}" stroke-width="12" '
        f'stroke-linecap="round" stroke-dasharray="{filled} {circumference}" '
        'transform="rotate(-90 60 60)"/>'
        f'<text x="60" y="62" text-anchor="middle" font-size="26" font-weight="bold" '
        f'fill="{color}">{score}</text>'
        f'<text x="60" y="80" text-anchor="middle" font-size="12" fill="#999">'
        f'{escape(label)}</text></svg>'
    )


def bars_html(counts: dict[str, int], color: str) -> str:
    """生成五类问题 CSS 横向条形图（宽度按 问题数/最大值 比例，最小 2%）。"""
    max_count = max(counts.values()) or 1
    rows = []
    for label, n in counts.items():
        pct = max(2, round(n / max_count * 100))
        rows.append(
            f'<div class="bar-row">'
            f'<span class="bar-label">{escape(label)}</span>'
            f'<span class="bar-track"><span class="bar-fill" style="width:{pct}%">'
            f'</span></span>'
            f'<span class="bar-count">{n}</span></div>'
        )
    return "".join(rows)


def render_html_report(report: HealthReport, stale_days: int = 90) -> str:
    """渲染 HTML 版《知识库健康报告》，含可视化图表（邮件发送用）。

    依赖 report 的字段：checked_at / total_cards / health_score /
    orphans / broken_links / index_drift / stale_cards /
    unresolved_conflicts / suggestions；无需先运行 lint 巡检。
    """
    score = report.health_score
    score_color, score_label = score_style(score)
    gauge = gauge_svg(score, score_color, score_label)

    counts = {
        "孤儿卡片": len(report.orphans),
        "断链": len(report.broken_links),
        "index 漂移": len(report.index_drift),
        "过期声明": len(report.stale_cards),
        "未裁决矛盾": len(report.unresolved_conflicts),
    }
    bars = bars_html(counts, score_color)

    def _list(items, fmt) -> str:
        if not items:
            return "<li class='ok'>无</li>"
        return "".join(f"<li>{fmt(it)}</li>" for it in items)

    orphans_li = _list(report.orphans, lambda s: f"<code>{escape(s)}</code>")
    broken_li = _list(
        report.broken_links,
        lambda b: f"<code>{escape(b['from_slug'])}</code> → "
        f"<code>{escape(b['to_slug'])}</code>",
    )
    drift_li = _list(report.index_drift, lambda s: f"<code>{escape(s)}</code>")
    stale_li = _list(
        report.stale_cards,
        lambda s: f"<code>{escape(s['slug'])}</code> "
        f"（超期 {s['days_unaccessed']} 天）",
    )
    conflict_li = _list(
        report.unresolved_conflicts,
        lambda u: f"<code>{escape(u['source_slug'])}</code> ↔ "
        f"<code>{escape(u['target_slug'])}</code>：{escape(u.get('summary', ''))}",
    )
    suggestions_li = _list(report.suggestions, lambda s: escape(s))

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>知识库健康报告</title>
<style>
  body {{ font-family: -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif; margin: 24px; color: #333; }}
  h1 {{ border-bottom: 2px solid {score_color}; padding-bottom: 8px; }}
  .meta {{ color: #777; font-size: 14px; }}
  .overview {{ display: flex; align-items: center; gap: 32px; margin: 20px 0; }}
  .gauge-title {{ font-size: 14px; color: #777; text-align: center; }}
  .score-note {{ color: {score_color}; font-weight: bold; }}
  table {{ border-collapse: collapse; width: 100%; margin: 12px 0; }}
  th, td {{ border: 1px solid #ddd; padding: 6px 10px; text-align: left; font-size: 14px; }}
  th {{ background: #f5f5f5; }}
  .bar-row {{ display: flex; align-items: center; margin: 6px 0; gap: 8px; }}
  .bar-label {{ width: 110px; font-size: 13px; text-align: right; }}
  .bar-track {{ flex: 1; background: #f0f0f0; border-radius: 4px; height: 18px; }}
  .bar-fill {{ display: block; height: 100%; border-radius: 4px; background: {score_color}; }}
  .bar-count {{ width: 40px; font-weight: bold; font-size: 13px; }}
  h2 {{ margin-top: 24px; font-size: 18px; }}
  ul {{ padding-left: 20px; font-size: 14px; }}
  .ok {{ color: #999; }}
  code {{ background: #f5f5f5; padding: 1px 5px; border-radius: 3px; font-size: 13px; }}
  .footer {{ margin-top: 32px; color: #aaa; font-size: 12px; }}
</style>
</head>
<body>
<h1>📋 知识库健康报告</h1>
<p class="meta">巡检时间：{escape(report.checked_at)} ｜ 卡片总数：{report.total_cards} ｜ 过期阈值：{stale_days} 天</p>

<div class="overview">
  <div>
    <div class="gauge-title">健康分</div>
    {gauge}
  </div>
  <div style="flex:1;">
    <h2 style="margin-top:0;">五类问题统计</h2>
    {bars}
  </div>
</div>

<h2>一、孤儿卡片（{len(report.orphans)}）</h2>
<ul>{orphans_li}</ul>

<h2>二、断链（{len(report.broken_links)}）</h2>
<ul>{broken_li}</ul>

<h2>三、index 漂移（{len(report.index_drift)}）</h2>
<ul>{drift_li}</ul>

<h2>四、过期声明（{len(report.stale_cards)}）</h2>
<ul>{stale_li}</ul>

<h2>五、未裁决矛盾（{len(report.unresolved_conflicts)}）</h2>
<ul>{conflict_li}</ul>

<h2>六、处置建议</h2>
<ul>{suggestions_li}</ul>

<p class="footer">本报告由云枢知识库定时审计生成 · 每日 02:00 自动巡检</p>
</body>
</html>"""
