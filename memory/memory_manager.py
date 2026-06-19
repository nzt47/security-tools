"""MemoryManager — 记忆管理系统的核心编排层"""

import asyncio
import logging
import threading
import time
from datetime import datetime, timezone
from .token_counter import TokenCounter
from .llm_service import LLMService
from .summarizer import Summarizer
from .storage import Storage
from .black_box import BlackBox

logger = logging.getLogger(__name__)

def log_event_loop_status() -> str:
    """检查并记录事件循环状态"""
    try:
        loop = asyncio.get_running_loop()
        return f"事件循环: {type(loop).__name__}, 运行中: {loop.is_running()}, 关闭: {loop.is_closed()}"
    except RuntimeError:
        return "事件循环: 未找到运行中的事件循环"
    except Exception as e:
        return f"事件循环检查异常: {e}"


class AsyncCompressor:
    """后台压缩任务（asyncio实现）

    使用asyncio协程替代threading，避免阻塞主线程，提高并发性能。
    """

    def __init__(self, memory_manager, interval: int = 60):
        self._memory_manager = memory_manager
        self._interval = interval
        self._pending = False
        self._running = False
        self._task = None
        self._lock = threading.Lock()

    async def start(self):
        """启动后台压缩任务（异步版本）"""
        with self._lock:
            if self._running:
                return
            self._running = True

        self._task = asyncio.create_task(self._run())
        logger.warning("后台压缩任务已启动")

    def start_sync(self):
        """启动后台压缩任务（同步版本，保持向后兼容）"""
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.start())
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.create_task(self.start())
            threading.Thread(target=loop.run_forever, daemon=True).start()

    async def stop(self):
        """优雅停止后台压缩任务（异步版本）"""
        with self._lock:
            self._running = False

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        logger.warning("后台压缩任务已停止")

    def stop_sync(self):
        """优雅停止后台压缩任务（同步版本，保持向后兼容）"""
        if self._task:
            self._task.cancel()
            self._task = None
        with self._lock:
            self._running = False
        logger.warning("后台压缩任务已停止")

    def request(self):
        """标记需要压缩"""
        with self._lock:
            self._pending = True

    def has_pending(self) -> bool:
        """是否有待处理的压缩请求"""
        with self._lock:
            return self._pending

    def is_running(self) -> bool:
        """后台任务是否正在运行"""
        with self._lock:
            return self._running

    async def _run(self):
        """后台任务主循环"""
        loop_start_time = time.time()
        iteration_count = 0
        logger.warning("┌═══════════════════════════════════════════════")
        logger.warning("│ 🔄 [后台压缩循环] 主循环启动")
        logger.warning(f"│    ├─ {log_event_loop_status()}")
        logger.warning(f"│    ├─ 检查间隔: {self._interval}s")
        logger.warning(f"│    └─ 启动时间: {datetime.now(timezone.utc).isoformat()}")
        logger.warning("└═══════════════════════════════════════════════")

        while self._running:
            iteration_count += 1
            iter_start = time.time()
            try:
                with self._lock:
                    pending = self._pending

                if pending:
                    logger.debug(f"[后台压缩循环] 第 {iteration_count} 轮 - 有待处理请求，执行压缩")
                    await self._do_compress()
                else:
                    logger.debug(f"[后台压缩循环] 第 {iteration_count} 轮 - 无待处理请求，等待 {self._interval}s")
                    await asyncio.sleep(self._interval)

                iter_elapsed = (time.time() - iter_start) * 1000
                logger.debug(f"[后台压缩循环] 第 {iteration_count} 轮完成，耗时: {iter_elapsed:.2f}ms")

            except asyncio.CancelledError:
                logger.warning("┌═══════════════════════════════════════════════")
                logger.warning("│ ⏹️ [后台压缩循环] 收到取消信号，退出循环")
                logger.warning("└═══════════════════════════════════════════════")
                break
            except Exception as e:
                logger.error("┌═══════════════════════════════════════════════")
                logger.error(f"│ ❌ [后台压缩循环] 第 {iteration_count} 轮异常: {e}")
                logger.error(f"│    └─ {log_event_loop_status()}")
                logger.error("└═══════════════════════════════════════════════")
                logger.error("堆栈跟踪:", exc_info=True)
                await asyncio.sleep(self._interval)

        total_elapsed = (time.time() - loop_start_time) * 1000
        logger.warning("┌═══════════════════════════════════════════════")
        logger.warning(f"│ 🛑 [后台压缩循环] 主循环结束")
        logger.warning(f"│    ├─ 总迭代次数: {iteration_count}")
        logger.warning(f"│    ├─ 总运行时间: {total_elapsed:.2f}ms")
        logger.warning(f"│    └─ {log_event_loop_status()}")
        logger.warning("└═══════════════════════════════════════════════")

    def _is_skill_enabled_static(self, skill_id: str) -> bool:
        """静态检查技能是否启用（供后台线程使用）"""
        try:
            import json, os
            result = True
            for sf in [
                os.path.join(os.path.dirname(__file__), '..', 'data', 'skills.json'),
                os.path.join(os.path.dirname(__file__), '..', 'agent', 'data', 'skills.json'),
            ]:
                if os.path.exists(sf):
                    with open(sf, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    for s in data.get("skills", []):
                        if s["id"] == skill_id:
                            result = s.get("enabled", True)
            return result
        except Exception:
            pass
        return True

    async def _do_compress(self):
        """异步执行压缩任务"""
        if not self._is_skill_enabled_static("memory_summary"):
            logger.debug("[Memory] memory_summary 技能已禁用，跳过压缩")
            return
        start_time = time.time()
        logger.warning("┌═══════════════════════════════════════════════")
        logger.warning("│ 🚀 [后台压缩] 开始执行压缩任务")
        logger.warning(f"│    ├─ 线程ID: {threading.current_thread().ident}")
        logger.warning(f"│    ├─ 线程名称: {threading.current_thread().name}")
        logger.warning(f"│    └─ {log_event_loop_status()}")
        logger.warning("└═══════════════════════════════════════════════")

        # 使用线程池执行阻塞操作，避免阻塞事件循环
        loop = asyncio.get_event_loop()
        logger.warning("│ 🔄 [后台压缩] 将压缩任务提交到线程池执行")
        submit_time = time.time()

        success = await loop.run_in_executor(
            None,
            self._memory_manager._execute_compression
        )

        exec_elapsed = (time.time() - submit_time) * 1000
        total_elapsed = (time.time() - start_time) * 1000

        if success:
            with self._lock:
                self._pending = False
            logger.warning("┌═══════════════════════════════════════════════")
            logger.warning("│ ✅ [后台压缩] 压缩任务完成")
            logger.warning(f"│    ├─ 线程池执行耗时: {exec_elapsed:.2f}ms")
            logger.warning(f"│    ├─ 总耗时: {total_elapsed:.2f}ms")
            logger.warning(f"│    ├─ 待处理标记已清除")
            logger.warning(f"│    └─ {log_event_loop_status()}")
            logger.warning("└═══════════════════════════════════════════════")
        else:
            logger.warning("┌═══════════════════════════════════════════════")
            logger.warning("│ ⚠️  [后台压缩] 压缩任务失败或无消息")
            logger.warning(f"│    ├─ 线程池执行耗时: {exec_elapsed:.2f}ms")
            logger.warning(f"│    ├─ 总耗时: {total_elapsed:.2f}ms")
            logger.warning(f"│    └─ {log_event_loop_status()}")
            logger.warning("└═══════════════════════════════════════════════")


class MemoryManager:
    """记忆管理器 — 云枢的记忆系统入口

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
            memory_manager=self,
            interval=ac_cfg.get("interval_seconds", 60)
        )
        if ac_cfg.get("enabled", True):
            self._async_compressor.start_sync()

        # 压缩阈值
        self._token_limit = config.get("token_limit", 4096)
        self._compress_threshold = config.get("compress_threshold", 0.8)

        self._need_compress = False
        logger.info("MemoryManager 初始化完成")

    def _execute_compression(self) -> bool:
        """执行压缩任务的公共方法

        统一的压缩逻辑：加载旧摘要、压缩新消息、合并摘要、保存结果。

        Returns:
            bool: 压缩是否成功执行
        """
        total_start = time.time()
        try:
            logger.warning("┌─────────────────────────────────────────────")
            logger.warning("│ [压缩] 开始执行压缩任务")
            logger.warning(f"│   ├─ 线程ID: {threading.current_thread().ident}")
            logger.warning(f"│   ├─ 线程名称: {threading.current_thread().name}")
            logger.warning(f"│   └─ 是否主线程: {threading.current_thread().name == 'MainThread'}")
            logger.warning("└─────────────────────────────────────────────")

            step_start = time.time()
            logger.warning("├─ [步骤1] 加载旧摘要")
            old_summary = self._storage.load_summary()
            old_version = old_summary[1] if old_summary else 0
            step_elapsed = (time.time() - step_start) * 1000
            logger.warning("│   └─ 旧摘要版本: %d, 耗时: %.2fms", old_version, step_elapsed)

            step_start = time.time()
            logger.warning("├─ [步骤2] 加载最近消息 (limit=100)")
            recent_messages = self._storage.load_recent_messages(limit=100)
            step_elapsed = (time.time() - step_start) * 1000
            logger.warning("│   └─ 加载到消息数: %d, 耗时: %.2fms", len(recent_messages), step_elapsed)

            if not recent_messages:
                logger.warning("│   └─ ⚠️ 无消息需要压缩，跳过")
                return False

            step_start = time.time()
            logger.warning("├─ [步骤3] 调用 LLM 压缩消息")
            summary = self._summarizer.compress(recent_messages)
            step_elapsed = (time.time() - step_start) * 1000
            logger.warning("│   └─ ✓ LLM 返回摘要长度: %d 字符, 耗时: %.2fms", len(summary) if summary else 0, step_elapsed)

            step_start = time.time()
            if old_summary:
                old_text, old_version = old_summary
                logger.warning("├─ [步骤4] 合并旧摘要 (版本 %d)", old_version)
                summary = self._summarizer.merge_summaries(old_text, summary)
                new_version = old_version + 1
            else:
                new_version = 1
                logger.warning("├─ [步骤4] 无旧摘要，创建初始摘要 (版本 1)")
            step_elapsed = (time.time() - step_start) * 1000
            logger.warning("│   └─ 合并/创建完成, 耗时: %.2fms", step_elapsed)

            step_start = time.time()
            logger.warning("├─ [步骤5] 保存摘要 (版本 %d)", new_version)
            self._storage.save_summary(summary, new_version)
            step_elapsed = (time.time() - step_start) * 1000
            logger.warning("│   └─ ✓ 摘要保存成功, 耗时: %.2fms", step_elapsed)

            step_start = time.time()
            logger.warning("├─ [步骤6] 记录黑匣子日志")
            self._black_box.log("memory_compress", {
                "version": new_version,
                "messages_count": len(recent_messages),
                "execution_time_ms": (time.time() - total_start) * 1000
            })
            step_elapsed = (time.time() - step_start) * 1000
            logger.warning("│   └─ ✓ 日志记录完成, 耗时: %.2fms", step_elapsed)

            total_elapsed = (time.time() - total_start) * 1000
            logger.warning("└─────────────────────────────────────────────")
            logger.warning("│ ✓ 压缩完成！版本: %d | 消息数: %d | 总耗时: %.2fms", 
                       new_version, len(recent_messages), total_elapsed)
            logger.warning("└─────────────────────────────────────────────")
            return True
        except Exception as e:
            total_elapsed = (time.time() - total_start) * 1000
            logger.error("└─────────────────────────────────────────────")
            logger.error("│ ✗ 压缩失败: %s", e)
            logger.error(f"│   └─ 失败前耗时: {total_elapsed:.2f}ms")
            logger.error("└─────────────────────────────────────────────")
            logger.error("堆栈跟踪:", exc_info=True)
            return False

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

    def score_and_save_message(self, role: str, content: str) -> str:
        """评分并保存消息

        根据消息内容进行重要性评分（1-10），附带评分一起保存。
        高重要性消息会在后续的记忆压缩和检索中优先保留。

        Args:
            role: 消息角色（user/assistant）
            content: 消息内容

        Returns:
            消息的时间戳 ID
        """
        # 计算重要性分数
        score = self._score_content(role, content)

        message = {
            "role": role,
            "content": content,
            "importance_score": score,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        msg_id = self._storage.save_message(message)

        # 记录评分到黑匣子
        self._black_box.log("message_scored", {
            "role": role,
            "score": score,
            "tokens": self._token_counter.count(content)
        })

        # 高重要性消息（>=7）触发快速压缩检查
        if score >= 7:
            recent = self._storage.load_recent_messages(limit=200)
            total_tokens = self._token_counter.count_messages(recent)
            if self._summarizer.should_compress(total_tokens, self._token_limit,
                                                self._compress_threshold):
                self._need_compress = True
                self._async_compressor.request()

        return msg_id

    def _score_content(self, role: str, content: str) -> int:
        """对消息内容进行重要性评分（1-10）

        启发式评分策略：
        - 长度因子：更长消息更可能重要
        - 内容因子：问题、代码、指令等关键模式
        - 角色因子：用户消息权重略高于助手的常规回复
        """
        if not content or not content.strip():
            return 1

        score = 5  # 基准分

        # 长度因子（最长 2000 字符以上得满分）
        length = len(content)
        if length > 2000:
            score += 2
        elif length > 500:
            score += 1
        elif length < 20:
            score -= 1

        # 用户消息——检测问题、请求、指令
        if role == "user":
            # 包含问号（中文/英文）
            if any(q in content for q in ["？", "?", "吗", "么", "呢", "吧"]):
                score += 1
            # 包含指令性词汇
            if any(kw in content for kw in ["请", "帮", "写", "创建", "修改", "修复",
                                              "分析", "解释", "如何", "为什么", "怎么"]):
                score += 1
            # 包含代码或配置相关
            if any(kw in content for kw in ["代码", "函数", "class", "def ", "import",
                                              "配置", "错误", "报错", "异常", "bug"]):
                score += 1
            # 长/复杂用户消息
            if length > 300:
                score += 1

        # 助手消息——检测技术性回复
        elif role == "assistant":
            # 包含代码块
            if "```" in content:
                score += 1
            # 技术性内容
            if any(kw in content for kw in ["函数", "类", "接口", "方法", "配置",
                                              "方案", "步骤", "修复"]):
                score += 1
            # 较长回复通常更有信息量
            if length > 800:
                score += 1

        # 归一化到 1-10
        return max(1, min(10, score))

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
            logger.warning("┌═══════════════════════════════════════════════")
            logger.warning("│ 🔄 [同步压缩] 检测到压缩需求")
            logger.warning("│    └─ 后台线程状态: %s", "运行中 ✓" if self._async_compressor.is_running() else "未运行 ✗")
            if not self._async_compressor.is_running():
                logger.warning("│    └─ 执行同步压缩...")
                self._execute_compression()
            else:
                logger.warning("│    └─ 等待后台处理")
            # 后台线程已处理或本次同步处理完成，清除标记
            self._need_compress = False
            logger.warning("│ ✅ [同步压缩] 压缩需求标记已清除")
            logger.warning("└═══════════════════════════════════════════════")

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
        """析构时停止后台任务"""
        if hasattr(self, '_async_compressor'):
            self._async_compressor.stop_sync()
