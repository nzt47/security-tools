#!/usr/bin/env python3
"""
精确修改 DigitalLife 的 chat 方法
"""

def modify_chat_method():
    """修改 chat 方法添加监控"""
    
    file_path = "agent/digital_life.py"
    
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    print("MODIFYING: chat method with monitoring...")
    
    # 找到 chat 方法的开始和结束
    start_marker = "    def chat(self, user_input: str) -> str:"
    end_marker = "    def _needs_planning(self, message: str) -> bool:"
    
    start_idx = content.find(start_marker)
    end_idx = content.find(end_marker)
    
    if start_idx == -1 or end_idx == -1:
        print("ERROR: Could not find chat method boundaries")
        return False
    
    # 提取旧的 chat 方法
    old_chat = content[start_idx:end_idx]
    
    print(f"Found chat method, length: {len(old_chat)} chars")
    
    # 替换为新的 chat 方法
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
        with TraceContext("DigitalLife", "chat") as ctx:
            logger.info("=" * 70)
            trace_id = get_trace_id()
            logger.info(f"[{trace_id}] 💬 [DigitalLife.chat] 收到对话请求")
            logger.info(f"   用户输入: {user_input[:100]}{'...' if len(user_input) > 100 else ''}")
            logger.info(f"   对话次数: {self._interaction_count + 1}")
            logger.info("=" * 70)

            if not self._running:
                logger.warning("云枢未运行，返回提示")
                return "我还没有被唤醒。请先调用 start() 让我醒来。"

            self._interaction_count += 1
            
            # ── 性能监控：计数器 ──
            collector = get_metrics_collector()
            collector.increment_counter("count.digital_life.chat.total")
            collector.increment_counter("count.digital_life.interaction.total")

            # ── 新增：判断是否启用规划模式 ──
            if self._planning_enabled and self._planner and self._needs_planning(user_input):
                logger.info(f"[{trace_id}] 🔍 复杂度评估: 启用规划模式")
                collector.increment_counter("count.digital_life.chat.planning_mode")
                return self._chat_with_planning(user_input)

            # 降级为原有的简单模式
            logger.info("🔍 复杂度评估: 直接模式")
            logger.info("🔍 执行流程: 感知 → 认知 → 行动 → 反思")

            try:
                result = self._process_user_input(user_input)
                logger.info("✅ 对话处理完成")
                
                # ── 性能监控：成功计数 ──
                collector.increment_counter("count.digital_life.chat.success")
                
                return result
            except Exception as e:
                logger.error(f"❌ 对话处理异常: {e}")
                import traceback
                logger.error(f"堆栈:\\n{traceback.format_exc()}")
                
                # ── 性能监控：错误计数 ──
                collector.increment_counter("count.digital_life.chat.error")
                collector.increment_counter("count.digital_life.error.total")
                
                return f"抱歉，处理您的请求时遇到了问题：{str(e)}"

'''
    
    # 替换
    content = content[:start_idx] + new_chat + content[end_idx:]
    
    # 保存
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)
    
    print("SUCCESS: chat method modified")
    return True

if __name__ == "__main__":
    success = modify_chat_method()
    if success:
        print("\nChat method is now monitored with:")
        print("- TraceContext for request tracing")
        print("- Performance counters (total, success, error)")
        print("- Trace ID in all log messages")
    else:
        print("\nFailed to modify chat method")
