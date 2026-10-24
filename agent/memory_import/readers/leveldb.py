"""只读 LevelDB 读取器（纯标准库，零第三方依赖）。

【为什么自己写】Trae / Electron 的 IndexedDB 与 Local Storage 都是 LevelDB
目录（.log 预写日志 + .ldb SST 表）。本机没有 plyvel / python-leveldb，
而"读别人的数据库"这件事**必须只读且不能被运行时锁住**：
- 不打开目录锁、不建 LOCK 文件，只按字节解析 CURRENT / MANIFEST / .log / .ldb；
- 解析失败只降级（返回已读到的部分 + warnings），绝不抛异常打断导入盘点。

覆盖的格式（LevelDB 1.x 公开格式）：
- 写前日志 .log：32 KiB 分块，7 字节记录头（crc32c + 长度 + 类型），
  FULL/FIRST/MIDDLE/LAST 四种分片；记录体是 WriteBatch（seq,u32 count,
  之后 put/delete 记录）；
- SST 表 .ldb：Footer(48 字节) -> index block -> data block；
  block 内是前缀压缩条目 + restart 数组，尾部 5 字节为压缩类型 + crc；
  压缩类型 0=不压缩、1=Snappy（内置纯 Python 解码器）。

【不易】.log 与 .ldb 的条目语义不同：.log 里可能先 put 后 delete，
必须按 seq 顺序回放；本读取器按文件内出现顺序合并，同一 key 后者覆盖前者。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

BLOCK_SIZE = 32768
_HEADER_SIZE = 7
_FOOTER_SIZE = 48
_TABLE_MAGIC = 0xDB4775248B80FB57

_TYPE_DELETE = 0
_TYPE_VALUE = 1
_LOG_FULL = 1
_LOG_FIRST = 2
_LOG_MIDDLE = 3
_LOG_LAST = 4

#: Snappy 标签低两位：0=字面量、1=1 字节偏移复制、2=2 字节、3=4 字节
_SNAPPY_LITERAL = 0
_SNAPPY_COPY_1 = 1
_SNAPPY_COPY_2 = 2
_SNAPPY_COPY_4 = 3


# ════════════════════════════════════════════════════════════
#  基础解码
# ════════════════════════════════════════════════════════════


def read_varint(buf: bytes, pos: int) -> Tuple[int, int]:
    """LevelDB 的 LEB128 变长整数（32/64 位共用同一编码）。"""
    result = 0
    shift = 0
    while True:
        if pos >= len(buf):
            raise ValueError("varint 越界")
        byte = buf[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, pos
        shift += 7
        if shift > 63:
            raise ValueError("varint 过长")


def snappy_decompress(data: bytes) -> bytes:
    """Snappy 原始格式解码（SST 数据块可能用它压缩）。

    仅实现解压（导入器只读）；遇到未知标签按 Snappy 规范视为损坏，
    抛 ValueError 由上层降级处理。
    """
    length, pos = read_varint(data, 0)
    out = bytearray()
    n = len(data)
    while pos < n:
        tag = data[pos]
        pos += 1
        kind = tag & 0x03
        if kind == _SNAPPY_LITERAL:
            literal = tag >> 2
            if literal >= 60:
                extra = literal - 59  # 60->1 字节, 61->2, 62->3, 63->4
                size = int.from_bytes(data[pos:pos + extra], "little")
                pos += extra
            else:
                size = literal
            size += 1
            if pos + size > n:
                raise ValueError("snappy 字面量越界")
            out.extend(data[pos:pos + size])
            pos += size
            continue
        if kind == _SNAPPY_COPY_1:
            size = 4 + ((tag >> 2) & 0x07)
            offset = ((tag >> 5) << 8) | data[pos]
            pos += 1
        elif kind == _SNAPPY_COPY_2:
            size = 1 + (tag >> 2)
            offset = int.from_bytes(data[pos:pos + 2], "little")
            pos += 2
        else:
            size = 1 + (tag >> 2)
            offset = int.from_bytes(data[pos:pos + 4], "little")
            pos += 4
        if offset <= 0 or offset > len(out):
            raise ValueError("snappy 偏移越界")
        start = len(out) - offset
        for i in range(size):  # 允许重叠复制（run-length 语义）
            out.append(out[start + i])
    return bytes(out[:length])


# ════════════════════════════════════════════════════════════
#  写前日志（.log）
# ════════════════════════════════════════════════════════════


def read_log_records(path: Path) -> List[bytes]:
    """读 .log 的全部完整记录（坏块/尾部半条只跳过，不抛错）。"""
    data = Path(path).read_bytes()
    records: List[bytes] = []
    buf = b""
    pos = 0
    total = len(data)
    while pos < total:
        offset = pos % BLOCK_SIZE
        if BLOCK_SIZE - offset < _HEADER_SIZE:
            pos += BLOCK_SIZE - offset
            continue
        if pos + _HEADER_SIZE > total:
            break
        _crc, length, kind = struct.unpack_from("<IHB", data, pos)
        pos += _HEADER_SIZE
        if length == 0 and kind == 0:  # 预分配区的空洞（合法）
            continue
        fragment = data[pos:pos + length]
        pos += length
        if kind == _LOG_FULL:
            records.append(fragment)
            buf = b""
        elif kind == _LOG_FIRST:
            buf = fragment
        elif kind == _LOG_MIDDLE:
            buf += fragment
        elif kind == _LOG_LAST:
            records.append(buf + fragment)
            buf = b""
    return records


def parse_write_batch(record: bytes) -> List[Tuple[int, bytes, bytes]]:
    """WriteBatch -> [(tag, key, value)]；tag=1 put / 0 delete。"""
    if len(record) < 12:
        return []
    count = struct.unpack_from("<I", record, 8)[0]
    pos = 12
    out: List[Tuple[int, bytes, bytes]] = []
    for _ in range(count):
        if pos >= len(record):
            break
        tag = record[pos]
        pos += 1
        key_len, pos = read_varint(record, pos)
        key = record[pos:pos + key_len]
        pos += key_len
        value = b""
        if tag == _TYPE_VALUE:
            value_len, pos = read_varint(record, pos)
            value = record[pos:pos + value_len]
            pos += value_len
        out.append((tag, key, value))
    return out


@dataclass
class LevelEntry:
    """一条键值（来自 .log 或 .ldb）。"""

    key: bytes
    value: bytes
    source: str = ""

    def key_text(self, encoding: str = "utf-8", errors: str = "replace") -> str:
        return self.key.decode(encoding, errors)

    def value_text(self, encoding: str = "utf-8", errors: str = "replace") -> str:
        return self.value.decode(encoding, errors)


# ════════════════════════════════════════════════════════════
#  SST 表（.ldb）
# ════════════════════════════════════════════════════════════


def _read_block(data: bytes, offset: int, size: int) -> bytes:
    block = data[offset:offset + size]
    if len(block) < size:
        raise ValueError("block 越界")
    trailer = data[offset + size:offset + size + 5]
    if len(trailer) < 5:
        raise ValueError("block trailer 缺失")
    compression = trailer[0]
    if compression == 0:
        return block
    if compression == 1:
        return snappy_decompress(block)
    raise ValueError(f"未知压缩类型: {compression}")


def _parse_block_entries(block: bytes) -> List[Tuple[bytes, bytes]]:
    """block -> [(key, value)]（前缀压缩 + restart 数组）。"""
    if len(block) < 4:
        return []
    num_restarts = struct.unpack_from("<I", block, len(block) - 4)[0]
    restarts_end = len(block) - 4 - 4 * num_restarts
    if restarts_end < 0:
        raise ValueError("restart 数组越界")
    # 【不易】数据区可能带型别前缀（新旧版本差异）；这里以 restart 数组
    # 的起点为界，从 0 开始按条目自描述长度顺序解析。
    pos = 0
    key = b""
    out: List[Tuple[bytes, bytes]] = []
    while pos < restarts_end:
        shared, pos = read_varint(block, pos)
        non_shared, pos = read_varint(block, pos)
        value_len, pos = read_varint(block, pos)
        if pos + non_shared + value_len > len(block):
            raise ValueError("条目越界")
        key = key[:shared] + block[pos:pos + non_shared]
        pos += non_shared
        value = block[pos:pos + value_len]
        pos += value_len
        out.append((key, value))
    return out


def read_sst_entries(path: Path) -> List[Tuple[bytes, bytes]]:
    """读一个 .ldb 的全部键值（索引块 -> 数据块）。"""
    data = Path(path).read_bytes()
    if len(data) < _FOOTER_SIZE:
        return []
    footer = data[-_FOOTER_SIZE:]
    magic = struct.unpack_from("<Q", footer, _FOOTER_SIZE - 8)[0]
    if magic != _TABLE_MAGIC:
        raise ValueError("不是 LevelDB 表文件（magic 不符）")
    meta_off, pos = read_varint(footer, 0)
    meta_size, pos = read_varint(footer, pos)
    index_off, pos = read_varint(footer, pos)
    index_size, pos = read_varint(footer, pos)
    del meta_off, meta_size
    index_block = _read_block(data, index_off, index_size)
    entries: List[Tuple[bytes, bytes]] = []
    for _key, handle in _parse_block_entries(index_block):
        block_off, p = read_varint(handle, 0)
        block_size, p = read_varint(handle, p)
        entries.extend(_parse_block_entries(_read_block(data, block_off, block_size)))
    return entries


# ════════════════════════════════════════════════════════════
#  目录级读取
# ════════════════════════════════════════════════════════════


@dataclass
class LevelDbScan:
    """一次目录级扫描的结果（含降级证据）。"""

    root: str
    entries: List[LevelEntry] = field(default_factory=list)
    files: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    log_entries: int = 0
    sst_entries: int = 0

    @property
    def count(self) -> int:
        return len(self.entries)

    def to_dict(self, *, sample: int = 0) -> Dict[str, Any]:
        entries = self.entries if sample <= 0 else self.entries[:sample]
        return {
            "root": self.root,
            "files": list(self.files),
            "entries": len(self.entries),
            "log_entries": self.log_entries,
            "sst_entries": self.sst_entries,
            "warnings": list(self.warnings),
            "sample": [{"key": e.key_text("utf-16-le"), "chars": len(e.value)}
                       for e in entries],
        }


def scan_leveldb(root: Path, *, max_entries: int = 50000) -> LevelDbScan:
    """只读扫描一个 LevelDB 目录（.log 先、.ldb 后，后者覆盖同名键）。

    【不易】不读 LOCK / MANIFEST 做恢复判断：导入盘点只关心"能读出多少条"，
    残缺目录也应产出已读部分 + warnings，而不是直接报错。
    """
    root = Path(root)
    scan = LevelDbScan(root=str(root))
    if not root.is_dir():
        scan.warnings.append(f"目录不存在: {root}")
        return scan
    logs = sorted(root.glob("*.log"))
    tables = sorted(root.glob("*.ldb"))
    scan.files = [p.name for p in (logs + tables)]
    latest: Dict[bytes, LevelEntry] = {}
    for path in logs:
        try:
            for record in read_log_records(path):
                for tag, key, value in parse_write_batch(record):
                    if tag == _TYPE_DELETE:
                        latest.pop(key, None)
                        continue
                    latest[key] = LevelEntry(key, value, path.name)
                    scan.log_entries += 1
                    if len(latest) >= max_entries:
                        scan.warnings.append(f"达到条数上限 {max_entries}，已截断")
                        break
        except OSError as exc:
            scan.warnings.append(f"读取失败（{path.name}）：{exc}")
    for path in tables:
        try:
            for key, value in read_sst_entries(path):
                latest[key] = LevelEntry(key, value, path.name)
                scan.sst_entries += 1
                if len(latest) >= max_entries:
                    scan.warnings.append(f"达到条数上限 {max_entries}，已截断")
                    break
        except (OSError, ValueError) as exc:
            scan.warnings.append(f"表解析失败（{path.name}）：{exc}")
    scan.entries = list(latest.values())
    return scan


def iter_json_values(scan: LevelDbScan, *, min_chars: int = 40) -> Iterator[Tuple[str, str]]:
    """从扫描结果里抽 JSON 形态的文本值（IDB 的 V8 序列化里夹着明文）。

    【不易】IndexedDB 的值是 V8 序列化字节，不是纯 JSON；这里不做完整
    V8 反序列化（版本多、易碎），只做"可读文本 + 平衡括号"的保守提取，
    宁可漏也不把二进制垃圾当正文。
    """
    for entry in scan.entries:
        text = best_effort_text(entry.value)
        if len(text) < min_chars:
            continue
        for candidate in json_candidates(text):
            yield entry.key_text("utf-16-le", "replace"), candidate


def best_effort_text(blob: bytes) -> str:
    """把二进制块还原成可读文本（UTF-8 与 UTF-16LE 取更"像文本"的一版）。"""
    if not blob:
        return ""
    utf8 = blob.decode("utf-8", "replace")
    if blob[:2] in (b"\xff\xfe", b"\xfe\xff") or _looks_like_utf16(blob):
        try:
            utf16 = blob.decode("utf-16-le", "replace")
        except (UnicodeDecodeError, ValueError):
            utf16 = ""
        if _printable_ratio(utf16) > _printable_ratio(utf8):
            return utf16
    return utf8


def _looks_like_utf16(blob: bytes) -> bool:
    sample = blob[:400]
    if len(sample) < 4:
        return False
    zeros = sample[1::2].count(0)
    return zeros >= max(2, len(sample) // 4)


def _printable_ratio(text: str) -> float:
    if not text:
        return 0.0
    good = sum(1 for ch in text if ch.isprintable() or ch in "\r\n\t")
    return good / len(text)


def json_candidates(text: str, *, limit: int = 20) -> List[str]:
    """从一段文本里找平衡的 JSON 对象/数组（含中文与转义的最小实现）。"""
    out: List[str] = []
    length = len(text)
    i = 0
    while i < length and len(out) < limit:
        if text[i] in "{[":
            parsed, end = _scan_balanced(text, i)
            if parsed and end > i:
                out.append(parsed)
                i = end
                continue
        i += 1
    return out


def _scan_balanced(text: str, start: int) -> Tuple[str, int]:
    """从 start 处的 { 或 [ 起找配对；字符串内的括号不计。"""
    opening = text[start]
    closing = "}" if opening == "{" else "]"
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == opening:
            depth += 1
        elif ch == closing:
            depth -= 1
            if depth == 0:
                return text[start:i + 1], i + 1
    return "", -1


__all__ = [
    "LevelDbScan", "LevelEntry", "best_effort_text", "iter_json_values",
    "json_candidates", "parse_write_batch", "read_log_records",
    "read_sst_entries", "read_varint", "scan_leveldb", "snappy_decompress",
]
