# Dockerfile & Helm Chart 最佳实践审查报告

> 审查时间: 2026-07-18
> 审查范围: docker/ops-reporter/Dockerfile + deploy/helm/tlm-ops-reporter/
> 审查焦点: sqlite-vec 版本兼容性 + 资源限制最佳实践
> 审查规范: engineering-test-delivery

---

## 1. 审查摘要

| 维度 | 严重度 | 数量 | 状态 |
|------|--------|------|------|
| 🔴 P0 阻断 | 0 | 0 | ✅ 通过 |
| 🟡 P1 重要 | 3 | 3 | ⚠️ 建议修复 |
| 🔵 P2 改进 | 4 | 4 | 💡 可选优化 |
| ✅ 已符合 | 8 | 8 | ✅ 良好 |

**结论**：无阻断项，核心契约（不易约束）全部守住。3 项 P1 建议在下一版本修复。

---

## 2. sqlite-vec 版本兼容性审查

### 2.1 ops-reporter 容器（本次审查对象）

| 检查项 | 结果 | 说明 |
|--------|------|------|
| 是否依赖 sqlite-vec | ❌ 不依赖 | ops-reporter 只读日志生成日报，不导入 sqlite_vec |
| 是否需要固定版本 | ❌ 不需要 | 无 sqlite-vec 依赖，无需版本固定 |
| 日报脚本是否解析 sqlite-vec 错误 | ✅ 是 | 脚本解析 `vec.import_failed` / `vec.load_failed` 等 action |

**结论**：ops-reporter 容器本身**不涉及 sqlite-vec 版本兼容性问题**。

### 2.2 主应用容器（关联影响）

> ⚠️ 主应用（digital-life）使用 sqlite-vec，但不在本次审查范围。以下为关联影响分析：

| 检查项 | 结果 | 建议 |
|--------|------|------|
| requirements.txt 是否固定 sqlite-vec | ❌ 缺失 | **需在主应用 requirements.txt 中添加 `sqlite-vec>=0.1.0`** |
| Dockerfile 是否安装 sqlite-vec | ❌ 缺失 | 主应用 Dockerfile 应 `pip install sqlite-vec` |
| Helm Chart 是否声明 sqlite-vec 依赖 | ❌ 缺失 | 主应用 Chart 应在 README 说明 sqlite-vec 版本要求 |
| 日报脚本是否兼容多版本错误格式 | ✅ 是 | 脚本用 action 字段匹配，不依赖具体错误消息 |

### 2.3 sqlite-vec 版本兼容性矩阵

基于代码分析（[holographic_adapter.py:196-251](../agent/memory/adapters/holographic_adapter.py)）：

| sqlite-vec 版本 | 加载方式 | 兼容性 | 说明 |
|-----------------|----------|--------|------|
| ≥0.1.0 | `sqlite_vec.load(conn)` Python 适配器 | ✅ 推荐 | TLM_DESIGN §5.2 确认可用 |
| ≥0.1.0 | `conn.load_extension('sqlite_vec')` 原生 | ✅ 兜底 | 需 SQLite 编译启用 ENABLE_LOAD_EXTENSION |
| <0.1.0 | 不支持 | ❌ 不兼容 | 缺少 `sqlite_vec.load` API |
| 未安装 | 降级为纯 FTS5 | ✅ 自动降级 | `_vec_available=False`，不抛异常 |

**建议**：在主应用 requirements.txt 中固定 `sqlite-vec==0.1.6`（或最新稳定版）。

---

## 3. 资源限制审查

### 3.1 Dockerfile 资源限制

| 检查项 | 结果 | 说明 |
|--------|------|------|
| 基础镜像固定 | ⚠️ 部分 | `python:3.11-slim` 固定主版本，但 slim 标签会浮动 |
| 非 root 运行 | ✅ 符合 | `USER reporter` (uid 1000) |
| 内存优化 | ✅ 符合 | `PYTHONDONTWRITEBYTECODE=1` 避免 .pyc 缓存 |
| 日志输出优化 | ✅ 符合 | `PYTHONUNBUFFERED=1` 确保日志实时输出 |
| HEALTHCHECK | ❌ 缺失 | Dockerfile 无 HEALTHCHECK，依赖 Compose/K8s 探针 |

### 3.2 docker-compose.yml 资源限制

| 检查项 | 结果 | 说明 |
|--------|------|------|
| CPU 限制 | ✅ 符合 | `cpus: '0.5'`（日报生成峰值足够） |
| 内存限制 | ✅ 符合 | `memory: 256M`（Python 脚本 + 解析缓冲） |
| 日志驱动限制 | ✅ 符合 | `max-size: "10m", max-file: "3"` |
| 重启策略 | ✅ 符合 | `restart: unless-stopped` |

### 3.3 Helm Chart 资源限制

| 检查项 | 结果 | 说明 |
|--------|------|------|
| requests 配置 | ✅ 符合 | cpu 100m, memory 128Mi |
| limits 配置 | ✅ 符合 | cpu 500m, memory 256Mi |
| QoS 等级 | ✅ Burstable | requests < limits，适合非关键路径 |
| Pod 安全上下文 | ⚠️ 部分 | 缺 `readOnlyRootFilesystem: true` |
| PDB | ❌ 缺失 | replicas=1 时无意义，但建议预留 |
| NetworkPolicy | ❌ 缺失 | ops-reporter 无需网络访问，建议添加限制 |

---

## 4. P1 重要问题清单

### 🔴 P1-1: Dockerfile 缺失 HEALTHCHECK

**影响**：docker-compose 场景下无法自动检测容器健康状态。

**当前**：
```dockerfile
# 无 HEALTHCHECK 指令
ENTRYPOINT ["/app/entrypoint.sh"]
```

**建议修复**：
```dockerfile
# [不易] 健康检查：cron 模式下 entrypoint.sh 进程应存活
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD pgrep -f entrypoint.sh || pgrep -f generate_ops_daily_report || exit 1
```

---

### 🔴 P1-2: Helm Chart 缺失 readinessProbe

**影响**：K8s 无法判断 Pod 是否就绪接收流量（虽然 ops-reporter 无 Service，但影响 Deployment 状态判断）。

**当前**：只有 livenessProbe。

**建议修复**：在 deployment.yaml 中添加：
```yaml
readinessProbe:
  exec:
    command: ["sh", "-c", "pgrep -f entrypoint.sh"]
  initialDelaySeconds: 10
  periodSeconds: 30
```

---

### 🔴 P1-3: Pod 安全上下文缺 readOnlyRootFilesystem

**影响**：容器内进程可写根文件系统，存在被篡改风险。

**当前**：
```yaml
podSecurityContext:
  runAsNonRoot: true
  runAsUser: 1000
  fsGroup: 1000
```

**建议修复**：
```yaml
podSecurityContext:
  runAsNonRoot: true
  runAsUser: 1000
  fsGroup: 1000
  readOnlyRootFilesystem: true  # 根文件系统只读
```

**注意**：需要确保 `/app/output` 和 `/tmp` 有可写 volumeMount：
```yaml
- name: tmp
  mountPath: /tmp
```

---

## 5. P2 改进建议

### 🔵 P2-1: 基础镜像固定小版本

**当前**：`python:3.11-slim`
**建议**：`python:3.11.9-slim-bookworm`
**理由**：slim 标签会随 Debian 版本浮动，固定 bookworm 确保可重现构建。

### 🔵 P2-2: 添加 .dockerignore

**当前**：无 .dockerignore
**建议**：在项目根目录添加 .dockerignore：
```
.git
.github
docs/
tests/
data/
logs/
*.md
__pycache__
```
**理由**：减少构建上下文体积，加速构建。

### 🔵 P2-3: 多架构构建支持

**当前**：只构建 amd64
**建议**：在 README 中说明多架构构建：
```bash
docker buildx build --platform linux/amd64,linux/arm64 \
  -t tlm-ops-reporter:v1.1 -f docker/ops-reporter/Dockerfile . --push
```
**理由**：支持 ARM 集群（如 AWS Graviton / 阿里云倚天）。

### 🔵 P2-4: NetworkPolicy 限制出站

**建议**：添加 NetworkPolicy 模板：
```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: {{ include "tlm-ops-reporter.fullname" . }}
spec:
  podSelector:
    matchLabels:
      {{- include "tlm-ops-reporter.selectorLabels" . | nindent 6 }}
  policyTypes:
    - Egress
  egress: []  # 拒绝所有出站（ops-reporter 无需网络）
```

---

## 6. 已符合的最佳实践

| # | 实践 | 落实位置 |
|---|------|----------|
| 1 | 非 root 运行 | Dockerfile `USER reporter` + Helm `runAsNonRoot: true` |
| 2 | 最小镜像 | python:3.11-slim（45.5MB content size） |
| 3 | 无 apt-get 网络依赖 | 去掉 tzdata/cron 安装，避免网络瓶颈 |
| 4 | CRLF 行尾修复 | `sed -i 's/\r$//'` |
| 5 | 日志目录只读挂载 | `readOnly: true` |
| 6 | 资源限制（CPU+内存） | Compose + Helm 双重设置 |
| 7 | 日志驱动限制 | `max-size: "10m", max-file: "3"` |
| 8 | Recreate 策略 | 避免 PVC ReadWriteOnce 冲突 |

---

## 7. 修复优先级建议

| 优先级 | 问题 | 修复成本 | 建议时间 |
|--------|------|----------|----------|
| P1-1 | Dockerfile HEALTHCHECK | 低（5min） | 立即 |
| P1-2 | Helm readinessProbe | 低（10min） | 立即 |
| P1-3 | readOnlyRootFilesystem | 中（30min） | 下一版本 |
| P2-1 | 镜像小版本固定 | 低（5min） | 下一版本 |
| P2-2 | .dockerignore | 低（5min） | 立即 |
| P2-3 | 多架构构建 | 中（文档） | 按需 |
| P2-4 | NetworkPolicy | 中（20min） | 生产部署前 |

---

## 8. 不变量验证

| 约束 | 验证结果 |
|------|----------|
| 日报脚本接口（--log-dir/--output/--date）不变 | ✅ 守住 |
| 告警规则 YAML 内容不变 | ✅ 守住 |
| 日志目录只读挂载 | ✅ 守住 |
| 非 root 运行 | ✅ 守住 |
| sqlite-vec 不可用时降级（主应用层） | ✅ 守住（ops-reporter 不涉及） |

---

## 9. 附录：测试验证结果

本地 Compose 测试环境 6/6 通过：

| # | 测试 | 结果 | 耗时 |
|---|------|------|------|
| 1 | Compose 启动（3 服务） | ✅ PASS | 6431ms |
| 2 | Prometheus 健康检查 | ✅ PASS | 83ms |
| 3 | 告警规则加载（5 组 15 条） | ✅ PASS | 3ms |
| 4 | 日报生成（6 种 action） | ✅ PASS | 2000ms |
| 5 | 空日志目录边界测试 | ✅ PASS | 575ms |
| 6 | 日报生成性能（<5s） | ✅ PASS | 675ms |

详见: [compose_test_audit_report.md](ops_daily/compose_test_audit_report.md)
