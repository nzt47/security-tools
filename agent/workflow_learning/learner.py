"""学习者 — 从 LLM 交互中提取可复用方法

输入: LearningRecord (一次成功的 LLM 交互记录)
输出: LearnedWorkflow 骨架 (尚未保存到仓库)

学习方法:
    1. 抽取工具调用序列: tool_calls 中按时间顺序提取 tool_name → WorkflowStep
    2. 参数模板化: 把具体参数值替换为 $input / $prev_output / 字面量占位
    3. 任务签名: 抽取用户输入的**实词**(英文按整词、中文按 2 字滑窗)后排序拼接
    4. 触发模式: 从用户输入提取至多 5 个实词(与签名同一分词口径, 见下)
    5. 置信度初值: 0.4 (== matcher 门槛, 见 learn() 内注释)

任务签名口径(F11 修复, 唯一实现 = `canonical_signature`)
------------------------------------------------------
旧口径复用 `_extract_keywords` 的**按字**分词取 top-10, 于是
"统计项目里所有 Python 文件的行数并保存报告" 的签名是
`python|件|所|文|有|目|统|计|里|项` —— 9 个汉字**单字**按字典序拼接。
那是"字符清单"而不是任务签名:
  1) 不携带语义(单字在任意中文任务里都高频);
  2) 无法区分任务(同一句话学 3 次 -> 3 条签名完全相同的条目);
  3) 签名字符串本身还会作为特征进入 matcher 的索引文本, 把"单字特征"扩散到匹配面。
新口径只产出**实词**:

    英文/标识符: 整个词(>=2 字符, 小写化)
    中文       : 先按中文字停用字(复用 `_STOP_WORDS`)把字串切成"词段",
                 再对每段取 **2 字滑窗(bigram)**
    签名       : 去重 -> 字典序排序 -> 用 "|" 拼接; 无实词 -> "general"

每个 token 因此至少携带 2 个字符, 与仓库唯一的触发词政策
(`admission.MIN_TRIGGER_CHARS = 2`, 即"单字无区分度")**同源**。
无实词的输入("" / "???" / 单个汉字)不构成任务 -> `learn()` 直接拒绝、不落库
(否则只会往仓库里塞没有签名、无法去重的垃圾条目)。

触发词口径(F11-B 修复, 唯一实现 = `trigger_tokens`)
--------------------------------------------------
`trigger_patterns` 与签名**共用同一个分词器**(`signature_tokens`), 不再走
`_extract_keywords` 的"按字"口径。改前的链路是:

    中文输入 --按字切分--> 单字 --`admission.MIN_TRIGGER_CHARS = 2` 全量过滤--> []

于是**任何纯中文任务**都拿到空触发列表, 被结构性准入判 `NO_DISCRIMINATIVE_TRIGGER`,
一律落草稿态 ⇒ 该子系统产不出可被消费的工作流(F11 §4.3 的实测结论)。改后:

    中文输入 --2 字滑窗(bigram)--> 每个 token >= 2 个有效字符 --> 非空

**这不是放宽门槛, 而是修正分词口径**: `admission.MIN_TRIGGER_CHARS = 2`
("单字无区分度")继续原样生效, 单字**仍然不得**成为触发词; 本模块只是把中文的
**切分单位**从"字"换成"2 字滑窗"—— 用"字"作单位在这条政策下必然全灭,
它度量的是错的东西。

英文侧既有行为保持不变: 整词、小写化、长度 >= 2、经 `_STOP_WORDS` 过滤。
(`signature_tokens` 本身**不**过滤英文停用词 —— 签名要的是"输入的函数";
而触发词进的是**匹配特征**, 停用词必须滤掉, 故 `trigger_tokens` 额外做这一步。)

行为边界(与 `admission` 政策一致, 均有单测固化):
  - 触发词非空是结构性准入的必要条件之一 ⇒ **中文 2 步以上的交互从此前的
    草稿态变为 `active`** —— 这是 F11-B 的**预期行为变更**(报告 §3);
  - 步骤数 < `admission.MIN_STEPS = 2` 的交互(含中文)**仍是草稿**: 步骤门槛未动;
  - 无实词输入(`"好"` / `"你好"` / `"???"`)仍被 `learn()` 拒绝, 不落库。
"""

from __future__ import annotations
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from .models import LearnedWorkflow, WorkflowStep, LearningRecord, WorkflowStatus
from .exceptions import WorkflowLearningError, ErrorCode
from .observability import logger, emit_metric, track_event, traced_action
from . import admission

# 简易停用词表 (中英文混合)
_STOP_WORDS = {
    # 中文
    "的", "了", "在", "是", "我", "你", "他", "她", "它", "们",
    "和", "与", "或", "及", "但", "而", "请", "帮", "给", "把",
    "这", "那", "一", "二", "三", "个", "中", "上", "下",
    # 英文
    "the", "a", "an", "is", "are", "was", "were", "be", "been",
    "to", "of", "in", "on", "at", "for", "with", "by",
    "and", "or", "but", "if", "then", "so", "do", "does", "did",
    "i", "you", "he", "she", "it", "we", "they",
    "please", "help", "me", "my", "your",
}

_WORD_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_]+|[\u4e00-\u9fff]")

#: 任务签名分词用的"字串"：英文/标识符整词 或 一段连续的汉字
_SIG_RUN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_+#.\-]*|[\u4e00-\u9fff]+")

#: 中文停用**字**(只用于给汉字串切"词段", 本身不成词)：
#: 与 `_STOP_WORDS` 同源，不新增第二份停用词表(单字口径的判定权仍在 admission)。
_CJK_STOP_CHARS = {w for w in _STOP_WORDS
                   if len(w) == 1 and "\u4e00" <= w <= "\u9fff"}

#: 任务签名里的实词上限：超长输入的签名会线性膨胀(写进 json + matcher 索引文本)，
#: 按**首次出现序**截断(句首承载任务意图)，再排序拼接。
SIGNATURE_MAX_TOKENS = 32

#: 触发词上限(F11-B)：与旧 `_extract_keywords(..., top_k=5)` 保持同一个 5，
#: 按**首次出现序**取(句首承载任务意图)，不额外引入新阈值。
TRIGGER_TOKENS_MAX = 5

#: 无实词时的签名占位值：**不落库**(见 learn())，仅作为对外可判定的退化标记
SIGNATURE_FALLBACK = "general"


def _extract_keywords(text: str, top_k: int = 5) -> List[str]:
    """提取关键词 (去停用词，按频率取 top_k)"""
    tokens = _WORD_RE.findall((text or "").lower())
    freq: Dict[str, int] = {}
    for t in tokens:
        if t in _STOP_WORDS or len(t) < 1:
            continue
        freq[t] = freq.get(t, 0) + 1
    return [t for t, _ in sorted(freq.items(), key=lambda x: -x[1])[:top_k]]


def _split_cjk_segments(run: str) -> List[str]:
    """把一段连续汉字按中文字停用字切成"词段"(停用字本身不成词)"""
    segments: List[str] = []
    cur: List[str] = []
    for ch in run:
        if ch in _CJK_STOP_CHARS:
            if cur:
                segments.append("".join(cur))
                cur = []
        else:
            cur.append(ch)
    if cur:
        segments.append("".join(cur))
    return segments


def term_stream(text: str) -> List[str]:
    """把文本切成**实词流**——保留重复、不截断

    口径与 `signature_tokens` **完全同一个**(见其 docstring)：
    - 英文/标识符: 整词, 小写化, 长度 >= 2;
    - 中文: 按停用字切段后取 2 字滑窗(bigram);
    - 单字(中/英)一律丢弃。

    区别只有两点：**不去重**、**不截断** —— 便于 TF 统计。
    `signature_tokens` 即"本函数去重后截断"，故"怎么切词"在本仓库
    依然只有这一处实现(F11-B 口径；F11-C 起 `matcher._tokenize` 也复用它，
    索引侧与查询侧共用同一分词器)。
    """
    out: List[str] = []
    for run in _SIG_RUN_RE.findall((text or "").lower()):
        if run[0].isascii():
            if len(run) >= 2:
                out.append(run)
            continue
        for seg in _split_cjk_segments(run):
            for i in range(len(seg) - 1):
                out.append(seg[i:i + 2])
    return out


def signature_tokens(text: str, limit: Optional[int] = SIGNATURE_MAX_TOKENS) -> List[str]:
    """抽取任务签名的**实词**(见模块 docstring §任务签名口径)

    - 英文/标识符: 整词, 小写化, 长度 >= 2;
    - 中文: 按停用字切段后取 2 字滑窗(bigram);
    - 单字(中/英)一律丢弃 —— 与 `admission.MIN_TRIGGER_CHARS` 同一口径。

    `limit` 只截断返回条数(默认 `SIGNATURE_MAX_TOKENS`)，**不改分词口径**；
    `trigger_tokens` 传 `limit=None` 取全部候选后再自行筛选 —— 这样"怎么切词"
    在本仓库始终只有这一处实现(F11-B：不写第二套分词)。

    F11-C：实现改为 `term_stream` 的**去重 + 截断**（行为逐字不变，
    见 `test_signature_tokens_与_term_stream_同源`）。
    """
    tokens: List[str] = []
    seen = set()
    for t in term_stream(text):
        if t not in seen:
            seen.add(t)
            tokens.append(t)
    return tokens if limit is None else tokens[:limit]


def canonical_signature(user_input: str) -> str:
    """任务签名(唯一口径)：实词去重 -> 字典序排序 -> "|" 拼接

    - 同一个输入恒得同一个签名(纯函数，无状态/无时间参与)；
    - 无实词时返回 `SIGNATURE_FALLBACK`(此时调用方不得落库)；
    - 所有 token 均 >= 2 字符 ⇒ 通过 `admission.is_discriminative_trigger`。
    """
    kws = sorted(set(signature_tokens(user_input)))
    return "|".join(kws) or SIGNATURE_FALLBACK


def _make_task_signature(user_input: str) -> str:
    """兼容旧名：等价于 `canonical_signature`"""
    return canonical_signature(user_input)


def trigger_tokens(text: str, top_k: int = TRIGGER_TOKENS_MAX) -> List[str]:
    """抽取**触发词**候选(见模块 docstring §触发词口径(F11-B))

    与 `signature_tokens` 共用同一个分词器, 只做两件它不做的事:

    1. 滤掉英文停用词(`_STOP_WORDS`)—— 签名是"输入的函数", 触发词是**匹配特征**,
       `the`/`and` 这类词进特征只会稀释匹配面(旧口径同样过滤, 属回归保持);
    2. 按首次出现序截断到 `top_k` 个(默认 5, 与旧 `_extract_keywords(top_k=5)` 同值)。

    中文 token 是 2 字滑窗, 英文 token 是整词, 因此**每个 token 都 >= 2 个有效字符**,
    与 `admission._informative_len` / `MIN_TRIGGER_CHARS` 同一口径: 单字永不入选。
    """
    out: List[str] = []
    # limit=None: 先取全部候选再筛停用词, 避免"前 32 个 token 恰好都是停用词"
    # 时把后面的实词一并截掉(长英文输入)
    for tok in signature_tokens(text, limit=None):
        if tok.isascii() and tok in _STOP_WORDS:
            continue
        out.append(tok)
        if len(out) >= top_k:
            break
    return out


def _templatize_params(params: Dict[str, Any], *, user_input: str) -> Dict[str, Any]:
    """把参数值模板化

    简化策略:
        - 字符串值若包含 user_input 关键词，替换为 $input
        - 字符串值若像 URL/路径，保持原样
        - 其他保持字面量
    """
    if not params:
        return {}
    template: Dict[str, Any] = {}
    keywords = _extract_keywords(user_input, top_k=3)
    for k, v in params.items():
        if isinstance(v, str):
            # 如果值里包含用户输入的关键词，模板化
            for kw in keywords:
                if kw and kw in v.lower():
                    v = v.lower().replace(kw, "${input}")
                    break
            template[k] = v
        else:
            template[k] = v
    return template


class WorkflowLearner:
    """工作流学习者"""

    def learn(self, record: LearningRecord) -> LearnedWorkflow:
        """从一次成功的 LLM 交互中学习方法"""
        with traced_action("wf_learn", session_id=record.session_id,
                           user_input=record.user_input[:80]) as ctx:
            if not record.success:
                raise WorkflowLearningError(
                    "仅能从成功的交互中学习",
                    code=ErrorCode.LEARN_FAILED,
                    details={"success": record.success},
                )
            if not record.tool_calls:
                raise WorkflowLearningError(
                    "工具调用序列为空，无可学习方法",
                    code=ErrorCode.LEARN_FAILED,
                )

            # 1) 提取步骤
            steps = self._extract_steps(record)
            if not steps:
                raise WorkflowLearningError(
                    "无法从 tool_calls 中提取有效步骤",
                    code=ErrorCode.LEARN_FAILED,
                )

            # 2) 任务签名（F11 新口径：实词）
            #    无实词的输入（"" / "???" / 单个汉字）不构成任务：学它只会往
            #    仓库塞一条没有签名、无法去重的垃圾条目 ⇒ 直接拒绝、不落库。
            signature = canonical_signature(record.user_input)
            if signature == SIGNATURE_FALLBACK:
                logger.info(
                    "[Learner] 拒绝学习: 输入无可提取实词 (session=%s)",
                    record.session_id)
                raise WorkflowLearningError(
                    "输入无可提取实词，无法形成任务签名，不落库",
                    code=ErrorCode.LEARN_FAILED,
                    details={"user_input": record.user_input[:80],
                             "signature": signature},
                )

            # 3) 触发模式
            #    【TASK-S10-01】触发模式只收**有区分度**的触发词：单字特征等价于
            #    "任意输入都可能命中"，既不进 trigger_patterns（技能"触发条件"
            #    章节的展示源），也不进索引。
            #    【F11-B 修复】切分口径改为与签名同源的 `trigger_tokens`：
            #    中文取 **2 字滑窗**（old: 按字切分 → 单字 → 被上面这条政策
            #    全量过滤 → 任何纯中文任务的 trigger_patterns 恒为 [] →
            #    一律判 NO_DISCRIMINATIVE_TRIGGER、只能落草稿）。
            #    政策本身**未动**（MIN_TRIGGER_CHARS 仍为 2，单字仍不得入选）；
            #    改的是分词口径 —— 详见模块 docstring §触发词口径(F11-B)。
            triggers_raw = trigger_tokens(record.user_input)
            triggers = admission.effective_trigger_patterns(triggers_raw)

            # 4) 生成工作流
            wf_id = self._derive_id(record, signature)
            wf = LearnedWorkflow(
                id=wf_id,
                name=self._derive_name(record, triggers),
                description=f"从会话 {record.session_id} 中学习得到。"
                            f"原始任务: {record.user_input[:100]}",
                task_signature=signature,
                trigger_patterns=triggers,
                steps=steps,
                source_session_id=record.session_id,
                source_user_input=record.user_input[:500],
                confidence=0.4,  # 初始置信度 = matcher.min_confidence 门槛，
                # 保证"刚学到"的 workflow 立即可被匹配执行（冷启动死锁修复：
                # 此前 0.3 < 默认 min_confidence 0.4 → 永远匹配不到 → 无法
                # 执行累积 → 恒卡 0.3，自动学习形同虚设）。首次执行成功后由
                # record_execution 单调上调；失败则温和下调。
                priority=50,
                tags=["learned"] + triggers[:3],
            )

            # 5) 准入门槛（TASK-S10-01）——不达标者**不得**进入匹配候选，
            #    也不得自动转技能，只留草稿态（仍可按 ID 人工执行）。
            decision = admission.check_structure(
                steps=wf.steps, trigger_patterns=wf.trigger_patterns)
            if not decision.admitted:
                wf.status = WorkflowStatus.DRAFT
                wf.description += (
                    f"\n【准入未通过 {'/'.join(decision.codes)}】"
                    f"{decision.reason_text}"
                    f"（草稿态：不进入匹配候选、不可自动转技能；"
                    f"可按 ID 人工执行）")
                for code in decision.codes:
                    emit_metric("yunshu_wf_admission_draft_total",
                                labels={"code": code}, kind="counter")
                track_event("wf_learned_draft", {
                    "workflow_id": wf.id, "session_id": record.session_id,
                    "codes": list(decision.codes),
                })
                logger.info(
                    "[Learner] 工作流 %s 未通过准入门槛 → 草稿态: %s",
                    wf.id, decision.reason_text)

            ctx["workflow_id"] = wf.id
            ctx["steps"] = len(steps)
            ctx["admitted"] = decision.admitted
            track_event("wf_learned", {
                "workflow_id": wf.id, "session_id": record.session_id,
                "steps": len(steps), "admitted": decision.admitted,
            })
            emit_metric("yunshu_wf_learned_total",
                        labels={"success": "true"}, kind="counter")
            logger.info("[Learner] 学习到工作流 %s (%d 步) 来自 session %s",
                        wf.id, len(steps), record.session_id)
            return wf

    # ─── 步骤提取 ───

    def _extract_steps(self, record: LearningRecord) -> List[WorkflowStep]:
        steps: List[WorkflowStep] = []
        for i, call in enumerate(record.tool_calls):
            name = call.get("name") or call.get("tool") or ""
            if not name:
                continue
            params = call.get("params") or call.get("arguments") or {}
            templated = _templatize_params(params, user_input=record.user_input)
            steps.append(WorkflowStep(
                step_id=f"step_{i+1}",
                tool_name=name,
                params_template=templated,
                output_key=f"step_{i+1}_output",
                description=call.get("description", ""),
            ))
        return steps

    # ─── ID 与名称生成 ───

    @staticmethod
    def _derive_id(record: LearningRecord, signature: str) -> str:
        """从签名 + 时间戳生成 ID"""
        import hashlib
        h = hashlib.sha256(
            f"{signature}:{record.session_id}:{record.user_input}".encode("utf-8")
        ).hexdigest()[:8]
        # 取签名第一个关键词作前缀
        prefix = signature.split("|", 1)[0][:16] or "wf"
        prefix = re.sub(r"[^a-z0-9\-]", "", prefix.lower()) or "wf"
        return f"{prefix}-{h}"

    @staticmethod
    def _derive_name(record: LearningRecord, triggers: List[str]) -> str:
        if triggers:
            return f"自动学习: {'-'.join(triggers[:3])}"
        return f"自动学习工作流 ({record.session_id[:8]})"
