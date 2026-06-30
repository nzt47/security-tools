# 混沌工程测试报告

**测试时间**: 2026-06-24T19:26:18.100543

## 测试概述

- 测试总数: 6
- 通过: 0
- 失败: 6

## 测试详情

### 网络延迟测试

- **故障类型**: network_delay
- **测试时间**: 2026-06-24T19:26:17.668946
- **持续时间**: 0.00ms
- **结果**: ❌ 失败

**问题列表**:
- 测试执行失败: type object 'datetime.datetime' has no attribute 'timedelta'

### 网络超时测试

- **故障类型**: network_timeout
- **测试时间**: 2026-06-24T19:26:17.668946
- **持续时间**: 0.00ms
- **结果**: ❌ 失败

**问题列表**:
- 测试执行失败: type object 'datetime.datetime' has no attribute 'timedelta'

### 服务不可用测试

- **故障类型**: service_unavailable
- **测试时间**: 2026-06-24T19:26:17.669941
- **持续时间**: 308.56ms
- **结果**: ❌ 失败

**问题列表**:
- ❌ 预期服务不可用但未发生
- ❌ 预期服务不可用但未发生
- ❌ 预期服务不可用但未发生
- ❌ 仅 0/3 请求触发服务不可用

### 内存压力测试

- **故障类型**: memory_pressure
- **测试时间**: 2026-06-24T19:26:17.978501
- **持续时间**: 1.00ms
- **结果**: ❌ 失败

**问题列表**:
- 测试执行失败: type object 'datetime.datetime' has no attribute 'timedelta'

### CPU压力测试

- **故障类型**: cpu_pressure
- **测试时间**: 2026-06-24T19:26:17.979503
- **持续时间**: 0.00ms
- **结果**: ❌ 失败

**问题列表**:
- 测试执行失败: type object 'datetime.datetime' has no attribute 'timedelta'

### 高并发压力测试

- **故障类型**: concurrent_pressure
- **测试时间**: 2026-06-24T19:26:17.980501
- **持续时间**: 97.53ms
- **结果**: ❌ 失败

**观测结果**:
- ✅ 完成 0/100 请求
- ✅ 平均延迟: 0.00ms
- ✅ 最大延迟: 0.00ms

**问题列表**:
- ❌ 100 个请求失败
-    - 请求 4: name 'random' is not defined
-    - 请求 5: name 'random' is not defined
-    - 请求 1: name 'random' is not defined

## 改进建议

### 发现的问题

- 测试执行失败: type object 'datetime.datetime' has no attribute 'timedelta'
- 测试执行失败: type object 'datetime.datetime' has no attribute 'timedelta'
- ❌ 预期服务不可用但未发生
- ❌ 预期服务不可用但未发生
- ❌ 预期服务不可用但未发生
- ❌ 仅 0/3 请求触发服务不可用
- 测试执行失败: type object 'datetime.datetime' has no attribute 'timedelta'
- 测试执行失败: type object 'datetime.datetime' has no attribute 'timedelta'
- ❌ 100 个请求失败
-    - 请求 4: name 'random' is not defined
-    - 请求 5: name 'random' is not defined
-    - 请求 1: name 'random' is not defined

### 建议措施

## 统计摘要

### 故障注入统计

- 活跃故障数: 0
- 总注入次数: 1
- 总触发次数: 0
- 受影响请求数: 0
