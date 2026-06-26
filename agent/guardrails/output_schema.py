"""输出 Schema 约束模块

定义统一的 JSON Schema 输出格式，强制所有 LLM 输出必须符合 Schema。
支持输出类型：文本响应、工具调用、错误提示、总结报告。
不符合 Schema 的输出自动触发重试或降级处理。

设计原则：
- 结构化输出：所有 LLM 输出必须遵循预定义的 JSON Schema
- 类型安全：严格的类型检查和验证
- 自动降级：不符合 Schema 的输出触发重试或降级为文本响应
- 可扩展性：支持自定义输出类型
- 容错机制：集成熔断器、限流和优雅降级

输出类型：
1. text_response - 纯文本响应
2. tool_call - 工具调用
3. error_message - 错误提示
4. summary_report - 总结报告
"""

import json
import logging
import time
from enum import Enum
from typing import Dict, Any, Optional, List, Union
from dataclasses import dataclass, field

from agent.monitoring.tracing import get_trace_id
from agent.circuit_breaker import get_circuit_breaker, CircuitBreakerError
from agent.graceful_degrade import get_degrade_manager, DegradeModule

logger = logging.getLogger(__name__)


class OutputType(Enum):
    """输出类型枚举"""
    TEXT_RESPONSE = "text_response"
    TOOL_CALL = "tool_call"
    ERROR_MESSAGE = "error_message"
    SUMMARY_REPORT = "summary_report"


class SchemaValidationError(Exception):
    """Schema 验证异常"""
    
    def __init__(self, message: str, errors: List[str] = None):
        super().__init__(message)
        self.errors = errors or []
        self.error_code = "SCHEMA_VALIDATION_ERROR"


@dataclass
class ToolCall:
    """工具调用结构"""
    tool_name: str
    tool_params: Dict[str, Any]
    thought: Optional[str] = None


@dataclass
class ErrorDetail:
    """错误详情"""
    error_code: str
    message: str
    suggestion: Optional[str] = None


@dataclass
class SummarySection:
    """总结报告章节"""
    title: str
    content: str
    importance: int = 3  # 1-5 重要性级别


@dataclass
class OutputSchema:
    """统一输出 Schema 基类"""
    output_type: OutputType = OutputType.TEXT_RESPONSE
    trace_id: str = field(default_factory=get_trace_id)
    timestamp: float = field(default_factory=lambda: time.time())
    version: str = "1.0"
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        result = {
            "output_type": self.output_type.value,
            "trace_id": self.trace_id,
            "timestamp": self.timestamp,
            "version": self.version,
        }
        return result
    
    def to_json(self) -> str:
        """转换为 JSON 字符串"""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


@dataclass(kw_only=True)
class TextResponse(OutputSchema):
    """文本响应输出"""
    content: str
    confidence: float = 1.0
    source: Optional[str] = None
    
    def __post_init__(self):
        self.output_type = OutputType.TEXT_RESPONSE
    
    def to_dict(self) -> Dict[str, Any]:
        result = super().to_dict()
        result.update({
            "content": self.content,
            "confidence": self.confidence,
            "source": self.source,
        })
        return result


@dataclass(kw_only=True)
class ToolCallOutput(OutputSchema):
    """工具调用输出"""
    tool_calls: List[ToolCall]
    
    def __post_init__(self):
        self.output_type = OutputType.TOOL_CALL
    
    def to_dict(self) -> Dict[str, Any]:
        result = super().to_dict()
        result.update({
            "tool_calls": [
                {
                    "tool_name": tc.tool_name,
                    "tool_params": tc.tool_params,
                    "thought": tc.thought
                } for tc in self.tool_calls
            ]
        })
        return result


@dataclass(kw_only=True)
class ErrorMessage(OutputSchema):
    """错误提示输出"""
    error: ErrorDetail
    retry_count: int = 0
    max_retries: int = 3
    
    def __post_init__(self):
        self.output_type = OutputType.ERROR_MESSAGE
    
    def to_dict(self) -> Dict[str, Any]:
        result = super().to_dict()
        result.update({
            "error": {
                "error_code": self.error.error_code,
                "message": self.error.message,
                "suggestion": self.error.suggestion
            },
            "retry_count": self.retry_count,
            "max_retries": self.max_retries
        })
        return result


@dataclass(kw_only=True)
class SummaryReport(OutputSchema):
    """总结报告输出"""
    title: str
    sections: List[SummarySection]
    conclusion: Optional[str] = None
    action_items: List[str] = field(default_factory=list)
    
    def __post_init__(self):
        self.output_type = OutputType.SUMMARY_REPORT
    
    def to_dict(self) -> Dict[str, Any]:
        result = super().to_dict()
        result.update({
            "title": self.title,
            "sections": [
                {
                    "title": s.title,
                    "content": s.content,
                    "importance": s.importance
                } for s in self.sections
            ],
            "conclusion": self.conclusion,
            "action_items": self.action_items
        })
        return result


class OutputSchemaValidator:
    """输出 Schema 验证器（集成容错机制）"""
    
    def __init__(self, enable_retry: bool = True, max_retries: int = 3):
        self.enable_retry = enable_retry
        self.max_retries = max_retries
        
        # 初始化容错组件
        self._circuit_breaker = get_circuit_breaker("schema_validation")
        self._degrade_manager = get_degrade_manager()
    
    def validate(self, output: Union[str, Dict[str, Any]]) -> bool:
        """验证输出是否符合 Schema
        
        Args:
            output: 输出内容（JSON 字符串或字典）
        
        Returns:
            True 如果验证通过，False 否则
        """
        trace_id = get_trace_id()
        
        try:
            # 解析输出
            if isinstance(output, str):
                output_dict = json.loads(output)
            else:
                output_dict = output
            
            # 验证必需字段
            required_fields = ["output_type", "trace_id", "timestamp", "version"]
            missing_fields = [f for f in required_fields if f not in output_dict]
            
            if missing_fields:
                logger.error(json.dumps({
                    "trace_id": trace_id,
                    "module_name": "output_schema",
                    "action": "validate",
                    "duration_ms": 0,
                    "error": f"缺少必需字段: {missing_fields}"
                }))
                return False
            
            # 验证 output_type
            output_type = output_dict.get("output_type")
            valid_types = [t.value for t in OutputType]
            if output_type not in valid_types:
                logger.error(json.dumps({
                    "trace_id": trace_id,
                    "module_name": "output_schema",
                    "action": "validate",
                    "duration_ms": 0,
                    "error": f"无效的 output_type: {output_type}, 有效值: {valid_types}"
                }))
                return False
            
            # 根据类型验证特定字段
            if not self._validate_by_type(output_type, output_dict):
                return False
            
            logger.info(json.dumps({
                "trace_id": trace_id,
                "module_name": "output_schema",
                "action": "validate",
                "duration_ms": 0,
                "result": "success",
                "output_type": output_type
            }))
            return True
            
        except json.JSONDecodeError as e:
            logger.error(json.dumps({
                "trace_id": trace_id,
                "module_name": "output_schema",
                "action": "validate",
                "duration_ms": 0,
                "error": f"JSON 解析失败: {str(e)}"
            }))
            return False
        except Exception as e:
            logger.error(json.dumps({
                "trace_id": trace_id,
                "module_name": "output_schema",
                "action": "validate",
                "duration_ms": 0,
                "error": f"验证失败: {str(e)}"
            }))
            return False
    
    def _validate_by_type(self, output_type: str, output_dict: Dict[str, Any]) -> bool:
        """根据输出类型进行验证"""
        trace_id = get_trace_id()
        
        if output_type == OutputType.TEXT_RESPONSE.value:
            if "content" not in output_dict:
                logger.error(json.dumps({
                    "trace_id": trace_id,
                    "module_name": "output_schema",
                    "action": "_validate_by_type",
                    "duration_ms": 0,
                    "error": "text_response 缺少 content 字段"
                }))
                return False
            return True
        
        elif output_type == OutputType.TOOL_CALL.value:
            if "tool_calls" not in output_dict:
                logger.error(json.dumps({
                    "trace_id": trace_id,
                    "module_name": "output_schema",
                    "action": "_validate_by_type",
                    "duration_ms": 0,
                    "error": "tool_call 缺少 tool_calls 字段"
                }))
                return False
            
            tool_calls = output_dict["tool_calls"]
            if not isinstance(tool_calls, list) or len(tool_calls) == 0:
                logger.error(json.dumps({
                    "trace_id": trace_id,
                    "module_name": "output_schema",
                    "action": "_validate_by_type",
                    "duration_ms": 0,
                    "error": "tool_calls 必须是非空列表"
                }))
                return False
            
            for i, tc in enumerate(tool_calls):
                if "tool_name" not in tc or "tool_params" not in tc:
                    logger.error(json.dumps({
                        "trace_id": trace_id,
                        "module_name": "output_schema",
                        "action": "_validate_by_type",
                        "duration_ms": 0,
                        "error": f"tool_call[{i}] 缺少 tool_name 或 tool_params"
                    }))
                    return False
            return True
        
        elif output_type == OutputType.ERROR_MESSAGE.value:
            if "error" not in output_dict:
                logger.error(json.dumps({
                    "trace_id": trace_id,
                    "module_name": "output_schema",
                    "action": "_validate_by_type",
                    "duration_ms": 0,
                    "error": "error_message 缺少 error 字段"
                }))
                return False
            
            error = output_dict["error"]
            if "error_code" not in error or "message" not in error:
                logger.error(json.dumps({
                    "trace_id": trace_id,
                    "module_name": "output_schema",
                    "action": "_validate_by_type",
                    "duration_ms": 0,
                    "error": "error 字段缺少 error_code 或 message"
                }))
                return False
            return True
        
        elif output_type == OutputType.SUMMARY_REPORT.value:
            if "title" not in output_dict or "sections" not in output_dict:
                logger.error(json.dumps({
                    "trace_id": trace_id,
                    "module_name": "output_schema",
                    "action": "_validate_by_type",
                    "duration_ms": 0,
                    "error": "summary_report 缺少 title 或 sections 字段"
                }))
                return False
            
            sections = output_dict["sections"]
            if not isinstance(sections, list) or len(sections) == 0:
                logger.error(json.dumps({
                    "trace_id": trace_id,
                    "module_name": "output_schema",
                    "action": "_validate_by_type",
                    "duration_ms": 0,
                    "error": "sections 必须是非空列表"
                }))
                return False
            
            for i, section in enumerate(sections):
                if "title" not in section or "content" not in section:
                    logger.error(json.dumps({
                        "trace_id": trace_id,
                        "module_name": "output_schema",
                        "action": "_validate_by_type",
                        "duration_ms": 0,
                        "error": f"section[{i}] 缺少 title 或 content"
                    }))
                    return False
            return True
        
        return True
    
    def parse_and_validate(self, output: Union[str, Dict[str, Any]]) -> OutputSchema:
        """解析并验证输出，返回对应的 OutputSchema 对象（集成熔断器和优雅降级）
        
        Args:
            output: 输出内容（JSON 字符串或字典）
        
        Returns:
            OutputSchema 对象，如果验证失败则返回错误提示对象或降级后的纯文本响应
        
        Raises:
            SchemaValidationError: 当验证失败且不允许重试时
        """
        trace_id = get_trace_id()
        start_time = time.time()
        
        # 检查熔断器状态
        try:
            if not self._circuit_breaker.allow_request():
                logger.warning(json.dumps({
                    "trace_id": trace_id,
                    "module_name": "output_schema",
                    "action": "parse_and_validate",
                    "duration_ms": 0,
                    "status": "blocked",
                    "reason": "Schema 校验熔断器已打开"
                }))
                
                # 熔断器打开时使用降级机制
                degrade_result = self._degrade_manager.schema_validate_with_degrade(
                    output if isinstance(output, dict) else {},
                    {}
                )
                if degrade_result.get("valid", False):
                    return TextResponse(
                        content=str(output) if isinstance(output, str) else "Schema 校验已降级",
                        confidence=0.7,
                        source="degraded"
                    )
                return ErrorMessage(
                    error=ErrorDetail(
                        error_code="SCHEMA_VALIDATION_CIRCUIT_BROKEN",
                        message="Schema 校验服务暂时不可用",
                        suggestion="服务正在恢复中，请稍后重试"
                    )
                )
        except CircuitBreakerError as e:
            logger.warning(json.dumps({
                "trace_id": trace_id,
                "module_name": "output_schema",
                "action": "parse_and_validate",
                "duration_ms": 0,
                "error": str(e)
            }))
        
        # 使用优雅降级进行 Schema 校验
        def validate_func():
            if not self.validate(output):
                raise SchemaValidationError("输出不符合 Schema")
            return output
        
        def lenient_fallback():
            """宽松校验降级：接受纯文本输出"""
            return {
                "valid": True,
                "degrade_level": "lenient",
                "content": str(output) if isinstance(output, str) else None
            }
        
        def text_only_fallback():
            """纯文本模式降级：跳过所有校验"""
            return {
                "valid": True,
                "degrade_level": "text_only",
                "content": str(output) if isinstance(output, str) else "文本响应"
            }
        
        try:
            result = self._degrade_manager.with_degrade(
                module=DegradeModule.SCHEMA,
                func=validate_func,
                fallback=lambda: lenient_fallback()
            )
        except Exception as e:
            # 校验完全失败，降级到纯文本模式
            result = text_only_fallback()
        
        # 根据降级结果处理
        if isinstance(result, dict) and result.get("degrade_level"):
            self._circuit_breaker.record_failure()
            
            if result.get("content"):
                return TextResponse(
                    content=result["content"],
                    confidence=0.6 if result["degrade_level"] == "lenient" else 0.4,
                    source=f"degraded_{result['degrade_level']}"
                )
            return ErrorMessage(
                error=ErrorDetail(
                    error_code="SCHEMA_VALIDATION_DEGRADED",
                    message="Schema 校验已降级",
                    suggestion="输出格式可能不符合预期"
                )
            )
        
        # 验证通过，记录成功
        self._circuit_breaker.record_success()
        
        # 解析为具体类型
        if isinstance(output, str):
            output_dict = json.loads(output)
        else:
            output_dict = output
        
        duration_ms = (time.time() - start_time) * 1000
        
        output_type = output_dict["output_type"]
        if output_type == OutputType.TEXT_RESPONSE.value:
            result = TextResponse(
                content=output_dict["content"],
                confidence=output_dict.get("confidence", 1.0),
                source=output_dict.get("source")
            )
        
        elif output_type == OutputType.TOOL_CALL.value:
            tool_calls = [
                ToolCall(
                    tool_name=tc["tool_name"],
                    tool_params=tc["tool_params"],
                    thought=tc.get("thought")
                ) for tc in output_dict["tool_calls"]
            ]
            result = ToolCallOutput(tool_calls=tool_calls)
        
        elif output_type == OutputType.ERROR_MESSAGE.value:
            error = output_dict["error"]
            result = ErrorMessage(
                error=ErrorDetail(
                    error_code=error["error_code"],
                    message=error["message"],
                    suggestion=error.get("suggestion")
                ),
                retry_count=output_dict.get("retry_count", 0),
                max_retries=output_dict.get("max_retries", 3)
            )
        
        elif output_type == OutputType.SUMMARY_REPORT.value:
            sections = [
                SummarySection(
                    title=s["title"],
                    content=s["content"],
                    importance=s.get("importance", 3)
                ) for s in output_dict["sections"]
            ]
            result = SummaryReport(
                title=output_dict["title"],
                sections=sections,
                conclusion=output_dict.get("conclusion"),
                action_items=output_dict.get("action_items", [])
            )
        
        else:
            result = ErrorMessage(
                error=ErrorDetail(
                    error_code="UNKNOWN_OUTPUT_TYPE",
                    message=f"未知的输出类型: {output_type}"
                )
            )
        
        logger.info(json.dumps({
            "trace_id": trace_id,
            "module_name": "output_schema",
            "action": "parse_and_validate",
            "duration_ms": round(duration_ms, 2),
            "result": "success",
            "output_type": output_type
        }))
        
        return result


class OutputSchemaBuilder:
    """输出 Schema 构建器"""
    
    @staticmethod
    def text(content: str, confidence: float = 1.0, source: Optional[str] = None) -> TextResponse:
        """创建文本响应"""
        return TextResponse(content=content, confidence=confidence, source=source)
    
    @staticmethod
    def tool_call(tool_name: str, tool_params: Dict[str, Any], thought: Optional[str] = None) -> ToolCallOutput:
        """创建工具调用输出"""
        return ToolCallOutput(tool_calls=[ToolCall(tool_name, tool_params, thought)])
    
    @staticmethod
    def tool_calls(tool_calls: List[Dict[str, Any]]) -> ToolCallOutput:
        """创建多个工具调用输出"""
        calls = [
            ToolCall(
                tool_name=tc["tool_name"],
                tool_params=tc["tool_params"],
                thought=tc.get("thought")
            ) for tc in tool_calls
        ]
        return ToolCallOutput(tool_calls=calls)
    
    @staticmethod
    def error(error_code: str, message: str, suggestion: Optional[str] = None) -> ErrorMessage:
        """创建错误提示输出"""
        return ErrorMessage(error=ErrorDetail(error_code, message, suggestion))
    
    @staticmethod
    def summary(title: str, sections: List[Dict[str, Any]], 
                conclusion: Optional[str] = None, action_items: List[str] = None) -> SummaryReport:
        """创建总结报告输出"""
        summary_sections = [
            SummarySection(
                title=s["title"],
                content=s["content"],
                importance=s.get("importance", 3)
            ) for s in sections
        ]
        return SummaryReport(
            title=title,
            sections=summary_sections,
            conclusion=conclusion,
            action_items=action_items or []
        )