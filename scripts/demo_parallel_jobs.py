"""
并行 job 演示脚本：模拟 GitHub Actions 中 3 个 job 并行推送 metrics

[用途] 模拟 ci-cd.yml 中 stress-test / integration-test / circuit-breaker-inspection
       三个 job 并行执行 --stage test 的场景，展示 grouping_key={run_id, ci_job} 的效果。
[不易] 使用 subprocess 模拟真实并行（每个进程独立 logger，无竞争）。
[变易] 3 个 job 共享同一 GITHUB_RUN_ID，但 GITHUB_JOB 不同，验证不覆盖。
[简易] 一键运行：python scripts/demo_parallel_jobs.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

# 3 个并行 job 配置（模拟 ci-cd.yml 中 needs: lint-and-typecheck 的 3 个并行 job）
PARALLEL_JOBS = [
    {
        "github_job": "stress-test",
        "stage": "test",
        "extra_args": ["--success"],
        "description": "压力测试（50 并发 × 1000 请求）",
    },
    {
        "github_job": "integration-test",
        "stage": "test",
        "extra_args": ["--success", "--coverage", "87.3"],
        "description": "集成测试（含覆盖率 87.3%）",
    },
    {
        "github_job": "circuit-breaker-inspection",
        "stage": "test",
        "extra_args": ["--success"],
        "description": "熔断器巡检（发布前阻断）",
    },
]

# 同时模拟 build 和 deploy 阶段（串行 job，展示完整流水线）
SERIAL_JOBS = [
    {
        "github_job": "lint-and-typecheck",
        "stage": "build",
        "extra_args": ["--success"],
        "description": "Lint + 类型检查",
    },
    {
        "github_job": "deployment-ready",
        "stage": "deploy",
        "extra_args": ["--success", "--env", "production", "--duration", "145.8"],
        "description": "部署到生产环境（耗时 145.8s）",
    },
]

SCRIPT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cicd_metrics_push.py")
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_job(job_config: dict, run_id: str) -> tuple[str, str, float]:
    """运行单个 job，返回 (stdout, stderr, 耗时)。

    [不易] 每个进程独立，logger 无竞争；pushgateway 不可达时日志仍完整输出。
    """
    env = os.environ.copy()
    env["GITHUB_RUN_ID"] = run_id
    env["GITHUB_JOB"] = job_config["github_job"]
    env["LOG_LEVEL"] = "DEBUG"
    env["PUSHGATEWAY_URL"] = env.get("PUSHGATEWAY_URL", "http://monitoring.internal:9091")

    cmd = [sys.executable, SCRIPT_PATH, "--stage", job_config["stage"]] + job_config["extra_args"]

    start = time.time()
    result = subprocess.run(
        cmd, env=env, capture_output=True, text=True, cwd=PROJECT_ROOT
    )
    duration = time.time() - start

    # [修复] Python logging 默认输出到 stderr，合并 stdout+stderr 供展示和解析
    combined = (result.stdout or "") + (result.stderr or "")
    return combined, result.stderr, duration


def demo_parallel() -> None:
    """演示 3 个并行 job（同 run_id, 不同 ci_job）。"""
    run_id = "run-parallel-demo-%d" % int(time.time())

    print()
    print("╔" + "═" * 76 + "╗")
    print("║" + " 并行 job 演示：3 个 job 同时执行 --stage test".center(76) + "║")
    print("╠" + "═" * 76 + "╣")
    print("║" + " GITHUB_RUN_ID: %s" % run_id.ljust(76 - 16)[:60] + "║")
    print("║" + " 验证: 同 run_id 不同 ci_job 的 grouping_key 独立，不覆盖".center(76) + "║")
    print("╚" + "═" * 76 + "╝")

    # 并行启动 3 个进程
    import threading

    results = {}
    threads = []

    def worker(job_config):
        stdout, stderr, duration = run_job(job_config, run_id)
        results[job_config["github_job"]] = {
            "stdout": stdout,
            "stderr": stderr,
            "duration": duration,
            "config": job_config,
        }

    for job_config in PARALLEL_JOBS:
        t = threading.Thread(target=worker, args=(job_config,))
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    # 展示结果
    print()
    for job_config in PARALLEL_JOBS:
        job_name = job_config["github_job"]
        r = results[job_name]
        print("┌" + "─" * 76 + "┐")
        # [修复] 运算符优先级：%(tuple).ljust() 会把 .ljust 应用到 tuple，
        # 需用括号让 % 先执行，再对结果字符串调用 .ljust
        print(("│ Job: %-40s %s" % (job_name, job_config["description"])).ljust(78)[:76] + "│")
        print("│ 阶段: %-8s  耗时: %.3fs" % (job_config["stage"], r["duration"]))
        print("├" + "─" * 76 + "┤")
        # [修复] agent 包导入后会触发完整模块加载（大量日志），仅展示 [metrics] 关键行
        for line in r["stdout"].strip().split("\n"):
            if line.strip() and "[metrics]" in line:
                print("│ " + line.ljust(75)[:75] + "│")
        print("└" + "─" * 76 + "┘")
        print()

    # 汇总
    print("╔" + "═" * 76 + "╗")
    print("║" + " 并行安全验证结果".center(76) + "║")
    print("╠" + "═" * 76 + "╣")
    grouping_keys = set()
    for job_config in PARALLEL_JOBS:
        job_name = job_config["github_job"]
        stdout = results[job_name]["stdout"]
        # 提取 grouping_key
        for line in stdout.split("\n"):
            if "grouping_key=" in line:
                gk_str = line.split("grouping_key=")[1].strip()
                grouping_keys.add(gk_str)
                print("║  %-35s → %s" % (job_name, gk_str))
                break
    print("║" + "─" * 76 + "║")
    if len(grouping_keys) == len(PARALLEL_JOBS):
        print("║" + " ✅ %d 个 job 的 grouping_key 全部不同，无覆盖".center(76) % len(grouping_keys) + "║")
    else:
        print("║" + " ⚠️  grouping_key 数量: %d（应有 %d）".center(76) % (len(grouping_keys), len(PARALLEL_JOBS)) + "║")
    print("╚" + "═" * 76 + "╝")


def demo_serial() -> None:
    """演示串行 job（build → deploy）。"""
    run_id = "run-serial-demo-%d" % int(time.time())

    print()
    print("╔" + "═" * 76 + "╗")
    print("║" + " 串行 job 演示：build → deploy（同 run_id, 不同 ci_job）".center(76) + "║")
    print("╠" + "═" * 76 + "╣")
    print("║" + " GITHUB_RUN_ID: %s" % run_id.ljust(76 - 16)[:60] + "║")
    print("╚" + "═" * 76 + "╝")

    for job_config in SERIAL_JOBS:
        stdout, stderr, duration = run_job(job_config, run_id)
        print()
        print("┌" + "─" * 76 + "┐")
        print(("│ Job: %-40s %s" % (job_config["github_job"], job_config["description"])).ljust(78)[:76] + "│")
        print("│ 阶段: %-8s  耗时: %.3fs" % (job_config["stage"], duration))
        print("├" + "─" * 76 + "┤")
        # [修复] 仅展示 [metrics] 关键行，过滤模块加载日志
        for line in stdout.strip().split("\n"):
            if line.strip() and "[metrics]" in line:
                print("│ " + line.ljust(75)[:75] + "│")
        print("└" + "─" * 76 + "┘")


def main():
    print("=" * 78)
    print("GitHub Actions 并行 job metrics 推送演示")
    print("=" * 78)
    print()
    print("本脚本模拟 ci-cd.yml 中的 job 执行流程：")
    print("  1. 并行阶段：stress-test / integration-test / circuit-breaker-inspection")
    print("  2. 串行阶段：lint-and-typecheck (build) → deployment-ready (deploy)")
    print()
    print("日志级别: DEBUG（展示完整 5 条日志/job）")
    print("pushgateway: http://monitoring.internal:9091（不可达时展示失败日志）")

    # 1. 并行演示
    demo_parallel()

    # 2. 串行演示
    demo_serial()

    # 3. 总结
    print()
    print("╔" + "═" * 76 + "╗")
    print("║" + " 演示总结".center(76) + "║")
    print("╠" + "═" * 76 + "╣")
    print("║" + " 并行 job: grouping_key={run_id, ci_job} 保证不覆盖".center(76) + "║")
    print("║" + " 串行 job: 不同 stage 的 job 名不同，天然不覆盖".center(76) + "║")
    print("║" + " 日志输出: DEBUG 级别展示 5 条日志（含 registry + 耗时）".center(76) + "║")
    print("║" + " 失败处理: pushgateway 不可达时不阻塞流水线".center(76) + "║")
    print("╚" + "═" * 76 + "╝")

    return 0


if __name__ == "__main__":
    sys.exit(main())
