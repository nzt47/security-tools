"""温/冷归档执行器（TASK-S8-01 步骤 2）。

【三层各自的归属】
    热  近期可查 → 不动。
    温  活动文件里的历史行按日分片 → **复用既有**
        `agent/skills_mgmt/log_archiver.archive_daily_file`（不自建第二套分片语义）。
    冷  整文件自描述压缩包 → 本模块；落地 `data/archive/<class>/<period>.<ext>.gz`
        + `<period>.manifest.json`。
    删  仅策略表标「可删」的类，且**必须先过 `PurgeGuard`**；默认全关。

【首跑必须 dry-run】
    `plan()` 只读：不写任何文件（含不建目录、不做 SQLite 备份），输出"将处理哪些文件、
    多少条、多少字节"。`run()` 必须显式 `confirm=True` 才落盘 —— 没有 `confirm`
    时直接返回 dry-run 报告，**不静默降级为执行**。

【为什么 SQLite 用一致性备份而不是文件复制】
    活动库开着 WAL 且有后台写线程，文件级复制会得到"页撕裂"副本（能打开、行数不对）。
    故走 `sqlite3.Connection.backup()`（官方在线备份 API）→ 副本入包。
    副本字节的 sha256 ≠ 活动库的 sha256，所以还原一致性用**行级摘要**
    （`ArchivedFile.row_digest`）判定，而不是字节比对。
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from agent.retention.manifest import (
    ARCHIVE_SCHEMA,
    ARCHIVE_SCHEMA_VERSION,
    CODEC_GZIP,
    ArchiveManifest,
    build_payload,
    compress,
    sha256_bytes,
    sha256_file,
    utc_now_iso,
)
from agent.retention.policy import (
    KIND_JSONL,
    KIND_SQLITE,
    KIND_TREE,
    PROJECT_ROOT,
    RetentionClass,
    RetentionPolicy,
    load_policy,
)
from agent.retention.scan import (
    cold_files,
    expand,
    file_day,
    file_time_range,
    sqlite_consistent_backup,
    sqlite_row_count,
    sqlite_row_digest,
    warm_plan,
)

logger = logging.getLogger("agent.retention.archiver")

#: 归档件扩展名（按形态自描述；manifest 恒为 `<period>.manifest.json`）
EXT_BY_KIND = {
    KIND_JSONL: ".jsonl.gz",
    KIND_SQLITE: ".db.gz",
    KIND_TREE: ".files.gz",
}
DEFAULT_EXT = ".pack.gz"

#: 审计动作名（验收要求逐字：`action="retention.run"`）
AUDIT_ACTION = "retention.run"
ACTOR = "retention"

#: 事件类型（与 `agent/observability/events.py::EV_RETENTION_RUN` 同源）
EVENT_TYPE = "retention.run"


# ════════════════════════════════════════════════════════════
#  报告模型
# ════════════════════════════════════════════════════════════


@dataclass
class ClassOutcome:
    """一类的归档结果（dry-run 时是"计划"，字段语义一致，便于逐条对照）。"""

    class_id: str = ""
    title: str = ""
    kind: str = ""
    status: str = "planned"        # planned / packed / exists / empty / skipped / error
    reason: str = ""
    warm: Dict[str, Any] = field(default_factory=dict)
    cold_files: List[str] = field(default_factory=list)
    cold_bytes: int = 0
    cold_records: int = 0
    archives: List[Dict[str, Any]] = field(default_factory=list)
    purge: Dict[str, Any] = field(default_factory=dict)
    deleted: List[str] = field(default_factory=list)
    deleted_bytes: int = 0
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RetentionReport:
    """一次保留策略运行的完整报告（dry-run 与执行共用同一结构）。"""

    dry_run: bool = True
    period: str = ""
    started_at: str = ""
    finished_at: str = ""
    schema: str = ARCHIVE_SCHEMA
    schema_version: int = ARCHIVE_SCHEMA_VERSION
    root: str = ""
    archive_dir: str = ""
    classes: List[ClassOutcome] = field(default_factory=list)
    totals: Dict[str, Any] = field(default_factory=dict)
    manifests: List[str] = field(default_factory=list)
    audit: Dict[str, Any] = field(default_factory=dict)
    event: Dict[str, Any] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["classes"] = [c if isinstance(c, dict) else c.to_dict() for c in self.classes]
        return d

    def summary_lines(self) -> List[str]:
        """人读摘要（CLI 与验收报告直接引用，数字全部来自真实扫描/执行）。"""
        mode = "DRY-RUN（未落盘）" if self.dry_run else "执行"
        out = [f"[{mode}] 周期={self.period} 归档目录={self.archive_dir}"]
        for c in self.classes:
            if c.status == "empty":
                out.append(f"  - {c.class_id:<22} 无待处理数据（{c.reason}）")
                continue
            if c.status == "skipped":
                out.append(f"  - {c.class_id:<22} 跳过：{c.reason}")
                continue
            if c.status == "error":
                out.append(f"  - {c.class_id:<22} 失败：{c.error}")
                continue
            arch = "、".join(os.path.basename(a.get("archive_file", ""))
                            for a in c.archives) or "-"
            line = (f"  - {c.class_id:<22} 冷归档 {len(c.cold_files)} 文件 / "
                    f"{c.cold_records} 记录 / {c.cold_bytes} 字节 → {arch}")
            if c.warm.get("enabled") and c.warm.get("lines"):
                line += (f"；温层移出 {c.warm['lines']} 行"
                         f"（{len(c.warm.get('buckets') or {})} 个分片）")
            if c.purge:
                if c.purge.get("executed") or c.deleted:
                    tag = f"已删除 {len(c.deleted)} 文件"
                elif c.purge.get("allowed"):
                    tag = ("护栏放行但未执行（dry-run）" if self.dry_run
                           else "护栏放行但未执行（总开关关闭）")
                else:
                    tag = f"拒绝删除（{c.purge.get('code')}）"
                line += f"；{tag}"
            out.append(line)
        t = self.totals
        out.append(f"  合计：归档 {t.get('archives', 0)} 个包 / "
                   f"{t.get('archived_files', 0)} 文件 / {t.get('archived_records', 0)} 记录 / "
                   f"{t.get('archived_bytes', 0)} 字节（压缩前）；"
                   f"删除 {t.get('deleted_files', 0)} 文件 / {t.get('deleted_bytes', 0)} 字节")
        return out

    def markdown(self) -> str:
        """Markdown 报告（供验收报告粘贴；数字与 `summary_lines` 同源）。"""
        lines = ["| 数据类 | 状态 | 冷归档文件 | 记录数 | 压缩前字节 | 归档件 | 删除 |",
                 "|---|---|---|---|---|---|---|"]
        for c in self.classes:
            arch = "、".join(os.path.basename(a.get("archive_file", ""))
                            for a in c.archives) or "-"
            if c.purge.get("executed"):
                purge = f"已删除 {len(c.deleted)}"
            elif c.purge.get("allowed"):
                purge = "放行未执行"
            elif c.purge:
                purge = f"拒绝（{c.purge.get('code')}）"
            else:
                purge = "不适用"
            lines.append(f"| `{c.class_id}` | {c.status} | {len(c.cold_files)} | "
                         f"{c.cold_records} | {c.cold_bytes} | {arch} | {purge} |")
        return "\n".join(lines)


# ════════════════════════════════════════════════════════════
#  归档器
# ════════════════════════════════════════════════════════════


class Archiver:
    """温/冷归档执行器（`plan()` 只读；`run()` 需显式确认）。"""

    def __init__(self, policy: Optional[RetentionPolicy] = None, *,
                 root: str = "",
                 archive_dir: str = "",
                 clock: Optional[Callable[[], datetime]] = None,
                 audit: bool = True,
                 emit_events: bool = True,
                 backup_dir: str = "") -> None:
        """
        Args:
            policy: 策略表（None → `load_policy()`）。
            root: 仓库根（None → 项目根）。测试可指向临时目录。
            archive_dir: 归档目录（None → 策略的 `archive_dir`）。
            clock: 注入时钟（返回 `datetime`；测试跨午夜/时区用）。
            audit: 是否写链式审计（每次执行一条 `retention.run`）。
            emit_events: 是否发事件（`retention.run`）。
            backup_dir: SQLite 一致性备份的临时目录（None → 系统临时目录）。
        """
        self.policy = policy or load_policy()
        self.root = os.path.abspath(root or PROJECT_ROOT)
        self.archive_dir = os.path.abspath(
            archive_dir or self.policy.archive_dir)
        self._clock = clock or (lambda: datetime.now())
        self.audit = bool(audit)
        self.emit_events = bool(emit_events)
        self.backup_dir = backup_dir or ""

    # ── 时间 ─────────────────────────────────────────────
    def now(self) -> datetime:
        value = self._clock()
        if isinstance(value, datetime):
            return value
        return datetime.fromtimestamp(float(value))

    # ── 计划（只读）─────────────────────────────────────
    def plan(self) -> RetentionReport:
        """**不落盘**的计划：列出将归档/删除的清单与体积。"""
        return self._run(execute=False)

    def run(self, *, confirm: bool = False) -> RetentionReport:
        """执行归档。未显式 `confirm=True` → 等价于 `plan()`（**不静默执行**）。"""
        if not confirm:
            report = self.plan()
            report.notes.append(
                "未传入 confirm=True：按首跑纪律只做 dry-run，未落盘任何文件")
            return report
        return self._run(execute=True)

    # ── 主流程 ───────────────────────────────────────────
    def _run(self, *, execute: bool) -> RetentionReport:
        now = self.now()
        period = now.date().isoformat()
        report = RetentionReport(
            dry_run=not execute, period=period, started_at=utc_now_iso(),
            root=self.root, archive_dir=self.archive_dir,
        )
        for cls in self.policy.selected():
            outcome = self._class_run(cls, now, period, execute=execute)
            report.classes.append(outcome)
        report.finished_at = utc_now_iso()
        report.manifests = [a["manifest_file"]
                            for c in report.classes for a in c.archives
                            if a.get("manifest_file")]
        report.totals = self._totals(report)
        if execute:
            report.audit = self._write_audit(report)
            report.event = self._emit_event(report)
        else:
            report.notes.append(
                "dry-run：未写归档件、未写审计、未改任何源文件")
        return report

    # ── 单类 ─────────────────────────────────────────────
    def _class_run(self, cls: RetentionClass, now: datetime, period: str, *,
                   execute: bool) -> ClassOutcome:
        out = ClassOutcome(class_id=cls.class_id, title=cls.title, kind=cls.kind)
        try:
            out.warm = warm_plan(cls, self.root, now=now)

            # 温层先做（仅在执行态）：分片产物会成为本轮之后的冷数据候选。
            # dry-run 不落盘 ⇒ 分片还不存在，故本轮冷清单按**当前磁盘状态**如实给出，
            # 并在 note 里说明"本轮温层移出的行将在下一次运行进入冷层"。
            if execute and out.warm.get("enabled") and out.warm.get("lines"):
                out.warm["result"] = self._do_warm(cls, out.warm)

            cold = self._cold_selection(cls, now)
            out.cold_files = cold
            out.cold_records = self._records_of(cls, cold)
            out.cold_bytes = sum(self._size(p) for p in cold)

            if not cold:
                out.status = "empty"
                out.reason = (f"无冷数据（{cls.cold_days} 天阈值内）"
                              if cls.kind != KIND_SQLITE else "库文件不存在")
                if out.warm.get("lines"):
                    out.reason += ("；本轮温层移出 "
                                   f"{out.warm['lines']} 行，其分片将在下次运行进入冷层")
                out.purge = self._purge_decision(cls, [], executed=False)
                return out

            # 冷归档：按"归属日"分组，每组一个自描述包
            if execute:
                out.archives = self._pack_groups(cls, cold, period, now)
                out.status = ("packed" if any(a.get("status") == "packed"
                                              for a in out.archives) else "exists")
            else:
                out.archives = self._plan_groups(cls, cold, period, now)
                out.status = "planned"

            # 删除（默认关闭：策略 delete_source=false 时不进入）
            # 归一化键：Windows 的 `normcase` 会小写化，两侧必须同一口径比对
            archived_now = {os.path.normcase(os.path.abspath(p))
                            for a in out.archives if a.get("verified")
                            for p in (a.get("sources") or [])}
            out.purge = self._purge_decision(cls, cold, executed=execute)
            if execute and self.policy.delete_source and out.purge.get("allowed"):
                deleted, deleted_bytes = self._do_purge(cls, cold, archived_now)
                out.deleted = deleted
                out.deleted_bytes = deleted_bytes
            return out
        except Exception as e:  # noqa: BLE001 单类失败不拖垮整轮
            logger.error("[Archiver] %s 归档失败：%s", cls.class_id, e, exc_info=True)
            out.status = "error"
            out.error = f"{type(e).__name__}: {e}"
            return out

    # ── 冷数据选片 ───────────────────────────────────────
    def _cold_selection(self, cls: RetentionClass, now: datetime) -> List[str]:
        """冷层选片。

        - JSONL / 树：`cold_files()`（按归属日 < now - cold_days）。
        - SQLite：**整库快照**（无法按日切片），只要库存在即入选；
          这也是 `audit_chain` 的取数方式（只归档不删除 ⇒ 不影响链序）。
        """
        if cls.kind == KIND_SQLITE:
            return [p for p in expand(cls, self.root) if os.path.isfile(p)]
        cold, _hot = cold_files(cls, self.root, now=now)
        return cold

    def _records_of(self, cls: RetentionClass, files: Sequence[str]) -> int:
        total = 0
        for path in files:
            try:
                if cls.kind == KIND_JSONL:
                    total += file_time_range(path)[2]
                elif cls.kind == KIND_SQLITE:
                    total += sqlite_row_count(path, cls.sqlite_table)
                else:
                    total += 1
            except OSError:
                continue
        return total

    @staticmethod
    def _size(path: str) -> int:
        try:
            return os.path.getsize(path)
        except OSError:
            return 0

    # ── 温层执行 ─────────────────────────────────────────
    def _do_warm(self, cls: RetentionClass, plan: Dict[str, Any]) -> Dict[str, Any]:
        """调用**既有** `log_archiver.archive_daily_file` 做按日分片。

        失败只记录不抛（与 `EventStore._maybe_archive` 同一降级铁律）。
        """
        try:
            from agent.skills_mgmt.log_archiver import archive_daily_file
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"log_archiver 不可用：{e}"}
        results: List[Dict[str, Any]] = []
        moved = 0
        for path in plan.get("targets") or []:
            try:
                res = archive_daily_file(path)
                moved += int(res.get("archived") or 0)
                results.append({"path": path, "archived": int(res.get("archived") or 0),
                                "files": res.get("files") or []})
            except Exception as e:  # noqa: BLE001 温层失败不影响冷归档
                logger.warning("[Archiver] 温层归档失败 %s: %s", path, e)
                results.append({"path": path, "error": str(e)})
        return {"ok": True, "moved": moved, "results": results}

    # ── 冷归档：计划 vs 执行 ─────────────────────────────
    def _period_groups(self, cls: RetentionClass, files: Sequence[str],
                       fallback: str) -> Dict[str, List[str]]:
        groups: Dict[str, List[str]] = {}
        for path in files:
            day = file_day(path) or fallback
            groups.setdefault(day, []).append(path)
        return groups

    def _archive_path(self, cls: RetentionClass, period: str) -> str:
        ext = EXT_BY_KIND.get(cls.kind, DEFAULT_EXT)
        return os.path.join(self.archive_dir, cls.class_id, period + ext)

    def _plan_groups(self, cls: RetentionClass, files: Sequence[str],
                     fallback: str, now: datetime) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for period, group in sorted(self._period_groups(cls, files, fallback).items()):
            target = self._archive_path(cls, period)
            out.append({
                "status": "planned",
                "period": period,
                "period_kind": "snapshot" if cls.kind == KIND_SQLITE else "day",
                "source": os.path.abspath(group[0]) if len(group) == 1 else "",
                "source_count": len(group),
                "sources": [os.path.abspath(p) for p in group],
                "archive_file": target,
                "manifest_file": target + ".manifest.json",
                "exists": os.path.exists(target),
                "records": self._records_of(cls, group),
                "payload_bytes": sum(self._size(p) for p in group),
                "note": ("已存在归档件（将按幂等规则比对，不覆盖）"
                         if os.path.exists(target) else ""),
            })
        return out

    def _pack_groups(self, cls: RetentionClass, files: Sequence[str],
                     fallback: str, now: datetime) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for period, group in sorted(self._period_groups(cls, files, fallback).items()):
            out.append(self._pack_one(cls, group, period, now))
        return out

    # ── 幂等判定 ─────────────────────────────────────────
    def _sources_unchanged(self, manifest: ArchiveManifest,
                           files: Sequence[str]) -> bool:
        """源文件集与内容是否与既有清单一致（幂等/冲突判定的唯一依据）。

        - 普通文件：比 `sha256`（字节级）。
        - SQLite 备份副本：载荷存的是**副本**，其 sha256 必然不同于活动库，
          故比 `row_digest`（行级，与页布局无关）。
        任一源文件已被改动 → 视为"同名归档件冲突"，**拒绝覆盖**。
        """
        recorded = {os.path.normcase(os.path.abspath(f.path)): f
                    for f in manifest.files}
        current = {os.path.normcase(os.path.abspath(p)) for p in files}
        if set(recorded) != current:
            return False
        for norm, item in recorded.items():
            if item.backup_copy:
                digest = sqlite_row_digest(norm, manifest.sqlite_table)
                if not digest or digest != item.row_digest:
                    return False
                continue
            try:
                if sha256_file(norm) != item.sha256:
                    return False
            except OSError:
                return False
        return True

    def _pack_one(self, cls: RetentionClass, files: Sequence[str],
                  period: str, now: datetime) -> Dict[str, Any]:
        """打包一组文件为自描述归档件（已存在则幂等比对，**不覆盖**）。"""
        target = self._archive_path(cls, period)
        manifest_path = target + ".manifest.json"
        info: Dict[str, Any] = {
            "status": "planned", "period": period,
            "period_kind": "snapshot" if cls.kind == KIND_SQLITE else "day",
            "source": os.path.abspath(files[0]) if len(files) == 1 else "",
            "source_count": len(files), "sources": [os.path.abspath(p) for p in files],
            "archive_file": target, "manifest_file": manifest_path,
            "records": self._records_of(cls, files),
            "payload_bytes": sum(self._size(p) for p in files),
        }
        if os.path.exists(manifest_path):
            try:
                old = ArchiveManifest.read(manifest_path)
            except Exception as e:  # noqa: BLE001 旧清单坏了 → 报错不覆盖
                info.update(status="error", error=f"既有清单不可读：{e}")
                return info
            same = self._sources_unchanged(old, files)
            if same and old.verify_archive_bytes():
                info.update(status="exists", verified=True,
                            archive_bytes=old.archive_bytes,
                            record_count=old.record_count,
                            note="归档件已存在且校验通过，跳过（幂等）")
                return info
            info.update(status="error",
                        error="同名归档件已存在但源内容/文件集与清单不一致：拒绝覆盖"
                              "（改周期或先人工处置）")
            return info

        tmp_dir = ""
        try:
            backup_map: Dict[str, str] = {}
            if cls.kind == KIND_SQLITE:
                tmp_dir = tempfile.mkdtemp(prefix="cp_retention_",
                                          dir=self.backup_dir or None)
                for path in files:
                    dest = os.path.join(tmp_dir, os.path.basename(path) + ".backup")
                    if not sqlite_consistent_backup(path, dest):
                        info.update(status="error",
                                    error=f"SQLite 一致性备份失败：{path}")
                        return info
                    backup_map[os.path.abspath(path)] = dest
            payload, entries = build_payload(
                files, base_root=self.root, kind=cls.kind,
                sqlite_table=cls.sqlite_table, backup_copies=backup_map)
            blob = compress(payload, CODEC_GZIP)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            tmp_pack = target + ".tmp"
            with open(tmp_pack, "wb") as fh:
                fh.write(blob)
            os.replace(tmp_pack, target)

            firsts = [e.first_ts for e in entries if e.first_ts]
            lasts = [e.last_ts for e in entries if e.last_ts]
            manifest = ArchiveManifest(
                class_id=cls.class_id, period=period,
                period_kind=info["period_kind"], created_at=utc_now_iso(),
                codec=CODEC_GZIP, archive_file=target,
                sqlite_table=cls.sqlite_table,
                record_count=sum(e.records for e in entries),
                file_count=len(entries), payload_bytes=len(payload),
                archive_bytes=len(blob), payload_sha256=sha256_bytes(payload),
                archive_sha256=sha256_bytes(blob),
                first_ts=min(firsts) if firsts else "",
                last_ts=max(lasts) if lasts else "",
                sources_bytes=sum(e.bytes for e in entries),
                files=entries,
                note=f"源 {len(files)} 文件；形态={cls.kind}；"
                     f"默认只归档不删除（delete_source="
                     f"{'true' if self.policy.delete_source else 'false'}）",
            )
            manifest.write(manifest_path)
            verified = manifest.verify_archive_bytes() and manifest.verify_payload()
            info.update(status="packed", verified=verified,
                        archive_bytes=len(blob), record_count=manifest.record_count,
                        file_count=manifest.file_count,
                        payload_sha256=manifest.payload_sha256,
                        archive_sha256=manifest.archive_sha256,
                        note="已落盘并自校验通过" if verified else "已落盘但自校验未通过")
            if not verified:
                logger.error("[Archiver] %s@%s 自校验未通过", cls.class_id, period)
            return info
        except Exception as e:  # noqa: BLE001
            logger.error("[Archiver] 打包失败 %s@%s: %s", cls.class_id, period, e,
                         exc_info=True)
            info.update(status="error", error=f"{type(e).__name__}: {e}")
            return info
        finally:
            if tmp_dir:
                shutil.rmtree(tmp_dir, ignore_errors=True)

    # ── 删除（默认关闭）──────────────────────────────────
    def _purge_decision(self, cls: RetentionClass, files: Sequence[str], *,
                        executed: bool) -> Dict[str, Any]:
        """删除判定（**总开关关着时也如实报告护栏结论**，便于验收对账）。"""
        from agent.retention.guard import PurgeGuard

        guard = PurgeGuard(self.policy, root=self.root)
        decision = guard.check(cls.class_id, list(files))
        entry = decision.to_dict()
        entry["delete_source_switch"] = bool(self.policy.delete_source)
        entry["executed"] = bool(executed and self.policy.delete_source
                                 and decision.allowed)
        if not self.policy.delete_source:
            entry["blocked_by"] = "CP_RETENTION_DELETE_SOURCE 未开启（默认只归档不删除）"
        return entry

    def _do_purge(self, cls: RetentionClass, files: Sequence[str],
                  archived: set) -> Tuple[List[str], int]:
        """执行删除：**再判一次护栏**（含"必须先有归档件"），然后逐个 `os.remove`。"""
        from agent.retention.guard import PurgeGuard

        candidates = [p for p in files
                      if os.path.normcase(os.path.abspath(p)) in archived]
        guard = PurgeGuard(self.policy, root=self.root, archived_paths=list(archived))
        decision = guard.check(cls.class_id, candidates)
        if not decision.allowed:
            logger.warning("[Archiver] 删除被护栏拒绝 %s：%s", cls.class_id,
                           decision.summary())
            return [], 0
        deleted: List[str] = []
        total = 0
        for path in candidates:
            size = self._size(path)
            try:
                os.remove(path)
            except OSError as e:
                logger.warning("[Archiver] 删除失败 %s: %s", path, e)
                continue
            deleted.append(path)
            total += size
        return deleted, total

    # ── 统计 / 留痕 ──────────────────────────────────────
    def _totals(self, report: RetentionReport) -> Dict[str, Any]:
        return {
            "classes": len(report.classes),
            "classes_with_data": sum(1 for c in report.classes if c.cold_files),
            "classes_packed": sum(1 for c in report.classes if c.status == "packed"),
            "classes_empty": sum(1 for c in report.classes if c.status == "empty"),
            "classes_error": sum(1 for c in report.classes if c.status == "error"),
            "archives": sum(len(c.archives) for c in report.classes),
            "archived_files": sum(len(c.cold_files) for c in report.classes),
            "archived_records": sum(c.cold_records for c in report.classes),
            "archived_bytes": sum(c.cold_bytes for c in report.classes),
            "packed_bytes": sum(int(a.get("archive_bytes") or 0)
                                for c in report.classes for a in c.archives),
            "warm_lines_moved": sum(int((c.warm.get("result") or {}).get("moved") or 0)
                                    for c in report.classes),
            "deleted_files": sum(len(c.deleted) for c in report.classes),
            "deleted_bytes": sum(c.deleted_bytes for c in report.classes),
            "purge_refused": sum(1 for c in report.classes
                                 if c.cold_files and c.purge
                                 and not c.purge.get("allowed")),
        }

    def _write_audit(self, report: RetentionReport) -> Dict[str, Any]:
        """入链式审计：`action="retention.run"`，记条数与体积（**只放计数，不放内容**）。"""
        if not self.audit:
            return {"status": "disabled"}
        payload = {
            "dry_run": report.dry_run,
            "period": report.period,
            "archive_dir": report.archive_dir,
            "archives": report.totals.get("archives", 0),
            "archived_files": report.totals.get("archived_files", 0),
            "archived_records": report.totals.get("archived_records", 0),
            "archived_bytes": report.totals.get("archived_bytes", 0),
            "packed_bytes": report.totals.get("packed_bytes", 0),
            "warm_lines_moved": report.totals.get("warm_lines_moved", 0),
            "deleted_files": report.totals.get("deleted_files", 0),
            "deleted_bytes": report.totals.get("deleted_bytes", 0),
            "purge_refused": report.totals.get("purge_refused", 0),
            "manifests": [os.path.relpath(m, self.root).replace("\\", "/")
                          if m.startswith(self.root) else m
                          for m in report.manifests],
        }
        try:
            from agent.audit import record as audit_record
            from agent.audit.facade import get_audit

            entry = audit_record(AUDIT_ACTION, actor=ACTOR,
                                 subject=f"retention:{report.period}",
                                 payload=payload, source="agent")
            if entry is None:
                return {"status": "not_recorded", "payload": payload}
            # 【不易】**必须显式 flush**：链的写入是后台批量线程（daemon，
            # 批大小 100 / 轮询 0.5s），而 CLI 一次运行通常几百毫秒就退出 ——
            # 不 flush 会让"每次执行入链式审计"变成"看起来写了、其实没落盘"
            # （S8-01 实测：连续两次执行只留下 1 条，第二次覆盖了同一个 seq）。
            flushed = False
            try:
                chain = get_audit().chain
                if chain is not None:
                    flushed = bool(chain.flush(timeout=5.0))
            except Exception as e:  # noqa: BLE001 flush 失败不改变"已 append"的事实
                logger.warning("[Archiver] 审计链 flush 失败：%s", e)
            return {"status": "recorded", "seq": int(getattr(entry, "seq", 0) or 0),
                    "self_hash": str(getattr(entry, "self_hash", "") or ""),
                    "flushed": flushed, "payload": payload}
        except Exception as e:  # noqa: BLE001 审计失败不阻断（但如实登记）
            logger.warning("[Archiver] 审计写入失败：%s", e)
            return {"status": "error", "error": str(e), "payload": payload}

    def _emit_event(self, report: RetentionReport) -> Dict[str, Any]:
        """发 `retention.run` 事件（best-effort；失败只登记不抛）。"""
        if not self.emit_events:
            return {"status": "disabled"}
        try:
            from agent.observability.events import emit

            envelope = emit(
                EVENT_TYPE,
                {
                    "period": report.period,
                    "archives": report.totals.get("archives", 0),
                    "archived_files": report.totals.get("archived_files", 0),
                    "archived_records": report.totals.get("archived_records", 0),
                    "archived_bytes": report.totals.get("archived_bytes", 0),
                    "deleted_files": report.totals.get("deleted_files", 0),
                    "deleted_bytes": report.totals.get("deleted_bytes", 0),
                },
                actor="auto",
            )
            if envelope is None:
                return {"status": "not_emitted"}
            return {"status": "emitted",
                    "event_id": str(getattr(envelope, "event_id", "") or "")}
        except Exception as e:  # noqa: BLE001
            logger.warning("[Archiver] 事件发射失败：%s", e)
            return {"status": "error", "error": str(e)}


__all__ = [
    "EXT_BY_KIND", "DEFAULT_EXT", "AUDIT_ACTION", "ACTOR", "EVENT_TYPE",
    "ClassOutcome", "RetentionReport", "Archiver",
]
