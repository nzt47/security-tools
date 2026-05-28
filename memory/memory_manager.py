"""MemoryManager — 记忆管理系统的核心编排层"""

import logging
import threading
from datetime import datetime, timezone
from .token_counter import TokenCounter
from .llm_service import LLMService
from .summarizer import Summarizer
from .storage import Storage
from .black_box import BlackBox

logger = logging.getLogger(__name__)


class AsyncCompressor:
    """后台压缩线程

    定时检查是否需要压缩，异步执行摘要生成。
    """

    def __init__(self, summarizer, storage, black_box, interval: int = 60):
        self._summarizer = summarizer
        self._storage = storage
        self._black_box = black_box
        self._interval = interval
        self._event = threading.Event()
        self._stop_event = threading.Event()
        self._thread = None
        self._pending = False

    def start(self):
        """启动后台线程"""
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("后台压缩线程已启动")

    def stop(self):
        """优雅停止后台线程"""
        self._stop_event.set()
        self._event.set()  # 唤醒线程
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        logger.info("后台压缩线程已停止")

    def request(self):
        """标记需要压缩"""
        self._pending = True
        self._event.set()

    def has_pending(self) -> bool:
        """是否有待处理的压缩请求"""
        return self._pending

    def is_running(self) -> bool:
        """后台线程是否正在运行"""
        return self._thread is not None and self._thread.is_alive()

    def _run(self):
        while not self._stop_event.is_set():
            triggered = self._event.wait(timeout=self._interval)
            if self._stop_event.is_set():
                break
            self._event.clear()
            if triggered:
                self._do_compress()

    def _do_compress(self):
        """执行压缩任务"""
        try:
            old_summary = self._storage.load_summary()
            recent_messages = self._storage.load_recent_messages(limit=100)

            if not recent_messages:
                return

            # 使用 LLM 压缩
            summary = self._summarizer.compress(recent_messages)

            if old_summary:
                old_text, old_version = old_summary
                summary = self._summarizer.merge_summaries(old_text, summary)
                new_version = old_version + 1
            else:
                new_version = 1

            self._storage.save_summary(summary, new_version)
            self._black_box.log("memory_compress", {
                "version": new_version,
                "messages_count": len(recent_messages)
            })
            self._pending = False
            logger.info("后台压缩完成，版本 %d", new_version)
        except Exception as e:
            logger.error("后台压缩失败: %s", e)


class MemoryManager:
    """记忆管理器 — 灵犀的记忆系统入口

    管理对话历史、滚动摘要、黑匣子日志的完整生命周期。
    """

    def __init__(self, config: dict = None):
        config = config or {}

        # Token 计数器
        self._token_counter = TokenCounter()

        # LLM 服务
        llm_cfg = config.get("llm", {})
        if llm_cfg.get("api_key"):
            self._llm_service = LLMService(
                provider=llm_cfg.get("provider", "openai"),
                api_key=llm_cfg["api_key"],
                model=llm_cfg.get("model", "gpt-4"),
                timeout=llm_cfg.get("timeout", 30)
            )
        else:
            self._llm_service = None
            logger.warning("未配置 LLM API Key，摘要功能不可用")

        # 摘要器
        self._summarizer = Summarizer(llm_service=self._llm_service)

        # 存储
        data_dir = config.get("data_dir", "./memory_data")
        self._storage = Storage(data_dir=data_dir)

        # 黑匣子
        bb_cfg = config.get("blackbox", {})
        self._black_box = BlackBox(
            log_dir=config.get("blackbox_dir", f"{data_dir}/blackbox"),
            max_size_bytes=bb_cfg.get("max_size_mb", 10) * 1024 * 1024,
            max_files=bb_cfg.get("max_files", 10)
        )

        # 后台压缩
        ac_cfg = config.get("async_compress", {})
        self._async_compressor = AsyncCompressor(
            summarizer=self._summarizer,
            storage=self._storage,
            black_box=self._black_box,
            interval=ac_cfg.get("interval_seconds", 60)
        )
        if ac_cfg.get("enabled", True):
            self._async_compressor.start()

        # 压缩阈值
        self._token_limit = config.get("token_limit", 4096)
        self._compress_threshold = config.get("compress_threshold", 0.8)

        self._need_compress = False
        logger.info("MemoryManager 初始化完成")

    def add_message(self, role: str, content: str) -> str:
        """添加新消息

        保存消息、记录日志、检查是否需要压缩。

        Args:
            role: 消息角色（user/assistant/system）
            content: 消息内容

        Returns:
            消息的时间戳 ID
        """
        message = {
            "role": role,
            "content": content,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        msg_id = self._storage.save_message(message)

        # 记录黑匣子
        self._black_box.log("message_added", {
            "role": role,
            "tokens": self._token_counter.count(content)
        })

        # 检查 Token 占用
        recent = self._storage.load_recent_messages(limit=200)
        total_tokens = self._token_counter.count_messages(recent)
        if self._summarizer.should_compress(total_tokens, self._token_limit,
                                            self._compress_threshold):
            self._need_compress = True
            self._async_compressor.request()

        return msg_id

    def get_context(self, token_limit: int) -> list[dict]:
        """获取压缩后的上下文

        如果标记了需要压缩，先尝试同步压缩（当没有后台线程时）。
        组装为 [system 摘要, recent_messages...] 格式。

        Args:
            token_limit: 上下文窗口 Token 上限

        Returns:
            消息列表 [{"role": "...", "content": "..."}]，无内容时返回空列表
        """
        # 如果有压缩需求且没有后台线程，同步执行
        if self._need_compress:
            if not self._async_compressor.is_running():
                recent = self._storage.load_recent_messages(limit=100)
                if recent:
                    summary = self._summarizer.compress(recent)
                    old = self._storage.load_summary()
                    if old:
                        old_text, old_version = old
                        summary = self._summarizer.merge_summaries(old_text, summary)
                        self._storage.save_summary(summary, old_version + 1)
                    else:
                        self._storage.save_summary(summary, 1)
            # 后台线程已处理或本次同步处理完成，清除标记
            self._need_compress = False

        # 加载摘要和最近消息
        summary = self._storage.load_summary()
        recent = self._storage.load_recent_messages(limit=20)

        if not recent:
            return []

        context = []
        if summary:
            summary_text, _ = summary
            context.append({
                "role": "system",
                "content": f"以下是之前的对话摘要：\n{summary_text}"
            })

        context.extend(recent)

        # 如果仍然超限，丢弃最旧消息
        has_summary = summary is not None
        while len(context) > 1:
            total = self._token_counter.count_messages(context)
            if total <= token_limit:
                break
            # 丢弃最旧的非摘要消息
            context.pop(1 if has_summary else 0)

        return context

    def compress(self, old_messages: list[dict]) -> str:
        """压缩历史对话（同步接口）

        Args:
            old_messages: 待压缩的消息列表

        Returns:
            摘要文本
        """
        return self._summarizer.compress(old_messages)

    def save_log(self, event_type: str, data: dict):
        """记录黑匣子事件（快捷入口）"""
        self._black_box.log(event_type, data)

    def load_summary(self) -> tuple[str, int] | None:
        """加载当前摘要"""
        return self._storage.load_summary()

    def clear_memory(self):
        """清空记忆（保留摘要和黑匣子日志）"""
        self._storage.clear_messages()
        self._need_compress = False
        self._black_box.log("memory_cleared", {})
        logger.info("记忆已清空")

    def query_logs(self, **filters) -> list[dict]:
        """查询黑匣子日志（快捷入口）"""
        return self._black_box.query(**filters)

    def __del__(self):
        """析构时停止后台线程"""
        if hasattr(self, '_async_compressor'):
            self._async_compressor.stop()
