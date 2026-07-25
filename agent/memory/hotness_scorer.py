"""HotnessScorer — 记忆热度计算与冷热调度 [TLM-L2]

职责：
- record_access: 检索命中时记录访问事件（内存 + 由 adapter 事务性更新 SQLite）
- compute_hotness: 按公式计算单条记忆的热度分
- get_hot_records: 查询热度 Top-N（供 ContextAssembler.L0 热数据层使用）
- run_background_scan: 后台周期性重算全表 hotness 列并持久化

【不易】
- 热度公式锁定：hotness = importance * access_count / (hours_since + 1) ^ decay
  · importance: 业务重要性（默认 1.0，由 metadata.importance 或主表列提供）
  · access_count: 检索命中累计次数（事务性更新，见 adapter._record_access_for_results）
  · hours_since: 距 last_accessed 的小时数（未访问记 0）
  · decay: 时间衰减指数（默认 1.5，可配）
- access_count = 0 时 hotness = 0（无访问记录的记忆不进热数据层）
- 后台扫描持锁仅更新内存与 SQL，禁外部 I/O 回调（守 project_memory）

【变易】
- decay / scan_interval / batch_size 通过构造参数可配
- adapter 可选（None 时 compute_hotness 仍可纯函数调用，但 get_hot_records/run_background_scan 不可用）

【简易】
- 内存访问缓存 _access_cache: {key: (count, last_ts)}，避免每次 compute 都查 SQLite
- get_hot_records 复用 adapter.get_raw_memories_all + 排序，无需新 SQL
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Optional

from agent.logging_utils import log_dict

logger = logging.getLogger(__name__)


class HotnessScorer:
    """[TLM-L2] 记忆热度计算器

    用法:
        adapter = HolographicAdapter(db_path="...")
        scorer = HotnessScorer(adapter)
        adapter.set_scorer(scorer)
        # 之后 adapter.search(...) 命中会自动调用 scorer.record_access
        hot = scorer.get_hot_records(top_n=10)
        # 后台扫描（可选，由调用方启动线程）
        scorer.run_background_scan_once()
    """

    # 默认衰减指数（1.5 介于线性 1.0 与平方 2.0 之间，平衡近期权重与长尾衰减）
    _DEFAULT_DECAY_EXPONENT = 1.5
    # 默认 importance 兜底值（无 metadata.importance 时）
    _DEFAULT_IMPORTANCE = 1.0
    # 后台扫描默认周期（秒）
    _DEFAULT_SCAN_INTERVAL = 300.0
    # 后台批量 UPDATE 大小（避免单次事务过长阻塞 search）
    _DEFAULT_BATCH_SIZE = 200
    # _access_cache 容量上限（LRU 防止无限增长）
    _CACHE_MAX_SIZE = 2000

    def __init__(
        self,
        adapter: Optional[Any] = None,
        decay_exponent: float = _DEFAULT_DECAY_EXPONENT,
        scan_interval: float = _DEFAULT_SCAN_INTERVAL,
        batch_size: int = _DEFAULT_BATCH_SIZE,
    ):
        """初始化 HotnessScorer

        Args:
            adapter: HolographicAdapter 实例（用于读取全表 + 更新 hotness 列）
                     传 None 时仅支持纯函数 compute_hotness，无法持久化与查询
            decay_exponent: 时间衰减指数（建议 1.0 ~ 2.0）
            scan_interval: 后台扫描周期秒
            batch_size: 批量 UPDATE 大小
        """
        self.adapter = adapter
        self.decay_exponent = float(decay_exponent)
        self.scan_interval = float(scan_interval)
        self.batch_size = int(batch_size)

        self._lock = threading.Lock()
        # 内存访问缓存：{key: [access_count, last_accessed_ts]}
        # Why: 减少 SQLite 读压力，compute_hotness 优先用缓存值
        self._access_cache: dict[str, list] = {}
        self._scan_thread: Optional[threading.Thread] = None
        self._scan_stop = threading.Event()

        logger.info(
            log_dict({
                "module_name": "hotness_scorer",
                "action": "init",
                "msg": f"[HotnessScorer] 初始化完成: decay={self.decay_exponent}, "
                       f"scan_interval={self.scan_interval}s, batch={self.batch_size}, "
                       f"adapter={'injected' if adapter else 'none'}",
            })
        )

    # ── 访问事件记录 ──

    def record_access(self, key: str, timestamp: Optional[float] = None) -> None:
        """记录一次检索命中（由 adapter._record_access_for_results 在锁外调用）

        Why: adapter 的事务性更新已写 SQLite 主表 access_count/last_accessed，
             本方法仅同步内存缓存，保证 compute_hotness 即时看到最新值。

        Args:
            key: 记忆主键
            timestamp: 访问时间戳（默认 time.time()）
        """
        if not key:
            return
        ts = float(timestamp) if timestamp is not None else time.time()
        with self._lock:
            entry = self._access_cache.get(key)
            if entry is None:
                # LRU 兜底：缓存超限时清最早插入项（dict 保持插入序）
                if len(self._access_cache) >= self._CACHE_MAX_SIZE:
                    # 弹出第一个 key（FIFO 近似 LRU，避免引入 OrderedDict 复杂度）
                    first_key = next(iter(self._access_cache))
                    self._access_cache.pop(first_key, None)
                self._access_cache[key] = [1, ts]
            else:
                entry[0] += 1
                entry[1] = ts

    # ── 热度计算 ──

    def compute_hotness(self, record: dict) -> float:
        """纯函数：根据 record 字段计算热度分

        公式：hotness = importance * access_count / (hours_since + 1) ^ decay
        - access_count <= 0 → 0.0（无访问不进热数据层）
        - last_accessed 缺失 → hours_since = 0（视为刚访问，避免冷启动误判）

        Args:
            record: 含 importance / access_count / last_accessed 的字典
                    （来自 adapter.get_raw_memory 或内存缓存）

        Returns:
            热度分（float，>= 0）
        """
        importance = float(record.get("importance") or self._DEFAULT_IMPORTANCE)
        access_count = int(record.get("access_count") or 0)
        if access_count <= 0:
            return 0.0
        last_accessed = record.get("last_accessed")
        try:
            last_ts = float(last_accessed) if last_accessed is not None else None
        except (TypeError, ValueError):
            last_ts = None
        if last_ts is not None and last_ts > 0:
            hours_since = max(0.0, (time.time() - last_ts) / 3600.0)
        else:
            hours_since = 0.0
        denominator = (hours_since + 1.0) ** self.decay_exponent
        return importance * access_count / denominator

    # ── 热数据查询 ──

    def get_hot_records(self, top_n: int = 10) -> list[dict]:
        """查询热度 Top-N 记录（供 ContextAssembler.L0 使用）

        策略：
        1. 优先用 adapter.get_raw_memories_all 读取全表
        2. 用内存 _access_cache 覆盖 access_count/last_accessed（最新值）
        3. compute_hotness 排序取前 N
        4. 失败/空表返回空列表（守降级）

        Args:
            top_n: 返回前 N 条

        Returns:
            [{key, data, metadata, category, importance, access_count,
              last_accessed, hotness}] 按 hotness 降序
        """
        if self.adapter is None:
            logger.debug("[HotnessScorer] adapter 未注入，get_hot_records 返回空列表")
            return []
        if top_n <= 0:
            return []
        try:
            records = self.adapter.get_raw_memories_all()
        except Exception as e:
            logger.warning(
                log_dict({
                    "module_name": "hotness_scorer",
                    "action": "get_hot_records.read_failed",
                    "msg": f"[HotnessScorer] 读取全表失败: {e}",
                })
            )
            return []
        if not records:
            return []
        # 用内存缓存覆盖最新访问状态
        with self._lock:
            cache_snapshot = {k: list(v) for k, v in self._access_cache.items()}
        enriched = []
        for rec in records:
            key = rec.get("key")
            cached = cache_snapshot.get(key)
            if cached is not None:
                rec = {**rec, "access_count": cached[0], "last_accessed": cached[1]}
            hotness = self.compute_hotness(rec)
            rec["hotness"] = hotness
            enriched.append(rec)
        enriched.sort(key=lambda r: r["hotness"], reverse=True)
        top = enriched[:top_n]
        # 仅对返回的 top-N 埋点（避免全表埋点拖慢关键路径，守简易）
        try:
            from agent.memory.observability import track_tlm_hotness_score
            for rec in top:
                track_tlm_hotness_score(rec.get("key", ""), rec.get("hotness", 0.0))
        except Exception as e_metric:
            logger.debug("[HotnessScorer] hotness 埋点失败（忽略）: %s", e_metric)
        return top

    # ── 后台扫描（持久化 hotness 列）──

    def run_background_scan_once(self) -> int:
        """执行一次全表 hotness 重算并 UPDATE 主表 hotness 列

        Why: hotness 是派生值，access_count/last_accessed 变更后需重算持久化，
             供 SQL 直接 ORDER BY hotness 加速 L0 查询。本方法单次执行，不循环。

        Returns: 成功更新的记录数
        """
        if self.adapter is None:
            logger.debug("[HotnessScorer] adapter 未注入，跳过后台扫描")
            return 0
        try:
            records = self.adapter.get_raw_memories_all()
        except Exception as e:
            logger.warning(
                log_dict({
                    "module_name": "hotness_scorer",
                    "action": "scan.read_failed",
                    "msg": f"[HotnessScorer] 后台扫描读取全表失败: {e}",
                })
            )
            return 0
        if not records:
            return 0
        # 用内存缓存覆盖最新访问状态
        with self._lock:
            cache_snapshot = {k: list(v) for k, v in self._access_cache.items()}
        updated = 0
        t0 = time.time()
        # 分批 UPDATE，避免单事务过长
        for i in range(0, len(records), self.batch_size):
            batch = records[i:i + self.batch_size]
            updates: list[tuple[float, str]] = []
            for rec in batch:
                key = rec.get("key")
                if not key:
                    continue
                cached = cache_snapshot.get(key)
                if cached is not None:
                    rec = {**rec, "access_count": cached[0], "last_accessed": cached[1]}
                hotness = self.compute_hotness(rec)
                updates.append((hotness, key))
            if not updates:
                continue
            try:
                self._batch_update_hotness(updates)
                updated += len(updates)
            except Exception as e:
                logger.warning(
                    log_dict({
                        "module_name": "hotness_scorer",
                        "action": "scan.batch_failed",
                        "msg": f"[HotnessScorer] 批量更新失败 batch={i}: {e}",
                    })
                )
        elapsed_ms = int((time.time() - t0) * 1000)
        logger.info(
            log_dict({
                "module_name": "hotness_scorer",
                "action": "scan.done",
                "duration_ms": elapsed_ms,
                "msg": f"[HotnessScorer] 后台扫描完成: updated={updated}, "
                       f"elapsed={elapsed_ms}ms",
            })
        )
        return updated

    def _batch_update_hotness(self, updates: list[tuple[float, str]]) -> None:
        """批量 UPDATE memory_items.hotness（持锁仅做 SQL，无外部回调）

        Args:
            updates: [(hotness, key), ...]
        """
        if not updates or self.adapter is None:
            return
        # 复用 adapter 的连接与锁，保证与 search 等操作的互斥
        with self.adapter._lock:
            with self.adapter._get_conn() as conn:
                # 分 CASE 单条 UPDATE 会触发多次往返，这里用 executemany 批量
                conn.executemany(
                    f"UPDATE {self.adapter._CONTENT_TABLE} SET hotness = ? WHERE key = ?",
                    updates,
                )
                conn.commit()

    def start_background_scan(self) -> bool:
        """启动后台周期扫描线程（守护线程，进程退出自动结束）

        Returns: True 表示已启动（或已在运行）
        """
        if self._scan_thread is not None and self._scan_thread.is_alive():
            return True
        if self.adapter is None:
            logger.warning("[HotnessScorer] adapter 未注入，无法启动后台扫描")
            return False
        self._scan_stop.clear()
        self._scan_thread = threading.Thread(
            target=self._scan_loop, daemon=True, name="hotness-scan",
        )
        self._scan_thread.start()
        logger.info(
            log_dict({
                "module_name": "hotness_scorer",
                "action": "scan.start",
                "msg": f"[HotnessScorer] 后台扫描已启动: interval={self.scan_interval}s",
            })
        )
        return True

    def stop_background_scan(self) -> None:
        """停止后台扫描线程"""
        self._scan_stop.set()
        if self._scan_thread is not None:
            self._scan_thread.join(timeout=2.0)
            self._scan_thread = None
        logger.info("[HotnessScorer] 后台扫描已停止")

    def _scan_loop(self) -> None:
        """后台扫描主循环（守护线程）"""
        while not self._scan_stop.is_set():
            try:
                self.run_background_scan_once()
            except Exception as e:
                logger.warning(
                    log_dict({
                        "module_name": "hotness_scorer",
                        "action": "scan.loop_exception",
                        "msg": f"[HotnessScorer] 扫描循环异常（继续）: {e}",
                    })
                )
            # 用 wait 代替 sleep，便于及时响应停止信号
            self._scan_stop.wait(self.scan_interval)
