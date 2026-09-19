"""技能自动分类引擎 + 持久化分类注册表

目标（用户需求）:
    1. 把技能自动分到「类」，同类在 UI 上折叠浏览；
    2. 新技能出现（创建/外来安装/导入/工作流转化/运行时扩展安装）自动归类；
    3. 现有类都不匹配的新技能 → 自动创建新类并归入，无需人工建类。

机制:
    - 确定性规则打分：种子类（人工维护关键词表）× 技能文本
      （name×3 + description×2 + tags×2 + content×1，英文按词、中文按子串计数）。
    - 命中阈值 ≥ MIN_SCORE 归入种子类；零命中但名称/标签给出
      「新概念」候选 → 自动建类（类名取自标签或非通用英文 token）；
      其余落入「未分类」，可随时人工移动。
    - 持久化注册表 data/skills_classes.json：
      {version, updated_at, assignments: {"asset:<id>|rt:<id>": 类名},
       auto_classes: {类名: {created_at, hits}}} —— 种子类不落盘（关键词在代码里），
      自动建类与人工移动落盘；两类生态（资产库 / 运行时）共用同一注册表，
      key 加命名空间前缀区分。
    - 全部写操作带进程级锁（waitress 多线程安全），读-改-写幂等。

设计约束:
    - 纯确定性、无 LLM 依赖；单测可直接验证打分/建类/移动/幂等。
    - 归错类可被人工移动覆盖（assign 后 resolve 不再自动改判）。
    - 不改技能状态/审核语义（与 advisory digest 一致，仅附加分类元数据）。
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 默认注册表：仓库根 data/skills_classes.json（运行时产物，gitignore）
_REPO_DATA = os.path.normpath(os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data"))
DEFAULT_REGISTRY_PATH = os.path.join(_REPO_DATA, "skills_classes.json")

UNCLASSIFIED = "未分类"          # 虚拟类：未命中任何规则/新概念
SEED_NAMES: List[str] = []       # 运行时由 SEED_CLASSES 填充
_VERSION = 1
_REG_LOCK = threading.RLock()
_MAX_CONTENT = 3000              # 正文参与打分的最长长度（防超长注入失控）
_MIN_SCORE = 2                   # 种子类归属阈值（加权文本命中 ≥2）
_ASCII_TOKEN = re.compile(r"[a-z0-9]{2,}")
_CJK = re.compile(r"[\u4e00-\u9fff]+")
# 名称/标签里视为“通用词”的英文 token，不作为新概念建类依据
_GENERIC_TOKENS = {
    "skill", "skills", "test", "demo", "sample", "probe", "mock", "smoke",
    "ui", "new", "example", "the", "and", "for", "with", "from", "into",
    "v1", "v2", "ext", "self", "my", "tool", "run", "auto", "data",
    "script", "helper", "helper", "probe", "notes", "feedback", "weather",
    # 注：digest 仅为通用词 token（旧评审语义名遗留的类名黑词，非语义使用点；
    # TASK-S0-01 术语纪律：不改分类行为，仅保留注释说明）
    "digest", "redraft", "audit", "clone", "version", "eval", "adv", "req",
    "sma", "smb", "pub", "last", "legacy", "c3", "cr", "knob", "ver",
    "selftest", "meta", "gen", "sync", "watch", "sys", "util", "utils",
    "core", "base", "api", "app", "admin", "agent", "bot", "chat", "page",
    "guide", "craft", "maker", "assistant", "generator", "master", "lab",
}
# 英文 token 触发「自动建类」的最短长度（防测试残留/短缩写产生碎片类）
_AUTO_TOKEN_MIN_LEN = 5
# 英文新概念 → 更友好的中文类名（可选；缺省用原 token）
TOPIC_NAMES = {
    "meditation": "冥想", "mindfulness": "冥想正念",
    "resume": "简历", "interview": "面试",
    "finance": "财务", "stock": "股票金融", "trading": "交易",
    "health": "健康", "fitness": "健身",
    "travel": "旅行", "recipe": "菜谱", "cooking": "下厨",
    "music": "音乐", "video": "视频处理", "photo": "图片处理",
    "weather": "天气", "calendar": "日程", "schedule": "日程",
    "translation": "翻译", "writing": "写作",
}

# ── 种子分类（关键词为小写；英文按整词匹配，中文按子串匹配）──────────
SEED_CLASSES: List[Dict[str, Any]] = [
    {"name": "交流与人格", "keywords": [
        "情感", "反思", "感知", "表达", "风格", "语气", "人格", "对话",
        "共情", "情绪", "主动", "自省", "幽默", "亲和", "同理", "态度",
        # 【简易】裸「建议」已移除（2026-09-19，TASK-02）：通用词 ——
        #   "建议 / 给出建议 / 后续建议"在代码、文档、规划各域都出现，不属"交流与人格"专属。
        #   覆盖来源：本域仍由 `情感/反思/感知/表达/风格/语气/人格/对话/共情/情绪/主动/
        #   自省/幽默/亲和/同理/态度` 与 `emotion/reflect/personality/proactive/empathy` 等承担。
        #   回归见 `test_generic_probe_words_do_not_claim_any_domain`。
        "emotion", "suggestion", "reflect", "context", "personality",
        "proactive", "expression", "empathy", "mood", "tone",
    ]},
    {"name": "记忆与知识", "keywords": [
        "记忆", "摘要", "知识", "归档", "检索", "回忆", "压缩", "总结", "归纳",
        "备忘", "知识库", "长期记忆", "memory", "summary", "recall",
        # 注：digest 为「记忆与知识」英文关键词（摘要语义，非云枢评审语义；
        # TASK-S0-01 术语纪律：不改分类行为，仅保留注释说明）
        "knowledge", "archive", "summarize", "digest",
    ]},
    {"name": "安全与合规", "keywords": [
        "安全", "守护", "过滤", "合规", "敏感", "危险", "审查", "越狱", "风险",
        "脱敏", "防火墙", "拦截", "safety", "guard", "security", "filter",
        "compliance", "danger", "block",
    ]},
    {"name": "语音与多媒体", "keywords": [
        "语音", "声音", "音频", "视频", "图像", "图片", "朗读", "播放",
        "媒体", "画面", "语音识别", "voice", "audio", "video", "image",
        "ocr", "tts", "speech", "media", "photo",
        # 【不易】裸「识别」已移除（2026-09-19）：它是个通用词（"识别不变量/识别风险/
        #   识别需求"都命中），却单独决定语音域 ⇒ 实测把编码方法论技能「易之三义」
        #   （描述里只有"约束识别"这一处命中）判成语音与多媒体。
        #   与当年移除 `markdown`（见 test_ui_skill_not_misclassified_by_doc_noise）
        #   同一手法：**噪音关键词不得独立决定一个域**。语音语义仍由「语音识别」
        #   及 voice/audio/ocr 等复合词/英文词覆盖（回归见
        #   test_bare_identify_word_does_not_claim_voice_domain）。
    ]},
    {"name": "邮件与通讯", "keywords": [
        "邮件", "通讯", "消息", "通知", "收件", "发件", "推送", "提醒", "短信",
        "email", "mail", "message", "notify", "notification", "sms",
    ]},
    {"name": "文档与办公", "keywords": [
        "表格", "笔记", "纪要", "起草",
        # 【简易】裸「文档」「报告」「文件」「整理」已移除（2026-09-19，TASK-02）。
        #   判定依据（写进 `RETIRED_GENERIC_KEYWORDS` 的通用词客观标准）：
        #   **一个词若在多个不同语义域的技能文本里都会出现，它不得单独决定域归属。**
        #     - 「文档」："流程文档编写 / 输出文档 / 查阅外部文档 / SKILL.md 文档" —— 代码、
        #       写作、验证、办公四域都会出现。实测把写作方法论技能 `writing-skills`
        #       （描述里"流程文档编写"+"SKILL.md 文档"两次命中 ⇒ 3 分）从「翻译与写作」
        #       拉到「文档与办公」，与 `writing`(3 分) 打平后靠表序胜出 —— 用户问的第二个
        #       "为什么"。
        #     - 「报告」："输出报告 / 报告缺陷 / 生成报告" —— 代码、安全、办公三域都会出现
        #       （实测曾是「代码与工程」类技能的次高类）。
        #     - 「文件」："读取文件 / 输出文件 / 列出目录下文件" —— 代码、系统、办公都会出现。
        #     - 「整理」："整理输出 / 整理数据 / 整理会话记录" —— 各域都会出现。
        #   覆盖来源：本域仍由 `表格/笔记/纪要/起草` 与
        #   `office/excel/ppt/document/note/report/pdf/word/resume/letter` 承担；
        #   英文走整词匹配、不误伤，`document`/`report` 完整保留了"文档/报告"的语义。
        #   影响面（实测，勿凭印象）：对现存 113 条台账做**规则复算**，这四处移除只改 2 条
        #   （`wf-f19dc52c-skill` 两个 key：文档与办公 → 未分类，该技能是退化的
        #   工作流学习产物）；`writing-skills` 也受影响，但它两条都有人工钉住，可见结果不变。
        #   回归见 `test_writing_skills_not_dragged_into_office_domain` 与
        #   `test_generic_probe_words_do_not_claim_any_domain`（都在
        #   `tests/unit/test_skills_classifier.py`）。
        "office", "excel", "ppt", "document", "note", "report",
        "pdf", "word", "resume", "letter",
    ]},
    {"name": "代码与工程", "keywords": [
        "代码", "编程", "开发", "构建", "编译", "脚本", "调试", "重构",
        "函数", "接口", "代码审查", "code", "coding", "program", "sdk",
        "python", "javascript", "typescript", "git", "compile", "debug",
        "refactor", "function", "api", "cli",
        # 【变易】补中文动名词「编码」（2026-09-19）：中文技术文本里"编码前/编码时"
        #   比"代码"更常出现在**描述**里（而描述正是运行时行唯二可用的字段）；
        #   补上后「易之三义」（"编码前必输出…"）能凭 2 分归入本域。
        #   ⚠️ 注释修订（2026-09-19，TASK-02 复核时实测）：原文写的是"能凭 2 分与 analysis
        #   **并列**并按时序归回本域"——"并列"意味着那是 **0 分差平局**：既靠 `SEED_CLASSES`
        #   表序（「代码与工程」在「数据分析与可视化」之前）才判对，又属**单点依赖**
        #   （移除 `编码` 即翻回「数据分析与可视化」）。真正的止血是 `_token_count` 的
        #   标识符收紧（`<san_yi_analysis>` 不再贡献 `analysis`）：现在该技能的运行时行
        #   是 2:0 而非 2:2。回归见 `test_easy_three_meanings_beats_analysis_tag_noise`
        #   与 `test_bare_identify_word_does_not_claim_voice_domain`。
        "编码",
    ]},
    {"name": "网络与搜索", "keywords": [
        "网络", "搜索", "抓取", "网页", "爬虫", "查询", "联网", "浏览器",
        "web", "search", "fetch", "crawl", "http", "url", "scrape",
        "browser", "internet",
    ]},
    {"name": "数据分析与可视化", "keywords": [
        "数据", "图表", "统计", "可视化", "报表", "建模", "指标",
        # 【简易】裸「分析」已移除（2026-09-19，TASK-02）：通用词 ——
        #   "分析需求 / 分析不变量 / 分析风险 / analysis 阶段"遍布各域（TASK-02 §4 明确把
        #   「分析」列为通用词范例）。实测它在「易之三义」里凭标签名 `<san_yi_analysis>`
        #   贡献 2 分、与「编码」打平，是那条归类 0 分差平局的直接成因。
        #   覆盖来源：本域仍由 `数据/图表/统计/可视化/报表/建模/指标` 与
        #   `data/analysis/chart/plot/statistics/visualization/pandas/numpy/metric/dashboard`
        #   承担（英文 `analysis` 完整保留"分析"语义）。
        #   回归见 `test_generic_probe_words_do_not_claim_any_domain`。
        "data", "analysis", "chart", "plot", "statistics", "visualization",
        "pandas", "numpy", "metric", "dashboard",
    ]},
    {"name": "工作流与自动化", "keywords": [
        "工作流", "自动化", "编排", "定时", "调度", "批处理",
        "workflow", "automation", "schedule", "cron", "pipeline", "job",
        "orchestrat", "batch",
    ]},
    {"name": "翻译与写作", "keywords": [
        "翻译", "写作", "润色", "文案", "改写", "语法", "校对", "措辞",
        "translate", "translation", "writing", "polish", "copywrite",
        "grammar", "proofread",
    ]},
]
for _c in SEED_CLASSES:
    SEED_NAMES.append(_c["name"])

# ── 关键词角色声明（结构性护栏的数据源）─────────────────────────────────
#
# 【为什么需要这份声明】TASK-02 的教训：一次误判（裸「识别」把编码技能「易之三义」判成
#   「语音与多媒体」）只能靠人肉复现才发现，因为**词表里没有任何东西标注"这个词够不够格
#   单独决定一个域"**。声明把这件事显式化：
#
#   - `DOMAIN_ANCHORS`：当前词表中**允许单独决定域归属**的关键词（等价于"域锚点"）。
#     判定标准（客观、可复核）：该词**单独出现**在一段技能文本里时，能把该技能判回本域，
#     且它**不会**同时出现在多个语义域的常用表述中。
#   - `RETIRED_GENERIC_KEYWORDS`：历史上曾在词表里、但被判定为**通用词**而移除的词。
#     通用词的客观标准（写死在此，后续评审按它判）：
#       **一个词若在多个不同语义域的技能文本里都会出现**
#       （如「识别/分析/文件/报告/管理/生成/整理/处理/文档」），
#       那么它不得单独决定域归属 —— 它顶多能作为加分项，不能当唯一依据。
#
# 结构性护栏测试（`tests/unit/test_skills_classifier.py::TestKeywordGuardrails`）断言：
#   ① `DOMAIN_ANCHORS` 的**每一个**关键词，仅凭自己就能判回它所属的类；
#   ② `RETIRED_GENERIC_KEYWORDS` 的**每一个**词，都不能单独判出任何类；
#   ③ 任何关键词都不得同时属于两个类（否则归属由表序决定）。
# ⇒ 往 `SEED_CLASSES` 里加词的人必然触到 ①②③，无法再"随手加一个通用词"。
DOMAIN_ANCHORS: Dict[str, List[str]] = {
    c["name"]: list(c["keywords"]) for c in SEED_CLASSES
}

# 已退役的通用词：移除时必须在 `SEED_CLASSES` 原位留注释（理由/覆盖来源/回归测试名）
RETIRED_GENERIC_KEYWORDS = {
    "识别": "通用词（识别不变量/识别风险/识别需求）；已移除，语义改由「语音识别」+ voice/audio/ocr 承担",
    "markdown": "通用词（正文/标签里的文档格式名）；已移除，改由 document/pdf/word 等承担",
    "文档": "通用词（流程文档/输出文档/查阅外部文档；代码、写作、验证、办公四域都会出现）；"
            "已移除，改由 document/表格/笔记/纪要/起草/整理 承担",
    "报告": "通用词（输出报告/报告缺陷/生成报告；代码、安全、办公三域都会出现）；"
            "已移除，改由 report/表格/纪要 承担",
    "文件": "通用词（读取文件/输出文件/列出目录下文件；代码、系统、办公三域都会出现）；"
            "已移除，改由 document/pdf/word 承担",
    "分析": "通用词（分析需求/分析不变量/分析风险）；已移除，改由 data/analysis/chart/统计/图表 承担",
    "整理": "通用词（整理输出/整理数据/整理会话记录；各域都会出现）；"
            "已移除，改由 document/表格/笔记/纪要/起草 承担",
    "建议": "通用词（建议/给出建议/后续建议；代码、文档、规划各域都会出现）；"
            "已移除，改由 suggestion/主动/态度 等承担",
}

# 通用词探针：结构性护栏用它做"注入攻击" —— 把通用词塞进**别的域**的典型文本，
# 断言归类不被夺走。这些词大多不在词表里（正是目的：证明它们已被排除）。
GENERIC_PROBE_WORDS: List[str] = [
    "识别", "分析", "文件", "报告", "管理", "生成", "整理", "处理", "文档", "建议",
]


# ── 纯打分（无副作用，便于单测）────────────────────────────────────────

def _field_text(name: str, description: str, content: str, tags) -> str:
    """加权拼接技能文本（name×3 / description×2 / tags×2 / content×1）。"""
    name = str(name or "")
    desc = str(description or "")
    body = str(content or "")[:_MAX_CONTENT]
    tag_txt = " ".join(str(t) for t in (tags or []) if str(t).strip())
    parts = [name] * 3
    if desc.strip():
        parts += [desc] * 2
    if tag_txt.strip():
        parts += [tag_txt] * 2
    if body.strip():
        parts.append(body)
    return "\n".join(parts).lower()


def _token_count(text: str, kw: str) -> int:
    """英文整词 / 中文子串 出现次数（每关键词至多计 3，防长正文重复刷分）。

    【不易】英文关键词**不得在「下划线连写的复合标识符」内部命中**（2026-09-19 收紧）。
    原因：`_ASCII_TOKEN` 把 `from_knowledge` 切成 `from`/`knowledge`、把 `<san_yi_analysis>`
    切成 `san`/`yi`/`analysis` —— 于是**词表名与标签名被当成语义命中**。
    实测两处误报（回归见 `test_ascii_keyword_does_not_match_inside_identifier`）：
      1. `pd-*-skill` 系列技能的 provenance 标签 `from_knowledge` 让 5 条技能白拿
         「记忆与知识」2 分（`asset:pd-executing-plans-95cbf64a-skill` 等）；
      2. 编码方法论技能「易之三义」的描述里写了标签 `<san_yi_analysis>`，
         让「数据分析与可视化」白拿 2 分，与「编码」打平后**靠 `SEED_CLASSES` 表序**才判回
         「代码与工程」——即所谓"修好了"其实是 0 分差平局（见 :136-141 的注释修订）。
    代价（已权衡）：`web_search`、`skill.md` 这类复合名里的词也不再命中，
    但同一份文本的**散文部分**通常已含这些词；且本改动方向是**收紧而非放宽**。
    """
    if not kw:
        return 0
    if not kw.isascii():
        return min(text.count(kw), 3)
    n = 0
    for m in _ASCII_TOKEN.finditer(text):
        if m.group(0) != kw:
            continue
        i, j = m.start(), m.end()
        # 紧邻 `_` ⇒ 该词是更长标识符的一段（`from_knowledge` / `<san_yi_analysis>`），不算命中
        if (i > 0 and text[i - 1] == "_") or (j < len(text) and text[j] == "_"):
            continue
        n += 1
    return min(n, 3)


def classify_fields(name: str = "", description: str = "",
                    content: str = "", tags: Optional[list] = None
                    ) -> Dict[str, Any]:
    """规则打分：返回 {class: 类名|None, score, matched, auto_name}。

    - best 种子类命中分数 ≥ _MIN_SCORE → 归入该种子类；
    - 零命中但名称/标签含“新概念”候选（非通用英文 token 或非种子相关标签）
      → 建议自动建类 auto_name；
    - 否则未分类。
    """
    hay = _field_text(name, description, content, tags)
    best, best_score, matched = None, 0, []
    for cls in SEED_CLASSES:
        s = sum(_token_count(hay, kw) for kw in cls["keywords"])
        if s > best_score:
            best_score, best = s, cls["name"]
        if s > 0:
            matched.append(cls["name"])
    if best is not None and best_score >= _MIN_SCORE:
        return {"class": best, "score": best_score,
                "matched": sorted(set(matched)), "auto_name": None}

    # 新概念候选：非通用英文 token（来自名称）
    auto_name = None
    if best is None:
        cjk = _CJK.findall(str(name or ""))
        # 名称里未被种子覆盖的中文词组：直接取名称（截短）作为新类名
        name_txt = str(name or "").strip()
        if name_txt and not any(_token_count(name_txt, k)
                                for c in SEED_CLASSES for k in c["keywords"]):
            cand = "".join(cjk) if cjk else ""
            if cand and len(cand) <= 12 and not _is_seed_like(cand):
                auto_name = cand
        if auto_name is None:
            toks = [t for t in _ASCII_TOKEN.findall(str(name or "").lower())
                    if len(t) >= _AUTO_TOKEN_MIN_LEN
                    and t not in _GENERIC_TOKENS
                    and not _is_seed_like(t)]
            if toks:
                t = toks[0]
                auto_name = TOPIC_NAMES.get(t, t)
    return {"class": None, "score": best_score if best else 0,
            "matched": sorted(set(matched)), "auto_name": auto_name}


def _is_seed_like(text: str) -> bool:
    """候选名与某种子类名重合/含种子类名 → 不新建重复类。"""
    t = text.lower()
    return any(t == n.lower() or n.lower() in t or t in n.lower()
               for n in SEED_NAMES)


# ── 持久化注册表 ────────────────────────────────────────────────────────

class SkillClassRegistry:
    """分类注册表：assignments + auto_classes，读-改-写带进程锁。

    key 命名：资产库 "asset:<skill_id>"；运行时 "rt:<skill_id>"。
    """

    def __init__(self, path: Optional[str] = None):
        self.path = path or DEFAULT_REGISTRY_PATH
        self._state: Optional[Dict[str, Any]] = None

    # ── 状态读写 ──
    def _load(self) -> Dict[str, Any]:
        try:
            if os.path.exists(self.path):
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict) and isinstance(
                        data.get("assignments"), dict):
                    return data
        except Exception as e:  # noqa: BLE001 坏文件降级为空状态
            logger.warning("[Categorizer] 注册表读取失败 %s: %s", self.path, e)
        return {"version": _VERSION, "updated_at": "", "assignments": {},
                "auto_classes": {}}

    def _save(self, state: Dict[str, Any]) -> None:
        state["version"] = _VERSION
        state["updated_at"] = datetime.now().isoformat(timespec="seconds")
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)
        except Exception as e:  # noqa: BLE001 写失败仅告警（分类是附加能力）
            logger.warning("[Categorizer] 注册表写入失败 %s: %s", self.path, e)

    # ── 对外查询/变更 ──
    def snapshot(self) -> Dict[str, Any]:
        with _REG_LOCK:
            return self._load()

    def assignment(self, key: str) -> Optional[str]:
        with _REG_LOCK:
            st = self._load()
            return st.get("assignments", {}).get(key)

    def assign(self, key: str, cls_name: str) -> str:
        """人工移动：把技能归入指定类并标记 manual（后续自动重判不再覆盖）。

        【不易】必须**同时钉住两个命名空间的 key**（2026-09-19，TASK-02 修复）。
        根因：同一技能在「技能资产库(asset:)」与「运行时(rt:)」各有一条记录，
        `resolve()` 对**非 manual** 的既有归类允许"置信命中即覆盖"。只钉 asset 的话，
        下一次 `GET /api/skills`（技能库页的数据源）走 `resolve('rt:*')` 会按关键词重新打分，
        把人工选择静默覆盖回旧类 —— 技能中心显示新类、技能库页显示旧类，两视图分叉。
        历史实证：`writing-skills` 曾被移到「代码与工程」，但只有 `asset:` 侧被钉住，
        `rt:` 侧随后被自动判定回滚（提交 `f8443cff` 的意图，但 `assign` 本身当时没落地）。
        ⇒ 只要**对侧 key 已存在**（说明该技能确实有两套记录），本方法就一并钉住它。
        """
        cls_name = str(cls_name or UNCLASSIFIED).strip()
        with _REG_LOCK:
            st = self._load()
            assignments = st.setdefault("assignments", {})
            assignments[key] = cls_name
            manual = set(st.setdefault("manual", []))
            manual.add(key)
            # 对侧命名空间：仅当它已经有记录时才钉（不凭空造出第二条记录）
            ns, _, sid = key.partition(":")
            if sid:
                other = f"{'rt' if ns == 'asset' else 'asset'}:{sid}"
                if other in assignments and other != key:
                    assignments[other] = cls_name
                    manual.add(other)
            st["manual"] = sorted(manual)
            self._save(st)
        return cls_name

    def inconsistent_pairs(self) -> List[Dict[str, Any]]:
        """自愈检查（**只报告，不自动改**）：同一技能的 asset/rt 归类不一致且未人工钉住。

        【为什么只告警不自动改】自动改会把"两条记录都错了"这种**自动分类的真实缺陷**
        掩盖成"一致了"。报告出来交给人看，才会去修规则表 —— 这正是 TASK-02 的立场。
        消费方：`agent/skills_mgmt/cleanup.py` 的治理巡检 / 启动自检（告警即可，勿阻断）。
        """
        with _REG_LOCK:
            st = self._load()
            assignments = st.get("assignments", {})
            manual = set(st.get("manual", []))
        by_sid: Dict[str, Dict[str, str]] = {}
        for key, cls in assignments.items():
            ns, _, sid = key.partition(":")
            by_sid.setdefault(sid, {})[ns] = cls
        out = []
        for sid, byns in sorted(by_sid.items()):
            if "asset" in byns and "rt" in byns and byns["asset"] != byns["rt"]:
                keys = {f"asset:{sid}", f"rt:{sid}"}
                out.append({
                    "skill_id": sid, "asset": byns["asset"], "rt": byns["rt"],
                    "pinned": sorted(k for k in keys if k in manual),
                    "pinned_both": keys <= manual,
                })
        return out

    def same_name_conflicts(self) -> List[Dict[str, Any]]:
        """自愈检查（**只报告**）：同一个技能名、不同实例被分到了不同的类。

        【为什么单列一条】`_reconcile_same_skill` 只覆盖**同名 skill id** 的双生态；
        而素材蒸馏会给同一份素材生成 `pd-<语义名>-<哈希>-skill` 的多份实例，
        用户看到的是"同一个名字出现在两个类下面"。这类不一致必须人工定案。
        """
        import re as _re
        pd_id = _re.compile(r"^pd-(.+)-[0-9a-f]{8}-skill$")
        with _REG_LOCK:
            st = self._load()
            assignments = st.get("assignments", {})
        by_name: Dict[str, Dict[str, List[str]]] = {}
        for key, cls in assignments.items():
            sid = key.partition(":")[2].lower()
            m = pd_id.match(sid)
            nm = m.group(1) if m else sid
            by_name.setdefault(nm, {}).setdefault(cls, []).append(key)
        return [{"name": nm, "by_class": {c: sorted(k) for c, k in v.items()}}
                for nm, v in sorted(by_name.items()) if len(v) > 1]

    def unset_manual(self, key: str) -> bool:
        """取消人工钉住（"恢复自动分类"）：只从 manual 集合移除，**保留**当前归类

        【为什么保留归类而不清空】清空会让该技能在下次 `resolve` 时重新打分 —— 有可能又
        回到人工纠正之前那个不合理的结果。保留归类 + 解除钉住 = "先维持现状，之后若内容域
        变化（置信命中不同）再自动跟随"，这是"撤销人工干预"最不意外的语义。
        """
        with _REG_LOCK:
            st = self._load()
            manual = set(st.get("manual", []))
            if key not in manual:
                return False
            manual.discard(key)
            st["manual"] = sorted(manual)
            self._save(st)
        return True

    def mirror(self, src_key: str, dst_key: str) -> bool:
        """把 src 的分类同步到同技能的另一生态 key（asset↔rt 同名技能）。

        规则：dst 不存在或 dst 是人工移动过（manual）→ 不动；
        否则 dst 自动归类跟随 src（同一技能两侧保持一致）。
        """
        if not src_key or not dst_key or src_key == dst_key:
            return False
        with _REG_LOCK:
            st = self._load()
            assignments = st.setdefault("assignments", {})
            if src_key not in assignments or dst_key not in assignments:
                return False
            if dst_key in set(st.get("manual", [])):
                return False
            if assignments[src_key] == assignments[dst_key]:
                return False
            assignments[dst_key] = assignments[src_key]
            self._save(st)
            return True

    def auto_class_names(self) -> set:
        """当前已自动建类的类名集合（供 UI 打「自动建类」徽标）。"""
        with _REG_LOCK:
            return set(self._load().get("auto_classes", {}).keys())

    @staticmethod
    def _reconcile_same_skill(st: Dict[str, Any], key: str) -> bool:
        """同名技能双生态（asset:/rt:）自动归类收敛，返回是否发生了改写。

        同一技能 id 同时存在于「技能资产库(asset:)」与「运行时(rt:)」时，两侧
        自动归类必须一致，否则 技能资产库 与 技能面板(LLM 技能) 两个视图会分叉。
        权威方向：运行时(rt:) 使用 名称/描述（技能意图）；资产侧还会掺入正文/标签
        噪音（如正文提到“查阅外部文档”、标签 markdown 会误导打分）——因此无论哪侧
        先/后落盘，asset 的自动归类都对齐到 rt（rt 不存在或 rt 为人工移动时不动 asset；
        人工移动过的 key 一律不动）。
        """
        assignments = st.setdefault("assignments", {})
        manual = set(st.get("manual", []))
        if key.startswith("asset:"):
            sid = key.split(":", 1)[1]
            rk = f"rt:{sid}"
            if rk in assignments and rk not in manual \
                    and assignments[rk] != assignments[key]:
                assignments[key] = assignments[rk]   # asset 对齐 rt（rt 意图为准）
                return True
        elif key.startswith("rt:"):
            sid = key.split(":", 1)[1]
            ak = f"asset:{sid}"
            if ak in assignments and ak not in manual \
                    and assignments[ak] != assignments[key]:
                assignments[ak] = assignments[key]   # asset 对齐 rt
                return True
        return False

    def resolve(self, key: str, *, name: str = "", description: str = "",
                content: str = "", tags: Optional[list] = None) -> str:
        """取技能分类；未记录则自动判定并落盘。

        - 人工移动过的（manual）→ 永远保留人工选择；
        - 已有自动归类 → 仅在“新判定为置信种子命中”时才覆盖（内容域变化重分类）；
          弱判定（未分类/名称派生弱类）不把已归类技能踢走（不降级）。
        """
        with _REG_LOCK:
            st = self._load()
            assignments = st.setdefault("assignments", {})
            manual = set(st.get("manual", []))
            if key in assignments and key in manual:
                return assignments[key]
            verdict = classify_fields(name, description, content, tags)
            confident = verdict.get("class")  # 种子类置信命中
            cls_name = confident
            # 运行时生态弱判定（无置信种子命中：未分类/名称派生弱类）→
            # 回退同名资产分类（asset: 为准，信息更全；资产人工移动过则不受影响）
            asset_fallback = False
            if key.startswith("rt:") and confident is None:
                sid = key.split(":", 1)[1]
                akey = f"asset:{sid}"
                if akey in assignments and akey not in manual:
                    cls_name = assignments[akey]
                    asset_fallback = True
            if cls_name is None and not asset_fallback:
                auto_name = verdict.get("auto_name") or UNCLASSIFIED
                if auto_name != UNCLASSIFIED:
                    st.setdefault("auto_classes", {}).setdefault(
                        auto_name, {"created_at": datetime.now().isoformat(
                            timespec="seconds"), "hits": 0})
                    st["auto_classes"][auto_name]["hits"] = \
                        st["auto_classes"][auto_name].get("hits", 0) + 1
                cls_name = auto_name
            if key in assignments:
                if confident is None and not asset_fallback:
                    return assignments[key]  # 弱判定不覆盖既有归类
                assignments[key] = cls_name  # 置信命中/资产回退 → 落盘
            else:
                assignments[key] = cls_name
            # 同名双生态收敛：asset:/rt: 自动归类保持一致（人工不动）
            self._reconcile_same_skill(st, key)
            self._save(st)
            return cls_name

    def run_auto(self, skills: List[Dict[str, Any]], ns: str = "asset",
                 force_unclassified: bool = False) -> Dict[str, Any]:
        """对一组技能批量自动归类；返回统计。

        - 默认只补未记录项（人工/已有归类不受影响）；
        - force_unclassified=True 时，落在「未分类」的存量也重新判定一次
          （仍尊重人工移动 manual）。
        """
        classified, created, by_class = 0, 0, {}
        with _REG_LOCK:
            st = self._load()
            assignments = st.setdefault("assignments", {})
            manual = set(st.get("manual", []))
            auto_classes = st.setdefault("auto_classes", {})
            for s in skills:
                sid = str(s.get("id", ""))
                if not sid:
                    continue
                key = f"{ns}:{sid}"
                if key in assignments:
                    if key in manual:
                        by_class[assignments[key]] = by_class.get(
                            assignments[key], 0) + 1
                        continue
                    if not (force_unclassified
                            and assignments[key] == UNCLASSIFIED):
                        by_class[assignments[key]] = by_class.get(
                            assignments[key], 0) + 1
                        continue
                verdict = classify_fields(
                    s.get("name", ""), s.get("description", ""),
                    s.get("content", "") or s.get("script", ""),
                    s.get("tags"))
                cls_name = verdict.get("class")
                if cls_name is None:
                    auto_name = verdict.get("auto_name") or UNCLASSIFIED
                    if auto_name != UNCLASSIFIED:
                        auto_classes.setdefault(auto_name, {
                            "created_at": datetime.now().isoformat(
                                timespec="seconds"), "hits": 0})
                        auto_classes[auto_name]["hits"] = \
                            auto_classes[auto_name].get("hits", 0) + 1
                        created += 1
                    cls_name = auto_name
                assignments[key] = cls_name
                # 同名双生态收敛：asset:/rt: 保持一致（人工不动）
                self._reconcile_same_skill(st, key)
                final_cls = assignments[key]
                by_class[final_cls] = by_class.get(final_cls, 0) + 1
                classified += 1
            self._save(st)
        return {"processed": len(skills), "classified": classified,
                "created_classes": created,
                "by_class": dict(sorted(by_class.items(),
                                        key=lambda kv: -kv[1]))}

    def group_summary(self, skills: List[Dict[str, Any]], ns: str = "asset",
                      ) -> Dict[str, Any]:
        """给出一组分好类的技能视图：按类分组 + 元信息（不落盘，纯读）。"""
        with _REG_LOCK:
            st = self._load()
            assignments = st.get("assignments", {})
            auto_classes = st.get("auto_classes", {})
        buckets: Dict[str, List[Dict[str, Any]]] = {}
        for s in skills:
            sid = str(s.get("id", ""))
            if not sid:
                continue
            cls_name = assignments.get(f"{ns}:{sid}", UNCLASSIFIED)
            buckets.setdefault(cls_name, []).append(s)
        groups = []
        for cls_name, members in sorted(buckets.items(),
                                        key=lambda kv: (-len(kv[1]),
                                                        kv[0])):
            groups.append({
                "name": cls_name,
                "count": len(members),
                "auto": cls_name in auto_classes,
                "created_at": (auto_classes.get(cls_name) or {})
                .get("created_at", ""),
                "skills": members,
            })
        return {"total": len(skills), "groups": groups,
                "auto_classes": sorted(auto_classes.keys())}
