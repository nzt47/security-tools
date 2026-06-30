# 混沌工程测试报告

**测试时间**: 2026-06-24T19:34:41.345362

## 测试概述

- 测试总数: 6
- 通过: 5
- 失败: 1

## 测试详情

### 网络延迟测试

- **故障类型**: network_delay
- **测试时间**: 2026-06-24T19:34:27.127440
- **持续时间**: 9313.40ms
- **结果**: ✅ 通过

**观测结果**:
- ✅ 网络延迟注入成功，平均延迟: 3102.00ms
- ✅ 追踪ID正常生成: 117b4f13fa9b4eb6

### 网络超时测试

- **故障类型**: network_timeout
- **测试时间**: 2026-06-24T19:34:36.444628
- **持续时间**: 25.23ms
- **结果**: ✅ 通过

**观测结果**:
- ✅ 捕获到超时异常: Chaos injection: Network timeout
- ✅ 捕获到超时异常: Chaos injection: Network timeout
- ✅ 捕获到超时异常: Chaos injection: Network timeout
- ✅ 所有请求均触发超时

### 服务不可用测试

- **故障类型**: service_unavailable
- **测试时间**: 2026-06-24T19:34:36.469857
- **持续时间**: 5.26ms
- **结果**: ✅ 通过

**观测结果**:
- ✅ 捕获到服务不可用: Chaos injection: Service unavailable (503)
- ✅ 捕获到服务不可用: Chaos injection: Service unavailable (503)
- ✅ 捕获到服务不可用: Chaos injection: Service unavailable (503)
- ✅ 所有请求均触发服务不可用

### 内存压力测试

- **故障类型**: memory_pressure
- **测试时间**: 2026-06-24T19:34:36.475722
- **持续时间**: 4216.03ms
- **结果**: ✅ 通过

**观测结果**:
- ✅ 内存压力注入成功，使用: 626.90 MB
- ✅ 内存压力下操作正常
- ✅ 内存清理成功，当前使用: 77.30 MB

### CPU压力测试

- **故障类型**: cpu_pressure
- **测试时间**: 2026-06-24T19:34:40.692719
- **持续时间**: 9.31ms
- **结果**: ❌ 失败

**问题列表**:
- 测试执行失败: Can't pickle local object 'ChaosInjector.inject_cpu_pressure.<locals>.cpu_eater_process'

### 高并发压力测试

- **故障类型**: concurrent_pressure
- **测试时间**: 2026-06-24T19:34:40.702991
- **持续时间**: 612.46ms
- **结果**: ✅ 通过

**观测结果**:
- ✅ 完成 100/100 请求
- ✅ 平均延迟: 104.17ms
- ✅ 最大延迟: 149.60ms

## 改进建议

### 发现的问题

- 测试执行失败: Can't pickle local object 'ChaosInjector.inject_cpu_pressure.<locals>.cpu_eater_process'

### 建议措施

## 统计摘要

### 故障注入统计

- 活跃故障数: 0
- 总注入次数: 4
- 总触发次数: 9
- 受影响请求数: 9
