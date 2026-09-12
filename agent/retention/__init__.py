"""数据生命周期治理（TASK-S8-01）：策略 / 温冷归档 / 还原 / 删除闸门 / 调度。

【一句话】
    云枢有 12 类"只追加、无清理"的运行时数据。本包给每一类一张成文策略，
    并把"归档—还原—删除"三件事变成**可机械判定**的动作：
    归档必须自描述且可还原；删除必须先过 `PurgeGuard`；审计链永久保留。

【模块地图】
    policy.py      策略表（保留期 / 分层 / 归档方式 / 可否删除 / 执行者 / 依据）
    scan.py        只读普查与选片（文档数字与归档选片共用同一套口径）
    manifest.py    归档件自描述格式（schema 版本 / 时间范围 / 记录数 / 校验和）
    archiver.py    温层（复用既有 log_archiver）+ 冷层（自描述压缩包）
    restorer.py    还原 + 抽样往返一致性（逐文件 sha256 / SQLite 行摘要判定）
    guard.py       删除前校验（红线 / 记忆路径 / 可删标记 / 指标依赖 / 越界 / 已归档）
    metrics.py     既有指标的复算对照（**不定义新口径**）
    scheduler.py   调度（默认关闭；首跑强制 dry-run）

【三条不可越界原则的落点】
    ① 审计链永久保留 → `policy.REDLINE_CLASS_IDS` + `guard.CODE_REDLINE`；
    ② 默认保守       → `delete_source` 默认 False + `scheduler` 首跑强制 dry-run；
    ③ 不改统计口径   → `metrics` 归档前后复算对照 + 温层仅对读端分片感知的类开启。
"""

from __future__ import annotations

from agent.retention.archiver import (
    ACTOR,
    AUDIT_ACTION,
    EVENT_TYPE,
    Archiver,
    ClassOutcome,
    RetentionReport,
)
from agent.retention.guard import (
    CODE_FORGETTING_PATH,
    CODE_METRIC_DEPENDENCY,
    CODE_NOT_DELETABLE,
    CODE_OK,
    CODE_OUT_OF_SCOPE,
    CODE_REDLINE,
    FORGETTING_ENTRY,
    GuardDecision,
    PurgeGuard,
    assert_no_redline_deletion,
)
from agent.retention.manifest import (
    ARCHIVE_SCHEMA,
    ARCHIVE_SCHEMA_VERSION,
    ArchiveFormatError,
    ArchiveManifest,
    ArchivedFile,
)
from agent.retention.metrics import (
    METRIC_NAMES,
    ConsistencyReport,
    MetricComparison,
    compare_metrics,
    check_roundtrip_metrics,
    digestion_throughput_metric,
    audit_chain_metric,
    snapshot,
    utc_weekly_metric,
)
from agent.retention.policy import (
    ARCHIVE_COLD_PACK,
    ARCHIVE_NONE,
    ARCHIVE_WARM_DAILY,
    DEFAULT_CLASSES,
    DELETE_GUARDED,
    DELETE_NONE,
    DELETE_S5_01_FORGETTING,
    DELETABLE_MODES,
    FORGETTING_CLASS_IDS,
    KIND_JSONL,
    KIND_SQLITE,
    KIND_TREE,
    POLICY_SCHEMA,
    POLICY_SCHEMA_VERSION,
    REDLINE_CLASS_IDS,
    RetentionClass,
    RetentionPolicy,
    RetentionPolicyError,
    load_policy,
)
from agent.retention.restorer import (
    RestoreRefusedError,
    RestoreReport,
    RestoredFile,
    Restorer,
    cleanup_restore_dir,
)
from agent.retention.scan import (
    class_footprint,
    cold_files,
    expand,
    footprint_totals,
    scan_all,
    warm_plan,
)
from agent.retention.scheduler import DEFAULT_SCHEDULE, register_retention_job

__all__ = [
    # 归档器
    "Archiver", "ClassOutcome", "RetentionReport", "ACTOR", "AUDIT_ACTION",
    "EVENT_TYPE",
    # 闸门
    "PurgeGuard", "GuardDecision", "assert_no_redline_deletion",
    "CODE_OK", "CODE_REDLINE", "CODE_FORGETTING_PATH", "CODE_NOT_DELETABLE",
    "CODE_METRIC_DEPENDENCY", "CODE_OUT_OF_SCOPE", "FORGETTING_ENTRY",
    # 归档格式
    "ARCHIVE_SCHEMA", "ARCHIVE_SCHEMA_VERSION", "ArchiveFormatError",
    "ArchiveManifest", "ArchivedFile",
    # 指标复算
    "METRIC_NAMES", "ConsistencyReport", "MetricComparison", "compare_metrics",
    "snapshot", "utc_weekly_metric", "digestion_throughput_metric",
    "audit_chain_metric", "check_roundtrip_metrics",
    # 策略
    "RetentionPolicy", "RetentionClass", "RetentionPolicyError", "load_policy",
    "DEFAULT_CLASSES", "REDLINE_CLASS_IDS", "FORGETTING_CLASS_IDS",
    "POLICY_SCHEMA", "POLICY_SCHEMA_VERSION", "DELETABLE_MODES",
    "ARCHIVE_NONE", "ARCHIVE_WARM_DAILY", "ARCHIVE_COLD_PACK",
    "DELETE_NONE", "DELETE_GUARDED", "DELETE_S5_01_FORGETTING",
    "KIND_JSONL", "KIND_SQLITE", "KIND_TREE",
    # 扫描
    "scan_all", "class_footprint", "footprint_totals", "cold_files", "warm_plan",
    "expand",
    # 还原
    "Restorer", "RestoreReport", "RestoredFile", "RestoreRefusedError",
    "cleanup_restore_dir",
    # 调度
    "register_retention_job", "DEFAULT_SCHEDULE",
]
