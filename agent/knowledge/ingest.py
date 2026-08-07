"""素材层 Ingest 管道（任务1 · 低摩擦收集）。

「收集即入库」：外部资料（文章/剪藏/会议转写/随手想法）以**原样只读**方式
落入 knowledge/raw|inbox/，生成 .meta.json 元数据并登记 log.md。

不变式（不易）：
- 层内素材字节原样只读：入库使用复制而非移动，入库前后 sha256 必须一致；
  绝不修改/删除/重命名已入库文件（证据保留）。
- log.md 契约格式：`## [YYYY-MM-DD] <action> | <slug> | <detail>`，
  新记录追加在顶部标记行之后，只追加不改写既有行。
- 敏感检测只标记（meta.sensitive=true），不阻断入库——素材层保留证据。

依赖（简易）：仅标准库 + agent.utils.sensitive_data_filter + sensor.file_watcher。

CLI: python -m agent.knowledge.ingest <path> [--layer inbox|raw] [--source-type TYPE]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import shutil
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

logger = logging.getLogger(__name__)

# 只读素材层（与 knowledge/AGENTS.md 权限约定一致）
LAYERS: tuple[str, ...] = ("inbox", "raw")

META_SUFFIX = ".meta.json"
LOG_MARKER = "<!-- 新记录追加到此行下方（顶部） -->"
LOG_HEADER = (
    "# 操作时间线日志\n"
    "\n"
    "> AI 自动维护（任务0 契约）：每次执行任务后，在**本文件顶部**追加一条记录。\n"
    "> 格式：`## [YYYY-MM-DD] <action> | <slug> | <detail>`\n"
    "\n"
    f"{LOG_MARKER}\n"
)
ENV_ROOT = "KNOWLEDGE_ROOT"
_SAMPLE_LIMIT = 1024 * 1024  # 敏感检测采样上限（1MB，防大文件拖慢入库）

# 进程内互斥：Windows 字节锁按进程判定，同进程多线程需先串行化（见 _FileLock）
_THREAD_LOCK = threading.Lock()


class IngestError(RuntimeError):
    """素材入库异常（含只读性校验失败）。"""


class LockTimeout(RuntimeError):
    """获取 log.md 跨进程文件锁超时。"""


@dataclass
class IngestResult:
    """一次入库操作的结果摘要。"""

    src_path: str
    dest_path: str
    layer: str
    slug: str
    sha256: str
    sensitive: bool
    sensitive_patterns: list = field(default_factory=list)
    source_type: Optional[str] = None
    captured_at: str = ""
    meta_path: str = ""
    log_line: str = ""
    idempotent: bool = False   # True=文件已入库且 meta 已存在，本次零副作用
    log_appended: bool = False
    meta_written: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    def __str__(self) -> str:
        status = "幂等(已存在)" if self.idempotent else "新入库"
        return f"[ingest:{status}] {self.slug} -> {self.dest_path} sha256={self.sha256[:12]}"


# ═══════════════════════════════════════════════════════════════
#  路径与哈希
# ═══════════════════════════════════════════════════════════════

def get_knowledge_root(override: Optional[str] = None) -> Path:
    """解析 knowledge 根目录。

    优先级：显式参数 > 环境变量 KNOWLEDGE_ROOT > 仓库默认（<repo>/knowledge）。
    """
    if override:
        return Path(override).resolve()
    env = os.environ.get(ENV_ROOT)
    if env:
        return Path(env).resolve()
    return Path(__file__).resolve().parents[2] / "knowledge"


def _sha256_file(path: Path) -> str:
    """流式计算文件 sha256（不读入内存）。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_within(child: Path, parent: Path) -> bool:
    """child 是否位于 parent 目录内（resolve 后比较，防 inbox_xxx 前缀误判）。"""
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except (ValueError, OSError):
        return False


def _layer_dir(root: Path, layer: str) -> Path:
    """目标层目录（不存在则创建）。非法层名抛 ValueError。"""
    if layer not in LAYERS:
        raise ValueError(f"非法目标层: {layer!r}（允许: {', '.join(LAYERS)}）")
    d = Path(root) / layer
    d.mkdir(parents=True, exist_ok=True)
    return d


def _slug_of(name: str) -> str:
    """素材标识：入库文件名（不含扩展名），含去重后缀（保证可回溯唯一）。"""
    return Path(name).stem


def _meta_path_for(dest: Path) -> Path:
    """素材对应的元数据路径：<filename>.meta.json（与素材同目录，不隐藏）。"""
    return Path(str(dest) + META_SUFFIX)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_sample(path: Path, limit: int = _SAMPLE_LIMIT) -> str:
    """读取素材前 limit 字节用于敏感检测（二进制容错解码）。"""
    try:
        with open(path, "rb") as f:
            data = f.read(limit)
    except OSError:
        return ""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="ignore")


# ═══════════════════════════════════════════════════════════════
#  敏感信息检测（只标记，不阻断）
# ═══════════════════════════════════════════════════════════════

def detect_sensitive(text: str) -> tuple[bool, list[str]]:
    """检测文本敏感信息，返回 (是否敏感, 命中的模式名列表)。

    复用 SensitiveDataFilter；模块不可用时降级为不标记（不易：不阻断入库）。
    """
    try:
        from agent.utils.sensitive_data_filter import SensitiveDataFilter
    except Exception:
        logger.warning("[ingest] SensitiveDataFilter 不可用，跳过敏感检测")
        return False, []
    try:
        result = SensitiveDataFilter().detect(text)
        if result.violations:
            patterns = sorted({v.pattern_name for v in result.violations})
            return True, patterns
    except Exception:
        logger.warning("[ingest] 敏感检测异常，按不敏感处理", exc_info=True)
    return False, []


# ═══════════════════════════════════════════════════════════════
#  log.md 追加（顶部标记行之后；幂等 + 跨进程文件锁）
# ═══════════════════════════════════════════════════════════════

class _FileLock:
    """log.md 跨进程文件锁。

    - Windows: msvcrt.locking（字节锁）；POSIX: fcntl.flock（整文件锁）。
    - 叠加模块级线程锁：Windows 字节锁按进程判定，同进程多线程必须先串行化。
    - 锁只保护 log.md 的读改写（最小化持锁时长，锁内无外部调用）。
    """

    def __init__(self, path: Path, timeout: float = 10.0):
        self._path = path
        self._timeout = timeout
        self._fh = None

    def __enter__(self) -> "_FileLock":
        _THREAD_LOCK.acquire()
        fh = open(self._path, "a+b")  # 不存在则创建，保证至少有可锁文件
        fh.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                deadline = time.monotonic() + self._timeout
                while True:
                    try:
                        msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise LockTimeout(f"获取文件锁超时: {self._path}")
                        time.sleep(0.05)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        except Exception:
            fh.close()
            _THREAD_LOCK.release()
            raise
        self._fh = fh
        return self

    @property
    def fh(self):
        """被锁定的文件句柄（调用方应仅在此 fd 上做读改写，勿再 open 同路径）。

        Why 单 fd（不易）：Windows CRT 下对已持 msvcrt 字节锁的文件再次 open
        会抛 PermissionError（共享冲突），故读改写必须复用锁持有者的句柄。
        """
        return self._fh

    def __exit__(self, *exc) -> bool:
        try:
            self._fh.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        finally:
            self._fh.close()
            _THREAD_LOCK.release()
        return False


def _line_exists(content: str, line: str) -> bool:
    """log.md 中是否已存在完全一致的记录行（幂等判定）。"""
    target = line.strip()
    return any(l.strip() == target for l in content.splitlines())


# 进程内最近写入 log.md 的记录集合（(log_path, line) 对，容量 _LOG_RECENT_MAX）。
# 【不易】P0-1.1 判重短路：仅覆盖「本进程近期成功写入」的记录——日志契约只追加
# 不改写既有行，命中即代表文件里必然已存在该行，可直接幂等返回 False；
# 跨进程/历史记录/写失败均未登记，仍走文件全量判重，幂等语义不变。
# 键含 log_path：不同知识库/测试目录互不串扰。
_LOG_RECENT: set[tuple[str, str]] = set()
_LOG_RECENT_LOCK = threading.Lock()
_LOG_RECENT_MAX = 100


def _append_log_line(log_path: Path, line: str) -> bool:
    """向 log.md 顶部追加记录行（幂等）。已存在返回 False。

    写回逻辑：读 → 判重 → 组装 → 单 fd 原地写（持锁期间完成，无外部调用；
    Windows CRT 下复用锁定句柄避免共享冲突，见 _FileLock.fh）。
    性能（P0-1.1）：持锁前先查进程内最近写入集合，命中则跳过「锁 + 全量读 +
    逐行判重」，批量重复入库主场景从 O(L) 降为 O(1)。
    """
    key = (str(log_path), line.strip())
    with _LOG_RECENT_LOCK:
        if key in _LOG_RECENT:
            return False  # 本进程刚写入过 → 文件必然已存在（日志只追加）
    with _FileLock(log_path) as lock:
        fh = lock.fh
        fh.seek(0)
        content = fh.read().decode("utf-8")
        if _line_exists(content, line):
            return False
        new_content = _insert_line(content, line)
        fh.seek(0)
        fh.truncate()
        fh.write(new_content.encode("utf-8"))
        fh.flush()
    with _LOG_RECENT_LOCK:
        if len(_LOG_RECENT) >= _LOG_RECENT_MAX:
            _LOG_RECENT.clear()  # 超容清空：最坏清空后首次写多读一次文件，无正确性影响
        _LOG_RECENT.add(key)
    return True


def _insert_line(content: str, line: str) -> str:
    """在 log.md 顶部标记行之后插入记录；无标记文件则顶部直接插入。

    保留文件原有换行风格（LF/CRLF），写回时不引入 BOM。
    """
    if not content.strip():
        return LOG_HEADER + line + "\n"
    nl = "\r\n" if "\r\n" in content else "\n"
    if LOG_MARKER in content:
        pos = content.index(LOG_MARKER)
        end = pos + len(LOG_MARKER)
        return content[:end] + nl + line + nl + content[end:].lstrip("\r\n")
    return line + nl + content


def _log_line(slug: str, source_type: Optional[str]) -> str:
    """契约格式：## [YYYY-MM-DD] ingest | <slug> | <detail>"""
    date_str = datetime.now().strftime("%Y-%m-%d")
    return f"## [{date_str}] ingest | {slug} | {source_type or 'unknown'}"


# ═══════════════════════════════════════════════════════════════
#  元数据读写
# ═══════════════════════════════════════════════════════════════

def _write_meta(meta_path: Path, meta: dict) -> None:
    """原子写 .meta.json（临时文件 + os.replace）。"""
    tmp = Path(str(meta_path) + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    os.replace(tmp, meta_path)


def _load_meta(meta_path: Path) -> Optional[dict]:
    """读取 .meta.json；缺失或损坏返回 None。"""
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _build_meta(src_path: Path, dest_name: str, slug: str, digest: str,
                source_type: Optional[str], layer: str, sensitive: bool,
                patterns: list[str]) -> dict:
    return {
        "source_path": str(src_path.resolve()),
        "source_type": source_type,
        "captured_at": _utcnow(),
        "sha256": digest,
        "sensitive": sensitive,
        "sensitive_patterns": patterns,
        "layer": layer,
        "filename": dest_name,
        "slug": slug,
    }


# ═══════════════════════════════════════════════════════════════
#  核心入库逻辑
# ═══════════════════════════════════════════════════════════════

def _resolve_dest(layer_dir: Path, name: str, digest: str) -> Path:
    """确定入库目标路径。

    - 目标不存在 → 直接使用原名。
    - 已存在且内容一致（同 hash）→ 复用（幂等命中）。
    - 已存在但内容不同 → 追加去重后缀 -2/-3...（不改写既有素材，守不易）。
    """
    dest = layer_dir / name
    if not dest.exists():
        return dest
    if _sha256_file(dest) == digest:
        return dest
    stem, ext = os.path.splitext(name)
    n = 2
    while True:
        cand = layer_dir / f"{stem}-{n}{ext}"
        if not cand.exists():
            logger.info("[ingest] 目标名冲突且内容不同，追加去重后缀 %s → %s", name, cand.name)
            return cand
        if _sha256_file(cand) == digest:
            return cand
        n += 1


def _register(source: Path, dest_path: Path, layer: str, digest: str,
              source_type: Optional[str], root: Path) -> IngestResult:
    """为**已在层内**的素材补全 meta + log（登记）。

    幂等：meta 已存在 → 返回零副作用结果，不重复写 meta/log。

    Args:
        source: 素材的真实来源路径（外部源或已落入层内的文件本身）。
        dest_path: 层内素材路径（登记/元数据绑定对象）。
    """
    slug = _slug_of(dest_path.name)
    meta_path = _meta_path_for(dest_path)
    existing = _load_meta(meta_path)
    if existing is not None:
        logger.info("[ingest] 幂等命中 slug=%s（meta 已存在，零副作用）layer=%s",
                    slug, layer)
        return IngestResult(
            src_path=str(source), dest_path=str(dest_path), layer=layer,
            slug=slug, sha256=existing.get("sha256", digest),
            sensitive=existing.get("sensitive", False),
            sensitive_patterns=existing.get("sensitive_patterns", []),
            source_type=existing.get("source_type", source_type),
            captured_at=existing.get("captured_at", ""),
            meta_path=str(meta_path),
            log_line=existing.get("log_line", ""),
            idempotent=True,
        )

    text = _read_sample(dest_path)
    sensitive, patterns = detect_sensitive(text)
    meta = _build_meta(source, dest_path.name, slug, digest, source_type, layer,
                       sensitive, patterns)
    _write_meta(meta_path, meta)
    line = _log_line(slug, source_type)
    appended = _append_log_line(root / "log.md", line)
    logger.info("[ingest] 已登记 slug=%s → %s sensitive=%s patterns=%s log_appended=%s",
                slug, dest_path, sensitive, patterns, appended)
    return IngestResult(
        src_path=str(source), dest_path=str(dest_path), layer=layer,
        slug=slug, sha256=digest, sensitive=sensitive,
        sensitive_patterns=patterns, source_type=source_type,
        captured_at=meta["captured_at"], meta_path=str(meta_path),
        log_line=line, log_appended=appended, meta_written=True,
    )


def ingest_file(src_path: str, dest_layer: str = "inbox", source_type: Optional[str] = None,
                knowledge_root: Optional[str] = None) -> IngestResult:
    """将素材复制（非移动）入层并登记 meta + log。

    Args:
        src_path: 源素材路径（文件）。
        dest_layer: 目标层，inbox（收集箱）或 raw（原始素材），默认 inbox。
        source_type: 来源类型（article/clip/transcript/thought 等），缺省 unknown。
        knowledge_root: knowledge 根目录，缺省取环境变量 KNOWLEDGE_ROOT 或仓库默认。

    Returns:
        IngestResult。同一文件重复入库时命中幂等，返回零副作用结果。

    Raises:
        FileNotFoundError: 源文件不存在。
        ValueError: 非法目标层，或试图直接入库 .meta.json。
        IngestError: 复制后 hash 不一致（素材只读性校验失败）。
    """
    root = get_knowledge_root(knowledge_root)
    src = Path(src_path)
    if not src.is_file():
        raise FileNotFoundError(f"源文件不存在: {src_path}")
    if src.name.endswith(META_SUFFIX):
        raise ValueError(f"不能直接入库元数据文件: {src.name}")

    layer_dir = _layer_dir(root, dest_layer)
    digest = _sha256_file(src)

    # 素材已在目标层内（监听器/手工直接放入）→ 只登记，不复制（不易：原样只读）
    if _is_within(src, layer_dir):
        logger.info("[ingest] 素材已在层内，只登记不复制 source=%s layer=%s",
                    src, dest_layer)
        return _register(src, src, dest_layer, digest, source_type, root)

    dest = _resolve_dest(layer_dir, src.name, digest)
    if dest.exists():
        # 已入库且内容一致 → 幂等（_register 内部再判定 meta 是否齐备）
        logger.info("[ingest] 目标已存在且内容一致（幂等判定）: %s", dest)
        return _register(src, dest, dest_layer, digest, source_type, root)

    # 复制（非移动），并校验字节不变（【不易】验收线：raw/inbox 内源文件字节不变）
    shutil.copy2(src, dest)
    if _sha256_file(dest) != digest:
        raise IngestError(f"复制后 hash 不一致（素材只读性校验失败）: {dest}")
    logger.info("[ingest] 复制入库 %s → %s（sha256=%s...）hash 校验一致",
                src, dest, digest[:12])
    return _register(src, dest, dest_layer, digest, source_type, root)


# ═══════════════════════════════════════════════════════════════
#  列出待处理素材（供后续提炼管线消费）
# ═══════════════════════════════════════════════════════════════

def list_layer(layer: str, knowledge_root: Optional[str] = None) -> list[dict]:
    """列出指定层的素材（排除 .meta.json 与隐藏文件），附带 meta 摘要。"""
    root = get_knowledge_root(knowledge_root)
    layer_dir = _layer_dir(root, layer)
    entries: list[dict] = []
    if not layer_dir.is_dir():
        return entries
    for p in sorted(layer_dir.iterdir()):
        name = p.name
        if not p.is_file() or name.startswith(".") or name.endswith(META_SUFFIX):
            continue
        meta = _load_meta(_meta_path_for(p)) or {}
        entries.append({
            "filename": name,
            "path": str(p),
            "slug": meta.get("slug") or _slug_of(name),
            "sha256": meta.get("sha256"),
            "sensitive": meta.get("sensitive", False),
            "sensitive_patterns": meta.get("sensitive_patterns", []),
            "source_type": meta.get("source_type"),
            "captured_at": meta.get("captured_at"),
            "has_meta": bool(meta),
        })
    return entries


def list_inbox(knowledge_root: Optional[str] = None) -> list[dict]:
    """列出 inbox 待处理素材与来源。"""
    return list_layer("inbox", knowledge_root)


def list_raw(knowledge_root: Optional[str] = None) -> list[dict]:
    """列出 raw 原始素材与来源。"""
    return list_layer("raw", knowledge_root)


# ═══════════════════════════════════════════════════════════════
#  文件监听（复用 sensor/file_watcher.py，新文件落入 inbox 自动登记 log.md）
# ═══════════════════════════════════════════════════════════════

class KnowledgeWatcher:
    """knowledge/ 素材层监听器：新文件落入被监听层时自动登记 meta + log.md。"""

    def __init__(self, knowledge_root: Optional[str] = None,
                 on_ingest=None, layers: Sequence[str] = ("inbox",)):
        self.root = get_knowledge_root(knowledge_root)
        self.on_ingest = on_ingest
        self.layers = tuple(layers)
        self._watcher = None
        self._layer_dirs = [self.root / l for l in self.layers]

    @property
    def watched_dirs(self) -> list[str]:
        return [str(d) for d in self._layer_dirs]

    @property
    def is_running(self) -> bool:
        return bool(self._watcher and self._watcher.is_running)

    def start(self) -> None:
        """启动监听（惰性导入 sensor.file_watcher，避免模块加载期引入 watchdog）。"""
        if self._watcher is not None:
            return
        from sensor.file_watcher import FileWatcher  # noqa: PLC0415

        existing = []
        for d in self._layer_dirs:
            d.mkdir(parents=True, exist_ok=True)
            existing.append(d)
        self._watcher = FileWatcher(
            existing, callback=self._on_event,
            exclude=["*.meta.json", "*.lock", "*.tmp"],
            debounce_sec=0.5,
        )
        self._watcher.start()

    def stop(self) -> None:
        if self._watcher is not None:
            self._watcher.stop()

    def _on_event(self, reading) -> None:
        """watchdog 回调：仅响应文件创建事件，忽略自身产物（meta/tmp/隐藏）。"""
        meta = reading.metadata or {}
        if meta.get("event_type") != "created" or meta.get("is_directory"):
            return
        self.handle_path(meta.get("src_path") or reading.value)

    def handle_path(self, path: str) -> Optional[IngestResult]:
        """登记单个路径（供回调与测试直接调用）。

        文件已在层内 → 走登记分支（补 meta + log），幂等安全。
        忽略：meta.json/临时/隐藏文件。
        """
        if not path or not os.path.isfile(path):
            return None
        name = os.path.basename(path)
        if name.startswith(".") or name.endswith((META_SUFFIX, ".lock", ".tmp")):
            return None
        layer = self._layer_of(path)
        if layer is None:
            return None
        result = ingest_file(path, dest_layer=layer, source_type=None,
                             knowledge_root=str(self.root))
        if self.on_ingest and callable(self.on_ingest):
            self.on_ingest(result)
        return result

    def _layer_of(self, path: str) -> Optional[str]:
        p = Path(path)
        for layer, d in zip(self.layers, self._layer_dirs):
            if _is_within(p, d):
                return layer
        return None


# ═══════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════

def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m agent.knowledge.ingest",
        description="素材层 Ingest 管道：复制入库 + meta + log.md 登记",
    )
    parser.add_argument("paths", nargs="*", help="要入库的素材文件路径（可多个）")
    parser.add_argument("--layer", default="inbox", choices=LAYERS,
                        help="目标层（默认 inbox）")
    parser.add_argument("--source-type", default=None,
                        help="来源类型（article/clip/transcript/thought 等）")
    parser.add_argument("--root", default=None,
                        help="knowledge 根目录（默认取环境变量 KNOWLEDGE_ROOT 或仓库默认）")
    parser.add_argument("--list", action="store_true",
                        help="列出 inbox/raw 待处理素材")
    parser.add_argument("--watch", action="store_true",
                        help="监听层目录新文件并自动登记（Ctrl+C 退出）")
    args = parser.parse_args(argv)

    try:
        if args.watch:
            watcher = KnowledgeWatcher(args.root)
            watcher.start()
            print(f"[ingest] 监听中: {watcher.watched_dirs}（Ctrl+C 退出）")
            try:
                threading.Event().wait()
            except KeyboardInterrupt:
                pass
            finally:
                watcher.stop()
            return 0

        if args.list:
            for layer in LAYERS:
                print(f"== {layer} ==")
                for e in list_layer(layer, args.root):
                    flag = "⚠敏感" if e["sensitive"] else "    "
                    print(f"  {flag} {e['filename']}  <- {e['source_type'] or 'unknown'}")
            return 0

        if not args.paths:
            parser.error("至少需要一个素材路径（或使用 --list / --watch）")

        rc = 0
        for p in args.paths:
            try:
                result = ingest_file(p, dest_layer=args.layer,
                                     source_type=args.source_type,
                                     knowledge_root=args.root)
                print(json.dumps(result.to_dict(), ensure_ascii=False))
                if result.sensitive:
                    print(f"[ingest] ⚠ 敏感内容已标记: {result.slug} "
                          f"（patterns={result.sensitive_patterns}，未阻断）",
                          file=sys.stderr)
            except Exception as e:
                print(f"[ingest] 失败 {p}: {e}", file=sys.stderr)
                rc = 1
        return rc
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
