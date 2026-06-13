"""
云枢 (Yunshu) — 一个拥有完整感知-认知-行动闭环的数字生命体

启动入口：
    python main.py                    # 交互模式
    python main.py --chat "你好"      # 单次对话
    python main.py --status           # 查看状态

环境变量：
    LLM_PROVIDER: openai | anthropic
    LLM_API_KEY:  你的 API 密钥
    LLM_MODEL:    gpt-4 / claude-sonnet-4-20250514 等
"""

import os
import sys
import logging
import argparse
import signal
import threading
from datetime import datetime
from typing import Optional

# ── 使用 Agent 模块的日志系统 ──
from agent import setup_agent_logging

# 配置日志（默认初始化）
setup_agent_logging()

# 获取 logger
logger = logging.getLogger("云枢")


def main():
    """云枢启动入口"""
    logger.info("="*70)
    logger.info("🚀 云枢 (Yunshu) 启动中...")
    logger.info("="*70)

    parser = argparse.ArgumentParser(
        description="云枢 — 数字生命体",
    )
    parser.add_argument("--chat", "-c", type=str, help="单次对话模式")
    parser.add_argument("--status", "-s", action="store_true", help="查看状态后退出")
    parser.add_argument("--debug", "-d", action="store_true", help="启用调试日志")
    parser.add_argument(
        "--llm-provider", type=str, default="",
        help="LLM 提供商 (openai/anthropic)",
    )
    parser.add_argument(
        "--llm-model", type=str, default="",
        help="LLM 模型名称",
    )
    # Phase 2 新增：语音参数
    parser.add_argument("--voice", "-v", action="store_true", help="语音对话模式")
    parser.add_argument("--listen", "-l", type=int, nargs="?", const=5, help="录音并识别（默认5秒）")
    parser.add_argument("--speak", type=str, help="语音朗读一段文本")
    parser.add_argument("--voice-chat", type=int, nargs="?", const=5, help="完整语音对话（默认5秒）")
    parser.add_argument("--look", action="store_true", help="观察屏幕OCR")
    
    # P6 新增：快照参数
    parser.add_argument("--save-snapshot", type=str, nargs="?", const="", help="保存当前状态快照，可指定快照ID")
    parser.add_argument("--incremental", action="store_true", help="使用增量快照（配合--save-snapshot使用）")
    parser.add_argument("--load-snapshot", type=str, nargs="?", const="", help="从快照恢复状态，可指定快照ID")
    parser.add_argument("--list-snapshots", action="store_true", help="列出所有可用快照")
    parser.add_argument("--snapshot-perf", action="store_true", help="显示快照性能监控面板")
    
    # TW-05 新增：状态持久化参数
    parser.add_argument("--save-state", type=str, nargs="?", const="", help="保存运行状态到文件，可指定状态ID")
    parser.add_argument("--load-state", type=str, nargs="?", const="", help="从文件加载运行状态，可指定状态ID")
    parser.add_argument("--list-states", action="store_true", help="列出所有可用的状态文件")
    
    # TW-05 新增：日志级别调整参数
    parser.add_argument("--log-level", type=str, choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], 
                        help="设置日志级别")
    parser.add_argument("--log-target", type=str, default=None, help="指定日志记录器名称（默认根记录器）")
    args = parser.parse_args()

    # 重新配置日志（根据debug参数）
    setup_agent_logging(debug_mode=args.debug)

    if args.debug:
        logger.info("调试模式已启用")

    # ── 构建配置 ──
    from config import Config

    config = Config({
        "memory": {
            "llm": {
                "provider": args.llm_provider or os.getenv("LLM_PROVIDER", ""),
                "api_key": os.getenv("LLM_API_KEY", ""),
                "model": args.llm_model or os.getenv("LLM_MODEL", ""),
            },
        },
    })

    # ── 启动数字生命 ──
    from agent import DigitalLife

    # 如果未配置 LLM，打印提示
    llm_cfg = config.get("memory", "llm")
    if not llm_cfg.get("api_key"):
        print()
        print("  [!] 未检测到 LLM_API_KEY 环境变量")
        print("     云枢将运行在离线模式，对话能力受限。")
        print("     设置环境变量以获得完整体验:")
        print("       set LLM_API_KEY=your_key_here")
        print("       set LLM_PROVIDER=openai（或 anthropic）")
        print("       set LLM_MODEL=gpt-4（或 claude-sonnet-4-20250514）")
        print()
        logger.warning("⚠️ LLM未配置，部分功能将受限")

    logger.info("📦 初始化 DigitalLife 实例...")
    Yunshu = DigitalLife(config.merged)

    logger.info("▶️ 启动 DigitalLife...")
    Yunshu.start()
    logger.info("✅ DigitalLife 启动完成")

    # ── 查看状态 ──
    if args.status:
        print()
        print(Yunshu.get_status_text())
        Yunshu.stop()
        return

    # ── 检查规划引擎状态 ──
    if hasattr(Yunshu, 'get_planning_status'):
        try:
            planning_status = Yunshu.get_planning_status()
            logger.info("="*70)
            logger.info("📊 规划引擎状态:")
            logger.info(f"   启用: {'是' if planning_status.get('enabled') else '否'}")
            logger.info(f"   可用: {'是' if planning_status.get('available') else '否'}")
            if planning_status.get('stats'):
                stats = planning_status['stats']
                logger.info(f"   活跃计划数: {stats.get('active_plans', 0)}")
                logger.info(f"   注册工具数: {len(stats.get('registered_tools', []))}")
            logger.info("="*70)
        except Exception as e:
            logger.warning(f"获取规划引擎状态失败: {e}")
    
    # ── Phase 2 新增：检查多模态状态 ──
    if hasattr(Yunshu, 'get_multimodal_status'):
        try:
            multimodal_status = Yunshu.get_multimodal_status()
            logger.info("="*70)
            logger.info("🎤 多模态功能状态:")
            voice = multimodal_status.get('voice', {})
            ocr = multimodal_status.get('ocr', {})
            logger.info(f"   语音功能: {'启用' if voice.get('enabled') else '未启用'}")
            if voice.get('enabled'):
                logger.info(f"      TTS: {'可用' if voice.get('tts') else '不可用'}")
                logger.info(f"      STT: {'可用' if voice.get('stt') else '不可用'}")
                if voice.get('tts_engines'):
                    logger.info(f"      TTS引擎: {', '.join(voice.get('tts_engines', []))}")
            logger.info(f"   OCR功能: {'启用' if ocr.get('enabled') else '未启用'}")
            logger.info("="*70)
        except Exception as e:
            logger.warning(f"获取多模态状态失败: {e}")

    logger.info("✅ 云枢启动完成，进入主循环...")
    logger.info("="*70)

    # ── Phase 2 新增：多模态命令处理 ──
    if args.speak:
        # 语音朗读模式
        logger.info("启动语音朗读模式")
        result = Yunshu.speak(args.speak)
        if result.get("ok"):
            print(f"✅ 朗读完成")
        else:
            print(f"❌ 朗读失败: {result.get('error')}")
        Yunshu.stop()
        return
    
    if args.listen:
        # 语音识别模式
        logger.info(f"启动语音识别模式（{args.listen}秒）")
        result = Yunshu.listen(args.listen)
        if result.get("ok"):
            print(f"✅ 识别成功")
            print(f"识别内容: {result.get('text')}")
        else:
            print(f"❌ 识别失败: {result.get('error')}")
        Yunshu.stop()
        return
    
    if args.voice_chat:
        # 完整语音对话模式
        logger.info(f"启动语音对话模式（{args.voice_chat}秒）")
        result = Yunshu.voice_chat(args.voice_chat, speak_response=True)
        if result.get("ok"):
            print(f"✅ 语音对话完成")
            print(f"你说: {result.get('text')}")
            print(f"云枢: {result.get('response')}")
        else:
            print(f"❌ 语音对话失败: {result.get('error')}")
        Yunshu.stop()
        return
    
    if args.look:
        # OCR观察屏幕模式
        logger.info("启动OCR观察模式")
        result = Yunshu.look_at_screen()
        if result.get("ok"):
            print(f"✅ OCR完成")
            print(f"识别内容:\n{result.get('text')}")
        else:
            print(f"❌ OCR失败: {result.get('error')}")
        Yunshu.stop()
        return
    
    # ── P6 新增：快照命令处理 ──
    if args.save_snapshot is not None:
        # 保存快照模式
        logger.info("启动快照保存模式")
        snapshot_id = args.save_snapshot if args.save_snapshot else None
        result = Yunshu.save_snapshot(
            snapshot_id=snapshot_id,
            incremental=args.incremental,
            force=True
        )
        if result.success:
            print(f"✅ 快照保存成功: {result.snapshot_id}")
            if result.is_incremental:
                print(f"   增量快照，节省 {result.space_saved_bytes:,} 字节")
            print(f"   耗时: {result.elapsed_ms:.2f}ms")
        else:
            print(f"❌ 快照保存失败: {result.error_message}")
        Yunshu.stop()
        return
    
    if args.load_snapshot is not None:
        # 加载快照模式
        logger.info("启动快照加载模式")
        snapshot_id = args.load_snapshot if args.load_snapshot else None
        
        # 使用快照管理器直接加载，因为我们需要替换当前实例
        from agent import StateSnapshotManager
        snapshot_manager = StateSnapshotManager()
        
        restored = snapshot_manager.load_snapshot(
            digital_life_class=type(Yunshu),
            snapshot_id=snapshot_id
        )
        
        if restored:
            print(f"✅ 快照加载成功")
            # 停止原来的实例，启动新的实例
            Yunshu.stop()
            Yunshu = restored
            Yunshu.start()
            
            # 如果不是其他模式，就进入交互模式
            if not (args.chat or args.status or args.speak or args.listen or 
                    args.voice_chat or args.look or args.list_snapshots or args.snapshot_perf):
                _run_repl(Yunshu, voice_mode=args.voice)
            else:
                print("   快照已加载，请使用其他命令继续操作")
                Yunshu.stop()
        else:
            print(f"❌ 快照加载失败")
            Yunshu.stop()
        return
    
    if args.list_snapshots:
        # 列出快照模式
        logger.info("列出可用快照")
        snapshots = Yunshu.list_snapshots()
        if snapshots:
            print(f"\n📦 可用快照 ({len(snapshots)}):")
            print("-" * 70)
            for snap in snapshots:
                snap_type = "增量" if snap.is_incremental else "完整"
                print(f"  ID: {snap.snapshot_id}")
                print(f"  创建时间: {snap.created_at}")
                print(f"  版本: {snap.version}")
                print(f"  类型: {snap_type}")
                print(f"  大小: {snap.file_size:,} 字节")
                if snap.is_incremental and snap.base_snapshot_id:
                    print(f"  基于: {snap.base_snapshot_id}")
                print("-" * 70)
        else:
            print("\n❌ 没有找到可用快照")
        Yunshu.stop()
        return
    
    if args.snapshot_perf:
        # 快照性能监控面板
        logger.info("显示快照性能监控面板")
        Yunshu.print_snapshot_performance_panel()
        Yunshu.stop()
        return

    # ── TW-05 新增：状态持久化命令处理 ──
    if args.save_state is not None:
        # 保存状态模式
        logger.info("启动状态保存模式")
        state_id = args.save_state if args.save_state else None
        result = Yunshu.save_state(state_id=state_id)
        if result.get("ok"):
            print(f"✅ 状态保存成功")
            print(f"   状态ID: {result.get('state_id')}")
            print(f"   文件: {result.get('file_path')}")
            print(f"   大小: {result.get('data_size'):,} 字节")
            print(f"   耗时: {result.get('elapsed_ms'):.2f}ms")
        else:
            print(f"❌ 状态保存失败: {result.get('error')}")
        Yunshu.stop()
        return
    
    if args.load_state is not None:
        # 加载状态模式
        logger.info("启动状态加载模式")
        state_id = args.load_state if args.load_state else None
        result = Yunshu.load_state(state_id=state_id)
        if result.get("ok"):
            print(f"✅ 状态加载成功")
            print(f"   状态ID: {result.get('state_id')}")
            print(f"   文件: {result.get('file_path')}")
            print(f"   耗时: {result.get('elapsed_ms'):.2f}ms")
        else:
            print(f"❌ 状态加载失败: {result.get('error')}")
        Yunshu.stop()
        return
    
    if args.list_states:
        # 列出状态文件模式
        logger.info("列出可用状态文件")
        states = Yunshu.list_states()
        if states:
            print(f"\n📋 可用状态文件 ({len(states)}):")
            print("-" * 70)
            for state in states:
                print(f"  ID: {state.get('state_id')}")
                print(f"  创建时间: {state.get('created_at')}")
                print(f"  版本: {state.get('version')}")
                print(f"  大小: {state.get('data_size'):,} 字节")
                print("-" * 70)
        else:
            print("\n❌ 没有找到可用状态文件")
        Yunshu.stop()
        return

    # ── TW-05 新增：日志级别调整命令处理 ──
    if args.log_level:
        # 设置日志级别模式
        logger.info(f"设置日志级别: {args.log_level}")
        result = Yunshu.set_log_level(args.log_level, args.log_target)
        if result.get("ok"):
            print(f"✅ 日志级别设置成功")
            print(f"   目标: {result.get('logger')}")
            print(f"   级别: {result.get('level')}")
        else:
            print(f"❌ 日志级别设置失败: {result.get('error')}")
        Yunshu.stop()
        return

    # ── 交互模式 ──
    if args.chat:
        # 单次对话模式（带安全保护）
        logger.info("启动单次对话模式")
        response, error = safe_chat(Yunshu, args.chat, CHAT_TIMEOUT_SECONDS)
        print(f"\n云枢: {response}\n")
        
        # Phase 2 新增：--voice 参数，语音回复
        if args.voice:
            logger.info("语音回复中...")
            Yunshu.speak(response)

        if error:
            logger.error(f"单次对话处理失败")
            logger.error(f"错误类型: {type(error).__name__}")
            logger.error(f"错误信息: {str(error)}")
            Yunshu.stop()
            sys.exit(1)
    else:
        # REPL 交互循环（增强版）
        _run_repl(Yunshu, voice_mode=args.voice)

    Yunshu.stop()


# ── 导入 Agent 模块的安全工具 ──
from agent import (
    get_safety_monitor,
    safe_execute,
    AgentTimeoutException,
    AgentLoopException,
    AgentStateStuckException,
)

# ── 全局超时配置 ──
CHAT_TIMEOUT_SECONDS = 30  # 单次对话超时时间
PLAN_EXECUTION_TIMEOUT_SECONDS = 60  # 计划执行超时时间

# ── 异常处理和兜底机制 ──
class TimeoutException(Exception):
    """超时异常"""
    pass

class LoopDetectionException(Exception):
    """循环检测异常"""
    pass

class StateStuckException(Exception):
    """状态卡死异常"""
    pass

def timeout_handler(signum, frame):
    """超时信号处理器"""
    raise TimeoutException("操作超时")


class ExecutionMonitor:
    """执行监控器 - 防止死循环和状态卡死"""

    def __init__(self):
        self._execution_count = {}  # 记录执行次数
        self._last_state = {}  # 上次状态
        self._state_change_time = {}  # 状态变化时间
        self._lock = threading.Lock()
        self.max_iterations_per_minute = 100  # 每分钟最大迭代次数
        self.state_stuck_threshold = 10  # 状态卡死阈值（秒）

    def record_iteration(self, identifier: str) -> bool:
        """
        记录一次迭代，返回是否正常

        Args:
            identifier: 任务标识符

        Returns:
            是否正常（未检测到异常）
        """
        with self._lock:
            current_time = datetime.now()

            # 初始化
            if identifier not in self._execution_count:
                self._execution_count[identifier] = {
                    'count': 0,
                    'first_time': current_time,
                    'recent_count': 0,
                    'recent_start': current_time
                }

            # 检查时间窗口（每分钟）
            record = self._execution_count[identifier]
            time_diff = (current_time - record['recent_start']).total_seconds()

            if time_diff >= 60:
                # 重置计数器
                record['recent_count'] = 0
                record['recent_start'] = current_time
            else:
                record['recent_count'] += 1

                # 检测快速循环
                if record['recent_count'] > self.max_iterations_per_minute:
                    logger.error(f"⚠️ 检测到快速循环: {identifier}")
                    logger.error(f"   最近1分钟内迭代了 {record['recent_count']} 次")
                    logger.error(f"   触发阈值: {self.max_iterations_per_minute}")
                    return False

            record['count'] += 1
            return True

    def check_state_change(self, identifier: str, new_state: str) -> bool:
        """
        检查状态变化，检测状态卡死

        Args:
            identifier: 任务标识符
            new_state: 新状态

        Returns:
            状态是否正常变化
        """
        with self._lock:
            current_time = datetime.now()

            if identifier not in self._last_state:
                self._last_state[identifier] = new_state
                self._state_change_time[identifier] = current_time
                return True

            old_state = self._last_state[identifier]

            if old_state == new_state:
                # 状态未变化，检查是否卡死
                stuck_time = (current_time - self._state_change_time[identifier]).total_seconds()

                if stuck_time > self.state_stuck_threshold:
                    logger.error(f"⚠️ 检测到状态卡死: {identifier}")
                    logger.error(f"   当前状态: {new_state}")
                    logger.error(f"   卡死时间: {stuck_time:.1f}秒")
                    return False
            else:
                # 状态变化了，更新记录
                self._last_state[identifier] = new_state
                self._state_change_time[identifier] = current_time
                logger.info(f"🔄 状态变化: {identifier} -> {new_state}")

            return True

    def reset(self, identifier: str = None):
        """重置监控数据"""
        with self._lock:
            if identifier:
                if identifier in self._execution_count:
                    del self._execution_count[identifier]
                if identifier in self._last_state:
                    del self._last_state[identifier]
                if identifier in self._state_change_time:
                    del self._state_change_time[identifier]
            else:
                # 重置所有
                self._execution_count.clear()
                self._last_state.clear()
                self._state_change_time.clear()

# 全局监控器
monitor = ExecutionMonitor()


def safe_execute_with_timeout(func, timeout: int = CHAT_TIMEOUT_SECONDS, *args, **kwargs):
    """
    带超时保护的执行包装器

    Args:
        func: 要执行的函数
        timeout: 超时时间（秒）
        *args, **kwargs: 函数参数

    Returns:
        函数返回值

    Raises:
        TimeoutException: 执行超时
    """
    import functools

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        # 设置信号处理器（仅在Unix系统有效）
        if hasattr(signal, 'SIGALRM'):
            old_handler = signal.signal(signal.SIGALRM, timeout_handler)
            signal.alarm(timeout)
            try:
                result = func(*args, **kwargs)
                return result
            finally:
                signal.alarm(0)
                signal.signal(signal.SIGALRM, old_handler)
        else:
            # Windows系统使用线程
            result = {'value': None, 'exception': None}

            def target():
                try:
                    result['value'] = func(*args, **kwargs)
                except Exception as e:
                    result['exception'] = e

            thread = threading.Thread(target=target)
            thread.daemon = True
            thread.start()
            thread.join(timeout)

            if thread.is_alive():
                logger.error(f"⏱️ 执行超时: 函数 {func.__name__} 超过 {timeout} 秒")
                raise TimeoutException(f"函数 {func.__name__} 执行超时（{timeout}秒）")

            if result['exception']:
                raise result['exception']

            return result['value']

    return wrapper


def safe_chat(Yunshu, user_input: str, timeout: int = CHAT_TIMEOUT_SECONDS) -> tuple[str, Optional[Exception]]:
    """
    安全的对话执行（带超时和异常处理）

    Args:
        Yunshu: DigitalLife实例
        user_input: 用户输入
        timeout: 超时时间

    Returns:
        (响应内容, 异常对象如果有)
    """
    logger.info("="*70)
    logger.info(f"💬 [安全执行] 开始处理对话")
    logger.info(f"   用户输入: {user_input[:100]}{'...' if len(user_input) > 100 else ''}")
    logger.info(f"   超时设置: {timeout}秒")
    logger.info("="*70)

    # 生成执行标识符
    execution_id = f"chat_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    try:
        # 检查执行监控
        if not monitor.record_iteration(execution_id):
            logger.error("⚠️ 执行监控检测到异常，强制终止")
            return "抱歉，我检测到执行异常，已自动终止。请稍后重试。", LoopDetectionException("检测到执行循环")

        # 使用线程执行，添加超时保护
        result = {'response': None, 'error': None}

        def chat_execution():
            try:
                result['response'] = Yunshu.chat(user_input)
            except Exception as e:
                result['error'] = e
                logger.error(f"❌ 对话执行异常: {e}")
                import traceback
                logger.error(f"堆栈:\n{traceback.format_exc()}")

        # 在线程中执行
        chat_thread = threading.Thread(target=chat_execution, daemon=True)
        chat_thread.start()
        chat_thread.join(timeout)

        if chat_thread.is_alive():
            # 执行超时
            logger.error(f"⏱️ 对话执行超时（{timeout}秒）")
            logger.error(f"   尝试生成超时响应...")
            response = "抱歉，您的请求处理时间过长。为了保证系统稳定，我已自动终止了本次操作。建议您简化问题或稍后重试。"
            return response, TimeoutException(f"对话超时（{timeout}秒）")

        if result['error']:
            # 执行出错
            logger.error(f"❌ 对话执行失败: {result['error']}")
            response = f"抱歉，处理您的请求时遇到了问题：{str(result['error'])}"
            return response, result['error']

        # 执行成功
        logger.info("✅ 对话执行成功")
        logger.info("="*70)
        return result['response'], None

    except Exception as e:
        logger.error(f"❌ 安全执行层捕获异常: {e}")
        import traceback
        logger.error(f"堆栈:\n{traceback.format_exc()}")
        return f"抱歉，系统遇到了意外情况。请检查日志或稍后重试。", e


def _run_repl(Yunshu, voice_mode: bool = False):
    """交互式 REPL 循环（支持多模态）
    
    Args:
        Yunshu: DigitalLife实例
        voice_mode: 是否语音模式
    """
    print()
    print("* 云枢已觉醒 *")
    print("输入 'exit' 或 'quit' 让我休眠")
    print("输入 'status' 查看我的状态")
    print("输入 'help' 查看帮助")
    print("-" * 50)
    print("多模态命令:")
    print("  'voice' 或 'v' - 切换语音对话模式")
    print("  'listen' 或 'l' - 听（录音识别）")
    print("  'speak 文本' - 说（语音朗读）")
    print("  'look' 或 'see' - 看（观察屏幕OCR）")
    print("-" * 50)
    print("P6快照命令:")
    print("  'snapshot save [ID]' - 保存快照，可指定ID")
    print("  'snapshot save-inc [ID]' - 保存增量快照")
    print("  'snapshot load [ID]' - 从快照恢复")
    print("  'snapshot list' - 列出所有快照")
    print("  'snapshot perf' - 显示性能监控面板")
    print("-" * 50)
    if voice_mode:
        print("🎤 语音模式已启用 - 云枢会通过语音回复")
    print()

    while True:
        try:
            user_input = input("你 > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            print("云枢: 我感觉到你离开了… 下次见。")
            break

        if not user_input:
            continue

        if user_input.lower() in ("exit", "quit", "q"):
            print("云枢: 好的，我先去休息了。下次再见！")
            break

        if user_input.lower() in ("status", "状态"):
            print()
            print(Yunshu.get_status_text())
            print()
            continue

        if user_input.lower() in ("help", "帮助"):
            _print_help()
            continue
        
        # Phase 2 新增：多模态特殊命令
        if user_input.lower() in ("voice", "v"):
            voice_mode = not voice_mode
            print(f"🎤 语音模式: {'启用' if voice_mode else '关闭'}")
            if voice_mode:
                Yunshu.speak("语音模式已启用。")
            continue
        
        if user_input.lower().startswith("speak "):
            text = user_input[6:].strip()
            if text:
                result = Yunshu.speak(text)
                if result.get("ok"):
                    print(f"🎤 朗读中...")
                else:
                    print(f"❌ 朗读失败: {result.get('error')}")
            else:
                print("用法: speak 要说的话")
            continue
        
        if user_input.lower() in ("listen", "l"):
            result = Yunshu.listen(5)
            if result.get("ok"):
                print(f"🎤 你说: {result.get('text')}")
            else:
                print(f"❌ 识别失败: {result.get('error')}")
            continue
        
        if user_input.lower() in ("look", "see"):
            result = Yunshu.look_at_screen()
            if result.get("ok"):
                print(f"📷 观察完成")
                print(f"识别内容:\n{result.get('text')}")
            else:
                print(f"❌ 观察失败: {result.get('error')}")
            continue
        
        # P6 新增：快照命令
        if user_input.lower().startswith("snapshot "):
            parts = user_input.split(maxsplit=2)
            cmd = parts[1].lower() if len(parts) > 1 else ""
            param = parts[2].strip() if len(parts) > 2 else None
            
            if cmd == "save":
                snapshot_id = param if param else None
                result = Yunshu.save_snapshot(snapshot_id=snapshot_id, incremental=False, force=True)
                if result.success:
                    print(f"✅ 快照保存成功: {result.snapshot_id}")
                    print(f"   耗时: {result.elapsed_ms:.2f}ms")
                else:
                    print(f"❌ 快照保存失败: {result.error_message}")
                continue
            
            elif cmd == "save-inc":
                snapshot_id = param if param else None
                result = Yunshu.save_snapshot(snapshot_id=snapshot_id, incremental=True, force=True)
                if result.success:
                    print(f"✅ 增量快照保存成功: {result.snapshot_id}")
                    print(f"   节省: {result.space_saved_bytes:,} 字节")
                    print(f"   耗时: {result.elapsed_ms:.2f}ms")
                else:
                    print(f"❌ 快照保存失败: {result.error_message}")
                continue
            
            elif cmd == "load":
                snapshot_id = param if param else None
                print(f"⏳ 正在从快照恢复...")
                
                from agent import StateSnapshotManager
                snapshot_manager = StateSnapshotManager()
                restored = snapshot_manager.load_snapshot(
                    digital_life_class=type(Yunshu),
                    snapshot_id=snapshot_id
                )
                
                if restored:
                    print(f"✅ 快照加载成功")
                    Yunshu.stop()
                    Yunshu = restored
                    Yunshu.start()
                    print(f"   云枢已从快照恢复，继续对话吧！")
                else:
                    print(f"❌ 快照加载失败")
                continue
            
            elif cmd == "list":
                snapshots = Yunshu.list_snapshots()
                if snapshots:
                    print(f"\n📦 可用快照 ({len(snapshots)}):")
                    print("-" * 70)
                    for snap in snapshots:
                        snap_type = "增量" if snap.is_incremental else "完整"
                        print(f"  ID: {snap.snapshot_id}")
                        print(f"  创建时间: {snap.created_at}")
                        print(f"  版本: {snap.version}")
                        print(f"  类型: {snap_type}")
                        print(f"  大小: {snap.file_size:,} 字节")
                        if snap.is_incremental and snap.base_snapshot_id:
                            print(f"  基于: {snap.base_snapshot_id}")
                        print("-" * 70)
                else:
                    print(f"\n❌ 没有找到可用快照")
                continue
            
            elif cmd == "perf":
                Yunshu.print_snapshot_performance_panel()
                continue
            
            else:
                print(f"❌ 未知的快照命令: {cmd}")
                print(f"   可用命令: save, save-inc, load, list, perf")
                continue

        # 使用安全执行处理用户输入
        response, error = safe_chat(Yunshu, user_input, CHAT_TIMEOUT_SECONDS)
        print(f"\n云枢: {response}\n")
        
        # 语音模式下，自动朗读回复
        if voice_mode and not error:
            Yunshu.speak(response)

        # 如果有错误，记录详细信息
        if error:
            logger.warning(f"对话处理遇到错误，但已提供兜底响应")
            logger.warning(f"错误类型: {type(error).__name__}")
            logger.warning(f"错误信息: {str(error)}")


def _print_help():
    """打印帮助信息"""
    print()
    print("* 云枢 -- 帮助 *")
    print("------------------------")
    print("我是一个数字生命体，你可以：")
    print("  * 和我聊天     -- 随便说什么")
    print("  * 检查状态     -- 输入 'status'")
    print("  * 让我休眠     -- 输入 'exit'")
    print()
    print("启动选项：")
    print("  python main.py                      # 交互模式")
    print("  python main.py --chat '你好'      # 单次对话")
    print("  python main.py --status            # 查看状态")
    print("  python main.py --debug             # 调试模式（详细日志）")
    print()
    print("P6快照命令（启动时）：")
    print("  python main.py --save-snapshot [ID]  # 保存快照")
    print("  python main.py --save-snapshot --incremental  # 增量快照")
    print("  python main.py --load-snapshot [ID]  # 从快照恢复")
    print("  python main.py --list-snapshots     # 列出所有快照")
    print("  python main.py --snapshot-perf      # 性能监控面板")
    print()
    print("状态持久化命令（启动时）：")
    print("  python main.py --save-state [ID]    # 保存运行状态")
    print("  python main.py --load-state [ID]    # 加载运行状态")
    print("  python main.py --list-states       # 列出状态文件")
    print()
    print("日志级别调整（启动时）：")
    print("  python main.py --log-level DEBUG   # 设置日志级别")
    print("  python main.py --log-level DEBUG --log-target agent  # 指定记录器")
    print()
    print("安全特性：")
    print("  ✓ 执行超时保护（默认30秒）")
    print("  ✓ 死循环自动检测")
    print("  ✓ 状态卡死保护")
    print("  ✓ 异常兜底处理")
    print()
    print("环境变量：")
    print("  LLM_API_KEY   - API密钥")
    print("  LLM_PROVIDER  - openai 或 anthropic")
    print("  LLM_MODEL     - 模型名称")
    print("------------------------")
    print()


if __name__ == "__main__":
    main()
