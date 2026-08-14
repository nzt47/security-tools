#!/usr/bin/env python3
"""TASK-03 学习度量演示脚本 — 构造模拟数据验证 7 项 KPI 聚合逻辑

用法:
    python scripts/demo_learning_metrics.py           # 直接填充单例并打印聚合快照（默认）
    python scripts/demo_learning_metrics.py --server  # 启动临时 Flask 服务（端口 5680）
    python scripts/demo_learning_metrics.py --api     # 走真实 Flask 路由 /api/learning/metrics 验证

设计口径（50 次混合交互，数值自洽可验算）:
    - workflow 命中 17/50 = 0.34（每次 saved_tokens=2000）
    - 语义层 50 次查询，skill 命中 13/50 = 0.26（每次 saved_tokens=2000）
    - LLM 消耗 40 次 × 1500 token = 60000
    - token 复用率 = saved / (saved + consumed)
      saved = 17*2000 + 13*2000 = 60000 → 60000/(60000+60000) = 0.5
    - qa 任务失败率 10/50 = 0.2
    - 反馈均分 4.5（10 次 5 分 + 10 次 4 分）
    - 沉淀增量: skill=5, workflow=45, reflection=0
    - 进化采纳率 13/50 = 0.26

【简易】单文件自包含；【变易】--server/--api 可选；【不易】不改 learning_metrics 任何语义。
"""

import argparse
import sys
import time
from pathlib import Path

# 保证以项目根为 sys.path[0]（python scripts/x.py 时默认是 scripts/）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def seed_demo_data() -> dict:
    """填充学习度量单例并返回 7 日聚合快照"""
    from agent.learning_metrics import get_learning_metrics, reset_learning_metrics

    reset_learning_metrics()
    m = get_learning_metrics()

    N = 50
    saved = 2000
    for _ in range(N):
        m.record_interaction()
        # 1) workflow 拦截层：17 次命中（短路，不进语义层）
        if _ < 17:
            m.record_workflow_match(hit=True, saved_tokens=saved)
            continue
        m.record_workflow_match(hit=False)
        # 2) 语义层：剩余 33 次查询中 13 次命中
        if _ < 17 + 13:
            m.record_semantic_query(hit=True, saved_tokens=saved)
        else:
            m.record_semantic_query(hit=False)

    # 3) LLM 实际消耗：40 次 × 1500
    for _ in range(40):
        m.record_llm_tokens(1500)

    # 4) 任务结果：qa 失败 10/50
    for _ in range(N):
        m.record_task_result("qa", success=(_ >= 10))

    # 5) 反馈均分 4.5
    for _ in range(10):
        m.record_feedback(5)
    for _ in range(10):
        m.record_feedback(4)

    # 6) 沉淀增量
    for _ in range(5):
        m.record_artifact("skill")
    for _ in range(45):
        m.record_artifact("workflow")

    # 7) 进化采纳率 13/50
    for _ in range(N):
        m.record_evolution_candidate(adopted=(_ < 13))

    return m.get_snapshot(days=7)


def check_kpis(snap: dict) -> bool:
    """校验 7 项 KPI 非零且数值自洽（口径见文件头，key 对齐 get_snapshot schema）"""
    k = snap["kpis"]
    checks = {
        "workflow 命中率": (k["workflow_hit_rate"]["rate"], 17 / 50, 0.34),
        "skill 命中率": (k["skill_hit_rate"]["rate"], round(13 / 33, 4), round(13 / 33, 4)),
        "token 复用率": (k["token_reuse_rate"]["rate"], 0.5, 0.5),
        "qa 任务失败率": (k["failure_rate_by_task_type"]["qa"]["rate"], 10 / 50, 0.2),
        "反馈均分": (k["feedback_rating_trend"]["current_avg"], 4.5, 4.5),
        "沉淀增量 skill": (k["artifact_delta"].get("skill", 0), 5, 5),
        "沉淀增量 workflow": (k["artifact_delta"].get("workflow", 0), 45, 45),
        "进化采纳率": (k["evolution_adoption_rate"]["rate"], 13 / 50, 0.26),
    }
    ok = True
    for name, (got, expect, exact) in checks.items():
        passed = abs(got - expect) < 1e-6
        ok = ok and passed
        print("  [%s] %-20s got=%.4f expect=%.4f" % ("PASS" if passed else "FAIL", name, got, expect))
    if not ok:
        print("!! KPI 自洽性校验未通过，请检查聚合逻辑")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description="TASK-03 学习度量模拟数据演示")
    ap.add_argument("--server", action="store_true", help="启动临时 Flask 服务（端口 5680）")
    ap.add_argument("--api", action="store_true", help="走真实 Flask 路由验证 /api/learning/metrics")
    ap.add_argument("--port", type=int, default=5680)
    args = ap.parse_args()

    snap = seed_demo_data()
    print("== 7 日聚合快照（generated_at=%s, days=%d）==" % (snap["generated_at"], snap["days"]))
    import json
    print(json.dumps(snap, ensure_ascii=False, indent=2))
    print("== KPI 自洽性校验 ==")
    ok = check_kpis(snap)

    if args.api or args.server:
        from app_server import app
        if args.api:
            client = app.test_client()
            resp = client.get("/api/learning/metrics")
            print("== HTTP %d /api/learning/metrics ==" % resp.status_code)
            body = resp.get_json()
            print(json.dumps(body, ensure_ascii=False, indent=2))
            ok = ok and resp.status_code == 200 and bool(body.get("kpis"))
        if args.server:
            print("启动临时服务: http://127.0.0.1:%d/api/learning/metrics" % args.port)
            app.run(host="127.0.0.1", port=args.port, debug=False)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
