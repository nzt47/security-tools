"""还原器（TASK-S8-01 步骤 2「归档必须可还原」）。

【还原为什么是硬要求】
    "归档"如果没有还原路径，就只是"删除的另一种说法"。故每个冷归档件都必须能被
    还原，并且**可判定是否还原正确**：以 `manifest` 登记的逐文件 `sha256`
    （SQLite 备份副本用**行级摘要**）为准做机械比对。

【默认不改线上】
    `restore(archive)` 缺省还原到**临时目录**并返回该目录；要覆盖线上位置必须显式
    `allow_overwrite_live=True`。抽样验证（`sample_roundtrip`）永远用临时目录。
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from agent.retention.manifest import (
    MANIFEST_SUFFIX,
    ArchiveFormatError,
    ArchiveManifest,
    decompress,
    sha256_bytes,
)
from agent.retention.policy import PROJECT_ROOT, RetentionPolicy, load_policy
from agent.retention.scan import sqlite_row_digest

logger = logging.getLogger("agent.retention.restorer")


class RestoreRefusedError(RuntimeError):
    """还原被拒（目标会覆盖线上文件，或归档件校验不通过）。"""


@dataclass
class RestoredFile:
    """单个文件的还原结果与一致性判定。"""

    path: str = ""            # 归档登记的源路径
    rel_path: str = ""
    restored_to: str = ""
    verdict: str = "unknown"  # byte_exact / row_digest / mismatch / missing
    expected: str = ""
    actual: str = ""
    bytes: int = 0
    line_count: int = 0
    row_count: int = 0

    @property
    def match(self) -> bool:
        return self.verdict in ("byte_exact", "row_digest")

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["match"] = self.match
        return d


@dataclass
class RestoreReport:
    """一次还原的完整结果。"""

    archive_file: str = ""
    manifest_file: str = ""
    class_id: str = ""
    period: str = ""
    target_dir: str = ""
    temporary: bool = False
    allow_overwrite_live: bool = False
    archive_verified: bool = False
    payload_verified: bool = False
    files: List[RestoredFile] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return (self.archive_verified and self.payload_verified
                and bool(self.files) and all(f.match for f in self.files))

    @property
    def matched(self) -> int:
        return sum(1 for f in self.files if f.match)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["files"] = [f if isinstance(f, dict) else f.to_dict() for f in self.files]
        d["ok"] = self.ok
        d["matched"] = self.matched
        return d

    def summary(self) -> str:
        return (f"[还原] {self.class_id}@{self.period} → {self.target_dir}；"
                f"文件 {len(self.files)} 个，一致 {self.matched} 个；"
                f"归档件校验={self.archive_verified} 载荷校验={self.payload_verified}；"
                f"判定={'通过' if self.ok else '不通过'}")


class Restorer:
    """归档件还原 + 往返一致性抽样验证。"""

    def __init__(self, policy: Optional[RetentionPolicy] = None, *,
                 root: str = "", archive_dir: str = "") -> None:
        self.policy = policy or load_policy()
        self.root = os.path.abspath(root or PROJECT_ROOT)
        self.archive_dir = os.path.abspath(archive_dir or self.policy.archive_dir)

    # ── 定位 ─────────────────────────────────────────────
    def resolve(self, archive: str) -> Tuple[str, ArchiveManifest]:
        """接受压缩件路径或清单路径，返回 `(清单路径, manifest)`。"""
        path = os.path.abspath(archive)
        candidate = path + MANIFEST_SUFFIX if not path.endswith(MANIFEST_SUFFIX) else path
        if not os.path.exists(candidate):
            if os.path.exists(path):
                raise ArchiveFormatError(
                    f"归档件存在但缺清单（自描述缺失，拒绝还原）：{path}")
            raise ArchiveFormatError(f"归档件/清单不存在：{archive}")
        return candidate, ArchiveManifest.read(candidate)

    def list_archives(self, class_id: str = "") -> List[Dict[str, Any]]:
        """列出归档目录下的全部归档件（读清单；坏清单标注 `error` 而非静默跳过）。"""
        base = os.path.join(self.archive_dir, class_id) if class_id else self.archive_dir
        out: List[Dict[str, Any]] = []
        if not os.path.isdir(base):
            return out
        for dirpath, _dirs, names in os.walk(base):
            for name in sorted(names):
                if not name.endswith(MANIFEST_SUFFIX):
                    continue
                full = os.path.join(dirpath, name)
                try:
                    m = ArchiveManifest.read(full)
                    out.append({
                        "manifest_file": full, "archive_file": m.archive_file,
                        "class_id": m.class_id, "period": m.period,
                        "period_kind": m.period_kind, "created_at": m.created_at,
                        "file_count": m.file_count, "record_count": m.record_count,
                        "payload_bytes": m.payload_bytes,
                        "archive_bytes": m.archive_bytes,
                        "archive_sha256": m.archive_sha256,
                        "first_ts": m.first_ts, "last_ts": m.last_ts,
                        "verified": m.verify_archive_bytes(),
                    })
                except Exception as e:  # noqa: BLE001 坏清单如实登记
                    out.append({"manifest_file": full, "error": str(e)})
        return out

    # ── 还原 ─────────────────────────────────────────────
    def restore(self, archive: str, target: str = "", *,
                allow_overwrite_live: bool = False,
                only: Optional[Sequence[str]] = None) -> RestoreReport:
        """把归档件还原到 `target`。

        Args:
            archive: 压缩件或清单路径。
            target: 目标目录；空 → 新建临时目录（**默认不改线上**）。
            allow_overwrite_live: 是否允许覆盖仍然存在的源文件。
            only: 只还原这些源路径（相对/绝对均可）；None = 全部。
        """
        manifest_path, manifest = self.resolve(archive)
        report = RestoreReport(
            archive_file=manifest.archive_file, manifest_file=manifest_path,
            class_id=manifest.class_id, period=manifest.period,
            allow_overwrite_live=allow_overwrite_live,
        )
        report.archive_verified = manifest.verify_archive_bytes()
        report.payload_verified = manifest.verify_payload()
        if not report.payload_verified:
            raise RestoreRefusedError(
                f"归档件载荷校验不通过（拒绝还原）：{manifest.archive_file}")

        if not target:
            target = tempfile.mkdtemp(prefix="cp_restore_")
            report.temporary = True
        os.makedirs(target, exist_ok=True)
        report.target_dir = os.path.abspath(target)

        wanted = {os.path.normcase(os.path.abspath(p)) for p in (only or [])}
        rel_wanted = {str(p).replace("\\", "/") for p in (only or [])}

        with open(manifest.archive_file, "rb") as fh:
            blob = decompress(fh.read(), manifest.codec)

        offset = 0
        for item in manifest.files:
            segment = blob[offset:offset + item.bytes]
            offset += item.bytes
            if wanted and os.path.normcase(os.path.abspath(item.path)) not in wanted \
                    and item.rel_path not in rel_wanted:
                continue
            rel = item.rel_path or os.path.basename(item.path)
            dest = os.path.join(report.target_dir, rel)
            os.makedirs(os.path.dirname(dest) or report.target_dir, exist_ok=True)
            if os.path.exists(item.path) and not allow_overwrite_live \
                    and os.path.normcase(os.path.abspath(dest)) == \
                    os.path.normcase(os.path.abspath(item.path)):
                raise RestoreRefusedError(
                    f"目标会覆盖仍然存在的线上文件：{item.path}"
                    f"（如确需原地还原请显式 allow_overwrite_live=True）")
            with open(dest, "wb") as fh:
                fh.write(segment)
            record = RestoredFile(
                path=item.path, rel_path=rel, restored_to=dest,
                expected=item.row_digest or item.sha256, bytes=len(segment),
                line_count=item.line_count, row_count=item.row_count,
            )
            if item.backup_copy:
                # **必须带上清单登记的表名**：多表库（如 tool_trace.db 同时有
                # `tool_traces` 与 `unified_traces`）用"全库摘要"会比出假不一致
                # —— S8-01 实测踩过：单表库通过、双表库误报 mismatch。
                actual = sqlite_row_digest(dest, manifest.sqlite_table)
                record.verdict = "row_digest" if actual and actual == item.row_digest \
                    else "mismatch"
                record.actual = actual
            else:
                actual = sha256_bytes(segment)
                record.verdict = "byte_exact" if actual == item.sha256 else "mismatch"
                record.actual = actual
            report.files.append(record)

        if not report.files:
            report.notes.append("无文件被还原（only 过滤后为空）")
        return report

    # ── 抽样往返一致性 ───────────────────────────────────
    def sample_roundtrip(self, archive: str, *, sample: int = 0,
                         target: str = "") -> RestoreReport:
        """还原到临时目录并逐文件判定一致性（验收要求"抽样往返一致"）。

        Args:
            archive: 压缩件或清单路径。
            sample: 只验证前 N 个文件（0 = 全部）。
            target: 目标目录（空 → 临时目录并自动清理由调用方决定）。
        """
        _mp, manifest = self.resolve(archive)
        only = None
        if sample and sample > 0:
            only = [f.path for f in manifest.files[:int(sample)]]
        report = self.restore(archive, target, only=only)
        if report.ok:
            logger.info("[Restorer] %s", report.summary())
        else:
            logger.warning("[Restorer] %s", report.summary())
        return report

    def verify_only(self, archive: str) -> Dict[str, Any]:
        """只校验归档件（不还原）：字节校验和 + 载荷校验和 + 分段切片。"""
        manifest_path, manifest = self.resolve(archive)
        return {
            "manifest_file": manifest_path,
            "archive_file": manifest.archive_file,
            "class_id": manifest.class_id,
            "period": manifest.period,
            "file_count": manifest.file_count,
            "record_count": manifest.record_count,
            "archive_bytes": manifest.archive_bytes,
            "archive_verified": manifest.verify_archive_bytes(),
            "payload_verified": manifest.verify_payload(),
            "sources": [f.path for f in manifest.files],
        }


def cleanup_restore_dir(report: RestoreReport) -> bool:
    """清理临时还原目录（仅当 `report.temporary` 为真；绝不删用户指定的目录）。"""
    if not report.temporary or not report.target_dir:
        return False
    shutil.rmtree(report.target_dir, ignore_errors=True)
    return True


__all__ = [
    "RestoreRefusedError", "RestoredFile", "RestoreReport", "Restorer",
    "cleanup_restore_dir",
]
