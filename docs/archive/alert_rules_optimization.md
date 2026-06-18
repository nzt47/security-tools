# 📋 告警规则优化对比报告

**优化日期**: 2026-06-09  
**数据来源**: Yunshu 实际运行指标分析

---

## 🎯 优化概览

### 优化前后对比

| 指标 | 优化前阈值 | 优化后阈值 | 变化幅度 | 理由 |
|------|------------|------------|----------|------|
| **错误率 warning** | >10% | >5% | ⬇️ 50% | 系统稳定，应更敏感 |
| **错误率 critical** | 新增 | >20% | ➕ 新增 | 填补阈值空白 |
| **错误率 emergency** | >50% | >50% | ➖ 不变 | 保持原有标准 |
| **延迟 warning** | >5s | >500ms | ⬇️ 90% | 当前性能优秀 |
| **延迟 critical** | >10s | >1s | ⬇️ 90% | 用户体验优先 |
| **延迟 emergency** | 新增 | >2s (99 分位) | ➕ 新增 | 极端情况监控 |
| **安全 warning** | >10 次/分 | >3 次/分 | ⬇️ 70% | 正常频率很低 |
| **安全 critical** | >5 次/分 | >10 次/分 | ⬆️ 100% | 重新定级 |
| **安全 emergency** | 新增 | >30 次/分 | ➕ 新增 | 大规模攻击检测 |
| **CPU warning** | >80% | >70% | ⬇️ 12.5% | 提前预警 |
| **CPU critical** | >95% | >90% | ⬇️ 5% | 更保守 |
| **内存 warning** | >80% | >80% | ➖ 不变 | 合理 |
| **内存 critical** | >95% | >90% | ⬇️ 5% | 提前预警 |

---

## 📊 优化依据

### 1. 错误率告警优化

**实际数据**:
- 当前系统运行稳定
- 5xx 错误极少
- 性能优秀

**优化理由**:
```
原配置：warning >10%, emergency >50%
问题：阈值过高，可能错过早期异常
优化：warning >5%, critical >20%, emergency >50%
优势：更早发现问题，分级响应
```

### 2. 延迟告警优化

**实际性能数据**:
```
api_health:   平均 9.26ms, 99% < 25ms
api_sensors:  平均 2.69ms, 99% < 25ms
api_mode:     平均 2.47ms, 99% < 25ms
static:       平均 102.9ms, 100% < 500ms
```

**优化理由**:
```
原配置：95 分位 >5s (warning), 99 分位 >10s (critical)
问题：阈值过于宽松，5 秒响应已严重影响体验
优化：95 分位 >500ms (warning), >1s (critical), 99 分位 >2s (emergency)
优势：
  - 500ms 是用户可感知的临界点
  - 1s 响应明显延迟
  - 2s 以上不可接受
```

### 3. 安全拦截告警优化

**实际拦截频率**:
```
正常情况：3-5 次/天
测试数据：高峰期 10-20 次/小时
```

**优化理由**:
```
原配置：warning >10 次/分，critical >5 次/分
问题：
  - 阈值倒挂（critical 比 warning 低）
  - 10 次/分对于正常流量过高
优化：warning >3 次/分，critical >10 次/分，emergency >30 次/分
优势：
  - 3 次/分已经异常（正常每天仅几次）
  - 10 次/分可能是有组织的攻击
  - 30 次/分是大规模攻击
```

### 4. 系统资源告警优化

**当前资源使用**:
```
CPU: 25.1%
内存：76.8%
```

**优化理由**:
```
CPU:
  原配置：warning >80%, critical >95%
  优化：warning >70%, critical >90%
  理由：提前预警，留出响应时间

内存:
  原配置：warning >80%, critical >95%
  优化：warning >80%, critical >90%
  理由：当前已 76.8%，80% 合理，90% 更保守
```

### 5. 新增告警类型

#### 对话系统错误率
```promql
新增：
  - warning: 异常率 >10%
  - critical: 异常率 >30%

理由：对话系统是核心功能，需要独立监控
```

#### 无流量检测
```promql
新增：
  - warning: 10 分钟无请求

理由：检测服务是否"假死"（进程在但无法处理请求）
```

#### 高连接数
```promql
新增：
  - warning: 连接数 >100

理由：检测可能的 DDoS 攻击或连接泄漏
```

---

## 🔧 告警级别说明

### 分级标准

| 级别 | 响应时间 | 影响范围 | 通知方式 |
|------|----------|----------|----------|
| **warning** | 30 分钟内处理 | 轻微影响 | 邮件/IM |
| **critical** | 10 分钟内处理 | 功能受损 | 邮件 + IM+ 电话 |
| **emergency** | 立即响应 | 服务中断 | 邮件 + IM+ 电话 + 短信 |

### 告警分级示例

**错误率**:
- warning (5%): 少量请求失败，用户可能未感知
- critical (20%): 明显错误，用户体验受损
- emergency (50%): 服务基本不可用

**延迟**:
- warning (500ms): 响应变慢，但可接受
- critical (1s): 明显卡顿
- emergency (2s): 用户可能放弃等待

**安全**:
- warning (3 次/分): 可能是误操作或扫描
- critical (10 次/分): 有组织的攻击
- emergency (30 次/分): 大规模攻击，需要立即响应

---

## 📈 预期效果

### 告警敏感度提升

| 场景 | 优化前 | 优化后 | 改进 |
|------|--------|--------|------|
| 延迟从 10ms 升至 600ms | ❌ 无告警 | ✅ warning | 提前 8 倍发现 |
| 错误率 8% | ❌ 无告警 | ✅ warning | 早期预警 |
| 安全攻击 5 次/分 | ❌ 无告警 | ✅ warning | 及时发现 |
| CPU 75% | ❌ 无告警 | ✅ warning | 提前响应 |

### 误报率控制

**优化策略**:
1. 引入 `for` 持续时间（5m/3m/2m）
2. 使用速率指标（rate）而非绝对值
3. 分级响应，避免告警疲劳

**预期误报率**: <5%

---

## 🎯 生产环境建议

### 1. 告警通知配置

**Prometheus Alertmanager 配置示例**:

```yaml
route:
  group_by: ['alertname', 'severity']
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 4h
  receiver: 'default'
  routes:
    - match:
        severity: warning
      receiver: 'email'
    - match:
        severity: critical
      receiver: 'slack+phone'
    - match:
        severity: emergency
      receiver: 'all-channels'
```

### 2. Grafana 告警

**配置建议**:
- 使用 Prometheus 数据源
- 配置通知渠道（钉钉/企业微信/飞书）
- 设置值班表

### 3. 告警优化循环

**每周回顾**:
1. 统计告警数量和误报率
2. 调整不合理的阈值
3. 添加新的告警规则（基于故障）
4. 删除无效告警

---

## 📚 参考文档

- [原始告警配置](file:///c:/Users/Administrator/agent/monitoring/alerts.yml)
- [生产环境配置](file:///c:/Users/Administrator/agent/monitoring/alerts_production.yml)
- [指标分析报告](file:///c:/Users/Administrator\agent\metrics_analysis_report.md)
- [部署指南](file:///c:/Users/Administrator/agent/PROMETHEUS_GRAFANA_DEPLOYMENT_GUIDE.md)

---

**文档生成**: AI Assistant  
**数据来源**: Yunshu 生产环境指标  
**优化日期**: 2026-06-09
