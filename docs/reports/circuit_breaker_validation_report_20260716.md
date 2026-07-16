# CircuitBreakerScopeConfig 14 项校验体系验证报告

- **验证时间**: 2026-07-16 01:26:21
- **验证总数**: 29
- **通过**: 29
- **失败**: 0

## 14 项校验体系构成

| 规则集 | 数量 | 说明 |
|--------|------|------|
| SEARCH_INSTANCE_VALIDATION_RULES | 2 | 搜索实例配置校验 |
| CIRCUIT_BREAKER_VALIDATION_RULES | 12 | 三级熔断器配置校验（SESSION/USER/GLOBAL 各 4 项） |
| **总计** | **14** | |

## 验证维度详情

| # | 维度 | 结果 | 说明 |
|---|------|------|------|
| 1 | 14 项 ValidationRule 总数 | ✓ 通过 | SEARCH_INSTANCE=2 + CIRCUIT_BREAKER=12 = 14 |
| 2 | 12 项 CIRCUIT_BREAKER path 完整 | ✓ 通过 | 12 项 path 全部匹配 |
| 3 | 12 项规则全部 required=True | ✓ 通过 |  |
| 4 | CircuitBreakerScopeConfig 4 字段 | ✓ 通过 | 实际字段: {'min_requests', 'failure_threshold', 'half_open_max_calls', 'recovery_timeout'} |
| 5 | CircuitBreakerConfigSection 3 字段（session/user/global_） | ✓ 通过 | 实际字段: {'global_', 'session', 'user'} |
| 6 | ConfigModel 包含 circuit_breaker 字段 | ✓ 通过 | ConfigModel 字段: ['behavior', 'circuit_breaker', 'cognitive', 'features', 'log_system', 'memory', 'permission', 'planning', 'security', 'sensor', 'skills_mgmt', 'voice', 'workflow_learning'] |
| 7 | SESSION.failure_threshold=1.0 | ✓ 通过 |  |
| 8 | SESSION.min_requests=5 | ✓ 通过 |  |
| 9 | SESSION.recovery_timeout=60.0 | ✓ 通过 |  |
| 10 | SESSION.half_open_max_calls=1 | ✓ 通过 |  |
| 11 | USER.failure_threshold=1.0 | ✓ 通过 |  |
| 12 | USER.min_requests=20 | ✓ 通过 |  |
| 13 | USER.recovery_timeout=300.0 | ✓ 通过 |  |
| 14 | USER.half_open_max_calls=2 | ✓ 通过 |  |
| 15 | GLOBAL.failure_threshold=1.0 | ✓ 通过 |  |
| 16 | GLOBAL.min_requests=100 | ✓ 通过 |  |
| 17 | GLOBAL.recovery_timeout=600.0 | ✓ 通过 |  |
| 18 | GLOBAL.half_open_max_calls=3 | ✓ 通过 |  |
| 19 | alias 'global' 键加载成功 | ✓ 通过 | global_.min_requests=80 |
| 20 | 合法配置校验通过（0 errors） | ✓ 通过 | 12 项规则全部通过 |
| 21 | 非法配置被拒绝（session_failure_threshold=1.5） | ✓ 通过 | errors=['session failure_threshold 必须在 [0,1]'] |
| 22 | 非法配置被拒绝（global_min_requests=0） | ✓ 通过 | errors=['global min_requests 必须在 [1,10000]'] |
| 23 | _basic_validation required_sections 包含 circuit_breaker | ✓ 通过 |  |
| 24 | _basic_validation 缺失 circuit_breaker 时报错 | ✓ 通过 | errors_loc=['circuit_breaker'] |
| 25 | _basic_validation 包含 circuit_breaker 时通过 | ✓ 通过 | errors_loc=[] |
| 26 | validate_and_fix_config 缺失时自动补全 circuit_breaker | ✓ 通过 | fixed_config keys: ['sensor', 'cognitive', 'memory', 'behavior', 'permission', 'security', 'circuit_breaker'] |
| 27 | Pydantic 拒绝 failure_threshold=1.5 | ✓ 通过 |  |
| 28 | Pydantic 拒绝 min_requests=0 | ✓ 通过 |  |
| 29 | Pydantic 拒绝 recovery_timeout=100000 | ✓ 通过 |  |

## 结论

**CircuitBreakerScopeConfig 已在 14 项校验体系中完全生效 ✓**

- 12 项 CIRCUIT_BREAKER_VALIDATION_RULES 与 Pydantic 模型字段完全对应
- Pydantic ConfigModel.circuit_breaker 能正确加载默认值与自定义值
- validate_dict_against_rules 实际校验逻辑运行正确（合法通过/非法拒绝）
- _basic_validation 与 validate_and_fix_config 已纳入 circuit_breaker 必需节
- Pydantic 严格校验对非法值（超范围）正确抛出 ValidationError
