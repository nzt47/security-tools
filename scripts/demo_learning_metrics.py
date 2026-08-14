"""TASK-03 学习度量演示/验证脚本

用法:
    python scripts/demo_learning_metrics.py            # 模拟 50 次交互 → 走真实 Flask 路由 → 打印 7 项 KPI
    python scripts/demo_learning_metrics.py --server   # 模拟 50 次交互 → 启动临时服务（端口 5680），可浏览器访问
                                                       #   GET http://127.0.0.1:5680/api/learning/metrics

说明:
    - 只向本进程内 LearningMetrics 单例注入模拟数据，不触碰运行中服务的进程；
    - 生产环境 KPI 由 orchestrator/feedback 埋点自然累积；
    - 本脚本用于验证聚合逻辑正确性（纯只读聚合，不落盘、不改业务行为）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # scripts/ 下执行需注入项目根

from flask import Flask

from agent.learning_metrics import get_learning_metrics
from agent.learning_metrics_api import learning_metrics_bp


def seed_demo_data() -> None:
    """模拟 50 次混合交互（workflow/skill 命中、失败、反馈、沉淀、进化）"""
    from agent.learning_metrics import reset_learning_metrics
    reset_learning_metrics()  # 清空单例，保证可重复执行
    lm = get_learning_metrics()
    for i in range(50):
        lm.record_interaction()
        hit_wf = i % 3 == 0          # workflow 命中 17/50
        hit_sk = i % 4 == 0          # skill 命中 13/50
        lm.record_workflow_match(hit_wf, saved_tokens=1200 if hit_wf else 0)
        lm.record_semantic_query(hit_sk, saved_tokens=800 if hit_sk else 0)
        lm.record_llm_tokens(2000)
        lm.record_task_result("qa", success=(i % 5 != 0))   # qa 失败 10/50
        lm.record_feedback(4 if i % 2 == 0 else 5)          # 均分 4.5
        lm.record_artifact("skill" if i % 10 == 0 else "workflow")
        lm.record_evolution_candidate(adopted=(i % 4 == 0))  # 采纳 13/50


def build_app() -> Flask:
    app = Flask(__name__)
    app.register_blueprint(learning_metrics_bp)
    return app


def main() -> None:
    seed_demo_data()
    app = build_app()
    if "--server" in sys.argv:
        app.run(host="127.0.0.1", port=5680, debug=False)
        return
    client = app.test_client()
    resp = client.get("/api/learning/metrics")
    print("HTTP", resp.status_code)
    import json
    data = resp.get_json()
    print(json.dumps(data, ensure_ascii=False, indent=2))
    if resp.status_code != 200:
        sys.exit(1)


if __name__ == "__main__":
    from agent.learning_metrics import reset_learning_metrics  # noqa: E402
    main()
