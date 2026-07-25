"""MarkdownSyncer — SQLite → Markdown 异步物化视图 [TLM-L3]

职责：
- 订阅 HolographicAdapter 的写入/删除事件（notify_change）
- 累积变更，达到 debounce 时间窗或 batch_threshold 后批量 flush
- 按 metadata.category 分组，为每条记忆生成带 YAML Front Matter 的 .md 文件

【不易】
- Front Matter 必含 sqlite_id / last_synced_at / content_hash（+ category / importance）
- content_hash = sha256(data)[:16]，派生值不入库，避免 schema 膲胀
- 文件 I/O 全部在 adapter 锁外执行（持锁禁 I/O，守 project_memory）
- 文件命名锁定：<output_dir>/<category>/<sqlite_id>.md

【变易】
- debounce_seconds / batch_threshold 可配置
- syncer 可选（adapter._syncer=None 时不启用）
- 向量重索引不在本层职责，由 FileWatcher 反向同步时触发

【简易】
- 防抖 = threading.Timer + Lock，标准 debounce 模式
- flush 时一次性读 SQLite，按 category 分组渲染
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Optional

import yaml

from agent.logging_utils import log_dict

logger = logging.getLogger(__name__)


def compute_content_hash(data: str) -> str:
    """计算 content_hash（sha256 前 16 位 hex）。

    Why: 派生值，无需入库；正向写入时算出存入 Front Matter 作为「上次同步基线」，
    反向同步时与文件 hash / DB hash 三路比较判定冲突。
    """
    if not isinstance(data, str):
        data = json.dumps(data, ensure_ascii=False)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()[:16]


def iso_utc_now() -> str:
    """ISO8601 UTC 时间戳（Z 后缀）"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# Front Matter 渲染 + 正文解析的固定分隔
_FM_DELIM = "---"
# 标题行前缀（生成的，反向解析时用于剥离恢复原始 data）
_TITLE_PREFIX = "# "


class MarkdownSyncer:
    """[TLM-L3] Markdown 审计视图 — 异步物化

    用法:
        syncer = MarkdownSyncer(adapter, output_dir="./data/memory_md")
        adapter.set_syncer(syncer)
        # 之后 adapter.save_with_embedding(...) 会自动触发 notify_change → flush
    """

    def __init__(
        self,
        adapter: Any,
        output_dir: str,
        debounce_seconds: int = 5,
        batch_threshold: int = 10,
    ):
        self.adapter = adapter
        self.output_dir = os.path.abspath(output_dir)
        self.debounce_seconds = debounce_seconds
        self.batch_threshold = batch_threshold

        self._lock = threading.Lock()
        # 待 flush 的变更：{key: op}，op ∈ {"upsert", "delete"}
        self._pending: dict[str, str] = {}
        self._timer: Optional[threading.Timer] = None
        # 反向同步抑制集合：FileWatcher 反向更新 SQLite 时加入 key，
        # notify_change 见到则跳过（避免立即重渲染文件），并移除该 key。
        # 幂等性作为第二道保险：即便抑制失效，file_hash==db_hash 也会终止回环。
        self._suppress_keys: set[str] = set()
        self._closed = False
        # [TLM-L2 优化] read_fragment 路径缓存：key → filepath，避免每次 glob 跨目录查找
        # Why: glob(recursive=True) 是 O(子目录数)，缓存后 O(1)；专用锁避免与 _flush 竞争
        self._fragment_path_cache: dict[str, str] = {}
        self._cache_lock = threading.Lock()

        os.makedirs(self.output_dir, exist_ok=True)
        logger.info(
            log_dict({
                "module_name": "markdown_syncer",
                "action": "init",
                "msg": f"[MarkdownSyncer] 初始化完成: output_dir={self.output_dir}, "
                       f"debounce={debounce_seconds}s, batch={batch_threshold}",
            })
        )
        # 【方案二】启动时从 pending_recovery 表恢复未 flush 的 pending（崩溃补偿）
        self._recover_pending()

    # ── 【方案二】崩溃恢复 ──

    def _recover_pending(self):
        """从 pending_recovery 表恢复上次 close 时未 flush 的 pending

        场景：syncer.close() 后到达的 notify_change 落盘到 pending_recovery，
        下次启动时读取并 re-apply 到 _pending，下次 flush 时正常处理。
        补偿完成后清理恢复表（避免重复 re-apply）。
        """
        if self.adapter is None:
            return
        try:
            recovered = self.adapter.load_pending_recovery()
        except Exception as e:
            logger.warning("[MarkdownSyncer] load_pending_recovery 失败: %s", e)
            return
        if not recovered:
            return
        with self._lock:
            for item in recovered:
                self._pending[item["key"]] = item["op"]
        # 清理恢复表（已 re-apply 到 _pending，下次 flush 会处理）
        try:
            self.adapter.clear_pending_recovery()
        except Exception as e:
            logger.warning("[MarkdownSyncer] clear_pending_recovery 失败: %s", e)
        logger.info(
            log_dict({
                "module_name": "markdown_syncer",
                "action": "init.pending_recovered",
                "msg": f"[MarkdownSyncer] 从 pending_recovery 恢复 {len(recovered)} 个 "
                       f"pending（下次 flush 处理）",
            })
        )

    # ── 事件入口（adapter 钩子调用）──

    def notify_change(self, key: str, op: str = "upsert"):
        """累积变更事件，达 debounce 或 batch_threshold 后触发 _flush。

        - 防抖：首次 notify 创建 Timer，后续 notify 只累积 pending（避免高频
          写入时反复 cancel+create Timer 线程，Windows 上线程创建开销大）
        - 批量：累积 key 数 ≥ batch_threshold 立即 flush
        - 抑制：反向同步触发的写入选过 flush（key 在 _suppress_keys 中）

        Why 优化：原实现每次 notify 都 cancel 旧 Timer + create 新 Timer，
        150 次 save 会创建 150 个线程，导致吞吐从 145 ops/s 跌到 43 ops/s。
        改为"首次创建，后续累积"后，burst 内只创建 1 个 Timer，吞吐恢复。
        - 【方案二】_closed=True 后落盘到 pending_recovery，下次启动补偿
        """
        if self._closed:
            # 【方案二】close 后到达的 pending 落盘，避免 silently 丢失
            # 场景：watcher 异步反向更新在 syncer.close 后到达 notify_change
            if self.adapter is not None and key:
                try:
                    self.adapter.save_pending_recovery(key, op)
                except Exception as e:
                    logger.warning(
                        "[MarkdownSyncer] save_pending_recovery 失败 key=%s: %s",
                        key, e,
                    )
            return
        if op not in ("upsert", "delete"):
            op = "upsert"

        fire_now = False
        with self._lock:
            # 反向同步抑制：跳过 flush，仅清理抑制标记
            if key in self._suppress_keys:
                self._suppress_keys.discard(key)
                logger.debug("[MarkdownSyncer] 抑制反向同步触发的重渲染 key=%s", key)
                return

            self._pending[key] = op
            pending_count = len(self._pending)

            if pending_count >= self.batch_threshold:
                # 达批量阈值立即 flush，取消已存在的 Timer
                if self._timer is not None:
                    self._timer.cancel()
                    self._timer = None
                fire_now = True
                logger.debug(
                    log_dict({
                        "module_name": "markdown_syncer",
                        "action": "notify.batch_trigger",
                        "msg": f"[MarkdownSyncer] 批量阈值触发 flush: "
                               f"pending={pending_count} >= batch={self.batch_threshold}",
                    })
                )
            elif self._timer is None:
                # 首次 notify 创建 Timer（burst 内后续 notify 只累积 pending）
                self._timer = threading.Timer(self.debounce_seconds, self._flush)
                self._timer.daemon = True
                self._timer.start()
                logger.debug(
                    log_dict({
                        "module_name": "markdown_syncer",
                        "action": "notify.debounce_scheduled",
                        "msg": f"[MarkdownSyncer] 防抖启动: pending={pending_count} "
                               f"< batch={self.batch_threshold}, "
                               f"debounce={self.debounce_seconds}s key={key} op={op}",
                    })
                )
            else:
                logger.debug(
                    log_dict({
                        "module_name": "markdown_syncer",
                        "action": "notify.accumulate",
                        "msg": f"[MarkdownSyncer] 累积变更: pending={pending_count} "
                               f"key={key} op={op}（Timer 已在运行）",
                    })
                )

        if fire_now:
            self._flush()

    # ── flush 主流程 ──

    def _flush(self):
        """取出 pending 批量渲染（锁内仅交换 pending，I/O 在锁外）"""
        if self._closed:
            return
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            pending = self._pending
            self._pending = {}

        if not pending:
            return

        t0 = time.time()
        upsert_keys = [k for k, op in pending.items() if op == "upsert"]
        delete_keys = [k for k, op in pending.items() if op == "delete"]
        logger.info(
            log_dict({
                "module_name": "markdown_syncer",
                "action": "flush.start",
                "msg": f"[MarkdownSyncer] flush 开始: upsert={len(upsert_keys)}, "
                       f"delete={len(delete_keys)}",
            })
        )

        rendered = 0
        deleted = 0
        errors = 0

        # 正向：按 category 分组读取并渲染
        if upsert_keys:
            try:
                records = self.adapter.get_raw_memories(upsert_keys)
            except Exception as e:
                logger.error(
                    log_dict({
                        "module_name": "markdown_syncer",
                        "action": "flush.read_failed",
                        "msg": f"[MarkdownSyncer] 批量读取 SQLite 失败: {e}",
                    })
                )
                records = []
            grouped: dict[str, list] = {}
            for rec in records:
                cat = rec.get("category") or "uncategorized"
                grouped.setdefault(cat, []).append(rec)
            logger.debug(
                log_dict({
                    "module_name": "markdown_syncer",
                    "action": "flush.grouped",
                    "msg": f"[MarkdownSyncer] 分组完成: categories={len(grouped)} "
                           f"({dict({c: len(r) for c, r in grouped.items()})})",
                })
            )
            for category, recs in grouped.items():
                try:
                    rendered += self._render_file(category, recs)
                except Exception as e:
                    errors += 1
                    logger.error(
                        log_dict({
                            "module_name": "markdown_syncer",
                            "action": "flush.render_failed",
                            "msg": f"[MarkdownSyncer] 渲染 category={category} 失败: {e}",
                        })
                    )

        # 删除：移除对应 .md 文件
        for key in delete_keys:
            try:
                if self._delete_file_for_key(key):
                    deleted += 1
            except Exception as e:
                errors += 1
                logger.error(
                    log_dict({
                        "module_name": "markdown_syncer",
                        "action": "flush.delete_failed",
                        "msg": f"[MarkdownSyncer] 删除文件失败 key={key}: {e}",
                    })
                )

        elapsed_ms = int((time.time() - t0) * 1000)
        logger.info(
            log_dict({
                "module_name": "markdown_syncer",
                "action": "flush.done",
                "duration_ms": elapsed_ms,
                "msg": f"[MarkdownSyncer] flush 完成: rendered={rendered}, "
                       f"deleted={deleted}, errors={errors}, elapsed={elapsed_ms}ms",
            })
        )

    def flush_all(self):
        """全量正向同步：读取 SQLite 全部记忆并渲染。

        用于初始化全量物化或集成测试。"""
        if self._closed:
            return
        t0 = time.time()
        try:
            records = self.adapter.get_raw_memories_all()
        except Exception as e:
            logger.error(
                log_dict({
                    "module_name": "markdown_syncer",
                    "action": "flush_all.read_failed",
                    "msg": f"[MarkdownSyncer] 全量读取失败: {e}",
                })
            )
            return 0

        grouped: dict[str, list] = {}
        for rec in records:
            cat = rec.get("category") or "uncategorized"
            grouped.setdefault(cat, []).append(rec)

        rendered = 0
        for category, recs in grouped.items():
            try:
                rendered += self._render_file(category, recs)
            except Exception as e:
                logger.error(
                    log_dict({
                        "module_name": "markdown_syncer",
                        "action": "flush_all.render_failed",
                        "msg": f"[MarkdownSyncer] 全量渲染 category={category} 失败: {e}",
                    })
                )
        elapsed_ms = int((time.time() - t0) * 1000)
        logger.info(
            log_dict({
                "module_name": "markdown_syncer",
                "action": "flush_all.done",
                "duration_ms": elapsed_ms,
                "msg": f"[MarkdownSyncer] 全量同步完成: rendered={rendered}, "
                       f"elapsed={elapsed_ms}ms",
            })
        )
        return rendered

    def refresh_single(self, key: str) -> bool:
        """[TLM-L3] 重新渲染单个 key 的文件，刷新 Front Matter 的 content_hash 基线。

        Why: 反向同步成功更新 SQLite 后，文件 Front Matter 的 content_hash 仍停在
        上次 forward sync 的旧值。若不刷新，该文件后续任何编辑都会因 base 永远
        偏离 db/file 而误判为双向冲突（违反幂等与冲突不变量）。
        刷新后 file body == db → file_hash == db_hash，watchdog 再触发会幂等跳过，无回环。
        """
        if self._closed:
            return False
        try:
            recs = self.adapter.get_raw_memories([key])
        except Exception as e:
            logger.warning(
                log_dict({
                    "module_name": "markdown_syncer",
                    "action": "refresh_single.read_failed",
                    "msg": f"[MarkdownSyncer] refresh_single 读取失败 key={key}: {e}",
                })
            )
            return False
        if not recs:
            return False
        rec = recs[0]
        cat = rec.get("category") or "uncategorized"
        db_hash = compute_content_hash(rec["data"])
        # 竞态守卫（守不易：不丢失用户数据）：若文件已被用户再次编辑
        # （file_hash != db_hash），跳过刷新，保留用户最新编辑交由下次反向同步处理
        fp = os.path.join(self.output_dir, _safe_dirname(cat), f"{key}.md")
        if os.path.exists(fp):
            existing = parse_markdown_file(fp)
            if existing is not None:
                if compute_content_hash(existing["data"]) != db_hash:
                    logger.info(
                        log_dict({
                            "module_name": "markdown_syncer",
                            "action": "refresh_single.skipped",
                            "msg": f"[MarkdownSyncer] 跳过基线刷新 key={key}: "
                                   f"文件已被再次编辑（保留用户编辑）",
                        })
                    )
                    return False
        try:
            self._render_file(cat, [rec])
            logger.debug(
                log_dict({
                    "module_name": "markdown_syncer",
                    "action": "refresh_single.done",
                    "msg": f"[MarkdownSyncer] 基线已刷新 key={key} "
                           f"(content_hash={db_hash})",
                })
            )
            return True
        except Exception as e:
            logger.warning(
                log_dict({
                    "module_name": "markdown_syncer",
                    "action": "refresh_single.render_failed",
                    "msg": f"[MarkdownSyncer] refresh_single 渲染失败 key={key}: {e}",
                })
            )
            return False

    def read_fragment(self, key: str, max_chars: int = 500) -> str:
        """[TLM-L2] 从 Markdown 归档懒加载指定 key 的前 max_chars 字符

        不变量：L2 冷数据懒加载必须从 .md 归档读取，绝不查 SQLite 主表。
        Why: ContextAssembler.L2 仅在向量摘要命中时按需加载原文片段，
             走 Markdown 归档避免冷数据回压主表查询缓存。

        优化（方案 A+B）：
        - 路径缓存：首次 glob 命中后缓存 key→filepath，后续 O(1) 命中（避免重复 glob）
        - 限量读取：f.read(max_chars*4) 替代 f.read()，避免读全文
          （UTF-8 中文最多 4 字节/字符，读 max_chars*4 字节后截断到 max_chars 字符）

        Args:
            key: 记忆主键（对应 .md 文件名，不含扩展名）
            max_chars: 返回的最大字符数

        Returns:
            文件前 max_chars 字符；文件不存在或读取失败返回空字符串
        """
        if not key:
            return ""
        # 方案 A：路径缓存（首次 glob 后缓存，后续直接读，O(1)）
        fp = self._fragment_path_cache.get(key)
        if fp is None:
            import glob as _glob
            # 每个 key 一个独立 .md 文件，路径 {output_dir}/{category_dir}/{key}.md
            # 不知 category（不查主表），用 glob 跨子目录查找；转义 key 防 glob 元字符注入
            pattern = os.path.join(
                self.output_dir, "**", f"{_glob.escape(key)}.md"
            )
            matches = _glob.glob(pattern, recursive=True)
            if not matches:
                logger.debug("[MarkdownSyncer] read_fragment 未找到归档文件 key=%s", key)
                return ""
            fp = matches[0]
            with self._cache_lock:
                self._fragment_path_cache[key] = fp
        try:
            # 方案 B：限量读取（max_chars*4 字节，UTF-8 中文最多 4 字节/字符）
            with open(fp, "r", encoding="utf-8") as f:
                content = f.read(max_chars * 4)
            return content[:max_chars]
        except Exception as e:
            logger.warning(
                log_dict({
                    "module_name": "markdown_syncer",
                    "action": "read_fragment.failed",
                    "msg": f"[MarkdownSyncer] read_fragment 读取失败 key={key}: {e}",
                })
            )
            return ""

    def _render_file(self, category: str, records: list) -> int:
        """渲染一个 category 下的所有记录，每条 → <category>/<sqlite_id>.md

        Returns: 成功写入的文件数
        """
        cat_dir = os.path.join(self.output_dir, _safe_dirname(category))
        os.makedirs(cat_dir, exist_ok=True)
        count = 0
        for rec in records:
            key = rec["key"]
            data = rec["data"]
            content_hash = compute_content_hash(data)
            # title = data 前 50 字符（单行，去换行）
            title = (data[:50].replace("\n", " ").strip()) or key
            importance = rec.get("importance")
            front = {
                "sqlite_id": key,
                "category": category,
                "last_synced_at": iso_utc_now(),
                "content_hash": content_hash,
            }
            if importance is not None:
                front["importance"] = importance

            # Front Matter + 标题 + 空行 + 原始 data（verbatim）
            fm = yaml.safe_dump(front, allow_unicode=True, sort_keys=False, default_flow_style=False)
            body = f"{_FM_DELIM}\n{fm}{_FM_DELIM}\n\n{_TITLE_PREFIX}{title}\n\n{data}\n"
            # 末尾保证单个换行
            if not body.endswith("\n"):
                body += "\n"

            path = os.path.join(cat_dir, f"{key}.md")
            # 原子写：先写临时文件再 rename，避免半写状态被 FileWatcher 读到
            tmp_path = path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(body)
            os.replace(tmp_path, path)
            count += 1
        return count

    def _delete_file_for_key(self, key: str) -> bool:
        """删除某 key 对应的 .md（遍历 category 子目录查找）"""
        if not os.path.isdir(self.output_dir):
            return False
        removed = False
        for cat_name in os.listdir(self.output_dir):
            cat_dir = os.path.join(self.output_dir, cat_name)
            if not os.path.isdir(cat_dir):
                continue
            target = os.path.join(cat_dir, f"{key}.md")
            if os.path.exists(target):
                try:
                    os.remove(target)
                    removed = True
                except OSError as e:
                    logger.warning("[MarkdownSyncer] 删除文件失败 %s: %s", target, e)
        return removed

    def suppress_for_reverse(self, key: str):
        """标记某 key 正在被反向同步更新，通知 notify_change 跳过重渲染。"""
        with self._lock:
            self._suppress_keys.add(key)

    def close(self):
        """关闭：取消计时器，循环 flush 残留 pending，再设 _closed。

        【不易】循环 flush 直到 pending 为空，避免单次 _flush 后到 _closed=True
        窗口期到达的 notify_change 累积的 pending 丢失（数据完整性约束）。
        max_rounds 兜底防活锁（close 阶段不应有持续高频写入，超过则记警告退出）。
        幂等：二次调用时 _closed 已 True，循环检查 pending 为空直接退出。
        """
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
        # 【不易】循环 flush 直到 pending 为空
        # Why: 单次 _flush 后到 _closed=True 前的窗口期，notify_change 可能累积
        #      新 pending，不循环 flush 会导致这部分丢失
        max_rounds = 5  # 防活锁兜底
        for round_i in range(max_rounds):
            with self._lock:
                pending_empty = len(self._pending) == 0
            if pending_empty:
                break
            self._flush()
        else:
            # 循环超过 max_rounds 仍有 pending，落盘残留后强制退出
            # 【不易】守数据完整性：兜底退出前必须把残留 pending 落盘到 pending_recovery，
            #        否则下次启动无法补偿（silently 丢失违反不变量）
            with self._lock:
                residual_pending = dict(self._pending)
                self._pending.clear()
            if residual_pending and self.adapter is not None:
                for key, op in residual_pending.items():
                    try:
                        self.adapter.save_pending_recovery(key, op)
                    except Exception as e:
                        logger.warning(
                            "[MarkdownSyncer] close 兜底落盘失败 key=%s: %s", key, e
                        )
            logger.warning(
                log_dict({
                    "module_name": "markdown_syncer",
                    "action": "close.max_rounds_exceeded",
                    "msg": f"[MarkdownSyncer] close() 循环 flush 超过 {max_rounds} 次"
                           f"仍有 {len(residual_pending)} 个 pending（疑似持续高频写入），"
                           f"已落盘 pending_recovery，强制退出",
                })
            )
        self._closed = True


def _safe_dirname(name: str) -> str:
    """category → 安全目录名（去除路径分隔符）"""
    return "".join(c if c not in r"\/:*?\"<>|" else "_" for c in str(name)) or "uncategorized"


# ── Front Matter 解析（供 FileWatcher 复用）──

def parse_markdown_file(path: str) -> Optional[dict]:
    """解析带 YAML Front Matter 的 Markdown 文件。

    Returns:
        {
            "front_matter": {...},   # YAML 解析结果
            "data": str,             # 剥离标题后的原始 data（用于反向同步）
            "body": str,             # Front Matter 之后的完整正文
        }
        文件无合法 Front Matter 返回 None。
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return None

    return parse_markdown_content(content)


def parse_markdown_content(content: str) -> Optional[dict]:
    """纯字符串版解析（便于测试）"""
    if not content.startswith(_FM_DELIM):
        return None
    # 找第二个 --- 作为 Front Matter 结束
    rest = content[len(_FM_DELIM):]
    # 跳过首行换行
    if rest.startswith("\r\n"):
        rest = rest[2:]
    elif rest.startswith("\n"):
        rest = rest[1:]
    end_idx = rest.find(f"\n{_FM_DELIM}")
    if end_idx == -1:
        return None
    fm_text = rest[:end_idx]
    body = rest[end_idx + len(_FM_DELIM) + 1:]  # 跳过 \n---
    # body 可能以 \n--- 之外的 --- 开头，再吞掉可能的 ---
    if body.startswith(_FM_DELIM):
        body = body[len(_FM_DELIM):]
    body = body.lstrip("\n")

    try:
        front_matter = yaml.safe_load(fm_text) or {}
    except yaml.YAMLError:
        return None
    if not isinstance(front_matter, dict):
        return None

    data = _extract_data_from_body(body)
    return {"front_matter": front_matter, "data": data, "body": body}


def _extract_data_from_body(body: str) -> str:
    """从正文中剥离生成的标题行，恢复原始 data。

    规则：
    1. 首行若以 '# ' 开头（生成的标题），丢弃该行 + 紧随的一个空行，剩余为 data
    2. 剥离恰好一个尾随换行（渲染时固定追加的 \n），保证往返精确
    Why: 正向渲染格式固定为 `# {title}\n\n{data}\n`，反向需精确还原 data 以算 hash；
         若不剥离尾随 \n，file_hash 恒不等于 db_hash，幂等性失效（守不易约束）。
    """
    if not body:
        return ""
    lines = body.split("\n")
    idx = 0
    # 跳过开头的空行（防御）
    while idx < len(lines) and lines[idx].strip() == "":
        idx += 1
    if idx < len(lines) and lines[idx].startswith(_TITLE_PREFIX):
        idx += 1
        # 跳过标题后恰好一个空行
        if idx < len(lines) and lines[idx].strip() == "":
            idx += 1
    result = "\n".join(lines[idx:])
    # 剥离渲染时追加的恰好一个尾随换行（往返精确性）
    if result.endswith("\n"):
        result = result[:-1]
    return result
