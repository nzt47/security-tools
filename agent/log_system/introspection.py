"""
内省式学习层 — 空闲时段自动分析日志，提炼认知洞察，生成行动建议

工作流程：
  空闲检测 -> 数据提取 -> 规则预分析 -> LLM深度分析 -> 知识提炼 -> 行动生成 -> 结果存储

关键原则：
  - 仅在系统空闲时执行，不干扰正常服务
  - 两阶段策略：先规则/统计预分析，再对高价值数据调用 LLM
  - 产出可执行的、优先级明确的改进建议
"""

import time
import json
import uuid
import logging
import threading
import platform
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone

from .storage import get_storage
from .models import (
    Insight, ActionItem, KnowledgeFinding,
    LogCategory, LogLevel, LogEntry,
)
from .analyzer import LogAnalyzer
from agent.logging_utils import log_dict

logger = logging.getLogger(__name__)

def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]


# 尝试引入 LLM 工具（可选）
try:
    from agent.tool_calling import ToolCallingService
    _LLM_AVAILABLE = True
except ImportError:
    _LLM_AVAILABLE = False
    logger.info(log_dict({'module_name': 'introspection', 'action': 'toolcallingservice', 'msg': '[Introspection] ToolCallingService 不可用，将仅使用规则引擎分析'}))


class IdleDetector:
    """空闲检测器 — 判断系统是否可执行分析任务"""

    def __init__(self, idle_timeout: int = 300, max_cpu: float = 20.0):
        """
        Args:
            idle_timeout: 无用户交互的秒数阈值（默认 5 分钟）
            max_cpu: CPU 使用率阈值百分比
        """
        self.idle_timeout = idle_timeout
        self.max_cpu = max_cpu
        self._last_activity = time.time()
        self._lock = threading.Lock()

    def mark_activity(self):
        """标记用户活动"""
        with self._lock:
            self._last_activity = time.time()

    def is_idle(self) -> bool:
        """检查系统是否空闲"""
        with self._lock:
            elapsed = time.time() - self._last_activity
            if elapsed < self.idle_timeout:
                return False

        # 检查 CPU 使用率（仅支持 psutil 可用时）
        try:
            import psutil
            cpu_pct = psutil.cpu_percent(interval=0.1)
            if cpu_pct > self.max_cpu:
                logger.debug("[Introspection] 系统繁忙: CPU %.1f%% > %.0f%%", cpu_pct, self.max_cpu)
                return False
        except ImportError:
            pass  # 没有 psutil 跳过 CPU 检查

        return True

    def seconds_until_idle(self) -> float:
        """返回还需多少秒进入空闲"""
        with self._lock:
            return max(0, self.idle_timeout - (time.time() - self._last_activity))


class InsightExtractor:
    """洞察提取器 — 从分析结果中提炼认知"""

    def __init__(self):
        self._llm_service = None

    def _get_llm_service(self):
        """延迟获取 LLM 服务"""
        if self._llm_service is None and _LLM_AVAILABLE:
            try:
                self._llm_service = ToolCallingService()
            except Exception as e:
                logger.warning("[Introspection] LLM 服务初始化失败: %s", e)
        return self._llm_service

    def extract_from_analysis(self, analysis: dict) -> List[Insight]:
        """从分析结果中提取洞察"""
        insights = []

        # 1. 规则触发 -> 趋势/优化洞察
        for hit in analysis.get('rule_hits', []):
            insight_type = 'optimization' if hit.get('severity') == 'critical' else 'trend'
            insights.append(Insight(
                type=insight_type,
                summary=hit.get('description', hit.get('rule', '规则触发')),
                detail=self._format_rule_detail(hit),
                confidence=0.8,
                evidence={'rule': hit['rule'], 'value': hit.get('value'), 'threshold': hit.get('threshold')},
                tags=['rule_engine', hit.get('severity', 'info')],
                source_analysis='rule_engine',
            ))

        # 2. 异常检测 -> anomaly 类型洞察
        for anomaly in analysis.get('anomalies', []):
            insights.append(Insight(
                type='anomaly',
                summary=anomaly.get('description', f"异常: {anomaly.get('type', 'unknown')}"),
                detail=json.dumps(anomaly, ensure_ascii=False, indent=2),
                confidence=0.7,
                evidence=anomaly,
                tags=['stats_engine', 'anomaly', anomaly.get('type', '')],
                source_analysis='stats_engine',
            ))

        # 3. 模式发现 -> pattern 类型洞察
        for pat in analysis.get('patterns', []):
            if pat.get('count', 0) >= 10:  # 只保留高频模式
                insights.append(Insight(
                    type='pattern',
                    summary=pat.get('detail', f"发现模式: {pat.get('type', '')}"),
                    detail=json.dumps(pat, ensure_ascii=False),
                    confidence=0.75,
                    evidence=pat,
                    tags=['stats_engine', 'pattern', pat.get('type', '')],
                    source_analysis='stats_engine',
                ))

        return insights

    def extract_with_llm(self, candidates: List[dict]) -> List[Insight]:
        """使用 LLM 对高价值候选数据进行深度分析（失败时降级）"""
        if not candidates:
            return []

        llm = self._get_llm_service()
        if not llm:
            # 降级：按规则提取
            insights = []
            for c in candidates:
                insights.append(Insight(
                    type='optimization',
                    summary=c.get('content', {}).get('description', '待分析的候选数据'),
                    detail=json.dumps(c, ensure_ascii=False),
                    confidence=0.5,
                    evidence=c,
                    tags=['llm_unavailable', c.get('type', '')],
                    source_analysis='rule_fallback',
                ))
            return insights

        # 构建 LLM 分析提示
        prompt = self._build_llm_prompt(candidates)
        try:
            response = llm.execute_tool(api_params={
                'messages': [
                    {'role': 'system', 'content': '你是一个系统日志分析专家。根据提供的日志分析候选数据，提炼关键洞察。'},
                    {'role': 'user', 'content': prompt},
                ],
                'model': 'claude-sonnet-4-6',
                'max_tokens': 2000,
            })
            return self._parse_llm_response(response, candidates)
        except Exception as e:
            logger.error("[Introspection] LLM 分析失败: %s", e)
            return self.extract_from_analysis({'rule_hits': [], 'anomalies': candidates, 'patterns': []})

    def _build_llm_prompt(self, candidates: List[dict]) -> str:
        """构建 LLM 分析提示"""
        items = []
        for i, c in enumerate(candidates, 1):
            items.append(f"{i}. [{c.get('severity', 'info').upper()}] {c.get('type', 'unknown')}")
            items.append(f"   内容: {json.dumps(c.get('content', {}), ensure_ascii=False)[:300]}")
        return (
            "以下是系统日志分析中识别出的高价值候选数据，请对每个条目进行深度分析：\n\n"
            + "\n".join(items) + "\n\n"
            + "请按以下 JSON 格式输出分析结果（不要有其他文字）：\n"
            + '{"insights": [{"type": "pattern|trend|anomaly|optimization", '
            + '"summary": "简短但有洞察力的摘要", '
            + '"detail": "详细分析说明（中文）", '
            + '"confidence": 0.0-1.0, '
            + '"tags": ["标签1", "标签2"]}]}'
        )

    def _parse_llm_response(self, response, candidates) -> List[Insight]:
        """解析 LLM 响应"""
        insights = []
        try:
            content = response
            if isinstance(response, dict):
                content = response.get('content', response.get('response', str(response)))

            if isinstance(content, str):
                # 尝试提取 JSON
                import re
                json_match = re.search(r'\{.*\}', content, re.DOTALL)
                if json_match:
                    data = json.loads(json_match.group())
                    for item in data.get('insights', []):
                        insights.append(Insight(
                            type=item.get('type', 'pattern'),
                            summary=item.get('summary', ''),
                            detail=item.get('detail', ''),
                            confidence=item.get('confidence', 0.5),
                            tags=item.get('tags', []),
                            source_analysis='llm',
                        ))
        except Exception as e:
            logger.warning("[Introspection] LLM 响应解析失败: %s", e)
            # 降级
            for c in candidates:
                insights.append(Insight(
                    type='optimization',
                    summary=c.get('content', {}).get('description', f"候选: {c.get('type')}"),
                    detail='LLM 分析解析失败，此为降级条目',
                    confidence=0.4,
                    evidence=c,
                    source_analysis='llm_parse_failed',
                ))

        return insights

    def _format_rule_detail(self, hit: dict) -> str:
        """格式化规则触发详情"""
        return (
            f"规则 '{hit.get('rule')}' 触发: "
            f"指标={hit.get('metric')}, "
            f"当前值={hit.get('value')}, "
            f"阈值={hit.get('threshold')}, "
            f"描述={hit.get('description')}"
        )


class ActionGenerator:
    """行动建议生成器 — 基于洞察生成可执行的改进建议"""

    def __init__(self):
        # 预定义行动建议模板
        self._templates = {
            'high_p95_latency': {
                'priority': 'high',
                'category': 'performance',
                'title': '优化高延迟操作',
                'description': '系统 P95 延迟超出预期阈值，建议分析耗时操作并优化。',
                'effort': 'medium',
            },
            'high_error_rate': {
                'priority': 'high',
                'category': 'reliability',
                'title': '降低系统错误率',
                'description': '错误率超过警戒线，建议检查各模块错误分布并优先处理高发模块。',
                'effort': 'medium',
            },
            'error_concentration': {
                'priority': 'high',
                'category': 'reliability',
                'title': '排查错误集中模块',
                'description': '检测到某个模块错误占比异常高，建议重点审查该模块的异常处理逻辑。',
                'effort': 'medium',
            },
            'slow_operation_cluster': {
                'priority': 'medium',
                'category': 'performance',
                'title': '优化慢操作集群',
                'description': '部分操作耗时持续超过 3 秒，建议进行性能分析并针对性优化。',
                'effort': 'medium',
            },
            'anomaly': {
                'priority': 'medium',
                'category': 'reliability',
                'title': '调查异常指标',
                'description': '系统检测到离群数据点，建议确认是否为偶发问题或需要修复的持续问题。',
                'effort': 'small',
            },
        }

    def generate(self, insights: List[Insight], analysis: dict = None) -> List[ActionItem]:
        """基于洞察生成行动建议"""
        items = []
        seen_titles = set()

        for insight in insights:
            # 从 insight 的 evidence 匹配模板
            template_key = None
            evidence = insight.evidence if isinstance(insight.evidence, dict) else {}
            rule_name = evidence.get('rule', '')

            if rule_name and rule_name in self._templates:
                template_key = rule_name
            elif insight.type == 'anomaly':
                anomaly_type = evidence.get('type', '')
                if anomaly_type in self._templates:
                    template_key = anomaly_type
                else:
                    template_key = 'anomaly'
            elif insight.type == 'pattern' and 'slow' in str(insight.tags):
                template_key = 'slow_operation_cluster'
            elif insight.type == 'optimization':
                template_key = 'high_p95_latency'  # 默认映射

            if template_key and template_key not in seen_titles:
                tmpl = self._templates.get(template_key, {})
                seen_titles.add(template_key)
                items.append(ActionItem(
                    priority=tmpl.get('priority', 'medium'),
                    category=tmpl.get('category', 'performance'),
                    title=tmpl.get('title', '检查系统状态'),
                    description=tmpl.get('description', ''),
                    rationale=f"基于内省分析: {insight.summary}",
                    expected_impact='待评估',
                    effort=tmpl.get('effort', 'medium'),
                    insight_id=str(id(insight)),
                ))

        # 按优先级排序
        priority_order = {'high': 0, 'medium': 1, 'low': 2}
        items.sort(key=lambda x: priority_order.get(x.priority, 99))

        return items


# ════════════════════════════════════════════════════════════
# 内省引擎主类
# ════════════════════════════════════════════════════════════

class IntrospectionEngine:
    """内省式学习引擎 — 组合空闲检测、分析、洞察提取、行动生成"""

    def __init__(self):
        self.idle_detector = IdleDetector()
        self.analyzer = LogAnalyzer()
        self.extractor = InsightExtractor()
        self.action_gen = ActionGenerator()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_run = 0.0
        self._run_count = 0
        self._lock = threading.Lock()

    @property
    def storage(self):
        return get_storage()

    def mark_activity(self):
        """标记用户活动（供外部调用）"""
        self.idle_detector.mark_activity()

    def should_run(self) -> bool:
        """判断是否应该执行分析"""
        if not self.idle_detector.is_idle():
            return False
        if self._running:
            return False
        # 保证至少 10 分钟间隔
        if time.time() - self._last_run < 600:
            return False
        return True

    def run_cycle(self, force: bool = False) -> Optional[Dict[str, Any]]:
        """执行一次完整的内省分析周期"""
        if not force and not self.should_run():
            return None

        with self._lock:
            if self._running and not force:
                return None
            self._running = True

        try:
            start = time.time()
            logger.info("[Introspection] 开始内省分析周期 #%d", self._run_count + 1)

            # 1. 执行分析
            analysis = self.analyzer.analyze(hours=24)
            if not analysis or 'error' in analysis:
                logger.warning("[Introspection] 分析阶段失败: %s", analysis.get('error', '未知错误'))
                return None

            # 2. 提取洞察（规则引擎部分）
            rule_insights = self.extractor.extract_from_analysis(analysis)
            logger.info("[Introspection] 规则引擎产出 %d 条洞察", len(rule_insights))

            # 3. 筛选高价值数据做 LLM 深度分析
            llm_candidates = self.analyzer.get_llm_candidates(analysis)
            llm_insights = []
            if llm_candidates:
                llm_insights = self.extractor.extract_with_llm(llm_candidates)
                logger.info("[Introspection] LLM 分析产出 %d 条深度洞察", len(llm_insights))

            all_insights = rule_insights + llm_insights

            # 4. 生成行动建议
            action_items = self.action_gen.generate(all_insights, analysis)
            logger.info("[Introspection] 生成 %d 条行动建议", len(action_items))

            # 5. 存储结果
            storage = self.storage
            if storage:
                for insight in all_insights:
                    storage.write_insight(insight)
                for item in action_items:
                    storage.write_action_item(item)

                # 存储知识发现
                if all_insights:
                    storage.write_knowledge(KnowledgeFinding(
                        domain='system_behavior',
                        finding=analysis.get('summary', ''),
                        tags=['introspection', 'auto'],
                        confidence=0.7,
                    ))

            elapsed = time.time() - start
            self._run_count += 1
            self._last_run = time.time()

            result = {
                'cycle': self._run_count,
                'elapsed_seconds': round(elapsed, 2),
                'insights_count': len(all_insights),
                'action_items_count': len(action_items),
                'llm_used': len(llm_insights) > 0,
                'summary': analysis.get('summary', ''),
            }

            logger.info(
                "[Introspection] 分析完成: #%d, %.1fs, %d 洞察, %d 行动建议, LLM=%s",
                self._run_count, elapsed, len(all_insights), len(action_items), bool(llm_insights)
            )
            return result

        except Exception as e:
            logger.error("[Introspection] 内省分析异常: %s", e, exc_info=True)
            return None
        finally:
            self._running = False

    def start_background_loop(self, interval_seconds: int = 1800):
        """启动后台循环（在独立线程中定期执行）"""
        if self._thread and self._thread.is_alive():
            logger.warning(log_dict({'module_name': 'introspection', 'action': 'log', 'msg': '[Introspection] 后台循环已在运行'}))
            return

        def _loop():
            logger.info("[Introspection] 后台循环已启动，间隔 %d 秒", interval_seconds)
            while True:
                try:
                    self.run_cycle()
                except Exception as e:
                    logger.error("[Introspection] 后台循环异常: %s", e)
                time.sleep(interval_seconds)

        self._thread = threading.Thread(target=_loop, daemon=True, name='introspection-loop')
        self._thread.start()

    def stop_background_loop(self):
        """停止后台循环"""
        self._thread = None
        logger.info(log_dict({'module_name': 'introspection', 'action': 'log', 'msg': '[Introspection] 后台循环已停止'}))

    def get_status(self) -> dict:
        """获取内省引擎状态"""
        return {
            'running': self._running,
            'last_run': datetime.fromtimestamp(self._last_run).isoformat() if self._last_run else 'never',
            'total_cycles': self._run_count,
            'is_idle': self.idle_detector.is_idle(),
            'seconds_until_idle': round(self.idle_detector.seconds_until_idle()),
            'thread_alive': self._thread and self._thread.is_alive() if self._thread else False,
        }
