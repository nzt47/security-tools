"""知识沉淀模块——记忆压缩与去重

将冗长的对话历史和任务结果提炼为结构化摘要，
剔除冗余信息，写入持久化记忆。
设计思想：设计文档 4.3（发现与学习）

架构说明：
- 当前为基于规则的关键信息提取（零 Token 消耗）
- 后续可引入 LLM 驱动的高质量摘要生成
- 通过 MemoryRouter 写入持久化记忆

沉淀条件：
- 跳过低价值交互（问候、帮助等）
- 至少提取出 1 个关键事实
- 置信度 >= 0.5 的记录才写持久化
"""
import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class KnowledgeRecord:
    """知识沉淀记录

    Attributes:
        task_type: 任务类型（chat, execute_shell, write_file 等）
        summary: 结构化摘要
        key_facts: 提取的关键事实列表
        entities: 涉及的外部实体（邮箱、URL 等）
        confidence: 置信度 0.0~1.0
        timestamp: 记录时间戳
    """
    task_type: str
    summary: str
    key_facts: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    confidence: float = 0.0
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


class KnowledgePrecipitator:
    """知识沉淀器——基于规则的关键信息提取

    从交互中提取有价值的碎片信息，沉淀为结构化记忆。
    支持通过 MemoryRouter 将沉淀结果写入持久化存储。

    Usage:
        precipitator = KnowledgePrecipitator(memory_router)
        record = precipitator.precipitate("chat", "帮我查天气", "今天晴转多云...")
        if record:
            print("沉淀了", len(record.key_facts), "个关键事实")
    """

    # 低价值模式——不需要沉淀的交互类型
    SKIP_PATTERNS: set[str] = {
        "hello", "hi", "你好", "help", "帮助",
        "check_time", "check_date", "check_health",
        "goodbye", "再见", "bye", "thanks", "谢谢",
    }

    # 包含要点/决策的信号词
    SIGNAL_WORDS: set[str] = {
        "设置", "配置", "修改", "创建", "删除",
        "记住", "保存", "记录", "通知",
        "打开", "关闭", "启用", "禁用",
        "安装", "部署", "上传", "下载",
    }

    def __init__(self, memory_router=None):
        """初始化

        Args:
            memory_router: 可选的 MemoryRouter 实例，
                           用于将沉淀结果写入持久化记忆
        """
        self._memory_router = memory_router

    def precipitate(self,
                    task_type: str,
                    input_text: str,
                    output: str,
                    trace_id: str = "") -> KnowledgeRecord | None:
        """执行知识沉淀——从交互中提取有价值信息

        Args:
            task_type: 任务类型
            input_text: 用户输入
            output: 系统输出
            trace_id: 可选的追踪 ID

        Returns:
            KnowledgeRecord 或 None（无有价值信息时）
        """
        # 跳过低价值交互
        if task_type in self.SKIP_PATTERNS:
            return None

        # 提取关键事实
        key_facts = self._extract_facts(input_text, output)
        if not key_facts:
            return None

        # 提取外部实体
        entities = self._extract_entities(input_text, output)

        # 计算置信度
        confidence = self._calculate_confidence(key_facts, entities, input_text, output)

        record = KnowledgeRecord(
            task_type=task_type,
            summary=self._generate_summary(input_text, output),
            key_facts=key_facts,
            entities=entities,
            confidence=confidence,
        )

        logger.info("[Cognitive] 知识沉淀: type=%s, facts=%d, entities=%d, confidence=%.2f",
                    task_type, len(key_facts), len(entities), confidence)

        # 高置信度记录写入持久化记忆
        if self._memory_router and confidence >= 0.5:
            asyncio.create_task(self._persist(record, trace_id))

        return record

    def _extract_facts(self, input_text: str, output: str) -> list[str]:
        """提取关键事实

        从输入输出中识别包含数字、日期、配置决策的信息。

        Args:
            input_text: 用户输入
            output: 系统输出

        Returns:
            关键事实列表
        """
        facts = []

        # 1. 数值类信息
        numbers = re.findall(r'\d+', output)
        if numbers:
            # 过滤掉明显不是有意义数字的（如单个数字）
            meaningful = [n for n in numbers if len(n) >= 2 or n in ("0", "1")]
            if meaningful:
                facts.append("涉及数值: %s" % ", ".join(meaningful[:5]))

        # 2. 文件路径信息
        paths = re.findall(r'[\w/\\]+\.\w+', output)
        if paths:
            facts.append("涉及文件: %s" % ", ".join(paths[:3]))

        # 3. 配置/设置类信息（包含信号词）
        for word in self.SIGNAL_WORDS:
            if word in input_text or word in output:
                # 提取包含该词的上下文句子
                for text in (input_text, output):
                    for sentence in text.split("。"):
                        if word in sentence:
                            snippet = sentence.strip()[:60]
                            if snippet:
                                facts.append("操作记录: %s" % snippet)
                                break
                    if len(facts) > 3:
                        break

        # 去重
        seen = set()
        unique_facts = []
        for f in facts:
            if f not in seen:
                seen.add(f)
                unique_facts.append(f)

        return unique_facts[:10]  # 最多返回 10 条

    def _extract_entities(self, input_text: str, output: str) -> list[str]:
        """提取外部实体

        Args:
            input_text: 用户输入
            output: 系统输出

        Returns:
            实体列表（邮箱、URL 等）
        """
        entities = []

        # 邮箱地址
        emails = re.findall(r'[\w.+-]+@[\w-]+\.[\w.-]+', output)
        entities.extend(emails)

        # URL
        urls = re.findall(r'https?://[^\s)]+', output)
        entities.extend(urls)

        # IP 地址
        ips = re.findall(r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}', output)
        entities.extend(ips)

        return entities[:10]

    def _generate_summary(self, input_text: str, output: str) -> str:
        """生成结构化摘要

        将交互提炼为简洁的结构化摘要。

        Args:
            input_text: 用户输入
            output: 系统输出

        Returns:
            摘要文本
        """
        input_short = input_text[:100].replace("\n", " ")
        output_short = output[:200].replace("\n", " ")
        return "任务: %s → 结果: %s" % (input_short, output_short)

    def _calculate_confidence(self, key_facts: list[str],
                               entities: list[str],
                               input_text: str,
                               output: str) -> float:
        """计算知识置信度

        基于规则估算提取信息的可信程度。

        Args:
            key_facts: 提取的关键事实
            entities: 提取的外部实体
            input_text: 用户输入
            output: 系统输出

        Returns:
            置信度 0.0~1.0
        """
        confidence = 0.5  # 基础值

        # 有数字/数值信息 → 高置信度
        if re.search(r'\d{2,}', output):
            confidence += 0.1

        # 有文件路径 → 高置信度
        if re.search(r'[\w/\\]+\.\w+', output):
            confidence += 0.1

        # 有实体 → 更高置信度
        if entities:
            confidence += 0.1

        # 输出足够长 → 信息充分
        if len(output) > 100:
            confidence += 0.1

        # 包含错误信息 → 降低置信度
        if "错误" in output or "失败" in output:
            confidence -= 0.2

        # 包含不确定表述 → 降低置信度
        if "可能" in output or "大概" in output or "maybe" in output.lower():
            confidence -= 0.1

        return max(0.0, min(1.0, confidence))

    async def _persist(self, record: KnowledgeRecord, trace_id: str):
        """将知识记录持久化到记忆系统

        Args:
            record: 知识记录
            trace_id: 追踪 ID
        """
        if not self._memory_router:
            return

        try:
            key = "knowledge_%s_%s" % (record.timestamp, record.task_type)
            await self._memory_router.save(
                key,
                {
                    "type": "cognitive_knowledge",
                    "task_type": record.task_type,
                    "summary": record.summary,
                    "key_facts": record.key_facts,
                    "entities": record.entities,
                    "confidence": record.confidence,
                    "timestamp": record.timestamp,
                    "trace_id": trace_id,
                },
                task_type="fact_extraction",
            )
            logger.info("[Cognitive] 知识沉淀已持久化: %s", key)
        except Exception as e:
            logger.warning("[Cognitive] 知识沉淀持久化失败: %s", e)
