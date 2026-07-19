#!/usr/bin/env python3
"""验证可观测性指标是否正确上报到 Prometheus /metrics 端点。

步骤：
1. 调用 /api/skills-mgmt/match 触发 report_retrieval_observability
2. 调用 /api/skills-mgmt/<skill_id>/execute 触发 emit_eval_score_metric
3. 抓取 /metrics 检查 yunshu_skill_* 指标
"""
import urllib.request
import urllib.error
import json
import sys

BASE = "http://127.0.0.1:5678"


def call_match():
    """触发 match_skills -> report_retrieval_observability"""
    data = json.dumps({"intent": "邮件处理", "top_k": 5}).encode("utf-8")
    req = urllib.request.Request(
        BASE + "/api/skills-mgmt/match",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    r = urllib.request.urlopen(req, timeout=60)
    result = json.loads(r.read().decode("utf-8"))
    print("=== 1. /api/skills-mgmt/match ===")
    print("  ok:", result.get("ok"))
    matches = result.get("matches", [])
    print("  matches:", len(matches), "项")
    print("  estimated_tokens:", result.get("estimated_total_tokens"))
    if matches:
        first = matches[0]
        sid = first.get("skill_id") or first.get("id") or "?"
        print("  首个匹配:", sid)
    return result


def call_record_execution(skill_id):
    """触发 record_execution -> emit_eval_score_metric"""
    data = json.dumps({
        "success": True,
        "latency_ms": 150,
        "eval_score": {
            "task_success": True,
            "instruction_followed": True,
            "hallucination_detected": False,
            "score": 0.92,
        },
    }).encode("utf-8")
    req = urllib.request.Request(
        BASE + "/api/skills-mgmt/" + skill_id + "/execute",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        r = urllib.request.urlopen(req, timeout=30)
        result = json.loads(r.read().decode("utf-8"))
        print("\n=== 2. /api/skills-mgmt/" + skill_id + "/execute ===")
        print("  ok:", result.get("ok"))
        return result
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        print("\n=== 2. /api/skills-mgmt/" + skill_id + "/execute (HTTP " + str(e.code) + ") ===")
        print(" ", body[:200])
        return None


def check_metrics():
    """抓取 /metrics 检查 yunshu_skill_* 指标"""
    r = urllib.request.urlopen(BASE + "/metrics", timeout=10)
    text = r.read().decode("utf-8")
    yunshu_lines = [l for l in text.splitlines() if "yunshu_skill_" in l]
    print("\n=== 3. /metrics 中的 yunshu_skill_* 指标（" + str(len(yunshu_lines)) + " 行）===")
    for line in yunshu_lines:
        print(" ", line)
    if not yunshu_lines:
        print("  （暂无 yunshu_skill_ 指标）")
        skill_lines = [l for l in text.splitlines() if "skill" in l.lower()]
        print("\n  含 'skill' 的指标行（" + str(len(skill_lines)) + " 行）：")
        for line in skill_lines[:15]:
            print(" ", line)


if __name__ == "__main__":
    try:
        match_result = call_match()
        matches = match_result.get("matches", []) if match_result else []
        if matches:
            skill_id = matches[0].get("skill_id") or matches[0].get("id")
            if skill_id:
                call_record_execution(skill_id)
        else:
            print("\n（无匹配技能，跳过 execute 调用）")
        check_metrics()
    except Exception as e:
        print("错误:", e, file=sys.stderr)
        sys.exit(1)