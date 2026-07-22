"""P2/P3 上线自动化回归测试脚本

用途: chromadb 降级到 0.5.x 后的核心功能验证
运行: python scripts/verify_p2_p3_regression.py
环境: Windows / Linux（chromadb 0.5.x 降级后）

覆盖验证项:
1. 环境检查（chromadb 版本 + Python 版本）
2. chromadb PersistentClient API
3. Collection CRUD 操作
4. 语义搜索（query）
5. Windows 临时目录兼容性（NotADirectoryError 验证）
6. VectorStore 集成（后端选择）
7. P2 预热缓存（LRU 命中率）
8. P4 heapq 排序（BM25 排序）
9. 数据持久化（重启后数据保留）
10. 线程安全（并发 add/search）
"""

import os
import sys
import time
import tempfile
import threading
import shutil
from pathlib import Path
from datetime import datetime

# 项目根目录
_PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

# 离线模式（避免 HuggingFace 网络请求）
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")


class RegressionTestRunner:
    """回归测试运行器"""

    def __init__(self):
        self.results = []
        self.passed = 0
        self.failed = 0
        self.skipped = 0

    def _record(self, name, status, detail=""):
        """记录测试结果"""
        self.results.append({
            "name": name,
            "status": status,
            "detail": detail,
            "elapsed_ms": 0,
        })
        if status == "PASS":
            self.passed += 1
        elif status == "FAIL":
            self.failed += 1
        else:
            self.skipped += 1

    def _run_test(self, name, test_func):
        """运行单个测试"""
        start = time.perf_counter()
        try:
            detail = test_func()
            elapsed = (time.perf_counter() - start) * 1000
            self.results.append({
                "name": name,
                "status": "PASS",
                "detail": detail or "",
                "elapsed_ms": round(elapsed, 2),
            })
            self.passed += 1
            print(f"  [PASS] {name} ({elapsed:.0f}ms)")
        except Exception as e:
            elapsed = (time.perf_counter() - start) * 1000
            self.results.append({
                "name": name,
                "status": "FAIL",
                "detail": str(e),
                "elapsed_ms": round(elapsed, 2),
            })
            self.failed += 1
            print(f"  [FAIL] {name} ({elapsed:.0f}ms): {e}")

    # ═══════════════════════════════════════════════════════════════
    # 测试项
    # ═══════════════════════════════════════════════════════════════

    def test_01_environment(self):
        """1. 环境检查：chromadb 版本 + Python 版本"""
        import chromadb
        version = chromadb.__version__
        major = int(version.split(".")[0])

        assert major == 0, f"期望 chromadb 0.5.x，实际 {version}（降级未成功）"
        assert sys.version_info >= (3, 10), f"Python {sys.version} < 3.10"

        return f"chromadb {version}, Python {sys.version_info.major}.{sys.version_info.minor}"

    def test_02_persistent_client(self):
        """2. chromadb PersistentClient API"""
        import chromadb
        from chromadb.config import Settings

        with tempfile.TemporaryDirectory() as tmpdir:
            client = chromadb.PersistentClient(
                path=tmpdir,
                settings=Settings(anonymized_telemetry=False),
            )
            assert client is not None

            col = client.get_or_create_collection(name="regression_test")
            assert col.name == "regression_test"

        return "PersistentClient + get_or_create_collection 正常"

    def test_03_crud_operations(self):
        """3. Collection CRUD 操作"""
        import chromadb
        from chromadb.config import Settings

        with tempfile.TemporaryDirectory() as tmpdir:
            client = chromadb.PersistentClient(
                path=tmpdir, settings=Settings(anonymized_telemetry=False)
            )
            col = client.get_or_create_collection(name="crud_test")

            # Create
            col.add(
                documents=["doc1", "doc2", "doc3"],
                metadatas=[{"id": 1}, {"id": 2}, {"id": 3}],
                ids=["id1", "id2", "id3"],
            )
            assert col.count() == 3, f"Create 失败: count={col.count()}"

            # Read
            result = col.get(ids=["id1"])
            assert len(result["ids"]) == 1, "Read 失败"

            # Update
            col.update(ids=["id1"], documents=["updated"])
            result = col.get(ids=["id1"])
            assert result["documents"][0] == "updated", "Update 失败"

            # Delete
            col.delete(ids=["id1"])
            assert col.count() == 2, f"Delete 失败: count={col.count()}"

        return "CRUD(add/get/update/delete) 全部正常"

    def test_04_semantic_search(self):
        """4. 语义搜索（query）"""
        import chromadb
        from chromadb.config import Settings

        with tempfile.TemporaryDirectory() as tmpdir:
            client = chromadb.PersistentClient(
                path=tmpdir, settings=Settings(anonymized_telemetry=False)
            )
            col = client.get_or_create_collection(name="query_test")
            col.add(
                documents=["apple fruit", "banana fruit", "car vehicle"],
                ids=["1", "2", "3"],
            )

            results = col.query(query_texts=["fruit"], n_results=2)

            # 验证返回格式
            for key in ("ids", "documents", "distances", "metadatas"):
                assert key in results, f"返回缺少 {key}"

            assert len(results["ids"][0]) == 2, f"返回结果数不正确: {len(results['ids'][0])}"

        return f"query 返回格式正确，返回 {len(results['ids'][0])} 条结果"

    def test_05_windows_temp_dir(self):
        """5. Windows 临时目录兼容性（NotADirectoryError 验证）"""
        import chromadb
        from chromadb.config import Settings

        # [TLM-L1] Windows 临时目录兼容性 — 1.x 触发 NotADirectoryError [WinError 267]
        # 0.5.x 修复了此问题，验证不再报错
        tmpdir = tempfile.mkdtemp()
        try:
            client = chromadb.PersistentClient(
                path=tmpdir, settings=Settings(anonymized_telemetry=False)
            )
            col = client.get_or_create_collection(name="winpath_test")
            col.add(documents=["test doc"], ids=["1"])
            result = col.query(query_texts=["test"], n_results=1)
            assert len(result["ids"][0]) == 1
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

        return "Windows 临时目录无 NotADirectoryError"

    def test_06_vectorstore_integration(self):
        """6. VectorStore 集成（后端选择）"""
        # [TLM-L1] VectorStore 集成 — 验证降级后使用向量后端（chromadb/sqlite_vec，非 json fallback）
        # Why: VectorStore 优先级 sqlite_vec > chromadb > json。
        #   降级验证目标是确保不是 json fallback（即向量搜索可用），
        #   sqlite_vec 和 chromadb 都是有效的向量后端。
        from memory.vector_store import VectorStore

        with tempfile.TemporaryDirectory() as tmpdir:
            store = VectorStore(
                persist_dir=tmpdir,
                collection_name="vs_integration",
                cache_size=100,
            )
            assert store._backend in ("chromadb", "sqlite_vec"), (
                f"期望 chromadb 或 sqlite_vec 后端，实际 {store._backend}（降级到 json fallback）"
            )

            store.add("测试记忆内容", metadata={"type": "verify"})
            store.add("另一条记忆", metadata={"type": "test"})

            results = store.search("测试", top_k=5)
            assert len(results) > 0, "搜索无结果"

        return f"backend={store._backend}, 搜索返回 {len(results)} 条结果"

    def test_07_p2_warmup_cache(self):
        """7. P2 预热缓存（LRU 命中率）"""
        # [TLM-L1] P2 预热缓存 — 首次 search 预热 LRU，后续命中缓存
        from unittest import mock
        from memory.vector_store import VectorStore
        from memory.vector_store import vector_store as vs_module

        # 使用 mock 强制 JSON fallback 路径（隔离 chromadb 网络/模型依赖）
        with mock.patch.object(vs_module, "HAS_CHROMA", False), \
             mock.patch.object(vs_module, "HAS_SENTENCE_TRANSFORMERS", False), \
             mock.patch.dict(sys.modules, {"sqlite_vec": None, "chromadb": None}):
            with tempfile.TemporaryDirectory() as tmpdir:
                store = VectorStore(
                    persist_dir=tmpdir,
                    collection_name="p2_cache",
                    enable_inverted_index=True,
                    cache_size=100,
                )
                assert store._backend == "json", f"期望 json 后端，实际 {store._backend}"

                for i in range(100):
                    store.add(f"document {i}: testing BM25 search", metadata={"id": i})

                # 预热
                store.search("testing BM25 search", top_k=5)

                # 100 次搜索（应命中缓存）
                for _ in range(100):
                    store.search("testing BM25 search", top_k=5)

                stats = store.get_cache_stats()
                assert stats["hit_rate"] >= 95.0, (
                    f"缓存命中率 {stats['hit_rate']}% < 95%"
                )

        return f"命中率={stats['hit_rate']}%, hits={stats['hits']}, misses={stats['misses']}"

    def test_08_p4_heapq_sort(self):
        """8. P4 heapq 排序（BM25 排序）"""
        # [TLM-L1] P4 heapq — heapq.nlargest 替代 sorted[:top_k]
        # Why: InvertedIndex 在 vector_store.vector_store 模块中，__init__.py 未导出
        from memory.vector_store.vector_store import InvertedIndex

        index = InvertedIndex()
        for i in range(500):
            index.add_document(f"doc_{i}", f"document {i} testing search performance benchmark")

        results = index.search("testing search performance", top_k=5)
        assert len(results) == 5, f"期望 5 条结果，实际 {len(results)}"

        # 验证结果按分数降序排列
        scores = [score for _, score in results]
        assert scores == sorted(scores, reverse=True), "结果未按分数降序"

        return f"返回 {len(results)} 条结果，top score={scores[0]:.4f}"

    def test_09_persistence(self):
        """9. 数据持久化（重启后数据保留）"""
        import chromadb
        from chromadb.config import Settings

        persist_path = tempfile.mkdtemp()
        try:
            # 第一次: 写入
            client1 = chromadb.PersistentClient(
                path=persist_path, settings=Settings(anonymized_telemetry=False)
            )
            col1 = client1.get_or_create_collection(name="persist_test")
            col1.add(documents=["持久化测试文档"], ids=["1"])
            assert col1.count() == 1

            # 第二次: 重读
            client2 = chromadb.PersistentClient(
                path=persist_path, settings=Settings(anonymized_telemetry=False)
            )
            col2 = client2.get_or_create_collection(name="persist_test")
            assert col2.count() == 1, f"持久化失败: count={col2.count()}"
        finally:
            shutil.rmtree(persist_path, ignore_errors=True)

        return "数据持久化正常（写入 → 重读一致）"

    def test_10_thread_safety(self):
        """10. 线程安全（并发 add/search）"""
        import chromadb
        from chromadb.config import Settings

        errors = []

        with tempfile.TemporaryDirectory() as tmpdir:
            client = chromadb.PersistentClient(
                path=tmpdir, settings=Settings(anonymized_telemetry=False)
            )
            col = client.get_or_create_collection(name="thread_test")

            def writer():
                try:
                    for i in range(20):
                        col.add(
                            documents=[f"thread doc {i}"],
                            ids=[f"t_{threading.current_thread().name}_{i}"],
                        )
                except Exception as e:
                    errors.append(f"writer: {e}")

            def reader():
                try:
                    for _ in range(20):
                        col.query(query_texts=["thread"], n_results=5)
                except Exception as e:
                    errors.append(f"reader: {e}")

            threads = [
                threading.Thread(target=writer, name="W1"),
                threading.Thread(target=writer, name="W2"),
                threading.Thread(target=reader, name="R1"),
                threading.Thread(target=reader, name="R2"),
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            assert not errors, f"线程安全错误: {errors}"
            assert col.count() == 40, f"期望 40 条，实际 {col.count()}"

        return f"4 线程并发（2写2读）无错误，count={col.count()}"

    # ═══════════════════════════════════════════════════════════════
    # 运行 & 报告
    # ═══════════════════════════════════════════════════════════════

    def run_all(self):
        """运行所有测试"""
        print("=" * 70)
        print("P2/P3 上线自动化回归测试")
        print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"环境: Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
        print("=" * 70)

        tests = [
            ("1. 环境检查", self.test_01_environment),
            ("2. PersistentClient API", self.test_02_persistent_client),
            ("3. Collection CRUD", self.test_03_crud_operations),
            ("4. 语义搜索 query", self.test_04_semantic_search),
            ("5. Windows 临时目录兼容性", self.test_05_windows_temp_dir),
            ("6. VectorStore 集成", self.test_06_vectorstore_integration),
            ("7. P2 预热缓存", self.test_07_p2_warmup_cache),
            ("8. P4 heapq 排序", self.test_08_p4_heapq_sort),
            ("9. 数据持久化", self.test_09_persistence),
            ("10. 线程安全", self.test_10_thread_safety),
        ]

        for name, func in tests:
            print(f"\n[{name}]")
            self._run_test(name, func)

        self._print_report()
        return self.failed == 0

    def _print_report(self):
        """打印测试报告"""
        print("\n" + "=" * 70)
        print("测试报告汇总")
        print("=" * 70)
        print(f"{'测试项':<35} {'状态':<8} {'耗时':<10} {'详情'}")
        print("-" * 70)
        for r in self.results:
            status_icon = "[OK]" if r["status"] == "PASS" else "[X]"
            detail = r["detail"][:40] if r["detail"] else ""
            print(f"{r['name']:<35} {status_icon:<8} {r['elapsed_ms']:.0f}ms   {detail}")
        print("-" * 70)
        total = self.passed + self.failed + self.skipped
        print(f"总计: {total}  通过: {self.passed}  失败: {self.failed}  跳过: {self.skipped}")
        print("=" * 70)

        if self.failed == 0:
            print("所有测试通过 — P2/P3 降级后核心功能正常")
        else:
            print(f"有 {self.failed} 项测试失败 — 请检查上方详情")

        # 生成报告文件
        report_path = _PROJECT_ROOT / "logs" / "p2_p3_regression_report.md"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(f"# P2/P3 回归测试报告\n\n")
            f.write(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"环境: Python {sys.version_info.major}.{sys.version_info.minor}\n\n")
            f.write(f"| 测试项 | 状态 | 耗时(ms) | 详情 |\n")
            f.write(f"|--------|------|----------|------|\n")
            for r in self.results:
                f.write(f"| {r['name']} | {r['status']} | {r['elapsed_ms']:.0f} | {r['detail'][:60]} |\n")
            f.write(f"\n**总计**: {total}  **通过**: {self.passed}  **失败**: {self.failed}  **跳过**: {self.skipped}\n")
        print(f"\n报告已保存: {report_path}")


if __name__ == "__main__":
    runner = RegressionTestRunner()
    success = runner.run_all()
    sys.exit(0 if success else 1)
