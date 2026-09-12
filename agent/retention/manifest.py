"""冷归档件的**自描述**清单（schema 版本 / 时间范围 / 记录数 / 校验和）。

【为什么必须自描述】
    归档件的价值取决于「十年后还能证明它是什么、还原得回来」。故每个归档件都配一份
    `<period>.manifest.json`，显式记录：

        schema / schema_version   归档格式版本（非法版本一律拒读，不做"尽力兼容"）
        class_id / period         数据类 + 归属周期
        time_range                归档内容的真实时间范围（首/末 ts，可空）
        record_count              记录数（JSONL = 行数；SQLite = 表行数；树 = 文件数）
        payload_bytes / sha256    解压后载荷的字节数与校验和
        archive_bytes / sha256    压缩件本身的字节数与校验和（**防归档件被改**）
        files[*]                  每个源文件的字节切片（offset/bytes）+ 自身 sha256
        codec                     压缩编解码器（还原端按此解，不靠猜扩展名）

【可还原性的机械保证】
    载荷 = 各源文件**原始字节按 files 顺序拼接**（不重排、不解析、不规范化）。
    还原 = 解压 → 按 `offset/bytes` 切片写回 → 逐文件比对 `sha256`。
    因此"往返一致"是**逐字节**判定，而不是"看起来差不多"。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger("agent.retention.manifest")

#: 归档格式 schema（读端只认这一个；任何其它版本 → `ArchiveFormatError`）
ARCHIVE_SCHEMA = "retention.archive.v1"
ARCHIVE_SCHEMA_VERSION = 1
MANIFEST_SUFFIX = ".manifest.json"

#: 压缩编解码器（用 stdlib：`zstandard` 虽在本机可用但**未进 requirements**，
#: 硬依赖会让 CI 在干净环境上失败；故默认 gzip，解压端按 manifest 的 codec 派发）
CODEC_GZIP = "gzip"
SUPPORTED_CODECS = (CODEC_GZIP,)


class ArchiveFormatError(ValueError):
    """归档件/清单格式非法（版本不符、切片越界、校验和不符）。"""


def utc_now_iso() -> str:
    """UTC ISO-8601 秒级时间戳（归档件时间一律 UTC，避免时区歧义）。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def gzip_compress(data: bytes, *, level: int = 6) -> bytes:
    """确定性 gzip 压缩（`mtime=0`：同输入必得同字节，便于校验和比对）。"""
    import gzip

    return gzip.compress(data, compresslevel=level, mtime=0)


def gzip_decompress(data: bytes) -> bytes:
    import gzip

    return gzip.decompress(data)


def compress(data: bytes, codec: str = CODEC_GZIP) -> bytes:
    if codec == CODEC_GZIP:
        return gzip_compress(data)
    raise ArchiveFormatError(f"不支持的压缩编解码器：{codec}")


def decompress(data: bytes, codec: str = CODEC_GZIP) -> bytes:
    if codec == CODEC_GZIP:
        return gzip_decompress(data)
    raise ArchiveFormatError(f"不支持的压缩编解码器：{codec}")


@dataclass
class ArchivedFile:
    """归档载荷里的一段源文件（原始字节切片）。"""

    path: str                 # 源路径（仓库内为相对路径；仓库外为绝对路径）
    rel_path: str = ""        # 相对仓库根（可用于还原到任意目标根）
    bytes: int = 0            # 该段字节数
    offset: int = 0           # 在解压载荷中的起始偏移
    sha256: str = ""          # 该段字节的 sha256（**载荷切片**，非源文件本身）
    line_count: int = 0       # JSONL 记录数（其余形态为 0）
    row_count: int = 0        # SQLite 表行数（备份副本用）
    row_digest: str = ""      # SQLite 行级摘要（还原后据此逐行比对）
    first_ts: str = ""
    last_ts: str = ""
    mtime: str = ""
    readonly: bool = False
    backup_copy: bool = False  # True = 由一致性备份（SQLite）临时生成，非原文件本身
    note: str = ""

    @property
    def records(self) -> int:
        """本段的"记录数"口径：JSONL 用行数、SQLite 用行数、其余一个文件算一条。"""
        if self.row_count:
            return self.row_count
        if self.line_count:
            return self.line_count
        return 0 if self.backup_copy else 1

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["records"] = self.records
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ArchivedFile":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in (data or {}).items() if k in known})


@dataclass
class ArchiveManifest:
    """一个冷归档件的自描述清单。"""

    schema: str = ARCHIVE_SCHEMA
    schema_version: int = ARCHIVE_SCHEMA_VERSION
    class_id: str = ""
    period: str = ""
    period_kind: str = "day"           # day / month / run
    created_at: str = ""
    codec: str = CODEC_GZIP
    archive_file: str = ""             # 压缩件路径（相对仓库根，或绝对）
    sqlite_table: str = ""             # SQLite 类的表名（行数/行摘要的作用域）
    deleted_sources: List[str] = field(default_factory=list)
    record_count: int = 0
    file_count: int = 0
    payload_bytes: int = 0
    archive_bytes: int = 0
    payload_sha256: str = ""
    archive_sha256: str = ""
    first_ts: str = ""
    last_ts: str = ""
    sources_bytes: int = 0
    files: List[ArchivedFile] = field(default_factory=list)
    tool: str = "agent/retention"
    note: str = ""

    # ── 序列化 ───────────────────────────────────────────
    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["time_range"] = {"first_ts": self.first_ts, "last_ts": self.last_ts}
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, indent=2)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ArchiveManifest":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        payload = {k: v for k, v in (data or {}).items() if k in known}
        files = payload.get("files") or []
        obj = cls(**payload)
        obj.files = [f if isinstance(f, ArchivedFile) else ArchivedFile.from_dict(f)
                     for f in files]
        return obj

    @classmethod
    def read(cls, manifest_path: str) -> "ArchiveManifest":
        """读取并**先做 schema 校验**（非法版本/缺字段一律拒，不做尽力兼容）。"""
        if not os.path.exists(manifest_path):
            raise ArchiveFormatError(f"清单不存在：{manifest_path}")
        try:
            with open(manifest_path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, json.JSONDecodeError, ValueError) as e:
            raise ArchiveFormatError(f"清单不可解析：{manifest_path}（{e}）") from e
        if not isinstance(raw, dict):
            raise ArchiveFormatError(f"清单根不是对象：{manifest_path}")
        if raw.get("schema") != ARCHIVE_SCHEMA:
            raise ArchiveFormatError(
                f"清单 schema 不支持：{raw.get('schema')!r}（仅认 {ARCHIVE_SCHEMA}）")
        if int(raw.get("schema_version") or 0) != ARCHIVE_SCHEMA_VERSION:
            raise ArchiveFormatError(
                f"清单 schema_version 不支持：{raw.get('schema_version')!r}")
        if str(raw.get("codec") or "") not in SUPPORTED_CODECS:
            raise ArchiveFormatError(f"压缩编解码器不支持：{raw.get('codec')!r}")
        manifest = cls.from_dict(raw)
        manifest.validate()
        return manifest

    def validate(self) -> None:
        """结构自检：切片连续、覆盖整个载荷、逐项有校验和。"""
        if not self.class_id:
            raise ArchiveFormatError("清单缺 class_id")
        if not self.archive_file:
            raise ArchiveFormatError("清单缺 archive_file")
        expect = 0
        for item in self.files:
            if item.offset != expect:
                raise ArchiveFormatError(
                    f"切片不连续：{item.path} offset={item.offset} 期望 {expect}")
            if item.bytes < 0:
                raise ArchiveFormatError(f"切片字节数非法：{item.path}")
            if not item.sha256:
                raise ArchiveFormatError(f"切片缺 sha256：{item.path}")
            expect += item.bytes
        if self.payload_bytes and expect != self.payload_bytes:
            raise ArchiveFormatError(
                f"切片总长 {expect} != payload_bytes {self.payload_bytes}")
        if self.file_count and self.file_count != len(self.files):
            raise ArchiveFormatError(
                f"file_count {self.file_count} != files 长度 {len(self.files)}")

    # ── 写盘 / 校验 ───────────────────────────────────────
    def manifest_path(self) -> str:
        return self.archive_file + MANIFEST_SUFFIX

    def write(self, manifest_path: str = "") -> str:
        target = manifest_path or self.manifest_path()
        os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
        tmp = target + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(self.to_json())
        os.replace(tmp, target)
        return target

    def verify_archive_bytes(self) -> bool:
        """校验压缩件本身未被改动（字节级）。"""
        if not os.path.exists(self.archive_file):
            return False
        return sha256_file(self.archive_file) == self.archive_sha256

    def verify_payload(self) -> bool:
        """校验解压载荷与清单登记一致（字节 + 分段）。"""
        try:
            with open(self.archive_file, "rb") as fh:
                blob = decompress(fh.read(), self.codec)
        except (OSError, ArchiveFormatError, EOFError, OSError):
            return False
        if self.payload_bytes and len(blob) != self.payload_bytes:
            return False
        if sha256_bytes(blob) != self.payload_sha256:
            return False
        offset = 0
        for item in self.files:
            segment = blob[offset:offset + item.bytes]
            if sha256_bytes(segment) != item.sha256:
                return False
            offset += item.bytes
        return True

    def summary(self) -> str:
        return (f"{self.class_id}@{self.period}：{self.file_count} 文件 / "
                f"{self.record_count} 记录 / {self.archive_bytes} 字节（压缩）")


def build_payload(files: Sequence[str], *, base_root: str = "",
                  kind: str = "", sqlite_table: str = "",
                  backup_copies: Optional[Dict[str, str]] = None
                  ) -> Tuple[bytes, List[ArchivedFile]]:
    """构造载荷：按 `files` 顺序拼接**原始字节**，返回 `(payload, [ArchivedFile])`。

    Args:
        files: 源文件绝对路径列表（**顺序即载荷顺序**）。
        base_root: 仓库根；提供时额外记录 `rel_path`（便于还原到任意目标根）。
        kind: 数据类形态（`KIND_JSONL` / `KIND_SQLITE` / 其它）。决定"记录数"口径。
        sqlite_table: `kind=KIND_SQLITE` 时统计的表名（空 → 全库）。
        backup_copies: `{源路径: 备份副本路径}`；SQLite 走一致性备份（**不读活动库文件**，
            避免读到写一半的页；载荷存备份副本的字节，`path` 仍记原路径）。
    """
    from agent.retention.scan import (
        KIND_JSONL,
        KIND_SQLITE,
        file_time_range,
        sqlite_row_digest,
        sqlite_row_count,
    )

    copies = backup_copies or {}
    chunks: List[bytes] = []
    entries: List[ArchivedFile] = []
    offset = 0
    for path in files:
        source_path = os.path.abspath(path)
        pack_path = copies.get(source_path, source_path)
        with open(pack_path, "rb") as fh:
            blob = fh.read()
        is_backup = pack_path != source_path
        entry = ArchivedFile(
            path=source_path, bytes=len(blob), offset=offset,
            sha256=sha256_bytes(blob), backup_copy=is_backup,
        )
        if base_root:
            try:
                entry.rel_path = os.path.relpath(source_path, base_root).replace("\\", "/")
            except ValueError:  # pragma: no cover - 跨盘符
                entry.rel_path = ""
        try:
            st = os.stat(source_path)
            entry.mtime = datetime.fromtimestamp(
                st.st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            entry.readonly = not bool(st.st_mode & 0o200)
        except OSError:
            pass
        if kind == KIND_SQLITE:
            entry.row_count = sqlite_row_count(pack_path, sqlite_table)
            entry.row_digest = sqlite_row_digest(pack_path, sqlite_table)
        elif kind == KIND_JSONL:
            entry.first_ts, entry.last_ts, entry.line_count = file_time_range(source_path)
        chunks.append(blob)
        entries.append(entry)
        offset += len(blob)
    return b"".join(chunks), entries


__all__ = [
    "ARCHIVE_SCHEMA", "ARCHIVE_SCHEMA_VERSION", "MANIFEST_SUFFIX",
    "CODEC_GZIP", "SUPPORTED_CODECS", "ArchiveFormatError",
    "utc_now_iso", "sha256_bytes", "sha256_file", "compress", "decompress",
    "gzip_compress", "gzip_decompress",
    "ArchivedFile", "ArchiveManifest", "build_payload",
]
