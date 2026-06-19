#!/usr/bin/env python3
"""
将监控模块集成到 DigitalLife
"""

def integrate_monitoring():
    """集成监控模块到 DigitalLife"""
    
    file_path = "agent/digital_life.py"
    
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    print("STARTING: Integrating monitoring module into DigitalLife...")
    
    # 1. 添加监控模块导入
    print("\n1. Adding monitoring module imports...")
    
    # 找到导入部分，在向量记忆导入后添加监控模块
    old_import = '''# ── 新增：向量记忆模块导入 ──
try:
    from memory import VectorStore, KnowledgeBase
    _MEMORY_AVAILABLE = True
    logger.info("[ok] 向量记忆模块已加载")
except ImportError as e:
    logger.warning(f"向量记忆模块导入失败: {e}")
    _MEMORY_AVAILABLE = False'''

    new_import = '''# ── 新增：向量记忆模块导入 ──
try:
    from memory import VectorStore, KnowledgeBase
    _MEMORY_AVAILABLE = True
    logger.info("[ok] 向量记忆模块已加载")
except ImportError as e:
    logger.warning(f"向量记忆模块导入失败: {e}")
    _MEMORY_AVAILABLE = False

# ── 新增：性能监控模块导入 ──
try:
    from agent.monitoring import (
        TraceContext, 
        get_metrics_collector,
        get_trace_id
    )
    _MONITORING_AVAILABLE = True
    logger.info("[ok] 性能监控模块已加载")
except ImportError as e:
    logger.warning(f"性能监控模块导入失败: {e}")
    _MONITORING_AVAILABLE = False'''

    if old_import in content:
        content = content.replace(old_import, new_import)
        print("   [OK] Monitoring imports added")
    else:
        print("   [SKIP] Imports already exist or pattern not found")
    
    # 2. 修改 chat 方法，添加追踪和指标收集
    print("\n2. Modifying chat method...")
    
    old_chat = '''    def chat(self, user_input: str) -> str:
        """与云枢对话——完整的感知-认知-行动闭环

        这是与云枢交互的唯一入口。
        每次对话都经历：感知身体 → 智能判断(是否规划) → 执行 → 反思记录

        Args:
            user_input: 用户说给云枢的话

        Returns:
            云枢的回复
        """
        logger.info("=" * 70)
        logger.info("💬 [DigitalLife.chat] 收到对话请求")
        logger.info(f"   用户输入: {user_input[:100]}{'...' if len(user_input) > 100 else ''}")
        logger.info(f"   对话次数: {self._interaction_count + 1}")
        logger.info("=" * 70)

        if not self._running:
            logger.warning("云枢未运行，返回提示")
            return "我还没有被唤醒。请先调用 start() 让我醒来。"

        self._interaction_count += 1

        # ── 新增：判断是否启用规划模式 ──
        if self._planning_enabled and self._planner and self._needs_planning(user_input):
            logger.info("🔍 复杂度评估: 启用规划模式")
            return self._chat_with_planning(user_input)

        # 降级为原有的简单模式
        logger.info("🔍 复杂度评估: 直接模式")
        logger.info("🔍 执行流程: 感知 → 认知 → 行动 → 反思")

        try:
            result = self._process_user_input(user_input)
            logger.info("✅ 对话处理完成")
            return result
        except Exception as e:
            logger.error(f"❌ 对话处理异常: {e}")
            import traceback
            logger.error(f"堆栈:\n{traceback.format_exc()}")
            return f"抱歉，处理您的请求时遇到了问题：{str(e)}"'''

    new_chat = '''    def chat(self, user_input: str) -> str:
        """与云枢对话——完整的感知-认知-行动闭环

        这是与云枢交互的唯一入口。
        每次对话都经历：感知身体 → 智能判断(是否规划) → 执行 → 反思记录

        Args:
            user_input: 用户说给云枢的话

        Returns:
            云枢的回复
        """
        # ── 性能监控：追踪上下文 ──
        if _MONITORING_AVAILABLE:
            collector = get_metrics_collector()
        
        with TraceContext("DigitalLife", "chat") as ctx:
            logger.info("=" * 70)
            logger.info(f"[{get_trace_id()}] 💬 [DigitalLife.chat] 收到对话请求")
            logger.info(f"   用户输入: {user_input[:100]}{'...' if len(user_input) > 100 else ''}")
            logger.info(f"   对话次数: {self._interaction_count + 1}")
            logger.info("=" * 70)

            if not self._running:
                logger.warning("云枢未运行，返回提示")
                return "我还没有被唤醒。请先调用 start() 让我醒来。"

            self._interaction_count += 1
            
            # ── 性能监控：计数器 ──
            if _MONITORING_AVAILABLE:
                collector.increment_counter("count.digital_life.chat.total")
                collector.increment_counter("count.digital_life.interaction.total")

            # ── 新增：判断是否启用规划模式 ──
            if self._planning_enabled and self._planner and self._needs_planning(user_input):
                logger.info("[{0}] 🔍 复杂度评估: 启用规划模式".format(get_trace_id()))
                
                # ── 性能监控：规划模式计数 ──
                if _MONITORING_AVAILABLE:
                    collector.increment_counter("count.digital_life.chat.planning_mode")
                
                return self._chat_with_planning(user_input)

            # 降级为原有的简单模式
            logger.info("🔍 复杂度评估: 直接模式")
            logger.info("🔍 执行流程: 感知 → 认知 → 行动 → 反思")

            try:
                result = self._process_user_input(user_input)
                logger.info("✅ 对话处理完成")
                
                # ── 性能监控：成功计数 ──
                if _MONITORING_AVAILABLE:
                    collector.increment_counter("count.digital_life.chat.success")
                
                return result
            except Exception as e:
                logger.error(f"❌ 对话处理异常: {e}")
                import traceback
                logger.error(f"堆栈:\n{traceback.format_exc()}")
                
                # ── 性能监控：错误计数 ──
                if _MONITORING_AVAILABLE:
                    collector.increment_counter("count.digital_life.chat.error")
                    collector.increment_counter("count.digital_life.error.total")
                
                return f"抱歉，处理您的请求时遇到了问题：{str(e)}"'''

    if old_chat in content:
        content = content.replace(old_chat, new_chat)
        print("   [OK] chat method modified with monitoring")
    else:
        print("   [SKIP] chat method already modified or pattern not found")
    
    # 3. 在 _process_user_input 方法中添加追踪
    print("\n3. Modifying _process_user_input method...")
    
    # 找到 _process_user_input 方法的起始
    old_process = '''    def _process_user_input(self, user_input: str) -> str:
        """处理用户输入——完整的认知流程"""
        
        logger.info("开始处理用户输入...")'''

    new_process = '''    def _process_user_input(self, user_input: str) -> str:
        """处理用户输入——完整的认知流程"""
        
        if _MONITORING_AVAILABLE:
            with TraceContext("DigitalLife", "process_user_input") as ctx:
                return self._process_user_input_impl(user_input)
        else:
            return self._process_user_input_impl(user_input)
    
    def _process_user_input_impl(self, user_input: str) -> str:
        """处理用户输入——完整实现"""
        
        logger.info("开始处理用户输入...")'''

    if old_process in content:
        content = content.replace(old_process, new_process)
        print("   [OK] _process_user_input method wrapped with tracing")
    else:
        print("   [SKIP] _process_user_input already modified or pattern not found")
    
    # 4. 保存文件
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)
    
    print("\n" + "="*70)
    print("SUCCESS: Monitoring integration complete!")
    print("="*70)
    print("\nChanges made:")
    print("1. Added monitoring module imports")
    print("2. Wrapped chat() method with TraceContext")
    print("3. Added performance counters (chat total, success, error)")
    print("4. Wrapped _process_user_input() with tracing")
    print("\nNext steps:")
    print("1. Run: python test_integration.py")
    print("2. Check logs for Trace ID in output")
    print("3. View metrics with: collector.get_all_metrics()")

if __name__ == "__main__":
    integrate_monitoring()
