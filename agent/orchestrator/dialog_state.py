"""对话状态跟踪（DST）— 指代消解与省略句补全

架构定位：三层漏斗前置预处理层
    orchestrator.process()
        ├── 第零步 InputGuard
        ├── 第一步 WorkflowEngine
        ├── 【新增】DST 预处理（本模块）← 在意图路由前补全省略句
        ├── 第三步 IntentRouter
        ├── 第三步半 语义层
        └── 第四步 LLM

解决的问题:
    用户说"那个呢"/"然后呢"/"再来一个"等省略句时，前序规则层和语义层
    无法理解指代对象，导致误判为未知意图直落 LLM。本模块通过继承上一轮
    的意图/关键词，将省略句补全为完整查询，使前序层能正确匹配。
    并用 augmented 与上一轮原始输入的向量相似度做软门控，过滤语义断裂场景。

【不易】
  - 不引入重型 NLP 依赖（禁 spaCy/CoreNLP），复用现有 SentenceTransformer
  - 补全后的查询不修改 user_input，而是作为 augmented_input 传递
  - 状态仅在会话内有效，不持久化（隐私+简易）
【变易】
  - 状态槽位可扩展（last_intent/last_keywords/last_skill/last_entity/last_user_input）
  - 向量置信度可选注入（vector_adapter），热则用，冷则降级纯正则
  - 省略句模式可通过 register_pattern 运行时扩展
【简易】
  - 单文件单类，无外部硬依赖（numpy 延迟导入）
  - 无指代时不补全（返回 None），调用方用原始输入
  - 向量不可用时自动回退纯正则，不阻断主链路

设计参考: message_handler.py 的 FOLLOW_UP_PATTERNS，但升级为状态跟踪 +
           SkillVectorAdapter.encode_query 的向量置信度软门控
"""

from __future__ import annotations

import re
import logging
import threading
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════
#  省略句/指代词模式
# ════════════════════════════════════════════════════════════

# 指代词模式（需要从历史继承上下文）
_DEIXIS_PATTERNS = [
    re.compile(r"(?i)^(那(个|些|个呢)|这(个|些)|它|他|她)(呢|是啥|是什么|怎么样)?[？?]?$"),
    re.compile(r"(?i)^(前一个|上一个|刚才(那个|那个的))"),
]

# 接续词模式（需要继承上一轮动作）
_CONTINUATION_PATTERNS = [
    re.compile(r"(?i)^(然后(呢|呢？)?|接着(呢|说)?|继续|再来一个|下一个|还有呢?)$"),
    re.compile(r"(?i)^(再(来|来一个|给一个|试一次))"),
]

# 短句阈值（短于此且匹配指代/接续模式时触发补全）
_SHORT_QUERY_THRESHOLD = 15


class DialogState:
    """对话状态跟踪器

    维护会话内的对话状态，支持指代消解与省略句补全。

    用法:
        dst = DialogState()
        # 第一轮
        dst.update(intent="pdf_convert", keywords=["PDF", "转换"], user_input="帮我转换PDF")
        # 第二轮
        augmented = dst.resolve("那个呢")
        # augmented = "关于 PDF 转换 呢"（补全后，向量校验通过则返回）

    向量置信度（【变易】可选）:
        注入 vector_adapter（鸭子类型，需有 encode_query(str)->Optional[vec]）
        后，resolve() 会用 augmented 与 last_user_input 的余弦相似度做软门控；
        未注入或编码失败时回退纯正则（【简易】优雅降级）。
    """

    def __init__(self, vector_adapter: Optional[Any] = None):
        # Why RLock：HTTP 路由线程可能并发调用 update/resolve/to_dict/reset，
        # turn_count 读-改-写非原子；RLock 允许嵌套重入（update 内日志读字段）。
        self._lock = threading.RLock()
        # 对话状态槽位
        self.last_intent: Optional[str] = None
        self.last_keywords: List[str] = []
        self.last_skill: Optional[str] = None
        self.last_entity: Optional[str] = None
        self.last_response_topic: Optional[str] = None
        # 上一轮用户原始输入（供下轮向量置信度校验用）
        self.last_user_input: Optional[str] = None
        self.turn_count: int = 0
        # 向量适配器（可选，鸭子类型；None=纯正则模式）
        self.vector_adapter: Optional[Any] = vector_adapter
        # 最近一次 resolve 的向量相似度（供 orchestrator 日志读取；None=未计算/纯止则）
        self.last_similarity: Optional[float] = None

    def update(self, *, intent: Optional[str] = None,
               keywords: Optional[List[str]] = None,
               skill: Optional[str] = None,
               entity: Optional[str] = None,
               response_topic: Optional[str] = None,
               user_input: Optional[str] = None) -> None:
        """更新对话状态（每轮对话后调用）

        Args:
            intent: 本轮意图（如 "pdf_convert"）
            keywords: 本轮关键词列表
            skill: 本轮匹配的技能 ID
            entity: 本轮涉及的实体（如文件名）
            response_topic: 本轮回复主题
            user_input: 本轮用户原始输入（供下轮向量置信度校验）
        """
        # Why 整体持锁：字段组 + turn_count 必须原子更新——并发 update/resolve 交错
        # 会读到"intent 已更新但 keywords 未更新"的半状态组合；turn_count 读-改-写
        # 非原子会丢轮数
        with self._lock:
            if intent is not None:
                self.last_intent = intent
            if keywords is not None:
                self.last_keywords = list(keywords)
            if skill is not None:
                self.last_skill = skill
            if entity is not None:
                self.last_entity = entity
            if response_topic is not None:
                self.last_response_topic = response_topic
            if user_input is not None:
                self.last_user_input = user_input
            self.turn_count += 1
            logger.debug(log_dict_safe({
                "module_name": "dialog_state",
                "action": "dst.update",
                "turn": self.turn_count,
                "last_intent": self.last_intent,
                "last_keywords": self.last_keywords,
            }))

    def is_ellipsis_query(self, text: str) -> bool:
        """检测是否为省略句/指代句

        Args:
            text: 用户输入

        Returns:
            True 表示需要指代消解
        """
        if not text or not text.strip():
            return False
        text = text.strip()
        # 短句 + 匹配指代/接续模式
        if len(text) > _SHORT_QUERY_THRESHOLD:
            return False
        for pattern in _DEIXIS_PATTERNS + _CONTINUATION_PATTERNS:
            if pattern.search(text):
                return True
        return False

    def resolve(self, text: str) -> Optional[str]:
        """指代消解 — 将省略句补全为完整查询

        策略（正则消解 + 向量置信度软门控）:
        1. 检测是否为省略句（正则）
        2. 从 last_keywords + last_intent 构造上下文
        3. 拼接为补全查询
        4. 【变易】若注入 vector_adapter 且有 last_user_input，
           用 augmented 与 last_user_input 的余弦相似度做软门控：
           sim < DST_VECTOR_MIN_SIM 则拒绝补全（避免语义断裂误导路由）；
           向量不可用时回退纯正则（返回 augmented）

        Args:
            text: 用户原始输入

        Returns:
            补全后的查询字符串；无需补全/置信度过低时返回 None
        """
        # Why 锁内快照：状态槽位可能在并发 update 中变更，锁内一次性拷贝
        # （keywords/intent/skill/turn_count/last_user_input），锁外完成计算；
        # last_similarity 写入回锁，避免并发 resolve 互相覆盖可见性
        with self._lock:
            # 每轮重置；仅当向量门控实际计算时才覆盖（供 orchestrator 日志读取）
            self.last_similarity = None
            turn_count = self.turn_count
            keywords = list(self.last_keywords)
            intent = self.last_intent
            skill = self.last_skill
            last_user_input = self.last_user_input

        if not self.is_ellipsis_query(text):
            return None

        # 无历史状态可继承
        if turn_count == 0:
            return None

        text = text.strip()

        # 构造上下文片段
        context_parts: List[str] = []

        # 优先用关键词
        if keywords:
            context_parts.extend(keywords[:3])  # 最多取3个关键词
        # 关键词不足时用意图
        elif intent and intent != "unknown":
            context_parts.append(intent)
        # 意图也不足时用技能
        elif skill:
            context_parts.append(skill)

        if not context_parts:
            # 有历史但无可用上下文（如历史全是 unknown）
            return None

        context_str = " ".join(context_parts)

        # 判断补全类型
        is_continuation = any(p.search(text) for p in _CONTINUATION_PATTERNS)

        if is_continuation:
            # 接续句："然后呢" → "继续 PDF 转换"
            augmented = f"继续 {context_str}"
        else:
            # 指代句："那个呢" → "那个 PDF 转换 呢"
            # 保留原句的语气词
            suffix = ""
            if text.endswith("呢"):
                suffix = " 呢"
            elif text.endswith(("？", "?")):
                suffix = " ？"
            augmented = f"关于 {context_str}{suffix}"

        # 【变易】向量置信度软门控 — augmented 与 last_user_input 余弦相似度
        # （用锁内快照，锁外编码：encode_query 为 CPU 密集，不得持锁阻塞 update）
        sim, reject_reason = self._vector_confidence_check(augmented, last_user_input)
        # 暴露给 orchestrator 日志（None=向量不可用/纯止则；float=实际相似度）
        with self._lock:
            self.last_similarity = sim
        if reject_reason is not None:
            # 语义断裂或显式关闭 → 拒绝补全，返回 None
            logger.info(log_dict_safe({
                "module_name": "dialog_state",
                "action": "dst.resolve.rejected",
                "original": text,
                "augmented": augmented,
                "similarity": (round(sim, 4) if sim is not None else None),
                "reject_reason": reject_reason,
                "turn": turn_count,
            }))
            return None

        logger.info(log_dict_safe({
            "module_name": "dialog_state",
            "action": "dst.resolve.augmented",
            "original": text,
            "augmented": augmented,
            "similarity": (round(sim, 4) if sim is not None else None),
            "turn": turn_count,
        }))

        return augmented

    def _vector_confidence_check(self, augmented: str, last_user_input: Optional[str]) -> tuple:
        """[TLM-L0] 向量置信度软门控 — augmented vs last_user_input

        Args:
            augmented: 补全后的查询
            last_user_input: 上一轮用户原始输入（锁外传入快照，避免持锁编码）

        Returns:
            (similarity, reject_reason):
              - similarity: 余弦相似度 float，或 None（向量不可用/跳过）
              - reject_reason: None=通过；非 None=拒绝原因字符串
        """
        import os as _os

        # 开关：默认启用，DST_VECTOR_ENABLED=false 时跳过门控（纯正则）
        if _os.environ.get("DST_VECTOR_ENABLED", "true").lower() not in ("true", "1", "yes"):
            return (None, None)
        # 无向量适配器或无上一轮输入 → 无法校验，回退纯正则（通过）
        if self.vector_adapter is None or not last_user_input:
            return (None, None)

        try:
            vec_aug = self.vector_adapter.encode_query(augmented)
            vec_last = self.vector_adapter.encode_query(last_user_input)
        except Exception as e:  # noqa: BLE001
            # 适配器异常不阻断主链路，回退纯正则
            logger.debug(log_dict_safe({
                "module_name": "dialog_state",
                "action": "dst.vector.encode_failed",
                "error": f"{type(e).__name__}: {e}",
            }))
            return (None, None)

        # encode 返回 None（后端不可用）→ 回退纯正则
        if vec_aug is None or vec_last is None:
            return (None, None)

        try:
            import numpy as _np
            # 向量已在 encode_query 内归一化（normalize_embeddings=True），点积即余弦相似度
            sim = float(_np.dot(vec_aug, vec_last))
        except Exception as e:  # noqa: BLE001
            logger.debug(log_dict_safe({
                "module_name": "dialog_state",
                "action": "dst.vector.sim_failed",
                "error": f"{type(e).__name__}: {e}",
            }))
            return (None, None)

        min_sim = float(_os.environ.get("DST_VECTOR_MIN_SIM", "0.5"))
        if sim < min_sim:
            return (sim, f"similarity {sim:.4f} < threshold {min_sim}")
        return (sim, None)

    def to_dict(self) -> Dict[str, Any]:
        """导出状态快照（供日志/调试，锁内读取防半状态）"""
        with self._lock:
            return {
                "turn_count": self.turn_count,
                "last_intent": self.last_intent,
                "last_keywords": list(self.last_keywords),
                "last_skill": self.last_skill,
                "last_entity": self.last_entity,
                "last_response_topic": self.last_response_topic,
                "last_user_input": self.last_user_input,
            }

    def reset(self) -> None:
        """重置状态（新会话时调用，锁内整组重置）"""
        with self._lock:
            self.last_intent = None
            self.last_keywords = []
            self.last_skill = None
            self.last_entity = None
            self.last_response_topic = None
            self.last_user_input = None
            self.turn_count = 0
        # 注意：vector_adapter 不重置（模型加载昂贵，跨会话复用）


# ════════════════════════════════════════════════════════════
#  会话级 DST 管理器（按 session_id 隔离状态）
# ════════════════════════════════════════════════════════════

# 模块级会话状态表（session_id -> DialogState）
# 【简易】纯内存，不持久化；会话结束由 GC 回收
_SESSION_STATES: Dict[str, DialogState] = {}

# Why RLock：多路 HTTP 请求并发首轮访问各自 session，get_dialog_state 的
# check-then-create 非原子（TOCTOU），不加锁会为同一 session 创建多个实例
_SESSION_LOCK = threading.RLock()


def get_dialog_state(session_id: str = "default",
                     vector_adapter: Optional[Any] = None) -> DialogState:
    """获取会话级 DST 实例（懒创建）

    Args:
        session_id: 会话 ID
        vector_adapter: 可选向量适配器（鸭子类型）；实例已存在时幂等更新

    Returns:
        该会话的 DialogState 实例
    """
    with _SESSION_LOCK:  # 检查-创建-赋值原子（防并发重复实例）
        state = _SESSION_STATES.get(session_id)
        if state is None:
            state = DialogState(vector_adapter=vector_adapter)
            _SESSION_STATES[session_id] = state
        elif vector_adapter is not None:
            # 幂等更新：语义层热后注入已初始化的适配器
            state.vector_adapter = vector_adapter
        return state


def reset_session_state(session_id: str) -> None:
    """重置会话状态（会话结束时调用）"""
    with _SESSION_LOCK:
        state = _SESSION_STATES.get(session_id)
        if state is not None:
            state.reset()


# ════════════════════════════════════════════════════════════
#  日志 helper（避免循环依赖）
# ════════════════════════════════════════════════════════════

def log_dict_safe(payload: dict) -> dict:
    """轻量日志规范化"""
    import uuid
    data = dict(payload)
    if "trace_id" not in data:
        data["trace_id"] = uuid.uuid4().hex[:16]
    if "module_name" not in data:
        data["module_name"] = "dialog_state"
    if "action" not in data:
        data["action"] = "unknown"
    return data
