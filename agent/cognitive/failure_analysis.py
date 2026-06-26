#!/usr/bin/env python3
"""
失败模式分类归档模块

功能：
- 幻觉案例收集机制
- 按类型分桶：编造 API、字段错误、流程跳步等
- 针对性优化建议生成
- 结构化日志输出（包含 trace_id、module_name、action、duration_ms）
"""

import os
import json
import time
import sqlite3
import logging
import threading
from datetime import datetime
from enum import Enum
from dataclasses import dataclass, field, asdict
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)


class FailureType(Enum):
    """失败类型枚举"""
    API_FICTION = "api_fiction"           # 编造不存在的API
    FIELD_ERROR = "field_error"           # 字段错误（类型、格式、值错误）
    FLOW_SKIP = "flow_skip"               # 流程跳步（跳过必要步骤）
    LOGIC_ERROR = "logic_error"           # 逻辑错误（推理错误）
    DATA_INVENTION = "data_invention"     # 数据虚构（编造不存在的数据）
    TOOL_MISUSE = "tool_misuse"           # 工具使用错误
    CONTEXT_LOSS = "context_loss"         # 上下文丢失
    TIMEOUT = "timeout"                   # 超时
    RATE_LIMIT = "rate_limit"             # 限流
    AUTH_ERROR = "auth_error"             # 认证错误
    UNKNOWN = "unknown"                   # 未知错误

    @classmethod
    def from_string(cls, name: str) -> 'FailureType':
        """从字符串获取枚举值"""
        try:
            return cls[name.upper()]
        except KeyError:
            return cls.UNKNOWN


class FailureSeverity(Enum):
    """失败严重程度"""
    LOW = "low"           # 低（不影响核心功能）
    MEDIUM = "medium"     # 中（部分功能受影响）
    HIGH = "high"         # 高（核心功能受影响）
    CRITICAL = "critical" # 严重（系统不可用）


@dataclass
class FailureRecord:
    """失败记录"""
    trace_id: str
    failure_type: FailureType
    severity: FailureSeverity = FailureSeverity.MEDIUM
    message: str = ""
    source: str = ""
    timestamp: float = field(default_factory=time.time)
    context: Dict[str, Any] = field(default_factory=dict)
    evidence: List[str] = field(default_factory=list)
    suggested_fix: str = ""
    fix_applied: bool = False
    
    def to_dict(self) -> dict:
        """序列化为字典"""
        d = asdict(self)
        d['failure_type'] = self.failure_type.value
        d['severity'] = self.severity.value
        d['timestamp_iso'] = datetime.fromtimestamp(self.timestamp).isoformat()
        return d


@dataclass
class FailurePattern:
    """失败模式/模式识别结果"""
    pattern_id: str
    failure_type: FailureType
    description: str = ""
    frequency: int = 0
    last_occurrence: float = 0.0
    affected_components: List[str] = field(default_factory=list)
    suggested_actions: List[str] = field(default_factory=list)
    
    def to_dict(self) -> dict:
        """序列化为字典"""
        d = asdict(self)
        d['failure_type'] = self.failure_type.value
        d['last_occurrence_iso'] = datetime.fromtimestamp(self.last_occurrence).isoformat() if self.last_occurrence else ""
        return d


class FailureAnalyzer:
    """失败分析器"""
    
    def __init__(self, storage_path: str = None):
        self.storage_path = storage_path or os.path.join(
            os.path.dirname(__file__), '..', '..', 'data', 'failures'
        )
        os.makedirs(self.storage_path, exist_ok=True)
        self._db_path = os.path.join(self.storage_path, 'failures.db')
        self._local = threading.local()
        self._write_lock = threading.Lock()
        self._initialized = False
        
        self._detection_patterns = {
            FailureType.API_FICTION: [
                r'(调用|使用|调用了).*不存在.*API',
                r'(调用|使用|调用了).*API.*不存在',
                r'未找到.*API',
                r'API.*不存在',
                r'无效的.*API.*名称',
                r'虚构.*API',
                r'不存在的.*API',
            ],
            FailureType.FIELD_ERROR: [
                r'字段.*类型错误',
                r'字段.*格式错误',
                r'缺少.*字段',
                r'字段.*不能为空',
                r'字段.*值无效',
            ],
            FailureType.FLOW_SKIP: [
                r'跳过.*步骤',
                r'未执行.*步骤',
                r'缺少.*步骤',
                r'直接跳过',
            ],
            FailureType.DATA_INVENTION: [
                r'虚构数据',
                r'编造信息',
                r'不存在的.*数据',
                r'伪造数据',
            ],
            FailureType.TOOL_MISUSE: [
                r'工具.*使用错误',
                r'参数.*不正确',
                r'工具.*调用失败',
                r'不支持的.*参数',
            ],
            FailureType.CONTEXT_LOSS: [
                r'上下文.*丢失',
                r'上下文.*为空',
                r'无法获取.*上下文',
            ],
        }
        
        self._fix_suggestions = {
            FailureType.API_FICTION: [
                "检查 API 文档确认接口是否存在",
                "验证 API 名称拼写是否正确",
                "确认 API 版本是否正确",
                "检查权限是否足够",
            ],
            FailureType.FIELD_ERROR: [
                "验证输入数据类型是否正确",
                "检查字段格式是否符合要求",
                "确保必填字段不为空",
                "添加输入验证逻辑",
            ],
            FailureType.FLOW_SKIP: [
                "检查流程定义是否完整",
                "验证流程条件是否正确",
                "添加步骤依赖检查",
                "增加流程监控告警",
            ],
            FailureType.LOGIC_ERROR: [
                "审查推理逻辑",
                "添加更多测试用例",
                "增加断言检查",
                "使用更严格的验证",
            ],
            FailureType.DATA_INVENTION: [
                "增加事实核查步骤",
                "验证数据来源可靠性",
                "添加外部数据源验证",
                "使用知识库验证",
            ],
            FailureType.TOOL_MISUSE: [
                "检查工具参数定义",
                "验证参数类型和格式",
                "添加参数校验",
                "更新工具调用逻辑",
            ],
            FailureType.CONTEXT_LOSS: [
                "增加上下文保持机制",
                "使用 ContextVar 传递上下文",
                "添加上下文检查点",
                "增加上下文恢复逻辑",
            ],
            FailureType.TIMEOUT: [
                "增加超时重试机制",
                "优化请求批处理",
                "使用异步调用",
                "增加超时预警",
            ],
        }
        
        logger.info(f"[FailureAnalysis] 初始化失败分析器，存储路径: {self.storage_path}")
    
    def _get_conn(self) -> sqlite3.Connection:
        """获取数据库连接"""
        if not hasattr(self._local, 'conn') or self._local.conn is None:
            os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
            self._local.conn = sqlite3.connect(
                self._db_path, check_same_thread=False
            )
            self._local.conn.row_factory = sqlite3.Row
        return self._local.conn
    
    def initialize(self):
        """初始化数据库表"""
        if self._initialized:
            return
        
        with self._write_lock, self._get_conn() as conn:
            cursor = conn.cursor()
            
            # 失败记录表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS failures (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trace_id TEXT NOT NULL,
                    failure_type TEXT NOT NULL,
                    severity TEXT NOT NULL DEFAULT 'medium',
                    message TEXT DEFAULT '',
                    source TEXT DEFAULT '',
                    timestamp REAL NOT NULL,
                    context TEXT DEFAULT '{}',
                    evidence TEXT DEFAULT '[]',
                    suggested_fix TEXT DEFAULT '',
                    fix_applied INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT (datetime('now'))
                )
            """)
            
            # 失败模式统计表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS failure_patterns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pattern_id TEXT NOT NULL UNIQUE,
                    failure_type TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    frequency INTEGER DEFAULT 0,
                    last_occurrence REAL DEFAULT 0,
                    affected_components TEXT DEFAULT '[]',
                    suggested_actions TEXT DEFAULT '[]',
                    created_at TEXT DEFAULT (datetime('now')),
                    updated_at TEXT DEFAULT (datetime('now'))
                )
            """)
            
            # 创建索引
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_failures_trace_id ON failures(trace_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_failures_type ON failures(failure_type)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_failures_severity ON failures(severity)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_failures_timestamp ON failures(timestamp)")
            
            conn.commit()
        
        self._initialized = True
        logger.info("[FailureAnalysis] 数据库初始化完成")
    
    def classify_failure(self, message: str) -> FailureType:
        """根据错误消息分类失败类型"""
        import re
        
        logger.info(json.dumps({
            "trace_id": "",
            "module_name": "failure_analysis",
            "action": "classify_failure",
            "message": message[:100] if len(message) > 100 else message,
            "duration_ms": 0,
            "level": "INFO"
        }))
        
        for failure_type, patterns in self._detection_patterns.items():
            for pattern in patterns:
                match = re.search(pattern, message, re.IGNORECASE)
                if match:
                    logger.info(f"[FailureAnalysis] 🔍 检测到失败类型 [{failure_type.value}]")
                    logger.info(f"[FailureAnalysis]   匹配模式: {pattern}")
                    logger.info(f"[FailureAnalysis]   匹配内容: {match.group() if match else ''}")
                    return failure_type
        
        logger.warning(f"[FailureAnalysis] ⚠️ 未识别的失败类型，消息: {message[:100]}")
        return FailureType.UNKNOWN
    
    def generate_fix_suggestion(self, failure_type: FailureType) -> str:
        """生成优化建议"""
        suggestions = self._fix_suggestions.get(failure_type, [])
        if suggestions:
            return "\n".join(f"- {s}" for s in suggestions)
        return "暂无针对性建议，请根据具体情况分析处理"
    
    def record_failure(self, record: FailureRecord):
        """记录失败案例"""
        self.initialize()
        
        logger.info(f"[FailureAnalysis] 📝 开始记录失败案例")
        logger.info(f"[FailureAnalysis]   trace_id: {record.trace_id}")
        logger.info(f"[FailureAnalysis]   failure_type: {record.failure_type.value}")
        logger.info(f"[FailureAnalysis]   severity: {record.severity.value}")
        logger.info(f"[FailureAnalysis]   source: {record.source}")
        logger.info(f"[FailureAnalysis]   message: {record.message[:100]}...")
        logger.info(f"[FailureAnalysis]   evidence_count: {len(record.evidence)}")
        
        context_json = json.dumps(record.context, ensure_ascii=False)
        evidence_json = json.dumps(record.evidence, ensure_ascii=False)
        
        try:
            with self._write_lock, self._get_conn() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """INSERT INTO failures
                       (trace_id, failure_type, severity, message, source,
                        timestamp, context, evidence, suggested_fix, fix_applied)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (record.trace_id, record.failure_type.value, record.severity.value,
                     record.message[:2000], record.source, record.timestamp,
                     context_json, evidence_json, record.suggested_fix[:2000],
                     1 if record.fix_applied else 0)
                )
                conn.commit()
            
            logger.info(f"[FailureAnalysis] ✅ 失败案例写入数据库成功")
            
            # 更新失败模式统计
            self._update_pattern_statistics(record.failure_type, record.source)
            
            # 输出结构化日志
            logger.info(json.dumps({
                "trace_id": record.trace_id,
                "module_name": "failure_analysis",
                "action": "record_failure",
                "failure_type": record.failure_type.value,
                "severity": record.severity.value,
                "duration_ms": 0,
                "level": "INFO"
            }))
            
            logger.info(f"[FailureAnalysis] ✅ 记录失败案例完成: {record.failure_type.value}, trace_id={record.trace_id}")
            
        except Exception as e:
            logger.error(f"[FailureAnalysis] ❌ 记录失败案例失败: {str(e)}")
            logger.error(f"[FailureAnalysis]   trace_id: {record.trace_id}")
            logger.error(f"[FailureAnalysis]   failure_type: {record.failure_type.value}")
            raise
    
    def _update_pattern_statistics(self, failure_type: FailureType, source: str):
        """更新失败模式统计"""
        pattern_id = f"pattern_{failure_type.value}"
        
        with self._get_conn() as conn:
            cursor = conn.cursor()
            
            # 检查模式是否已存在
            cursor.execute("SELECT * FROM failure_patterns WHERE pattern_id = ?", (pattern_id,))
            row = cursor.fetchone()
            
            if row:
                # 更新现有模式
                new_frequency = row['frequency'] + 1
                components = json.loads(row['affected_components'])
                if source and source not in components:
                    components.append(source)
                
                cursor.execute(
                    """UPDATE failure_patterns
                       SET frequency = ?, last_occurrence = ?, affected_components = ?,
                           updated_at = datetime('now')
                       WHERE pattern_id = ?""",
                    (new_frequency, time.time(), json.dumps(components), pattern_id)
                )
            else:
                # 创建新模式
                description = self._get_failure_type_description(failure_type)
                actions = self._fix_suggestions.get(failure_type, [])
                
                cursor.execute(
                    """INSERT INTO failure_patterns
                       (pattern_id, failure_type, description, frequency,
                        last_occurrence, affected_components, suggested_actions)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (pattern_id, failure_type.value, description, 1,
                     time.time(), json.dumps([source] if source else []),
                     json.dumps(actions))
                )
            
            conn.commit()
    
    def _get_failure_type_description(self, failure_type: FailureType) -> str:
        """获取失败类型描述"""
        descriptions = {
            FailureType.API_FICTION: "编造不存在的API调用",
            FailureType.FIELD_ERROR: "字段类型或格式错误",
            FailureType.FLOW_SKIP: "流程执行跳步",
            FailureType.LOGIC_ERROR: "推理逻辑错误",
            FailureType.DATA_INVENTION: "虚构数据或信息",
            FailureType.TOOL_MISUSE: "工具使用方式错误",
            FailureType.CONTEXT_LOSS: "上下文信息丢失",
            FailureType.TIMEOUT: "请求超时",
            FailureType.RATE_LIMIT: "API限流",
            FailureType.AUTH_ERROR: "认证失败",
            FailureType.UNKNOWN: "未知错误类型",
        }
        return descriptions.get(failure_type, "")
    
    def query_failures(self, **kwargs) -> List[dict]:
        """查询失败记录"""
        self.initialize()
        
        sql = "SELECT * FROM failures WHERE 1=1"
        params = []
        
        failure_type = kwargs.get('failure_type')
        if failure_type:
            sql += " AND failure_type = ?"
            params.append(failure_type.value if isinstance(failure_type, FailureType) else failure_type)
        
        severity = kwargs.get('severity')
        if severity:
            sql += " AND severity = ?"
            params.append(severity.value if isinstance(severity, FailureSeverity) else severity)
        
        trace_id = kwargs.get('trace_id')
        if trace_id:
            sql += " AND trace_id = ?"
            params.append(trace_id)
        
        source = kwargs.get('source')
        if source:
            sql += " AND source LIKE ?"
            params.append(f"%{source}%")
        
        start_time = kwargs.get('start_time', 0)
        if start_time > 0:
            sql += " AND timestamp >= ?"
            params.append(start_time)
        
        end_time = kwargs.get('end_time', float('inf'))
        if end_time > 0:
            sql += " AND timestamp <= ?"
            params.append(end_time)
        
        limit = kwargs.get('limit', 100)
        offset = kwargs.get('offset', 0)
        
        sql += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            results = []
            for row in cursor.fetchall():
                result = dict(row)
                result['context'] = json.loads(result['context'])
                result['evidence'] = json.loads(result['evidence'])
                result['fix_applied'] = bool(result['fix_applied'])
                results.append(result)
        
        return results
    
    def get_failure_patterns(self) -> List[FailurePattern]:
        """获取失败模式统计"""
        self.initialize()
        
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM failure_patterns ORDER BY frequency DESC")
            
            patterns = []
            for row in cursor.fetchall():
                pattern = FailurePattern(
                    pattern_id=row['pattern_id'],
                    failure_type=FailureType(row['failure_type']),
                    description=row['description'],
                    frequency=row['frequency'],
                    last_occurrence=row['last_occurrence'],
                    affected_components=json.loads(row['affected_components']),
                    suggested_actions=json.loads(row['suggested_actions'])
                )
                patterns.append(pattern)
        
        return patterns
    
    def get_failure_summary(self, hours: float = 24) -> Dict[str, Any]:
        """获取失败汇总统计"""
        self.initialize()
        
        since = time.time() - hours * 3600
        
        with self._get_conn() as conn:
            cursor = conn.cursor()
            
            # 总失败数
            cursor.execute("SELECT COUNT(*) as cnt FROM failures WHERE timestamp >= ?", (since,))
            total = cursor.fetchone()['cnt']
            
            # 按类型统计
            cursor.execute("""
                SELECT failure_type, COUNT(*) as cnt 
                FROM failures 
                WHERE timestamp >= ? 
                GROUP BY failure_type
                ORDER BY cnt DESC
            """, (since,))
            by_type = {row['failure_type']: row['cnt'] for row in cursor.fetchall()}
            
            # 按严重程度统计
            cursor.execute("""
                SELECT severity, COUNT(*) as cnt 
                FROM failures 
                WHERE timestamp >= ? 
                GROUP BY severity
            """, (since,))
            by_severity = {row['severity']: row['cnt'] for row in cursor.fetchall()}
            
            # 前10来源
            cursor.execute("""
                SELECT source, COUNT(*) as cnt 
                FROM failures 
                WHERE timestamp >= ? AND source != '' 
                GROUP BY source 
                ORDER BY cnt DESC 
                LIMIT 10
            """, (since,))
            top_sources = [(row['source'], row['cnt']) for row in cursor.fetchall()]
        
        return {
            "time_range_hours": hours,
            "total_failures": total,
            "by_type": by_type,
            "by_severity": by_severity,
            "top_sources": top_sources,
            "patterns": [p.to_dict() for p in self.get_failure_patterns()]
        }
    
    def suggest_optimizations(self, failure_type: FailureType = None) -> List[Dict[str, Any]]:
        """生成优化建议"""
        if failure_type:
            suggestions = self._fix_suggestions.get(failure_type, [])
            return [{
                "failure_type": failure_type.value,
                "description": self._get_failure_type_description(failure_type),
                "suggestions": suggestions
            }]
        
        # 返回所有类型的建议
        results = []
        for ft in FailureType:
            if ft != FailureType.UNKNOWN:
                results.append({
                    "failure_type": ft.value,
                    "description": self._get_failure_type_description(ft),
                    "suggestions": self._fix_suggestions.get(ft, [])
                })
        
        return results

    def get_high_frequency_failures(self, threshold: int = 5,
                                    hours: float = 24 * 7) -> List[Dict[str, Any]]:
        """获取高频失败模式（超过阈值的失败类型）

        Args:
            threshold: 频率阈值
            hours: 统计时间范围（小时）

        Returns:
            高频失败模式列表
        """
        self.initialize()
        start_time = time.time()

        summary = self.get_failure_summary(hours=hours)
        patterns = summary.get('patterns', [])

        high_freq = []
        for p in patterns:
            if p.get('frequency', 0) >= threshold:
                high_freq.append(p)

        duration_ms = (time.time() - start_time) * 1000
        logger.info(json.dumps({
            "trace_id": "",
            "module_name": "failure_analysis",
            "action": "get_high_frequency_failures",
            "threshold": threshold,
            "hours": hours,
            "count": len(high_freq),
            "duration_ms": round(duration_ms, 2),
            "level": "INFO"
        }))

        return high_freq

    def generate_auto_fix_suggestion(self, failure_type: FailureType,
                                     target_type: str = "prompt",
                                     target_id: str = "") -> Dict[str, Any]:
        """自动生成修复建议（针对特定失败类型）

        Args:
            failure_type: 失败类型
            target_type: 修复目标类型（prompt/skill/tool）
            target_id: 目标ID

        Returns:
            修复建议字典
        """
        self.initialize()
        start_time = time.time()

        base_suggestions = self._fix_suggestions.get(failure_type, [])
        description = self._get_failure_type_description(failure_type)

        prompt_templates = {
            FailureType.API_FICTION: {
                "instruction": "你是一个严谨的API调用助手。在调用任何API之前，必须先确认该API确实存在。",
                "constraints": [
                    "1. 禁止调用未经文档验证的API",
                    "2. 调用前必须检查API名称拼写",
                    "3. 不确定时先搜索API文档",
                    "4. 使用API前先验证权限"
                ],
                "example": "错误：调用 get_user_infos()\n正确：先调用 list_apis() 确认存在，再调用 get_user_info()"
            },
            FailureType.FIELD_ERROR: {
                "instruction": "你是一个数据校验专家。处理数据时必须严格验证字段类型和格式。",
                "constraints": [
                    "1. 所有输入字段必须进行类型校验",
                    "2. 必填字段不能为空",
                    "3. 枚举值必须在允许范围内",
                    "4. 字符串长度必须在限制内"
                ],
                "example": "错误：直接使用用户输入\n正确：先验证字段类型，再进行业务处理"
            },
            FailureType.FLOW_SKIP: {
                "instruction": "你是一个流程执行专家。必须严格按照步骤执行，不得跳步。",
                "constraints": [
                    "1. 每个步骤执行前检查前置条件",
                    "2. 步骤完成后验证结果",
                    "3. 不允许跳过必要步骤",
                    "4. 步骤顺序不可颠倒"
                ],
                "example": "错误：直接输出结果\n正确：分析问题→制定计划→执行步骤→验证结果"
            },
            FailureType.LOGIC_ERROR: {
                "instruction": "你是一个逻辑推理专家。推理过程必须严谨，避免逻辑谬误。",
                "constraints": [
                    "1. 推理必须有明确的前提和结论",
                    "2. 避免循环论证和因果倒置",
                    "3. 使用演绎推理而非主观臆断",
                    "4. 关键结论必须有证据支持"
                ],
                "example": "错误：因为A所以B（无证据）\n正确：因为C和D，所以A，因此B"
            },
            FailureType.DATA_INVENTION: {
                "instruction": "你是一个事实核查员。所有陈述必须基于真实数据，不得编造。",
                "constraints": [
                    "1. 所有数据必须有可靠来源",
                    "2. 不确定时明确说明'不确定'",
                    "3. 禁止编造数字、人名、事件",
                    "4. 引用数据需注明出处"
                ],
                "example": "错误：某公司年营收100亿（编造）\n正确：根据公开财报，某公司2023年营收约XX亿"
            },
            FailureType.TOOL_MISUSE: {
                "instruction": "你是一个工具使用专家。使用工具前必须仔细阅读工具说明。",
                "constraints": [
                    "1. 使用前阅读工具参数说明",
                    "2. 参数类型必须严格匹配",
                    "3. 必填参数不可省略",
                    "4. 遇到错误先检查参数"
                ],
                "example": "错误：search(query='test', limit='abc')\n正确：search(query='test', limit=10)"
            },
            FailureType.CONTEXT_LOSS: {
                "instruction": "你是一个上下文保持专家。必须在对话中保持上下文一致性。",
                "constraints": [
                    "1. 回复前先回顾对话历史",
                    "2. 关键信息必须显式引用",
                    "3. 话题切换需明确说明",
                    "4. 重要决策需确认上下文"
                ],
                "example": "错误：直接回答新问题\n正确：根据之前的讨论，结合您的新问题..."
            },
        }

        template = prompt_templates.get(failure_type, {
            "instruction": f"请特别注意避免{description}类型的错误。",
            "constraints": [f"- {s}" for s in base_suggestions],
            "example": ""
        })

        suggestion = {
            "failure_type": failure_type.value,
            "failure_description": description,
            "target_type": target_type,
            "target_id": target_id,
            "fix_type": "prompt_optimization",
            "generated_prompt": self._format_fix_prompt(template),
            "base_suggestions": base_suggestions,
            "template": template,
            "confidence": 0.7 if failure_type in prompt_templates else 0.4,
            "estimated_improvement": "20%-40%"
        }

        duration_ms = (time.time() - start_time) * 1000
        logger.info(json.dumps({
            "trace_id": "",
            "module_name": "failure_analysis",
            "action": "generate_auto_fix_suggestion",
            "failure_type": failure_type.value,
            "target_type": target_type,
            "confidence": suggestion['confidence'],
            "duration_ms": round(duration_ms, 2),
            "level": "INFO"
        }))

        return suggestion

    def _format_fix_prompt(self, template: Dict[str, Any]) -> str:
        """格式化修复提示词"""
        parts = []

        if template.get('instruction'):
            parts.append(f"【系统指令】\n{template['instruction']}")

        if template.get('constraints'):
            parts.append(f"\n【约束条件】\n" + "\n".join(template['constraints']))

        if template.get('example'):
            parts.append(f"\n【示例】\n{template['example']}")

        return "\n\n".join(parts)

    def track_fix_effectiveness(self, failure_type: FailureType,
                                fix_start_time: float,
                                window_hours: float = 24 * 7) -> Dict[str, Any]:
        """追踪修复效果（对比修复前后的失败率变化）

        Args:
            failure_type: 失败类型
            fix_start_time: 修复开始时间戳
            window_hours: 对比窗口（小时）

        Returns:
            效果分析结果
        """
        self.initialize()
        start_time = time.time()

        before_start = fix_start_time - window_hours * 3600
        after_end = min(time.time(), fix_start_time + window_hours * 3600)

        before_failures = self.query_failures(
            failure_type=failure_type,
            start_time=before_start,
            end_time=fix_start_time
        )

        after_failures = self.query_failures(
            failure_type=failure_type,
            start_time=fix_start_time,
            end_time=after_end
        )

        before_count = len(before_failures)
        after_count = len(after_failures)

        if before_count > 0:
            reduction_rate = (before_count - after_count) / before_count * 100
        else:
            reduction_rate = 0.0 if after_count == 0 else -100.0

        before_duration = fix_start_time - before_start
        after_duration = after_end - fix_start_time

        before_rate = before_count / max(before_duration / 86400, 0.01)
        after_rate = after_count / max(after_duration / 86400, 0.01)

        if before_rate > 0:
            rate_reduction = (before_rate - after_rate) / before_rate * 100
        else:
            rate_reduction = 0.0 if after_rate == 0 else -100.0

        result = {
            "failure_type": failure_type.value,
            "fix_start_time": fix_start_time,
            "fix_start_time_iso": datetime.fromtimestamp(fix_start_time).isoformat(),
            "before_period": {
                "start": before_start,
                "end": fix_start_time,
                "days": round(before_duration / 86400, 2),
                "failure_count": before_count,
                "daily_rate": round(before_rate, 2)
            },
            "after_period": {
                "start": fix_start_time,
                "end": after_end,
                "days": round(after_duration / 86400, 2),
                "failure_count": after_count,
                "daily_rate": round(after_rate, 2)
            },
            "absolute_reduction": before_count - after_count,
            "reduction_rate_percent": round(reduction_rate, 2),
            "rate_reduction_percent": round(rate_reduction, 2),
            "is_improved": after_count < before_count,
            "is_significant": rate_reduction > 20
        }

        duration_ms = (time.time() - start_time) * 1000
        logger.info(json.dumps({
            "trace_id": "",
            "module_name": "failure_analysis",
            "action": "track_fix_effectiveness",
            "failure_type": failure_type.value,
            "before_count": before_count,
            "after_count": after_count,
            "reduction_rate": round(reduction_rate, 2),
            "duration_ms": round(duration_ms, 2),
            "level": "INFO"
        }))

        return result

    def batch_generate_fix_suggestions(self, threshold: int = 5,
                                       hours: float = 24 * 7) -> List[Dict[str, Any]]:
        """批量生成高频失败模式的修复建议

        Args:
            threshold: 频率阈值
            hours: 统计时间范围

        Returns:
            修复建议列表
        """
        self.initialize()
        start_time = time.time()

        high_freq = self.get_high_frequency_failures(threshold=threshold, hours=hours)

        suggestions = []
        for pattern in high_freq:
            try:
                ft = FailureType(pattern.get('failure_type', 'unknown'))
                suggestion = self.generate_auto_fix_suggestion(ft)
                suggestion['frequency'] = pattern.get('frequency', 0)
                suggestion['pattern_id'] = pattern.get('pattern_id', '')
                suggestions.append(suggestion)
            except Exception as e:
                logger.warning(json.dumps({
                    "trace_id": "",
                    "module_name": "failure_analysis",
                    "action": "batch_generate_fix_suggestions",
                    "warning": f"生成建议失败: {e}",
                    "pattern": pattern.get('pattern_id'),
                    "duration_ms": 0,
                    "level": "WARNING"
                }))

        suggestions.sort(key=lambda x: x.get('frequency', 0), reverse=True)

        duration_ms = (time.time() - start_time) * 1000
        logger.info(json.dumps({
            "trace_id": "",
            "module_name": "failure_analysis",
            "action": "batch_generate_fix_suggestions",
            "threshold": threshold,
            "suggestion_count": len(suggestions),
            "duration_ms": round(duration_ms, 2),
            "level": "INFO"
        }))

        return suggestions

    def mark_fix_applied(self, trace_id: str, failure_type: FailureType = None,
                         fix_description: str = "") -> bool:
        """标记修复已应用

        Args:
            trace_id: 失败记录的trace_id
            failure_type: 失败类型（可选）
            fix_description: 修复描述

        Returns:
            是否成功
        """
        self.initialize()
        start_time = time.time()

        try:
            with self._write_lock, self._get_conn() as conn:
                cursor = conn.cursor()
                if failure_type:
                    cursor.execute(
                        """UPDATE failures SET fix_applied = 1,
                           suggested_fix = COALESCE(NULLIF(suggested_fix, ''), ?)
                           WHERE trace_id = ? AND failure_type = ?""",
                        (fix_description, trace_id, failure_type.value)
                    )
                else:
                    cursor.execute(
                        """UPDATE failures SET fix_applied = 1,
                           suggested_fix = COALESCE(NULLIF(suggested_fix, ''), ?)
                           WHERE trace_id = ?""",
                        (fix_description, trace_id)
                    )
                conn.commit()

            duration_ms = (time.time() - start_time) * 1000
            logger.info(json.dumps({
                "trace_id": trace_id,
                "module_name": "failure_analysis",
                "action": "mark_fix_applied",
                "failure_type": failure_type.value if failure_type else "",
                "duration_ms": round(duration_ms, 2),
                "level": "INFO"
            }))
            return True
        except Exception as e:
            logger.error(json.dumps({
                "trace_id": trace_id,
                "module_name": "failure_analysis",
                "action": "mark_fix_applied",
                "error": str(e),
                "duration_ms": 0,
                "level": "ERROR"
            }))
            raise


# 全局失败分析器实例
_global_failure_analyzer = None

def get_failure_analyzer() -> FailureAnalyzer:
    """获取全局失败分析器实例"""
    global _global_failure_analyzer
    if _global_failure_analyzer is None:
        _global_failure_analyzer = FailureAnalyzer()
        _global_failure_analyzer.initialize()
    return _global_failure_analyzer


def report_failure(trace_id: str, message: str, source: str = "", 
                  context: Dict[str, Any] = None, evidence: List[str] = None):
    """便捷函数：报告失败案例"""
    analyzer = get_failure_analyzer()
    
    failure_type = analyzer.classify_failure(message)
    severity = _determine_severity(failure_type)
    suggested_fix = analyzer.generate_fix_suggestion(failure_type)
    
    record = FailureRecord(
        trace_id=trace_id,
        failure_type=failure_type,
        severity=severity,
        message=message,
        source=source,
        context=context or {},
        evidence=evidence or [],
        suggested_fix=suggested_fix
    )
    
    analyzer.record_failure(record)
    return record


def _determine_severity(failure_type: FailureType) -> FailureSeverity:
    """根据失败类型确定严重程度"""
    critical_types = {FailureType.AUTH_ERROR, FailureType.TIMEOUT}
    high_types = {FailureType.API_FICTION, FailureType.LOGIC_ERROR}
    
    if failure_type in critical_types:
        return FailureSeverity.CRITICAL
    elif failure_type in high_types:
        return FailureSeverity.HIGH
    else:
        return FailureSeverity.MEDIUM


__all__ = [
    'FailureType', 'FailureSeverity', 'FailureRecord', 'FailurePattern',
    'FailureAnalyzer', 'get_failure_analyzer', 'report_failure'
]