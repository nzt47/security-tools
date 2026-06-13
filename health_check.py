"""云枢系统接口健康检查脚本

定期检测所有 API 端点的可用性，生成结构化报告。
支持单次运行和定时循环两种模式。

用法：
    # 单次检查并输出报告
    python health_check.py

    # 定时循环检查（每 60 秒一次）
    python health_check.py --interval 60

    # 指定服务器地址
    python health_check.py --host http://192.168.1.100:5678

    # 仅输出 JSON 格式
    python health_check.py --json
"""

import os
import sys
import json
import time
import argparse
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional

try:
    import requests
except ImportError:
    print("错误：需要安装 requests 库，请运行 pip install requests")
    sys.exit(1)

# 日志配置
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════
#  接口定义
# ════════════════════════════════════════════════════════════

# 所有需要检查的接口列表
# 格式：(方法, 路径, 请求体, 预期状态码, 分类, 说明)
ENDPOINTS: List[Dict[str, Any]] = [
    # 核心接口
    {"method": "GET",  "path": "/api/health",              "body": None, "expected": 200, "category": "核心", "desc": "健康检查"},
    {"method": "GET",  "path": "/api/status",               "body": None, "expected": 200, "category": "核心", "desc": "系统状态"},
    {"method": "GET",  "path": "/api/panorama",             "body": None, "expected": 200, "category": "核心", "desc": "全景概览"},
    {"method": "GET",  "path": "/api/sensors",              "body": None, "expected": 200, "category": "核心", "desc": "传感器数据"},
    {"method": "GET",  "path": "/api/workspace",            "body": None, "expected": 200, "category": "核心", "desc": "工作区"},
    {"method": "GET",  "path": "/api/clipboard",            "body": None, "expected": 200, "category": "核心", "desc": "剪贴板"},
    {"method": "GET",  "path": "/metrics",                  "body": None, "expected": 200, "category": "核心", "desc": "监控指标"},
    # 工具接口
    {"method": "GET",  "path": "/api/tools/config",         "body": None, "expected": 200, "category": "工具", "desc": "工具配置"},
    # 记忆接口
    {"method": "GET",  "path": "/api/memory/overview",      "body": None, "expected": 200, "category": "记忆", "desc": "记忆概览"},
    {"method": "GET",  "path": "/api/memory/windows/events","body": None, "expected": 200, "category": "记忆", "desc": "窗口事件"},
    {"method": "GET",  "path": "/api/memory/windows/stats", "body": None, "expected": 200, "category": "记忆", "desc": "窗口统计"},
    # 文件系统接口
    {"method": "GET",  "path": "/api/filesystem/list?path=.","body": None,"expected": 200, "category": "文件系统", "desc": "目录列表"},
    # 进程接口
    {"method": "GET",  "path": "/api/process/list",         "body": None, "expected": 200, "category": "进程", "desc": "进程列表"},
    # 定时任务接口
    {"method": "GET",  "path": "/api/scheduler/tasks",      "body": None, "expected": 200, "category": "定时任务", "desc": "任务列表"},
    # 沙盒接口（应返回 503 表示已关闭）
    {"method": "POST", "path": "/api/sandbox/run",          "body": {"code": "print(1)"}, "expected": 503, "category": "沙盒", "desc": "沙盒（应关闭）"},
    # 聊天接口（空消息应返回 400）
    {"method": "POST", "path": "/api/chat",                 "body": {"message": ""}, "expected": 400, "category": "聊天", "desc": "空消息校验"},
]

# 报告输出目录
REPORT_DIR = os.path.join(os.path.dirname(__file__), "logs", "health_check")


# ════════════════════════════════════════════════════════════
#  检查逻辑
# ════════════════════════════════════════════════════════════

def check_endpoint(base_url: str, ep: Dict[str, Any], timeout: int = 5) -> Dict[str, Any]:
    """检查单个接口

    Args:
        base_url: 服务器基础 URL
        ep: 接口定义字典
        timeout: 请求超时时间（秒）

    Returns:
        检查结果字典
    """
    url = base_url + ep["path"]
    result = {
        "method": ep["method"],
        "path": ep["path"],
        "category": ep["category"],
        "desc": ep["desc"],
        "expected": ep["expected"],
        "actual": None,
        "status": "FAIL",
        "latency_ms": None,
        "error": None,
    }

    try:
        start = time.time()
        if ep["method"] == "GET":
            r = requests.get(url, timeout=timeout)
        else:
            r = requests.post(url, json=ep["body"], timeout=timeout)
        latency = round((time.time() - start) * 1000, 1)

        result["actual"] = r.status_code
        result["latency_ms"] = latency

        if r.status_code == ep["expected"]:
            result["status"] = "PASS"
        else:
            result["status"] = "WARN"
            result["error"] = f"期望 {ep['expected']}，实际 {r.status_code}"

    except requests.exceptions.ConnectionError as e:
        result["status"] = "FAIL"
        result["error"] = f"连接失败: {str(e)[:80]}"
    except requests.exceptions.Timeout:
        result["status"] = "FAIL"
        result["error"] = f"请求超时（{timeout}s）"
    except Exception as e:
        result["status"] = "FAIL"
        result["error"] = f"异常: {str(e)[:80]}"

    return result


def run_check(base_url: str, timeout: int = 5) -> Dict[str, Any]:
    """执行一次完整的接口检查

    Args:
        base_url: 服务器基础 URL
        timeout: 请求超时时间（秒）

    Returns:
        完整检查报告字典
    """
    results = []
    for ep in ENDPOINTS:
        r = check_endpoint(base_url, ep, timeout)
        results.append(r)

    # 统计
    total = len(results)
    passed = sum(1 for r in results if r["status"] == "PASS")
    warned = sum(1 for r in results if r["status"] == "WARN")
    failed = sum(1 for r in results if r["status"] == "FAIL")

    # 按分类统计
    categories = {}
    for r in results:
        cat = r["category"]
        if cat not in categories:
            categories[cat] = {"total": 0, "pass": 0, "warn": 0, "fail": 0}
        categories[cat]["total"] += 1
        if r["status"] == "PASS":
            categories[cat]["pass"] += 1
        elif r["status"] == "WARN":
            categories[cat]["warn"] += 1
        else:
            categories[cat]["fail"] += 1

    # 平均延迟（仅统计成功的请求）
    latencies = [r["latency_ms"] for r in results if r["latency_ms"] is not None]
    avg_latency = round(sum(latencies) / len(latencies), 1) if latencies else 0

    report = {
        "timestamp": datetime.now().isoformat(),
        "base_url": base_url,
        "summary": {
            "total": total,
            "passed": passed,
            "warned": warned,
            "failed": failed,
            "pass_rate": f"{passed / total * 100:.1f}%",
            "avg_latency_ms": avg_latency,
        },
        "categories": categories,
        "results": results,
    }

    return report


# ════════════════════════════════════════════════════════════
#  报告输出
# ════════════════════════════════════════════════════════════

def format_report_text(report: Dict[str, Any]) -> str:
    """将报告格式化为可读文本"""
    lines = []
    lines.append("=" * 72)
    lines.append("  云枢系统接口健康检查报告")
    lines.append("=" * 72)
    lines.append(f"  检查时间: {report['timestamp']}")
    lines.append(f"  目标服务: {report['base_url']}")
    lines.append("")

    # 总览
    s = report["summary"]
    lines.append("  ── 总览 ──────────────────────────────────────")
    lines.append(f"  总接口数: {s['total']}  |  通过: {s['passed']}  |  警告: {s['warned']}  |  失败: {s['failed']}")
    lines.append(f"  通过率: {s['pass_rate']}  |  平均延迟: {s['avg_latency_ms']}ms")
    lines.append("")

    # 分类统计
    lines.append("  ── 分类统计 ──────────────────────────────────")
    for cat, stats in report["categories"].items():
        rate = f"{stats['pass'] / stats['total'] * 100:.0f}%"
        lines.append(f"  {cat:8s}  通过 {stats['pass']}/{stats['total']}  警告 {stats['warn']}  失败 {stats['fail']}  ({rate})")
    lines.append("")

    # 详细结果
    lines.append("  ── 详细结果 ──────────────────────────────────")
    for r in report["results"]:
        status_icon = {"PASS": "OK", "WARN": "!!", "FAIL": "XX"}[r["status"]]
        latency = f"{r['latency_ms']:6.1f}ms" if r["latency_ms"] is not None else "    N/A"
        line = f"  [{status_icon}] {r['method']:4s} {r['path']:40s} {latency}  {r['actual'] or '---'}"
        if r["error"]:
            line += f"  ({r['error']})"
        lines.append(line)

    lines.append("")
    lines.append("=" * 72)
    return "\n".join(lines)


def save_report(report: Dict[str, Any]) -> str:
    """保存报告到文件（JSON + 文本双格式）

    Args:
        report: 检查报告字典

    Returns:
        保存的文件路径
    """
    os.makedirs(REPORT_DIR, exist_ok=True)

    # 使用时间戳作为文件名
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    # 保存 JSON 格式
    json_path = os.path.join(REPORT_DIR, f"health_{ts}.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    # 保存文本格式
    txt_path = os.path.join(REPORT_DIR, f"health_{ts}.txt")
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(format_report_text(report))

    # 保留最新一份报告的软链接（方便查看）
    latest_json = os.path.join(REPORT_DIR, "health_latest.json")
    latest_txt = os.path.join(REPORT_DIR, "health_latest.txt")
    try:
        # Windows 下直接覆盖
        with open(latest_json, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        with open(latest_txt, "w", encoding="utf-8") as f:
            f.write(format_report_text(report))
    except Exception:
        pass

    return txt_path


# ════════════════════════════════════════════════════════════
#  主入口
# ════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="云枢系统接口健康检查")
    parser.add_argument("--host", default="http://127.0.0.1:5678", help="服务器地址（默认 http://127.0.0.1:5678）")
    parser.add_argument("--timeout", type=int, default=5, help="请求超时时间（秒，默认 5）")
    parser.add_argument("--interval", type=int, default=0, help="循环间隔（秒，0 表示单次运行）")
    parser.add_argument("--json", action="store_true", help="仅输出 JSON 格式")
    parser.add_argument("--save", action="store_true", default=True, help="保存报告到文件（默认开启）")
    args = parser.parse_args()

    base_url = args.host.rstrip("/")

    if args.interval > 0:
        logger.info("定时检查模式，间隔 %d 秒，目标 %s", args.interval, base_url)

    round_num = 0
    while True:
        round_num += 1
        if args.interval > 0:
            logger.info("──── 第 %d 轮检查开始 ────", round_num)

        report = run_check(base_url, args.timeout)

        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            text = format_report_text(report)
            print(text)

        # 保存报告
        if args.save:
            path = save_report(report)
            if args.interval > 0:
                logger.info("报告已保存: %s", path)

        # 失败时输出警告
        if report["summary"]["failed"] > 0:
            logger.warning("发现 %d 个接口异常！", report["summary"]["failed"])

        # 单次模式直接退出
        if args.interval <= 0:
            break

        logger.info("下一轮检查将在 %d 秒后执行...", args.interval)
        try:
            time.sleep(args.interval)
        except KeyboardInterrupt:
            logger.info("用户中断，退出定时检查")
            break


if __name__ == "__main__":
    main()
