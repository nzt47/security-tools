
"""
自动周报生成器 - 基于 data_analytics 模块
定时任务版本：每周自动生成并保存分析报告
"""

import logging
import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)

def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]

logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "weekly_report_generator", "action": "log", "msg": "[WeeklyReportGenerator] 加载周报生成器"}, ensure_ascii=False))


class WeeklyReportGenerator:
    """自动周报生成器
    
    基于 data_analytics 模块的智能分析功能
    每周自动生成工作总结、趋势分析、洞察和建议
    
    功能:
    - 周工作汇总（记忆、事件、行为）
    - 趋势分析（与上周对比）
    - 洞察提取（关键发现）
    - 优化建议（基于分析结果）
    - 多格式输出（text, html, json）
    """
    
    def __init__(self, output_dir: str = "./data/reports", analytics=None):
        """初始化周报生成器
        
        Args:
            output_dir: 报告输出目录
            analytics: DataAnalytics 实例（可选）
        """
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "weekly_report_generator", "action": "__init__", "msg": "[WeeklyReportGenerator] __init__ 开始初始化"}, ensure_ascii=False))
        
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # 延迟加载 analytics（避免循环依赖）
        self._analytics = analytics
        self._analytics_loaded = False
        
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "weekly_report_generator", "action": "self.output_dir", "msg": f"[WeeklyReportGenerator] 输出目录: {self.output_dir}"}, ensure_ascii=False))
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "weekly_report_generator", "action": "__init__", "msg": "[WeeklyReportGenerator] __init__ 初始化完成"}, ensure_ascii=False))
    
    @property
    def analytics(self):
        """延迟加载 analytics"""
        if not self._analytics_loaded:
            logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "weekly_report_generator", "action": "analytics", "msg": "[WeeklyReportGenerator] 延迟加载 analytics"}, ensure_ascii=False))
            try:
                from agent.data_analytics import DataAnalytics
                from memory.vector_store import VectorStore
                
                vs = VectorStore(collection_name="agent_memory")
                self._analytics = DataAnalytics(vector_store=vs)
                self._analytics_loaded = True
                logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "weekly_report_generator", "action": "analytics", "msg": "[WeeklyReportGenerator] analytics 加载成功"}, ensure_ascii=False))
            except Exception as e:
                logger.error(json.dumps({"trace_id": _trace_id(), "module_name": "weekly_report_generator", "action": "analytics", "msg": f"[WeeklyReportGenerator] analytics 加载失败: {e}"}, ensure_ascii=False))
        
        return self._analytics
    
    def generate_weekly_report(self, week_offset: int = 0) -> Dict[str, Any]:
        """生成周报
        
        Args:
            week_offset: 周偏移量（0=本周, -1=上周, -2=上上周）
            
        Returns:
            周报数据字典
        """
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "weekly_report_generator", "action": "offset.week_offset", "msg": f"[WeeklyReportGenerator] 生成周报: offset={week_offset}"}, ensure_ascii=False))
        
        # 计算日期范围
        end_date = datetime.now() - timedelta(weeks=abs(week_offset))
        start_date = end_date - timedelta(days=7)
        
        # 如果是本周，调整结束日期
        if week_offset == 0:
            end_date = datetime.now()
        
        start_str = start_date.strftime("%Y-%m-%d")
        end_str = end_date.strftime("%Y-%m-%d")
        
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "weekly_report_generator", "action": "start_str.end_str", "msg": f"[WeeklyReportGenerator] 日期范围: {start_str} 至 {end_str}"}, ensure_ascii=False))
        
        report = {
            "meta": {
                "week_offset": week_offset,
                "start_date": start_str,
                "end_date": end_str,
                "generated_at": datetime.now().isoformat(),
                "period_days": 7
            },
            "content": {},
            "statistics": {},
            "insights": [],
            "recommendations": []
        }
        
        # 获取分析数据
        if self.analytics:
            # 事件趋势
            report["content"]["event_trends"] = self.analytics.analyze_event_trends(days=7)
            
            # 异常检测
            report["content"]["anomalies"] = self.analytics.detect_anomalies()
            
            # 用户行为
            report["content"]["user_behavior"] = self.analytics.analyze_user_behavior()
        
        # 生成统计摘要
        report["statistics"] = self._generate_statistics(report)
        
        # 生成洞察
        report["insights"] = self._extract_insights(report)
        
        # 生成建议
        report["recommendations"] = self._generate_recommendations(report)
        
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "weekly_report_generator", "action": "log", "msg": "[WeeklyReportGenerator] 周报生成完成"}, ensure_ascii=False))
        
        return report
    
    def _generate_statistics(self, report: Dict) -> Dict[str, Any]:
        """生成统计摘要"""
        stats = {
            "total_events": 0,
            "total_memories": 0,
            "event_types": 0,
            "anomaly_count": 0
        }
        
        if "event_trends" in report["content"]:
            et = report["content"]["event_trends"]
            if "overview" in et:
                stats["total_events"] = et["overview"].get("total_events", 0)
                stats["event_types"] = et["overview"].get("total_types", 0)
        
        if "user_behavior" in report["content"]:
            ub = report["content"]["user_behavior"]
            stats["total_memories"] = ub.get("recent_memory_count", 0)
        
        if "anomalies" in report["content"]:
            stats["anomaly_count"] = len(report["content"]["anomalies"])
        
        return stats
    
    def _extract_insights(self, report: Dict) -> List[str]:
        """提取关键洞察"""
        insights = []
        
        stats = report.get("statistics", {})
        
        if stats.get("total_events", 0) > 100:
            insights.append(f"本周事件活跃度较高，共处理 {stats['total_events']} 个事件")
        
        if stats.get("anomaly_count", 0) > 5:
            insights.append(f"检测到 {stats['anomaly_count']} 个异常，建议关注")
        elif stats.get("anomaly_count", 0) == 0:
            insights.append("本周系统运行平稳，未检测到明显异常")
        
        if stats.get("total_memories", 0) > 50:
            insights.append(f"知识积累良好，新增 {stats['total_memories']} 条记忆")
        
        # 从用户行为中提取洞察
        if "user_behavior" in report["content"]:
            ub = report["content"]["user_behavior"]
            if "insights" in ub:
                insights.extend(ub["insights"][:3])
        
        return insights
    
    def _generate_recommendations(self, report: Dict) -> List[Dict[str, str]]:
        """生成优化建议"""
        recommendations = []
        
        stats = report.get("statistics", {})
        
        if stats.get("anomaly_count", 0) > 5:
            recommendations.append({
                "priority": "high",
                "category": "系统监控",
                "suggestion": "异常事件较多，建议检查系统日志和错误报告",
                "action": "review_anomalies"
            })
        
        if stats.get("total_memories", 0) < 10:
            recommendations.append({
                "priority": "medium",
                "category": "知识管理",
                "suggestion": "本周记忆积累较少，建议主动记录更多工作要点",
                "action": "increase_memory"
            })
        
        if stats.get("total_events", 0) < 20:
            recommendations.append({
                "priority": "low",
                "category": "活动度",
                "suggestion": "本周活动较少，考虑增加系统使用频率",
                "action": "increase_usage"
            })
        
        # 默认建议
        recommendations.append({
            "priority": "info",
            "category": "持续改进",
            "suggestion": "定期查看周报，持续优化系统使用效率",
            "action": "regular_review"
        })
        
        return recommendations
    
    def save_report(self, report: Dict, format: str = "json") -> str:
        """保存周报
        
        Args:
            report: 周报数据
            format: 保存格式 (json, html, text)
            
        Returns:
            保存的文件路径
        """
        week_start = report["meta"]["start_date"]
        filename = f"weekly_report_{week_start}.{format}"
        filepath = self.output_dir / filename
        
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "weekly_report_generator", "action": "filepath", "msg": f"[WeeklyReportGenerator] 保存周报: {filepath}"}, ensure_ascii=False))
        
        try:
            if format == "json":
                with open(filepath, "w", encoding="utf-8") as f:
                    json.dump(report, f, ensure_ascii=False, indent=2)
            elif format == "html":
                html_content = self._generate_html_report(report)
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(html_content)
            elif format == "text":
                text_content = self._generate_text_report(report)
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(text_content)
            
            logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "weekly_report_generator", "action": "filepath", "msg": f"[WeeklyReportGenerator] 周报已保存: {filepath}"}, ensure_ascii=False))
            return str(filepath)
            
        except Exception as e:
            logger.error(json.dumps({"trace_id": _trace_id(), "module_name": "weekly_report_generator", "action": "log", "msg": f"[WeeklyReportGenerator] 保存失败: {e}"}, ensure_ascii=False))
            return None
    
    def _generate_text_report(self, report: Dict) -> str:
        """生成文本格式周报"""
        lines = []
        
        lines.append("=" * 80)
        lines.append(f"  📊 智能周报 - {report['meta']['start_date']} 至 {report['meta']['end_date']}")
        lines.append("=" * 80)
        lines.append(f"\n生成时间: {report['meta']['generated_at']}")
        
        # 统计摘要
        stats = report.get("statistics", {})
        lines.append("\n📈 统计摘要")
        lines.append("-" * 80)
        lines.append(f"  总事件数: {stats.get('total_events', 0)}")
        lines.append(f"  记忆总数: {stats.get('total_memories', 0)}")
        lines.append(f"  事件类型: {stats.get('event_types', 0)}")
        lines.append(f"  异常数量: {stats.get('anomaly_count', 0)}")
        
        # 洞察
        insights = report.get("insights", [])
        if insights:
            lines.append("\n💡 关键洞察")
            lines.append("-" * 80)
            for i, insight in enumerate(insights, 1):
                lines.append(f"  {i}. {insight}")
        
        # 建议
        recs = report.get("recommendations", [])
        if recs:
            lines.append("\n🎯 优化建议")
            lines.append("-" * 80)
            for i, rec in enumerate(recs, 1):
                lines.append(f"  [{rec['priority'].upper()}] {rec['suggestion']}")
        
        lines.append("\n" + "=" * 80)
        
        return "\n".join(lines)
    
    def _generate_html_report(self, report: Dict) -> str:
        """生成 HTML 格式周报"""
        stats = report.get("statistics", {})
        
        html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>智能周报 - {report['meta']['start_date']}</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; }}
        .header {{ background: #2196F3; color: white; padding: 20px; border-radius: 5px; }}
        .stats {{ display: flex; gap: 20px; margin: 20px 0; }}
        .stat-card {{ background: #f5f5f5; padding: 15px; border-radius: 5px; flex: 1; }}
        .stat-value {{ font-size: 24px; font-weight: bold; color: #2196F3; }}
        .section {{ margin: 20px 0; }}
        .insight {{ background: #E3F2FD; padding: 10px; margin: 5px 0; border-left: 4px solid #2196F3; }}
        .recommendation {{ background: #FFF3E0; padding: 10px; margin: 5px 0; border-left: 4px solid #FF9800; }}
        .high {{ border-color: #f44336; }}
        .medium {{ border-color: #FF9800; }}
        .low {{ border-color: #4CAF50; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>📊 智能周报</h1>
        <p>{report['meta']['start_date']} 至 {report['meta']['end_date']}</p>
    </div>
    
    <div class="stats">
        <div class="stat-card">
            <div class="stat-value">{stats.get('total_events', 0)}</div>
            <div>总事件数</div>
        </div>
        <div class="stat-card">
            <div class="stat-value">{stats.get('total_memories', 0)}</div>
            <div>记忆总数</div>
        </div>
        <div class="stat-card">
            <div class="stat-value">{stats.get('anomaly_count', 0)}</div>
            <div>异常数量</div>
        </div>
    </div>
    
    <div class="section">
        <h2>💡 关键洞察</h2>
        {"".join(f'<div class="insight">{i+1}. {insight}</div>' for i, insight in enumerate(report.get('insights', [])))}
    </div>
    
    <div class="section">
        <h2>🎯 优化建议</h2>
        {"".join(f'<div class="recommendation {rec["priority"]}">[{rec["priority"].upper()}] {rec["suggestion"]}</div>' for rec in report.get('recommendations', []))}
    </div>
    
    <footer style="margin-top: 40px; color: #666;">
        <p>生成时间: {report['meta']['generated_at']}</p>
    </footer>
</body>
</html>"""
        
        return html


def run_weekly_report(output_dir: str = "./data/reports", save_formats: List[str] = ["json", "html", "text"]):
    """运行周报生成（可作为定时任务调用）
    
    Args:
        output_dir: 报告输出目录
        save_formats: 保存格式列表
    """
    logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "weekly_report_generator", "action": "log", "msg": "=" * 80}, ensure_ascii=False))
    logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "weekly_report_generator", "action": "log", "msg": "  🚀 自动周报生成任务开始"}, ensure_ascii=False))
    logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "weekly_report_generator", "action": "log", "msg": "=" * 80}, ensure_ascii=False))
    
    generator = WeeklyReportGenerator(output_dir=output_dir)
    
    # 生成本周报告
    logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "weekly_report_generator", "action": "log", "msg": "生成本周报告..."}, ensure_ascii=False))
    report = generator.generate_weekly_report(week_offset=0)
    
    # 保存到多种格式
    saved_files = []
    for fmt in save_formats:
        filepath = generator.save_report(report, format=fmt)
        if filepath:
            saved_files.append(filepath)
    
    logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "weekly_report_generator", "action": "log", "msg": "=" * 80}, ensure_ascii=False))
    logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "weekly_report_generator", "action": "log", "msg": "  ✅ 周报生成任务完成"}, ensure_ascii=False))
    logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "weekly_report_generator", "action": "log", "msg": "=" * 80}, ensure_ascii=False))
    logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "weekly_report_generator", "action": "len.saved_files", "msg": f"已保存 {len(saved_files)} 个文件:"}, ensure_ascii=False))
    for f in saved_files:
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "weekly_report_generator", "action": "log", "msg": f"  - {f}"}, ensure_ascii=False))
    
    return report, saved_files


if __name__ == "__main__":
    # 演示周报生成
    print("\n演示周报生成...\n")
    report, files = run_weekly_report()
    
    print("\n" + "=" * 80)
    print("  周报生成成功!")
    print("=" * 80)
    print(f"\n生成了 {len(files)} 个文件:")
    for f in files:
        print(f"  - {f}")
