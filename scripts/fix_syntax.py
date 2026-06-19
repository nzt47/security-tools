#!/usr/bin/env python3
"""
修复digital_life.py中的语法错误
"""

def fix_syntax_error():
    """修复语法错误"""
    
    file_path = "agent/digital_life.py"
    
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    print("🔧 修复语法错误...")
    
    # 修复重复的summary行和缩进问题
    old_code = '''                # 降级为原有记忆
                if not memory_context:
                    try:
                        context_messages = self._memory.get_context(token_limit=2048)
                        summary = self._memory.load_summary()
                    summary = self._memory.load_summary()
                    if summary:
                        memory_context = f"我有过去的记忆：\\n{summary[0][:500]}"
                    elif context_messages:
                        recent = context_messages[-3:]
                        memory_context = "最近对话：\\n" + "\\n".join(
                            f"{m['role']}: {m['content'][:200]}"
                            for m in recent if m.get('content')
                        )
                    logger.info("   └─ 原有记忆获取完成")
                except Exception as e:
                    logger.warning(f"获取原有记忆上下文失败: {e}")
                    memory_context = ""'''
    
    new_code = '''                # 降级为原有记忆
                elif not memory_context:
                    try:
                        logger.info("📚 使用原有记忆管理器...")
                        context_messages = self._memory.get_context(token_limit=2048)
                        summary = self._memory.load_summary()
                        if summary:
                            memory_context = f"我有过去的记忆：\\n{summary[0][:500]}"
                            logger.info(f"   ├─ 摘要长度: {len(summary[0])}")
                        elif context_messages:
                            recent = context_messages[-3:]
                            memory_context = "最近对话：\\n" + "\\n".join(
                                f"{m['role']}: {m['content'][:200]}"
                                for m in recent if m.get('content')
                            )
                            logger.info(f"   ├─ 最近对话数: {len(recent)}")
                        logger.info("   └─ 原有记忆获取完成")
                    except Exception as e:
                        logger.warning(f"获取原有记忆上下文失败: {e}")
                        memory_context = ""'''
    
    if old_code in content:
        content = content.replace(old_code, new_code)
        print("   ✅ 语法错误修复成功")
    else:
        print("   ⚠️ 代码已是最新版本或未找到目标代码")
    
    # 保存文件
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)
    
    print("\n✅ 修复完成！")

if __name__ == "__main__":
    fix_syntax_error()
