#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""CI/CD 流水线触发模拟器——模型下载失败告警演练

【不易】忠于 .github/workflows/l3-docker-tests.yml 的实际告警逻辑
【变易】支持多种失败场景模拟（模型下载失败/测试失败/覆盖率不足）
【简易】本地模拟 GitHub Actions 四阶段流程，输出结构化日志

模拟场景：
    1. build-image: 模型预下载失败（三层防护保证构建不阻断）
    2. l3-tests:    测试因模型缺失而失败
    3. coverage-analysis: 覆盖率分析
    4. test-summary: 失败告警（PR 评论 + Slack 通知）

用法:
    python scripts/simulate_ci_pipeline.py
    python scripts/simulate_ci_pipeline.py --scenario model_download_failure
    python scripts/simulate_ci_pipeline.py --scenario all_pass
    python scripts/simulate_ci_pipeline.py --scenario coverage_below_threshold
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path


# =====================================================================
# GitHub Actions 日志格式工具
# =====================================================================

def gh_group(name: str):
    """GitHub Actions 日志分组"""
    print(f"::group::{name}")


def gh_endgroup():
    """结束日志分组"""
    print("::endgroup::")


def gh_error(message: str, file: str = "", line: int = 0):
    """GitHub Actions error workflow command（红色高亮）"""
    if file:
        print(f"::error file={file},line={line}::{message}")
    else:
        print(f"::error::{message}")


def gh_warning(message: str, file: str = ""):
    """GitHub Actions warning workflow command（黄色高亮）"""
    if file:
        print(f"::warning file={file}::{message}")
    else:
        print(f"::warning::{message}")


def gh_notice(message: str):
    """GitHub Actions notice"""
    print(f"::notice::{message}")


def gh_step_summary(content: str):
    """写入 GitHub Step Summary"""
    print(f"::add-masked-output::{content[:50]}...")  # 模拟写入
    print(f"[Step Summary] {content}")


def gh_info(message: str):
    """普通日志"""
    print(message)


def separator():
    print("=" * 70)


# =====================================================================
# 模拟阶段 1: build-image
# =====================================================================

def simulate_build_image(model_download_fails: bool = True) -> dict:
    """模拟 Docker 镜像构建阶段

    【不易】模型预下载失败不阻断构建（三层防护核心不变量）
    """
    gh_group("Job 1: 构建 Docker 镜像")
    separator()
    gh_info("Run actions/checkout@v4")
    gh_info("  ✓ 检出代码完成")
    gh_info("")
    gh_info("Run docker/setup-buildx-action@v3")
    gh_info("  ✓ Buildx 设置完成")
    gh_info("")

    # 模拟镜像元数据
    tag = f"master-a1b2c3d-{datetime.now().strftime('%Y%m%d%H%M')}"
    gh_info(f"构建镜像标签: {tag}")
    gh_info("")

    # ── 模拟 Docker 构建（含模型预下载）──
    gh_info("Run docker/build-push-action@v5")
    gh_info("  context: .")
    gh_info("  file: ./Dockerfile.linux-test")
    gh_info("  tags: agent-test-sqlite-vec:" + tag)
    gh_info("  load: true")
    gh_info("")

    gh_group("Docker Build Log (Layer 1: 模型预下载)")
    gh_info("[15/15] RUN python scripts/predownload_models.py || echo \"[WARN] 模型预下载失败\"")
    gh_info("============================================================")
    gh_info("预下载 HuggingFace embedding 模型")
    gh_info("============================================================")
    gh_info(f"缓存目录: /app/.hf_cache")
    gh_info("模型列表: ['paraphrase-multilingual-MiniLM-L12-v2', 'all-MiniLM-L6-v2', 'BAAI/bge-small-zh-v1.5']")
    gh_info("超时秒数: 300")
    gh_info("")

    if model_download_fails:
        # 模拟模型下载失败
        models = [
            ("paraphrase-multilingual-MiniLM-L12-v2", 31.7),
            ("all-MiniLM-L6-v2", 31.7),
            ("BAAI/bge-small-zh-v1.5", 33.2),
        ]
        for model_name, elapsed in models:
            gh_info(f"  [下载] {model_name} ... FAILED ({elapsed}s): Connection refused")
        gh_info("")
        gh_info("预下载完成: 0/3 成功")
        gh_info("失败模型: ['paraphrase-multilingual-MiniLM-L12-v2', 'all-MiniLM-L6-v2', 'BAAI/bge-small-zh-v1.5']")
        gh_warning("部分模型下载失败，测试时可能需要网络访问")
        gh_info("")

        # Layer 2: Dockerfile || echo 兜底
        gh_info("[WARN] 模型预下载失败")
        gh_notice("Layer 2 防护触发: Dockerfile `|| echo` 兜底，构建继续")

        # Layer 1: sys.exit(0) 不阻断
        gh_notice("Layer 1 防护生效: predownload_models.py sys.exit(0)，退出码为 0")
    else:
        gh_info("  [下载] paraphrase-multilingual-MiniLM-L12-v2 ... OK (45.2s, dim=384, 120.0MB)")
        gh_info("  [下载] all-MiniLM-L6-v2 ... OK (38.1s, dim=384, 90.0MB)")
        gh_info("  [下载] BAAI/bge-small-zh-v1.5 ... OK (52.3s, dim=512, 95.0MB)")
        gh_info("")
        gh_info("预下载完成: 3/3 成功")

    gh_endgroup()

    # ── 构建结果 ──
    gh_info("")
    gh_info("=== 验证镜像 ===")
    gh_info("agent-test-sqlite-vec   latest    a1b2c3d4e5f6   2 minutes ago   3.2GB")
    gh_info("")
    gh_info("=== 验证关键模块可导入 ===")
    gh_info("sqlite-vec: 0.1.9")
    gh_info("torch: 2.12.0+cu130")
    gh_info("sentence-transformers: OK")
    gh_info("LongTermMemory: OK")
    gh_info("")

    # Layer 3: HEALTHCHECK 状态
    gh_group("Layer 3: 运行时健康检查 (HEALTHCHECK)")
    if model_download_fails:
        gh_warning("HEALTHCHECK: 模型缓存目录为空，容器标记为 unhealthy")
        gh_info("  状态: unhealthy")
        gh_info("  检查命令: python -c 'from agent.utils.docker_fault_tolerance import health_check_resources; ...'")
        gh_info("  结果: 3 个模型缓存均缺失")
        gh_notice("Layer 3 防护生效: 标记 unhealthy，但不阻断构建")
    else:
        gh_info("HEALTHCHECK: 模型缓存完整，容器 healthy")
    gh_endgroup()

    gh_info("")
    build_success = True  # 构建始终成功（三层防护核心不变量）
    gh_info(f"✅ Job 1 完成: build-image ({'SUCCESS' if build_success else 'FAILURE'})")
    gh_endgroup()
    print()

    return {
        "job": "build-image",
        "result": "success" if build_success else "failure",
        "tag": tag,
        "model_download_succeeded": not model_download_fails,
        "layer1_triggered": model_download_fails,  # sys.exit(0)
        "layer2_triggered": model_download_fails,  # || echo
        "layer3_triggered": model_download_fails,  # HEALTHCHECK unhealthy
    }


# =====================================================================
# 模拟阶段 2: l3-tests
# =====================================================================

def simulate_l3_tests(model_available: bool, test_mode: str = "sqlite-vec") -> dict:
    """模拟 L3 测试执行阶段"""
    gh_group(f"Job 2: L3 回归测试 ({test_mode})")
    separator()
    gh_info("Run actions/checkout@v4")
    gh_info("Run docker/setup-buildx-action@v3")
    gh_info("")

    gh_info("创建测试结果目录: test-results coverage-report")
    gh_info("")

    gh_group(f"运行 L3 测试 ({test_mode} 模式)")
    gh_info("docker run --rm \\")
    gh_info("  -v tests:/app/tests:ro \\")
    gh_info("  -v coverage-report:/app/htmlcov \\")
    gh_info("  -v test-results:/app/test_results \\")
    gh_info("  --entrypoint bash \\")
    gh_info("  agent-test-sqlite-vec:latest \\")
    gh_info("  -c 'pip install pytest-timeout pytest-cov -q && python -m pytest ...'")
    gh_info("")

    if not model_available:
        # 模型缺失导致测试失败
        gh_info("========================= test session starts ==========================")
        gh_info("platform linux -- Python 3.12.0, pytest-7.4.0, plasm-0.1.0")
        gh_info("rootdir: /app, configfile: pytest.ini")
        gh_info("plugins: timeout-2.1.0, cov-4.1.0")
        gh_info("")
        gh_info("collecting ... collected 130 items")
        gh_info("")

        # 模拟部分测试失败
        failed_tests = [
            ("tests/unit/test_memory_vector_store.py::TestVectorStore::test_init_with_model",
             "OSError: Cannot connect to huggingface.co"),
            ("tests/unit/test_vector_store_sqlite_vec.py::TestSqliteVec::test_knn_search",
             "RuntimeError: Model not found in cache"),
            ("tests/unit/test_long_term_memory_embedding.py::TestEmbedding::test_search_semantic",
             "ConnectionError: Failed to download model"),
        ]
        passed = 127
        failed = len(failed_tests)

        for test, error in failed_tests:
            gh_info(f"FAILED {test}")
            gh_error(error, file=test.split("::")[0], line=1)
            gh_info("")

        gh_info(f"========================= {passed} passed, {failed} failed in 45.2s ==========================")
        gh_error(f"L3 测试失败: {failed} 个测试因模型缺失而失败")
    else:
        gh_info("========================= test session starts ==========================")
        gh_info("collected 130 items")
        gh_info("")
        gh_info("tests/unit/test_long_term_memory_embedding.py ......  [ 12%]")
        gh_info("tests/unit/test_tlm_memory_store.py ...............  [ 28%]")
        gh_info("tests/unit/test_memory_storage_boundary.py ........  [ 42%]")
        gh_info("tests/unit/test_vector_store_sqlite_vec.py ........  [ 58%]")
        gh_info("tests/unit/test_memory_vector_store.py ............  [100%]")
        gh_info("")
        gh_info("========================= 130 passed in 89.5s ==========================")
        gh_notice("✅ 所有 130 个测试通过")

    gh_endgroup()

    # 测试结果解析
    gh_group("解析测试结果")
    if not model_available:
        gh_info("测试总数: 130")
        gh_info("通过: 127")
        gh_info("失败: 3")
        gh_info("耗时: 45.2s")
        gh_error("测试失败！")
    else:
        gh_info("测试总数: 130")
        gh_info("通过: 130")
        gh_info("失败: 0")
        gh_info("耗时: 89.5s")
        gh_info("✅ 所有测试通过！")
    gh_endgroup()

    result = "success" if model_available else "failure"
    gh_info(f"\n{'✅' if model_available else '❌'} Job 2 完成: l3-tests ({result.upper()})")
    gh_endgroup()
    print()

    return {
        "job": "l3-tests",
        "result": result,
        "total": 130,
        "passed": 130 if model_available else 127,
        "failed": 0 if model_available else 3,
    }


# =====================================================================
# 模拟阶段 3: coverage-analysis
# =====================================================================

def simulate_coverage_analysis(test_passed: bool, coverage_below_threshold: bool = False) -> dict:
    """模拟覆盖率分析阶段"""
    gh_group("Job 3: 覆盖率分析")
    separator()
    gh_info("Run actions/checkout@v4")
    gh_info("Run actions/download-artifact@v4")
    gh_info("  name: coverage-report-sqlite-vec")
    gh_info("  path: coverage-data/")
    gh_info("")

    gh_group("分析核心模块覆盖率")
    if not test_passed:
        gh_warning("coverage.xml 不完整（测试失败，覆盖率数据可能缺失）")
        gh_info("")

    # 模拟覆盖率数据
    modules = [
        ("LongTermMemory", 75.8, True),
        ("VectorStore", 44.0 if not test_passed else 62.0, True),
        ("SqliteVecBackend", 89.0, True),
        ("EnvConfigManager", 0.0, True),
        ("NetworkConfig", 70.3, False),
    ]

    gh_info("| 模块 | 覆盖率 | 阈值 | 状态 |")
    gh_info("|------|--------|------|------|")

    below_threshold = []
    for name, pct, critical in modules:
        status = "✅" if pct >= 80 else "❌"
        gh_info(f"| {name} | {pct:.1f}% | 80% | {status} |")
        if pct < 80:
            below_threshold.append((name, pct))

    if below_threshold:
        gh_info("")
        gh_warning("覆盖率不足 80% 的核心模块：")
        for name, pct in below_threshold:
            gh_warning(f"  - {name}: {pct:.1f}%")
        gh_info("")
        gh_notice("建议补充测试用例提升覆盖率")
    else:
        gh_info("")
        gh_notice("✅ 所有核心模块覆盖率 ≥ 80%")

    gh_endgroup()

    result = "success"  # 覆盖率分析不阻断（仅告警）
    if coverage_below_threshold:
        result = "failure"

    gh_info(f"\n✅ Job 3 完成: coverage-analysis ({result.upper()})")
    gh_endgroup()
    print()

    return {
        "job": "coverage-analysis",
        "result": result,
        "below_threshold": below_threshold,
    }


# =====================================================================
# 模拟阶段 4: test-summary（告警通知）
# =====================================================================

def simulate_test_summary(build_result: dict, test_result: dict, cov_result: dict,
                          event_name: str = "pull_request") -> dict:
    """模拟测试总结与通知阶段（告警核心）"""
    gh_group("Job 4: 测试总结与通知")
    separator()

    build_status = build_result["result"]
    test_status = test_result["result"]
    cov_status = cov_result["result"]

    # ── Step Summary ──
    summary_lines = [
        "## L3 Docker 测试总结",
        "",
        "| 阶段 | 结果 |",
        "|------|------|",
        f"| 镜像构建 | {build_status} |",
        f"| L3 测试 | {test_status} |",
        f"| 覆盖率分析 | {cov_status} |",
        "",
    ]

    overall_failed = test_status == "failure"
    if build_status == "failure":
        summary_lines.append("❌ Docker 镜像构建失败")
    elif test_status == "failure":
        summary_lines.append("❌ L3 测试失败")
    else:
        summary_lines.append("✅ 所有阶段通过")

    for line in summary_lines:
        gh_step_summary(line)

    gh_info("Step Summary 已写入 $GITHUB_STEP_SUMMARY")
    gh_info("")

    # ── PR 评论通知（失败时）──
    if overall_failed and event_name == "pull_request":
        gh_group("PR 评论通知（失败时触发）")
        gh_info("Run actions/github-script@v7")

        pr_comment_body = [
            "## ❌ L3 Docker 测试失败",
            "",
            "**失败阶段**:",
            f"- 镜像构建: {build_status}",
            f"- L3 测试: {test_status}",
            f"- 覆盖率分析: {cov_status}",
            "",
            f"**查看详情**: [Workflow Run](https://github.com/owner/repo/actions/runs/123456)",
            "",
            "**排查建议**:",
            "1. 检查 Docker 构建日志（模型预下载失败不阻断构建）",
            "2. 检查 L3 测试输出中的 FAILED 项",
            "3. 检查覆盖率报告是否正常生成",
            "",
            "**模型下载失败详情**:",
        ]

        if build_result.get("layer1_triggered"):
            pr_comment_body.extend([
                "- Layer 1 防护触发: `predownload_models.py` 中 `sys.exit(0)` 生效",
                "- Layer 2 防护触发: Dockerfile `|| echo` 兜底生效",
                "- Layer 3 防护触发: HEALTHCHECK 标记容器 unhealthy",
                "",
                "**根因**: HuggingFace 网络不通，3 个模型下载均失败",
                "**影响**: 测试运行时无法加载模型，3 个依赖模型的测试失败",
                "",
                "**修复建议**:",
                "- 本地预下载模型后重新构建: `python scripts/predownload_models.py`",
                "- 或设置 HF_HUB_OFFLINE=1 跳过模型加载（仅测试非模型路径）",
            ])

        gh_info("PR 评论内容:")
        for line in pr_comment_body:
            gh_info(f"  {line}")

        gh_info("")
        gh_notice("✅ PR 评论已发送到 Pull Request #123")
        gh_endgroup()

    # ── Slack 通知 ──
    gh_group("Slack 通知")
    gh_info("Run slackapi/slack-github-action@v1.24.0")

    color = "#36a64f" if not overall_failed else "#ff0000"
    gh_info("Slack payload:")
    gh_info(json.dumps({
        "text": f"L3 Docker 测试 {'success' if not overall_failed else 'failure'}",
        "attachments": [{
            "color": color,
            "fields": [
                {"title": "状态", "value": "failure" if overall_failed else "success"},
                {"title": "分支", "value": "master"},
                {"title": "触发者", "value": "github-actions[bot]"},
                {"title": "构建", "value": build_status},
                {"title": "测试", "value": test_status},
                {"title": "模型下载", "value": "失败 (3/3)" if build_result.get("layer1_triggered") else "成功"},
            ]
        }]
    }, indent=2, ensure_ascii=False))

    if overall_failed:
        gh_notice("✅ Slack 告警已发送到 #ci-alerts 频道")
    else:
        gh_notice("✅ Slack 成功通知已发送")
    gh_endgroup()

    # ── 最终结果 ──
    gh_info("")
    separator()
    if overall_failed:
        gh_error("❌ L3 Docker 测试流水线失败！")
        gh_info("")
        gh_info("告警已通过以下渠道发送：")
        gh_info("  1. GitHub PR 评论（含失败详情和修复建议）")
        gh_info("  2. Slack #ci-alerts 频道（含状态摘要）")
        gh_info("  3. GitHub Step Summary（PR 检查页面底部）")
    else:
        gh_notice("✅ L3 Docker 测试流水线全部通过！")
    separator()

    gh_endgroup()

    return {
        "job": "test-summary",
        "result": "failure" if overall_failed else "success",
        "notifications_sent": ["pr_comment", "slack"] if overall_failed else ["slack"],
        "overall_status": "failure" if overall_failed else "success",
    }


# =====================================================================
# 主流程
# =====================================================================

def run_simulation(scenario: str = "model_download_failure"):
    """运行 CI/CD 模拟

    Args:
        scenario: 模拟场景
            - model_download_failure: 模型下载失败（默认）
            - all_pass: 全部通过
            - coverage_below_threshold: 覆盖率不足
    """
    print()
    print("█" * 70)
    print("█  CI/CD 流水线触发模拟器")
    print("█  工作流: L3 Docker Tests (.github/workflows/l3-docker-tests.yml)")
    print(f"█  场景: {scenario}")
    print("█" * 70)
    print()

    start_time = time.time()

    # ── 场景配置 ──
    if scenario == "model_download_failure":
        model_fails = True
        coverage_below = False
    elif scenario == "all_pass":
        model_fails = False
        coverage_below = False
    elif scenario == "coverage_below_threshold":
        model_fails = False
        coverage_below = True
    else:
        print(f"未知场景: {scenario}")
        sys.exit(1)

    # ── 阶段 1: build-image ──
    build_result = simulate_build_image(model_download_fails=model_fails)

    # ── 阶段 2: l3-tests ──
    test_result = simulate_l3_tests(model_available=not model_fails)

    # ── 阶段 3: coverage-analysis ──
    cov_result = simulate_coverage_analysis(
        test_passed=test_result["result"] == "success",
        coverage_below_threshold=coverage_below,
    )

    # ── 阶段 4: test-summary ──
    summary_result = simulate_test_summary(
        build_result=build_result,
        test_result=test_result,
        cov_result=cov_result,
        event_name="pull_request",
    )

    elapsed = time.time() - start_time

    # ── 模拟结果汇总 ──
    print()
    print("█" * 70)
    print("█  模拟结果汇总")
    print("█" * 70)
    print(f"  模拟耗时: {elapsed:.1f}s")
    print(f"  整体状态: {summary_result['overall_status'].upper()}")
    print()
    print("  各阶段结果:")
    print(f"    1. build-image:        {build_result['result'].upper()}"
          + (f" (三层防护: L1={build_result['layer1_triggered']}, L2={build_result['layer2_triggered']}, L3={build_result['layer3_triggered']})"
             if model_fails else ""))
    print(f"    2. l3-tests:           {test_result['result'].upper()}"
          + (f" ({test_result['passed']}/{test_result['total']} passed)" if test_result['result'] == 'failure' else f" ({test_result['total']} passed)"))
    print(f"    3. coverage-analysis:  {cov_result['result'].upper()}"
          + (f" ({len(cov_result['below_threshold'])} modules below 80%)" if cov_result['below_threshold'] else ""))
    print(f"    4. test-summary:       {summary_result['result'].upper()}"
          + f" (通知: {', '.join(summary_result['notifications_sent'])})")
    print()

    if model_fails:
        print("  告警演练结论:")
        print("    ✅ Layer 1 (脚本层): sys.exit(0) 生效，构建未阻断")
        print("    ✅ Layer 2 (Dockerfile层): || echo 兜底生效")
        print("    ✅ Layer 3 (运行时层): HEALTHCHECK 标记 unhealthy")
        print("    ✅ PR 评论: 含失败详情 + 根因分析 + 修复建议")
        print("    ✅ Slack 通知: 含状态摘要 + 颜色标识")
        print("    ✅ Step Summary: PR 检查页面底部可见")
    print("█" * 70)


def main():
    parser = argparse.ArgumentParser(
        description="CI/CD 流水线触发模拟器——模型下载失败告警演练"
    )
    parser.add_argument(
        "--scenario",
        choices=["model_download_failure", "all_pass", "coverage_below_threshold"],
        default="model_download_failure",
        help="模拟场景（默认: model_download_failure）",
    )
    args = parser.parse_args()

    run_simulation(scenario=args.scenario)


if __name__ == "__main__":
    main()
