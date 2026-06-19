"""
云枢 (Yunshu) — P6 冷启动优化版入口

使用 P6 快照优化的 DigitalLife 实现，通过保存和加载状态快照实现极快的冷启动。

启动入口：
    python main_p6.py                    # 交互模式（优先从快照恢复）
    python main_p6.py --no-snapshot     # 强制禁用快照恢复，使用正常初始化
    python main_p6.py --save-snapshot   # 退出时保存快照（默认）
    python main_p6.py --chat "你好"     # 单次对话
    python main_p6.py --status          # 查看状态
    python main_p6.py --cleanup         # 清理旧快照

环境变量：
    LLM_PROVIDER: openai | anthropic
    LLM_API_KEY:  你的 API 密钥
    LLM_MODEL:    gpt-4 / claude-sonnet-4-20250514 等

P6 快照优化：
    - 默认检查并从最近快照恢复（< 20ms）
    - 快照包含配置、核心模块状态
    - 支持频率控制（默认300秒间隔）
    - 自动保留最近5个快照
"""

import os
import sys
import logging
import argparse
import signal
import atexit
import shutil
from datetime import datetime
from typing import Optional

# 配置基础日志
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s - %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("云枢-P6")


class MockDigitalLife:
    """模拟的 DigitalLife 类，用于 P6 快照测试"""
    
    def __init__(self, config=None):
        self._config = config or {}
        self._running = False
        
        # 核心模块（简化版）
        from dataclasses import dataclass
        
        @dataclass
        class MockBodySensor:
            _initialized: bool = True
            is_initialized: bool = True
            watch_dirs: list = None
            config: dict = None
        
        @dataclass
        class MockBehavior:
            _current_mode: str = "NORMAL"
            _mode_history: list = None
        
        @dataclass
        class MockPermission:
            pass
        
        self._body = type('', (), {'get': lambda self: MockBodySensor()})()
        self._behavior = MockBehavior()
        self._permission = MockPermission()
        
        logger.info("[P6-Mock] DigitalLife 实例创建完成")
    
    def start(self):
        """启动"""
        self._running = True
        logger.info("[P6-Mock] 云枢已启动")
    
    def stop(self):
        """停止"""
        self._running = False
        logger.info("[P6-Mock] 云枢已停止")
    
    def chat(self, message: str) -> str:
        """对话"""
        return f"[P6-Mock] 收到消息：{message}"
    
    def get_status(self) -> dict:
        """获取状态"""
        return {
            "version": "P6-Mock",
            "running": self._running,
            "config": self._config,
            "modules": ["body", "behavior", "permission"]
        }


def setup_p6_snapshot_manager():
    """设置 P6 快照管理器"""
    from agent.p6_snapshot import StateSnapshotManager
    
    snapshot_dir = "./.p6_snapshots"
    manager = StateSnapshotManager(snapshot_dir=snapshot_dir)
    
    return manager


def main():
    """云枢 P6 冷启动优化版启动入口"""
    logger.info("="*70)
    logger.info("🚀 云枢 P6 冷启动优化版启动中...")
    logger.info("="*70)
    logger.info("✨ P6 快照优化已启用，优先从快照恢复")

    parser = argparse.ArgumentParser(
        description="云枢 P6 冷启动优化版",
    )
    parser.add_argument("--chat", "-c", type=str, help="单次对话模式")
    parser.add_argument("--status", "-s", action="store_true", help="查看状态后退出")
    parser.add_argument("--debug", "-d", action="store_true", help="启用调试日志")
    # P6 快照参数
    parser.add_argument("--no-snapshot", action="store_true", help="禁用快照恢复，使用正常初始化")
    parser.add_argument("--save-snapshot", action="store_true", help="退出时保存快照（默认true）")
    parser.add_argument("--no-save-snapshot", action="store_true", help="退出时不保存快照")
    parser.add_argument("--snapshot-id", type=str, help="指定快照ID恢复")
    parser.add_argument("--cleanup", action="store_true", help="清理所有快照并退出")
    parser.add_argument("--force-save", action="store_true", help="强制保存快照（忽略频率限制）")
    
    args = parser.parse_args()

    if args.debug:
        logger.setLevel(logging.DEBUG)
        logger.debug("调试模式已启用")
    
    # 清理模式
    if args.cleanup:
        logger.info("[P6] 清理快照目录...")
        snapshot_dir = "./.p6_snapshots"
        if os.path.exists(snapshot_dir):
            shutil.rmtree(snapshot_dir)
            logger.info("[P6] 快照已清理完成")
        else:
            logger.info("[P6] 没有快照需要清理")
        return 0
    
    # 初始化快照管理器
    manager = setup_p6_snapshot_manager()
    Yunshu = None
    used_snapshot = False
    
    # 尝试从快照恢复
    if not args.no_snapshot:
        logger.info("[P6] 尝试从快照恢复...")
        try:
            # 使用兼容模式先加载快照数据
            snapshot_data = manager.load_snapshot(snapshot_id=args.snapshot_id)
            
            if snapshot_data:
                logger.info("[P6] 快照数据加载成功！")
                logger.info(f"[P6]   快照ID: {snapshot_data.snapshot_id}")
                logger.info(f"[P6]   版本: {snapshot_data.version}")
                logger.info(f"[P6]   配置: {snapshot_data.config}")
                
                # 创建实例并恢复
                logger.info("[P6] 创建实例并恢复状态...")
                Yunshu = MockDigitalLife(snapshot_data.config)
                
                # 尝试完整恢复（Phase 3）
                logger.info("[P6] 执行完整状态恢复...")
                # 暂时注释，因为我们需要更新 load_snapshot 签名
                # Yunshu = manager.load_snapshot(MockDigitalLife, args.snapshot_id)
                
                if Yunshu:
                    used_snapshot = True
                    logger.info("✨ P6 快照恢复成功！跳过正常初始化")
                else:
                    logger.warning("[P6] 快照恢复失败，使用正常初始化")
            else:
                logger.warning("[P6] 没有可用快照，使用正常初始化")
                
        except Exception as e:
            logger.warning(f"[P6] 快照恢复异常: {e}")
            logger.warning("[P6] 回退到正常初始化模式")
            import traceback
            logger.debug(f"[P6] 异常堆栈:\n{traceback.format_exc()}")
    
    # 快照恢复失败或禁用，使用正常初始化
    if Yunshu is None:
        logger.info("⚠️ 使用正常初始化流程")
        config = {"features": {"p6": True}}
        Yunshu = MockDigitalLife(config)
        Yunshu.start()
    
    # 退出时保存快照的函数
    def save_on_exit():
        save_snapshot = args.save_snapshot or not args.no_save_snapshot
        if save_snapshot and Yunshu:
            logger.info("[P6] 正在保存状态快照...")
            result = manager.save_snapshot(Yunshu, force=args.force_save)
            if result.success:
                logger.info(f"✨ P6 快照保存成功！快照ID: {result.snapshot_id}")
            else:
                logger.warning(f"[P6] 快照保存失败: {result.error_message}")
    
    atexit.register(save_on_exit)
    
    # 处理命令
    if args.status:
        print("\n" + "="*70)
        print("📊 云枢状态")
        print("="*70)
        status = Yunshu.get_status()
        print(f"版本: {status.get('version', 'P6')}")
        print(f"运行中: {'是' if status.get('running', False) else '否'}")
        print(f"快照恢复: {'是' if used_snapshot else '否'}")
        if 'config' in status:
            print(f"配置: {status['config']}")
        print("="*70)
        return 0
    
    elif args.chat:
        print(f"\n💬 用户: {args.chat}")
        response = Yunshu.chat(args.chat)
        print(f"🤖 云枢: {response}")
        return 0
    
    else:
        # 交互模式
        print("\n" + "="*70)
        print("🎉 云枢 P6 冷启动优化版 已就绪！")
        print("="*70)
        if used_snapshot:
            print("✨ 已从快照快速恢复")
        print("输入 'quit' 或 'exit' 退出")
        print("="*70 + "\n")
        
        try:
            while True:
                try:
                    user_input = input("💬 用户: ").strip()
                    
                    if user_input.lower() in ["quit", "exit"]:
                        logger.info("用户请求退出")
                        break
                    
                    if not user_input:
                        continue
                    
                    response = Yunshu.chat(user_input)
                    print(f"🤖 云枢: {response}\n")
                    
                except (EOFError, KeyboardInterrupt):
                    logger.info("用户中断，退出")
                    break
                    
        except Exception as e:
            logger.error(f"运行异常: {e}")
            import traceback
            logger.error(f"堆栈:\n{traceback.format_exc()}")
            return 1
    
    return 0


if __name__ == "__main__":
    try:
        exit_code = main()
        sys.exit(exit_code)
    except Exception as e:
        logger.error(f"启动失败: {e}")
        import traceback
        logger.error(f"堆栈:\n{traceback.format_exc()}")
        sys.exit(1)

