"""上下文工程集成演示脚本

演示场景：一个包含敏感信息的复杂任务执行流程
- 复杂任务创建与计划确认
- 记忆过滤（敏感信息检测）
- 子代理摘要压缩
- 上下文隔离屏障
- 失败回退机制

运行方式:
    python tests/integration/test_context_engineering_demo.py
"""

import sys
import os
import asyncio
import json
import time

# Windows 控制台编码修复
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    os.environ["PYTHONIOENCODING"] = "utf-8"

# 设置日志级别为 INFO 以查看结构化日志
import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

# 确保可以导入项目模块
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


async def main():
    print("=" * 70)
    print("  云枢智能体 - 上下文工程集成演示")
    print("=" * 70)
    print()

    trace_id = f"demo_{int(time.time())}"

    # ──────────────────────────────────────────
    # 场景一：复杂任务创建与计划确认
    # ──────────────────────────────────────────
    print("【场景一】复杂任务创建与计划确认")
    print("-" * 50)

    from agent.task_planner.enhanced_planner import (
        EnhancedTaskPlanner,
        TaskComplexity,
        PlanPreview,
    )

    planner = EnhancedTaskPlanner()

    # 模拟一个复杂任务
    complex_goal = "帮我设计一个分布式微服务架构系统，包含用户管理、订单处理、支付集成和数据监控告警平台"

    print(f"任务目标: {complex_goal}")
    print()

    # 1. 创建计划
    print("→ 步骤 1: 创建执行计划")
    plan = await planner.create_plan(complex_goal)
    print(f"  计划ID: {plan.plan_id}")
    print(f"  任务数量: {len(plan._nodes)}")
    print()

    # 2. 复杂度评估
    print("→ 步骤 2: 复杂度评估")
    complexity = planner._evaluate_complexity(complex_goal)
    print(f"  复杂度等级: {complexity.value}")
    print(f"  需要确认: {complexity.value >= TaskComplexity.MODERATE.value}")
    print()

    # 3. 计划预览
    print("-> 步骤 3: 生成计划预览")
    preview = planner.get_preview(plan, goal=complex_goal)
    print(preview.get_summary_text(use_emoji=False))
    print()

    # 4. 确认计划
    print("→ 步骤 4: 计划确认流程")
    confirm_result = await planner.confirm_plan(plan.plan_id, confirmed_by="demo_user")
    print(f"  确认结果: {'成功' if confirm_result.confirmed else '失败'}")
    print(f"  确认任务数: {len(confirm_result.confirmed_tasks)}")
    print(f"  拒绝任务数: {len(confirm_result.rejected_tasks)}")
    print(f"  消息: {confirm_result.message}")
    print(f"  计划状态: {planner.is_plan_ready(plan.plan_id)}")
    print()

    # ──────────────────────────────────────────
    # 场景二：敏感信息过滤
    # ──────────────────────────────────────────
    print("【场景二】敏感信息过滤演示")
    print("-" * 50)

    from agent.memory.filter import SensitiveDataFilter, SensitiveLevel, FilterResult

    filter = SensitiveDataFilter(block_critical=True, block_high=True)

    test_cases = [
        {
            "name": "用户偏好设置（安全）",
            "content": {"theme": "dark", "language": "zh-CN", "notifications": True},
            "expected": "通过",
        },
        {
            "name": "包含 API Key 的配置",
            "content": {"api_key": "sk-1234567890abcdefghijklmnopqrstuvwxyz1234567890ab", "endpoint": "https://api.example.com"},
            "expected": "阻止",
        },
        {
            "name": "包含身份证号的记录",
            "content": {"name": "张三", "id_card": "110101199001011234", "phone": "13812345678"},
            "expected": "阻止",
        },
        {
            "name": "包含密码的用户信息",
            "content": {"username": "admin", "password": "SuperSecret123!"},
            "expected": "阻止",
        },
        {
            "name": "代码实现片段",
            "content": "def process_data(items):\n    result = []\n    for item in items:\n        result.append(item * 2)\n    return result",
            "expected": "阻止",
        },
    ]

    for i, case in enumerate(test_cases, 1):
        print(f"  测试 {i}: {case['name']}")
        result = filter.check(case["content"])

        status = "✓ 通过" if result.allowed else "✗ 阻止"
        print(f"    结果: {status}  (预期: {case['expected']})")

        if result.violations:
            print(f"    检测到 {len(result.violations)} 项敏感信息:")
            for v in result.violations[:5]:  # 最多显示 5 条
                level_name = v.level.name if hasattr(v.level, 'name') else str(v.level)
                print(f"      - [{level_name}] {v.pattern_name}: {v.matched_text}")

        print()

    # ──────────────────────────────────────────
    # 场景三：子代理摘要压缩与隔离屏障
    # ──────────────────────────────────────────
    print("【场景三】子代理摘要压缩与隔离屏障")
    print("-" * 50)

    from agent.subagent.summarizer import SubagentSummarizer, SummaryStrategy, SubagentSummary
    from agent.subagent.barrier import SubagentBarrier, IsolationLevel, SubagentMessage
    from agent.subagent.container import SubagentContainer, SubagentConfig

    # 1. 创建子代理
    print("→ 步骤 1: 创建三个子代理")
    sa_config_1 = SubagentConfig(name="代码分析代理", model_id="gpt-4o")
    sa_config_2 = SubagentConfig(name="数据统计代理", model_id="gpt-3.5")
    sa_config_3 = SubagentConfig(name="安全审计代理", model_id="gpt-4o")

    sa_1 = SubagentContainer(sa_config_1)
    sa_2 = SubagentContainer(sa_config_2)
    sa_3 = SubagentContainer(sa_config_3)
    print(f"  - sa-analysis: 代码分析代理")
    print(f"  - sa-stats: 数据统计代理")
    print(f"  - sa-security: 安全审计代理")
    print()

    # 2. 注册到隔离屏障
    print("→ 步骤 2: 注册到隔离屏障")
    barrier = SubagentBarrier(isolation_level=IsolationLevel.FULL)
    barrier.register("sa-analysis", sa_1)
    barrier.register("sa-stats", sa_2)
    barrier.register("sa-security", sa_3)
    print(f"  已注册 {barrier.get_stats()['registered_agents']} 个子代理")
    print(f"  隔离级别: {barrier.get_stats()['isolation_level']}")
    print()

    # 3. 模拟子代理执行并生成摘要
    print("→ 步骤 3: 子代理执行结果摘要压缩")
    summarizer = SubagentSummarizer()

    # 模拟详细输出
    detailed_output = """
代码分析完成报告：

【分析摘要】
对项目中的 15 个 Python 文件进行了全面的代码质量分析。

【关键发现】
关键发现: 发现 3 处潜在的性能瓶颈，主要集中在数据库查询层
关键发现: 识别出 5 个安全漏洞，包括 SQL 注入风险和 XSS 漏洞
关键发现: 代码覆盖率为 72%，建议增加单元测试

【详细指标】
- 代码行数: 12,847
- 函数数量: 342
- 平均圈复杂度: 4.2
- 重复代码率: 8.5%

【决策】
决策: 优先修复 SQL 注入漏洞（P0 级别）
决策: 第二阶段优化数据库查询性能

【下一步行动】
任务: 修复安全漏洞（预计 3 天）
任务: 优化慢查询（预计 2 天）
任务: 增加测试覆盖率到 85%（预计 5 天）

【实现细节】
发现问题的具体代码位置：
- 文件 auth.py 第 45 行存在 SQL 注入风险
- 文件 api.py 第 128 行 XSS 防护不完整
- db/query_builder.py 中 N+1 查询问题
"""

    print(f"  原始输出长度: {len(detailed_output)} 字符")

    summary = await summarizer.summarize(
        output=detailed_output,
        subagent_id="sa-analysis",
        subagent_name="代码分析代理",
        strategy=SummaryStrategy.FULL,
        trace_id=trace_id,
    )

    print(f"  摘要长度: {len(summary.summary_text)} 字符")
    print(f"  压缩比: {1 - len(summary.summary_text) / len(detailed_output):.1%}")
    print(f"  置信度: {summary.confidence:.2f}")
    print()
    print("  结构化摘要:")
    print(f"    关键发现: {len(summary.key_findings)} 条")
    for kf in summary.key_findings:
        print(f"      - {kf}")
    print(f"    决策: {len(summary.decisions)} 条")
    for d in summary.decisions:
        print(f"      - {d}")
    print(f"    待办: {len(summary.action_items)} 条")
    for ai in summary.action_items:
        print(f"      - {ai}")
    print()

    # 4. 通过屏障传递摘要（不传递原始上下文）
    print("→ 步骤 4: 通过隔离屏障传递摘要")
    result = barrier.send_message(
        from_id="sa-analysis",
        to_id=None,  # 发送给主代理
        message_type="summary",
        content=summary.to_dict(),
    )
    print(f"  消息发送结果: {'成功' if result else '失败'}")

    # 主代理获取消息
    master_messages = barrier.fetch_messages_for_master(clear=True)
    print(f"  主代理收到消息数: {len(master_messages)}")
    if master_messages:
        msg = master_messages[0]
        print(f"  消息内容字段: {list(msg.content.keys())}")
        print(f"  消息类型: {msg.message_type}")
    print()

    # 5. 验证隔离性
    print("→ 步骤 5: 上下文隔离验证")
    report = barrier.verify_isolation("sa-analysis")
    print(f"  子代理: {report['subagent_id']}")
    print(f"  已注册: {report['is_registered']}")
    print(f"  上下文可直接访问: {report['context_accessible']}")
    print(f"  可访问其他子代理上下文: {report['can_access_other_contexts']}")
    print()

    # ──────────────────────────────────────────
    # 场景四：计划失败与回退机制
    # ──────────────────────────────────────────
    print("【场景四】计划失败与回退机制")
    print("-" * 50)

    from agent.task_planner.enhanced_dag import EnhancedDAG, EnhancedTaskNode, PlanStatus

    # 1. 创建一个模拟执行的计划
    print("→ 步骤 1: 创建模拟执行计划")
    plan = EnhancedDAG()
    plan.plan_id = "demo_fail_plan"
    plan.status = PlanStatus.DRAFT

    tasks = [
        ("step_0", "需求调研", []),
        ("step_1", "技术选型", ["step_0"]),
        ("step_2", "架构设计", ["step_1"]),
        ("step_3", "模块开发", ["step_2"]),
        ("step_4", "集成测试", ["step_3"]),
        ("step_5", "部署上线", ["step_4"]),
    ]

    for task_id, desc, deps in tasks:
        node = EnhancedTaskNode(
            id=task_id,
            description=desc,
            depends_on=deps,
            estimated_duration=10.0,
            requires_confirmation=False,
            rollback_action=f"回退 {desc} 前的状态",
        )
        plan.add_task(node)

    # 全部确认
    for task_id, _, _ in tasks:
        plan.confirm_task(task_id, confirmed_by="demo_user")
    plan.status = PlanStatus.CONFIRMED

    print(f"  计划ID: {plan.plan_id}")
    print(f"  任务数: {len(plan._nodes)}")
    print()

    # 2. 模拟执行到中途失败
    print("→ 步骤 2: 模拟执行失败")
    plan.mark_running("step_0")
    plan.mark_done("step_0", result="完成需求调研")
    plan.mark_running("step_1")
    plan.mark_done("step_1", result="完成技术选型")
    plan.mark_running("step_2")
    plan.mark_done("step_2", result="完成架构设计")
    plan.mark_running("step_3")
    plan.mark_failed("step_3", error="开发过程中发现严重的设计缺陷，需要回退架构设计")

    print(f"  失败任务: step_3 - 模块开发")
    print(f"  失败原因: 开发过程中发现严重的设计缺陷")
    print()

    # 3. 查看计划状态
    print("→ 步骤 3: 失败后计划状态")
    summary = plan.get_plan_summary()
    print(f"  总任务数: {summary['total_tasks']}")
    print(f"  状态分布: {summary['status_counts']}")
    print()

    # 4. 生成回退路径
    print("→ 步骤 4: 生成回退路径")
    rollback_path = plan.get_rollback_path("step_3")
    print(f"  需要回退的任务数: {len(rollback_path)}")
    for task_id in rollback_path:
        task = plan.get_task(task_id)
        if task:
            print(f"    - {task_id}: {task.description}")
    print()

    # 5. 创建回退计划
    print("→ 步骤 5: 创建回退计划")
    rollback_plan = planner.create_rollback_plan(plan)

    if rollback_plan:
        print(f"  回退计划ID: {rollback_plan.plan_id}")
        print(f"  回退任务数: {len(rollback_plan._nodes)}")
        print(f"  回退计划状态: {rollback_plan.status.value}")
    else:
        print("  无需回退")
    print()

    # ──────────────────────────────────────────
    # 场景五：长期记忆与审查
    # ──────────────────────────────────────────
    print("【场景五】长期记忆与审查")
    print("-" * 50)

    import tempfile
    from agent.memory.long_term_memory import LongTermMemory
    from agent.memory.short_term_memory import ShortTermMemory
    from agent.memory.reviewer import MemoryReviewer

    tmpdir = tempfile.mkdtemp()
    try:
        # 1. 长期记忆
        print("-> 步骤 1: 长期记忆管理")
        ltm = LongTermMemory(db_path=os.path.join(tmpdir, "memory.db"))

        memory_items = [
            ("user_theme", {"theme": "dark", "font_size": 14}, 3, ["preference"], False),
            ("project_architecture", {"type": "microservices", "services": 5}, 5, ["architecture"], False),
            ("api_credential", {"key": "dummy", "endpoint": "example.com"}, 5, ["credentials"], True),
            ("user_profile", {"name": "测试用户", "role": "admin"}, 4, ["profile"], False),
        ]

        for key, content, importance, tags, sensitive in memory_items:
            # 先过滤
            result = filter.check(content)
            if result.allowed:
                await ltm.save(key, content, importance=importance, tags=tags, sensitive=sensitive)
                print(f"  ✓ 保存记忆: {key} (重要性: {importance})")
            else:
                print(f"  ✗ 过滤阻止: {key} - 包含敏感信息")

        print()

        # 2. 记忆库审查
        print("→ 步骤 2: 记忆库审查")
        reviewer = MemoryReviewer(ltm, stale_threshold_days=30)
        review_result = await reviewer.review()

        print(f"  总条目数: {review_result.total_entries}")
        print(f"  健康条目: {review_result.healthy_entries}")
        print(f"  陈旧条目: {review_result.stale_entries}")
        print(f"  重复条目: {review_result.duplicate_entries}")
        print(f"  健康评分: {review_result.report['health_score']}/100")
        print()
        print("  审查建议:")
        for suggestion in review_result.suggestions:
            print(f"    - {suggestion}")
        print()

        # 3. 临时记忆
        print("→ 步骤 3: 临时记忆（会话级）")
        stm = ShortTermMemory(max_size=5, default_ttl=10)

        await stm.save("temp_result_1", "中间计算结果 A", task_id="task_1")
        await stm.save("temp_result_2", "中间计算结果 B", task_id="task_1")
        await stm.save("draft_idea", "一个想法草稿", task_id="task_2")

        stats = stm.get_stats()
        print(f"  临时记忆数: {stats['total_entries']}/{stats['max_size']}")
        print(f"  使用率: {stats['usage_pct']}%")
        print()

        # 清理任务记忆
        cleared = await stm.clear_task_memory("task_1")
        print(f"  清理 task_1 相关记忆: {cleared} 条")
        stats = stm.get_stats()
        print(f"  清理后剩余: {stats['total_entries']} 条")

    finally:
        # 清理临时目录（忽略文件锁定错误）
        import shutil
        try:
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass

    print()
    print("=" * 70)
    print("  演示完成！所有场景执行完毕")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
