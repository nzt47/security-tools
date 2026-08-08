"""HTML 健康报告可视化生成单元测试（任务5 · reporting 工具模块原子函数）。

聚焦三个可独立验证的生成函数：
- `score_style`：健康分颜色分级（绿/黄/红）边界。
- `gauge_svg`：SVG 环形仪表盘几何（r、圆周长、dasharray 比例、旋转起点）。
- `bars_html`：CSS 横向条形图（宽度按 问题数/最大值 比例、最小 2%）。

并做 render_html_report 集成断言：SVG 与条形图确实被嵌入最终文档，
且颜色与分级结果一致。
"""

from __future__ import annotations

from types import SimpleNamespace

from agent.knowledge.lint import HealthReport
from agent.knowledge.reporting import (
    bars_html,
    gauge_svg,
    render_html_report,
    score_style,
)

# 圆周长 = 2πr（r=52）；与实现保持一致，避免魔法数散落
_R = 52
_CIRCUMFERENCE = round(2 * 3.14159 * _R, 2)  # ≈ 326.73


def _report(score: float, orphans: int = 0, broken: int = 0, drift: int = 0,
            stale: int = 0, conflicts: int = 0) -> HealthReport:
    return HealthReport(
        checked_at="2026-08-08",
        total_cards=1,
        orphans=[f"孤儿{i}" for i in range(orphans)],
        broken_links=[{"from_slug": f"a{i}", "to_slug": "ghost"} for i in range(broken)],
        index_drift=[f"漂移{i}" for i in range(drift)],
        stale_cards=[{"slug": f"旧{i}", "days_unaccessed": 120} for i in range(stale)],
        unresolved_conflicts=[{"source_slug": f"s{i}", "target_slug": "t",
                               "summary": ""} for i in range(conflicts)],
        health_score=score,
    )


# ---------- score_style 颜色分级 ----------


class TestScoreStyle:
    def test_green_when_100(self):
        assert score_style(100.0) == ("#2e7d32", "健康")

    def test_green_at_boundary_90(self):
        assert score_style(90.0) == ("#2e7d32", "健康")

    def test_yellow_between_70_and_90(self):
        assert score_style(89.9) == ("#f9a825", "关注")
        assert score_style(78.0) == ("#f9a825", "关注")

    def test_yellow_at_boundary_70(self):
        assert score_style(70.0) == ("#f9a825", "关注")

    def test_red_below_70(self):
        assert score_style(69.9) == ("#c62828", "危险")
        assert score_style(0.0) == ("#c62828", "危险")


# ---------- gauge_svg 环形仪表盘几何 ----------


class TestGaugeSvg:
    def test_circumference_matches_r52(self):
        """圆周长按 r=52 计算：2πr ≈ 326.73，dasharray 以该值为满环。"""
        svg = gauge_svg(100.0, "#2e7d32", "健康")
        assert f'r="{_R}"' in svg
        assert f'stroke-dasharray="{_CIRCUMFERENCE} {_CIRCUMFERENCE}"' in svg
        assert f'aria-label="健康分 100.0"' in svg

    def test_dasharray_fraction_of_score(self):
        """score=78 → 填充弧长 = 326.73 × 0.78 ≈ 254.85。"""
        svg = gauge_svg(78.0, "#f9a825", "关注")
        filled = round(_CIRCUMFERENCE * 78 / 100, 2)
        assert filled == 254.85
        assert f'stroke-dasharray="{filled} {_CIRCUMFERENCE}"' in svg

    def test_start_angle_rotated_90(self):
        """rotate(-90) 使环形起始点位于正上方（12 点钟方向）。"""
        assert 'transform="rotate(-90 60 60)"' in gauge_svg(50.0, "#c62828", "危险")

    def test_score_and_label_rendered(self):
        svg = gauge_svg(84.5, "#f9a825", "关注")
        assert ">84.5</text>" in svg
        assert ">关注</text>" in svg

    def test_color_used_in_ring_and_score(self):
        """颜色同时作用于环形描边与分数文字。"""
        svg = gauge_svg(60.0, "#c62828", "危险")
        assert svg.count("#c62828") == 2  # 前景环 stroke + 分数 fill


# ---------- bars_html 横向条形图 ----------


class TestBarsHtml:
    def test_width_relative_to_max(self):
        """最大值条形 width:100%，其余按比例（如 5/10 → 50%）。"""
        html = bars_html(
            {"孤儿卡片": 10, "断链": 5, "index 漂移": 0, "过期声明": 0, "未裁决矛盾": 0},
            "#f9a825",
        )
        assert 'width:100%' in html
        assert 'width:50%' in html
        assert html.count("bar-row") == 5  # 五类固定输出

    def test_min_width_floor_2_percent(self):
        """最小宽度 2%，避免零值条形完全消失。"""
        html = bars_html(
            {"孤儿卡片": 100, "断链": 0, "index 漂移": 0, "过期声明": 0, "未裁决矛盾": 0},
            "#2e7d32",
        )
        assert 'width:2%' in html
        assert 'width:100%' in html

    def test_all_zero_uses_max_1(self):
        """全部为 0 → 基准 1，所有条形取最小 2%（避免除零）。"""
        html = bars_html(
            {"孤儿卡片": 0, "断链": 0, "index 漂移": 0, "过期声明": 0, "未裁决矛盾": 0},
            "#2e7d32",
        )
        assert 'width:2%' in html
        assert 'width:100%' not in html

    def test_count_and_label_rendered(self):
        html = bars_html({"孤儿卡片": 3, "断链": 0, "index 漂移": 0,
                          "过期声明": 0, "未裁决矛盾": 0}, "#f9a825")
        assert "孤儿卡片" in html
        assert ">3</span>" in html

    def test_label_escaped(self):
        """标签经 html.escape，防注入。"""
        html = bars_html({"<script>x</script>": 1, "断链": 0, "index 漂移": 0,
                          "过期声明": 0, "未裁决矛盾": 0}, "#2e7d32")
        assert "<script>" not in html
        assert "&lt;script&gt;" in html


# ---------- render_html_report 集成 ----------


class TestRenderIntegration:
    def test_embed_gauge_and_bars(self):
        """最终文档同时包含 SVG 仪表盘与五类条形图。"""
        html = render_html_report(_report(78.0, orphans=2, broken=3))
        assert "<svg" in html
        assert "stroke-dasharray" in html
        # 用完整 div 标签统计（CSS 样式定义中也含 "bar-row" 字样，不能直接 count）
        assert html.count('<div class="bar-row">') == 5

    def test_color_consistent_with_score_style(self):
        """文档颜色与 score_style 分级一致（关注档 → 黄色系）。"""
        html = render_html_report(_report(78.0))
        assert "#f9a825" in html  # h1 下边框 / bar-fill / svg 环

    def test_green_gauge_for_healthy(self):
        html = render_html_report(_report(95.0))
        assert "#2e7d32" in html

    def test_red_gauge_for_critical(self):
        html = render_html_report(_report(55.0))
        assert "#c62828" in html

    def test_issue_counts_appear(self):
        html = render_html_report(_report(70.0, orphans=1, broken=2))
        assert "孤儿卡片（1）" in html
        assert "断链（2）" in html

    def test_standalone_without_health_report(self):
        """独立工具能力：传入任意「健康报告形状」对象即可渲染，无需 HealthReport。

        证明 reporting 模块不依赖完整 lint 巡检流程（其他模块可直接调用）。
        """
        report = SimpleNamespace(
            checked_at="2026-08-08",
            total_cards=2,
            health_score=66.0,
            orphans=["o"],
            broken_links=[{"from_slug": "a", "to_slug": "ghost"}],
            index_drift=[],
            stale_cards=[],
            unresolved_conflicts=[],
            suggestions=["需处理"],
        )
        html = render_html_report(report)
        assert "<!DOCTYPE html>" in html
        assert "66.0" in html
        assert "<svg" in html
        assert "需处理" in html
