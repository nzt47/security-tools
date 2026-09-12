"""数据生命周期策略表（TASK-S8-01 步骤 1/3）。

【不易·本模块的地位】
    云枢有 12 类「只追加、无清理」的运行时数据。本模块把它们收拢成**一张表**
    （`DEFAULT_CLASSES`），每类显式声明：保留期 / 热温冷分层 / 归档方式 /
    删除方式 / **可否删除** / 执行者 / 依据。策略表是
    `docs/zh/数据生命周期策略.md` 的机器可读同源版本 —— 文档与代码由
    `tests/unit/test_retention_policy.py::test_policy_matches_doc` 逐行对齐守护。

【三档分层】
    | 层 | 含义 | 实现 |
    |---|---|---|
    | 热 | 近期可查，仍在活动文件里 | 不动 |
    | 温 | 按日分片、可查、不压缩 | `agent/skills_mgmt/log_archiver.archive_daily_file`（**复用既有**，不自建第二套） |
    | 冷 | 自描述压缩包，离线可还原 | `agent/retention/archiver.py` |
    | 删 | 仅显式标「可删」的类 | `agent/retention/guard.py` 先校验，再删 |

【默认保守（批次总表 §二②）】
    `delete_source` 默认 **False** ⇒ 默认策略 = **只归档不删除**。
    全表仅 `digestion_drafts` 标 `deletable=True`；记忆类（`memory_entries` /
    `memory_snapshots`）**不允许**被本模块直接删除，必须走 S5-01 的
    「删记忆不删证据」路径（`delete_mode=DELETE_S5_01_FORGETTING`）。

【温层为什么不是每类都开】
    温层分片会**改变文件布局**。只有读取端已被证实「分片感知」的类才能开温层
    （`reader_shard_aware=True`），否则读端只认活动文件 ⇒ 统计口径会变（等于篡改历史）。
    实测：`events`（`event_files()` 收 `events-*.jsonl`）与 `policy_decisions`
    （`_candidate_files()` 收 `<stem>.*.jsonl`）分片感知；`shadow_ledger` /
    `skills_audit` 的读端只认单文件 ⇒ 温层显式关闭。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger("agent.retention.policy")

#: 仓库根（agent/retention/policy.py → 上三级）
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: 策略表 schema（归档件与策略表都带版本号，非法版本一律拒）
POLICY_SCHEMA = "retention.policy.v1"
POLICY_SCHEMA_VERSION = 1

# ── 归档方式 ─────────────────────────────────────────────
ARCHIVE_NONE = "none"                 # 不归档（保留期内的热数据）
ARCHIVE_WARM_DAILY = "warm_daily"     # 温层：按日分片（复用 log_archiver）
ARCHIVE_COLD_PACK = "cold_pack"       # 冷层：自描述压缩包（可还原）

# ── 删除方式 ─────────────────────────────────────────────
DELETE_NONE = "none"                          # 禁止删除（红线 / 只归档不删）
DELETE_GUARDED = "guarded"                    # 允许删除，但必须先过 PurgeGuard
DELETE_S5_01_FORGETTING = "s5_01_forgetting"  # 只允许走 S5-01「删记忆不删证据」

# ── 数据形态 ─────────────────────────────────────────────
KIND_JSONL = "jsonl_files"       # 一个或多个 JSONL 文件（按行记数）
KIND_SQLITE = "sqlite_db"        # 单个 SQLite 库（按行记数，备份后打包）
KIND_TREE = "file_tree"          # 任意文件树（按文件记数）

DELETABLE_MODES = (DELETE_GUARDED, DELETE_S5_01_FORGETTING)

# ── 环境开关（显式常量：让 scripts/scan_settings.py 能机械解析出字面量）──
ENV_ENABLED = "CP_RETENTION_ENABLED"
ENV_DRY_RUN = "CP_RETENTION_DRY_RUN"
ENV_DELETE_SOURCE = "CP_RETENTION_DELETE_SOURCE"
ENV_ARCHIVE_DIR = "CP_RETENTION_ARCHIVE_DIR"
ENV_DAY_OF_WEEK = "CP_RETENTION_DAY_OF_WEEK"
ENV_HOUR = "CP_RETENTION_HOUR"
ENV_MINUTE = "CP_RETENTION_MINUTE"
ENV_CLASSES = "CP_RETENTION_CLASSES"

_TRUE = ("true", "1", "yes", "on")


@dataclass(frozen=True)
class RetentionClass:
    """一类运行时数据的保留策略（策略表的一行）。

    Attributes:
        class_id: 稳定标识（同时是 `data/archive/<class_id>/` 目录名）。
        title: 人读名称。
        kind: `KIND_JSONL` / `KIND_SQLITE` / `KIND_TREE`。
        globs: 相对 `root` 的 glob（`**` 递归）；`external=True` 时相对 `external_root`。
        warm_days: 温层阈值。`0` = **活动文件只保留当日**（与既有
            `log_archiver` 的「非今日即历史行」判定逐字一致）；`None` = 不开温层；
            其它正值**不被支持**（无法由既有实现表达，见 `scan.warm_plan`）。
        cold_days: 冷层阈值：mtime 早于 `now - cold_days` 天的**整文件**进入冷归档。
        retention_days: 保留期（天）。`None` = 永久保留（只归档不删）。
        archive_mode: `ARCHIVE_WARM_DAILY` / `ARCHIVE_COLD_PACK` / `ARCHIVE_NONE`。
        delete_mode: `DELETE_NONE` / `DELETE_GUARDED` / `DELETE_S5_01_FORGETTING`。
        deletable: **可否删除**（策略语义开关，非运行开关）。
        redline: 是否红线类（审计链/每日根/纯审计轨）—— `PurgeGuard` 一律拦截。
        reader_shard_aware: 读端是否分片感知（决定温层是否允许开启）。
        metric_dependencies: 依赖本类明细才能复算的既有指标（非空 ⇒ 删除会破坏口径）。
        owner: 执行者。
        basis: 依据（上游任务/裁定/源文件行号）。
    """

    class_id: str
    title: str
    kind: str
    globs: Tuple[str, ...]
    warm_days: Optional[int] = None
    cold_days: int = 90
    retention_days: Optional[int] = None
    archive_mode: str = ARCHIVE_COLD_PACK
    delete_mode: str = DELETE_NONE
    deletable: bool = False
    redline: bool = False
    reader_shard_aware: bool = False
    metric_dependencies: Tuple[str, ...] = ()
    owner: str = ""
    basis: str = ""
    sqlite_table: str = ""
    external_root: str = ""
    note: str = ""

    # ── 派生查询 ─────────────────────────────────────────
    @property
    def external(self) -> bool:
        """数据是否落在仓库树之外（如记忆快照库）。"""
        return bool(self.external_root)

    @property
    def can_purge(self) -> bool:
        """策略表是否**标为可删**（红线/未标可删 → 否）。

        注意语义边界：`True` 只表示"策略允许考虑删除"，**不等于本模块会删**：
        `delete_mode=DELETE_S5_01_FORGETTING` 的类（记忆）虽标可删，
        `PurgeGuard` 仍会拒绝并把调用方转给 S5-01 的 `ForgettingEngine`。
        """
        return bool(self.deletable) and self.delete_mode in DELETABLE_MODES and not self.redline

    @property
    def warm_allowed(self) -> bool:
        """温层是否允许开启（必须显式开 + 读端分片感知）。"""
        return (self.warm_days is not None and self.reader_shard_aware
                and self.archive_mode != ARCHIVE_NONE)

    def retention_label(self) -> str:
        return "永久保留" if self.retention_days is None else f"{self.retention_days} 天"

    def to_dict(self) -> Dict[str, Any]:
        d = dict(self.__dict__)
        d["globs"] = list(self.globs)
        d["metric_dependencies"] = list(self.metric_dependencies)
        d["external"] = self.external
        d["can_purge"] = self.can_purge
        d["warm_allowed"] = self.warm_allowed
        d["deletable_label"] = "可删" if self.can_purge else "禁止删除" if (
            self.redline or not self.deletable) else "受限"
        d["retention_label"] = self.retention_label()
        return d

    def with_overrides(self, **kw: Any) -> "RetentionClass":
        return replace(self, **kw)


#: 记忆快照库默认根（与 `agent/memory/forgetting.py::DEFAULT_SNAPSHOT_ROOT` 同源，
#: 该常量刻意放在仓库树之外：快照是删除前的回滚凭据，不与业务数据同盘同树）
DEFAULT_SNAPSHOT_ROOT = os.path.join(os.path.expanduser("~"), ".cloudpivot",
                                     "vault", "snapshots")


# ════════════════════════════════════════════════════════════
#  策略表（12 类；与 docs/zh/数据生命周期策略.md 逐行同源）
# ════════════════════════════════════════════════════════════

DEFAULT_CLASSES: Tuple[RetentionClass, ...] = (
    # ── 红线类 ①：链式审计链 + 每日 Merkle 根（禁止删除）──
    RetentionClass(
        class_id="audit_chain",
        title="链式审计链 + 每日 Merkle 根",
        kind=KIND_SQLITE,
        # 【安全边界】签名私钥 data/audit/audit_signing_key.pem **不进 globs**：
        # 私钥不参与归档（打包会把私钥复制到归档目录，扩大暴露面）。
        globs=("data/audit/audit_chain.db", "data/audit/daily_roots.jsonl"),
        warm_days=None,
        cold_days=365,
        retention_days=None,
        archive_mode=ARCHIVE_COLD_PACK,
        delete_mode=DELETE_NONE,
        deletable=False,
        redline=True,
        reader_shard_aware=False,
        metric_dependencies=("audit.verify_chain", "audit.export"),
        owner="审计链本体（S2-02 `agent/audit/chain.py`）",
        basis="批次总表 §二①「审计链永久保留」；S2-02 验收裁定；未实现（也不实现）链裁剪/轮转",
        sqlite_table="audit_chain",
        note="只归档不删除：归档件为一致性备份副本，源库与链序（seq）永不改动",
    ),
    # ── 红线类 ②：技能评审纯审计轨（S2-02 migration 已置只读）──
    RetentionClass(
        class_id="skills_audit",
        title="技能评审/评估审计轨（按日分片）",
        kind=KIND_JSONL,
        globs=("data/skills_assessment_events*.jsonl",
               "data/skills_digest_events*.jsonl",
               "data/skills_mgmt_review_audit.jsonl"),
        warm_days=None,
        cold_days=90,
        retention_days=None,
        archive_mode=ARCHIVE_COLD_PACK,
        delete_mode=DELETE_NONE,
        deletable=False,
        redline=True,
        reader_shard_aware=False,
        metric_dependencies=(),
        owner="技能管理审计轨（`agent/skills_mgmt/log_archiver.py`）",
        basis="`agent/audit/migration.py::LEGACY_TARGETS` 已判为纯审计轨（MODE_READONLY，chmod 0444）",
        note="纯审计轨：只归档不删除；源分片若已置只读，冷归档只读打包、不改权限",
    ),
    # ── 红线类 ③：存量审计只读归档镜像 ──
    RetentionClass(
        class_id="legacy_audit_archive",
        title="存量审计只读归档镜像",
        kind=KIND_TREE,
        globs=("data/audit/legacy_archive/**/*",
               "data/audit/legacy_archive_manifest.json"),
        warm_days=None,
        cold_days=365,
        retention_days=None,
        archive_mode=ARCHIVE_COLD_PACK,
        delete_mode=DELETE_NONE,
        deletable=False,
        redline=True,
        reader_shard_aware=False,
        metric_dependencies=(),
        owner="迁移设施（`agent/audit/migration.py`）",
        basis="S2-02 迁移策略：`ArchiveReport.deleted` / `.retraced` 恒为 0（不删除、不追溯）",
        note="已是只读归档，冷归档只做异地副本；不删除镜像",
    ),
    # ── 统一轨迹台账（S2-01；既有 90 天保留口径）──
    RetentionClass(
        class_id="unified_traces",
        title="统一轨迹台账 unified_traces（SQLite）",
        kind=KIND_SQLITE,
        globs=("agent/data/tool_trace.db",),
        warm_days=None,
        cold_days=90,
        retention_days=90,
        archive_mode=ARCHIVE_COLD_PACK,
        delete_mode=DELETE_GUARDED,
        deletable=False,
        reader_shard_aware=False,
        metric_dependencies=("acr.success_rate", "digestion.case_provenance"),
        owner="轨迹设施（S2-01 `agent/observability/trace_v2.py`）",
        basis="S2-01 既有口径「统一台账 90 天保留」（`cases.py` §3.1 引用）；append-only（唯一 DELETE 为测试专用 `clear()`）",
        sqlite_table="unified_traces",
        note="保留期已到但 **deletable=False**：ACR 成功率与判定集溯源仍按明细复算；需回收请先补聚合摘要轨",
    ),
    # ── 事件流（S2-03；UTC/ACR 的复算源）──
    RetentionClass(
        class_id="events",
        title="事件流（按日分片 JSONL）",
        kind=KIND_JSONL,
        globs=("data/events/events.jsonl", "data/events/events-*.jsonl"),
        warm_days=0,
        cold_days=90,
        retention_days=None,
        archive_mode=ARCHIVE_WARM_DAILY,
        delete_mode=DELETE_NONE,
        deletable=False,
        redline=False,
        reader_shard_aware=True,
        metric_dependencies=("utc.weekly", "utc.daily", "acr", "cost_brake"),
        owner="事件出口（S2-03 `agent/observability/events.py`）",
        basis="S2-03 已复用 `log_archiver` 做跨日归档；`event_files()` 收 `events-*.jsonl` ⇒ 读端分片感知",
        note="温层可开（读端分片感知 + 按 event_id 跨分片去重）；明细是 UTC/ACR 的复算源 ⇒ 禁止删除",
    ),
    # ── 判定集（S3-02）──
    RetentionClass(
        class_id="case_store",
        title="判定集 CaseStore（每能力一文件 + 历史版本）",
        kind=KIND_TREE,
        globs=("data/digestion/cases/**/*",),
        warm_days=None,
        cold_days=180,
        retention_days=None,
        archive_mode=ARCHIVE_COLD_PACK,
        delete_mode=DELETE_GUARDED,
        deletable=False,
        reader_shard_aware=False,
        metric_dependencies=("digestion.pass_rate", "digestion.coverage"),
        owner="消化设施（S3-02 `agent/digestion/cases.py`）",
        basis="S3-02 裁定 `MAX_STORE_HISTORY=5`（**既有历史裁剪口径，本策略不改**）；§3.1 判定集独立于 trace 保留期",
        note="历史版本上限由 CaseStore 自身管理；本策略只做冷归档，不参与版本裁剪",
    ),
    # ── 消化草稿（S3-01）——全表唯一显式「可删」类 ──
    RetentionClass(
        class_id="digestion_drafts",
        title="消化草稿（未固化 SKILL.md）",
        kind=KIND_TREE,
        globs=("data/digestion/drafts/**/*.md",),
        warm_days=None,
        cold_days=90,
        retention_days=180,
        archive_mode=ARCHIVE_COLD_PACK,
        delete_mode=DELETE_GUARDED,
        deletable=True,
        reader_shard_aware=False,
        metric_dependencies=(),
        owner="消化设施（S3-01 `agent/digestion/generation.py`）",
        basis="TASK-S8-01 §三「默认策略 = 只归档不删除（除显式标『可删』的类，如草稿）」",
        note="可由模式重新生成 ⇒ 唯一允许删除的类；仍须显式 `CP_RETENTION_DELETE_SOURCE=true` 且过 PurgeGuard",
    ),
    # ── 灰度/抽检台账（S3-03）──
    RetentionClass(
        class_id="shadow_ledger",
        title="灰度台账 + 人工复核队列",
        kind=KIND_JSONL,
        globs=("data/digestion/shadow/shadow_ledger.jsonl",
               "data/digestion/shadow/manual_reviews.jsonl"),
        warm_days=None,
        cold_days=180,
        retention_days=None,
        archive_mode=ARCHIVE_COLD_PACK,
        delete_mode=DELETE_NONE,
        deletable=False,
        reader_shard_aware=False,
        metric_dependencies=("digestion.shadow_pass_rate", "digestion.throughput"),
        owner="灰度设施（S3-03 `agent/digestion/shadow.py`）",
        basis="S3-03：灰度台账是能力晋升（六条件）与人工抽检的唯一证据；裁定 D4 已暴露「无留痕」风险",
        note="温层**关闭**：`ShadowLedger.rows()` / `ManualReviewQueue.rows()` 只读单文件，分片会让读端丢数据",
    ),
    # ── 策略决策日志（S4-02）──
    RetentionClass(
        class_id="policy_decisions",
        title="策略决策日志",
        kind=KIND_JSONL,
        globs=("data/policies/decisions.jsonl", "data/policies/decisions.*.jsonl"),
        warm_days=0,
        cold_days=90,
        retention_days=None,
        archive_mode=ARCHIVE_WARM_DAILY,
        delete_mode=DELETE_NONE,
        deletable=False,
        redline=False,
        reader_shard_aware=True,
        metric_dependencies=("policy.decision_audit",),
        owner="策略设施（S4-02 `agent/policy/decisions.py`）",
        basis="S4-02：`_candidate_files()` 已支持 `<stem>.*.jsonl` 分片读取（**读端已分片感知**）；S8-02 承接写入端轮转",
        note="温层可开；S8-02 负责写入端轮转，本策略只消费其产物，不改写入语义",
    ),
    # ── 成本日聚合（S5-03 派生件）──
    RetentionClass(
        class_id="cost_daily",
        title="成本日聚合 cost_daily.json",
        kind=KIND_TREE,
        globs=("data/cost_daily.json",),
        warm_days=None,
        cold_days=365,
        retention_days=None,
        archive_mode=ARCHIVE_COLD_PACK,
        delete_mode=DELETE_NONE,
        deletable=False,
        reader_shard_aware=False,
        metric_dependencies=("cost_brake.daily",),
        owner="成本设施（S5-03 `agent/monitoring/cost_brake.py`）",
        basis="S5-03：`cost_daily_view()` / `write_cost_daily()` 产物；UTC 聚合**读事件明细**、不读本件",
        note="派生件；只归档不删除（历史可比性）",
    ),
    # ── 记忆四层（S5-01）——只允许走「删记忆不删证据」──
    RetentionClass(
        class_id="memory_entries",
        title="记忆四层存根",
        kind=KIND_TREE,
        globs=("data/memory/**/*.json",),
        warm_days=None,
        cold_days=180,
        retention_days=None,
        archive_mode=ARCHIVE_COLD_PACK,
        delete_mode=DELETE_S5_01_FORGETTING,
        deletable=True,
        reader_shard_aware=False,
        metric_dependencies=(),
        owner="记忆设施（S5-01 `agent/memory/forgetting.py`）",
        basis="TASK-S8-01 §二.6 + 批次总表 §二①：记忆删除走 S5-01「删记忆不删证据」（审计匿名化、链保留）",
        note="**本模块绝不直接删记忆**：PurgeGuard 拒绝后转 `ForgettingEngine`（先快照 → 再删除 → 盐销毁 → 链校验）",
    ),
    # ── 记忆快照库（S5-01；仓库树之外）──
    RetentionClass(
        class_id="memory_snapshots",
        title="记忆删除前快照库（仓库树之外）",
        kind=KIND_TREE,
        globs=("**/*.json",),
        warm_days=None,
        cold_days=30,
        retention_days=30,
        archive_mode=ARCHIVE_COLD_PACK,
        delete_mode=DELETE_S5_01_FORGETTING,
        deletable=True,
        reader_shard_aware=False,
        metric_dependencies=(),
        owner="记忆设施（S5-01 `MemorySnapshotStore`）",
        basis="S5-01：`SNAPSHOT_RETENTION_DAYS=30` 由 `MemorySnapshotStore.prune()` 执行（既有口径，本策略不改）",
        external_root=DEFAULT_SNAPSHOT_ROOT,
        note="到期回收由 `MemorySnapshotStore.prune()` 执行；本模块只做冷归档",
    ),
)

#: 红线类清单（`PurgeGuard` 的拦截依据；验收要求「审计链与每日根标注禁止删除」）
REDLINE_CLASS_IDS: Tuple[str, ...] = tuple(
    c.class_id for c in DEFAULT_CLASSES if c.redline)

#: 需要走 S5-01「删记忆不删证据」的类
FORGETTING_CLASS_IDS: Tuple[str, ...] = tuple(
    c.class_id for c in DEFAULT_CLASSES
    if c.delete_mode == DELETE_S5_01_FORGETTING)


class RetentionPolicyError(ValueError):
    """策略非法（未知类 / 未知方式 / 非法阈值）。"""


class RetentionPolicy:
    """策略表 + 运行时覆盖（`config.yaml retention:` + `CP_RETENTION_*`）。

    阅读顺序：`class_overrides`（显式传入）> 环境/配置 > 内置默认。
    **非法值一律回退默认并 warn（绝不抛给主流程）** —— 与 S7 各调度器同一口径。
    """

    def __init__(self, classes: Optional[Sequence[RetentionClass]] = None, *,
                 class_overrides: Optional[Dict[str, Dict[str, Any]]] = None,
                 archive_dir: str = "",
                 delete_source: bool = False,
                 dry_run: bool = True,
                 enabled: bool = False,
                 class_filter: Sequence[str] = ()) -> None:
        base: Tuple[RetentionClass, ...] = tuple(classes or DEFAULT_CLASSES)
        self._classes: Dict[str, RetentionClass] = {}
        for cls in base:
            if cls.class_id in self._classes:
                raise RetentionPolicyError(f"策略表类重复：{cls.class_id}")
            self._classes[cls.class_id] = cls
        for cid, patch in (class_overrides or {}).items():
            if cid not in self._classes:
                raise RetentionPolicyError(f"未知数据类：{cid}")
            safe = self._sanitize_patch(cid, patch)
            self._classes[cid] = self._classes[cid].with_overrides(**safe)
        self.archive_dir = os.path.abspath(
            archive_dir or os.path.join(PROJECT_ROOT, "data", "archive"))
        self.delete_source = bool(delete_source)
        self.dry_run = bool(dry_run)
        self.enabled = bool(enabled)
        self.class_filter: Tuple[str, ...] = tuple(
            str(c) for c in class_filter if str(c).strip())
        #: 调度参数（由 `load_policy` 填充；与策略同源，避免第二份配置读取点）
        self.schedule: Dict[str, int] = {"day_of_week": 6, "hour": 3, "minute": 0}

    # ── 校验 ─────────────────────────────────────────────
    @staticmethod
    def _sanitize_patch(class_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
        """过滤非法字段/非法值：**只接受白名单字段**，非法值丢弃并 warn。

        红线不变量：**任何覆盖都不能把红线类改成可删**（`redline` 与
        `delete_mode`/`deletable` 的组合在 `with_overrides` 后重新校验）。
        """
        allowed = {
            "warm_days", "cold_days", "retention_days", "archive_mode",
            "delete_mode", "deletable", "reader_shard_aware", "globs",
        }
        out: Dict[str, Any] = {}
        for key, val in (patch or {}).items():
            if key not in allowed:
                logger.warning("[RetentionPolicy] 忽略未知覆盖字段 %s.%s", class_id, key)
                continue
            if key in ("warm_days", "retention_days"):
                if val in (None, ""):
                    out[key] = None
                    continue
                try:
                    num = int(val)
                except (TypeError, ValueError):
                    logger.warning("[RetentionPolicy] %s.%s=%r 非法整数，回退默认",
                                   class_id, key, val)
                    continue
                out[key] = num if num > 0 else None
                continue
            if key == "cold_days":
                try:
                    out[key] = max(0, int(val))
                except (TypeError, ValueError):
                    logger.warning("[RetentionPolicy] %s.cold_days=%r 非法，回退默认",
                                   class_id, val)
                continue
            if key == "archive_mode":
                if val in (ARCHIVE_NONE, ARCHIVE_WARM_DAILY, ARCHIVE_COLD_PACK):
                    out[key] = str(val)
                else:
                    logger.warning("[RetentionPolicy] %s.archive_mode=%r 非法，回退默认",
                                   class_id, val)
                continue
            if key == "delete_mode":
                if val in (DELETE_NONE, DELETE_GUARDED, DELETE_S5_01_FORGETTING):
                    out[key] = str(val)
                else:
                    logger.warning("[RetentionPolicy] %s.delete_mode=%r 非法，回退默认",
                                   class_id, val)
                continue
            if key in ("deletable", "reader_shard_aware"):
                out[key] = str(val).strip().lower() in _TRUE if not isinstance(val, bool) else val
                continue
            if key == "globs":
                if isinstance(val, (list, tuple)) and val:
                    out[key] = tuple(str(v) for v in val)
                else:
                    logger.warning("[RetentionPolicy] %s.globs 非法，回退默认", class_id)
        return out

    # ── 读取 ─────────────────────────────────────────────
    @property
    def classes(self) -> Tuple[RetentionClass, ...]:
        return tuple(self._classes.values())

    def get(self, class_id: str) -> RetentionClass:
        try:
            return self._classes[class_id]
        except KeyError as e:
            raise RetentionPolicyError(f"未知数据类：{class_id}") from e

    def has(self, class_id: str) -> bool:
        return class_id in self._classes

    def selected(self) -> Tuple[RetentionClass, ...]:
        """本次运行要处理的类（`CP_RETENTION_CLASSES` 过滤；空 = 全部）。"""
        if not self.class_filter:
            return self.classes
        unknown = [c for c in self.class_filter if c not in self._classes]
        if unknown:
            logger.warning("[RetentionPolicy] 忽略未知类过滤项：%s", ",".join(unknown))
        return tuple(c for c in self.classes if c.class_id in self.class_filter)

    def to_table(self) -> List[Dict[str, Any]]:
        return [c.to_dict() for c in self.classes]

    def summary(self) -> Dict[str, Any]:
        return {
            "schema": POLICY_SCHEMA,
            "schema_version": POLICY_SCHEMA_VERSION,
            "classes": len(self._classes),
            "redline_classes": list(REDLINE_CLASS_IDS),
            # "标为可删"的两条不同路径（口径必须分开，否则会把记忆类误读成可直接删）
            "deletable_direct_classes": [c.class_id for c in self.classes
                                         if c.can_purge
                                         and c.delete_mode == DELETE_GUARDED],
            "deletable_via_forgetting_classes": list(FORGETTING_CLASS_IDS),
            "forbidden_classes": [c.class_id for c in self.classes
                                  if not c.can_purge],
            "archive_dir": self.archive_dir,
            "delete_source": self.delete_source,
            "dry_run": self.dry_run,
            "enabled": self.enabled,
        }

    def __repr__(self) -> str:  # pragma: no cover - 诊断用
        return (f"RetentionPolicy(classes={len(self._classes)}, "
                f"dry_run={self.dry_run}, delete_source={self.delete_source})")


# ════════════════════════════════════════════════════════════
#  配置解析（env > config.yaml > 默认；非法回退默认）
# ════════════════════════════════════════════════════════════


def _env_flag(name: str, default: bool) -> bool:
    """布尔环境开关；未设置/空白 → 默认；非法值 → 默认（warn）。"""
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return bool(default)
    text = str(raw).strip().lower()
    if text in _TRUE:
        return True
    if text in ("false", "0", "no", "off"):
        return False
    logger.warning("[RetentionPolicy] 非法布尔 %s=%r，回退默认 %s", name, raw, default)
    return bool(default)


def _env_int(name: str, default: int, *, lo: int, hi: int) -> int:
    """整数环境配置；非法回退默认，越界夹紧（与 S7 调度器同口径）。"""
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return int(default)
    try:
        val = int(str(raw).strip())
    except (TypeError, ValueError):
        logger.warning("[RetentionPolicy] 非法整数 %s=%r，回退默认 %d", name, raw, default)
        return int(default)
    if val < lo or val > hi:
        logger.warning("[RetentionPolicy] %s=%d 越界 [%d,%d]，夹紧", name, val, lo, hi)
        return max(lo, min(hi, val))
    return val


def _config_section(config_path: str = "") -> Dict[str, Any]:
    """读取 `config.yaml` 的 `retention:` 段（失败返回空 dict，不抛）。"""
    path = config_path or os.path.join(PROJECT_ROOT, "config.yaml")
    if not os.path.exists(path):
        return {}
    try:
        import yaml  # type: ignore[import-untyped]

        with open(path, "r", encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh) or {}
    except Exception as e:  # noqa: BLE001 配置解析失败回退默认
        logger.debug("[RetentionPolicy] config.yaml 读取失败回退默认: %s", e)
        return {}
    section = cfg.get("retention") if isinstance(cfg, dict) else None
    return section if isinstance(section, dict) else {}


def load_policy(*, config_path: str = "", env: Optional[Dict[str, str]] = None,
                classes: Optional[Sequence[RetentionClass]] = None) -> RetentionPolicy:
    """构造策略：`config.yaml retention:` 提供覆盖，`CP_RETENTION_*` 优先级更高。

    Args:
        config_path: 覆盖 `config.yaml` 路径（测试用）。
        env: 环境映射覆盖（测试用；None = 真实 `os.environ`）。
        classes: 覆盖内置策略表（测试用）。
    """
    section = _config_section(config_path)
    saved = None
    if env is not None:
        saved = dict(os.environ)
        os.environ.clear()
        os.environ.update({k: str(v) for k, v in env.items()})
    try:
        # env > config > 默认
        enabled = _env_flag(ENV_ENABLED, bool(section.get("enabled", False)))
        dry_run = _env_flag(ENV_DRY_RUN, bool(section.get("dry_run", True)))
        delete_source = _env_flag(ENV_DELETE_SOURCE,
                                  bool(section.get("delete_source", False)))
        archive_dir = (os.environ.get(ENV_ARCHIVE_DIR)
                       or str(section.get("archive_dir") or "")).strip()
        day_of_week = _env_int(ENV_DAY_OF_WEEK, int(section.get("day_of_week", 6) or 6),
                               lo=0, hi=6)
        hour = _env_int(ENV_HOUR, int(section.get("hour", 3) or 3), lo=0, hi=23)
        minute = _env_int(ENV_MINUTE, int(section.get("minute", 0) or 0), lo=0, hi=59)
        raw_classes = (os.environ.get(ENV_CLASSES)
                       or str(section.get("classes") or "")).strip()
        class_filter = tuple(c.strip() for c in raw_classes.split(",") if c.strip())
        raw_overrides = section.get("classes_override") or {}
        overrides = raw_overrides if isinstance(raw_overrides, dict) else {}
    finally:
        if saved is not None:
            os.environ.clear()
            os.environ.update(saved)

    pol = RetentionPolicy(classes, class_overrides=overrides,
                          archive_dir=archive_dir, delete_source=delete_source,
                          dry_run=dry_run, enabled=enabled,
                          class_filter=class_filter)
    # 调度参数随策略返回（与策略同源，避免第二份配置读取点）
    pol.schedule = {"day_of_week": day_of_week, "hour": hour, "minute": minute}
    return pol


__all__ = [
    "PROJECT_ROOT", "POLICY_SCHEMA", "POLICY_SCHEMA_VERSION",
    "ARCHIVE_NONE", "ARCHIVE_WARM_DAILY", "ARCHIVE_COLD_PACK",
    "DELETE_NONE", "DELETE_GUARDED", "DELETE_S5_01_FORGETTING",
    "KIND_JSONL", "KIND_SQLITE", "KIND_TREE", "DELETABLE_MODES",
    "ENV_ENABLED", "ENV_DRY_RUN", "ENV_DELETE_SOURCE", "ENV_ARCHIVE_DIR",
    "ENV_DAY_OF_WEEK", "ENV_HOUR", "ENV_MINUTE", "ENV_CLASSES",
    "DEFAULT_SNAPSHOT_ROOT", "DEFAULT_CLASSES", "REDLINE_CLASS_IDS",
    "FORGETTING_CLASS_IDS", "RetentionClass", "RetentionPolicy",
    "RetentionPolicyError", "load_policy",
]
