
"""
数据智能分析模块
基于 Phase 3 新架构的数据分析和洞察提取功能
"""

import logging
import json
import uuid
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from collections import defaultdict, Counter

logger = logging.getLogger(__name__)

def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]

# 参数边界约束
MAX_ANALYZE_DAYS = 36500  # 100 年，超过此值 datetime 计算可能溢出

logger.info("[DataAnalytics] 加载数据分析模块")


class DataAnalytics:
    """数据智能分析器
    
    功能特性:
    - 日志事件趋势分析
    - 用户行为模式识别
    - 性能指标追踪
    - 异常检测
    - 报告生成
    """
    
    def __init__(self, black_box=None, vector_store=None):
        """初始化分析器
        
        Args:
            black_box: 黑匣子日志系统实例
            vector_store: 向量存储实例
        """
        logger.info("[DataAnalytics] __init__ 开始初始化")
        self.black_box = black_box
        self.vector_store = vector_store
        
        # 内部统计缓存
        self._cache = {}
        self._cache_ttl = 300  # 秒
        
        logger.info("[DataAnalytics] __init__ 初始化完成")
    
    def analyze_event_trends(self, days: int = 7) -> Dict[str, Any]:
        """分析事件趋势

        Args:
            days: 分析最近 N 天（0 ≤ days ≤ 36500）

        Returns:
            趋势分析结果

        Raises:
            ValueError: days 为负数或超过上限 MAX_ANALYZE_DAYS 时抛出
        """
        logger.info(f"[DataAnalytics] 分析事件趋势: {days} 天")

        # 边界显性化：校验 days 参数，防止 OverflowError
        if not isinstance(days, int) or days < 0:
            logger.error(json.dumps({
                "trace_id": _trace_id(),
                "module_name": "data_analytics",
                "action": "analyze_event_trends.invalid_days",
                "duration_ms": 0,
                "days": days,
                "reason": "days must be non-negative int",
            }, ensure_ascii=False))
            raise ValueError(
                f"days 必须为非负整数，得到: {days!r}"
            )
        if days > MAX_ANALYZE_DAYS:
            logger.error(json.dumps({
                "trace_id": _trace_id(),
                "module_name": "data_analytics",
                "action": "analyze_event_trends.days_overflow",
                "duration_ms": 0,
                "days": days,
                "max_allowed": MAX_ANALYZE_DAYS,
            }, ensure_ascii=False))
            raise ValueError(
                f"days 超过上限 {MAX_ANALYZE_DAYS}，得到: {days}"
            )

        if not self.black_box:
            return {"error": "black_box not available"}

        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)
        
        events = self.black_box.query(
            start=start_date.strftime("%Y-%m-%dT00:00:00Z"),
            end=end_date.strftime("%Y-%m-%dT23:59:59Z"),
            limit=10000
        )
        
        # 按天统计
        daily_stats = defaultdict(lambda: {
            "total": 0,
            "by_type": defaultdict(int)
        })
        
        for event in events:
            date = event["timestamp"][:10]
            daily_stats[date]["total"] += 1
            daily_stats[date]["by_type"][event.get("event_type", "unknown")] += 1
        
        # 趋势分析
        type_counts = Counter()
        for day_data in daily_stats.values():
            for event_type, count in day_data["by_type"].items():
                type_counts[event_type] += count
        
        result = {
            "period": {
                "start": start_date.strftime("%Y-%m-%d"),
                "end": end_date.strftime("%Y-%m-%d"),
                "days": days
            },
            "overview": {
                "total_events": len(events),
                "total_types": len(type_counts),
                "top_events": type_counts.most_common(5)
            },
            "daily": dict(daily_stats),
            "type_distribution": dict(type_counts)
        }
        
        logger.info(f"[DataAnalytics] 趋势分析完成: {len(events)} 条事件")
        return result
    
    def detect_anomalies(self, threshold_multiplier: float = 2.0) -> List[Dict]:
        """检测异常行为
        
        Args:
            threshold_multiplier: 异常阈值倍数
            
        Returns:
            异常列表
        """
        logger.info(f"[DataAnalytics] 检测异常: 阈值 {threshold_multiplier}x")
        
        if not self.black_box:
            return []
        
        events = self.black_box.query(limit=5000)
        
        # 统计每个小时的事件数
        hourly_counts = defaultdict(int)
        for event in events:
            hour = event["timestamp"][:13]
            hourly_counts[hour] += 1
        
        # 计算平均和标准差
        if len(hourly_counts) < 2:
            return []
        
        counts = list(hourly_counts.values())
        avg = sum(counts) / len(counts)
        variance = sum((x - avg) ** 2 for x in counts) / len(counts)
        std_dev = variance ** 0.5
        
        threshold = avg + threshold_multiplier * std_dev
        anomalies = []
        
        for hour, count in hourly_counts.items():
            if count > threshold:
                anomalies.append({
                    "timestamp": hour,
                    "count": count,
                    "threshold": threshold,
                    "type": "spike"
                })
            elif count < max(1, avg - threshold_multiplier * std_dev):
                anomalies.append({
                    "timestamp": hour,
                    "count": count,
                    "threshold": threshold,
                    "type": "lull"
                })
        
        logger.info(f"[DataAnalytics] 异常检测完成: {len(anomalies)} 个异常")
        return anomalies
    
    def analyze_user_behavior(self) -> Dict[str, Any]:
        """分析用户行为模式
        
        Returns:
            用户行为分析结果
        """
        logger.info("[DataAnalytics] 分析用户行为")
        
        if not self.vector_store:
            return {"error": "vector_store not available"}
        
        # 获取记忆并分析
        recent = self.vector_store.get_recent(limit=100)
        
        categories = defaultdict(int)
        sources = defaultdict(int)
        tags = defaultdict(int)
        
        for item in recent:
            md = item.metadata
            if "category" in md:
                categories[md["category"]] += 1
            if "source" in md:
                sources[md["source"]] += 1
            if "tags" in md and isinstance(md["tags"], list):
                for tag in md["tags"]:
                    tags[tag] += 1
        
        result = {
            "recent_memory_count": len(recent),
            "categories": dict(categories),
            "sources": dict(sources),
            "tags": dict(tags),
            "insights": self._extract_insights(categories, sources, tags)
        }
        
        logger.info("[DataAnalytics] 用户行为分析完成")
        return result
    
    def _extract_insights(self, categories, sources, tags) -> List[str]:
        """提取洞察"""
        insights = []
        
        if categories:
            top_cat = max(categories.items(), key=lambda x: x[1])
            insights.append(f"最常使用的类别: {top_cat[0]} ({top_cat[1]} 次)")
        
        if sources:
            top_src = max(sources.items(), key=lambda x: x[1])
            insights.append(f"最活跃的来源: {top_src[0]}")
        
        if tags:
            top_tags = sorted(tags.items(), key=lambda x: x[1], reverse=True)[:3]
            insights.append(f"热门标签: {', '.join(t[0] for t in top_tags)}")
        
        return insights
    
    def generate_report(self, format: str = "text") -> str:
        """生成综合分析报告
        
        Args:
            format: 报告格式 (text, json, html)
            
        Returns:
            报告内容
        """
        logger.info(f"[DataAnalytics] 生成报告: {format}")
        
        report_data = {
            "generated_at": datetime.now().isoformat(),
            "event_trends": self.analyze_event_trends(),
            "anomalies": self.detect_anomalies(),
            "user_behavior": self.analyze_user_behavior()
        }
        
        if format == "json":
            return json.dumps(report_data, ensure_ascii=False, indent=2)
        elif format == "html":
            return self._generate_html_report(report_data)
        
        # 文本格式
        lines = ["=" * 80, "  数据智能分析报告", "=" * 80]
        lines.append(f"\n生成时间: {report_data['generated_at']}")
        
        if "overview" in report_data["event_trends"]:
            lines.append("\n📊 事件概览")
            lines.append(f"   - 总事件数: {report_data['event_trends']['overview']['total_events']}")
            lines.append(f"   - 事件类型数: {report_data['event_trends']['overview']['total_types']}")
            lines.append(f"   - 热门事件: {report_data['event_trends']['overview']['top_events']}")
        
        if report_data["anomalies"]:
            lines.append(f"\n⚠️ 检测到 {len(report_data['anomalies'])} 个异常")
        
        if "recent_memory_count" in report_data["user_behavior"]:
            lines.append(f"\n👤 用户行为")
            lines.append(f"   - 近期记忆数: {report_data['user_behavior']['recent_memory_count']}")
        
        lines.append("\n" + "=" * 80)
        
        return "\n".join(lines)
    
    def _generate_html_report(self, data) -> str:
        """生成 HTML 格式报告"""
        return f"""&lt;html&gt;
&lt;head&gt;&lt;title&gt;数据分析报告&lt;/title&gt;&lt;/head&gt;
&lt;body style="font-family: Arial, sans-serif; padding: 20px;"&gt;
&lt;h1&gt;数据智能分析报告&lt;/h1&gt;
&lt;p&gt;生成时间: {data["generated_at"]}&lt;/p&gt;
&lt;pre style="background: #f4f4f4; padding: 15px; border-radius: 5px;"&gt;
{json.dumps(data, ensure_ascii=False, indent=2)}
&lt;/pre&gt;
&lt;/body&gt;
&lt;/html&gt;"""


# 快捷函数
def create_analytics(black_box=None, vector_store=None):
    """创建数据分析实例"""
    return DataAnalytics(black_box, vector_store)


def _safe_call(func, *args, action="safe_call", **kwargs):
    """安全调用包装器——捕获异常并记录结构化日志后重新抛出

    用于边界显性化：可能失败的操作应通过此包装器调用，
    确保异常被记录后再向上传播，而非静默吞掉。
    """
    try:
        return func(*args, **kwargs)
    except Exception as e:
        logger.error(json.dumps({
            "trace_id": _trace_id(),
            "module_name": "data_analytics",
            "action": action + ".failed",
            "error": f"{type(e).__name__}: {e}",
        }, ensure_ascii=False))
        raise
