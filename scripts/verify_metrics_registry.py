#!/usr/bin/env python3
"""直接验证可观测性指标是否注册到 prometheus_client.REGISTRY。

步骤：
1. 调用 emit_eval_score_metric 触发 yunshu_skill_eval_score / yunshu_skill_hallucination_total
2. 调用 emit_retrieval_precision_metric 触发 yunshu_skill_retrieval_precision_at_k
3. 从 prometheus_client.REGISTRY 抓取指标输出
"""
import sys
import os

# 确保从项目根目录导入
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")
os.chdir(os.path.dirname(os.path.abspath(__file__)) + "/..")

from agent.skills_mgmt.observability import (
    emit_eval_score_metric,
    emit_retrieval_precision_metric,
    report_retrieval_observability,
)

try:
    from prometheus_client import REGISTRY, generate_latest
    PROM_AVAILABLE = True
except ImportError:
    PROM_AVAILABLE = False
    print("[WARN] prometheus_client 未安装")


def main():
    print("=== 1. 触发 emit_eval_score_metric ===")
    emit_eval_score_metric(
        skill_id="verify-test-skill",
        eval_score={
            "task_success": True,
            "instruction_followed": True,
            "hallucination_detected": False,
            "score": 0.92,
        },
    )
    print("  emit_eval_score_metric(skill_id=verify-test-skill, score=0.92) 已调用")

    print("\n=== 2. 触发 emit_eval_score_metric（含幻觉）===")
    emit_eval_score_metric(
        skill_id="verify-halluc-skill",
        eval_score={
            "task_success": True,
            "instruction_followed": False,
            "hallucination_detected": True,
            "score": 0.45,
        },
    )
    print("  emit_eval_score_metric(skill_id=verify-halluc-skill, hallucination=True) 已调用")

    print("\n=== 3. 触发 emit_retrieval_precision_metric ===")
    emit_retrieval_precision_metric(k=3, hits=2, precision=0.6667)
    emit_retrieval_precision_metric(k=5, hits=3, precision=0.6)
    emit_retrieval_precision_metric(k=10, hits=5, precision=0.5)
    print("  emit_retrieval_precision_metric(k=3/5/10) 已调用")

    print("\n=== 4. 触发 report_retrieval_observability ===")
    chunks = [
        {"skill_id": "skill-" + str(i), "score": 0.5 + i * 0.01, "layer": 1, "tokens": 100 + i}
        for i in range(5)
    ]
    report_retrieval_observability(chunks, precision_at_k={"k": 3, "hits": 2, "precision": 0.6667})
    print("  report_retrieval_observability(5 chunks) 已调用")

    if PROM_AVAILABLE:
        print("\n=== 5. prometheus_client.REGISTRY 中的 yunshu_skill_* 指标 ===")
        output = generate_latest(REGISTRY).decode("utf-8")
        yunshu_lines = [l for l in output.splitlines() if "yunshu_skill_" in l]
        print("共 " + str(len(yunshu_lines)) + " 行 yunshu_skill_* 指标：")
        for line in yunshu_lines:
            print("  " + line)
        if not yunshu_lines:
            print("  （未找到 yunshu_skill_* 指标）")
            # 检查 BusinessMetricsCollector 是否有独立导出
            print("\n  检查 BusinessMetricsCollector.export_prometheus()...")
            try:
                from agent.monitoring.business_metrics import BusinessMetricsCollector
                bc = BusinessMetricsCollector()
                exported = bc.export_prometheus()
                skill_lines = [l for l in exported.splitlines() if "skill" in l.lower()]
                print("  export_prometheus() 中含 'skill' 的行：", len(skill_lines))
                for line in skill_lines[:10]:
                    print("  " + line)
            except Exception as e:
                print("  BusinessMetricsCollector 检查失败:", e)
    else:
        print("\n[SKIP] prometheus_client 不可用，跳过 REGISTRY 检查")


if __name__ == "__main__":
    main()