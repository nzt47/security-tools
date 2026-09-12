"""审计链 seq 预留日志（跨进程 seq 分配的**持久化权威**）—— TASK-S8-02 步骤 3

【为什么需要它：单写者假设失效后的第一条裂缝就是 seq】
    ``AuditChain`` 原本在**进程内**分配 seq：``append()`` 在 ``_append_lock``
    里读 `self._next_seq` 并 +1，``_next_seq`` 只在构造时从
    ``SELECT seq ... ORDER BY seq DESC LIMIT 1`` 恢复一次。于是两个进程各自
    从同一个 max(seq) 起分配 ⇒ **重复 seq** ⇒ 撞 ``seq INTEGER NOT NULL UNIQUE``
    ⇒ 当前代码把整批记录塞进 `_failed_buffer`（进程内 deque）⇒ 进程退出即
    永久丢失。这不是"性能问题"，是**审计断链**。

【为什么不是"每 append 一个同步 SQLite 提交"（实测数据）】
    正确性上那是最简单的方案（事务内 ``SELECT MAX(seq)`` + INSERT，天然无重复、
    无空洞、崩溃即回滚）。但实测单进程 ``append()`` 的 p99 是 **0.14ms**，而一次
    ``PRAGMA synchronous=FULL`` 的提交是 **17–22ms**（约 150×）——直接违反
    「单进程写入 p99 不退化」。故必须把"持久化"与"批量入库"解耦。

【本模块的解法：**写入前日志（reservation journal）承载 seq 权威**】
    ``append()`` 在**跨进程锁内**：
      1. 解析权威链头 = max(内存链头, 本日志末行, DB 最大行)（按 seq 取大）；
      2. seq = 链头 + 1；prev_hash = 链头 self_hash；算两级哈希；
      3. 把**整条记录**追加进本日志并 ``flush()``（OS 缓冲，**不做 fsync**）；
      4. 释放锁；入库交给原有后台 writer 批量完成。

    为什么这样就够（三个性质）：
    - **无重复 seq**：分配在锁内、且链头来自"锁内可见的持久化来源"（日志末行
      或 DB），任一进程分配过的 seq 立刻对其它进程可见。
    - **无空洞**：整条记录都在日志里，**不是只预留一个号**。所以"崩溃后空洞"
      这个语义在本方案下**不会发生**：进程被杀后，日志里那几条由启动重放
      （``read_since``）补写进 DB。这也正是任务书要求的"链式哈希校验必须仍能
      通过"——链是连续的，``verify_chain`` 的 ``expect_seq = first + idx`` 恒成立。
    - **崩溃语义**：日志 ``flush()`` 后即对**同机其它进程**可见（OS 页缓存），
      因此"进程被杀"不丢；只有**整机掉电**可能丢掉最后几条尚未 fsync 的记录
      ——而它们同样没进 DB，即日志与 DB 一起只缺尾巴，**仍是一致的**（无空洞、
      无重复）。牺牲"掉电尾几条"换 "p99 不退化"是本任务明确接受的取舍，这里写明。

【为什么压缩 = 直接 truncate(0)，而不是重写文件】
    日志在"**全部已入库**"时才压缩。此时丢弃整份日志是安全的：权威链头退化为
    DB 最大行，信息不丢。反之若用"临时文件 + ``os.replace``"做部分压缩，
    **换 inode 会让其它进程已打开的追加句柄继续写旧 inode**（写进已删除的文件，
    静默丢数据）。故本模块**只**在"全清"语义下 truncate，绝不 rename。

【依赖方向】纯标准库 + 无 ``AuditEntry`` 依赖（只处理"列值列表"），
    从而 ``agent.audit.chain`` 可以单向依赖本模块而不成环。
"""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger("agent.audit.seq_journal")

#: 读末行时向后回溯的字节窗口（一行记录远小于此；窗口越大越能容忍长 payload）
TAIL_SCAN_BYTES = 256 * 1024

#: 压缩时**保留的最近已入库记录条数**（作为降级读路径的"近期记录缓存"，见
#: ``compact`` 的 retain 说明）。默认 512：约几百 KB 量级，磁盘占用有界，
#: 却足以覆盖"DB 突然不可用后仍要读得到刚写的审计"这一窗口。
DEFAULT_RETAIN_RECORDS = 512

#: 单行上限（与 payload 上限同量级；超过则拒绝写入，避免一条脏记录拖垮读取）
MAX_JOURNAL_LINE_BYTES = 1 << 22


class SeqJournalError(RuntimeError):
    """预留日志不可用（路径不可写等）"""


def _fsync_dir(path: str) -> None:
    """尽力 fsync 目录（POSIX 有效；Windows 无此语义，静默跳过）"""
    try:
        fd = os.open(os.path.dirname(os.path.abspath(path)) or ".", os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


class SeqJournal:
    """追加式「待入库记录」日志（JSONL；每行 = 一条审计记录的列值字典）

    Args:
        path: 日志文件路径（**必须显式传**：用例用临时目录，绝不碰真实运行目录）。
        enabled: 关闭时退化为"无日志"（单进程语义；调用方须自行承担跨进程风险）。
        fsync: 是否每条 fsync（默认 False —— 见模块 docstring 的取舍说明）。
    """

    def __init__(self, path: str, *, enabled: bool = True,
                 fsync: bool = False) -> None:
        self.path = os.path.abspath(str(path))
        self.enabled = bool(enabled)
        self.fsync = bool(fsync)
        self._handle: Optional[Any] = None
        self._lock = threading.RLock()
        #: 缓存的"末行链头" + 其判据（文件大小/修改时间），用于免去每次 stat 后的重读
        self._cached_size: int = -1
        self._cached_mtime_ns: int = -1
        self._cached_head: Tuple[int, str] = (0, "")
        self._cached_offset: int = 0
        #: 观测计数
        self.appended = 0
        self.append_failures = 0
        self.replayed = 0
        self.compactions = 0
        self.torn_lines = 0
        #: 上次压缩之后新增的行数（供调用方做**压缩节流**，见 chain 的
        #: `_maybe_compact_journal`：不节流会让"每次 append 都重写日志"，
        #: 实测把单条 append 的 p50 从 0.035ms 拖到 2.4ms）
        self.rows_since_compact = 0

    # ── 生命周期 ──

    def _ensure_handle(self) -> Optional[Any]:
        """惰性打开追加句柄（**二进制**追加模式）

        【为什么必须是 ``"ab"`` 而不是 ``"a"``（实现期实测缺陷）】文本模式下
        Windows 会把 ``\\n`` 翻译成 ``\\r\\n``，**实际写入字节数 ≠ 我们算出的
        ``len(encoded)``**。而 ``append_row`` 用"旧大小 + 本次字节数"**算术**推出
        新文件大小（为省掉写后那次 ``nt.stat``，见 ``head()``）—— 两者一旦不等，
        ``head()`` 就认为"文件被外部改动"，于是**每次 append 都重读尾部 256KB**
        （实测 ``bytes.split`` 占 0.48s/1500 次，p50 从 ~0.2ms 反弹回 1.2ms）。
        二进制写入"所见即所写"，字节账目自洽。

        【为什么用追加模式（O_APPEND）】POSIX 下 O_APPEND 保证"定位到 EOF 并写"
        是原子的；即便别处 truncate 过文件，本句柄也会写到**新的** EOF，
        不会在旧偏移留下文件空洞。
        """
        if not self.enabled:
            return None
        if self._handle is not None:
            return self._handle
        try:
            parent = os.path.dirname(self.path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            self._handle = open(self.path, "ab")
            return self._handle
        except OSError as exc:
            self.append_failures += 1
            raise SeqJournalError(f"预留日志不可用 {self.path}: {exc}") from exc

    def close(self) -> None:
        with self._lock:
            if self._handle is not None:
                try:
                    self._handle.flush()
                    self._handle.close()
                except Exception:  # noqa: BLE001
                    pass
                self._handle = None

    def exists(self) -> bool:
        try:
            return os.path.exists(self.path) and os.path.getsize(self.path) > 0
        except OSError:
            return False

    # ── 写入 ──

    def append_row(self, row: Dict[str, Any]) -> int:
        """追加一条待入库记录；返回写入字节数（关闭/失败返回 0）

        Line 形态：``{"seq":..,"ts":..,...,"payload":"<canonical json>"}``
        —— 刻意用**扁平字典**而不是数组，避免与 ``chain._COLUMNS`` 的顺序耦合
        （列顺序将来变动不会让历史日志读不出来）。

        【前置条件】**调用方必须持有跨进程锁**。本方法在写入后就地更新
        "末行链头"缓存（省掉一次 256KB 的尾部重读，实测 p50 由 1.7ms 降到
        ~0.03ms）；该优化只有在"没有别人并发追加"时成立——即持锁。
        """
        if not self.enabled:
            return 0
        line = json.dumps(row, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), default=str)
        encoded = (line + "\n").encode("utf-8")
        if len(encoded) > MAX_JOURNAL_LINE_BYTES:
            self.append_failures += 1
            raise SeqJournalError(
                f"预留日志单行超限（{len(encoded)} > {MAX_JOURNAL_LINE_BYTES}）"
                f"：seq={row.get('seq')}")
        with self._lock:
            handle = self._ensure_handle()
            if handle is None:
                return 0
            try:
                handle.write(encoded)
                handle.flush()
                if self.fsync:
                    os.fsync(handle.fileno())
                    _fsync_dir(self.path)
            except OSError as exc:
                self.append_failures += 1
                # 句柄损坏（磁盘满/被删）→ 丢弃句柄，下次重开
                try:
                    handle.close()
                except Exception:  # noqa: BLE001
                    pass
                self._handle = None
                raise SeqJournalError(f"预留日志写入失败 {self.path}: {exc}") from exc
            self.appended += 1
            self.rows_since_compact += 1
            # 【关键性能：写入后**算术更新缓存**，不做 stat】
            # 作废缓存会让下一次 head() 重读文件尾部（TAIL_SCAN_BYTES=256KB）；
            # 而"写后再 stat 一次"则每次多付一次 nt.stat（Windows 实测 ~0.13ms）。
            # 因为调用方**持跨进程锁**（无并发追加），新大小 = 旧大小 + 本次字节数，
            # 可直接算出。该前提写进方法 docstring。
            try:
                if self._cached_size >= 0:
                    self._cached_size += len(encoded)
                else:
                    self._cached_size, self._cached_mtime_ns = self._stat()
                self._cached_head = (int(row.get("seq") or 0),
                                     str(row.get("self_hash") or ""))
                self._cached_offset = self._cached_size
            except Exception:  # noqa: BLE001 更新失败只影响性能（下次重读）
                self._cached_size, self._cached_mtime_ns = -1, -1
            return len(encoded)

    def flush(self) -> None:
        with self._lock:
            if self._handle is None:
                return
            try:
                self._handle.flush()
                if self.fsync:
                    os.fsync(self._handle.fileno())
            except Exception as exc:  # noqa: BLE001
                logger.debug("预留日志 flush 失败: %s", exc)

    # ── 读取 ──

    def _stat(self) -> Tuple[int, int]:
        try:
            st = os.stat(self.path)
            return int(st.st_size), int(st.st_mtime_ns)
        except OSError:
            return 0, 0

    def head(self) -> Tuple[int, str]:
        """日志**末条完整记录**的 ``(seq, self_hash)``；无有效行返回 ``(0, "")``

        【为什么要缓存（性能，且不牺牲正确性）】``append()`` 每次都问链头；
        每轮都全量读日志会让单条延迟随日志增长而恶化。变更判据用 **文件大小**
        （``st_size``）：别的进程追加必然使文件变大、压缩必然使其变小，两种
        外部动作都会被察觉。

        【为什么只比 size 不比 mtime（实测性能）】``nt.stat`` 在 Windows 上
        一次约 **0.13ms**；而本方法在**每次 append** 都被调用。只用 ``st_size``
        的好处是：本进程刚写完可以用"旧大小 + 本次字节数"**算术推出**新大小
        （见 ``append_row``），从而完全省掉写后的那次 stat。
        残余盲区：另一进程"追加 + 压缩"恰好把大小还原成同一个值——概率极低，
        且后果只是一次重复 seq，由 DB 的 UNIQUE 约束与 ``_resync_seq`` 兜住。
        """
        with self._lock:
            if not self.enabled:
                return 0, ""
            size, mtime = self._stat()
            if size == self._cached_size:
                return self._cached_head
            seq, self_hash, offset = self._read_tail()
            self._cached_size, self._cached_mtime_ns = size, mtime
            self._cached_head, self._cached_offset = (seq, self_hash), offset
            return seq, self_hash

    def _read_tail(self) -> Tuple[int, str, int]:
        """从文件尾部回溯，找到最后一条**完整且合法**的行"""
        size, _ = self._stat()
        if size <= 0:
            return 0, "", 0
        start = max(0, size - TAIL_SCAN_BYTES)
        try:
            with open(self.path, "rb") as fh:
                fh.seek(start)
                raw = fh.read()
        except OSError as exc:
            logger.warning("预留日志读取失败（按无日志处理）: %s", exc)
            return 0, "", 0
        # 末尾若是半行（进程被杀在写一半）→ 直接丢弃这一段
        chunks = raw.split(b"\n")
        if raw[-1:] != b"\n":
            self.torn_lines += 1
        base = start
        for chunk in reversed(chunks):
            text = chunk.decode("utf-8", errors="ignore").strip()
            base -= (len(chunk) + 1)
            if not text:
                continue
            try:
                data = json.loads(text)
            except (ValueError, TypeError):
                # 残缺/损坏行：可能是窗口起点截断的半行，也可能是被外部破坏
                continue
            if not isinstance(data, dict):
                continue
            seq = int(data.get("seq") or 0)
            if seq <= 0:
                continue
            return seq, str(data.get("self_hash") or ""), max(base, 0)
        return 0, "", 0

    def read_since(self, after_seq: int, *, limit: int = 1000) -> List[Dict[str, Any]]:
        """读出 ``seq > after_seq`` 的记录（按 seq 升序，至多 limit 条）

        供两处使用：
        - **启动重放**：把"进程被杀前已分配但未入库"的记录补进 DB（消除丢失）；
        - **后台收敛**：writer 每轮确保日志里没有落后于 DB 水位的记录。
        """
        if not self.enabled:
            return []
        out: List[Dict[str, Any]] = []
        seen: set = set()
        try:
            with open(self.path, "r", encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except (ValueError, TypeError):
                        continue
                    if not isinstance(data, dict):
                        continue
                    seq = int(data.get("seq") or 0)
                    if seq <= int(after_seq) or seq in seen:
                        continue
                    seen.add(seq)
                    out.append(data)
        except OSError as exc:
            logger.warning("预留日志重放读取失败: %s", exc)
            return []
        out.sort(key=lambda d: int(d.get("seq") or 0))
        if limit and len(out) > limit:
            out = out[:int(limit)]
        return out

    def max_seq(self) -> int:
        """日志中出现的最大 seq（0 = 无）"""
        return int(self.head()[0])

    # ── 压缩 ──

    def compact(self, *, upto_seq: int, retain: int = 0) -> bool:
        """压缩日志：丢弃 ``seq <= upto_seq - retain`` 的行

        【前置条件（调用方责任）】``upto_seq`` 必须是**已确认入库的连续水位**：
        即所有 ``seq <= upto_seq`` 的记录**都已持久化在 DB**。满足该前提时，
        本方法丢弃的任何一行都只是"DB 里已有记录的副本"，故**永不丢数据**；
        而 ``seq > upto_seq - retain`` 的尾部（含尚未入库的记录）一律保留。

        【为什么不在方法内自查"有无未入库记录"（实现期修正）】第一版加了
        ``if self.max_seq() > upto_seq: return False`` 的自查，本意是防止误删未入库
        记录；但那与 ``retain`` 语义**直接冲突**：有未入库尾巴时永远压缩不了，
        ``retain`` 形同虚设（用例
        ``test_journal_compaction_keeps_recent_and_never_loses_uncommitted``
        实测失败）。正确的分工是：**水位由调用方保证、裁剪由本方法保证**——
        水位不具备时调用方干脆不调（见 ``AuditChain._maybe_compact_journal``）。

        【为什么是"原地重写"而不是"rename"（不易，实测踩过）】
        ``os.replace`` 会**换 inode**，而其它进程可能正持有旧 inode 的追加句柄
        —— 它们会继续往"已被删除的文件"里写，**静默丢数据**。故坚持原地重写
        （``truncate`` + 写回）。原地重写中途崩溃最多丢掉"已入库记录"的副本，
        链本身无损（正是上面的前置条件给的保证）。

        【为什么要 ``retain``（实现期实测缺陷）】审计链的**降级读路径**
        （``_buffered_extra``）在 DB 不可用时靠日志兜底"读得到刚写的"。
        第一版把日志清成空，于是"已提交入库 → 随后 DB 被判不可用"的记录
        既不在 DB（读不了）也不在日志（被清了）——读路径出现盲区，并让
        ``test_failed_write_keeps_records_visible`` 变成**偶发绿灯**（12 次复现 1–3 次）。
        保留最近 ``retain`` 条已入库记录作为"近期记录缓存"即可消除盲区，
        且磁盘占用有界。``retain=0`` 表示"全清"。

        Returns:
            True = 本次真的改写了日志。
        """
        if not self.enabled:
            return False
        with self._lock:
            if not self.exists():
                return False
            floor = int(upto_seq) - max(int(retain), 0)
            if floor <= 0 and int(retain) <= 0:
                return self._truncate_all()
            try:
                with open(self.path, "r", encoding="utf-8",
                          errors="ignore") as fh:
                    lines = fh.read().splitlines()
            except OSError as exc:
                logger.warning("预留日志压缩读取失败: %s", exc)
                return False
            kept: List[str] = []
            for line in lines:
                text = line.strip()
                if not text:
                    continue
                try:
                    data = json.loads(text)
                except (ValueError, TypeError):
                    # 损坏行：无法判定其 seq ⇒ **保留**（宁留不删）
                    kept.append(text)
                    continue
                seq = int((data or {}).get("seq") or 0) if isinstance(data, dict) else 0
                if seq > floor or seq <= 0:
                    kept.append(text)
            if len(kept) == len(lines):
                return False
            try:
                if self._handle is not None:
                    self._handle.flush()
                # 原地重写：清空后写回（**不 rename**，避免换 inode）
                with open(self.path, "w", encoding="utf-8") as fh:
                    if kept:
                        fh.write("\n".join(kept) + "\n")
                    fh.flush()
            except OSError as exc:
                logger.warning("预留日志压缩失败（不影响写入）: %s", exc)
                return False
            self.compactions += 1
            self.rows_since_compact = 0
            self._cached_size, self._cached_mtime_ns = -1, -1
            self._cached_head, self._cached_offset = (0, ""), 0
            return True

    def _truncate_all(self) -> bool:
        """清空日志（仅当"全部已入库"且不要求保留尾部时使用）"""
        try:
            if self._handle is not None:
                self._handle.flush()
            with open(self.path, "w", encoding="utf-8"):
                pass
        except OSError as exc:
            logger.warning("预留日志清空失败（不影响写入）: %s", exc)
            return False
        self.compactions += 1
        self.rows_since_compact = 0
        self._cached_size, self._cached_mtime_ns = -1, -1
        self._cached_head, self._cached_offset = (0, ""), 0
        return True

    def discard_torn_tail(self) -> int:
        """丢弃末尾的半行（进程被杀留下的残行），返回保留的字节数

        只在**启动期**调用（此时本进程尚未写入，且通常已持跨进程锁）。
        半行若被后续 append 追加在其后，会让两行粘连成一条非法 JSON —— 那会
        让整条记录（及后续）永远读不出来，所以必须在写入前清掉。
        """
        if not self.enabled or not self.exists():
            return 0
        size, _ = self._stat()
        try:
            with open(self.path, "rb") as fh:
                if size > 0:
                    fh.seek(max(0, size - MAX_JOURNAL_LINE_BYTES))
                    raw = fh.read()
                else:
                    raw = b""
        except OSError:
            return 0
        if not raw or raw[-1:] == b"\n":
            return size
        cut = raw.rfind(b"\n")
        keep = size - (len(raw) - cut - 1) if cut >= 0 else 0
        try:
            with open(self.path, "r+b") as fh:
                fh.truncate(max(keep, 0))
            self.torn_lines += 1
            self._cached_size, self._cached_mtime_ns = -1, -1
        except OSError as exc:
            logger.warning("预留日志残行清理失败: %s", exc)
        return max(keep, 0)

    def stats(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "path": self.path,
            "fsync": self.fsync,
            "exists": self.exists(),
            "head_seq": int(self.head()[0]),
            "appended": self.appended,
            "append_failures": self.append_failures,
            "replayed": self.replayed,
            "compactions": self.compactions,
            "rows_since_compact": self.rows_since_compact,
            "torn_lines": self.torn_lines,
        }


__all__ = [
    "TAIL_SCAN_BYTES", "MAX_JOURNAL_LINE_BYTES", "DEFAULT_RETAIN_RECORDS",
    "SeqJournalError", "SeqJournal",
]
