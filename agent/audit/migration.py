"""存量 JSONL 审计只读归档 + 双写过渡（v7.2 §3.2 迁移策略）

【迁移策略（§3.2）】旧库只读 + 双写过渡 ≥1 版本 + 回滚窗口。

【本模块交付】
    1. **盘点**：`inventory_legacy_files()` 全量登记存量 JSONL 审计/事件文件
       （路径 / 大小 / 行数 / sha256 / 首尾时间戳 / 归档模式）。
    2. **只读归档（不删除、不追溯）**：`archive_legacy_files()` 对**纯审计轨**文件
       置只读（chmod 0o444）并登记清单；对**系统记录**（审批状态库、进化档案等，
       冻结会破坏运行时写入）只做**只读镜像副本**（`data/audit/legacy_archive/`），
       原文件保持可写——两者都**不删除、不导入链、不追溯**。
    3. **双写过渡**：`LegacyTrack.emit()` 同时写「旧 JSONL 轨」与「新链式轨」，
       以 `audit_ref` 关联键对齐；`verify_consistency()` 报告两轨一致率。
    4. **回滚窗口**：`AUDIT_LEGACY_WRITE=0`（或 `LegacyTrack.legacy_enabled=False`）
       即切回「仅旧轨」，无需改代码、不回滚数据。

【为什么区分两种归档模式】
    `data/audit/audit_YYYYMMDD.jsonl` 是**纯审计轨**（按日分片、旧日不再追加），
    故旧日文件可安全置只读；而 `approval_records.jsonl` / `evolution_archive.jsonl`
    是**系统记录本体**（审批状态、谱系真相），一旦置只读，运行时的原子重写会
    PermissionError —— 因此只做只读镜像，链上留审计，原文件继续由旧轨写入，
    待链式轨验收 ≥1 个 minor 后再行退役（`retire_after`）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import stat
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

from agent.audit.chain import SOURCE_MIGRATION, get_audit_chain

logger = logging.getLogger("agent.audit.migration")

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: 默认归档目录与清单
DEFAULT_ARCHIVE_DIR = os.path.join(_PROJECT_ROOT, "data", "audit", "legacy_archive")
DEFAULT_MANIFEST_PATH = os.path.join(_PROJECT_ROOT, "data", "audit",
                                     "legacy_archive_manifest.json")

_ENV_LEGACY_WRITE = "AUDIT_LEGACY_WRITE"

#: 归档模式
MODE_READONLY = "archive_readonly"    # 纯审计轨：置只读（不再追加）
MODE_MIRROR = "mirror_only"           # 系统记录本体：只做只读镜像，原文件继续写


@dataclass
class LegacyTarget:
    """一类存量文件的归档策略"""

    name: str
    pattern: str
    mode: str = MODE_MIRROR
    kind: str = "audit"          # audit / lineage / state / metrics
    note: str = ""


#: 存量写入面（步骤 1 盘点的载体侧；写入方清单见验收报告 §盘点）
LEGACY_TARGETS: Sequence[LegacyTarget] = (
    LegacyTarget("audit_jsonl", "data/audit/audit_*.jsonl", MODE_READONLY, "audit",
                 "agent/audit/logger.py::AuditLogger.log（按日分片，纯审计轨）"),
    LegacyTarget("skills_assessment_events", "data/skills_assessment_events*.jsonl",
                 MODE_READONLY, "audit",
                 "skills_mgmt 评审/评估事件（按日分片，纯事件轨）"),
    LegacyTarget("skills_digest_events", "data/skills_digest_events-*.jsonl",
                 MODE_READONLY, "audit", "skills_mgmt 摘要事件历史分片"),
    LegacyTarget("skills_mgmt_review_audit", "data/skills_mgmt_review_audit.jsonl",
                 MODE_READONLY, "audit", "技能评审审计轨（存在时）"),
    LegacyTarget("approval_records", "data/approval_records.jsonl", MODE_MIRROR, "state",
                 "审批状态本体（skills_mgmt/approval.py 原子重写）——不可冻结"),
    LegacyTarget("evolution_archive", "data/evolution_archive*.jsonl", MODE_MIRROR,
                 "lineage", "进化谱系本体（skills_mgmt/lineage.py）——不可冻结"),
    LegacyTarget("task_history", "data/task_history.jsonl", MODE_MIRROR, "metrics",
                 "任务历史（运行时计量，非审计轨）"),
    LegacyTarget("async_tasks", "data/async_tasks.jsonl", MODE_MIRROR, "metrics",
                 "异步任务轨迹（运行时计量）"),
)

#: 模块级：已归档文件的只读登记（路径 → 信息），供写入侧守卫
_ARCHIVED_READONLY: Dict[str, Dict[str, Any]] = {}


# ════════════════════════════════════════════════════════════
#  盘点
# ════════════════════════════════════════════════════════════


@dataclass
class LegacyFileInfo:
    """一个存量文件的盘点结果"""

    name: str
    path: str
    mode: str
    kind: str
    exists: bool = True
    size_bytes: int = 0
    line_count: int = 0
    sha256: str = ""
    first_ts: str = ""
    last_ts: str = ""
    read_only: bool = False
    archived_copy: str = ""
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _sha256_file(path: str, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def _scan_lines(path: str) -> Any:
    """(行数, 首时间戳, 末时间戳)（只读扫描；坏行跳过）"""
    count = 0
    first_ts = last_ts = ""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            count += 1
            try:
                rec = json.loads(line)
            except (ValueError, TypeError):
                continue
            if not isinstance(rec, dict):
                continue
            ts = str(rec.get("timestamp") or rec.get("ts") or rec.get("created_at")
                     or rec.get("updated_at") or "")
            if ts:
                if not first_ts:
                    first_ts = ts
                last_ts = ts
    return count, first_ts, last_ts


def is_readonly(path: str) -> bool:
    """文件是否只读（Windows 只读属性 / POSIX 无写位）"""
    try:
        st = os.stat(path)
    except OSError:
        return False
    if os.name == "nt":
        return not bool(st.st_mode & stat.S_IWRITE)
    return not bool(st.st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))


def legacy_write_allowed(path: str, *, retire_after: Optional[str] = None,
                         now: Optional[str] = None) -> bool:
    """双写过渡窗口判定：是否仍允许向旧 JSONL 追加

    - 已登记只读归档 → False（旧轨已退役）；
    - `retire_after`（YYYY-MM-DD）已过期 → False（退役窗口关闭）；
    - 否则 True（双写过渡期）。
    """
    if is_readonly(path) and os.path.exists(path):
        return False
    if retire_after:
        today = (now or datetime.now(timezone.utc).date().isoformat())[:10]
        if today > str(retire_after)[:10]:
            return False
    return True


def _resolve_targets(root: str,
                     targets: Optional[Iterable[LegacyTarget]] = None) -> List[LegacyTarget]:
    return list(targets if targets is not None else LEGACY_TARGETS)


def _expand(root: str, pattern: str) -> List[str]:
    import glob as _glob
    return sorted(_glob.glob(os.path.join(root, pattern)))


def inventory_legacy_files(root: Optional[str] = None, *,
                           targets: Optional[Iterable[LegacyTarget]] = None
                           ) -> List[LegacyFileInfo]:
    """盘点存量 JSONL 审计/事件文件（只读；不修改任何文件）"""
    base = os.path.abspath(root or _PROJECT_ROOT)
    out: List[LegacyFileInfo] = []
    for tgt in _resolve_targets(base, targets):
        paths = _expand(base, tgt.pattern)
        if not paths:
            out.append(LegacyFileInfo(name=tgt.name, path=os.path.join(base, tgt.pattern),
                                      mode=tgt.mode, kind=tgt.kind, exists=False,
                                      note=tgt.note))
            continue
        for p in paths:
            info = LegacyFileInfo(name=tgt.name, path=p, mode=tgt.mode, kind=tgt.kind,
                                  note=tgt.note)
            try:
                info.size_bytes = os.path.getsize(p)
                info.line_count, info.first_ts, info.last_ts = _scan_lines(p)
                info.sha256 = _sha256_file(p)
                info.read_only = is_readonly(p)
            except OSError as e:  # noqa: BLE001 权限/占用 → 记录并继续
                info.exists = False
                info.note = f"{tgt.note}｜扫描失败: {e}"
            out.append(info)
    return out


# ════════════════════════════════════════════════════════════
#  只读归档
# ════════════════════════════════════════════════════════════


@dataclass
class ArchiveReport:
    """归档结果清单"""

    archived_at: str = ""
    root: str = ""
    archive_dir: str = ""
    manifest_path: str = ""
    files: List[LegacyFileInfo] = field(default_factory=list)
    readonly_count: int = 0
    mirror_count: int = 0
    missing_count: int = 0
    errors: List[str] = field(default_factory=list)
    deleted: int = 0        # 恒为 0：不删除（显式留证）
    retraced: int = 0       # 恒为 0：不追溯（不导入链）

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["files"] = [f.to_dict() if isinstance(f, LegacyFileInfo) else f
                      for f in self.files]
        return d


def archive_legacy_files(root: Optional[str] = None, *,
                         archive_dir: Optional[str] = None,
                         manifest_path: Optional[str] = None,
                         targets: Optional[Iterable[LegacyTarget]] = None,
                         skip_active: bool = True,
                         dry_run: bool = False) -> ArchiveReport:
    """存量 JSONL 只读归档（**不删除、不追溯**）

    Args:
        root: 项目根（默认仓库根）。
        archive_dir: 镜像副本目录，默认 `data/audit/legacy_archive/`。
        manifest_path: 清单路径，默认 `data/audit/legacy_archive_manifest.json`。
        skip_active: 跳过「今日/最近修改」的活动分片（避免冻结仍在写的文件），
            默认 True；`MODE_MIRROR` 类文件不受影响（本就不冻结）。
        dry_run: True 仅盘点不落盘（用于报告预览）。

    Returns:
        ArchiveReport：逐文件归档结论 + 清单路径（deleted/retraced 恒为 0）。
    """
    base = os.path.abspath(root or _PROJECT_ROOT)
    arc_dir = os.path.abspath(archive_dir or os.path.join(base, "data", "audit",
                                                          "legacy_archive"))
    manifest = os.path.abspath(manifest_path or os.path.join(
        base, "data", "audit", "legacy_archive_manifest.json"))
    report = ArchiveReport(archived_at=datetime.now(timezone.utc).isoformat(),
                           root=base, archive_dir=arc_dir, manifest_path=manifest)
    infos = inventory_legacy_files(base, targets=targets)
    active = _active_files(base, infos)

    for info in infos:
        if not info.exists:
            report.missing_count += 1
            report.files.append(info)
            continue
        try:
            if info.mode == MODE_READONLY:
                if skip_active and info.path in active:
                    info.note = f"{info.note}｜活动分片跳过冻结（仍由旧轨写入）"
                    report.files.append(info)
                    continue
                if not dry_run:
                    ok = _set_readonly(info.path)
                    info.read_only = ok
                    if ok:
                        _ARCHIVED_READONLY[os.path.abspath(info.path)] = info.to_dict()
                else:
                    info.read_only = True
                report.readonly_count += 1
            else:
                copy_path = os.path.join(arc_dir, os.path.basename(info.path))
                if not dry_run:
                    os.makedirs(arc_dir, exist_ok=True)
                    # 幂等重跑：目的副本可能是上次留下的只读文件 → 先恢复写权限
                    if os.path.exists(copy_path):
                        try:
                            os.chmod(copy_path, 0o644)
                        except OSError:
                            pass
                    shutil.copy2(info.path, copy_path)
                    _set_readonly(copy_path)
                    info.archived_copy = copy_path
                else:
                    info.archived_copy = copy_path
                report.mirror_count += 1
        except Exception as e:  # noqa: BLE001 单个文件失败不阻断整体归档
            report.errors.append(f"{info.path}: {type(e).__name__}: {e}")
            logger.warning("存量归档失败 %s: %s", info.path, e)
        report.files.append(info)

    if not dry_run:
        try:
            os.makedirs(os.path.dirname(manifest) or ".", exist_ok=True)
            with open(manifest, "w", encoding="utf-8") as f:
                json.dump(report.to_dict(), f, ensure_ascii=False, indent=2)
        except OSError as e:  # noqa: BLE001 清单写入失败 → 记录但不抛
            report.errors.append(f"manifest: {e}")
            logger.warning("归档清单写入失败: %s", e)
    return report


def _active_files(base: str, infos: Sequence[LegacyFileInfo]) -> set:
    """判定「活动分片」：按日分片命名中含今日日期者（仍在写入）"""
    today = datetime.now(timezone.utc).date().isoformat()
    compact = today.replace("-", "")
    active = set()
    for info in infos:
        name = os.path.basename(info.path)
        if today in name or compact in name:
            active.add(info.path)
    return active


def _set_readonly(path: str) -> bool:
    """置只读（best-effort；单机降级保护，非安全边界）"""
    try:
        os.chmod(path, 0o444)
        return True
    except OSError as e:  # noqa: BLE001
        logger.debug("置只读失败 %s: %s", path, e)
        return False


def read_legacy_records(path: str, *, limit: Optional[int] = None,
                        offset: int = 0) -> List[Dict[str, Any]]:
    """只读读取存量 JSONL（不修改、不追加、不导入链）——追溯查询兼容入口"""
    out: List[Dict[str, Any]] = []
    if not os.path.exists(path):
        return out
    skipped = 0
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if skipped < offset:
                skipped += 1
                continue
            try:
                rec = json.loads(line)
            except (ValueError, TypeError):
                continue
            if isinstance(rec, dict):
                out.append(rec)
            if limit is not None and len(out) >= int(limit):
                break
    return out


# ════════════════════════════════════════════════════════════
#  双写过渡
# ════════════════════════════════════════════════════════════


@dataclass
class DualWriteResult:
    """一次双写的结果"""

    audit_ref: str
    legacy_written: bool = False
    chain_written: bool = False
    chain_seq: int = 0
    chain_self_hash: str = ""
    rollback: bool = False          # 本次是否处于「仅旧轨」回滚窗口
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ConsistencyReport:
    """双写过渡期「新旧两轨一致性」报告"""

    legacy_count: int = 0
    chain_count: int = 0
    matched: int = 0
    only_legacy: List[str] = field(default_factory=list)
    only_chain: List[str] = field(default_factory=list)

    @property
    def consistent(self) -> bool:
        return not self.only_legacy and not self.only_chain

    @property
    def match_rate(self) -> float:
        total = max(self.legacy_count, self.chain_count)
        return 1.0 if total == 0 else round(self.matched / total, 4)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["consistent"] = self.consistent
        d["match_rate"] = self.match_rate
        return d

    def summary(self) -> str:
        return (f"新旧轨一致={'是' if self.consistent else '否'} "
                f"旧轨 {self.legacy_count} 条 / 新链 {self.chain_count} 条 / "
                f"匹配 {self.matched} 条（一致率 {self.match_rate:.2%}）")


class LegacyTrack:
    """双写过渡轨：旧 JSONL + 新链式轨

    Args:
        path: 旧 JSONL 路径（如 `data/audit/audit_20260910.jsonl`）。
        facade: 审计门面（写链）；None → 进程级门面。
        name: 轨名（默认取文件名）；随链载荷 `audit_track` 字段落链，供一致性校验
            按轨过滤（避免把其它轨的关联键算作「仅新链」）。
        legacy_enabled: 是否写旧轨（None → 环境变量 AUDIT_LEGACY_WRITE，默认 1）。
        retire_after: 旧轨退役日期（YYYY-MM-DD）；过期后旧轨自动停写（回滚窗口关闭）。
        key_of_legacy / key_of_chain: 一致性比对键提取器（默认取 `audit_ref`）。
    """

    def __init__(self, path: str, *, facade: Any = None, name: str = "",
                 legacy_enabled: Optional[bool] = None,
                 retire_after: Optional[str] = None,
                 key_of_legacy: Optional[Callable[[Dict[str, Any]], str]] = None,
                 key_of_chain: Optional[Callable[[Any], str]] = None):
        self._path = os.path.abspath(path)
        # 轨名默认取「文件名 + 绝对路径短哈希」：同名文件目录并存时仍唯一，
        # 保证 verify_consistency 的 audit_track 过滤不会串轨
        self._name = name or (
            f"{os.path.basename(self._path)}:{hashlib.sha256(self._path.encode()).hexdigest()[:8]}")
        self._facade = facade
        self._legacy_env = legacy_enabled
        self._retire_after = retire_after
        self._key_legacy = key_of_legacy or (
            lambda r: str((r or {}).get("audit_ref") or ""))
        self._key_chain = key_of_chain or _default_chain_key

    # ── 开关 ────────────────────────────────────────────────

    @property
    def path(self) -> str:
        return self._path

    @property
    def name(self) -> str:
        return self._name

    @property
    def legacy_enabled(self) -> bool:
        """旧轨是否仍写（双写过渡/回滚窗口判定）"""
        if self._legacy_env is not None and not self._legacy_env:
            return False
        if self._legacy_env is None:
            if os.getenv(_ENV_LEGACY_WRITE, "1").strip().lower() in (
                    "0", "false", "no", "off"):
                return False
        if os.path.abspath(self._path) in _ARCHIVED_READONLY:
            return False
        return legacy_write_allowed(self._path, retire_after=self._retire_after)

    @property
    def in_rollback_window(self) -> bool:
        """回滚窗口：旧轨在写、新链停写（AUDIT_CHAIN_ENABLED=0 或门面关闭）"""
        facade = self._facade_obj()
        return self.legacy_enabled and not (facade is not None and facade.enabled)

    def _facade_obj(self) -> Any:
        if self._facade is not None:
            return self._facade
        from agent.audit.facade import audit
        return audit

    # ── 写入 ────────────────────────────────────────────────

    def emit(self, action: str, actor: str = "", subject: str = "",
             payload: Optional[Dict[str, Any]] = None, *,
             legacy_record: Optional[Dict[str, Any]] = None,
             source: str = "agent", audit_ref: str = "",
             ts: Any = None, trace_id: str = "",
             workspace_id: str = "") -> DualWriteResult:
        """双写一条：旧 JSONL（可选）+ 新链式轨

        `legacy_record` 由调用方给出（保持旧格式逐字不变，仅追加 `audit_ref` 关联键）；
        新轨载荷含同一 `audit_ref`，供 `verify_consistency()` 对齐两轨。
        """
        ref = audit_ref or uuid.uuid4().hex[:24]
        result = DualWriteResult(audit_ref=ref)
        record = dict(legacy_record or {})
        record.setdefault("audit_ref", ref)

        if self.legacy_enabled:
            try:
                os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
                with open(self._path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
                result.legacy_written = True
            except Exception as e:  # noqa: BLE001 旧轨失败不阻断新轨（反之亦然）
                result.errors.append(f"legacy: {type(e).__name__}: {e}")

        body = dict(payload or {})
        facade = self._facade_obj()
        if facade is not None and getattr(facade, "enabled", False):
            entry = facade.record(action, actor=actor, subject=subject, payload=body,
                                  source=source, ts=ts, trace_id=trace_id,
                                  workspace_id=workspace_id,
                                  technical={"audit_ref": ref,
                                             "audit_track": self._name})
            if entry is not None:
                result.chain_written = True
                result.chain_seq = entry.seq
                result.chain_self_hash = entry.self_hash
        # 回滚窗口：本次只有旧轨在记录（新链未写）
        result.rollback = result.legacy_written and not result.chain_written
        return result

    # ── 一致性校验 ──────────────────────────────────────────

    def verify_consistency(self, *, limit: Optional[int] = None,
                           chain_kwargs: Optional[Dict[str, Any]] = None
                           ) -> ConsistencyReport:
        """比对新旧两轨（按 `audit_ref` 关联键 + `audit_track` 轨名过滤）

        过渡期两轨必须一致：旧轨每条都能在链上找到同一 `audit_ref`，反之亦然。
        链侧按载荷 `audit_track == 本轨名` 过滤（避免把其它轨的关联键误判为「仅新链」）。
        """
        report = ConsistencyReport()
        legacy_recs = read_legacy_records(self._path, limit=limit)
        legacy_keys: List[str] = []
        for rec in legacy_recs:
            key = self._key_legacy(rec)
            if key:
                legacy_keys.append(key)
        report.legacy_count = len(legacy_recs)

        facade = self._facade_obj()
        chain = getattr(facade, "chain", None) if facade is not None else None
        chain_keys: List[str] = []
        if chain is not None:
            entries = chain.entries(**(chain_kwargs or {}))
            for e in entries:
                payload = getattr(e, "payload", None) or {}
                if str(payload.get("audit_track") or "") != self._name:
                    continue
                key = self._key_chain(e)
                if key:
                    chain_keys.append(key)
                if limit is not None and len(chain_keys) >= int(limit):
                    break
        report.chain_count = len(chain_keys)

        legacy_set, chain_set = set(legacy_keys), set(chain_keys)
        report.matched = len(legacy_set & chain_set)
        report.only_legacy = sorted(legacy_set - chain_set)
        report.only_chain = sorted(chain_set - legacy_set)
        return report


def record_migration_event(action: str, *, actor: str = "system",
                           subject: str = "", payload: Optional[Dict[str, Any]] = None,
                           db_path: Optional[str] = None) -> int:
    """把归档/迁移动作本身记入链（迁移也要留痕，且与 UI/Agent 同表）"""
    chain = get_audit_chain(db_path)
    entry = chain.append(action, actor=actor, subject=subject,
                         payload=payload or {}, source=SOURCE_MIGRATION)
    chain.flush()
    return entry.seq


def _default_chain_key(entry: Any) -> str:
    """默认关联键提取：链载荷顶层的 `audit_ref`（兼容嵌套在 payload.payload 的情形）"""
    payload = getattr(entry, "payload", None) or {}
    ref = payload.get("audit_ref")
    if not ref:
        nested = payload.get("payload")
        if isinstance(nested, dict):
            ref = nested.get("audit_ref")
    return str(ref or "")


__all__ = [
    "ArchiveReport", "ConsistencyReport", "DEFAULT_ARCHIVE_DIR", "DEFAULT_MANIFEST_PATH",
    "DualWriteResult", "LEGACY_TARGETS", "LegacyFileInfo", "LegacyTarget", "LegacyTrack",
    "MODE_MIRROR", "MODE_READONLY", "archive_legacy_files", "inventory_legacy_files",
    "is_readonly", "legacy_write_allowed", "read_legacy_records",
    "record_migration_event",
]
