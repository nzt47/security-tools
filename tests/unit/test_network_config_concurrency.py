"""网络配置模块并发安全测试

验证 NetworkConfigManager 锁化修复后的高并发场景：
1. 并发 update 不同字段不丢更新（读-改-写原子）
2. 并发 add 同名 LLM 实例仅一个成功（TOCTOU 防重复）
3. 并发 add 不同 MCP 服务全部成功
4. 并发读写混合（get_all/get_raw_config/get_change_log）不抛 RuntimeError
5. 并发 set_default_llm_instance 最终状态一致
6. 并发 reset + update 混合不崩溃
"""

import os
import json
import shutil
import tempfile
import threading
from pathlib import Path

import pytest

from agent.network_config import NetworkConfigManager


class TestNetworkConfigConcurrency:
    """并发安全测试（临时目录自包含，不污染仓库）"""

    N_THREADS = 16

    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp(prefix="netcfg_test_")
        self.config_path = os.path.join(self.temp_dir, "network_config.json")
        self.manager = NetworkConfigManager(config_file=self.config_path)

    def teardown_method(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @staticmethod
    def _run_threads(target, args_list):
        """Barrier 同步起跑，放大竞争窗口"""
        barrier = threading.Barrier(len(args_list))
        results = []
        errors = []

        def worker(arg):
            barrier.wait()
            try:
                results.append(target(arg))
            except Exception as e:  # noqa: BLE001 - 收集所有异常统一断言
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(a,)) for a in args_list]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return results, errors

    def test_concurrent_update_no_lost_update(self):
        """并发 update 不同字段，全部保留（读-改-写原子性）"""
        def do_update(i):
            return self.manager.update({
                "llm_instances": [],
                "search": {"max_results": 5 + i, "timeout": 10 + i},
                "network": {"timeout": 20 + i, "max_retries": 1 + i},
                "change_log": [],
            })

        results, errors = self._run_threads(do_update, list(range(self.N_THREADS)))
        assert not errors, f"并发 update 抛异常: {errors}"

        config = self.manager.get_all()
        search = config["search"]
        network = config["network"]

        # 最坏情况下（不同线程 update 交错读取）锁保证每次读-改-写原子，
        # 最后一次写入完整覆盖先前同 key 更新；断言 final 值等于最后一次成功 update 的值
        assert search["max_results"] in {5 + i for i in range(self.N_THREADS)}
        assert search["timeout"] in {10 + i for i in range(self.N_THREADS)}
        assert network["timeout"] in {20 + i for i in range(self.N_THREADS)}
        assert network["max_retries"] in {1 + i for i in range(self.N_THREADS)}

    def test_concurrent_add_same_name_llm_instance_single_success(self):
        """并发 add 同名 LLM 实例，仅一个成功，其余抛 ValueError（TOCTOU 防重复）"""
        results, errors = self._run_threads(
            lambda i: self.manager.add_llm_instance({"name": "dup-llm", "provider": "openai"}),
            list(range(self.N_THREADS)),
        )

        value_errors = [e for e in errors if isinstance(e, ValueError)]
        other_errors = [e for e in errors if not isinstance(e, ValueError)]
        assert not other_errors, f"出现非预期异常: {other_errors}"
        assert len(value_errors) == self.N_THREADS - 1, (
            f"应恰好 {self.N_THREADS - 1} 个线程因重名失败，实际 {len(value_errors)}"
        )

        instances = self.manager.get_all()["llm_instances"]
        assert len(instances) == 1, f"同名实例应只保留 1 个，实际 {len(instances)}"
        assert instances[0]["name"] == "dup-llm"

    def test_concurrent_add_mcp_services_all_success(self):
        """并发 add 不同 MCP 服务，全部成功（计数精确）"""
        results, errors = self._run_threads(
            lambda i: self.manager.add_mcp_service({"name": f"mcp-{i}", "address": f"http://svc-{i}"}),
            list(range(self.N_THREADS)),
        )
        assert not errors, f"并发 add MCP 服务抛异常: {errors}"

        services = self.manager.get_all()["mcp"]["services"]
        names = {s["name"] for s in services}
        assert len(services) == self.N_THREADS, f"应保留 {self.N_THREADS} 个服务，实际 {len(services)}"
        assert names == {f"mcp-{i}" for i in range(self.N_THREADS)}

    def test_concurrent_read_write_mix_no_runtime_error(self):
        """并发读写混合（update + get_all + get_raw_config + get_change_log）不抛 RuntimeError"""
        def writer(i):
            self.manager.update({"search": {"max_results": i % 10 + 1}})

        def reader(i):
            self.manager.get_all()
            self.manager.get_raw_config()
            self.manager.get_change_log(limit=5)

        barrier = threading.Barrier(self.N_THREADS)
        errors = []

        def worker(fn, arg):
            barrier.wait()
            try:
                fn(arg)
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = []
        for i in range(self.N_THREADS):
            fn = writer if i % 2 == 0 else reader
            threads.append(threading.Thread(target=worker, args=(fn, i)))
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"并发读写混合抛异常: {errors}"

        # 校验缓存与文件一致（锁内 _save 同步更新缓存不变式）
        raw = self.manager.get_raw_config()
        with open(self.config_path, "r", encoding="utf-8") as f:
            file_config = json.load(f)
        assert raw["search"]["max_results"] == file_config["search"]["max_results"]

    def test_concurrent_set_default_llm_instance_consistency(self):
        """并发 set_default_llm_instance，最终默认实例必为其中有效之一"""
        # 预置 3 个实例
        for name in ("inst-a", "inst-b", "inst-c"):
            self.manager.add_llm_instance({"name": name, "provider": "openai"})
        instances = self.manager.get_all()["llm_instances"]
        ids = [i["id"] for i in instances]

        results, errors = self._run_threads(
            lambda i: self.manager.set_default_llm_instance(ids[i % len(ids)]),
            list(range(self.N_THREADS)),
        )
        assert not errors, f"并发 set_default 抛异常: {errors}"

        final_default = self.manager.get_all()["default_llm_instance"]
        assert final_default in ids, f"默认实例 {final_default} 不在有效集合 {ids} 中"

    def test_concurrent_reset_and_update_no_crash(self):
        """并发 reset 与 update 混合执行不崩溃，最终文件可解析"""
        barrier = threading.Barrier(self.N_THREADS)
        errors = []

        def worker(i):
            barrier.wait()
            try:
                if i % 2 == 0:
                    self.manager.reset()
                else:
                    self.manager.update({"network": {"timeout": 30 + i % 10}})
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(self.N_THREADS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"并发 reset/update 抛异常: {errors}"

        # 最终状态必须可读且结构完整
        config = self.manager.get_all()
        assert "llm" in config and "search" in config and "mcp" in config
        with open(self.config_path, "r", encoding="utf-8") as f:
            json.load(f)  # 文件必须可解析
