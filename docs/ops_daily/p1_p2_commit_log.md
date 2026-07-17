# P1 安全加固 + P2-1 固定基础镜像版本 — 提交日志
```
**生成时间**: 2026-07-18
**分支**: master
**会话范围**: P1 修复实施 + P2-1 基础镜像固定 + HEALTHCHECK/readinessProbe 运行时验证
```
---
```
## 一、提交记录
```
本次会话共产生 3 个 commit：
```
| 序号 | Commit Hash | 类型 | 说明 |
|------|-------------|------|------|
| 1 | `80cbd1d9` | feat(ops) | P1 安全加固（HEALTHCHECK + readinessProbe + readOnlyRootFilesystem） |
| 2 | `22c34cc1` | fix(ops) | 修复 slim 镜像 pgrep 不可用 + P2-1 固定基础镜像版本 |
```
> **注**：序号 1 为 P1 初始提交，序号 2 为运行时验证发现的 bug 修复 + P2-1 合并提交。
```
---
```
## 二、Commit 1: `80cbd1d9` — P1 安全加固
```
### 提交信息
```
```
feat(ops): P1 安全加固（HEALTHCHECK + readinessProbe + readOnlyRootFilesystem）
 3 files changed, 29 insertions(+)
```
```
### 修改文件清单
```
| 文件 | 修改内容 | 行数 |
|------|----------|------|
| `docker/ops-reporter/Dockerfile` | P1-1: 添加 HEALTHCHECK 指令 | +5 |
| `deploy/helm/tlm-ops-reporter/templates/deployment.yaml` | P1-2: 添加 readinessProbe + P1-3: container securityContext | +13 |
| `deploy/helm/tlm-ops-reporter/values.yaml` | P1-3: 新增 containerSecurityContext 配置块 | +11 |
```
### P1-1: Dockerfile HEALTHCHECK
```
```dockerfile
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD pgrep -f entrypoint.sh > /dev/null || pgrep -f generate_ops_daily_report > /dev/null || exit 1
```
```
**参数说明**：
- `--interval=60s`: 每 60 秒检查一次
- `--timeout=10s`: 单次检查超时 10 秒
- `--start-period=30s`: 容器启动后 30 秒内失败不计入 retries
- `--retries=3`: 连续 3 次失败标记为 unhealthy
```
### P1-2: readinessProbe
```
```yaml
readinessProbe:
  exec:
    command:
      - sh
      - -c
      - "test -d {{ .Values.reporter.logDir }} && test -d {{ .Values.reporter.outputDir }} && test -f /app/generate_ops_daily_report.py"
  initialDelaySeconds: 5
  periodSeconds: 30
  timeoutSeconds: 5
```
```
**检测逻辑**：日志目录存在 + 输出目录存在 + 脚本文件可访问
```
### P1-3: containerSecurityContext
```
```yaml
containerSecurityContext:
  readOnlyRootFilesystem: true
  allowPrivilegeEscalation: false
  capabilities:
    drop:
      - ALL
```
```
**安全收益**：
- 只读根文件系统：容器内 /app 只读，写入仅限挂载的 PVC
- 禁止提权：子进程无法获取比父进程更多权限
- 丢弃所有 capabilities：最小权限原则
```
---
```
## 三、Commit 2: `22c34cc1` — pgrep 修复 + P2-1
```
### 提交信息
```
```
fix(ops): 修复 slim 镜像 pgrep 不可用 + P2-1 固定基础镜像版本
 2 files changed, 13 insertions(+), 6 deletions(-)
```
```
### 修改文件清单
```
| 文件 | 修改内容 |
|------|----------|
| `docker/ops-reporter/Dockerfile` | 修复 HEALTHCHECK 命令 + P2-1 FROM 固定版本 |
| `deploy/helm/tlm-ops-reporter/templates/deployment.yaml` | 修复 livenessProbe 命令 |
```
### 发现的问题（P0 级）
```
**问题**：P1 提交后运行时验证发现 `python:3.11-slim` 镜像未安装 `procps` 包，导致：
- `pgrep` 命令不存在（`sh: pgrep: not found`）
- `ps` 命令不存在
- HEALTHCHECK 持续失败，容器状态变为 unhealthy
- livenessProbe 在 K8s 中也会失败
```
**根因**：Debian slim 镜像为减小体积，默认不安装 procps 包
```
### 修复方案
```
改用 `grep -qaE` 读取 `/proc/1/cmdline` 检测 PID 1 进程：
```
```dockerfile
# 修复前（pgrep 不可用）
CMD pgrep -f entrypoint.sh > /dev/null || pgrep -f generate_ops_daily_report > /dev/null || exit 1
```
# 修复后（grep /proc/1/cmdline）
CMD grep -qaE 'entrypoint.sh|generate_ops_daily_report' /proc/1/cmdline 2>/dev/null || exit 1
```
```
**方案优势**：
1. 不依赖 pgrep/ps，兼容 slim 镜像
2. 只检测 PID 1，避免 `grep /proc/*/cmdline` 的自匹配问题
3. 两种模式都有效：
   - cron 模式：PID 1 = `/bin/sh /app/entrypoint.sh`
   - once 模式：PID 1 = `python /app/generate_ops_daily_report.py`
```
### P2-1: 固定基础镜像版本
```
```dockerfile
# 修复前（浮动版本）
FROM python:3.11-slim
```
# 修复后（固定补丁版本 + 发行版代号）
FROM python:3.11.9-slim-bookworm
```
```
**收益**：构建可重现，避免上游小版本更新引入 zoneinfo/ssl 等行为差异
```
---
```
## 四、运行时验证结果
```
### 验证环境
```
- Docker: 29.4.3
- 基础镜像: `python:3.11-slim`（本地缓存，P2-1 的 `python:3.11.9-slim-bookworm` 因网络问题未验证构建）
- 测试镜像: `tlm-ops-reporter:v1.2-test`
```
### 验证用例
```
| # | 验证项 | 方法 | 结果 |
|---|--------|------|------|
| 1 | HEALTHCHECK 配置 | `docker inspect` 查看 Healthcheck 字段 | ✅ Interval=60s, Timeout=10s, StartPeriod=30s, Retries=3 |
| 2 | HEALTHCHECK 执行 | 启动容器后等待 35s 查看状态 | ✅ `healthy`, ExitCode=0 |
| 3 | readinessProbe 探针 | `docker exec` 执行 test 命令 | ✅ 返回 0（READY） |
| 4 | livenessProbe 探针 | `docker exec` 执行 grep 命令 | ✅ 返回 0（HEALTHY） |
| 5 | pgrep 不可用确认 | `command -v pgrep` | ✅ MISSING（确认根因） |
| 6 | grep 可用确认 | `command -v grep` | ✅ AVAILABLE |
| 7 | /proc/1/cmdline 可读 | `cat /proc/1/cmdline` | ✅ `/bin/sh /app/entrypoint.sh --cron` |
| 8 | 自匹配问题验证 | grep 不存在进程名 | ✅ `/proc/1/cmdline` 方案无自匹配 |
```
### 健康检查日志
```
```
2026-07-17 16:35:53 UTC ExitCode=0 Output=
```
```
容器状态：`Up 46 seconds (healthy)`
```
---
```
## 五、不变量验证
```
| 不变量 | 状态 |
|--------|------|
| cron 模式 while+sleep 不变 | ✅ 未修改 entrypoint.sh |
| 非 root 用户运行（UID 1000） | ✅ 未修改 USER 指令 |
| PVC 挂载 readOnly + ReadWriteOnce | ✅ 未修改 volumeMounts |
| Helm Chart 结构完整 | ✅ 未删除任何模板文件 |
| zoneinfo 时区处理 | ✅ 未修改 Python 脚本 |
```
---
```
## 六、待办事项
```
### 已完成
```
- ✅ P1-1: Dockerfile HEALTHCHECK
- ✅ P1-2: Helm Chart readinessProbe
- ✅ P1-3: containerSecurityContext (readOnlyRootFilesystem)
- ✅ P2-1: 固定基础镜像版本
- ✅ pgrep 不可用 bug 修复
- ✅ 运行时验证（HEALTHCHECK healthy + readinessProbe READY）
```
### 待验证
```
- ⚠️ P2-1 的 `python:3.11.9-slim-bookworm` 因网络问题未验证构建（需网络恢复后执行 `docker build`）
```
### 待实施（P2 剩余项）
```
- P2-2: .dockerignore 补充（`docs/`、`deploy/`、`tests/`、`*.md`）
- P2-3: README 添加多架构构建说明
- P2-4: NetworkPolicy 模板（需测试验证）
```
---
```
## 七、Git 历史
```
```
22c34cc1 fix(ops): 修复 slim 镜像 pgrep 不可用 + P2-1 固定基础镜像版本
80cbd1d9 feat(ops): P1 安全加固（HEALTHCHECK + readinessProbe + readOnlyRootFilesystem）
7c0b0be3 test(ops): Compose 本地测试环境 + Dockerfile/Helm 最佳实践审查
15616f3a feat(ops): TLM 运维监控套件 v1.1（容器化 + Helm Chart + 故障手册）
```
