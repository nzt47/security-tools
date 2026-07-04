"""记忆 → 技能自动抽象器

核心算法:
    1. 多源记忆归一化: feedback / workflow / long_term_memory → MemoryEntry
    2. 聚类: 基于 Jaccard 相似度的贪心聚类
    3. 模式提取: 从聚类中找出公共工具调用 / 参数 / 标签
    4. 技能草稿生成: 构造符合 Skill.from_storage_dict 的 dict
    5. 质量门控: 聚类大小 / 成功率 / 与已有技能相似度
    6. 注册: 通过 SkillsMgmtService.create_manual + review

设计原则:
    - 边界显性化: 质量门控不通过 → 不注册, 返回 draft 供人工审核
    - 可观测: 每个抽象步骤输出结构化日志
    - 幂等: 同一记忆聚类产生的 skill_id 由 cluster 哈希决定, 重复调用不会创建重复技能
    - 安全: 默认 auto_register=False, 只返回草稿

P0 草稿结构化 (方法论落地):
    - root_cause_hypothesis: 5Whys 浅层自动化 (成功/失败参数对比)
    - trigger_conditions: 触发条件
    - execution_steps: Checklist 执行步骤
    - if_then_rules: If-Then-Else 结构化规则
    - anti_patterns: 反例边界 (不适用场景)
"""

from __future__ import annotations
import hashlib
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .observability import logger, emit_metric, track_event, traced_action


# ──────────────────────────────────────────────
# 归一化记忆条目
# ──────────────────────────────────────────────

@dataclass
class MemoryEntry:
    """归一化的记忆条目 — 跨数据源的统一抽象

    Attributes:
        source: 来源标识 (feedback / workflow / long_term_memory / session)
        source_id: 原始记录 ID
        task_text: 任务描述 (用户输入或 feedback 评论)
        success: 是否成功 (workflow.success / feedback LIKE)
        tool_calls: 工具调用列表 [{name, params, output}]
        params: 提取的参数 dict
        tags: 标签
        timestamp: ISO 时间戳
        session_id: 会话 ID (用于关联)
        signal_strength: 信号强度 (0.0-1.0), 由 SignalScorer 填充
            低于 threshold 的记忆会被过滤, 不进入聚类
    """
    source: str
    source_id: str
    task_text: str
    success: bool = True
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    params: Dict[str, Any] = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)
    timestamp: str = ""
    session_id: str = ""
    signal_strength: float = 0.0


# ──────────────────────────────────────────────
# 聚类
# ──────────────────────────────────────────────

@dataclass
class MemoryCluster:
    """记忆聚类 — 一组相似的记忆条目

    结构化字段 (P0 草稿结构化方法论):
        root_cause_hypothesis: 根因假设 — "为什么有效/无效" (5Whys 浅层自动化)
        trigger_conditions: 触发条件 — "什么场景下应用"
        execution_steps: 执行步骤 — Checklist 形式
        if_then_rules: If-Then-Else 规则 — 结构化表达
        anti_patterns: 反例边界 — "什么时候不用这个技能"
    """
    cluster_id: str
    entries: List[MemoryEntry] = field(default_factory=list)
    representative_text: str = ""
    common_tool_names: List[str] = field(default_factory=list)
    common_params: Dict[str, Any] = field(default_factory=dict)
    common_tags: List[str] = field(default_factory=list)
    success_rate: float = 0.0
    avg_text_length: float = 0.0
    # P0 结构化字段
    root_cause_hypothesis: str = ""
    trigger_conditions: List[str] = field(default_factory=list)
    execution_steps: List[str] = field(default_factory=list)
    if_then_rules: List[str] = field(default_factory=list)
    anti_patterns: List[str] = field(default_factory=list)

    @property
    def size(self) -> int:
        return len(self.entries)

    @property
    def success_count(self) -> int:
        return sum(1 for e in self.entries if e.success)

    @property
    def failure_count(self) -> int:
        return sum(1 for e in self.entries if not e.success)


_TOKEN_RE = re.compile(r"[a-zA-Z0-9_]+")


def _tokenize(text: str) -> set:
    """简单分词 (与 reviewer._tokenize 一致, 复用算法而非导入)"""
    if not text:
        return set()
    tokens = set(_TOKEN_RE.findall(text.lower()))
    for ch in text:
        if "\u4e00" <= ch <= "\u9fff":
            tokens.add(ch)
    return tokens


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _slugify(text: str, max_len: int = 30) -> str:
    """把任务文本转换为 kebab-case skill_id 片段"""
    # 取前若干词, 转小写, 非字母数字转 -
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
    s = re.sub(r"-+", "-", s)[:max_len].rstrip("-")
    return s or "skill"


# ──────────────────────────────────────────────
# 主算法
# ──────────────────────────────────────────────

class MemorySkillAbstractor:
    """记忆 → 技能自动抽象器"""

    # 质量门控阈值
    MIN_CLUSTER_SIZE = 3          # 聚类至少 3 个条目
    MIN_SUCCESS_RATE = 0.7        # 聚类成功率至少 70%
    MAX_EXISTING_DUP_JACCARD = 0.7  # 与已有技能 Jaccard 超过此值 → 不创建
    MAX_EXECUTION_STEPS = 10      # 执行步骤上限 (最小可行性规则: 超过则警告简化)
    CLUSTER_JACCARD_THRESHOLD = 0.5  # 聚类合并阈值
    TOOL_FREQUENCY_THRESHOLD = 0.5   # 工具调用频率超过 50% 才纳入 common_tool_names
    # 信号评分阈值: 低于此值的记忆不进入聚类
    SIGNAL_FILTER_THRESHOLD = 0.4
    # 平均信号强度软警告阈值: 聚类平均信号强度低于此值则警告 (不阻止)
    SIGNAL_WARN_AVG = 0.3

    def __init__(self, *,
                 skills_service: Optional[Any] = None,
                 min_cluster_size: int = MIN_CLUSTER_SIZE,
                 min_success_rate: float = MIN_SUCCESS_RATE,
                 max_existing_dup_jaccard: float = MAX_EXISTING_DUP_JACCARD,
                 cluster_jaccard: float = CLUSTER_JACCARD_THRESHOLD,
                 signal_filter_threshold: float = SIGNAL_FILTER_THRESHOLD,
                 enable_signal_scoring: bool = True):
        """初始化抽象器

        Args:
            skills_service: SkillsMgmtService 实例 (None 时延迟导入)
            min_cluster_size: 聚类最小大小 (质量门控)
            min_success_rate: 聚类最小成功率 (质量门控)
            max_existing_dup_jaccard: 与已有技能的最大 Jaccard (超过则跳过)
            cluster_jaccard: 聚类合并的 Jaccard 阈值
            signal_filter_threshold: 信号强度过滤阈值 (低于此值不聚类)
            enable_signal_scoring: 是否启用信号评分 (False 则不过滤)
        """
        self._skills_service = skills_service
        self.min_cluster_size = min_cluster_size
        self.min_success_rate = min_success_rate
        self.max_existing_dup_jaccard = max_existing_dup_jaccard
        self.cluster_jaccard = cluster_jaccard
        self.signal_filter_threshold = signal_filter_threshold
        self.enable_signal_scoring = enable_signal_scoring

    def _resolve_skills_service(self):
        if self._skills_service is not None:
            return self._skills_service
        try:
            from agent.state_manager import get_skills_mgmt_service
            return get_skills_mgmt_service()
        except Exception:
            from .service import SkillsMgmtService
            return SkillsMgmtService()

    # ─── 主入口 ───

    def abstract_new_skills(self, *,
                             memory_entries: Optional[List[MemoryEntry]] = None,
                             days: int = 30,
                             max_skills: int = 5,
                             auto_register: bool = False,
                             ) -> List[Dict[str, Any]]:
        """从记忆条目中抽象新技能

        Args:
            memory_entries: 预加载的记忆条目 (None 时自动从各源拉取最近 days 天)
            days: 自动拉取的天数范围
            max_skills: 最多生成多少个技能草稿
            auto_register: True 时自动调用 create_manual 注册; False 只返回草稿

        Returns:
            草稿列表, 每项形如:
                {
                    cluster_id, cluster_size, success_rate,
                    common_tool_names, common_tags,
                    draft_skill_id, draft_name, draft_description,
                    draft_content_preview,
                    quality_gate_passed: bool,
                    quality_gate_reasons: List[str],
                    registered: bool (auto_register=False 时为 False),
                    skill_id: str | None (注册成功后返回),
                    duplicate_of: str | None (与已有技能过于相似),
                }
        """
        with traced_action("memory_abstract_skills",
                           days=days, max_skills=max_skills,
                           auto_register=auto_register):
            # 1. 加载记忆条目
            if memory_entries is None:
                memory_entries = self._load_recent_memories(days=days)

            logger.info(
                "[MemAbstract] 开始抽象 | 输入记忆=%d 条 | "
                "min_cluster=%d | min_success=%.2f",
                len(memory_entries), self.min_cluster_size,
                self.min_success_rate,
            )

            if len(memory_entries) < self.min_cluster_size:
                logger.info(
                    "[MemAbstract] 记忆条目不足 (%d < %d), 跳过",
                    len(memory_entries), self.min_cluster_size,
                )
                return []

            # 1.5 信号评分 + 过滤低价值信号 (可禁用)
            if self.enable_signal_scoring:
                memory_entries = self._score_and_filter_signals(memory_entries)
                if len(memory_entries) < self.min_cluster_size:
                    logger.info(
                        "[MemAbstract] 信号过滤后记忆不足 (%d < %d), 跳过",
                        len(memory_entries), self.min_cluster_size,
                    )
                    return []

            # 2. 聚类
            clusters = self.cluster_memories(memory_entries)
            logger.info(
                "[MemAbstract] 聚类完成 | 共 %d 个聚类", len(clusters),
            )

            # 3. 提取模式 + 生成草稿 + 质量门控
            results: List[Dict[str, Any]] = []
            for cluster in clusters:
                if len(results) >= max_skills:
                    break
                result = self._process_cluster(
                    cluster, auto_register=auto_register,
                )
                results.append(result)

            # 4. 排序: 质量门控通过的优先, 再按 cluster_size 降序,
            #          再按平均信号强度降序
            results.sort(
                key=lambda r: (
                    not r["quality_gate_passed"],
                    -r["cluster_size"],
                    -r.get("avg_signal_strength", 0.0),
                ),
            )

            summary = {
                "total_clusters": len(clusters),
                "passed": sum(1 for r in results if r["quality_gate_passed"]),
                "registered": sum(1 for r in results if r.get("registered")),
            }
            logger.info("[MemAbstract] 抽象完成 | %s", summary)
            emit_metric("yunshu_memory_abstract_total",
                        value=len(results), kind="counter",
                        labels={"auto_register": str(auto_register)})
            return results

    # ─── 信号评分与过滤 ───

    def _score_and_filter_signals(self,
                                    entries: List[MemoryEntry],
                                    ) -> List[MemoryEntry]:
        """对记忆条目进行信号评分, 并过滤低价值信号

        评分流程:
            1. 加载已有技能 (用于 novelty 维度)
            2. 调用 SignalScorer 给每条记忆打分
            3. 过滤 signal_strength < threshold 的记忆

        降级策略:
            - 无 comment/rating 的 feedback → emotion 降级为中性, 权重重分配
            - 无已有技能 → novelty 满分
        """
        from .signal_scorer import SignalScorer

        scorer = SignalScorer(
            filter_threshold=self.signal_filter_threshold,
        )
        # 加载已有技能 (用于 novelty 维度)
        try:
            svc = self._resolve_skills_service()
            existing_skills = svc.list_all()
        except Exception as e:  # noqa: BLE001
            logger.warning("[MemAbstract] 加载已有技能失败, novelty 将满分: %s", e)
            existing_skills = []

        # 评分
        high_value_count_before = 0
        for entry in entries:
            total, breakdown = scorer.score(
                entry, entries, existing_skills,
            )
            entry.signal_strength = total
            if total >= self.signal_filter_threshold:
                high_value_count_before += 1

        # 过滤
        filtered = scorer.filter_high_value(
            entries, threshold=self.signal_filter_threshold,
        )
        logger.info(
            "[MemAbstract] 信号评分完成 | 输入=%d | 高价值=%d | "
            "保留=%d | threshold=%.2f",
            len(entries), high_value_count_before,
            len(filtered), self.signal_filter_threshold,
        )
        return filtered

    # ─── 记忆加载 ───

    def _load_recent_memories(self, *, days: int = 30) -> List[MemoryEntry]:
        """从各数据源加载最近 N 天的记忆条目

        数据源:
            - workflow: 工作流执行记录 (success/tool_calls/params)
            - feedback: 用户反馈 (LIKE → success, comment → task_text)
            - long_term_memory: 长期记忆库 (可选)

        失败降级: 任一数据源不可用不影响其他源
        """
        entries: List[MemoryEntry] = []
        # 工作流记录
        try:
            entries.extend(self._load_workflow_memories(days=days))
        except Exception as e:  # noqa: BLE001
            logger.warning("[MemAbstract] 加载 workflow 记忆失败: %s", e)
        # 反馈记录
        try:
            entries.extend(self._load_feedback_memories(days=days))
        except Exception as e:  # noqa: BLE001
            logger.warning("[MemAbstract] 加载 feedback 记忆失败: %s", e)
        # 长期记忆
        try:
            entries.extend(self._load_long_term_memories(days=days))
        except Exception as e:  # noqa: BLE001
            logger.warning("[MemAbstract] 加载 long_term_memory 失败: %s", e)

        logger.info("[MemAbstract] 记忆加载完成 | 共 %d 条", len(entries))
        return entries

    def _load_workflow_memories(self, *, days: int) -> List[MemoryEntry]:
        """从工作流执行记录加载"""
        try:
            from agent.workflow_learning.service import WorkflowLearningService
        except Exception:
            return []
        svc = WorkflowLearningService()
        cutoff = self._cutoff_ts(days)
        entries: List[MemoryEntry] = []
        for wf in svc.list_recent(limit=500):
            ts = wf.get("created_at", "")
            if ts and self._parse_ts(ts) < cutoff:
                continue
            entries.append(MemoryEntry(
                source="workflow",
                source_id=str(wf.get("id", "")),
                task_text=wf.get("task_text") or wf.get("intent", ""),
                success=bool(wf.get("success", True)),
                tool_calls=wf.get("tool_calls", []),
                params=wf.get("params", {}),
                tags=wf.get("tags", []),
                timestamp=ts,
                session_id=wf.get("session_id", ""),
            ))
        return entries

    def _load_feedback_memories(self, *, days: int) -> List[MemoryEntry]:
        """从用户反馈加载"""
        try:
            from agent.feedback_collector import FeedbackCollector
        except Exception:
            return []
        collector = FeedbackCollector()
        cutoff = self._cutoff_ts(days)
        entries: List[MemoryEntry] = []
        for fb in collector.list_recent(limit=500):
            ts = fb.get("timestamp", "")
            if ts and self._parse_ts(ts) < cutoff:
                continue
            entries.append(MemoryEntry(
                source="feedback",
                source_id=str(fb.get("id", "")),
                task_text=fb.get("comment") or fb.get("task_text", ""),
                success=fb.get("rating", 0) > 0,
                tool_calls=[],
                params={},
                tags=fb.get("tags", []),
                timestamp=ts,
                session_id=fb.get("session_id", ""),
            ))
        return entries

    def _load_long_term_memories(self, *, days: int) -> List[MemoryEntry]:
        """从长期记忆库加载"""
        try:
            from agent.memory_optimized import MemoryManager
        except Exception:
            return []
        mgr = MemoryManager()
        cutoff = self._cutoff_ts(days)
        entries: List[MemoryEntry] = []
        for mem in mgr.list_recent(limit=200):
            ts = mem.get("timestamp", "")
            if ts and self._parse_ts(ts) < cutoff:
                continue
            entries.append(MemoryEntry(
                source="long_term_memory",
                source_id=str(mem.get("id", "")),
                task_text=mem.get("content") or mem.get("summary", ""),
                success=bool(mem.get("success", True)),
                tool_calls=mem.get("tool_calls", []),
                params=mem.get("params", {}),
                tags=mem.get("tags", []),
                timestamp=ts,
                session_id=mem.get("session_id", ""),
            ))
        return entries

    @staticmethod
    def _cutoff_ts(days: int) -> float:
        import time
        return time.time() - days * 86400

    @staticmethod
    def _parse_ts(ts: str) -> float:
        """解析 ISO 时间戳为 epoch 秒"""
        if not ts:
            return 0.0
        try:
            from datetime import datetime
            # 支持 ISO 8601 / 常见格式
            for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S",
                        "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%SZ"):
                try:
                    return datetime.strptime(ts[:26], fmt).timestamp()
                except ValueError:
                    continue
            return 0.0
        except Exception:
            return 0.0

    # ─── 聚类 ───

    def cluster_memories(self, entries: List[MemoryEntry]) -> List[MemoryCluster]:
        """基于 Jaccard 相似度的贪心聚类

        算法:
            1. 对每条记忆分词
            2. 按 task_text 长度降序排 (长文本优先做聚类中心)
            3. 贪心: 每条记忆与已有聚类的代表文本比较,
               Jaccard >= 阈值则合并, 否则新建聚类
            4. 聚类内按 size 降序

        复杂度: O(n^2 * m), m 为平均 token 数
        """
        if not entries:
            return []

        # 预分词
        tokenized: List[Tuple[MemoryEntry, set]] = [
            (e, _tokenize(e.task_text)) for e in entries
        ]
        # 按文本长度降序 (长文本优先做聚类中心)
        tokenized.sort(key=lambda x: len(x[1]), reverse=True)

        clusters: List[MemoryCluster] = []
        for entry, tokens in tokenized:
            merged = False
            for cluster in clusters:
                rep_tokens = _tokenize(cluster.representative_text)
                if _jaccard(tokens, rep_tokens) >= self.cluster_jaccard:
                    cluster.entries.append(entry)
                    merged = True
                    break
            if not merged:
                # 新聚类
                cluster_id = self._make_cluster_id(entry, tokens)
                clusters.append(MemoryCluster(
                    cluster_id=cluster_id,
                    entries=[entry],
                    representative_text=entry.task_text,
                ))

        # 构建每个聚类的统计字段
        clusters = [self._build_cluster(c) for c in clusters]
        # 按 size 降序
        clusters.sort(key=lambda c: c.size, reverse=True)

        logger.info(
            "[MemAbstract] cluster_memories: 输入=%d | 聚类数=%d | "
            "大小分布=%s",
            len(entries), len(clusters),
            [c.size for c in clusters],
        )
        return clusters

    @staticmethod
    def _make_cluster_id(entry: MemoryEntry, tokens: set) -> str:
        """生成稳定的 cluster_id: 基于首条条目的 source_id + token hash"""
        raw = f"{entry.source}:{entry.source_id}:{sorted(tokens)[:10]}"
        h = hashlib.md5(raw.encode("utf-8")).hexdigest()[:10]
        return f"cl-{h}"

    def _build_cluster(self, cluster: MemoryCluster) -> MemoryCluster:
        """填充 cluster 的统计字段 + P0 结构化字段"""
        entries = cluster.entries
        if not entries:
            return cluster

        # 代表文本: 取最长的
        representative = max(entries, key=lambda e: len(e.task_text))
        cluster.representative_text = representative.task_text

        # 1. common_tool_names: 频率 >= TOOL_FREQUENCY_THRESHOLD
        tool_counter: Counter = Counter()
        for e in entries:
            for tc in e.tool_calls:
                name = tc.get("name") if isinstance(tc, dict) else str(tc)
                if name:
                    tool_counter[name] += 1
        threshold = self.TOOL_FREQUENCY_THRESHOLD * len(entries)
        cluster.common_tool_names = sorted([
            t for t, c in tool_counter.items() if c >= threshold
        ])

        # 2. common_params: 所有条目都有的键的众数值
        cluster.common_params = self._extract_common_params(entries)

        # 3. common_tags: 出现 >= 2 次
        tag_counter: Counter = Counter()
        for e in entries:
            tag_counter.update(e.tags)
        cluster.common_tags = sorted([
            t for t, c in tag_counter.items() if c >= 2
        ])

        # 4. 成功率
        success_count = sum(1 for e in entries if e.success)
        cluster.success_rate = success_count / len(entries) if entries else 0.0

        # 5. 平均文本长度
        cluster.avg_text_length = (
            sum(len(e.task_text) for e in entries) / len(entries)
        )

        # P0 结构化字段提取
        success_entries = [e for e in entries if e.success]
        failure_entries = [e for e in entries if not e.success]
        root_cause = self._extract_root_cause(
            entries, cluster.common_tool_names, cluster.common_params,
            cluster.success_rate, representative.task_text,
        )
        triggers = self._extract_trigger_conditions(
            cluster.common_tags, cluster.common_params, representative.task_text,
        )
        steps = self._extract_execution_steps(
            cluster.common_tool_names, cluster.common_params,
        )
        if_then = self._extract_if_then_rules(
            cluster.common_params, success_entries, failure_entries,
            cluster.common_tool_names,
        )
        anti = self._extract_anti_patterns(
            failure_entries, success_entries, representative.task_text,
        )

        cluster.root_cause_hypothesis = root_cause
        cluster.trigger_conditions = triggers
        cluster.execution_steps = steps
        cluster.if_then_rules = if_then
        cluster.anti_patterns = anti

        logger.info(
            "[MemAbstract] _build_cluster: cluster_id=%s | size=%d | "
            "success_rate=%.2f | tools=%s | root_cause_len=%d | "
            "triggers=%d | steps=%d | rules=%d | anti=%d",
            cluster.cluster_id, cluster.size, cluster.success_rate,
            cluster.common_tool_names, len(root_cause),
            len(triggers), len(steps), len(if_then), len(anti),
        )
        return cluster

    @staticmethod
    def _extract_common_params(entries: List[MemoryEntry]) -> Dict[str, Any]:
        """提取公共参数: 所有条目都有的键 → 众数值"""
        if not entries:
            return {}
        # 找出所有条目都有的键
        common_keys = set(entries[0].params.keys())
        for e in entries[1:]:
            common_keys &= set(e.params.keys())
        if not common_keys:
            return {}
        # 对每个公共键取众数值
        result: Dict[str, Any] = {}
        for key in common_keys:
            values = [e.params[key] for e in entries]
            counter = Counter(values)
            result[key] = counter.most_common(1)[0][0]
        return result

    # ─── P0 结构化模式提取 (方法论落地) ───

    @staticmethod
    def _extract_root_cause(entries: List[MemoryEntry],
                             common_tools: List[str],
                             common_params: Dict[str, Any],
                             success_rate: float,
                             representative_text: str) -> str:
        """5Whys 浅层自动化: 从成功/失败对比提炼"为什么有效/无效"

        策略:
            - 全成功: 根因 = 工具组合 + 关键参数持续有效
            - 有失败: 对比成功 vs 失败条目的 params/tools 差异, 找出成功的关键因子

        详细日志: 记录每次对比成功/失败参数时的具体差异点 (键集合差异 + 值差异)
        """
        success_entries = [e for e in entries if e.success]
        failure_entries = [e for e in entries if not e.success]
        tools_str = ", ".join(common_tools) if common_tools else "无特定工具"
        # 从 representative_text 提取前 5 个关键词作为任务摘要
        keywords = _tokenize(representative_text)
        keyword_str = " ".join(list(keywords)[:5]) if keywords else "该任务"

        logger.info(
            "[MemAbstract] _extract_root_cause: entries=%d | success=%d | "
            "failure=%d | success_rate=%.2f | common_tools=%s | "
            "common_params_keys=%s",
            len(entries), len(success_entries), len(failure_entries),
            success_rate, common_tools, sorted(common_params.keys()),
        )

        if not failure_entries:
            # 全成功: 根因 = 持续有效的工具 + 参数组合
            param_str = (f"配合参数 {common_params}"
                         if common_params else "无特殊参数")
            logger.info(
                "[MemAbstract] _extract_root_cause: 全成功场景 | "
                "无失败条目可对比 | 根因=工具组合持续有效"
            )
            return (f"使用 [{tools_str}] 处理「{keyword_str}」"
                    f"持续有效 (成功率 {success_rate * 100:.0f}%), "
                    f"{param_str}")

        # 有失败: 对比成功 vs 失败条目的参数差异
        success_param_keys = set()
        for e in success_entries:
            success_param_keys.update(e.params.keys())
        failure_param_keys = set()
        for e in failure_entries:
            failure_param_keys.update(e.params.keys())

        # 成功独有: 成功有条目有、失败条目无的参数键
        success_only = success_param_keys - failure_param_keys
        # 失败独有
        failure_only = failure_param_keys - success_param_keys
        # 共有的键
        common_keys = success_param_keys & failure_param_keys

        # 详细日志: 记录具体差异点
        logger.info(
            "[MemAbstract] _extract_root_cause: 成功/失败对比 | "
            "success_param_keys=%s | failure_param_keys=%s | "
            "success_only=%s | failure_only=%s | common_keys=%s",
            sorted(success_param_keys), sorted(failure_param_keys),
            sorted(success_only), sorted(failure_only), sorted(common_keys),
        )

        # 对比共有键的值差异
        value_diffs: List[str] = []
        for key in sorted(common_keys):
            success_values = {e.params.get(key) for e in success_entries
                              if key in e.params}
            failure_values = {e.params.get(key) for e in failure_entries
                              if key in e.params}
            if success_values != failure_values:
                diff_desc = (f"参数 {key}: 成功条目值={sorted(success_values, key=str)} "
                             f"vs 失败条目值={sorted(failure_values, key=str)}")
                value_diffs.append(diff_desc)
        if value_diffs:
            logger.info(
                "[MemAbstract] _extract_root_cause: 共有键值差异 | diffs=%s",
                value_diffs,
            )
        else:
            logger.info(
                "[MemAbstract] _extract_root_cause: 共有键值无差异 | "
                "差异可能在参数值或外部条件"
            )

        # 对比工具调用差异
        success_tools = {tc.get("name") for e in success_entries
                         for tc in e.tool_calls if isinstance(tc, dict)}
        failure_tools = {tc.get("name") for e in failure_entries
                         for tc in e.tool_calls if isinstance(tc, dict)}
        success_only_tools = success_tools - failure_tools
        failure_only_tools = failure_tools - success_tools
        if success_only_tools or failure_only_tools:
            logger.info(
                "[MemAbstract] _extract_root_cause: 工具差异 | "
                "success_only_tools=%s | failure_only_tools=%s",
                sorted(success_only_tools), sorted(failure_only_tools),
            )

        parts = [f"成功率 {success_rate * 100:.0f}%"]
        if success_only:
            parts.append(f"成功的关键因子是参数 {sorted(success_only)}")
        if failure_only:
            parts.append(f"失败案例带有 {sorted(failure_only)} 参数")
        if not success_only and not failure_only:
            # 参数相同但仍有失败 → 可能是参数值或外部因素
            parts.append("成功/失败条目参数键相同, 差异可能在参数值或外部条件")
        if value_diffs:
            parts.append(f"关键参数值差异: {'; '.join(value_diffs[:3])}")
        if success_only_tools:
            parts.append(f"成功独有工具: {sorted(success_only_tools)}")
        parts.append(f"核心工具: [{tools_str}]")
        return "; ".join(parts)

    @staticmethod
    def _extract_trigger_conditions(common_tags: List[str],
                                    common_params: Dict[str, Any],
                                    representative_text: str) -> List[str]:
        """触发条件: 什么场景下应用此技能

        从 tags + params + 关键词推导
        """
        conditions: List[str] = []
        # 1. 从 tags 推导
        for tag in common_tags[:3]:
            conditions.append(f"任务涉及 #{tag}")
        # 2. 从 params 推导
        for k, v in list(common_params.items())[:2]:
            conditions.append(f"参数 {k}={v}")
        # 3. 从 representative_text 提取前 3 个关键词
        keywords = list(_tokenize(representative_text))
        if keywords:
            top_kw = ", ".join(keywords[:3])
            conditions.append(f"任务描述包含: {top_kw}")
        conditions = conditions[:5]  # 最多 5 条
        logger.info(
            "[MemAbstract] _extract_trigger_conditions | "
            "tags=%d params=%d text_kw=%d → 条件数=%d",
            len(common_tags), len(common_params),
            len(keywords) if keywords else 0, len(conditions),
        )
        if conditions:
            logger.debug(
                "[MemAbstract]   触发条件明细:\n  - %s",
                "\n  - ".join(conditions),
            )
        return conditions

    @staticmethod
    def _extract_execution_steps(common_tools: List[str],
                                  common_params: Dict[str, Any]) -> List[str]:
        """执行步骤: Checklist 形式

        每个工具一个步骤 + 参数配置步骤 + 验证步骤
        """
        steps: List[str] = []
        # 每个工具一个步骤
        for i, tool in enumerate(common_tools, 1):
            steps.append(f"调用 {tool} 工具")
        # 参数配置
        if common_params:
            param_pairs = ", ".join(f"{k}={v}" for k, v in common_params.items())
            steps.append(f"配置参数: {param_pairs}")
        # 验证步骤
        steps.append("验证输出结果是否符合预期")
        logger.info(
            "[MemAbstract] _extract_execution_steps | "
            "tools=%d params=%d → 步骤数=%d",
            len(common_tools), len(common_params), len(steps),
        )
        if steps:
            logger.debug(
                "[MemAbstract]   执行步骤明细:\n  - [ ] %s",
                "\n  - [ ] ".join(steps),
            )
        return steps

    @staticmethod
    def _extract_if_then_rules(common_params: Dict[str, Any],
                                success_entries: List[MemoryEntry],
                                failure_entries: List[MemoryEntry],
                                common_tools: List[str]) -> List[str]:
        """If-Then-Else 规则: 结构化表达

        从参数 + 成功/失败对比生成
        """
        rules: List[str] = []
        # 1. 从 common_params 生成默认配置规则
        for k, v in list(common_params.items())[:3]:
            rules.append(f"IF 参数 {k} 未指定 THEN 使用默认值 {v}")
        # 2. 从成功/失败对比生成边界规则
        success_only_count = 0
        failure_only_count = 0
        if failure_entries and success_entries:
            success_param_keys = set()
            for e in success_entries:
                success_param_keys.update(e.params.keys())
            failure_param_keys = set()
            for e in failure_entries:
                failure_param_keys.update(e.params.keys())
            success_only = success_param_keys - failure_param_keys
            if success_only:
                rules.append(f"IF 缺少参数 {sorted(success_only)} THEN 可能失败")
                success_only_count = len(success_only)
            failure_only = failure_param_keys - success_param_keys
            if failure_only:
                rules.append(f"IF 出现参数 {sorted(failure_only)} THEN 警惕 (失败案例特有)")
                failure_only_count = len(failure_only)
        # 3. 工具缺失规则
        if common_tools:
            rules.append(f"IF 缺少工具 [{', '.join(common_tools)}] THEN 不适用此技能")
        rules = rules[:5]  # 最多 5 条
        logger.info(
            "[MemAbstract] _extract_if_then_rules | "
            "params=%d success=%d failure=%d tools=%d | "
            "success_only_keys=%d failure_only_keys=%d → 规则数=%d",
            len(common_params), len(success_entries), len(failure_entries),
            len(common_tools), success_only_count, failure_only_count,
            len(rules),
        )
        if rules:
            logger.debug(
                "[MemAbstract]   If-Then 规则明细:\n  - %s",
                "\n  - ".join(rules),
            )
        return rules

    @staticmethod
    def _extract_anti_patterns(failure_entries: List[MemoryEntry],
                                success_entries: List[MemoryEntry],
                                representative_text: str) -> List[str]:
        """反例边界: 什么时候不用这个技能

        从失败条目差异 + 域外场景推导
        """
        patterns: List[str] = []
        failure_only_tools_count = 0
        failure_only_keys_count = 0
        # 1. 从失败条目提取反例
        if failure_entries:
            # 失败条目独有的工具
            success_tools = {tc.get("name") for e in success_entries
                             for tc in e.tool_calls if isinstance(tc, dict)}
            failure_tools = {tc.get("name") for e in failure_entries
                             for tc in e.tool_calls if isinstance(tc, dict)}
            failure_only_tools = failure_tools - success_tools
            if failure_only_tools:
                patterns.append(f"不适用: 当任务需要工具 {sorted(failure_only_tools)} 时")
                failure_only_tools_count = len(failure_only_tools)
            # 失败条目独有的参数键
            success_keys = set()
            for e in success_entries:
                success_keys.update(e.params.keys())
            failure_keys = set()
            for e in failure_entries:
                failure_keys.update(e.params.keys())
            failure_only_keys = failure_keys - success_keys
            if failure_only_keys:
                patterns.append(f"不适用: 当出现参数 {sorted(failure_only_keys)} 时")
                failure_only_keys_count = len(failure_only_keys)
        # 2. 从 representative_text 推导域外场景
        keywords = _tokenize(representative_text)
        if keywords:
            top_kw = list(keywords)[:3]
            patterns.append(f"不涉及: 与 {', '.join(top_kw)} 无关的任务")
        # 3. 通用反例
        if not failure_entries:
            patterns.append("不适用: 复杂多步骤任务 (本技能基于单步成功案例提炼)")
        patterns = patterns[:4]  # 最多 4 条
        logger.info(
            "[MemAbstract] _extract_anti_patterns | "
            "failure=%d success=%d | failure_only_tools=%d "
            "failure_only_keys=%d → 反例数=%d",
            len(failure_entries), len(success_entries),
            failure_only_tools_count, failure_only_keys_count,
            len(patterns),
        )
        if patterns:
            logger.debug(
                "[MemAbstract]   反例边界明细:\n  - %s",
                "\n  - ".join(patterns),
            )
        return patterns

    # ─── 草稿生成 ───

    def generate_skill_draft(self, cluster: MemoryCluster) -> Dict[str, Any]:
        """从聚类生成技能草稿 dict

        草稿遵循三段式结构化 markdown:
            1. 核心原理 (root_cause_hypothesis)
            2. 触发条件 + 执行步骤 (Checklist)
            3. If-Then-Else 规则 + 反例边界
        """
        # skill_id: mem-{slug}-{cluster_hash[:6]}
        slug = _slugify(cluster.representative_text, max_len=30)
        cluster_hash = cluster.cluster_id.replace("cl-", "")[:6]
        skill_id = f"mem-{slug}-{cluster_hash}"

        name = cluster.representative_text[:60] or "memory-abstracted skill"
        description = (f"从 {cluster.size} 条记忆自动抽象 "
                        f"(成功率 {cluster.success_rate * 100:.0f}%)")

        logger.info(
            "[MemAbstract] generate_skill_draft START | "
            "cluster_id=%s | skill_id=%s | size=%d | success_rate=%.2f",
            cluster.cluster_id, skill_id, cluster.size, cluster.success_rate,
        )
        logger.debug(
            "[MemAbstract]   草稿输入字段: root_cause=%d chars | "
            "triggers=%d | steps=%d | if_then=%d | anti=%d | "
            "tools=%d | params=%d | tags=%d",
            len(cluster.root_cause_hypothesis or ""),
            len(cluster.trigger_conditions),
            len(cluster.execution_steps),
            len(cluster.if_then_rules),
            len(cluster.anti_patterns),
            len(cluster.common_tool_names),
            len(cluster.common_params),
            len(cluster.common_tags),
        )

        # 三段式结构化 markdown 内容
        content_lines: List[str] = [
            f"# {name}", "",
            "## 核心原理",
            cluster.root_cause_hypothesis or "未提取到根因假设, 请人工补充",
            "",
            "## 触发条件",
        ]
        if cluster.trigger_conditions:
            for cond in cluster.trigger_conditions:
                content_lines.append(f"- {cond}")
        else:
            content_lines.append("- (未提取到触发条件)")
        content_lines.append("")
        content_lines.append("## 执行步骤 (Checklist)")
        if cluster.execution_steps:
            for step in cluster.execution_steps:
                content_lines.append(f"- [ ] {step}")
        else:
            content_lines.append("- [ ] (未提取到执行步骤)")
        content_lines.append("")
        if cluster.if_then_rules:
            content_lines.append("## If-Then-Else 规则")
            for rule in cluster.if_then_rules:
                content_lines.append(f"- {rule}")
            content_lines.append("")
        if cluster.anti_patterns:
            content_lines.append("## 反例边界 (不适用场景)")
            for ap in cluster.anti_patterns:
                content_lines.append(f"- {ap}")
            content_lines.append("")
        # 来源信息
        content_lines.extend([
            "---",
            f"来源: memory_abstractor (从 {cluster.size} 条记忆聚类)",
            f"任务模式: {cluster.representative_text}",
        ])
        if cluster.common_tool_names:
            content_lines.append(f"常用工具: {', '.join(cluster.common_tool_names)}")
        if cluster.common_params:
            param_str = ", ".join(f"{k}={v}" for k, v in cluster.common_params.items())
            content_lines.append(f"默认参数: {param_str}")
        if cluster.common_tags:
            content_lines.append(f"标签: {', '.join(cluster.common_tags)}")

        content = "\n".join(content_lines)

        # config_schema 从 default_params 推断
        config_schema = self._infer_config_schema(cluster.common_params)

        sections = []
        if cluster.root_cause_hypothesis:
            sections.append("原理")
        if cluster.trigger_conditions:
            sections.append("触发")
        if cluster.execution_steps:
            sections.append("步骤")
        if cluster.if_then_rules:
            sections.append("规则")
        if cluster.anti_patterns:
            sections.append("反例")
        logger.info(
            "[MemAbstract] generate_skill_draft DONE | "
            "skill_id=%s | content_len=%d | sections=[%s]",
            skill_id, len(content),
            "+".join(sections) if sections else "(empty)",
        )

        return {
            "id": skill_id,
            "name": name,
            "description": description,
            "content": content,
            "content_type": "markdown",
            "category": "ai_generated",
            "tags": list(set(cluster.common_tags + ["memory-abstracted"])),
            "default_params": dict(cluster.common_params),
            "config_schema": config_schema,
            "dependencies": [],
            "source": "memory_abstractor",
            "author": "memory_abstractor",
            "version": "0.1.0",
            # P0 结构化字段
            "root_cause": cluster.root_cause_hypothesis,
            "triggers": cluster.trigger_conditions,
            "steps": cluster.execution_steps,
            "if_then_rules": cluster.if_then_rules,
            "anti_patterns": cluster.anti_patterns,
        }

    @staticmethod
    def _infer_config_schema(params: Dict[str, Any]) -> Dict[str, Any]:
        """从 params 推断 JSON Schema"""
        if not params:
            return {"type": "object", "properties": {}}
        properties = {}
        for k, v in params.items():
            if isinstance(v, bool):
                properties[k] = {"type": "boolean", "default": v}
            elif isinstance(v, (int, float)):
                properties[k] = {"type": "number", "default": v}
            elif isinstance(v, str):
                properties[k] = {"type": "string", "default": v}
            else:
                properties[k] = {"type": "string", "default": str(v)}
        return {"type": "object", "properties": properties}

    # ─── 质量门控 ───

    def check_quality_gate(self, cluster: MemoryCluster,
                            draft: Optional[Dict[str, Any]] = None,
                            ) -> Tuple[bool, List[str], str]:
        """质量门控检查

        检查项:
            1. 聚类大小 >= MIN_CLUSTER_SIZE (硬)
            2. 成功率 >= MIN_SUCCESS_RATE (硬)
            3. 与已有技能 Jaccard < MAX_EXISTING_DUP_JACCARD (硬)
            4. 复杂度: 执行步骤 <= MAX_EXECUTION_STEPS (软警告, 不阻止通过)

        Returns:
            (passed, reasons, duplicate_of)
            - passed: 是否通过 (软警告 [WARN] 不阻止)
            - reasons: 失败/警告原因列表
            - duplicate_of: 重复的已有技能 ID (无则 "")
        """
        reasons: List[str] = []
        gate_status: List[str] = []
        duplicate_of = ""

        # 1. 聚类大小
        if cluster.size < self.min_cluster_size:
            reasons.append(f"聚类大小 {cluster.size} < {self.min_cluster_size}")
            gate_status.append(f"size_FAIL({cluster.size}<{self.min_cluster_size})")
        else:
            gate_status.append(f"size_OK({cluster.size})")

        # 2. 成功率
        if cluster.success_rate < self.min_success_rate:
            reasons.append(
                f"成功率 {cluster.success_rate:.2f} < {self.min_success_rate}"
            )
            gate_status.append(
                f"rate_FAIL({cluster.success_rate:.2f}<{self.min_success_rate})"
            )
        else:
            gate_status.append(f"rate_OK({cluster.success_rate:.2f})")

        # 3. 重复检测
        if draft is not None:
            dup_id = self._find_duplicate(draft)
            if dup_id:
                reasons.append(f"与已有技能 {dup_id} 重复 (Jaccard 过高)")
                gate_status.append(f"dup_FAIL({dup_id})")
                duplicate_of = dup_id
            else:
                gate_status.append("dup_OK")

        # 4. 复杂度检查 (软警告 — 最小可行性规则, 不阻止通过)
        steps_count = len(cluster.execution_steps)
        if steps_count > self.MAX_EXECUTION_STEPS:
            reasons.append(f"[WARN] 执行步骤 {steps_count} > {self.MAX_EXECUTION_STEPS}, 建议简化或拆分为多个技能")
            gate_status.append(f"complexity_WARN({steps_count}>{self.MAX_EXECUTION_STEPS})")
        else:
            gate_status.append(f"complexity_OK({steps_count})")

        # passed 只看硬失败 ([WARN] 前缀的是软警告, 不阻止)
        hard_failures = [r for r in reasons if not r.startswith("[WARN]")]
        passed = len(hard_failures) == 0

        logger.info(
            "[MemAbstract] check_quality_gate: cluster_id=%s | passed=%s | "
            "status=%s | reasons=%s",
            cluster.cluster_id, passed, "|".join(gate_status), reasons,
        )
        return passed, reasons, duplicate_of

    def _find_duplicate(self, draft: Dict[str, Any]) -> Optional[str]:
        """检查草稿与已有技能的 Jaccard 相似度"""
        svc = self._resolve_skills_service()
        try:
            existing_skills = svc.list_all()
        except Exception as e:  # noqa: BLE001
            logger.warning("[MemAbstract] 加载已有技能失败: %s", e)
            return None
        if not existing_skills:
            return None
        draft_tokens = _tokenize(draft.get("content", "") + " " + draft.get("name", ""))
        for skill in existing_skills:
            existing_tokens = _tokenize(
                (skill.content or "") + " " + (skill.name or "")
            )
            sim = _jaccard(draft_tokens, existing_tokens)
            if sim >= self.max_existing_dup_jaccard:
                logger.info(
                    "[MemAbstract] _find_duplicate: 命中 | draft=%s | "
                    "existing=%s | jaccard=%.2f",
                    draft.get("id"), skill.id, sim,
                )
                return skill.id
        return None

    # ─── 单聚类处理 ───

    def _process_cluster(self, cluster: MemoryCluster,
                          *, auto_register: bool = False) -> Dict[str, Any]:
        """处理单个聚类: 生成草稿 → 质量门控 → (可选) 注册"""
        logger.info(
            "[MemAbstract] _process_cluster START | cluster_id=%s | "
            "size=%d | auto_register=%s",
            cluster.cluster_id, cluster.size, auto_register,
        )
        # 1. 生成草稿
        draft = self.generate_skill_draft(cluster)

        # 2. 质量门控
        passed, reasons, duplicate_of = self.check_quality_gate(
            cluster, draft=draft,
        )
        logger.info(
            "[MemAbstract] _process_cluster 质量门控 | "
            "cluster_id=%s | passed=%s | duplicate_of=%s | reasons=%s",
            cluster.cluster_id, passed, duplicate_of or "-",
            reasons or "[]",
        )

        # 计算聚类平均信号强度
        avg_signal = 0.0
        if cluster.entries:
            avg_signal = sum(e.signal_strength for e in cluster.entries) / cluster.size

        result: Dict[str, Any] = {
            "cluster_id": cluster.cluster_id,
            "cluster_size": cluster.size,
            "success_rate": cluster.success_rate,
            "common_tool_names": cluster.common_tool_names,
            "common_tags": cluster.common_tags,
            "draft_skill_id": draft["id"],
            "draft_name": draft["name"],
            "draft_description": draft["description"],
            "draft_content_preview": draft["content"][:500],
            "draft_default_params": draft["default_params"],
            "quality_gate_passed": passed,
            "quality_gate_reasons": reasons,
            "registered": False,
            "skill_id": None,
            "duplicate_of": duplicate_of or None,
            # 信号强度
            "avg_signal_strength": round(avg_signal, 3),
            # P0 结构化字段
            "draft_root_cause": draft.get("root_cause", ""),
            "draft_triggers": draft.get("triggers", []),
            "draft_steps": draft.get("steps", []),
            "draft_if_then_rules": draft.get("if_then_rules", []),
            "draft_anti_patterns": draft.get("anti_patterns", []),
        }
        logger.debug(
            "[MemAbstract]   草稿汇总: avg_signal=%.3f | "
            "draft_skill_id=%s | preview_len=%d",
            avg_signal, draft["id"], len(draft["content"][:500]),
        )

        # 3. 注册 (仅当通过质量门 + auto_register)
        if passed and auto_register and not duplicate_of:
            logger.info(
                "[MemAbstract] _process_cluster 尝试注册 | "
                "cluster_id=%s | skill_id=%s",
                cluster.cluster_id, draft["id"],
            )
            try:
                svc = self._resolve_skills_service()
                # 只传 Skill 接受的字段
                skill_data = {
                    "id": draft["id"],
                    "name": draft["name"],
                    "description": draft["description"],
                    "content": draft["content"],
                    "content_type": draft["content_type"],
                    "category": draft["category"],
                    "tags": draft["tags"],
                    "default_params": draft["default_params"],
                    "config_schema": draft["config_schema"],
                    "dependencies": draft["dependencies"],
                    "source": draft["source"],
                    "author": draft["author"],
                    "version": draft["version"],
                }
                skill = svc.create_manual(skill_data)
                result["registered"] = True
                result["skill_id"] = skill.id
                logger.info(
                    "[MemAbstract] _process_cluster: 已注册 | skill_id=%s",
                    skill.id,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "[MemAbstract] _process_cluster: 注册失败 | %s", e,
                )
                result["registered"] = False
                result["skill_id"] = None

        logger.info(
            "[MemAbstract] _process_cluster DONE | "
            "cluster_id=%s | passed=%s | registered=%s | duplicate_of=%s",
            cluster.cluster_id, passed, result["registered"],
            duplicate_of or "-",
        )
        return result
