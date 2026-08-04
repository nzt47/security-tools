#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""定期日志注入 + LogQL 正则稳定性自检

【不易】分母同步不变量：每条日志 layer_counts 各层之和 == metric_total
【变易】支持 file（Promtail 采集全链路）/ push（直推 Loki）两种模式，
        支持 --verify 自检（注入后查询 Loki 验证正则可命中）
【简易】单文件零第三方依赖（标准库 urllib），可作为 CronJob 周期执行

用途（任务 2）：
  定期在集群中模拟发送 semantic 埋点日志，持续验证：
    1) Promtail -> Loki 全链路采集是否稳定
    2) Grafana 告警规则/面板的 LogQL 正则（metric_total / llm_error / layer_counts）
       在新数据下是否始终可解析、可命中（避免日志格式变化导致正则静默失效）

运行方式：
  1) CronJob 周期执行（推荐，见 deploy/k8s/log-injector-cronjob.yaml）：
       python3 periodic_log_injector.py --mode file --count 10 --verify
  2) 手动一次性验证：
       python3 scripts/periodic_log_injector.py --mode push --count 5 --verify
       python3 scripts/periodic_log_injector.py --mode file --count 10 --no-verify

退出码：自检全部通过返回 0，任一检查失败返回 1（便于 CronJob 观测）。
"""
import argparse
import json
import sys
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request

# ────────────────────────────────────────────────────────────────
# 测试用例（与 send_semantic_logs.py 保持一致）
# ────────────────────────────────────────────────────────────────
CASES = [
    {"total": 5, "layers": {"rule": 1, "semantic": 2, "llm": 2},
     "skill": "verify_skill_001", "score": 0.875, "instr_len": 42},
    {"total": 12, "layers": {"rule": 3, "semantic": 4, "llm": 3,
                             "template": 1, "reject": 1},
     "skill": "verify_skill_002", "score": 0.613, "instr_len": 87},
    {"total": 25, "layers": {"rule": 5, "semantic": 8, "llm": 6,
                             "template": 2, "reject": 2,
                             "llm_error": 1, "llm_low_confidence_fallback": 1},
     "skill": "verify_skill_003", "score": 0.204, "instr_len": 156},
]

# 自检的 LogQL 查询（与 Grafana 面板/告警规则口径一致，含 |~ line filter）
# ⚠ 转义层级：Python 源码中 `\\\\` → LogQL 源码 `\\` → 正则模式 `\`（如 \[ \s \d \）
CHECK_QUERIES = [
    ("metric_total 正则",
     'sum(sum_over_time({action="orchestrator.semantic.metric_total"}'
     ' |~ "semantic\\\\[total="'
     ' | regexp "semantic\\\\[total=(?P<total>[0-9.]+)" | unwrap total [%dm]))'),
    ("llm_error 正则",
     'sum(sum_over_time({action="orchestrator.semantic.metric_total"}'
     ' |~ "semantic\\\\[total="'
     ' | regexp "\\"llm_error\\":\\\\s*(?P<llm_error>\\\\d+)" | unwrap llm_error [%dm]))'),
    ("layer_counts rule 正则",
     'sum(sum_over_time({action="orchestrator.semantic.metric_total"}'
     ' |~ "semantic\\\\[total="'
     ' | regexp "\\"rule\\":\\\\s*(?P<rule>\\\\d+)" | unwrap rule [%dm]))'),
]

# 注意：本环境 kind 集群中完整 FQDN（*.svc.cluster.local）解析会超时，
# 短域名（同命名空间服务名）正常。CronJob 与 Loki 同属 monitoring 命名空间，用短域名。
DEFAULT_LOKI_URL = "http://loki-gateway:3100/loki/api/v1/query_range"


def build_json_line(total, layers, skill, score, instr_len):
    """构造原始 JSON 日志行（与 orchestrator.py 输出一致）"""
    assert sum(layers.values()) == total, "分母同步不变量被破坏"
    return json.dumps({
        "module_name": "orchestrator",
        "action": "orchestrator.semantic.metric_total",
        "trace_id_ctx": uuid.uuid4().hex[:16],
        "message": "[埋点] semantic 触发, total=%d, counts=%s, skill=%s, "
                   "score=%.3f, instr_len=%d, instr_loaded=success" % (
                       total, layers, skill, score, instr_len),
        "metric_total": total,
        "layer_counts": layers,
        "skill_id": skill,
        "top1_score": float(score),
        "instruction_len": instr_len,
        "instruction_loaded": True,
    }, ensure_ascii=False)


def build_norm_line(total, layers, skill, score, instr_len):
    """构造规范化日志行（Promtail template+output stage 输出格式）"""
    assert sum(layers.values()) == total, "分母同步不变量被破坏"
    return ("semantic[total=%d][skill=%s][score=%.3f][instr_len=%d]"
            "[loaded=true][layers=%s]" % (
                total, skill, score, instr_len,
                json.dumps(layers, ensure_ascii=False)))


def inject_file(count, log_dir):
    """file 模式：写原始 JSON 到 Promtail 采集目录"""
    import os
    os.makedirs(log_dir, exist_ok=True)
    path = os.path.join(log_dir, "semantic_metric_total.log")
    with open(path, "a", encoding="utf-8") as f:
        for i in range(count):
            case = CASES[i % len(CASES)]
            f.write(build_json_line(**case) + "\n")
    print("[inject:file] 已写入 %d 条原始 JSON -> %s" % (count, path))


def inject_push(count, loki_url):
    """push 模式：直推规范化行到 Loki push API"""
    url = loki_url.replace("/query_range", "/push")
    now_ns = str(time.time_ns())
    values = []
    for i in range(count):
        case = CASES[i % len(CASES)]
        values.append([now_ns, build_norm_line(**case)])
    body = json.dumps({"streams": [{
        "stream": {
            "job": "yunshu-orchestrator",
            "action": "orchestrator.semantic.metric_total",
            "instruction_loaded": "true",
        },
        "values": values,
    }]}).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        print("[inject:push] Loki 返回 HTTP %d（%d 条规范化行）"
              % (resp.status, count))


def query_loki(url, expr):
    """执行 LogQL 查询，返回 (ok, samples) 或 (False, 错误信息)"""
    end = int(time.time())
    start = end - 3600
    full = ("%s?query=%s&start=%d&end=%d&limit=100&direction=forward"
            % (url, urllib.parse.quote(expr), start, end))
    try:
        resp = json.load(urllib.request.urlopen(full, timeout=15))
        samples = []
        for s in resp.get("data", {}).get("result", []):
            samples.extend(v[1] for v in s.get("values", []))
        return True, samples
    except urllib.error.HTTPError as e:
        return False, "HTTP %d: %s" % (e.code,
                                       e.read().decode("utf-8", "replace")[:300])
    except Exception as e:  # noqa: BLE001
        return False, "ERR: %s" % e


def verify_regex(loki_url, window_min):
    """LogQL 正则稳定性自检：所有检查查询必须可执行且返回样本"""
    print("=" * 60)
    print("[verify] 开始 LogQL 正则稳定性自检（窗口 %dm）" % window_min)
    all_ok = True
    for name, template in CHECK_QUERIES:
        expr = template % window_min
        ok, res = query_loki(loki_url, expr)
        if not ok:
            print("[FAIL] %-28s 查询执行失败: %s" % (name, res))
            all_ok = False
        elif not res:
            print("[FAIL] %-28s 无样本返回（正则未命中新日志）" % name)
            all_ok = False
        else:
            print("[PASS] %-28s 样本数=%d 最新值=%s"
                  % (name, len(res), res[-1][:20]))
    print("[verify] 结果: %s" % ("全部通过" if all_ok else "存在失败项"))
    return all_ok


def main():
    parser = argparse.ArgumentParser(
        description="定期注入 semantic 埋点日志并自检 LogQL 正则稳定性")
    parser.add_argument("--mode", choices=["file", "push"], default="file",
                        help="注入模式（默认 file，走 Promtail 全链路）")
    parser.add_argument("--count", type=int, default=10,
                        help="每次注入日志条数（默认 10）")
    parser.add_argument("--log-dir", default="/var/log/yunshu/orchestrator",
                        help="file 模式日志目录（Promtail 采集）")
    parser.add_argument("--loki-url", default=DEFAULT_LOKI_URL,
                        help="Loki query_range API 地址")
    parser.add_argument("--window", type=int, default=5,
                        help="自检查询窗口分钟（默认 5）")
    parser.add_argument("--verify", action="store_true", default=True,
                        help="注入后执行 LogQL 正则自检（默认开启）")
    parser.add_argument("--no-verify", action="store_false", dest="verify",
                        help="跳过自检")
    args = parser.parse_args()

    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    print("[run] %s 注入 %d 条日志（mode=%s）" % (ts, args.count, args.mode))

    if args.mode == "file":
        inject_file(args.count, args.log_dir)
    else:
        inject_push(args.count, args.loki_url)

    # 给 Promtail 采集留出缓冲（file 模式需要它读文件并推 Loki）
    if args.mode == "file":
        time.sleep(5)

    if not args.verify:
        print("[run] 已跳过自检（--no-verify）")
        return 0

    ok = verify_regex(args.loki_url, args.window)
    print("[run] %s" % ("PASS：LogQL 正则稳定" if ok else "FAIL：见上方自检输出"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
