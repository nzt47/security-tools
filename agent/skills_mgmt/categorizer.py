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
        "情感", "建议", "反思", "感知", "表达", "风格", "语气", "人格", "对话",
        "共情", "情绪", "主动", "自省", "幽默", "亲和", "同理", "态度",
        "emotion", "suggestion", "reflect", "context", "personality",
        "proactive", "expression", "empathy", "mood", "tone",
    ]},
    {"name": "记忆与知识", "keywords": [
        "记忆", "摘要", "知识", "归档", "检索", "回忆", "压缩", "总结", "归纳",
        "备忘", "知识库", "长期记忆", "memory", "summary", "recall",
        "knowledge", "archive", "summarize", "digest",
    ]},
    {"name": "安全与合规", "keywords": [
        "安全", "守护", "过滤", "合规", "敏感", "危险", "审查", "越狱", "风险",
        "脱敏", "防火墙", "拦截", "safety", "guard", "security", "filter",
        "compliance", "danger", "block",
    ]},
    {"name": "语音与多媒体", "keywords": [
        "语音", "声音", "音频", "视频", "图像", "图片", "识别", "朗读", "播放",
        "媒体", "画面", "语音识别", "voice", "audio", "video", "image",
        "ocr", "tts", "speech", "media", "photo",
    ]},
    {"name": "邮件与通讯", "keywords": [
        "邮件", "通讯", "消息", "通知", "收件", "发件", "推送", "提醒", "短信",
        "email", "mail", "message", "notify", "notification", "sms",
    ]},
    {"name": "文档与办公", "keywords": [
        "文档", "表格", "报告", "笔记", "纪要", "起草", "整理", "文件",
        "office", "excel", "ppt", "document", "note", "report",
        "pdf", "word", "resume", "letter",
    ]},
    {"name": "代码与工程", "keywords": [
        "代码", "编程", "开发", "构建", "编译", "脚本", "调试", "重构",
        "函数", "接口", "代码审查", "code", "coding", "program", "sdk",
        "python", "javascript", "typescript", "git", "compile", "debug",
        "refactor", "function", "api", "cli",
    ]},
    {"name": "网络与搜索", "keywords": [
        "网络", "搜索", "抓取", "网页", "爬虫", "查询", "联网", "浏览器",
        "web", "search", "fetch", "crawl", "http", "url", "scrape",
        "browser", "internet",
    ]},
    {"name": "数据分析与可视化", "keywords": [
        "数据", "分析", "图表", "统计", "可视化", "报表", "建模", "指标",
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
    """英文整词 / 中文子串 出现次数（每关键词至多计 3，防长正文重复刷分）。"""
    if not kw:
        return 0
    if kw.isascii():
        tokens = _ASCII_TOKEN.findall(text)
        return min(sum(1 for t in tokens if t == kw), 3)
    return min(text.count(kw), 3)


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
        """人工移动：把技能归入指定类并标记 manual（后续自动重判不再覆盖）。"""
        cls_name = str(cls_name or UNCLASSIFIED).strip()
        with _REG_LOCK:
            st = self._load()
            st.setdefault("assignments", {})[key] = cls_name
            manual = set(st.setdefault("manual", []))
            manual.add(key)
            st["manual"] = sorted(manual)
            self._save(st)
        return cls_name

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
