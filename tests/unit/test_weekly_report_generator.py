"""
周报生成器模块测试 - 覆盖报告数据聚合、模板渲染（mock 依赖）
"""

import pytest
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

from agent.weekly_report_generator import (
    WeeklyReportGenerator,
    run_weekly_report,
)


class TestWeeklyReportGenerator:
    """测试周报生成器"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_init(self):
        """测试初始化"""
        with tempfile.TemporaryDirectory() as tmpdir:
            generator = WeeklyReportGenerator(output_dir=tmpdir)

            assert generator.output_dir == Path(tmpdir)
            assert generator._analytics is None
            assert not generator._analytics_loaded

    @pytest.mark.unit
    @pytest.mark.p0
    def test_generate_weekly_report(self):
        """测试生成周报"""
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_analytics = Mock()
            mock_analytics.analyze_event_trends.return_value = {
                "overview": {
                    "total_events": 150,
                    "total_types": 10
                }
            }
            mock_analytics.detect_anomalies.return_value = []
            mock_analytics.analyze_user_behavior.return_value = {
                "recent_memory_count": 60,
                "insights": ["用户活跃度较高"]
            }

            generator = WeeklyReportGenerator(output_dir=tmpdir)
            generator._analytics = mock_analytics
            generator._analytics_loaded = True

            report = generator.generate_weekly_report(week_offset=0)

            assert "meta" in report
            assert "content" in report
            assert "statistics" in report
            assert "insights" in report
            assert "recommendations" in report

            assert report["meta"]["week_offset"] == 0
            assert report["statistics"]["total_events"] == 150
            assert report["statistics"]["total_memories"] == 60

    @pytest.mark.unit
    @pytest.mark.p0
    def test_generate_weekly_report_without_analytics(self):
        """测试无 analytics 时生成周报"""
        with tempfile.TemporaryDirectory() as tmpdir:
            generator = WeeklyReportGenerator(output_dir=tmpdir)

            report = generator.generate_weekly_report(week_offset=0)

            assert "meta" in report
            assert "content" in report
            assert report["statistics"]["total_events"] == 0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_save_report_json(self):
        """测试保存 JSON 格式报告"""
        with tempfile.TemporaryDirectory() as tmpdir:
            generator = WeeklyReportGenerator(output_dir=tmpdir)

            report = {
                "meta": {
                    "start_date": "2024-01-01",
                    "end_date": "2024-01-07",
                    "generated_at": "2024-01-07T12:00:00",
                    "period_days": 7
                },
                "content": {},
                "statistics": {},
                "insights": [],
                "recommendations": []
            }

            filepath = generator.save_report(report, format="json")

            assert filepath is not None
            assert Path(filepath).exists()

    @pytest.mark.unit
    @pytest.mark.p0
    def test_save_report_text(self):
        """测试保存文本格式报告"""
        with tempfile.TemporaryDirectory() as tmpdir:
            generator = WeeklyReportGenerator(output_dir=tmpdir)

            report = {
                "meta": {
                    "start_date": "2024-01-01",
                    "end_date": "2024-01-07",
                    "generated_at": "2024-01-07T12:00:00",
                    "period_days": 7
                },
                "content": {},
                "statistics": {
                    "total_events": 100,
                    "total_memories": 50,
                    "event_types": 5,
                    "anomaly_count": 2
                },
                "insights": ["测试洞察"],
                "recommendations": [
                    {"priority": "high", "category": "测试", "suggestion": "测试建议"}
                ]
            }

            filepath = generator.save_report(report, format="text")

            assert filepath is not None
            assert Path(filepath).exists()

            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
                assert "智能周报" in content
                assert "统计摘要" in content

    @pytest.mark.unit
    @pytest.mark.p0
    def test_save_report_html(self):
        """测试保存 HTML 格式报告"""
        with tempfile.TemporaryDirectory() as tmpdir:
            generator = WeeklyReportGenerator(output_dir=tmpdir)

            report = {
                "meta": {
                    "start_date": "2024-01-01",
                    "end_date": "2024-01-07",
                    "generated_at": "2024-01-07T12:00:00",
                    "period_days": 7
                },
                "content": {},
                "statistics": {
                    "total_events": 100,
                    "total_memories": 50,
                    "anomaly_count": 0
                },
                "insights": [],
                "recommendations": []
            }

            filepath = generator.save_report(report, format="html")

            assert filepath is not None
            assert Path(filepath).exists()

            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
                assert "<html>" in content
                assert "智能周报" in content

    @pytest.mark.unit
    @pytest.mark.p1
    def test_generate_statistics(self):
        """测试生成统计摘要"""
        with tempfile.TemporaryDirectory() as tmpdir:
            generator = WeeklyReportGenerator(output_dir=tmpdir)

            report = {
                "content": {
                    "event_trends": {
                        "overview": {
                            "total_events": 100,
                            "total_types": 5
                        }
                    },
                    "user_behavior": {
                        "recent_memory_count": 30
                    },
                    "anomalies": [1, 2, 3]
                }
            }

            stats = generator._generate_statistics(report)

            assert stats["total_events"] == 100
            assert stats["total_memories"] == 30
            assert stats["anomaly_count"] == 3

    @pytest.mark.unit
    @pytest.mark.p1
    def test_extract_insights(self):
        """测试提取洞察"""
        with tempfile.TemporaryDirectory() as tmpdir:
            generator = WeeklyReportGenerator(output_dir=tmpdir)

            report = {
                "statistics": {
                    "total_events": 150,
                    "total_memories": 60,
                    "anomaly_count": 0
                },
                "content": {
                    "user_behavior": {
                        "insights": ["洞察1", "洞察2"]
                    }
                }
            }

            insights = generator._extract_insights(report)

            assert len(insights) >= 2
            assert "本周事件活跃度较高" in insights[0]

    @pytest.mark.unit
    @pytest.mark.p1
    def test_extract_insights_with_anomalies(self):
        """测试提取洞察（有异常）"""
        with tempfile.TemporaryDirectory() as tmpdir:
            generator = WeeklyReportGenerator(output_dir=tmpdir)

            report = {
                "statistics": {
                    "total_events": 100,
                    "total_memories": 10,
                    "anomaly_count": 8
                },
                "content": {}
            }

            insights = generator._extract_insights(report)

            assert "检测到 8 个异常" in insights[0]

    @pytest.mark.unit
    @pytest.mark.p1
    def test_generate_recommendations(self):
        """测试生成建议"""
        with tempfile.TemporaryDirectory() as tmpdir:
            generator = WeeklyReportGenerator(output_dir=tmpdir)

            report = {
                "statistics": {
                    "anomaly_count": 6,
                    "total_memories": 5,
                    "total_events": 15
                }
            }

            recommendations = generator._generate_recommendations(report)

            assert len(recommendations) >= 2
            assert recommendations[0]["priority"] == "high"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_analytics_lazy_load(self):
        """测试 analytics 延迟加载"""
        with tempfile.TemporaryDirectory() as tmpdir:
            generator = WeeklyReportGenerator(output_dir=tmpdir)

            assert not generator._analytics_loaded

            with patch("agent.data_analytics.DataAnalytics") as MockDataAnalytics, \
                 patch("agent.memory.vector_store.VectorStore") as MockVectorStore:

                mock_vs = Mock()
                MockVectorStore.return_value = mock_vs

                _ = generator.analytics

                MockVectorStore.assert_called_once()
                MockDataAnalytics.assert_called_once()
                assert generator._analytics_loaded

    @pytest.mark.unit
    @pytest.mark.p2
    def test_analytics_load_failure(self):
        """测试 analytics 加载失败"""
        with tempfile.TemporaryDirectory() as tmpdir:
            generator = WeeklyReportGenerator(output_dir=tmpdir)

            with patch("agent.data_analytics.DataAnalytics", side_effect=Exception("load error")):
                result = generator.analytics

                assert result is None
                assert not generator._analytics_loaded

    @pytest.mark.unit
    @pytest.mark.p1
    def test_generate_weekly_report_last_week(self):
        """测试生成上周周报"""
        with tempfile.TemporaryDirectory() as tmpdir:
            generator = WeeklyReportGenerator(output_dir=tmpdir)

            report = generator.generate_weekly_report(week_offset=-1)

            assert report["meta"]["week_offset"] == -1

    @pytest.mark.unit
    @pytest.mark.p2
    def test_save_report_failure(self):
        """测试保存报告失败"""
        with tempfile.TemporaryDirectory() as tmpdir:
            generator = WeeklyReportGenerator(output_dir=tmpdir)

            report = {"meta": {"start_date": "2024-01-01"}}

            with patch("builtins.open", side_effect=PermissionError("permission denied")):
                filepath = generator.save_report(report, format="json")

                assert filepath is None


class TestRunWeeklyReport:
    """测试运行周报生成任务"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_run_weekly_report(self):
        """测试运行周报生成"""
        with tempfile.TemporaryDirectory() as tmpdir:
            report, files = run_weekly_report(output_dir=tmpdir, save_formats=["json"])

            assert report is not None
            assert len(files) == 1
            assert files[0].endswith(".json")

    @pytest.mark.unit
    @pytest.mark.p1
    def test_run_weekly_report_multiple_formats(self):
        """测试生成多种格式"""
        with tempfile.TemporaryDirectory() as tmpdir:
            report, files = run_weekly_report(
                output_dir=tmpdir,
                save_formats=["json", "text", "html"]
            )

            assert len(files) == 3
            assert any(f.endswith(".json") for f in files)
            assert any(f.endswith(".text") for f in files)
            assert any(f.endswith(".html") for f in files)