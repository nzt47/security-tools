"""性能日志集成指南

本模块说明了如何将 performance_logging.py 集成到 digital_life.py 中

使用方法：
1. 在 digital_life.py 顶部添加导入：
   ```python
   from .performance_logging import llm_cache, async_save_monitor, perf_logger
   ```

2. 在调用 LLM 前添加缓存检查：
   ```python
   # 检查缓存
   cached_response = llm_cache.get(user_input)
   if cached_response:
       return cached_response
   ```

3. 在调用 LLM 后添加缓存保存：
   ```python
   # 保存到缓存
   llm_cache.put(user_input, response)
   ```

4. 在异步保存时添加监控：
   ```python
   # 开始异步保存
   save_id = async_save_monitor.record_save_start('memory', 'user_input')
   threading.Thread(target=save_task, daemon=True).start()
   # 在 save_task 完成后调用:
   # async_save_monitor.record_save_end(save_id, success=True)
   ```

5. 在关键操作添加性能日志：
   ```python
   # 记录性能数据
   perf_logger.log('llm_call', elapsed_ms, {'model': model_name})
   ```

性能统计查看：
```python
# 查看 LLM 缓存统计
print(llm_cache.get_stats())

# 查看异步保存统计
print(async_save_monitor.get_stats())

# 查看性能统计
print(perf_logger.get_stats('llm_call'))
```

"""

# 示例代码片段

"""
# 示例 1: LLM 缓存使用示例
def call_llm_with_cache(self, user_input, system_prompt=None):
    # 1. 检查缓存
    cached = llm_cache.get(user_input)
    if cached:
        logger.info(f"缓存命中: {len(cached)} chars")
        return cached

    # 2. 调用 LLM
    start = time.perf_counter()
    response = self._llm.chat(
        messages=[{"role": "user", "content": user_input}],
        system_prompt=system_prompt
    )
    elapsed = (time.perf_counter() - start) * 1000

    # 3. 记录性能
    perf_logger.log('llm_call', elapsed, {
        'cached': False,
        'response_length': len(response)
    })

    # 4. 保存到缓存
    llm_cache.put(user_input, response)

    return response


# 示例 2: 异步保存监控示例
def chat_with_async_save(self, user_input):
    response = self.call_llm(user_input)

    # 异步保存记忆
    def save_memory():
        try:
            self._memory.add_message("user", user_input)
            self._memory.add_message("assistant", response)
            async_save_monitor.record_save_end(
                save_id, success=True
            )
        except Exception as e:
            async_save_monitor.record_save_end(
                save_id, success=False, error=str(e)
            )

    save_id = async_save_monitor.record_save_start(
        'memory', f'chat_{self._interaction_count}'
    )

    threading.Thread(target=save_memory, daemon=True).start()

    return response
"""
