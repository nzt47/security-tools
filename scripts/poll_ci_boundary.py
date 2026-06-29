#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CI 轮询脚本：自动监控 GitHub Actions 中 boundary-coverage-check 任务状态。

功能：
1. 轮询 GitHub API 获取 observability-ci.yml 最近运行
2. 定位 boundary-coverage-check job
3. 等待 job 完成后获取日志
4. 提取并输出最终覆盖率结果

使用方式：
    # 未认证模式（速率限制 60 次/小时）
    python scripts/poll_ci_boundary.py

    # 带 token 认证（速率限制 5000 次/小时）
    python scripts/poll_ci_boundary.py --token ghp_xxxxx

    # 指定轮询间隔（默认 30 秒）
    python scripts/poll_ci_boundary.py --interval 60

    # 指定最大等待时间（默认 30 分钟）
    python scripts/poll_ci_boundary.py --timeout 1800

可观测性约束：
    - 结构化日志：包含 trace_id/module_name/action/duration_ms
    - 边界显性化：网络/API 失败时抛出带明确错误码的异常
    - 埋点预留：关键操作点预留 trackEvent 占位符
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from typing import Any, Dict, Optional

# ==============================================================================
# 可观测性：结构化日志
# ==============================================================================
def log(action: str, **kwargs) -> None:
    """输出 JSON 格式的结构化日志。"""
    entry = {
        "trace_id": kwargs.pop("trace_id", f"poll_{int(time.time())}"),
        "module_name": "ci_poller",
        "action": action,
        "timestamp": datetime.now().isoformat(),
        "duration_ms": kwargs.pop("duration_ms", 0),
        **kwargs,
    }
    print(json.dumps(entry, ensure_ascii=False), flush=True)


def trackEvent(event_name: str, payload: Dict[str, Any]) -> None:
    """埋点预留：关键用户交互点的追踪调用占位符。"""
    # 实际环境中对接 BusinessMetricsCollector.record_* 方法
    pass


# ==============================================================================
# GitHub API 客户端
# ==============================================================================
class GitHubAPIClient:
    """GitHub REST API 客户端，支持认证和未认证模式。"""

    BASE_URL = "https://api.github.com"

    # 错误码定义
    ERR_NETWORK_TIMEOUT = "GITHUB_API_NETWORK_TIMEOUT"
    ERR_RATE_LIMIT = "GITHUB_API_RATE_LIMIT"
    ERR_NOT_FOUND = "GITHUB_API_NOT_FOUND"
    ERR_AUTH_FAILED = "GITHUB_API_AUTH_FAILED"
    ERR_UNKNOWN = "GITHUB_API_UNKNOWN"

    def __init__(self, token: Optional[str] = None, repo: str = "nzt47/security-tools"):
        self.token = token
        self.repo = repo
        self.headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ci-boundary-poller/1.0",
        }
        if token:
            self.headers["Authorization"] = f"Bearer {token}"

    def _request(self, url: str, timeout: int = 15) -> Dict[str, Any]:
        """发送 GET 请求并返回 JSON 响应。"""
        start = time.time()
        try:
            req = urllib.request.Request(url, headers=self.headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                remaining = resp.headers.get("X-RateLimit-Remaining", "?")
                log(
                    "api_request.success",
                    url=url.split("api.github.com")[-1],
                    rate_limit_remaining=remaining,
                    duration_ms=int((time.time() - start) * 1000),
                )
                return data
        except urllib.error.HTTPError as e:
            if e.code == 403 and "rate limit" in str(e).lower():
                raise RuntimeError(f"[{self.ERR_RATE_LIMIT}] GitHub API 速率限制，请稍后重试或使用 --token 认证")
            elif e.code == 401:
                raise RuntimeError(f"[{self.ERR_AUTH_FAILED}] GitHub API 认证失败，请检查 token")
            elif e.code == 404:
                raise RuntimeError(f"[{self.ERR_NOT_FOUND}] 资源不存在: {url}")
            else:
                body = e.read().decode("utf-8", errors="replace")[:200]
                raise RuntimeError(f"[{self.ERR_UNKNOWN}] HTTP {e.code}: {body}")
        except urllib.error.URLError as e:
            raise RuntimeError(f"[{self.ERR_NETWORK_TIMEOUT}] 网络请求失败: {e.reason}")
        except Exception as e:
            raise RuntimeError(f"[{self.ERR_UNKNOWN}] 未知错误: {e}")

    def get_workflow_runs(self, workflow_file: str = "observability-ci.yml", per_page: int = 5) -> Dict[str, Any]:
        """获取指定 workflow 的最近运行列表。"""
        url = f"{self.BASE_URL}/repos/{self.repo}/actions/workflows/{workflow_file}/runs?per_page={per_page}"
        return self._request(url)

    def get_workflow_run_jobs(self, run_id: int) -> Dict[str, Any]:
        """获取指定 run 的所有 job。"""
        url = f"{self.BASE_URL}/repos/{self.repo}/actions/runs/{run_id}/jobs"
        return self._request(url)

    def get_job_logs(self, job_id: int) -> str:
        """获取指定 job 的日志（返回纯文本）。"""
        url = f"{self.BASE_URL}/repos/{self.repo}/actions/jobs/{job_id}/logs"
        start = time.time()
        try:
            req = urllib.request.Request(url, headers=self.headers)
            with urllib.request.urlopen(req, timeout=30) as resp:
                logs = resp.read().decode("utf-8", errors="replace")
                log(
                    "get_job_logs.success",
                    job_id=job_id,
                    log_size=len(logs),
                    duration_ms=int((time.time() - start) * 1000),
                )
                return logs
        except Exception as e:
            # 日志可能因权限不足而无法获取（未认证模式）
            log("get_job_logs.failed", job_id=job_id, error=str(e), duration_ms=int((time.time() - start) * 1000))
            return ""


# ==============================================================================
# 覆盖率结果解析器
# ==============================================================================
class BoundaryCoverageParser:
    """从 CI 日志中解析 boundary_test_coverage 结果。"""

    # 日志中的覆盖率模式
    PATTERNS = [
        # "边界测试覆盖率: 12.2% (total=3801, boundary=462, status=warn)"
        r"边界测试覆盖率:\s*([\d.]+)%\s*\(total=(\d+),\s*boundary=(\d+),\s*status=(\w+)\)",
        # "✅ 边界测试覆盖率 12.2% 达标（阈值 5%）"
        r"边界测试覆盖率\s*([\d.]+)%\s*达标.*阈值\s*(\d+)%",
        # "❌ 边界测试覆盖率 3.2% 低于阈值 5%，阻断合并"
        r"边界测试覆盖率\s*([\d.]+)%\s*低于阈值\s*(\d+)%",
    ]

    @classmethod
    def parse(cls, logs: str) -> Optional[Dict[str, Any]]:
        """从日志文本中解析覆盖率结果。"""
        for pattern in cls.PATTERNS:
            match = re.search(pattern, logs)
            if match:
                groups = match.groups()
                result = {
                    "coverage_percent": float(groups[0]),
                    "matched_pattern": pattern[:50] + "...",
                    "raw_match": match.group(0),
                }
                if len(groups) >= 3:
                    result["total_tests"] = int(groups[1])
                    result["boundary_tests"] = int(groups[2])
                    result["status"] = groups[3] if len(groups) >= 4 else "unknown"
                elif len(groups) >= 2:
                    result["threshold"] = int(groups[1])
                return result
        return None


# ==============================================================================
# CI 轮询器
# ==============================================================================
class CIBoundaryPoller:
    """轮询 CI 直到 boundary-coverage-check job 完成。"""

    def __init__(
        self,
        client: GitHubAPIClient,
        interval: int = 30,
        timeout: int = 1800,
        workflow_file: str = "observability-ci.yml",
    ):
        self.client = client
        self.interval = interval
        self.timeout = timeout
        self.workflow_file = workflow_file

    def find_latest_run(self) -> Optional[Dict[str, Any]]:
        """查找最新的 workflow run。"""
        data = self.client.get_workflow_runs(self.workflow_file)
        runs = data.get("workflow_runs", [])
        if not runs:
            log("find_latest_run.no_runs", workflow_file=self.workflow_file)
            return None
        latest = runs[0]
        log(
            "find_latest_run.success",
            run_id=latest["id"],
            run_number=latest["run_number"],
            status=latest["status"],
            conclusion=latest.get("conclusion", "null"),
            head_commit=latest["head_commit"]["message"].split("\n")[0][:80],
            created_at=latest["created_at"],
        )
        return latest

    def find_boundary_job(self, run_id: int) -> Optional[Dict[str, Any]]:
        """在指定 run 中查找 boundary-coverage-check job。"""
        data = self.client.get_workflow_run_jobs(run_id)
        jobs = data.get("jobs", [])
        for job in jobs:
            if "boundary" in job.get("name", "").lower() or "boundary" in job.get("id", ""):
                log(
                    "find_boundary_job.success",
                    run_id=run_id,
                    job_id=job["id"],
                    job_name=job["name"],
                    status=job["status"],
                    conclusion=job.get("conclusion", "null"),
                )
                return job
        # 如果没找到精确匹配，尝试模糊匹配
        for job in jobs:
            if "边界" in job.get("name", ""):
                log(
                    "find_boundary_job.fuzzy_match",
                    run_id=run_id,
                    job_id=job["id"],
                    job_name=job["name"],
                )
                return job
        log("find_boundary_job.not_found", run_id=run_id, total_jobs=len(jobs))
        return None

    def poll(self) -> Dict[str, Any]:
        """主轮询循环。"""
        start_time = time.time()
        trackEvent("ci_poll_started", {"workflow": self.workflow_file})

        log("poll.started", workflow_file=self.workflow_file, interval=self.interval, timeout=self.timeout)

        # 1. 查找最新 run
        run = self.find_latest_run()
        if not run:
            raise RuntimeError(f"[POLL_NO_RUN] 未找到 {self.workflow_file} 的运行记录")

        run_id = run["id"]
        run_status = run["status"]

        # 2. 如果 run 已完成，直接获取结果
        if run_status == "completed":
            log("poll.already_completed", run_id=run_id, conclusion=run.get("conclusion"))
            return self._collect_result(run, self.find_boundary_job(run_id))

        # 3. 轮询等待 run 完成
        elapsed = 0
        while elapsed < self.timeout:
            job = self.find_boundary_job(run_id)

            if job:
                job_status = job["status"]
                if job_status == "completed":
                    log("poll.job_completed", run_id=run_id, job_id=job["id"], conclusion=job.get("conclusion"))
                    trackEvent("ci_poll_completed", {"run_id": run_id, "conclusion": job.get("conclusion")})
                    return self._collect_result(run, job)
                else:
                    log("poll.waiting", run_id=run_id, job_status=job_status, elapsed=elapsed, remaining=self.timeout - elapsed)
            else:
                # job 可能还没启动
                log("poll.job_not_started", run_id=run_id, elapsed=elapsed)

            time.sleep(self.interval)
            elapsed = int(time.time() - start_time)

        # 4. 超时
        trackEvent("ci_poll_timeout", {"run_id": run_id, "elapsed": elapsed})
        raise RuntimeError(f"[POLL_TIMEOUT] 等待 {self.timeout}s 后超时，run_id={run_id}")

    def _collect_result(self, run: Dict[str, Any], job: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """收集最终结果。"""
        result = {
            "run_id": run["id"],
            "run_number": run["run_number"],
            "run_status": run["status"],
            "run_conclusion": run.get("conclusion"),
            "run_url": run.get("html_url", ""),
            "head_commit": run["head_commit"]["message"].split("\n")[0][:100],
            "created_at": run["created_at"],
            "job": None,
            "boundary_coverage": None,
            "logs_excerpt": "",
        }

        if job:
            result["job"] = {
                "job_id": job["id"],
                "job_name": job["name"],
                "job_status": job["status"],
                "job_conclusion": job.get("conclusion"),
                "job_url": job.get("html_url", ""),
            }

            # 尝试获取日志
            logs = self.client.get_job_logs(job["id"])
            if logs:
                # 解析覆盖率
                parsed = BoundaryCoverageParser.parse(logs)
                if parsed:
                    result["boundary_coverage"] = parsed
                    log("result.parsed", **parsed)
                else:
                    log("result.parse_failed", reason="no pattern matched in logs")

                # 提取关键日志行
                key_lines = []
                for line in logs.split("\n"):
                    if any(kw in line for kw in ["边界测试覆盖率", "boundary_pass", "5%", "阈值", "boundary_count"]):
                        key_lines.append(line.strip())
                result["logs_excerpt"] = "\n".join(key_lines[:20])
            else:
                log("result.no_logs", reason="logs may require authentication")

        return result


# ==============================================================================
# 主入口
# ==============================================================================
def main():
    parser = argparse.ArgumentParser(description="轮询 GitHub Actions boundary-coverage-check 任务")
    parser.add_argument("--token", default=os.environ.get("GITHUB_TOKEN", ""), help="GitHub token（可选）")
    parser.add_argument("--repo", default="nzt47/security-tools", help="GitHub 仓库 (owner/repo)")
    parser.add_argument("--interval", type=int, default=30, help="轮询间隔（秒）")
    parser.add_argument("--timeout", type=int, default=1800, help="最大等待时间（秒）")
    parser.add_argument("--workflow", default="observability-ci.yml", help="workflow 文件名")
    parser.add_argument("--output", default="", help="结果输出文件路径（JSON）")
    args = parser.parse_args()

    try:
        client = GitHubAPIClient(token=args.token if args.token else None, repo=args.repo)
        poller = CIBoundaryPoller(client, interval=args.interval, timeout=args.timeout, workflow_file=args.workflow)

        result = poller.poll()

        # 输出结果摘要
        print("\n" + "=" * 70)
        print("📊 boundary-coverage-check 最终结果")
        print("=" * 70)
        print(f"Run #{result['run_number']} (ID: {result['run_id']})")
        print(f"Commit: {result['head_commit']}")
        print(f"Run 结论: {result['run_conclusion']}")

        if result.get("job"):
            job = result["job"]
            print(f"\nJob: {job['job_name']}")
            print(f"Job 状态: {job['job_status']}")
            print(f"Job 结论: {job['job_conclusion']}")
            print(f"Job URL: {job['job_url']}")

        if result.get("boundary_coverage"):
            cov = result["boundary_coverage"]
            print(f"\n✅ 边界测试覆盖率: {cov['coverage_percent']}%")
            if "total_tests" in cov:
                print(f"   总测试数: {cov['total_tests']}")
                print(f"   边界测试数: {cov['boundary_tests']}")
                print(f"   状态: {cov['status']}")
            print(f"   匹配行: {cov['raw_match']}")
        else:
            print("\n⚠️ 未从日志中解析到覆盖率数据")
            if result.get("logs_excerpt"):
                print("\n关键日志摘要:")
                print(result["logs_excerpt"])
            else:
                print("（日志可能需要认证才能获取，请使用 --token 参数）")

        print("=" * 70)

        # 保存 JSON 结果
        output_path = args.output or f"docs/observability/ci_boundary_result_{result['run_id']}.json"
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"\n结果已保存到: {output_path}")

        # 根据结论设置退出码
        if result.get("boundary_coverage"):
            coverage = result["boundary_coverage"]["coverage_percent"]
            exit(0 if coverage >= 5.0 else 1)
        elif result.get("job", {}).get("job_conclusion") == "success":
            exit(0)
        else:
            exit(2)

    except RuntimeError as e:
        print(f"\n❌ 轮询失败: {e}", file=sys.stderr)
        log("poll.failed", error=str(e))
        exit(3)
    except KeyboardInterrupt:
        print("\n\n⏹ 用户中断轮询", file=sys.stderr)
        exit(130)


if __name__ == "__main__":
    main()
