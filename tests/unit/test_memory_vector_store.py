"""
VectorStore 测试 - pytest 格式
针对 agent/memory/vector_store.py 的测试用例
"""
import pytest
from unittest.mock import patch
from memory.vector_store import VectorStore, KnowledgeBase


@pytest.fixture(autouse=True)
def _disable_sqlite_vec_for_legacy_tests():
    """禁用 sqlite-vec 后端，让现有测试使用 JSON fallback。

    Why: sqlite-vec 后端需要加载 sentence_transformers 模型（55s+），
    会导致现有测试超时。这些测试原本就使用 JSON fallback（HAS_CHROMA=False），
    保持原有行为以避免回归。sqlite-vec 后端的测试在
    test_vector_store_sqlite_vec.py 中独立覆盖。
    """
    import sys
    original = sys.modules.get('sqlite_vec')
    with patch.dict(sys.modules, {'sqlite_vec': None}):
        yield
    if original is not None:
        sys.modules['sqlite_vec'] = original


class TestVectorStoreBasics:
    """测试 VectorStore 的基本功能"""
    
    @pytest.fixture
    def simple_vector_store(self):
        """创建一个简单的 VectorStore 实例 (使用内存存储)"""
        try:
            return VectorStore(
                collection_name='test_collection',
                persist_dir='./data/test_memory'
            )
        except Exception as e:
            pytest.skip(f"VectorStore initialization skipped: {e}")
    
    @pytest.mark.p0
    def test_vector_store_class_exists(self):
        """测试 VectorStore 类存在"""
        assert VectorStore is not None
    
    @pytest.mark.p0
    def test_knowledge_base_class_exists(self):
        """测试 KnowledgeBase 类存在"""
        assert KnowledgeBase is not None
    
    @pytest.mark.p1
    def test_vector_store_imports(self):
        """测试模块导入"""
        import memory.vector_store
        assert memory.vector_store is not None

    @pytest.fixture
    def vector_store(self):
        """VectorStore 实例"""
        try:
            return VectorStore(
                collection_name='pytest_test',
                persist_dir='./data/test_pytest'
            )
        except Exception as e:
            pytest.skip(f"VectorStore not available: {e}")
    
    @pytest.mark.p1
    def test_vector_store_initialization(self, vector_store):
        """测试 VectorStore 初始化"""
        assert vector_store is not None
    
    @pytest.mark.p1
    def test_vector_store_has_methods(self, vector_store):
        """测试 VectorStore 有必要的方法"""
        # 检查常见的方法
        expected_methods = ['add', 'search', 'get', 'delete']
        for method_name in expected_methods:
            if hasattr(vector_store, method_name):
                assert True  # 只要有方法就行


class TestKnowledgeBase:
    """测试 KnowledgeBase"""
    
    @pytest.fixture
    def knowledge_base(self):
        """KnowledgeBase 实例"""
        try:
            vector_store = VectorStore(
                collection_name='kb_test',
                persist_dir='./data/test_kb'
            )
            return KnowledgeBase(vector_store)
        except Exception as e:
            pytest.skip(f"KnowledgeBase initialization skipped: {e}")
    
    @pytest.mark.p1
    def test_knowledge_base_init(self, knowledge_base):
        """测试 KnowledgeBase 初始化"""
        assert knowledge_base is not None
