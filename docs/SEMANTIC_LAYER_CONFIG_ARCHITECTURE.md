# 语义层配置持久化架构选型建议

> 版本: v1.0 | 日期: 2026-08-01 | 基于性能报告 PERFORMANCE_REPORT_sqlite_vs_etcd.md

## 1. 背景

语义层配置（`min_score`、`enabled`、`top_k` 等）支持运行时热更，需要持久化方案保证重启后配置不丢失。经性能对比测试（5000 并发 × 10 线程），SQLite 与 etcd 两种方案在延迟、吞吐、适用场景上存在显著差异。

## 2. 方案对比

### 2.1 性能数据（5000 并发 × 10 线程）

| 指标 | SQLite + 内存缓存 | etcd（模拟） | 倍数 |
|------|-------------------|-------------|------|
| P50 延迟 | 0.656 ms | 3.575 ms | 5.4x |
| P95 延迟 | 0.808 ms | 5.447 ms | 6.7x |
| P99 延迟 | 0.966 ms | 8.358 ms | 8.7x |
| 平均延迟 | 0.667 ms | 3.686 ms | 5.5x |
| QPS | 1499/s | 271/s | 5.5x |
| 总耗时 | 0.34 s | 1.91 s | 5.6x |

**SLA 基线**: P99 < 40ms（Prometheus warning 阈值）

### 2.2 特性对比

| 维度 | SQLite + 内存缓存 | etcd |
|------|-------------------|------|
| 延迟 | 极低（~0.01ms 内存命中） | 中（1-5ms 网络开销） |
| 依赖 | 零（复用现有 SQLite） | 新增 etcd3 库 + etcd 集群 |
| 多实例共享 | ❌ 不共享 | ✅ 全局共享 |
| 持久化 | ✅ 重启恢复 | ✅ 重启恢复 |
| 一致性 | ACID 事务 | 最终一致（秒级） |
| 运维成本 | 低（本地文件） | 中（集群维护） |
| 灰度发布 | ❌ 不支持 | ✅ 支持 |

## 3. 推荐方案：SQLite + 内存缓存（默认）

### 3.1 架构

```
┌─────────────────────────────────────────────────────┐
│                   Orchestrator                      │
│                                                     │
│  _load_semantic_layer_config()  ← 统一配置读取入口   │
│    ├── 层0: _SEM_DEFAULTS        (硬编码默认值)      │
│    ├── 层1: config.yaml          (mtime 缓存)       │
│    ├── 层2: 环境变量             (运维 hotfix)      │
│    └── 层3: _SEM_API_OVERRIDE    (API 热更, 内存)   │
│                ↑                                    │
│                ├── 启动时从 SQLite 恢复              │
│                └── 运行时 API 热更写入 SQLite        │
│                                                     │
│  data/orchestrator_config.db (SQLite WAL)           │
│    └── semantic_config_overrides 表                 │
└─────────────────────────────────────────────────────┘
```

### 3.2 选型理由

1. **性能最优**: P99=0.966ms，远低于 40ms SLA，性能余量 97.6%
2. **零新依赖**: 复用项目现有 SQLite 基础设施（thread-local + busy_timeout=5000）
3. **ACID 保证**: SQLite 事务确保配置写入的原子性和一致性
4. **低运维**: 本地文件，无需维护额外集群
5. **配置读取走内存**: `_SEM_API_OVERRIDE` 是内存读取（~0.01ms），SQLite 仅在启动/热更时访问

### 3.3 适用场景

- 单机部署
- 小规模集群（≤3 副本，各自独立配置）
- 开发/测试环境
- 配置读取频率高（每次 `process()` 调用）

## 4. etcd 方案：仅用于多副本同步

### 4.1 架构

```
┌──────────────┐     watch 推送     ┌─────────────────┐
│              │ ──────────────────→ │  副本 1 内存    │
│  etcd 配置   │     watch 推送     ├─────────────────┤
│    中心      │ ──────────────────→ │  副本 2 内存    │
│              │     watch 推送     ├─────────────────┤
└──────────────┘ ──────────────────→ │  副本 N 内存    │
                                     └─────────────────┘
                                            ↓
                                   process() 读取内存
                                   (~0.01ms, 不走网络)
```

### 4.2 选型理由

1. **多副本共享**: 所有副本通过 watch 推送获得一致配置
2. **灰度发布**: 支持按副本/标签逐步推送配置变更
3. **运行时仍走内存**: etcd 仅在配置变更时推送，process() 读取走 `_SEM_API_OVERRIDE` 内存（~0.01ms）

### 4.3 适用场景（仅以下场景选用）

- K8s 多副本部署（≥3 副本，需配置全局一致）
- 灰度发布需求（按副本逐步推送配置）
- 配置中心统一管理需求（多服务共享配置）

### 4.4 不适用场景

- 单机部署（过度设计，违【简易】）
- 配置读取频率高（etcd 网络延迟 1-5ms，是 SQLite 的 5-9 倍）
- 无 K8s 基础设施（etcd 集群运维成本高）

## 5. 实施建议

### 5.1 默认部署（SQLite + 内存缓存）

当前代码已实现，无需额外配置：

```yaml
# config.yaml
orchestrator:
  semantic_layer:
    enabled: true
    min_score: 0.3
    top_k: 5
```

热更通过 API：
```bash
POST /api/orchestrator/semantic-config
{"min_score": 0.5}
```

重启后自动从 `data/orchestrator_config.db` 恢复。

### 5.2 多副本部署（etcd + 内存缓存）

启用 etcd 集成：

```bash
# 环境变量
ETCD_ENABLED=true
ETCD_HOST=etcd-service.default.svc.cluster.local
ETCD_PORT=2379
```

```python
# app_server.py 启动时
from agent.config.etcd_config_client import init_etcd_config_integration
init_etcd_config_integration()
```

etcd watch 推送配置变更到 `_SEM_API_OVERRIDE`（内存），process() 读取走内存。

### 5.3 配置读取统一入口

所有配置读取必须通过 `_load_semantic_layer_config()`，禁止直接访问 config.yaml 或环境变量。已审计确认当前代码无绕过路径。

## 6. 决策矩阵

| 场景 | 推荐方案 | 理由 |
|------|----------|------|
| 单机/开发 | SQLite + 内存 | P99<1ms，零依赖 |
| 小规模生产（≤3 副本）| SQLite + 内存 | 各副本独立配置可接受 |
| K8s 多副本（≥3 副本）| etcd + 内存 | 全局共享 + watch 推送 |
| 灰度发布 | etcd + 内存 | 按副本逐步推送 |
| 高频配置读取 | 内存（任意方案）| _SEM_API_OVERRIDE ~0.01ms |

## 7. 性能回归防护

CI/CD 流水线集成 5000 并发压力测试（`scripts/bench_sqlite_vs_etcd.py`），每次提交自动检测 P99 回归：

- SQLite P99 基线: 0.966ms
- 回归阈值: P99 > 5ms（5 倍基线）
- 失败行为: 阻止合并 + 告警
