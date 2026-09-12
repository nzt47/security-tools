"""自愈策略与恢复动作映射

【模块地图（TASK-S4-03 追加）】

    policy.py            恢复动作映射表（既有，补 M4）：故障域 → 动作 + 实现状态
    levels.py            L1-L5 自愈语义层（v7.2 §4.4）：声明式映射 + 升级判定 +
                         事故卡（§3 IncidentCard）+ `healing.triggered` 发射
                         （S5-02 MTTD/MTTR 契约的发射方）
    release_bundle.py    整包回滚原子单位（§4.4 P7.2-15）：ReleaseBundle 整体 hash +
                         `rollback_bundle` + 部分回滚拒绝（直接触发 L4）
    saga.py              Saga 补偿事务（§4.6）：prepare/execute/confirm + journal +
                         `compensate`（幂等可重放）+ 补偿失败升级 L4；
                         risk ≥ high 强制 Saga 前置（`require_saga`）
    watchdog_singleton.py 单机唯一 Watchdog 守卫（§4.4 P7.2-16）：lockfile 强制，
                         第二个实例抛 `SplitBrainError`；集群化留 P5

【术语纪律】`levels.HealLevel` 的 L1-L5 是**自愈升级五级**，与
`agent/health/probes.py` 的**健康探针五层**（l1_process…l5_semantic）语义正交；
`levels.assert_no_level_confusion()` 是这条纪律的机器可读断言。

【与既有设施的关系】本包**不是**第二套自愈栈：执行一律交给
`agent/monitoring/self_healer.py`、`agent/graceful_degrade.py`、
`agent/observability/model_degrade.py`、`agent/p6_snapshot.py` 等既有设施
（逐条落点见 `levels.LEVEL_SPECS[*].legacy_facilities`）。

【注意】子模块**不做 eager import**：`levels`/`saga`/`release_bundle` 在导入期不触发
`agent.monitoring` / `agent.audit` / `agent.observability` 的装载（那些是延迟导入），
避免 `import agent.self_healing` 变成重依赖入口（保持既有 `policy` 的轻量契约）。
"""

__all__ = ["policy", "levels", "release_bundle", "saga", "watchdog_singleton"]
