# Day 4 行动计划：boundary_test_coverage & test_coverage 提升

> 生成时间：2026-07-01
> 前置完成：Day 3 structured_log_coverage 已达 71.9%（超额完成 70% 阶段 2 目标）
> 当前重点：验证过程可见层的两项短板指标

## 一、当前状态与目标差距

### 1.1 两项核心指标对比

| 指标 | 当前值 | 当前阈值 | 阶段2目标 | 最终目标 | 差距 | 优先级 |
|------|--------|---------|----------|---------|------|--------|
| boundary_test_coverage | 19.5% | 12% | 80% | 90% | -60.5% | P0 |
| test_coverage | 3.7% | 0% | 65% | 70% | -61.3% | P1 |

> 两项指标差距均超 60%，Day 4 聚焦于「快速提升边界测试覆盖率」+「建立 test_coverage 基线」。

### 1.2 边界测试覆盖现状

| 统计项 | 数值 |
|--------|------|
| 总模块数 | 34 |
| 总测试数 | 4652 |
| 边界测试数 | 905 |
| 当前覆盖率 | 19.5% |
| 零边界测试模块 | 9 个 |
| 低覆盖模块（<15%） | 10 个 |

### 1.3 零测试模块清单（7 个，优先级最高）

| 模块 | 描述 | 总测试 | 边界测试 | 缺失场景 |
|------|------|--------|---------|---------|
| core | 核心调度与状态机 | 0 | 0 | empty, timeout, invalid |
| circuit_breaker | 熔断器 | 0 | 0 | boundary, timeout, extreme |
| rate_limiter | 限流器 | 0 | 0 | boundary, overflow, extreme |
| graceful_degrade | 优雅降级 | 0 | 0 | timeout, invalid |
| disaster_recovery | 灾难恢复 | 0 | 0 | timeout, empty, extreme |
| permission_system | 权限系统 | 0 | 0 | invalid, boundary |
| config | 配置系统 | 0 | 0 | empty, invalid |

### 1.4 缺失场景统计

| 场景 | 需补充模块数 | 典型用例 |
|------|------------|---------|
| timeout | 6 | 请求超时、连接超时、执行超时 |
| invalid | 5 | 非法参数、格式错误、类型不匹配 |
| extreme | 5 | 极大值、极小值、边界极值 |
| empty | 4 | 空输入、空列表、空字符串 |
| boundary | 3 | 边界值、临界条件 |
| overflow | 2 | 溢出、超限 |

## 二、Day 4 任务清单

### 上午：BT-001~007 零测试模块补齐（预估 4h）

> 每个模块补充 3-5 个边界测试用例，覆盖缺失场景。

| 任务ID | 模块 | 测试文件 | 补充场景 | 预估用例数 | 预估工时 |
|--------|------|---------|---------|-----------|---------|
| BT-001 | circuit_breaker | tests/boundary/test_circuit_breaker_boundary.py | boundary, timeout, extreme | 5 | 0.6h |
| BT-002 | rate_limiter | tests/boundary/test_rate_limiter_boundary.py | boundary, overflow, extreme | 5 | 0.6h |
| BT-003 | graceful_degrade | tests/boundary/test_graceful_degrade_boundary.py | timeout, invalid | 4 | 0.5h |
| BT-004 | disaster_recovery | tests/boundary/test_disaster_recovery_boundary.py | timeout, empty, extreme | 5 | 0.6h |
| BT-005 | permission_system | tests/boundary/test_permission_system_boundary.py | invalid, boundary | 4 | 0.5h |
| BT-006 | config | tests/boundary/test_config_boundary.py | empty, invalid | 4 | 0.5h |
| BT-007 | core | tests/boundary/test_core_boundary.py | empty, timeout, invalid | 5 | 0.7h |

**上午小计：7 个模块，32 个边界测试用例，4.0h**

### 下午：BT-008~012 低覆盖模块补充（预估 3h）

| 任务ID | 模块 | 当前覆盖率 | 补充场景 | 预估用例数 | 预估工时 |
|--------|------|-----------|---------|-----------|---------|
| BT-008 | caching | 3% (2/63) | empty, invalid, timeout | 8 | 0.6h |
| BT-009 | health | 6% (1/16) | empty, invalid, extreme | 5 | 0.5h |
| BT-010 | human_in_the_loop | 5% (1/22) | timeout, invalid | 5 | 0.5h |
| BT-011 | lazy_loader | 5% (3/64) | empty, timeout | 6 | 0.6h |
| BT-012 | data | 5% (1/22) | empty, invalid | 5 | 0.5h |

**下午小计：5 个模块，29 个边界测试用例，2.7h**

### 下班前：验证 + test_coverage 基线（预估 1h）

| 任务ID | 内容 | 命令 | 预估工时 |
|--------|------|------|---------|
| TC-001 | 运行全量边界测试 | `python scripts/check_boundary_coverage.py --json-only` | 0.3h |
| TC-002 | 生成 test_coverage 基线 | `python -m pytest --cov=agent --cov-report=xml:coverage.xml tests/ -q` | 0.4h |
| TC-003 | 运行可见性报告 | `python scripts/visibility_report.py --config config.yaml` | 0.3h |

**下班前小计：1.0h**

## 三、Day 4 预期成果

### 3.1 boundary_test_coverage 预期

| 项目 | 数值 |
|------|------|
| 当前边界测试数 | 905 |
| Day 4 新增数 | ~61 |
| 预期总数 | ~966 |
| 当前总测试数 | 4652 |
| 预期覆盖率 | ~20.8% |
| 覆盖率提升 | +1.3% |

> 注：boundary_test_coverage 从 19.5% 提升到 ~21%，增幅有限。要达到 80% 需要持续多日补充（预估需 15+ 个工作日）。Day 4 的核心价值是**建立零测试模块的测试基线**。

### 3.2 test_coverage 预期

| 项目 | 数值 |
|------|------|
| 当前 line-rate | 3.7% |
| Day 4 目标 | 生成真实 coverage.xml 基线 |
| 后续策略 | 按模块优先级逐步补齐单元测试 |

### 3.3 长期收敛路线图

| 阶段 | 时间 | boundary_test_coverage | test_coverage |
|------|------|----------------------|--------------|
| Day 4（本次） | 1 天 | 19.5% → ~21% | 3.7% → 基线建立 |
| Week 2 | 5 天 | ~21% → ~35% | 基线 → ~15% |
| Week 3 | 5 天 | ~35% → ~50% | ~15% → ~30% |
| Week 4 | 5 天 | ~50% → ~65% | ~30% → ~45% |
| Week 5+ | 持续 | ~65% → 80% | ~45% → 65% |

## 四、边界测试编写模板

```python
"""<模块名> 边界测试"""
import pytest
from agent.<module_path> import <ClassName>

class Test<Module>Boundary:
    """边界场景测试"""

    def test_empty_input(self):
        """空输入边界"""
        with pytest.raises(ValueError):
            <ClassName>.process(None)

    def test_timeout(self):
        """超时边界"""
        with pytest.raises(TimeoutError):
            <ClassName>.execute(timeout=0.001)

    def test_invalid_param(self):
        """非法参数边界"""
        with pytest.raises((ValueError, TypeError)):
            <ClassName>.process("invalid_param")

    def test_extreme_value(self):
        """极值边界"""
        result = <ClassName>.process(999999999)
        assert result is not None

    def test_boundary_condition(self):
        """临界条件"""
        result = <ClassName>.process(0)
        assert result is not None
```

## 五、验证命令

```bash
# 1. 运行新增的边界测试
python -m pytest tests/boundary/test_circuit_breaker_boundary.py tests/boundary/test_rate_limiter_boundary.py -v

# 2. 检查边界覆盖率
python scripts/check_boundary_coverage.py --json-only

# 3. 生成 test_coverage 基线
python -m pytest --cov=agent --cov-report=xml:coverage.xml tests/ -q --timeout=60

# 4. 运行可见性报告
python scripts/visibility_report.py --config config.yaml --output docs/observability/visibility_report.md

# 5. Git 提交
git add tests/boundary/ coverage.xml docs/observability/
git commit -m "test(observability): BT-001~012 补充边界测试 + test_coverage 基线"
```

## 六、风险与缓解

| 风险 | 概率 | 缓解措施 |
|------|------|---------|
| 零测试模块缺少测试基础设施 | 高 | 先阅读模块源码，理解接口后再编写 |
| 全量 test_coverage 运行超时 | 中 | 使用 `--timeout=60` 限制单测超时 |
| 边界测试用例设计不当 | 中 | 参照缺失场景清单，确保每场景至少 1 个用例 |
| coverage.xml 生成失败 | 低 | 分批运行，先生成部分模块覆盖率 |

## 七、优先级排序依据

1. **boundary_test_coverage 优先于 test_coverage**：
   - 边界测试编写更快（每用例 5-10 分钟 vs 单元测试 15-30 分钟）
   - 边界测试对 test_coverage 也有正向贡献
   - 边界测试更能体现「验证过程可见」的核心价值

2. **零测试模块优先于低覆盖模块**：
   - 零测试模块的边际收益更高（从 0 到 1 > 从 1 到 2）
   - 建立测试基础设施后，后续补充更容易

3. **circuit_breaker / rate_limiter 优先**：
   - 这两个模块是 Phase 2 新增的核心稳定性组件
   - 无测试保护的风险最高
