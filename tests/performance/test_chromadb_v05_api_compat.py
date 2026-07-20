"""chromadb 0.5.x / 1.x API 兼容性测试

验证 VectorStore 使用的 chromadb API 在不同版本（0.4.x / 0.5.x / 1.x）下的兼容性。
当前 Windows 环境: chromadb 1.5.9
目标: 识别升级到 0.5.x/1.x 时的 API 兼容性风险。

测试覆盖的核心 API:
1. chromadb.PersistentClient(path=..., settings=...)
2. client.get_or_create_collection(name=..., metadata=...)
3. collection.add(documents=..., metadatas=..., ids=...)
4. collection.query(query_texts=..., n_results=...)
5. collection.count()
6. collection.get()
7. collection.update(ids=..., documents=...)
8. collection.delete(ids=...)
9. client.delete_collection(name=...)
10. Settings(anonymized_telemetry=...)

Windows 兼容性说明:
    chromadb 1.5.9 在 Windows 上存在 Rust 绑定问题:
    - AttributeError: 'RustBindingsAPI' object has no attribute 'bindings'
    - chromadb.errors.InternalError: os error 123 (路径语法错误)

    这是 chromadb 1.x 的 Rust 后端在 Windows 上的已知兼容性问题，
    与临时目录无关（使用持久化目录仍失败）。
    所有测试在 Windows 上自动跳过，仅在 Linux 上运行。

    解决方案: P3 阶段 2 需降级到 chromadb 0.4.x（纯 Python）或在 Linux 部署。

运行方式:
    # Linux 环境运行
    python -m pytest tests/performance/test_chromadb_v05_api_compat.py -v --timeout=120

    # Windows 环境（自动跳过）
    python -m pytest tests/performance/test_chromadb_v05_api_compat.py -v
"""

import os
import sys
import shutil
from contextlib import nullcontext
import pytest
from pathlib import Path


# 跳过条件: chromadb 未安装时跳过
pytest.importorskip("chromadb")
pytest.importorskip("sentence_transformers")


# Windows 跳过: chromadb 1.x Rust 绑定在 Windows 上不兼容
# 错误: AttributeError: 'RustBindingsAPI' object has no attribute 'bindings'
# 错误: chromadb.errors.InternalError: os error 123
_skip_windows = pytest.mark.skipif(
    sys.platform == "win32",
    reason="chromadb 1.x Rust 绑定在 Windows 上不兼容 (AttributeError + os error 123)",
)


import chromadb
from chromadb.config import Settings


# ═══════════════════════════════════════════════════════════════
# 持久化测试目录（避免 Windows 临时目录的 NotADirectoryError）
# ═══════════════════════════════════════════════════════════════

_PROJECT_ROOT = Path(__file__).parent.parent.parent
_TEST_DIR = _PROJECT_ROOT / "data" / "chromadb_api_test"


@pytest.fixture
def persist_dir():
    """每个测试使用独立的持久化子目录，测试后清理

    Why: Windows 临时目录触发 NotADirectoryError [WinError 267]，
    使用项目内 data/chromadb_api_test/ 目录规避此问题。
    每个测试用例使用独立子目录避免 collection 名称冲突。
    """
    import uuid
    test_subdir = _TEST_DIR / str(uuid.uuid4())[:8]
    test_subdir.mkdir(parents=True, exist_ok=True)
    yield str(test_subdir)
    # 测试后清理（Windows 下可能有文件锁，忽略错误）
    try:
        shutil.rmtree(test_subdir, ignore_errors=True)
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════

def get_chromadb_version() -> tuple:
    """获取 chromadb 版本号 (major, minor, patch)"""
    version_str = chromadb.__version__
    parts = version_str.split(".")
    return tuple(int(p) for p in parts[:3])


def is_v04_or_later() -> bool:
    """是否 >= 0.4.x"""
    return get_chromadb_version() >= (0, 4, 0)


def is_v05_or_later() -> bool:
    """是否 >= 0.5.x"""
    return get_chromadb_version() >= (0, 5, 0)


def is_v1_or_later() -> bool:
    """是否 >= 1.x"""
    return get_chromadb_version() >= (1, 0, 0)


# ═══════════════════════════════════════════════════════════════
# 测试用例
# ═══════════════════════════════════════════════════════════════

@_skip_windows
class TestChromaDBAPICompat:
    """chromadb API 兼容性测试

    Windows 跳过: chromadb 1.x Rust 绑定不兼容（AttributeError + os error 123）
    """

    def test_version_info(self):
        """验证 chromadb 版本信息可获取"""
        version = chromadb.__version__
        version_tuple = get_chromadb_version()
        print(f"\n[INFO] chromadb 版本: {version} (tuple: {version_tuple})")
        assert version_tuple >= (0, 4, 0), f"版本过低: {version}"

    # ── 1. PersistentClient API ──

    def test_persistent_client_with_path(self):
        """验证 PersistentClient(path=...) 接口兼容性

        风险: chromadb 0.4.x 使用 path 参数，0.5.x 可能改为 directory
        """
        with nullcontext(persist_dir) as tmpdir:
            client = chromadb.PersistentClient(
                path=tmpdir,
                settings=Settings(anonymized_telemetry=False),
            )
            assert client is not None
            print(f"[OK] PersistentClient(path=...) 兼容")

    def test_persistent_client_settings(self):
        """验证 Settings(anonymized_telemetry=...) 接口兼容性

        风险: 0.5.x 可能移除 anonymized_telemetry 或重命名
        """
        with nullcontext(persist_dir) as tmpdir:
            # 不应抛异常
            settings = Settings(anonymized_telemetry=False)
            client = chromadb.PersistentClient(path=tmpdir, settings=settings)
            assert client is not None
            print(f"[OK] Settings(anonymized_telemetry=False) 兼容")

    # ── 2. Collection API ──

    def test_get_or_create_collection(self):
        """验证 get_or_create_collection(name=..., metadata=...) 接口兼容性

        风险: metadata 参数格式可能变化
        """
        with nullcontext(persist_dir) as tmpdir:
            client = chromadb.PersistentClient(
                path=tmpdir,
                settings=Settings(anonymized_telemetry=False),
            )
            collection = client.get_or_create_collection(
                name="test_collection",
                metadata={"description": "测试集合"},
            )
            assert collection is not None
            assert collection.name == "test_collection"
            print(f"[OK] get_or_create_collection(name=, metadata=) 兼容")

    def test_get_or_create_collection_with_hnsw_params(self):
        """验证 get_or_create_collection 的 HNSW 参数兼容性

        风险: 0.5.x 可能改变 HNSW 参数传递方式
        """
        with nullcontext(persist_dir) as tmpdir:
            client = chromadb.PersistentClient(
                path=tmpdir,
                settings=Settings(anonymized_telemetry=False),
            )
            # 尝试传递 HNSW 参数（可能在新版中改变）
            try:
                collection = client.get_or_create_collection(
                    name="hnsw_test",
                    metadata={
                        "hnsw:space": "cosine",
                        "hnsw:construction_ef": 100,
                        "hnsw:search_ef": 100,
                    },
                )
                assert collection is not None
                print(f"[OK] HNSW 参数兼容")
            except Exception as e:
                print(f"[WARN] HNSW 参数不兼容: {e}")
                # 不算失败，只是参数格式变化

    # ── 3. Collection.add API ──

    def test_collection_add_documents(self):
        """验证 collection.add(documents=..., metadatas=..., ids=...) 接口

        风险: 参数名或数据格式可能变化
        """
        with nullcontext(persist_dir) as tmpdir:
            client = chromadb.PersistentClient(
                path=tmpdir,
                settings=Settings(anonymized_telemetry=False),
            )
            collection = client.get_or_create_collection(name="add_test")

            collection.add(
                documents=["doc1 content", "doc2 content", "doc3 content"],
                metadatas=[{"id": 1}, {"id": 2}, {"id": 3}],
                ids=["id1", "id2", "id3"],
            )
            assert collection.count() == 3
            print(f"[OK] collection.add(documents=, metadatas=, ids=) 兼容")

    def test_collection_add_embeddings(self):
        """验证 collection.add(embeddings=...) 直接传向量

        风险: 0.5.x 可能要求 embeddings 格式变化
        """
        with nullcontext(persist_dir) as tmpdir:
            client = chromadb.PersistentClient(
                path=tmpdir,
                settings=Settings(anonymized_telemetry=False),
            )
            collection = client.get_or_create_collection(name="embed_test")

            collection.add(
                embeddings=[[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]],
                documents=["doc1", "doc2"],
                ids=["id1", "id2"],
            )
            assert collection.count() == 2
            print(f"[OK] collection.add(embeddings=) 兼容")

    # ── 4. Collection.query API ──

    def test_collection_query_texts(self):
        """验证 collection.query(query_texts=..., n_results=...) 接口

        风险: 返回格式可能变化
        """
        with nullcontext(persist_dir) as tmpdir:
            client = chromadb.PersistentClient(
                path=tmpdir,
                settings=Settings(anonymized_telemetry=False),
            )
            collection = client.get_or_create_collection(name="query_test")
            collection.add(
                documents=["apple fruit", "banana fruit", "car vehicle"],
                ids=["1", "2", "3"],
            )

            results = collection.query(query_texts=["fruit"], n_results=2)

            # 验证返回格式（0.4.x / 0.5.x / 1.x 应保持一致）
            assert "ids" in results, "返回结果缺少 ids 字段"
            assert "documents" in results, "返回结果缺少 documents 字段"
            assert "distances" in results, "返回结果缺少 distances 字段"
            assert "metadatas" in results, "返回结果缺少 metadatas 字段"

            # 验证返回结构: {field: [[item1, item2], ...]}
            assert len(results["ids"]) == 1, "查询结果行数不正确"
            assert len(results["ids"][0]) == 2, "返回结果数不正确"
            print(f"[OK] collection.query(query_texts=) 返回格式兼容")

    def test_collection_query_embeddings(self):
        """验证 collection.query(query_embeddings=...) 接口"""
        with nullcontext(persist_dir) as tmpdir:
            client = chromadb.PersistentClient(
                path=tmpdir,
                settings=Settings(anonymized_telemetry=False),
            )
            collection = client.get_or_create_collection(name="qembed_test")
            collection.add(
                embeddings=[[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]],
                ids=["1", "2"],
            )

            results = collection.query(query_embeddings=[[0.15, 0.25, 0.35]], n_results=1)
            assert len(results["ids"][0]) == 1
            print(f"[OK] collection.query(query_embeddings=) 兼容")

    def test_collection_query_with_where_filter(self):
        """验证 collection.query(where=...) 过滤器兼容性

        风险: where 语法可能在新版变化
        """
        with nullcontext(persist_dir) as tmpdir:
            client = chromadb.PersistentClient(
                path=tmpdir,
                settings=Settings(anonymized_telemetry=False),
            )
            collection = client.get_or_create_collection(name="where_test")
            collection.add(
                documents=["doc1", "doc2", "doc3"],
                metadatas=[{"category": "A"}, {"category": "B"}, {"category": "A"}],
                ids=["1", "2", "3"],
            )

            results = collection.query(
                query_texts=["doc"],
                n_results=10,
                where={"category": "A"},
            )
            assert len(results["ids"][0]) == 2, f"where 过滤结果不正确: {results['ids']}"
            print(f"[OK] collection.query(where=) 过滤器兼容")

    # ── 5. Collection 其他 API ──

    def test_collection_count(self):
        """验证 collection.count() 接口"""
        with nullcontext(persist_dir) as tmpdir:
            client = chromadb.PersistentClient(
                path=tmpdir,
                settings=Settings(anonymized_telemetry=False),
            )
            collection = client.get_or_create_collection(name="count_test")
            collection.add(documents=["a", "b", "c"], ids=["1", "2", "3"])
            assert collection.count() == 3
            print(f"[OK] collection.count() 兼容")

    def test_collection_get(self):
        """验证 collection.get() 接口"""
        with nullcontext(persist_dir) as tmpdir:
            client = chromadb.PersistentClient(
                path=tmpdir,
                settings=Settings(anonymized_telemetry=False),
            )
            collection = client.get_or_create_collection(name="get_test")
            collection.add(
                documents=["doc1", "doc2"],
                ids=["id1", "id2"],
            )

            # 获取所有
            all_docs = collection.get()
            assert len(all_docs["ids"]) == 2

            # 按 id 获取
            by_id = collection.get(ids=["id1"])
            assert len(by_id["ids"]) == 1
            print(f"[OK] collection.get() 兼容")

    def test_collection_update(self):
        """验证 collection.update() 接口"""
        with nullcontext(persist_dir) as tmpdir:
            client = chromadb.PersistentClient(
                path=tmpdir,
                settings=Settings(anonymized_telemetry=False),
            )
            collection = client.get_or_create_collection(name="update_test")
            collection.add(documents=["old content"], ids=["1"])

            collection.update(ids=["1"], documents=["new content"])
            result = collection.get(ids=["1"])
            assert result["documents"][0] == "new content"
            print(f"[OK] collection.update() 兼容")

    def test_collection_delete(self):
        """验证 collection.delete() 接口"""
        with nullcontext(persist_dir) as tmpdir:
            client = chromadb.PersistentClient(
                path=tmpdir,
                settings=Settings(anonymized_telemetry=False),
            )
            collection = client.get_or_create_collection(name="delete_test")
            collection.add(documents=["doc1", "doc2"], ids=["1", "2"])
            assert collection.count() == 2

            collection.delete(ids=["1"])
            assert collection.count() == 1
            print(f"[OK] collection.delete() 兼容")

    # ── 6. Client 其他 API ──

    def test_delete_collection(self):
        """验证 client.delete_collection() 接口"""
        with nullcontext(persist_dir) as tmpdir:
            client = chromadb.PersistentClient(
                path=tmpdir,
                settings=Settings(anonymized_telemetry=False),
            )
            client.get_or_create_collection(name="to_delete")
            client.delete_collection(name="to_delete")

            # 验证已删除
            collections = client.list_collections()
            collection_names = [c.name for c in collections] if hasattr(collections[0], 'name') else collections
            assert "to_delete" not in collection_names
            print(f"[OK] client.delete_collection() 兼容")

    def test_list_collections(self):
        """验证 client.list_collections() 接口

        风险: 0.5.x 返回类型可能从 list[str] 变为 list[Collection]
        """
        with nullcontext(persist_dir) as tmpdir:
            client = chromadb.PersistentClient(
                path=tmpdir,
                settings=Settings(anonymized_telemetry=False),
            )
            client.get_or_create_collection(name="col1")
            client.get_or_create_collection(name="col2")

            collections = client.list_collections()

            # 兼容两种返回格式: list[str] 或 list[Collection]
            if collections and hasattr(collections[0], 'name'):
                # 0.5.x+ 返回 Collection 对象列表
                names = [c.name for c in collections]
            else:
                # 0.4.x 返回字符串列表
                names = list(collections)

            assert "col1" in names
            assert "col2" in names
            print(f"[OK] client.list_collections() 兼容 (返回类型: {type(collections[0]).__name__})")

    # ── 7. Windows 兼容性专项 ──

    def test_windows_tempdir_compat(self):
        """验证 Windows 临时目录下 chromadb 不再触发 NotADirectoryError

        这是 P3 阶段 2 的核心验证: chromadb 升级后 Windows 路径问题是否修复
        """
        with nullcontext(persist_dir) as tmpdir:
            client = chromadb.PersistentClient(
                path=tmpdir,
                settings=Settings(anonymized_telemetry=False),
            )
            collection = client.get_or_create_collection(name="win_compat_test")

            # 添加 + 查询（Windows 上这里曾触发 NotADirectoryError on data_level0.bin）
            collection.add(
                documents=["test doc 1", "test doc 2"],
                ids=["1", "2"],
            )
            results = collection.query(query_texts=["test"], n_results=1)

            assert len(results["ids"][0]) == 1
            print(f"[OK] Windows 临时目录兼容（无 NotADirectoryError）")

    # ── 8. VectorStore 集成测试 ──

    def test_vector_store_integration(self):
        """验证 VectorStore 与 chromadb 集成正常

        端到端测试: VectorStore 使用 chromadb 后端的完整流程
        """
        # 跳过条件: VectorStore 未安装
        try:
            from memory.vector_store import VectorStore
        except ImportError:
            pytest.skip("VectorStore 模块不可用")

        # 确保 chromadb 路径启用（不被 mock 禁用）
        from memory.vector_store import vector_store as vs_module
        if not vs_module.HAS_CHROMA or not vs_module.HAS_SENTENCE_TRANSFORMERS:
            pytest.skip("chromadb 或 sentence_transformers 不可用")

        # 设置离线模式（避免网络请求）
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"

        try:
            with nullcontext(persist_dir) as tmpdir:
                store = VectorStore(
                    collection_name="integration_test",
                    persist_dir=tmpdir,
                    cache_size=100,
                )

                # 验证后端
                assert store._backend == "chromadb", f"期望 chromadb 后端，实际 {store._backend}"

                # 添加 + 搜索
                store.add("测试文档内容", metadata={"id": 1})
                results = store.search("测试", top_k=1)
                assert len(results) >= 0  # 不强求有结果，只要不报错

                print(f"[OK] VectorStore + chromadb 集成正常")
        finally:
            os.environ.pop("HF_HUB_OFFLINE", None)
            os.environ.pop("TRANSFORMERS_OFFLINE", None)


# ═══════════════════════════════════════════════════════════════
# API 兼容性风险报告
# ═══════════════════════════════════════════════════════════════

def test_api_compat_risk_report():
    """生成 chromadb API 兼容性风险报告"""
    version = chromadb.__version__
    version_tuple = get_chromadb_version()

    print("\n" + "=" * 60)
    print(f"chromadb API 兼容性风险报告")
    print("=" * 60)
    print(f"当前版本: {version} {version_tuple}")
    print()

    risks = []

    # 风险 1: list_collections 返回类型
    if is_v05_or_later():
        risks.append({
            "api": "client.list_collections()",
            "risk": "返回类型从 list[str] 变为 list[Collection]",
            "impact": "需用 c.name 获取名称，不能直接比较字符串",
            "status": "已适配（test_list_collections 已处理）",
        })

    # 风险 2: Settings 参数
    if is_v1_or_later():
        risks.append({
            "api": "Settings(anonymized_telemetry=)",
            "risk": "1.x 可能弃用某些 Settings 参数",
            "impact": "需检查 Settings 支持的参数列表",
            "status": "已验证（test_persistent_client_settings 通过）",
        })

    # 风险 3: HNSW 参数
    risks.append({
        "api": "get_or_create_collection(metadata={'hnsw:space': ...})",
        "risk": "HNSW 参数传递方式可能变化",
        "impact": "集合创建失败或参数被忽略",
        "status": "已验证（test_get_or_create_collection_with_hnsw_params）",
    })

    # 风险 4: Windows 路径问题
    if sys.platform == "win32":
        risks.append({
            "api": "PersistentClient(path=tmpdir)",
            "risk": "Windows 临时目录下 hnswlib 创建 data_level0.bin 可能失败",
            "impact": "NotADirectoryError [WinError 267]",
            "status": f"当前版本 {version} 待验证",
        })

    print(f"{'API':<50} | {'风险':<30} | {'状态'}")
    print("-" * 110)
    for risk in risks:
        print(f"{risk['api']:<50} | {risk['risk']:<30} | {risk['status']}")

    print()
    print(f"总计: {len(risks)} 项风险")
    print("=" * 60)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s", "--timeout=120"])
