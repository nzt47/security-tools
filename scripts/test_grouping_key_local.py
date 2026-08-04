"""
本地测试脚本：模拟 CI 环境验证 grouping_key 逻辑

[用途] 在本地模拟多次 CI 运行（不同 GITHUB_RUN_ID），
       验证 cicd_metrics_push.py 的 push() 函数是否正确使用 grouping_key。
[不易] 不依赖真实 pushgateway，通过 mock 验证 grouping_key 参数。
[运行] python scripts/test_grouping_key_local.py
[覆盖] 6 个测试用例：
  1. 不同 GITHUB_RUN_ID 产生不同 grouping_key
  2. GITHUB_RUN_ID 不存在时降级为 'local'
  3. grouping_key 格式正确（含 run_id + ci_job 键）
  4. push 失败不抛异常（不阻塞流水线）
  5. 使用 pushadd_to_gateway 而非 push_to_gateway
  6. 同 run_id 不同 ci_job 的并行 job 不互相覆盖（并发安全）
"""

from __future__ import annotations

import os
import sys
from unittest.mock import patch

# 确保能导入 cicd_metrics_push（同目录）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cicd_metrics_push  # noqa: E402


def test_grouping_key_distinct_per_run():
    """测试 1：不同 GITHUB_RUN_ID 产生不同 grouping_key。"""
    print("\n[测试 1] 不同 GITHUB_RUN_ID 产生不同 grouping_key")

    captured = []

    def mock_pushadd(url, job, registry, grouping_key=None, **kwargs):
        captured.append({"job": job, "grouping_key": dict(grouping_key or {})})

    with patch("prometheus_client.pushadd_to_gateway", side_effect=mock_pushadd):
        # [不易] 模拟两次不同的 CI 运行
        os.environ["GITHUB_RUN_ID"] = "run-001"
        os.environ["GITHUB_JOB"] = "lint-and-typecheck"
        cicd_metrics_push.push("ci-cd-build")

        os.environ["GITHUB_RUN_ID"] = "run-002"
        os.environ["GITHUB_JOB"] = "lint-and-typecheck"
        cicd_metrics_push.push("ci-cd-build")

    print(f"  运行 1: job={captured[0]['job']}, grouping_key={captured[0]['grouping_key']}")
    print(f"  运行 2: job={captured[1]['job']}, grouping_key={captured[1]['grouping_key']}")

    assert captured[0]["grouping_key"]["run_id"] == "run-001", "运行 1 run_id 不正确"
    assert captured[1]["grouping_key"]["run_id"] == "run-002", "运行 2 run_id 不正确"
    assert captured[0]["grouping_key"] != captured[1]["grouping_key"], "grouping_key 应不同"
    print("  ✅ 通过：不同运行产生不同 grouping_key")


def test_grouping_key_fallback_local():
    """测试 2：GITHUB_RUN_ID 不存在时降级为 'local'。"""
    print("\n[测试 2] GITHUB_RUN_ID 不存在时降级为 'local'")

    os.environ.pop("GITHUB_RUN_ID", None)
    os.environ.pop("GITHUB_JOB", None)

    captured = []

    def mock_pushadd(url, job, registry, grouping_key=None, **kwargs):
        captured.append(dict(grouping_key or {}))

    with patch("prometheus_client.pushadd_to_gateway", side_effect=mock_pushadd):
        cicd_metrics_push.push("ci-cd-test")

    print(f"  grouping_key={captured[0]}")
    assert captured[0]["run_id"] == "local", f"应降级为 'local'，实际: {captured[0]['run_id']}"
    assert captured[0]["ci_job"] == "local", f"ci_job 应降级为 'local'，实际: {captured[0]['ci_job']}"
    print("  ✅ 通过：降级为 'local'")


def test_grouping_key_format():
    """测试 3：grouping_key 格式正确（包含 run_id 和 ci_job 键）。"""
    print("\n[测试 3] grouping_key 格式正确（含 run_id + ci_job）")

    os.environ["GITHUB_RUN_ID"] = "run-format-test"
    os.environ["GITHUB_JOB"] = "integration-test"

    captured = []

    def mock_pushadd(url, job, registry, grouping_key=None, **kwargs):
        captured.append({"job": job, "grouping_key": dict(grouping_key or {})})

    with patch("prometheus_client.pushadd_to_gateway", side_effect=mock_pushadd):
        cicd_metrics_push.push("ci-cd-deploy")

    gk = captured[0]["grouping_key"]
    print(f"  job={captured[0]['job']}, grouping_key={gk}")
    assert "run_id" in gk, "grouping_key 应包含 run_id 键"
    assert "ci_job" in gk, "grouping_key 应包含 ci_job 键"
    assert len(gk) == 2, f"grouping_key 应有 2 个键，实际: {len(gk)} 个 ({list(gk.keys())})"
    print("  ✅ 通过：格式正确（含 run_id + ci_job）")


def test_push_failure_no_raise():
    """测试 4：push 失败不抛异常（不阻塞流水线）。"""
    print("\n[测试 4] push 失败不抛异常")

    os.environ["GITHUB_RUN_ID"] = "run-fail-test"
    os.environ["GITHUB_JOB"] = "stress-test"

    with patch("prometheus_client.pushadd_to_gateway",
               side_effect=ConnectionError("pushgateway 不可达")):
        # [不易] push 失败应被捕获，不抛异常
        cicd_metrics_push.push("ci-cd-build")
    print("  ✅ 通过：push 失败未抛异常")


def test_uses_pushadd_not_push():
    """测试 5：使用 pushadd_to_gateway 而非 push_to_gateway。"""
    print("\n[测试 5] 使用 pushadd_to_gateway 而非 push_to_gateway")

    os.environ["GITHUB_RUN_ID"] = "run-pushadd-test"
    os.environ["GITHUB_JOB"] = "docker-build"

    call_log = {"pushadd": False, "push_to": False}

    def mock_pushadd(*a, **kw):
        call_log["pushadd"] = True

    def mock_push_to(*a, **kw):
        call_log["push_to"] = True

    with patch("prometheus_client.pushadd_to_gateway", side_effect=mock_pushadd), \
         patch("prometheus_client.push_to_gateway", side_effect=mock_push_to):
        cicd_metrics_push.push("ci-cd-test")

    print(f"  pushadd_to_gateway 调用: {call_log['pushadd']}")
    print(f"  push_to_gateway 调用: {call_log['push_to']}")
    assert call_log["pushadd"], "应调用 pushadd_to_gateway"
    assert not call_log["push_to"], "不应调用 push_to_gateway"
    print("  ✅ 通过：使用 pushadd_to_gateway")


def test_concurrent_jobs_no_overwrite():
    """测试 6：同 run_id 不同 ci_job 的并行 job 不互相覆盖（并发安全）。"""
    print("\n[测试 6] 同 run_id 不同 ci_job 的并行 job 不覆盖（并发安全）")

    # [修复 CHG-2026-0731] 模拟 ci-cd.yml 中 3 个并行 job 都用 --stage test
    os.environ["GITHUB_RUN_ID"] = "run-concurrent-001"

    captured = []

    def mock_pushadd(url, job, registry, grouping_key=None, **kwargs):
        captured.append({"job": job, "grouping_key": dict(grouping_key or {})})

    with patch("prometheus_client.pushadd_to_gateway", side_effect=mock_pushadd):
        # 模拟 3 个并行 job 都用 --stage test（push job 名相同）
        os.environ["GITHUB_JOB"] = "stress-test"
        cicd_metrics_push.push("ci-cd-test")

        os.environ["GITHUB_JOB"] = "integration-test"
        cicd_metrics_push.push("ci-cd-test")

        os.environ["GITHUB_JOB"] = "circuit-breaker-inspection"
        cicd_metrics_push.push("ci-cd-test")

    gk1 = captured[0]["grouping_key"]
    gk2 = captured[1]["grouping_key"]
    gk3 = captured[2]["grouping_key"]

    print(f"  stress-test:            {gk1}")
    print(f"  integration-test:       {gk2}")
    print(f"  circuit-breaker:        {gk3}")

    # [不易] 3 个 job 的 grouping_key 应互不相同（ci_job 不同）
    assert gk1 != gk2, "stress-test 和 integration-test 的 grouping_key 应不同"
    assert gk2 != gk3, "integration-test 和 circuit-breaker 的 grouping_key 应不同"
    assert gk1 != gk3, "stress-test 和 circuit-breaker 的 grouping_key 应不同"
    # [不易] run_id 相同（同一 CI 运行）
    assert gk1["run_id"] == gk2["run_id"] == gk3["run_id"] == "run-concurrent-001"
    # [不易] ci_job 不同
    assert gk1["ci_job"] == "stress-test"
    assert gk2["ci_job"] == "integration-test"
    assert gk3["ci_job"] == "circuit-breaker-inspection"
    print("  ✅ 通过：同 run_id 不同 ci_job 的并行 job grouping_key 独立，不覆盖")


def main():
    """运行所有测试。"""
    print("=" * 60)
    print("CI/CD grouping_key 本地验证测试")
    print("=" * 60)

    tests = [
        test_grouping_key_distinct_per_run,
        test_grouping_key_fallback_local,
        test_grouping_key_format,
        test_push_failure_no_raise,
        test_uses_pushadd_not_push,
        test_concurrent_jobs_no_overwrite,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"  ❌ 失败: {e}")
            failed += 1

    print("\n" + "=" * 60)
    print(f"测试结果: {passed} 通过, {failed} 失败")
    print("=" * 60)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
