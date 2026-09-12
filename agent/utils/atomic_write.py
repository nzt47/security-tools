"""统一原子写原语（TASK-S8-02 步骤 3「损坏防护：写入原子性」）

【为什么需要它：云枢已经有 **12 份**同款实现】
    写下本模块前先做了全仓普查，``def _atomic_write*`` 至少 12 处：

        env_config_manager.py:494   _atomic_write(lines)
        eval/anchor.py:257          _write_json_atomic(path, payload)
        knowledge/card.py:244       _atomic_write(path, text)
        knowledge/distill.py:229    _atomic_write(path, text)
        knowledge/index.py:66       _atomic_write(path, text)
        knowledge/links_index.py:46 _atomic_write(path, text)
        knowledge/logbook.py:29     _atomic_write(path, text)
        memory/forgetting.py:388    _atomic_write_json(path, payload)
        approval.py:241             _atomic_write_lines(path, lines)
        skills_mgmt/lineage.py:250  _atomic_write_lines(path, lines)
        skills_mgmt/log_archiver.py:208 _atomic_write_lines(path, lines)
        rollback.py:119             _atomic_write_lines(path, lines)

    这与 S8-02 对**锁**的判定是同一条道理：「严禁复制第 N 份实现」。
    故本模块提供**唯一**的原子写原语；本次先把**任务清单内**仍是非原子写的
    4 处接进来（``utc_snapshot.json`` / ``cost_daily.json`` /
    ``cost_brake_state.json`` / ``trace_stats.json``）。上表其余 12 处**不在本任务
    改动面**（属其它任务的代码面），登记为遗留，不在此迁移。

【原子写到底防的是什么（不是"更快"，是"不出现半截文件"）】
    ``open(path, "w")`` 会**立刻截断**目标文件，之后才写入。若进程在写入中途
    被杀、或磁盘写满、或另一进程同时写，留下的是一个**被截断/交错的 JSON**：
    下游 ``json.load`` 直接抛错。对``cost_daily.json`` / ``cost_brake_state.json``
    这类文件，后果不只是"读不到"——它们是 **S5-03 预算刹车**的输入，
    损坏可能让刹车读不到当日累计而**静默失去保护**。

    正确做法是把"新内容"写进**同目录**的临时文件，再 ``os.replace`` 原子地换名：
    - 目标文件在任何时刻都只可能是**旧的完整内容**或**新的完整内容**，不存在中间态；
    - ``os.replace`` 在 POSIX 与 Windows 上都是原子的（``os.rename`` 在 Windows
      上目标存在时会失败，**必须**用 ``replace``）。

【为什么临时文件必须与被替换文件**同目录**】
    ``os.replace`` 只在**同一文件系统**内原子；跨设备会退化为"复制+删除"，
    中途失败即留下半截目标文件。``tempfile.mkstemp(dir=<目标目录>)`` 保证同目录。

【为什么临时名必须唯一（并发写者）】
    固定名（如 ``<target>.tmp``）会让两个并发写者互相踩：A 写完 tmp、B 又截断同一个
    tmp，A 的 ``os.replace`` 就把 **B 的半截内容**换成了目标 ⇒ 原子性被破坏。
    故用 ``mkstemp`` 生成唯一名，并带 ``.tmp`` 后缀便于识别与清理。

【不变式】
    - 成功后**不留**临时文件；失败后尽力清理临时文件（清理失败也不掩盖原异常）；
    - **失败时不改动目标文件**（这是"原子"的全部意义）；
    - 失败一律**上抛原异常**（``OSError`` 等），由调用方决定是否 best-effort——
      本模块不替调用方吞异常，避免"静默降级"。

【⚠️ Windows 上原子写的**代价**（实测，必须知情地接受）】
    - **写侧**：目标若被别的句柄打开且未共享 delete，``os.replace`` 报
      ``WinError 5``。本模块用 ``REPLACE_ATTEMPTS`` 次有界重试消化瞬时占用。
    - **读侧**：换名进行的瞬间，并发读者 ``open()`` 可能拿到
      ``PermissionError [Errno 13]``。**这是本方案有意付出的代价**：
      它把"读者静默拿到半截 JSON"换成了"读者拿到一个可重试的瞬时错误"。
      对 ``cost_daily.json`` / ``cost_brake_state.json`` 这类**预算刹车输入**，
      "可重试的错误"远优于"解析不了或解析出错误数值"。
    - 结论：**读者应当容忍并重试瞬时 PermissionError**；本仓这些文件的生产读者
      （运维脚本 / 巡检 / 面板）都不是热路径，且各自已有 best-effort 兜底。
      该行为已固化为用例（``test_windows_reader_transient_denial_is_expected``），
      以免将来被误当 bug 而退回非原子写。
"""

from __future__ import annotations

import errno
import json
import logging
import os
import tempfile
import time
from typing import Any, Optional

logger = logging.getLogger("agent.utils.atomic_write")

#: 临时文件后缀（同时用于失败清理时的识别）
TEMP_SUFFIX = ".tmp"

#: ``os.replace`` 的重试次数 / 间隔（秒）。
#:
#: 【为什么必须重试（实现期实测的 Windows 约束）】Windows 上 ``os.replace`` 的目标
#: 若正被**另一个句柄打开且未共享 delete**，会失败并报
#: ``PermissionError: [WinError 5] 拒绝访问``。而 Python 内置 ``open()`` 恰恰**不**
#: 共享 delete ⇒ **任何并发读者（哪怕只是瞬间 read_text）都可能让换名失败**。
#: 实测：3 写者 + 3 读者并发时，不重试会有大量 ``WinError 5``。
#: 有界重试把绝大多数瞬时占用转成成功；**重试耗尽仍失败则上抛**——
#: 注意此时目标文件**仍是旧的完整内容**（临时文件已清理），绝不会半截。
REPLACE_ATTEMPTS = 10
REPLACE_RETRY_DELAY_S = 0.02


def _durable_replace(tmp_path: str, target: str) -> None:
    """把 ``tmp_path`` 原子换名为 ``target``（并尽力让换名本身持久）

    对 Windows 的"读者占用导致换名被拒"做**有界重试**，见
    ``REPLACE_ATTEMPTS`` 的实测说明。非瞬时错误（如磁盘满 ``ENOSPC``）不重试。
    """
    if os.path.exists(tmp_path):
        # 先落盘再换名：否则崩溃后可能出现"改名成功但内容还在页缓存"的空文件
        try:
            fd = os.open(tmp_path, os.O_RDONLY)
        except OSError:
            fd = -1
        if fd >= 0:
            try:
                os.fsync(fd)
            except OSError:
                pass
            finally:
                try:
                    os.close(fd)
                except OSError:
                    pass

    last_exc: Optional[BaseException] = None
    for attempt in range(max(int(REPLACE_ATTEMPTS), 1)):
        try:
            os.replace(tmp_path, target)
            return
        except OSError as exc:
            # 只重试"目标被占用"这一族；其余（ENOSPC/EXDEV/权限目录）立即上抛，
            # 免得把真实故障拖成 10 次无用等待。
            transient = isinstance(exc, PermissionError) or getattr(
                exc, "errno", None) in (errno.EACCES, errno.EBUSY, errno.EPERM)
            if not transient or attempt == max(int(REPLACE_ATTEMPTS), 1) - 1:
                raise
            last_exc = exc
            logger.debug("os.replace 被占用，重试 %d/%d: %s",
                         attempt + 1, REPLACE_ATTEMPTS, exc)
            time.sleep(REPLACE_RETRY_DELAY_S * (attempt + 1))
    if last_exc is not None:  # pragma: no cover - 循环必 return 或 raise
        raise last_exc


def _fsync_dir(directory: str) -> None:
    """尽力 fsync 目录项（POSIX 有效；Windows 无此语义，静默跳过）

    【为什么需要】``os.replace`` 的"改名"这一动作本身要先落到目录项上才算持久。
    不做这一步时，掉电后可能出现"内容在、名字没了"或反之。属 best-effort：
    平台不支持不该让写入失败。
    """
    try:
        fd = os.open(directory or ".", os.O_RDONLY)
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


def atomic_write_text(path: Any, text: str, *, encoding: str = "utf-8",
                      fsync: bool = True) -> None:
    """原子地把 ``text`` 写入 ``path``（临时文件 + ``os.replace``）

    Args:
        path: 目标路径（父目录不存在时自动创建）。
        text: 完整的新内容（**整体替换**，不是追加）。
        encoding: 文本编码。
        fsync: 是否在换名前 fsync 临时文件（默认 True）。对"崩溃后必须完整"的
            状态文件保持默认；对可随时重算的纯缓存可在调用方显式关掉。

    Raises:
        OSError: 写入/换名失败（**目标文件保持原样**，临时文件已尽力清理）。
    """
    target = os.path.abspath(os.fspath(path))
    directory = os.path.dirname(target) or "."
    os.makedirs(directory, exist_ok=True)

    data = text.encode(encoding)
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".", suffix=TEMP_SUFFIX)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            if fsync:
                try:
                    os.fsync(handle.fileno())
                except OSError as exc:  # noqa: BLE001 某些文件系统不支持：非致命
                    logger.debug("临时文件 fsync 失败（非致命）: %s", exc)
        _durable_replace(tmp_path, target)
        if fsync:
            _fsync_dir(directory)
    except BaseException:
        # 【为什么用 BaseException】KeyboardInterrupt / 进程被取消也要清临时文件，
        # 否则目标目录会堆积 .tmp（既是垃圾，也会让"归档不删"类盘点误判）。
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except OSError:
            pass  # 清理失败不得掩盖原异常
        raise


def atomic_write_json(path: Any, payload: Any, *, encoding: str = "utf-8",
                      indent: int = 2, ensure_ascii: bool = False,
                      fsync: bool = True,
                      default: Optional[Any] = str) -> str:
    """原子地写出 JSON（返回写出的文本，便于调用方复用/断言）

    与 ``atomic_write_text`` 同一原子性保证；``json.dumps`` 在**写盘之前**完成，
    因此"序列化失败"绝不会触碰目标文件（旧实现是先截断再序列化）。
    """
    text = json.dumps(payload, ensure_ascii=ensure_ascii, indent=indent,
                      default=default)
    atomic_write_text(path, text, encoding=encoding, fsync=fsync)
    return text


__all__ = ["TEMP_SUFFIX", "REPLACE_ATTEMPTS", "REPLACE_RETRY_DELAY_S",
           "atomic_write_text", "atomic_write_json"]
