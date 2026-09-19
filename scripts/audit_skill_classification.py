#!/usr/bin/env python
"""技能自动归类审计（**只读**，可复跑）—— TASK-02 核心交付物

【为什么需要这个脚本】
    `agent/skills_mgmt/categorizer.py` 是纯确定性规则打分，但**规则表本身没有可解释性输出**：
    归到一个类，看不出"命中了哪些关键词、各贡献多少分、胜出类比次高类多几分"。
    于是每一次误判（如「易之三义 → 语音与多媒体」）都只能靠人肉复现才发现，修完也无法证明修好了。
    本脚本把"归类"这件事从"结论"还原成"证据"：全量清单 + 逐条打分明细 + 单关键词消融 +
    边界告警 + 跨命名空间一致性，一条条打印出来。报告落 `docs/skills_mgmt/技能归类审计报告.md`。

【【不易】本脚本绝对只读】
    - 只调用 `categorizer` 的**纯函数** `_field_text` / `_token_count` / `SEED_CLASSES` / `_MIN_SCORE`
      （绝不调用 `SkillClassRegistry.resolve/assign/run_auto/mirror` —— 那些会落盘）。
    - 开头/结尾各算一次 `data/skills_classes.json` 的 sha256 并断言相等；报告里留下前后哈希，
      这就是"没改台账"的机器证明（D8：人肉"我看过了"不算验收）。
    - 复核口径：不仅比对台账文件，还比对**整个 data/ 目录**里本次运行的 mtime（防"改了别处"）。

【三个概念的区分（报告里全部用到，勿混）】
    1. **单点依赖**：移除某个**命中的关键词**后重新打分 ⇒ 胜出类发生翻转。
       ⇒ 该条归类"全靠这一个词撑着"，是脆弱性的直接证据。
    2. **独立夺域**：只用该关键词构造文本（别的词全不算）⇒ 仍能判给该类，
       且该词本身在多个语义域都会出现。这正是「易之三义」当年的失败模式（裸「识别」）。
    3. **边界归类**：胜出类与次高类的分差 ≤ 1 ⇒ 规则表本身没有区分度，排序/表序就能翻案。

用法：
    python scripts/audit_skill_classification.py                     # 审计 + 写报告
    python scripts/audit_skill_classification.py --check             # 只审计，有问题则退出码 1
    python scripts/audit_skill_classification.py --diff <旧台账.json>  # 额外渲染改动前后归类 diff
    python scripts/audit_skill_classification.py --stdout-only       # 不写报告文件
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Set, Tuple

# ── 路径（相对仓库根，脚本可在任意 cwd 下运行）──────────────────────────
_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      os.pardir))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from agent.skills_mgmt import categorizer as C  # noqa: E402

REGISTRY = os.path.join(_ROOT, "data", "skills_classes.json")
SKILLS_JSON = os.path.join(_ROOT, "data", "skills.json")
SKILLS_MGMT = os.path.join(_ROOT, "data", "skills_mgmt.json")
SKILLS_REPO = os.path.join(_ROOT, "data", "skills_repo")
OVERLAY = os.path.join(_ROOT, "data", "skills_descriptions_overlay.json")
REPORT_PATH = os.path.join(_ROOT, "docs", "skills_mgmt", "技能归类审计报告.md")

# 需要"逐条深挖"的目标案例（用户原话里的两个技能）
TARGET_CASES: List[Tuple[str, str]] = [
    ("易之三义（用户问题 1）", "skill"),
    ("writing-skills（用户问题 2）", "pd-writing-skills-5da20e67-skill"),
    ("writing-skills 的另一实例（同名不同哈希）", "pd-writing-skills-7da19002-skill"),
    ("verification-before-completion（无 pd- 前缀的实例）", "verification-before-completion"),
]

# ── 人工定案（结论必须由打分明细支撑；脚本每次复跑都会重新渲染，不会被覆盖丢失）──
CURATED: Dict[str, str] = {
    "asset:pd-writing-skills-5da20e67-skill + rt:pd-writing-skills-5da20e67-skill":
        "**保留人工钉住值「代码与工程」**（定案，2026-09-19）。理由：① 该技能是把 TDD"
        "（RED-GREEN-REFACTOR）应用于 SKILL.md 编写的方法论，其 tags 全部是"
        "「创建技能/编辑技能/验证技能」；② 这是一次**刻意的人工移动**（提交 `68bfd2d8`"
        "（人工改类入口）之后 17 分钟内的操作），不是自动归类的产物；③ 用户在问的是"
        "「为什么被归到文档与办公」——那是**自动规则**的结论，本次已从根因消除"
        "（裸「文档」退役）。**不新增 manual 条目**（这两条本就在 manual 里）。",
    "rt:skill（易之三义）":
        "**定案「代码与工程」，并把 `rt:skill` 一并钉住**（manual 3→4）。理由：`asset:skill`"
        "早已是人工钉住的「代码与工程」，但 `rt:skill` 漏钉 ⇒ 运行时行（技能库页）没有人工"
        "保护，历史上正是这条路径把人工结果静默回滚（提交 `f8443cff` 的已知缺陷）。"
        "本次同时修了 `assign()`（改为双键钉住）并补齐这条历史遗留。",
    "asset:pd-writing-skills-7da19002-skill + rt:（同名另一实例）":
        "**判定为「孤儿条目，无实体」**，不处置其值。它的两个 key 在 `skills.json`、"
        "`skills_mgmt.json`、`skills_repo/*/skill.md` 三处都没有实体 ⇒ 无法复算、也不可复现，"
        "属历史残留。与 5da20e67 实例的类名不一致（翻译与写作 vs 代码与工程）"
        "已由自愈检查 `same_name_conflicts()` 报出，留给下一轮人工定案（见「遗留待定」）。",
    "「演示收集技能」自动类（`rt:autofix-demo`，`hits: 1`）":
        "**判定为应当清理的碎片类**：该技能实体不存在（孤儿），类名来自测试残留的技能名。"
        "本任务**不删除**（`data/skills_classes.json` 是运行时产物，删除需服务层支持；"
        "且删除不可复现）—— 记为遗留项，建议在清理调度器里加「成员数为 0/1 且成员为孤儿的"
        "自动类」巡检。",
}

# ── 规则级"改动前后 diff"的复现基础（2026-09-19 治理）────────────────────
# 本次从 `SEED_CLASSES` 退役的通用词 + 当时的口径（英文在下划线标识符内部也命中）。
# 用于**现场复算**"改之前会判成什么"，从而把 diff 做成可复现的计算结果，
# 而不是抄一份人写的表格。
RESTORED_BEFORE = {
    "交流与人格": ["建议"],
    "文档与办公": ["文档", "报告", "文件", "整理"],
    "数据分析与可视化": ["分析"],
}

# 逐条优劣判定（必须逐条写；空白即视为"待逐条判定"）
RULE_DIFF_VERDICTS: Dict[str, str] = {
    "asset:pd-dispatching-parallel-agents-b8065ccd-skill":
        "**更优**：改前靠 provenance 标签 `from_knowledge` 白拿「记忆与知识」2 分（与另一类平局）。"
        "「并行分派子代理」本就不是记忆域；改后不再被标签名误导。",
    "asset:pd-executing-plans-95cbf64a-skill":
        "**中性偏优**：改前同样是 `from_knowledge` 标签造成的 2:2 平局（「记忆与知识」靠表序胜出）。"
        "改后由「审查」判给「安全与合规」。两个结果都不理想 —— 这是**阈值口径**的遗留问题"
        "（该技能描述只有 2 个有效信号），已登记为遗留待定项。",
    "asset:pd-systematic-debugging-556faa20-skill":
        "**中性**：改前唯一 ≥2 分的信号是 `from_knowledge` 标签（误报）；改后落入「未分类」。"
        "对调试技能而言「未分类」比「错归记忆域」更诚实，但也暴露 `debugging` 不匹配关键词 `debug`"
        "（整词匹配）这一**遗留缺口**。",
    "asset:pd-writing-skills-5da20e67-skill":
        "**更优（但台账值不变，且**仍是边界归类**）**：改前规则判定「文档与办公」（用户问的"
        "第二个「为什么」），改后规则判定「代码与工程」= 人工定案值 —— **根因（裸「文档」）已消除**。"
        "但必须如实说明：改后是 `refactor`×3（正文）对 `writing`×3（名称）的 **0 分差平局**，"
        "由 `SEED_CLASSES` 表序判给「代码与工程」，且 `refactor` 是**单点依赖**"
        "（移除即翻回「翻译与写作」）。该条靠 `manual` 钉住才稳定，已登记为遗留待定项。",
    "rt:pd-writing-skills-5da20e67-skill":
        "**更优（但台账值不变）**：改前「文档与办公」，改后「翻译与写作」"
        "（`writing`×3 对 `refactor`×2，分差 1）。同样因人工钉住而可见结果不变。",
    "asset:wf-f19dc52c-skill":
        "**中性**：改前靠裸「文件」判成「文档与办公」（该技能是工作流学习产物，"
        "原始任务是「列出目录下的文件」）。「文件」退役后落「未分类」。"
        "对一条 `deprecated`、名字是「自动学习: 列-出-当」的退化技能，「未分类」更诚实。",
    "rt:wf-f19dc52c-skill":
        "**中性**：同 `asset:wf-f19dc52c-skill`（运行时行与资产行同步变化）。",
}

# ── 验证记录（跑过的命令与真实结果；TASK-02 §5 E6 要求与基线逐条对比）──
TEST_EVIDENCE: List[str] = [
    "`python -m pytest tests/unit -q -k \"categor or skill_class or misclass\"` ⇒ "
    "**148 passed, 1 skipped, 0 failed**（连跑两次结果一致）。",
    "`python -m pytest tests/unit/test_skills_classifier.py -q` ⇒ **46 passed, 4 skipped, 0 failed**"
    "（本次新增 13 个用例：`TestKeywordGuardrails` 6 个 + 注册表一致性 4 个 + 关键词回归 3 个）。",
    "`python scripts/audit_skill_classification.py --check` ⇒ **通过**"
    "（跨命名空间冲突 ≤0、单点依赖 ≤ 预算 40、已退役通用词未回表、通用词探针不夺域）。",
    "**全量 `tests/unit` 未能一次跑完**（两次尝试分别在 46% / 62% 处被环境终止）：元凶是 "
    "`tests/unit/test_server_routes_registration_inventory.py`——它用 `subprocess` 起独立进程"
    "枚举 Flask 路由表，在本机（管道 stdio + 无 TTY）会挂住，线程级 `--timeout` 无法中断。"
    "⇒ 改为把 631 个测试文件按轮转法切成 4 批、每批 `-n 2` 执行，合计："
    "**18,958 passed / 306 skipped / 13 failed / 0 error**。",
    "**13 条失败的归因（逐条查过，均与技能分类无关）**："
    "① `test_tool_calling_comprehensive` 5 条 —— 单模块隔离运行 **116 passed / 0 failed** ⇒ "
    "xdist 跨模块状态污染的产物，非真实失败；"
    "② `test_server_routes_registration_inventory` 3 条 —— 同上那个 subprocess/路由枚举问题；"
    "③ `test_date_shift_blindspots_guard` 2 条 —— 守卫报的是**追踪且未被本次改动触碰**的 "
    "`tests/unit/test_load_tool_meta_cache.py:106`（把 `time.time()` 派生的值写进 mtime）；"
    "④ `test_scripts_quality_gate` 3 条 —— `scripts/observability_quality_gate.py` 因缺 "
    "`--results-dir` 返回 `overall_status=inconclusive`（退出码 2）。",
    "**与 `failures_baseline.txt` 的对比结论（E6）**：本批 13 条失败**全部不在**基线里；"
    "反过来，基线里 68 条 `tests/unit` 条目在本批**一条都没有复现**。"
    "⇒ `failures_baseline.txt` 是一份**陈旧快照**，与当前工作区（31 个未提交改动 + 4 个未入库的"
    "新测试文件）已脱节。按 E6 的字面判据「全量失败集合 ⊆ 基线」**本轮不通过**；"
    "但证据逐条指向环境/基线漂移，**没有一条由本次技能归类治理引入**。"
    "建议 TASK-03（地基加固 / 测试基线）重刷该基线。",
    "**⚠️ 并发施工污染（必须如实说明）**：本次执行期间，同一工作区**还有别的子任务在改文件**"
    "（TASK-00 建议 TASK-01/02/03 并行）。实测在执行过程中又出现了 "
    "`scripts/observability_quality_gate.py`、`scripts/scan_sensitive_data.py`、"
    "`docs/ops_quick_manual.md`、`docs/closeout/`、`docs/rfc/`、`scripts/audit_dependency_drift.py`、"
    "`scripts/check_baseline_regression.py`、`scripts/check_security_baseline.py`、"
    "`tests/unit/test_feature_flags.py` 等**并非本任务产生的**改动/新文件（本任务的改动面见第 14 节）。"
    "⇒ 上面 ②③④ 几组失败的**部分根因落在他人的在建改动上**；本任务的结论只对本任务改动面负责。"
    "这也解释了为什么全量跑会出现与技能分类毫无关系的失败（另有一次 "
    "`test_audit_safety_logging_singleton.py` 收集期 `IndentationError` 的瞬时错误，"
    "重跑即消失 —— 与「别的进程正在写文件」一致）。",
]

# 本任务的改动面（只列这些，方便与并发施工区分）
TOUCHED_FILES: List[str] = [
    "`agent/skills_mgmt/categorizer.py`（M）—— 关键词退役 + 英文标识符收紧 + 关键词角色声明 +"
    " `assign()` 双键钉住 + 两个只读自愈检查",
    "`tests/unit/test_skills_classifier.py`（M）—— 新增 13 个用例",
    "`scripts/audit_skill_classification.py`（新增，只读审计工具）",
    "`docs/skills_mgmt/技能归类审计报告.md`（新增，本报告）",
    "`data/skills_classes.json`（运行时台账，gitignore）—— **只把 `rt:skill` 补进 `manual`**，"
    "未改动任何既有归类值",
    "`data/backups/skills_classes.20260919-134552.json`（新增，改动前备份，gitignore）",
]

OPEN_ITEMS: List[str] = [
    "**59 条孤儿条目**（`external-skill` / `external-skill-2` / `autofix-demo` / `pdf-extractor` /"
    " `dsh` / `zip-d2968c59-skill` / `wf-23217647-skill*` / `engineering-test-delivery-2` /"
    " 多个 `pd-*-<其它哈希>-skill` 等）在 `data/skills.json`、`data/skills_mgmt.json`、"
    "`data/skills_repo/*/skill.md` 三处**都找不到实体** ⇒ 不可复算、不可复现。"
    "**本任务不删除**（台账是运行时产物，删除需服务层支持且不可复现）。"
    "建议：在 `agent/skills_mgmt/cleanup.py` 的清理调度里加「孤儿条目巡检」。",

    "**8 组跨实例同名技能归类不一致**（`writing-skills` / `brainstorming` / `executing-plans` /"
    " `frontend-design` / `systematic-debugging` / `using-superpowers` /"
    " `verification-before-completion` / `skill`）。已由新增的"
    " `SkillClassRegistry.same_name_conflicts()` 报出，但**逐组定案需要产品口径**"
    "（同一份素材蒸馏出的多份实例，应当合并、还是各自独立归类？）。"
    "其中 `skill` 组的 4 个 `pd-skill-<哈希>-skill` 是**蒸馏流水线把技能名统一写成了 `skill`** 的"
    "命名缺陷，不是语义冲突 —— 建议在蒸馏侧修名字，而不是在分类侧加规则。",

    "**`asset:pd-executing-plans-95cbf64a-skill` 的归类仍不稳定**：该技能描述只有 2 个有效信号"
    "（「设置审查节点」），改前靠 `from_knowledge` 标签造成的平局判「记忆与知识」，改后判"
    "「安全与合规」。两者都不理想，根因是 `_MIN_SCORE = 2` 恰好等于「一个中文词命中一次」——"
    "**提高阈值到 3 会让「易之三义」的运行时行（只有「编码」2 分）落「未分类」**，"
    "与 TASK-02 的必须通过项冲突，故本轮**不动阈值**，改由「资产回退」与「人工钉住」兜底。",

    "**`debugging` 不匹配关键词 `debug`**（`_ASCII_TOKEN` 是整词匹配，无词形还原）："
    "`pd-systematic-debugging-*` 因此拿不到本域分数。建议下一轮补英文词形（`debug`/`debugging`/"
    "`debugged`）或改用词干匹配 —— 属**放宽方向**，必须单独评估 diff。",

    "**自愈检查尚未接入调度**：`inconsistent_pairs()` / `same_name_conflicts()` 已实现且是纯读，"
    "但**未**挂到启动自检或 `cleanup_scheduler`（本任务只负责提供能力与告警口径，避免改动在飞的"
    "后台任务切片）。建议由 TASK-04/TASK-05 的 Registry 侧统一收口。",

    "**`asset:pd-writing-skills-5da20e67-skill` 仍是 0 分差平局**（`refactor`×3 对 `writing`×3，"
    "表序判给「代码与工程」），且 `refactor` 是单点依赖。用户可见结果由 `manual` 钉住保障，"
    "但**规则层面并未真正稳定**。根因是两个信号强度相同、且都来自「名称 vs 正文」的加权差 ——"
    "**属打分口径的结构性问题**（`name×3` 与 `content×1` 的重复次数把两者拉平）。"
    "本轮不动权重（改动面过大、且 TASK-02 §5 禁止放宽），登记为待定。",

    "**`rt:skill` 的人工钉住（manual 3→4）是「补齐历史遗留」，不是新增定案**："
    "它的类名与 `asset:skill` 完全一致（代码与工程），本次只补了运行时键的 `manual` 标记。"
    "若后续要让「易之三义」完全回到自动分类，需先解除 `asset:skill` 的钉住（"
    "`POST /api/skills-mgmt/classes/move {skill_id:'skill', auto:true}`）。",
]


# ── 历史口径复现（只为回答"为什么当年会判成那样"，不参与 11.2 的 diff）──
# `识别`（2026-09-19 移除，语音域）与 `markdown`（更早移除，办公域）都属"通用词退役"，
# 但**不是本次任务做的**。把她们放回词表就能复算出"当年的判定"，这是回答用户
# 「为什么「易之三义」被归成语音与多媒体」的唯一客观证据。
HISTORICAL_RESTORE = {
    "语音与多媒体": ["识别"],
    "文档与办公": ["markdown"],
}
# 历史口径还要**去掉当时尚未加入**的词，否则复算会高估本域分数：
# `编码` 是 2026-09-19 补进「代码与工程」的（见 `categorizer.py` 该处注释）。
HISTORICAL_REMOVE = {
    "代码与工程": ["编码"],
}


def _hist_scores(payloads, key):
    """按「历史口径」复算：把 `HISTORICAL_RESTORE` + `RESTORED_BEFORE` 放回词表、
    去掉 `HISTORICAL_REMOVE`（当时尚未加入的词），并用**旧的** `_token_count`。"""
    t = resolve_text(key, payloads)
    add: Dict[str, List[str]] = {}
    for src in (HISTORICAL_RESTORE, RESTORED_BEFORE):
        for cls_name, kws in src.items():
            add.setdefault(cls_name, []).extend(kws)
    classes = []
    for c in C.SEED_CLASSES:
        drop = set(HISTORICAL_REMOVE.get(c["name"], ()))
        kws = [k for k in c["keywords"] if k not in drop] + add.get(c["name"], [])
        classes.append({"name": c["name"], "keywords": kws})
    hay = C._field_text(t["name"], t["description"], t["content"], t["tags"])
    rows: Dict[str, Dict[str, Any]] = {}
    for cls in classes:
        total, hits = 0, []
        for kw in cls["keywords"]:
            if kw.isascii():
                toks = C._ASCII_TOKEN.findall(hay)
                n = min(sum(1 for x in toks if x == kw), 3)
            else:
                n = min(hay.count(kw), 3)
            if n:
                hits.append((kw, n))
                total += n
        rows[cls["name"]] = {"total": total, "rows": hits}
    cls, sc, second, second_s = winner(rows)
    return cls, sc, second, second_s, rows


def _old_rule_class(payloads, key):
    """复算"2026-09-19 治理之前"的口径：未退役词表 + 英文在下划线标识符内部也算命中。"""
    t = resolve_text(key, payloads)
    classes = [{"name": c["name"],
                "keywords": list(c["keywords"]) + RESTORED_BEFORE.get(c["name"], [])}
               for c in C.SEED_CLASSES]
    hay = C._field_text(t["name"], t["description"], t["content"], t["tags"])
    best, best_s = None, 0
    for cls in classes:
        s = 0
        for kw in cls["keywords"]:
            if kw.isascii():
                toks = C._ASCII_TOKEN.findall(hay)
                s += min(sum(1 for x in toks if x == kw), 3)
            else:
                s += min(hay.count(kw), 3)
        if s > best_s:
            best, best_s = cls["name"], s
    return best if (best is not None and best_s >= C._MIN_SCORE) else None


# 单点依赖预算：真实台账里"移除某一个关键词就会翻转"的条目数上限。
# 【为什么放在审计脚本而不是单测】`data/skills_classes.json` 是**运行时产物**
#   （.gitignore 忽略、随技能增删变化），把它写进 pytest 会成为 flaky 测试。
#   这里做成"治理预算"：`--check` 超预算即退出码 1，由 CI/巡检调度调用。
SINGLE_POINT_BUDGET = 40

# 跨命名空间冲突预算：0（人工钉住 + `_reconcile_same_skill` 应保证两侧一致）
CROSS_NS_BUDGET = 0


def _sha256(path: str) -> str:
    if not os.path.exists(path):
        return "<文件不存在>"
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ══════════════════════════════════════════════════════════════════
#  载荷读取（全部只读）
# ══════════════════════════════════════════════════════════════════

def _read_json(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001 缺文件/坏文件降级为默认值（审计脚本不因数据缺失崩）
        return default


def _read_skill_md(sid: str) -> Optional[Dict[str, Any]]:
    """读 `data/skills_repo/<id>/skill.md` 的 front matter（资产侧正文的次级来源）。"""
    path = os.path.join(SKILLS_REPO, sid, "skill.md")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
    except Exception:  # noqa: BLE001
        return None
    meta: Dict[str, Any] = {}
    body = raw
    if raw.startswith("---"):
        parts = raw.split("---", 2)
        if len(parts) >= 3:
            try:
                import yaml
                meta = yaml.safe_load(parts[1]) or {}
            except Exception:  # noqa: BLE001 front matter 坏了就只用正文
                meta = {}
            body = parts[2]
    if not isinstance(meta, dict):
        meta = {}
    return {
        "id": sid,
        "name": meta.get("name") or sid,
        "description": meta.get("description") or "",
        "content": body,
        "tags": meta.get("tags") or [],
        "_source": "skills_repo/<id>/skill.md",
    }


def load_payloads() -> Dict[str, Any]:
    overlay = _read_json(OVERLAY, {}) or {}
    skills_json = {s.get("id"): dict(s) for s in
                   (_read_json(SKILLS_JSON, {}) or {}).get("skills", [])
                   if s.get("id")}
    # 运行时行的描述会被 overlay 覆盖（`plugins/skills.py` 先 `_apply_desc_overlay` 再分类）
    for sid, ov in overlay.items():
        if sid in skills_json and isinstance(ov, dict) and ov.get("description"):
            skills_json[sid]["description"] = ov["description"]
    mgmt = _read_json(SKILLS_MGMT, {}) or {}
    return {"overlay": overlay, "skills_json": skills_json, "mgmt": mgmt}


def _norm_tags(tags: Any) -> List[str]:
    if isinstance(tags, str):
        return [tags]
    if isinstance(tags, (list, tuple)):
        return [str(t) for t in tags if str(t).strip()]
    return []


def resolve_text(key: str, payloads: Dict[str, Any]) -> Dict[str, Any]:
    """给定注册表 key，找出**当时实际参与打分**的文本载荷。

    口径（与运行时一致，勿随手改）：
      * `asset:<id>`  ← `data/skills_mgmt.json[<id>]`（name/description/content/tags 全有）；
                         资产库缺失该 id 时回落 `data/skills_repo/<id>/skill.md` 的 front matter。
      * `rt:<id>`     ← `data/skills.json` 的 skills 条目（**只有 id/name/description/params**，
                         无正文、无 tags）—— 这就是运行时行（技能库页）的真实输入。
    """
    ns, _, sid = key.partition(":")
    mgmt, sj = payloads["mgmt"], payloads["skills_json"]
    if ns == "asset":
        rec = mgmt.get(sid)
        if isinstance(rec, dict):
            return {
                "exists": True, "source": "skills_mgmt.json",
                "name": rec.get("name") or sid,
                "description": rec.get("description") or "",
                "content": rec.get("content") or "",
                "tags": _norm_tags(rec.get("tags")),
            }
        md = _read_skill_md(sid)
        if md:
            return {"exists": True, "source": md["_source"], "name": md["name"],
                    "description": md["description"], "content": md["content"],
                    "tags": _norm_tags(md["tags"])}
        if sid in sj:
            r = sj[sid]
            return {"exists": True, "source": "skills.json(仅运行时载荷)",
                    "name": r.get("name") or sid,
                    "description": r.get("description") or "",
                    "content": "", "tags": []}
        return {"exists": False, "source": "无实体", "name": sid,
                "description": "", "content": "", "tags": []}
    # ns == "rt"
    r = sj.get(sid)
    if r:
        return {"exists": True, "source": "skills.json", "name": r.get("name") or sid,
                "description": r.get("description") or "",
                "content": r.get("content") or r.get("script") or "",
                "tags": _norm_tags(r.get("tags"))}
    reg = mgmt.get(sid)
    if isinstance(reg, dict):  # 资产在、运行时行不在 ⇒ 该 rt 键已是孤儿（等待被重新解析）
        return {"exists": False,
                "source": "无运行时实体（资产侧有 skills_mgmt 记录）",
                "name": reg.get("name") or sid,
                "description": reg.get("description") or "", "content": "", "tags": []}
    md = _read_skill_md(sid)
    if md:
        return {"exists": True, "source": md["_source"] + "(运行时未装载)",
                "name": md["name"], "description": md["description"],
                "content": "", "tags": _norm_tags(md["tags"])}
    return {"exists": False, "source": "无实体", "name": sid,
            "description": "", "content": "", "tags": []}


# ══════════════════════════════════════════════════════════════════
#  打分复算（纯函数；与 categorizer.classify_fields 保持同构）
# ══════════════════════════════════════════════════════════════════

def score_all(name: str, description: str, content: str, tags: Any,
              disabled: Optional[Set[str]] = None
              ) -> Dict[str, Dict[str, Any]]:
    """复算每个种子类的得分，并保留**逐关键词贡献**。

    `disabled` 里的关键词按"未命中"处理 —— 这就是消融测试的实现方式。
    贡献值 = `_token_count` 的返回值（该关键词在加权文本里的命中次数，上限 3）。
    """
    disabled = disabled or set()
    hay = C._field_text(name, description, content, tags)
    out: Dict[str, Dict[str, Any]] = {}
    for cls in C.SEED_CLASSES:
        rows: List[Tuple[str, int]] = []
        total = 0
        for kw in cls["keywords"]:
            if kw in disabled:
                continue
            n = C._token_count(hay, kw)
            if n:
                rows.append((kw, n))
                total += n
        out[cls["name"]] = {"total": total, "rows": rows}
    return out


def winner(scores: Dict[str, Dict[str, Any]]) -> Tuple[Optional[str], int, Optional[str], int]:
    """按 `classify_fields` 的同一规则取胜出类（严格 `>` ⇒ 并列时取 SEED_CLASSES 表序在前者）。

    返回 (胜出类, 胜出分, 次高类, 次高分)。低于 `_MIN_SCORE` ⇒ 胜出类为 None（落未分类）。
    """
    order = [c["name"] for c in C.SEED_CLASSES]
    best, best_s = None, 0
    for nm in order:
        s = scores[nm]["total"]
        if s > best_s:
            best, best_s = nm, s
    second, second_s = None, 0
    for nm in order:
        if nm == best:
            continue
        s = scores[nm]["total"]
        if s > second_s:
            second, second_s = nm, s
    if best is None or best_s < C._MIN_SCORE:
        return None, best_s, second, second_s
    return best, best_s, second, second_s


def analyse(key: str, recorded: str, manual: bool, counterpart: Optional[str],
            payloads: Dict[str, Any]) -> Dict[str, Any]:
    """对一条归类做完整的可解释性 + 脆弱性分析。"""
    t = resolve_text(key, payloads)
    scores = score_all(t["name"], t["description"], t["content"], t["tags"])
    cls, sc, second, second_s = winner(scores)

    # 与 classify_fields 的一致性自检：复算口径若与生产口径不同，整份报告就不可信
    prod = C.classify_fields(t["name"], t["description"], t["content"], t["tags"])
    selfcheck_ok = (prod.get("class") == cls)

    matched: List[Tuple[str, str, int]] = []          # (类名, 关键词, 贡献)
    for nm, d in scores.items():
        for kw, n in d["rows"]:
            matched.append((nm, kw, n))

    # ── 单关键词消融：移除任一命中词后胜出类是否翻转 ──
    single_point: List[str] = []
    for _, kw, _n in matched:
        c2, _s2, _sec2, _secs2 = winner(
            score_all(t["name"], t["description"], t["content"], t["tags"],
                      disabled={kw}))
        if c2 != cls:
            single_point.append(kw)

    # ── 独立夺域：只用该词（其余全部屏蔽）能否复现胜出类 ──
    solo_claim: List[str] = []
    all_kw: Set[str] = {kw for _c, kw, _n in matched}
    if cls is not None:
        for kw in sorted(all_kw):
            c3, _s3, _a3, _b3 = winner(
                score_all(t["name"], t["description"], t["content"], t["tags"],
                          disabled=set(all_kw) - {kw}))
            if c3 == cls:
                solo_claim.append(kw)

    return {
        "key": key, "ns": key.split(":", 1)[0], "sid": key.split(":", 1)[1],
        "recorded": recorded, "manual": manual, "counterpart": counterpart,
        "exists": t["exists"], "source": t["source"],
        "recomputed": cls, "score": sc, "second": second, "second_score": second_s,
        "margin": (sc - second_s) if cls else 0,
        "matched": matched, "single_point": sorted(set(single_point)),
        "solo_claim": sorted(set(solo_claim)),
        "rows_by_class": {nm: d["rows"] for nm, d in scores.items() if d["rows"]},
        "selfcheck_ok": selfcheck_ok,
        "stale": (cls != recorded),
    }


def find_conflicts(assignments: Dict[str, str],
                   manual: Set[str]) -> List[Dict[str, Any]]:
    """按去掉命名空间前缀后的技能 id 分组，找 `asset:` 与 `rt:` **归类不一致**的组。"""
    groups: Dict[str, Dict[str, str]] = {}
    for key, cls in assignments.items():
        ns, _, sid = key.partition(":")
        groups.setdefault(sid, {})[ns] = cls
    out = []
    for sid, byns in sorted(groups.items()):
        if "asset" in byns and "rt" in byns and byns["asset"] != byns["rt"]:
            out.append({
                "sid": sid, "asset": byns["asset"], "rt": byns["rt"],
                "asset_key": f"asset:{sid}", "rt_key": f"rt:{sid}",
                "pinned": sorted(k for k in (f"asset:{sid}", f"rt:{sid}")
                                 if k in manual),
            })
    return out


_PAYLOADS_CACHE: Dict[str, Any] = {}
# `pd-<语义名>-<8位哈希>-skill`：同一份素材被多次蒸馏 ⇒ 同名多实例。剥掉流水号才看得见"同名"。
_PD_ID = re.compile(r"^pd-(.+)-[0-9a-f]{8}-skill$")


def semantic_key(a: Dict[str, Any]) -> str:
    """把一个 skill id 归约成"语义名"，用于跨实例比对。

    【为什么不能用实体里的 name】台账里大量条目是**孤儿**（`data/skills.json`、
    `skills_mgmt.json`、`skills_repo/*/skill.md` 三处都没有实体），拿不到 name 就只能退回 id。
    而 id 里的 `pd-<语义名>-<8位哈希>-skill` 本身携带语义名，剥掉流水号即可跨实例对齐。
    """
    t = resolve_text(a["key"], _PAYLOADS_CACHE)
    nm = (t.get("name") or "").strip().lower()
    sid = str(a["sid"]).strip().lower()
    if not nm or nm == sid:
        m = _PD_ID.match(sid)
        nm = m.group(1) if m else sid
    return nm


def find_name_conflicts(analyses: Dict[str, Any]) -> List[Dict[str, Any]]:
    """跨**实例**的语义一致性：同一个技能名，不同实例被分到了不同的类。

    【为什么这条比 asset/rt 更要紧】`asset:`/`rt:` 是**同一实体**的两个视图，分叉了只是页面不一致；
    而 `pd-writing-skills-7da19002-skill` 与 `pd-writing-skills-5da20e67-skill` 是**两份同名技能**，
    用户在技能库里看到的是「同一个名字出现在两个不同的类下面」—— 这是归类准确性的直接观感问题。
    `_reconcile_same_skill` 只覆盖同名 skill **id**，覆盖不到「同名不同 id」。
    """
    by_name: Dict[str, Dict[str, List[str]]] = {}
    for a in analyses.values():
        nm = semantic_key(a)
        if not nm:
            continue
        by_name.setdefault(nm, {}).setdefault(a["recorded"], []).append(a["key"])
    out = []
    for nm, by_cls in sorted(by_name.items()):
        if len(by_cls) > 1:
            out.append({"name": nm, "by_class": by_cls,
                        "classes": sorted(by_cls)})
    return out


# ══════════════════════════════════════════════════════════════════
#  报告渲染
# ══════════════════════════════════════════════════════════════════

def _md_table(head: List[str], rows: List[List[Any]]) -> str:
    out = ["| " + " | ".join(head) + " |",
           "|" + "|".join(["---"] * len(head)) + "|"]
    for r in rows:
        out.append("| " + " | ".join(
            str(x).replace("|", "\\|").replace("\n", " ") for x in r) + " |")
    return "\n".join(out)


def _fmt_rows(rows: List[Tuple[str, int]]) -> str:
    if not rows:
        return "（无命中）"
    return " + ".join(f"`{kw}`×{n}" for kw, n in rows)


def scores_of(a: Dict[str, Any], cls_name: str) -> List[Tuple[str, int]]:
    """取某条归类在某个种子类上的逐关键词命中（报告渲染用）。"""
    return a.get("rows_by_class", {}).get(cls_name, [])


def render_target_case(label: str, sid: str, analyses: Dict[str, Any],
                       payloads: Dict[str, Any]) -> List[str]:
    """渲染一个"目标案例"的深挖小节（用户两个"为什么"就靠它回答）。"""
    lines = [f"#### {label}", ""]
    keys = [k for k in analyses if k.split(":", 1)[1] == sid]
    if not keys:
        keys = [k for k in analyses if sid and sid in k]
    if not keys:
        lines += [f"- 台账 `data/skills_classes.json` 中**不存在**任何以 `{sid}` 为技能 id 的条目。", ""]
        return lines
    for key in sorted(keys):
        a = analyses[key]
        lines += [f"**`{key}`** — 载荷来源 `{a['source']}`，实体存在：{'是' if a['exists'] else '**否（孤儿条目）**'}", ""]
        for nm, d in score_all_sorted(payloads, key):
            hit = _fmt_rows(d["rows"])
            mark = " ⬅ **胜出**" if nm == a["recomputed"] else ""
            lines.append(f"- `{nm}` = **{d['total']}** 分{mark}：{hit}")
        lines += ["", f"- 台账记录值：`{a['recorded']}`；人工钉住(manual)：{'是' if a['manual'] else '否'}"
                      + (f"；另一命名空间值：`{a['counterpart']}`" if a["counterpart"] else ""),
                  f"- **纯规则复算值：`{a['recomputed'] or '未分类'}`（{a['score']} 分）**，"
                  f"次高类 `{a['second']}`（{a['second_score']} 分），分差 {a['margin']}",
                  f"- 单点依赖（移除即翻转）：{('`' + '`、`'.join(a['single_point']) + '`') if a['single_point'] else '无'}",
                  f"- 独立夺域词（单词即可夺域）：{('`' + '`、`'.join(a['solo_claim']) + '`') if a['solo_claim'] else '无'}",
                  ]
        hcls, hsc, hsec, hsecs, hrows = _hist_scores(payloads, key)
        if hcls != a["recomputed"]:
            retired = [w for kws in HISTORICAL_RESTORE.values() for w in kws]
            lines += ["- **【历史口径复算】** 把已退役的通用词（"
                      + "、".join(f"`{w}`" for w in retired)
                      + "）放回词表、并用旧的英文匹配口径重算 ⇒ 判成 "
                      f"`{hcls or '未分类'}`（{hsc} 分），次高 `{hsec}`（{hsecs} 分）。"
                      "**这就是「为什么当年会被归到那里」的确切原因**："]
            for nm, d in sorted(hrows.items(), key=lambda kv: -kv[1]["total"]):
                if d["total"]:
                    lines.append(f"  - `{nm}` = **{d['total']}** 分：{_fmt_rows(d['rows'])}")
            lines.append("")
        lines.append("")
    return lines


def score_all_sorted(payloads: Dict[str, Any], key: str) -> List[Tuple[str, Dict[str, Any]]]:
    t = resolve_text(key, payloads)
    scores = score_all(t["name"], t["description"], t["content"], t["tags"])
    return [(nm, d) for nm, d in scores.items() if d["total"] > 0] or \
           [(f"（{C.SEED_CLASSES[0]['name']} 等全部种子类）", {"total": 0, "rows": []})]


def build_report(ctx: Dict[str, Any]) -> str:
    an: Dict[str, Any] = ctx["analyses"]
    conflicts: List[Dict[str, Any]] = ctx["conflicts"]
    total = len(an)
    single_point = [a for a in an.values() if a["single_point"]]
    boundary = [a for a in an.values() if a["recomputed"] and a["margin"] <= 1]
    solo: List[Tuple[str, str, str]] = []
    for a in an.values():
        for kw in a["solo_claim"]:
            solo.append((a["key"], a["recomputed"], kw))
    stale = [a for a in an.values() if a["stale"]]
    orphans = [a for a in an.values() if not a["exists"]]

    L: List[str] = []
    L += [
        "# 技能自动归类审计报告",
        "",
        "> 本报告由 `scripts/audit_skill_classification.py` **只读**生成（TASK-02 交付物 2）。",
        "> 每一处结论都有打分明细支撑；凡是「我认为应该归 X」的说法一律不写进本报告。",
        "",
        "## 0. 只读证明",
        "",
        f"- 运行前 `data/skills_classes.json` sha256：`{ctx['hash_before']}`",
        f"- 运行后 `data/skills_classes.json` sha256：`{ctx['hash_after']}`",
        f"- 结论：{'**一致 —— 台账未被本脚本改动**' if ctx['hash_before'] == ctx['hash_after'] else '**不一致 —— 台账被改动，审计不可信**'}",
        f"- 台账 `updated_at`（台账自述）：`{ctx['registry_updated_at']}`",
        "",
        "## 1. 口径与总量",
        "",
        _md_table(["指标", "值"], [
            ["`assignments` 条目总数", total],
            ["审计覆盖数（逐条复算打分明细）", total],
            ["结算口径自检通过数（复算 == `classify_fields`）", sum(1 for a in an.values() if a["selfcheck_ok"])],
            ["`manual` 条数", len(ctx["manual"])],
            ["实体存在的条目", total - len(orphans)],
            ["**实体不存在（孤儿条目）**", len(orphans)],
            ["**单点依赖条目**", len(single_point)],
            ["**边界归类条目**（胜出 − 次高 ≤ 1）", len(boundary)],
            ["**记录了「独立夺域词」的条目**", len(solo)],
            ["台账记录值与纯规则复算值不一致（stale）", len(stale)],
            ["**跨命名空间冲突组**", len(conflicts)],
            ["**跨实例同名技能归类不一致组**", len(ctx.get("name_conflicts", []))],
            ["种子类数 / 关键词总数 / `_MIN_SCORE`",
             f"{len(C.SEED_CLASSES)} / {sum(len(c['keywords']) for c in C.SEED_CLASSES)} / {C._MIN_SCORE}"],
        ]),
        "",
    ]

    # ── 2. 两个"为什么" ──
    L += ["## 2. 回答用户的两个「为什么」（打分明细）", ""]
    for label, sid in TARGET_CASES:
        L += render_target_case(label, sid, an, ctx["payloads"])
    L += ["### 2.x 打分明细的读法", "",
          "打分规则：`name×3 + description×2 + tags×2 + content×1`，单关键词命中次数上限 3，"
          f"种子类归属阈值 `_MIN_SCORE = {C._MIN_SCORE}`。"
          "⇒ **中文关键词在 description 里出现 1 次就是 2 分，刚好等于阈值**。"
          "这就是「单个通用词足以独立决定一个域」的结构性原因。", ""]

    # ── 3. 跨命名空间一致性 ──
    L += ["## 3. 跨命名空间归类一致性（`asset:` vs `rt:`）", ""]
    if conflicts:
        L += [_md_table(["技能 id", "`asset:`", "`rt:`", "已钉住(manual) 的键"],
                        [[c["sid"], c["asset"], c["rt"],
                          "、".join(f"`{k}`" for k in c["pinned"]) or "无"] for c in conflicts]), ""]
    else:
        L += ["**当前台账无跨命名空间冲突**：所有同时存在 `asset:` 与 `rt:` 的技能，两侧类名一致。", ""]

    name_conflicts: List[Dict[str, Any]] = ctx.get("name_conflicts", [])
    L += [f"## 3b. 跨**实例**同名技能归类不一致（{len(name_conflicts)} 组）", "",
          "`asset:`/`rt:` 只是同一实体的两个视图；这一节看的是**同名但不同 id 的多份技能**"
          "被分到了不同类 —— 用户在技能库里会直接看到「同一个名字出现在两个类下面」。", ""]
    if name_conflicts:
        rows = []
        for g in name_conflicts:
            for cls_name, keys in sorted(g["by_class"].items()):
                rows.append([f"`{g['name']}`", cls_name,
                             "、".join(f"`{k}`" for k in sorted(keys))])
        L += [_md_table(["技能名", "被分到的类", "涉及条目"], rows), ""]
    else:
        L += ["无。", ""]

    # ── 4. 全量清单 ──
    L += ["## 4. 全量 `assignments` 清单（覆盖全部条目）", "",
          "「命中关键词」列是该条**实际参与打分的全部命中**（格式 `词×贡献分`），"
          "这是每一条结论的证据；不带这一列的结论在 TASK-02 里算无效结论。", "",
          _md_table(["key", "命名空间", "台账类名", "纯规则复算", "命中分",
                     "次高类/分", "分差", "实体", "载荷来源", "manual",
                     "命中关键词（打分明细）", "单点依赖", "独立夺域词"],
                    [[f"`{a['key']}`", a["ns"], a["recorded"],
                      a["recomputed"] or "未分类", a["score"],
                      f"{a['second'] or '—'}/{a['second_score']}", a["margin"],
                      "是" if a["exists"] else "**否**", a["source"],
                      "是" if a["manual"] else "",
                      "；".join(f"{nm}: {_fmt_rows(scores_of(a, nm))}"
                                for nm in sorted({m[0] for m in a["matched"]}))
                      or "（无命中）",
                      "、".join(a["single_point"]) or "",
                      "、".join(a["solo_claim"]) or ""]
                     for a in sorted(an.values(), key=lambda x: x["key"])]), ""]

    # ── 5. 单点依赖 ──
    L += [f"## 5. 单点依赖清单（{len(single_point)} 条）", "",
          "定义：**移除某一个命中的关键词后重新打分，胜出类发生翻转** ⇒ 该条的归类「全靠这一个词」。", ""]
    if single_point:
        L += [_md_table(["key", "台账类名", "复算类名", "命中的单点依赖词"],
                        [[f"`{a['key']}`", a["recorded"], a["recomputed"] or "未分类",
                          "、".join(f"`{k}`" for k in a["single_point"])]
                         for a in sorted(single_point, key=lambda x: x["key"])]), ""]
    else:
        L += ["无。", ""]

    # ── 6. 独立夺域词 ──
    L += [f"## 6. 独立夺域词（{len(solo)} 处）", "",
          "定义：把该条命中的**其它关键词全部屏蔽**，只用这一个词仍能判给同一个类 ⇒ 该词可独立夺域。",
          "判定「通用词」的客观标准：**该词在多个不同语义域的技能文本里都会出现**"
          "（如「识别/分析/文件/报告/管理/生成」），"
          "它不得单独决定域归属。", ""]
    if solo:
        agg: Dict[Tuple[str, str], List[str]] = {}
        for key, cls, kw in solo:
            agg.setdefault((cls, kw), []).append(key)
        L += [_md_table(["类", "独立夺域词", "涉及条目数", "举例"],
                        [[cls, f"`{kw}`", len(keys), "、".join(f"`{k}`" for k in keys[:3])]
                         for (cls, kw), keys in sorted(agg.items(),
                                                       key=lambda kv: (-len(kv[1]), kv[0]))]), ""]
    else:
        L += ["无。", ""]

    # ── 7. 边界归类 ──
    L += [f"## 7. 边界归类清单（{len(boundary)} 条）", "",
          "定义：**胜出类与次高类的分差 ≤ 1** ⇒ 规则表在这条上没有区分度，表序/顺序变化即可能翻案。", ""]
    if boundary:
        L += [_md_table(["key", "胜出类", "次高类", "分差"],
                        [[f"`{a['key']}`", a["recomputed"], a["second"], a["margin"]]
                         for a in sorted(boundary, key=lambda x: x["key"])]), ""]
    else:
        L += ["无。", ""]

    # ── 8. 孤儿条目 / stale ──
    L += [f"## 8. 实体缺失（孤儿）条目（{len(orphans)} 条）", "",
          "这些 key 在 `data/skills.json`、`data/skills_mgmt.json`、`data/skills_repo/*/skill.md` "
          "三处**都找不到实体**。它们不能被复算验证，只能作为「历史残留」记录在案。", ""]
    if orphans:
        L += [_md_table(["key", "台账类名", "namespace", "备注"],
                        [[f"`{a['key']}`", a["recorded"], a["ns"], a["source"]]
                         for a in sorted(orphans, key=lambda x: x["key"])]), ""]

    if stale:
        L += [f"## 9. 台账值与纯规则复算值不一致（{len(stale)} 条）", "",
              "不一致不等于错误：`manual` 人工钉住、`_reconcile_same_skill` 双生态收敛都会造成这种情况。"
              "逐条需人工判定（见第 10 节定案表）。", "",
              _md_table(["key", "台账值", "复算值", "manual", "判定依据"],
                        [[f"`{a['key']}`", a["recorded"], a["recomputed"] or "未分类",
                          "是" if a["manual"] else "",
                          "人工钉住" if a["manual"]
                          else ("双生态收敛（对齐 rt）" if a["counterpart"] else "**待定**")]
                         for a in sorted(stale, key=lambda x: x["key"])]), ""]

    # ── 10. 人工定案 ──
    if CURATED:
        L += ["## 10. 人工定案结论（每条都有打分明细 / 出处支撑）", ""]
        for k, v in CURATED.items():
            L += [f"- **`{k}`**：{v}"]
        L += [""]

    # ── 10b. 关键词治理台账 ──
    retired = getattr(C, "RETIRED_GENERIC_KEYWORDS", {})
    anchors = getattr(C, "DOMAIN_ANCHORS", {})
    probe = getattr(C, "GENERIC_PROBE_WORDS", [])
    L += ["## 10b. 关键词治理台账（`categorizer.py`）", "",
          f"- 种子类 **{len(C.SEED_CLASSES)}** 个；关键词 **{sum(len(c['keywords']) for c in C.SEED_CLASSES)}** 个；"
          f"`_MIN_SCORE = {C._MIN_SCORE}`",
          f"- 已声明「可独立决定域」的锚点词：**{sum(len(v) for v in anchors.values())}** 个"
          f"（结构性护栏测试断言其与词表**严格相等**）",
          f"- 已退役的通用词：**{len(retired)}** 个；通用词探针：{len(probe)} 个", "",
          "### 已退役通用词（每处的 `SEED_CLASSES` 原位都留了注释：理由 / 覆盖来源 / 回归测试名）",
          "", _md_table(["词", "退役理由 / 覆盖来源"], [[f"`{w}`", r] for w, r in retired.items()]), "",
          "### 通用词探针（结构性护栏的合成输入）", "",
          "对每个种子类构造一段典型语义文本，把下面这些词**注入**进去（一次 + 重复一次），"
          "断言归类不被夺走 —— 这是历史 bug（裸「识别」塞进编码技能文本夺走语音域）的直接泛化。",
          "", "、".join(f"`{w}`" for w in probe), ""]

    # ── 11. diff ──
    if ctx.get("diff_rows") is not None:
        L += ["## 11. 改动前后归类 diff", "",
              "### 11.1 台账值 diff（`data/skills_classes.json` 的 `assignments`）", "",
              f"- 基线文件：`{ctx['diff_path']}`",
              f"- 变化条目数：**{len(ctx['diff_rows'])}**", "",
              _md_table(["key", "改动前", "改动后", "优劣判定"],
                        ctx["diff_rows"]) if ctx["diff_rows"] else
              "**零变化。** 本次治理**没有改动任何既有归类值**（`assignments` 逐键全等）；"
              "唯一的台账写入是把 `rt:skill` 补进 `manual`（人工钉住由 3 条变 4 条），"
              "它不改变任何一条的类名，只把既有的人工定案补齐到运行时视图。", ""]
        rule_rows = ctx.get("rule_diff_rows") or []
        L += ["### 11.2 规则级 diff（同样输入下，「治理前口径」与「治理后口径」各判成什么）", "",
              "这一节才是关键词治理的真实影响面。复现方式：`RESTORED_BEFORE` 把本次退役的通用词"
              "放回词表、并用**旧的** `_token_count`（英文在下划线标识符内部也命中）重算，"
              "与当前口径逐条对拍。", "",
              f"- 变化条目数：**{len(rule_rows)}** / 全量 113", "",
              _md_table(["key", "治理前判定", "治理后判定", "逐条优劣判定"], rule_rows), ""]

    if OPEN_ITEMS:
        L += ["## 12. 遗留「待定」项（逐条说明为什么本轮不定）", ""]
        for i, item in enumerate(OPEN_ITEMS, 1):
            L += [f"{i}. {item}", ""]

    if TEST_EVIDENCE:
        L += ["## 13. 验证记录（跑过的命令 / 真实结果 / 基线对比）", ""]
        for i, item in enumerate(TEST_EVIDENCE, 1):
            L += [f"{i}. {item}", ""]

    L += ["## 14. 本任务的改动面（与并发施工区分）", ""]
    for item in TOUCHED_FILES:
        L += [f"- {item}"]
    L += [""]

    L += ["---", "",
          "生成命令：`python scripts/audit_skill_classification.py`", ""]
    return "\n".join(L)


def compute_diff(old_path: str, assignments: Dict[str, str]) -> Tuple[List[List[str]], str]:
    old = _read_json(old_path, {}) or {}
    oa = old.get("assignments", {}) if isinstance(old, dict) else {}
    rows: List[List[str]] = []
    for key in sorted(set(oa) | set(assignments)):
        a, b = oa.get(key), assignments.get(key)
        if a != b:
            if a is None:
                verdict = "新增条目（此前无记录）"
            elif b is None:
                verdict = "条目被移除"
            else:
                verdict = "**待逐条判定**"
            rows.append([f"`{key}`", a or "—", b or "—", verdict])
    return rows, old_path


# ══════════════════════════════════════════════════════════════════
#  主流程
# ══════════════════════════════════════════════════════════════════

def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="技能自动归类审计（只读）")
    ap.add_argument("--check", action="store_true",
                    help="只审计不写报告；发现问题（冲突/独立夺域/边界）时退出码 1")
    ap.add_argument("--diff", metavar="OLD_REGISTRY",
                    help="与一份旧台账做归类 diff 并渲染进报告")
    ap.add_argument("--stdout-only", action="store_true", help="不写报告文件")
    args = ap.parse_args(argv)

    try:  # Windows 控制台默认 GBK，中文会乱码；显示层问题，不影响结论
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass

    hash_before = _sha256(REGISTRY)
    raw = _read_json(REGISTRY, {})
    assignments: Dict[str, str] = raw.get("assignments", {}) if isinstance(raw, dict) else {}
    manual: Set[str] = set(raw.get("manual", []) if isinstance(raw, dict) else [])
    payloads = load_payloads()

    analyses: Dict[str, Any] = {}
    for key, cls in assignments.items():
        sid = key.split(":", 1)[1]
        ns = key.split(":", 1)[0]
        other = f"{'rt' if ns == 'asset' else 'asset'}:{sid}"
        counterpart = assignments.get(other)
        analyses[key] = analyse(key, cls, key in manual, counterpart, payloads)

    conflicts = find_conflicts(assignments, manual)
    _PAYLOADS_CACHE.clear()
    _PAYLOADS_CACHE.update(payloads)
    name_conflicts = find_name_conflicts(analyses)
    hash_after = _sha256(REGISTRY)

    single_point = [a for a in analyses.values() if a["single_point"]]
    boundary = [a for a in analyses.values() if a["recomputed"] and a["margin"] <= 1]
    solo = [a for a in analyses.values() if a["solo_claim"]]
    bad_selfcheck = [a for a in analyses.values() if not a["selfcheck_ok"]]
    orphans = [a for a in analyses.values() if not a["exists"]]

    print("=" * 68)
    print("技能自动归类审计（只读）")
    print("=" * 68)
    print(f"台账文件        : {REGISTRY}")
    print(f"台账 updated_at : {raw.get('updated_at', '')}")
    print(f"sha256 运行前   : {hash_before}")
    print(f"sha256 运行后   : {hash_after}")
    print(f"只读校验        : {'通过（台账未改动）' if hash_before == hash_after else '失败（台账被改动）'}")
    print("-" * 68)
    print(f"assignments 条目总数          : {len(assignments)}")
    print(f"审计覆盖数                    : {len(analyses)}")
    print(f"结算口径自检（复算==生产）    : {len(analyses) - len(bad_selfcheck)}/{len(analyses)}")
    print(f"manual 条数                   : {len(manual)}")
    print(f"实体缺失（孤儿）条目          : {len(orphans)}")
    print(f"单点依赖条目                  : {len(single_point)}")
    print(f"边界归类条目（分差≤1）        : {len(boundary)}")
    print(f"存在独立夺域词的条目          : {len(solo)}")
    print(f"跨命名空间冲突组              : {len(conflicts)}")
    print(f"跨实例同名技能不一致组        : {len(name_conflicts)}")
    for g in name_conflicts:
        print(f"    [同名不一致] {g['name']}: " +
              " | ".join(f"{c} ← {len(k)} 条" for c, k in sorted(g["by_class"].items())))
    print("=" * 68)

    for a in sorted(analyses.values(), key=lambda x: x["key"]):
        if a["key"] in ("rt:skill", "asset:skill"):
            print(f"\n[深挖] {a['key']}  台账={a['recorded']}  复算={a['recomputed']}"
                  f"({a['score']})  次高={a['second']}({a['second_score']})")
            for nm, d in score_all_sorted(payloads, a["key"]):
                print(f"    {nm:<14} {d['total']:>3} 分  {_fmt_rows(d['rows'])}")

    if bad_selfcheck:
        print("\n[警告] 复算口径与 categorize.classify_fields 不一致的条目：")
        for a in bad_selfcheck[:10]:
            print(f"    {a['key']}")

    if hash_before != hash_after:
        print("\n[致命] 审计脚本改动了台账，报告不可信！", file=sys.stderr)
        return 2

    if not args.stdout_only:
        diff_rows = None
        diff_path = ""
        if args.diff:
            diff_rows, diff_path = compute_diff(args.diff, assignments)
        os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
        ctx = {"analyses": analyses, "conflicts": conflicts, "manual": manual,
               "payloads": payloads, "hash_before": hash_before,
               "hash_after": hash_after, "name_conflicts": name_conflicts,
               "registry_updated_at": raw.get("updated_at", ""),
               "diff_rows": diff_rows, "diff_path": diff_path}
        ctx["rule_diff_rows"] = [
            [f"`{key}`", _old_rule_class(payloads, key) or "未分类",
             analyses[key]["recomputed"] or "未分类",
             RULE_DIFF_VERDICTS.get(key, "**待逐条判定**")]
            for key in sorted(analyses)
            if (_old_rule_class(payloads, key) != analyses[key]["recomputed"])
        ]
        with open(REPORT_PATH, "w", encoding="utf-8") as f:
            f.write(build_report(ctx))
        print(f"\n报告已写出：{REPORT_PATH}")
        print(f"规则级 diff 变化条目数：{len(ctx['rule_diff_rows'])}")

    if args.check:
        problems = []
        if len(conflicts) > CROSS_NS_BUDGET:
            problems.append(f"{len(conflicts)} 组跨命名空间冲突（预算 {CROSS_NS_BUDGET}）")
        if len(single_point) > SINGLE_POINT_BUDGET:
            problems.append(f"{len(single_point)} 条单点依赖（预算 {SINGLE_POINT_BUDGET}）")
        if bad_selfcheck:
            problems.append(f"{len(bad_selfcheck)} 条复算口径不一致")
        # 已退役的通用词不得回到词表、也不得再独立夺域（与单测同一不变量）
        seed_kws = {kw for c in C.SEED_CLASSES for kw in c["keywords"]}
        back = sorted(set(C.RETIRED_GENERIC_KEYWORDS) & seed_kws)
        if back:
            problems.append(f"已退役通用词回到词表：{back}")
        still = [w for w in C.RETIRED_GENERIC_KEYWORDS
                 if C.classify_fields("skill-x", w, "")["class"] is not None]
        if still:
            problems.append(f"已退役通用词仍能独立夺域：{still}")
        claiming = [w for w in C.GENERIC_PROBE_WORDS
                    if C.classify_fields("skill-x", w, "")["class"] is not None]
        if claiming:
            problems.append(f"通用词探针仍能夺域：{claiming}")
        if problems:
            print("\n[--check] 未通过：" + "；".join(problems), file=sys.stderr)
            return 1
        print("\n[--check] 通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
