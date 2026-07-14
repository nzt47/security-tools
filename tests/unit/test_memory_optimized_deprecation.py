"""[TLM-L6] memory_optimized.py 废弃警告单元测试

验证 import agent.memory_optimized 时正确触发 DeprecationWarning。
使用 importlib.reload 确保同进程内可重复触发（绕过模块缓存）。
"""

import warnings
import importlib
import pytest


class TestDeprecationWarning:
    """验证废弃警告在各种调用方式下触发"""

    def test_reload_triggers_warning(self):
        """reload memory_optimized 模块时触发 DeprecationWarning"""
        import agent.memory_optimized
        with pytest.warns(DeprecationWarning, match="VectorStore"):
            importlib.reload(agent.memory_optimized)

    def test_warning_message_contains_replacement(self):
        """警告消息包含替代方案 VectorStore"""
        import agent.memory_optimized
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            importlib.reload(agent.memory_optimized)
            # 找到 DeprecationWarning
            dep_warnings = [x for x in w if issubclass(x.category, DeprecationWarning)]
            assert len(dep_warnings) >= 1
            msg = str(dep_warnings[0].message)
            assert "VectorStore" in msg
            assert "memory_optimized" in msg

    def test_warning_category_is_deprecation(self):
        """警告类别确切为 DeprecationWarning"""
        import agent.memory_optimized
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            importlib.reload(agent.memory_optimized)
            dep_warnings = [x for x in w if x.category is DeprecationWarning]
            assert len(dep_warnings) >= 1

    def test_warning_mentions_sqlite_vec(self):
        """警告消息提及 sqlite-vec 后端（TLM Step 3 的替代方案）"""
        import agent.memory_optimized
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            importlib.reload(agent.memory_optimized)
            dep_warnings = [x for x in w if issubclass(x.category, DeprecationWarning)]
            msg = str(dep_warnings[0].message)
            assert "sqlite-vec" in msg or "chromadb" in msg
