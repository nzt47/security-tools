"""进化谱系与档案库（Evolution Archive & Lineage）— 任务 EVO-T1 数据层基础设施

【任务定位】
    为云枢后续所有进化机制（真实评估 / 选择策略 / 自动调度 / 工具与上下文进化 /
    审批流）提供统一的数据底座：记录"一次进化事件"的完整因果链
    （父代-子代关系、评估结果、提交决策、成本），支持全局追溯与审计。

【背景缺陷（来自审计）】
    1. 原版本管理（SkillEnhancer.bump_version 快照）是零散点状记录，
       无法表达父代-子代关系、评估结果、提交决策的完整因果链；
    2. 设计文档的"Archive 不做淘汰"主张在单机资源约束下不可直接照搬，
       本模块实现"活跃 N 代完整保留 + 更早记录压缩归档"的分层策略。

【不易边界】
    本模块只做数据层基础设施，不改变任何现有进化的行为逻辑；
    不修改 offline_evolver.py 的提交流程（那是任务 3 的范围）。

【数据模型】
    EvolutionRecord 一条进化事件记录，JSONL 持久化，字段见 dataclass 定义。

【分层保留策略】
    - 活跃记录（近 N 代，N 可配置，默认 10）完整保留在 <active_path>；
    - 更早记录压缩为摘要条目（record_id / version / decision / score /
      parent_record_id 等关键字段）移入 <archive_path>，防止无限膨胀。
    - 归档摘要仍可查询与回溯谱系（parent_record_id 保留，链路不中断）。

【文件位置与配置（.env 可覆盖，全部带默认值）】
    EVOLUTION_ARCHIVE_PATH                 # 活跃记录 JSONL，默认 data/evolution_archive.jsonl
    EVOLUTION_ARCHIVE_OLD_PATH             # 归档摘要 JSONL，默认 data/evolution_archive_old.jsonl
    EVOLUTION_ARCHIVE_ACTIVE_GENERATIONS   # 活跃代数阈值，默认 10

【后续任务接入点】
    - 任务 2（真实评估）: 评估完成后更新 record.eval_result 再 append；
    - 任务 3（进化循环）: SkillEnhancer.set_lineage_hook() 启用谱系记录，
      并用 EvolutionRecord.from_bump() / EvolutionArchive.append() 落库；
    - 任务 6（审批/回滚）: decision 字段驱动审批流与自动回滚判定。
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import uuid
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .observability import logger

_SCHEMA_VERSION = 1

# 对象类型（任务 4/5 将复用 prompt / knowledge_card / subagent_config / tool_code）
OBJECT_TYPES = ("skill", "prompt", "knowledge_card", "subagent_config", "tool_code")

# 提交决策（任务 6 审批流/自动回滚的数据依据）
# skipped：本次无建议产出（如无样本/未达阈值/变体生成失败），仅审计占位，不进入审批流
DECISIONS = ("committed", "rejected", "skipped", "pending_review", "rolled_back")

# 触发来源 / 执行者（trigger / actor 为自由字符串，未来任务可按需扩展）
TRIGGERS = ("manual", "scheduler", "feedback", "api")
ACTORS = ("system", "user", "reviewer")

# 默认路径 — 与 skills_mgmt 其他数据文件对齐（agent/data/ 目录）
_DEFAULT_ACTIVE_PATH = (
    Path(__file__).parent.parent.parent / "data" / "evolution_archive.jsonl"
)
_DEFAULT_OLD_PATH = (
    Path(__file__).parent.parent.parent / "data" / "evolution_archive_old.jsonl"
)

# .env 配置键名
_ENV_ACTIVE_PATH = "EVOLUTION_ARCHIVE_PATH"
_ENV_OLD_PATH = "EVOLUTION_ARCHIVE_OLD_PATH"
_ENV_ACTIVE_GENERATIONS = "EVOLUTION_ARCHIVE_ACTIVE_GENERATIONS"


def _env_active_path() -> Path:
    return Path(os.getenv(_ENV_ACTIVE_PATH, str(_DEFAULT_ACTIVE_PATH)))


def _env_old_path() -> Path:
    return Path(os.getenv(_ENV_OLD_PATH, str(_DEFAULT_OLD_PATH)))


def _env_active_generations() -> int:
    try:
        return max(1, int(os.getenv(_ENV_ACTIVE_GENERATIONS, "10")))
    except ValueError:
        # 非法配置回退默认，不抛致命异常（损坏配置不阻断启动）
        return 10


# ════════════════════════════════════════════════════════════
#  数据模型
# ════════════════════════════════════════════════════════════

@dataclass
class EvolutionRecord:
    """一次进化事件的完整记录（一条谱系节点）

    字段语义（Why 写注释）:
        record_id:        唯一 ID，格式 evt-<timestamp>-<hash>，空则自动生成
        object_type:      进化对象类型（OBJECT_TYPES 枚举）
        object_id:        进化对象 ID（如 skill_id）
        parent_record_id: 父代记录 ID，形成谱系链；首代为 None
        parent_version:   进化前版本 / new_version: 进化后版本
        strategy:         进化策略名（fine_tune / mutate / llm_edit / version_bump ...）
        change_summary:   变更说明
        eval_result:      评估结果快照 {score, dimensions, sample_count, evaluator_version}
        decision:         提交决策（DECISIONS 枚举）
        decision_reason:  决策原因（供审计与审批流）
        trigger:          触发来源（manual / scheduler / feedback / api）
        actor:            执行者（system / user / reviewer）
        cost:             成本 {tokens, duration_ms}（供任务 3 成本控制）
        created_at:       ISO 时间戳，空则自动生成
        archived:         True 表示该记录已被压缩为归档摘要（仅关键字段）
    """
    object_type: str = "skill"
    object_id: str = ""
    parent_record_id: Optional[str] = None
    parent_version: str = ""
    new_version: str = ""
    strategy: str = "version_bump"
    change_summary: str = ""
    eval_result: Optional[Dict[str, Any]] = None
    decision: str = "committed"
    decision_reason: str = ""
    trigger: str = "manual"
    actor: str = "system"
    cost: Optional[Dict[str, Any]] = None
    created_at: str = ""
    record_id: str = ""
    schema_version: int = _SCHEMA_VERSION
    archived: bool = False

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now().isoformat(timespec="seconds")
        if not self.record_id:
            self.record_id = self._generate_id()

    @staticmethod
    def _generate_id() -> str:
        """生成 evt-<timestamp>-<hash> 唯一 ID（timestamp 毫秒级 + 随机哈希）"""
        ts = datetime.now().strftime("%Y%m%d%H%M%S%f")
        return f"evt-{ts}-{uuid.uuid4().hex[:8]}"

    @classmethod
    def from_bump(cls, ctx: Dict[str, Any], *, object_type: str = "skill",
                  parent_record_id: Optional[str] = None,
                  strategy: str = "version_bump",
                  trigger: str = "manual",
                  actor: str = "system",
                  cost: Optional[Dict[str, Any]] = None,
                  decision: str = "committed",
                  change_summary: Optional[str] = None) -> "EvolutionRecord":
        """从 bump_version 谱系钩子上下文构建记录（任务 3 启用时使用）

        Args:
            ctx: 钩子回调上下文
                {skill_id, old_version, new_version, changelog, eval_result?}
                eval_result: 可选真实评估结果快照（EVO-T2 真实评估路径透传）
        """
        return cls(
            object_type=object_type,
            object_id=ctx.get("skill_id", ""),
            parent_record_id=parent_record_id,
            parent_version=ctx.get("old_version", ""),
            new_version=ctx.get("new_version", ""),
            strategy=strategy,
            change_summary=change_summary or ctx.get("changelog", ""),
            decision=decision,
            trigger=trigger,
            actor=actor,
            cost=cost,
            eval_result=ctx.get("eval_result"),
        )

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EvolutionRecord":
        """从字典恢复记录（容忍缺失/未知字段，兼容旧数据与归档摘要）

        Why 容忍未知字段: 未来任务可能新增字段，旧 JSONL 不应因新字段而崩溃。
        """
        allowed = {f.name for f in fields(cls)}
        d = {k: v for k, v in data.items() if k in allowed}
        # 归档摘要用 "version" 字段承载进化后版本，此处映射回 new_version
        if "new_version" not in d and "version" in data:
            d["new_version"] = data["version"]
        return cls(**d)

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典（JSONL 持久化用）"""
        return asdict(self)

    def to_summary(self) -> Dict[str, Any]:
        """压缩为归档摘要：仅保留审计/谱系必需字段，防无限膨胀

        不变量（不易）: record_id / decision 必保留，parent_record_id 保留以
        维持谱系链路不断，score 保留以支撑跨代评分变化审计。
        """
        score = None
        if isinstance(self.eval_result, dict):
            score = self.eval_result.get("score")
        return {
            "schema_version": _SCHEMA_VERSION,
            "archived": True,
            "record_id": self.record_id,
            "object_type": self.object_type,
            "object_id": self.object_id,
            "parent_record_id": self.parent_record_id,
            "version": self.new_version,
            "decision": self.decision,
            "score": score,
            "created_at": self.created_at,
        }

    def get_score(self) -> Optional[float]:
        """便捷读取评估分数（无评估结果返回 None）"""
        if isinstance(self.eval_result, dict):
            return self.eval_result.get("score")
        return None


# 归档摘要未保留的字段：恢复时显式置空，避免回落为 dataclass 默认值造成审计误导
_SUMMARY_DROPPED_FIELDS = (
    "strategy", "change_summary", "decision_reason",
    "trigger", "actor", "parent_version",
)


# ════════════════════════════════════════════════════════════
#  底层 IO 工具（原子写入 / 损坏容错）
# ════════════════════════════════════════════════════════════

def _atomic_write_lines(path: Path, lines: List[str]) -> None:
    """原子写入 JSONL（临时文件 + os.replace），崩溃/掉电不产生半行文件

    Windows 注意: os.replace 目标文件被 Defender 实时扫描锁定时偶发
    WinError 5，重试等待后仍失败才抛出（与 SkillStore 同策略）。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", delete=False,
        dir=str(path.parent), suffix=".tmp",
    ) as tmp:
        for line in lines:
            tmp.write(line)
            tmp.write("\n")
        tmp_path = tmp.name
    for attempt in range(3):
        try:
            os.replace(tmp_path, path)
            return
        except OSError:
            if attempt == 2:
                raise
            time.sleep(0.05)


def _backup_corrupted(path: Path, reason: str) -> None:
    """整体损坏时备份原文件（不抛异常），供人工排查"""
    backup = path.with_name(f"{path.name}.corrupted")
    try:
        if path.exists():
            path.replace(backup)
        logger.warning("[Lineage] 文件损坏已备份到 %s: %s", backup, reason)
    except OSError:
        logger.warning("[Lineage] 损坏文件备份失败 %s: %s", path, reason)


def _read_jsonl(path: Path) -> Tuple[List[Dict[str, Any]], int]:
    """读取 JSONL 文件 → (记录字典列表, 坏行数)

    容错策略（验收 5）:
        - 文件不存在 → 返回空，由调用方重建；
        - 整文件读取失败（OSError）→ 备份后重建；
        - 单行 JSON 损坏 → 跳过该行并计数，不致命。
    """
    if not path.exists():
        return [], 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw_lines = f.readlines()
    except (OSError, UnicodeDecodeError) as e:
        # UnicodeDecodeError: 文件含非法 UTF-8 字节（非 OSError 子类，需单独捕获）
        _backup_corrupted(path, f"读取失败: {e}")
        return [], 0

    records: List[Dict[str, Any]] = []
    bad = 0
    for raw in raw_lines:
        line = raw.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            bad += 1
    if bad:
        logger.warning("[Lineage] %s 跳过 %d 条损坏行", path, bad)
    return records, bad


# ════════════════════════════════════════════════════════════
#  进化档案库
# ════════════════════════════════════════════════════════════

class EvolutionArchive:
    """进化档案库 — 追加 / 查询 / 谱系回溯 / 分层保留（线程安全）

    线程安全（守不易）: 云枢多处并发调用技能接口，所有公开方法持
    threading.RLock，并发 append 不丢记录。

    分层保留（变易）: 每对象超过 active_generations 代的最老记录
    压缩为摘要移入归档文件，活跃文件保持有限规模。
    """

    def __init__(self, active_path: Optional[str] = None,
                 archive_path: Optional[str] = None,
                 active_generations: Optional[int] = None):
        """Args:
            active_path:     活跃记录 JSONL 路径（None=读 .env/默认）
            archive_path:    归档摘要 JSONL 路径（None=读 .env/默认）
            active_generations: 每对象活跃代数阈值（None=读 .env/默认 10）
        """
        self._active_path = Path(active_path) if active_path else _env_active_path()
        self._old_path = Path(archive_path) if archive_path else _env_old_path()
        self._active_generations = (
            max(1, int(active_generations))
            if active_generations is not None else _env_active_generations()
        )
        self._lock = threading.RLock()
        self._records: List[EvolutionRecord] = []
        self._index: Dict[str, EvolutionRecord] = {}
        self._summary_index: Dict[str, Dict[str, Any]] = {}
        self._loaded = False

    # ─── 写入 ───

    def append(self, record: EvolutionRecord) -> str:
        """追加一条进化记录（自动触发分层归档），返回 record_id

        Raises:
            ValueError: object_id 为空 / object_type、decision 非法
        """
        with self._lock:
            self._ensure_loaded()
            self._validate(record)
            self._records.append(record)
            self._index[record.record_id] = record
            archived_count = self._archive_excess()
            self._persist_active()
            if archived_count:
                self._persist_archive()
            logger.info(
                "[Lineage] 追加记录 %s object=%s/%s v%s→v%s decision=%s"
                " (归档 %d)",
                record.record_id, record.object_type, record.object_id,
                record.parent_version, record.new_version,
                record.decision, archived_count,
            )
            return record.record_id

    def import_records(self, records: List[Any], *,
                       overwrite: bool = False) -> int:
        """批量导入历史进化记录（迁移旧系统数据），返回实际写入条数

        与 append 的区别：
            - 单次加锁 + 单次原子持久化，批量性能优于逐条 append；
            - 按 record_id 去重：已存在记录默认跳过（防重复导入）；
              overwrite=True 时以新记录替换（归档摘要被完整记录恢复）；
            - 校验宽松：仅要求 object_id 非空（迁移历史数据不强制枚举校验）。

        Args:
            records: EvolutionRecord 或 dict 的可迭代对象（dict 经 from_dict 转换）
            overwrite: 同 record_id 已存在时是否覆盖（默认 False=跳过）

        Raises:
            ValueError: 某条记录 object_id 为空
        """
        with self._lock:
            self._ensure_loaded()
            imported = 0
            changed = False
            for i, item in enumerate(records):
                rec = (item if isinstance(item, EvolutionRecord)
                       else EvolutionRecord.from_dict(item))
                if not rec.object_id:
                    raise ValueError(
                        f"EvolutionRecord.object_id 不能为空（第 {i + 1} 条）")
                existing = self._index.get(rec.record_id)
                if existing is not None:
                    if not overwrite:
                        continue
                    self._records[self._records.index(existing)] = rec
                    self._index[rec.record_id] = rec
                    imported += 1
                    changed = True
                    continue
                if rec.record_id in self._summary_index:
                    if not overwrite:
                        continue
                    # 归档摘要被完整记录替换，恢复为活跃记录
                    del self._summary_index[rec.record_id]
                    self._records.append(rec)
                    self._index[rec.record_id] = rec
                    imported += 1
                    changed = True
                    continue
                self._records.append(rec)
                self._index[rec.record_id] = rec
                imported += 1
                changed = True
            if changed:
                self._archive_excess()
                self._persist_active()
                self._persist_archive()
            logger.info("[Lineage] 批量导入 %d 条（overwrite=%s）",
                        imported, overwrite)
            return imported

    # ─── 查询 ───

    def get(self, record_id: str) -> Optional[EvolutionRecord]:
        """按 record_id 查询（活跃完整记录或归档摘要）"""
        with self._lock:
            self._ensure_loaded()
            rec = self._index.get(record_id)
            if rec is not None:
                return rec
            summary = self._summary_index.get(record_id)
            if summary is not None:
                return self._summary_to_record(summary)
            return None

    def list_by_object(self, object_id: str) -> List[EvolutionRecord]:
        """列出某对象的全部进化记录（活跃 + 归档，按 created_at 升序）"""
        with self._lock:
            self._ensure_loaded()
            active = [r for r in self._records if r.object_id == object_id]
            archived = [
                self._summary_to_record(s)
                for s in self._summary_index.values()
                if s.get("object_id") == object_id
            ]
            merged = active + archived
            merged.sort(key=lambda r: r.created_at)
            return merged

    def get_lineage(self, object_id: str) -> List[EvolutionRecord]:
        """回溯某对象完整进化链（根 → 最新顺序）

        沿 parent_record_id 回溯全链；某代父记录缺失时链在此中断（容错）。
        同一对象存在多条独立链时返回最近一条（文档化行为）。
        """
        with self._lock:
            self._ensure_loaded()
            all_recs = self._all_records_for_object(object_id)
            if not all_recs:
                return []
            # 找链头：不是任何同对象记录父代的记录（即最末代）
            parent_ids = {r.parent_record_id for r in all_recs
                          if r.parent_record_id}
            heads = [r for r in all_recs if r.record_id not in parent_ids]
            head = max(heads, key=lambda r: r.created_at)
            chain: List[EvolutionRecord] = []
            cur: Optional[EvolutionRecord] = head
            seen = set()
            while cur is not None and cur.record_id not in seen:
                seen.add(cur.record_id)
                chain.append(cur)
                if not cur.parent_record_id:
                    break
                cur = self._raw_get(cur.parent_record_id)
            chain.reverse()  # 根 → 最新
            return chain

    def query(self, filter: Optional[Dict[str, Any]] = None, *,
              limit: Optional[int] = None) -> List[EvolutionRecord]:
        """按字段等值过滤查询（活跃 + 归档）

        filter 支持:
            - 等值匹配: {"decision": "committed"}
            - 集合匹配: {"decision": ["committed", "rolled_back"]}
        归档摘要缺少的字段（如 change_summary）按空值处理，不参与匹配。
        """
        with self._lock:
            self._ensure_loaded()
            results: List[EvolutionRecord] = []
            for rec in self._all_records():
                if self._match(rec, filter):
                    results.append(rec)
                    if limit is not None and len(results) >= limit:
                        break
            return results

    def count(self, object_id: Optional[str] = None) -> int:
        """记录总数（活跃 + 归档）；指定 object_id 时统计该对象"""
        with self._lock:
            self._ensure_loaded()
            if object_id is None:
                return len(self._records) + len(self._summary_index)
            return len(self.list_by_object(object_id))

    # ─── 内部 ───

    def _ensure_loaded(self) -> None:
        """懒加载（损坏容错：坏行跳过 / 整文件损坏备份重建）"""
        with self._lock:
            if self._loaded:
                return
            active_dicts, _ = _read_jsonl(self._active_path)
            for d in active_dicts:
                try:
                    rec = EvolutionRecord.from_dict(d)
                    self._records.append(rec)
                    self._index[rec.record_id] = rec
                except (ValueError, TypeError):
                    logger.warning("[Lineage] 跳过活跃文件损坏条目")
            old_dicts, _ = _read_jsonl(self._old_path)
            for d in old_dicts:
                if isinstance(d, dict) and d.get("record_id"):
                    self._summary_index[d["record_id"]] = d
            self._loaded = True
            logger.info(
                "[Lineage] 加载完成 active=%s archive=%s path=%s",
                len(self._records), len(self._summary_index),
                self._active_path,
            )

    @staticmethod
    def _validate(record: EvolutionRecord) -> None:
        if not record.object_id:
            raise ValueError("EvolutionRecord.object_id 不能为空")
        if record.object_type not in OBJECT_TYPES:
            raise ValueError(
                f"非法 object_type: {record.object_type}（允许: {OBJECT_TYPES}）"
            )
        if record.decision not in DECISIONS:
            raise ValueError(
                f"非法 decision: {record.decision}（允许: {DECISIONS}）"
            )

    def _archive_excess(self) -> int:
        """按对象分层归档：超过活跃阈值的【最老】记录压缩为摘要移入归档

        返回本次归档条数；0 表示无需归档。
        """
        by_obj: Dict[str, List[EvolutionRecord]] = {}
        for r in self._records:
            by_obj.setdefault(r.object_id, []).append(r)
        excess_ids = set()
        for recs in by_obj.values():
            if len(recs) > self._active_generations:
                for r in recs[: len(recs) - self._active_generations]:
                    excess_ids.add(id(r))
        if not excess_ids:
            return 0
        removed = 0
        for r in list(self._records):
            if id(r) in excess_ids:
                self._records.remove(r)
                self._index.pop(r.record_id, None)
                self._summary_index[r.record_id] = r.to_summary()
                removed += 1
        return removed

    def _persist_active(self) -> None:
        lines = [json.dumps(r.to_dict(), ensure_ascii=False)
                 for r in self._records]
        _atomic_write_lines(self._active_path, lines)

    def _persist_archive(self) -> None:
        lines = [json.dumps(s, ensure_ascii=False)
                 for s in self._summary_index.values()]
        _atomic_write_lines(self._old_path, lines)

    def _raw_get(self, record_id: str) -> Optional[EvolutionRecord]:
        """内部查询（活跃完整记录或归档摘要），供谱系回溯使用"""
        rec = self._index.get(record_id)
        if rec is not None:
            return rec
        summary = self._summary_index.get(record_id)
        if summary is not None:
            return self._summary_to_record(summary)
        return None

    def _all_records(self) -> List[EvolutionRecord]:
        return list(self._records) + [
            self._summary_to_record(s) for s in self._summary_index.values()
        ]

    def _all_records_for_object(self, object_id: str) -> List[EvolutionRecord]:
        return [
            r for r in self._all_records()
            if r.object_id == object_id
        ]

    @staticmethod
    def _summary_to_record(summary: Dict[str, Any]) -> EvolutionRecord:
        """归档摘要 → 轻量 EvolutionRecord（archived=True，缺失字段为空）

        score 存于摘要顶层，映射回 eval_result 以便 get_score()/谱系打印可用。
        摘要未保留的字段显式置空，避免回落为 dataclass 默认值（如 strategy 误显示
        为 version_bump）造成审计误导。
        """
        rec = EvolutionRecord.from_dict(summary)
        rec.archived = True
        if summary.get("score") is not None:
            rec.eval_result = {"score": summary.get("score")}
        for name in _SUMMARY_DROPPED_FIELDS:
            setattr(rec, name, "")
        rec.cost = None
        return rec

    @staticmethod
    def _match(record: EvolutionRecord,
               filter: Optional[Dict[str, Any]]) -> bool:
        if not filter:
            return True
        for key, expect in filter.items():
            actual = getattr(record, key, None)
            if isinstance(expect, (list, tuple, set)):
                if actual not in expect:
                    return False
            elif actual != expect:
                return False
        return True


# ════════════════════════════════════════════════════════════
#  审计与查询入口
# ════════════════════════════════════════════════════════════

def print_lineage(object_id: str,
                  archive: Optional[EvolutionArchive] = None) -> str:
    """输出某对象完整进化链文本（含每代评分变化），供审计/日志/CLI 使用

    Args:
        object_id: 进化对象 ID
        archive: 档案库实例（None=使用默认档案库）

    Returns:
        多行文本；对象无记录时返回单行提示。
    """
    a = archive if archive is not None else get_default_archive()
    chain = a.get_lineage(object_id)
    if not chain:
        return f"进化谱系: {object_id}（无记录）"
    lines = [f"进化谱系: {object_id}（共 {len(chain)} 代）"]
    prev_score: Optional[float] = None
    for i, rec in enumerate(chain, 1):
        score = rec.get_score()
        delta = ""
        if score is not None and prev_score is not None:
            delta = f" (Δ{score - prev_score:+.2f})"
        mark = " [归档]" if rec.archived else ""
        lines.append(
            f"{i}. {rec.record_id}{mark} v{rec.parent_version or '-'}"
            f"→v{rec.new_version or '-'} strategy={rec.strategy or '-'}"
            f" decision={rec.decision}"
            f" score={score if score is not None else '-'}{delta}"
            f" @{rec.created_at}"
        )
        if score is not None:
            prev_score = score
    return "\n".join(lines)


# 默认档案库（进程内缓存单例，只读查询路由/CLI 使用；测试请自行实例化隔离）
_default_archive: Optional[EvolutionArchive] = None


def get_default_archive() -> EvolutionArchive:
    """获取进程内默认档案库（懒加载单例）"""
    global _default_archive
    if _default_archive is None:
        _default_archive = EvolutionArchive()
    return _default_archive
