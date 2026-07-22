# v1.2 本地部署流程模拟 — 关键检查点

> **用途**: 网络恢复前，模拟 [local_verification_guide.md](local_verification_guide.md) 的 5 步部署流程，输出每步关键检查点
> **执行方式**: 纸面模拟（dry-run），不实际运行命令
> **关联文档**: [local_verification_guide.md](local_verification_guide.md)、[k8s_verification_checklist.md](k8s_verification_checklist.md)

---

## 模拟环境假设

| 项 | 值 |
|----|-----|
| OS | Windows 10 + Docker Desktop |
| kind | v0.23.0（待安装） |
| helm | v3.14.0（已预装） |
| 镜像 | tlm-ops-reporter:v1.2（待构建） |
| 集群名 | tlm-np-test |
| namespace | monitoring |

---

## 步骤 1: 安装 kind + helm

### 执行命令

```powershell
.\scripts\install_kind.ps1
kind version
helm version
```

### 检查点

| 检查项 | 预期输出 | Pass 判据 | Fail 处理 |
|--------|----------|-----------|-----------|
| CP-1.1 install_kind.ps1 退出码 | 0 | 脚本无异常退出 | 查看脚本日志，手动浏览器下载 kind 二进制 |
| CP-1.2 kind version | `kind v0.23.0 go1.21.11 .../amd64` | 输出包含 v0.23.0 | 检查 PATH 是否包含安装目录 |
| CP-1.3 helm version | `version.BuildInfo{Version:"v3.14.0"...}` | 输出包含 v3.14.0 | helm 未预装则手动安装 |
| CP-1.4 kind 路径 | `C:\Users\Administrator\bin\kind.exe` | 文件存在且可执行 | 重新运行安装脚本 |

### ⚠️ 已知风险

- **kind 下载失败**: 5 个镜像源全部超时（GitHub SSL 失败 / ghproxy 连接超时）
- **应对**: 脚本末尾已提供手动下载指引，用浏览器 + VPN 访问 GitHub releases

---

## 步骤 2: 构建镜像

### 执行命令

```powershell
# 构建
docker build -t tlm-ops-reporter:v1.2 -f docker/ops-reporter/Dockerfile .

# 验证 HEALTHCHECK
docker run --rm -d --name ops-test tlm-ops-reporter:v1.2
Start-Sleep -Seconds 35
docker inspect --format='{{.State.Health.Status}}' ops-test
docker stop ops-test
```

### 检查点

| 检查项 | 预期输出 | Pass 判据 | Fail 处理 |
|--------|----------|-----------|-----------|
| CP-2.1 docker build 退出码 | 0 | 构建成功无 ERROR | 查看 Dockerfile 日志，检查基础镜像拉取 |
| CP-2.2 基础镜像 | `python:3.11.9-slim-bookworm` 拉取成功 | 构建日志含 "FROM python:3.11.9-slim-bookworm" | 临时改 `FROM python:3.11-slim` 重试 |
| CP-2.3 容器启动 | `docker run` 返回容器 ID | 容器运行中 | `docker logs ops-test` 查看启动错误 |
| CP-2.4 HEALTHCHECK 状态 | `healthy` | 35s 后状态为 healthy | 检查 `grep /proc/1/cmdline` 健康检查命令 |
| CP-2.5 容器停止 | `ops-test` | 无残留容器 | `docker rm -f ops-test` 强制清理 |

### ⚠️ 已知风险

- **基础镜像拉取超时**: python:3.11.9-slim-bookworm 网络不稳
- **应对**: 临时改 Dockerfile `FROM python:3.11-slim`（仅测试用，发布前须改回）
- **HEALTHCHECK 失败**: slim 镜像无 pgrep（P0 已修复，改用 grep /proc/1/cmdline）

### 🔍 关键验证：HEALTHCHECK 命令

```dockerfile
# 验证点：P0 修复后的健康检查命令
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
  CMD grep -qaE 'entrypoint.sh|generate_ops_daily_report' /proc/1/cmdline || exit 1
```

---

## 步骤 3: kind 集群测试（NetworkPolicy）

### 执行命令

```powershell
# 一键运行 5 项测试
.\scripts\test_networkpolicy_kind.ps1 -SkipImageBuild -ImageTag tlm-ops-reporter:v1.2
```

### 检查点

| 检查项 | 预期输出 | Pass 判据 | Fail 处理 |
|--------|----------|-----------|-----------|
| CP-3.1 集群创建 | `Creating cluster "tlm-np-test" ...` | kind create cluster 成功 | 检查 Docker Desktop 是否运行 |
| CP-3.2 镜像加载 | `Image: tlm-ops-reporter:v1.2` 已加载 | kind load docker-image 成功 | 确认镜像已构建（步骤 2） |
| CP-3.3 helm install | `STATUS: deployed` | helm install 成功 | `helm lint` 检查模板语法 |
| CP-3.4 Pod Ready | 90s 内 Ready | `kubectl wait --for=condition=Ready` 成功 | `kubectl describe pod` 查看事件 |
| CP-3.5 T1 Pod Ready | `[PASS] T1 : Pod Ready` | 测试输出 PASS | 检查 Pod 启动日志 |
| CP-3.6 T2 DNS 解析 | `DNS_OK:10.96.0.1` | DNS 解析返回 ClusterIP | 确认 kindnetd CNI 支持 NetworkPolicy |
| CP-3.7 T3 外部访问隔离 | `TimeoutError` 或 `ConnectionError` | 外部访问**失败**（期望） | 检查 networkPolicy.enabled=true |
| CP-3.8 T4 NP 资源配置 | `[PASS] T4 : NP 资源配置` | policyTypes=[Ingress,Egress] + ingress=[] + egress DNS | `kubectl get networkpolicy -o yaml` 核对 |
| CP-3.9 T5 禁用 NP 回归 | `[PASS] T5 : 禁用 NP 回归` | NP 删除后外部访问恢复 | helm upgrade --set networkPolicy.enabled=false |
| CP-3.10 测试结果 | `结果: 5 / 5 通过` | 全部 PASS | 逐项排查失败的 T1-T5 |

### 🔍 关键验证：T2 DNS 与 T3 隔离的语义

```
T2 DNS 解析（应成功）:
  → egress DNS 规则放行 kube-dns UDP/TCP 53
  → python -c "import socket; socket.gethostbyname('kubernetes.default.svc.cluster.local')"
  → 预期: 返回 ClusterIP（如 10.96.0.1）

T3 外部访问隔离（应失败）:
  → egress 拒绝所有非 DNS 出站
  → python -c "import urllib.request,socket; socket.setdefaulttimeout(5); urllib.request.urlopen('http://example.com')"
  → 预期: TimeoutError（5s 超时）
```

> **三义校验**: T2 成功 + T3 失败 = NetworkPolicy 语义正确（[不易] 守住最小权限：仅 DNS 出站）

### ⚠️ 已知风险

- **kind 未安装**: 网络问题导致 kind CLI 下载失败
- **应对**: 已生成 [install_kind.ps1](../../scripts/install_kind.ps1)（5 镜像源），网络恢复后运行
- **CNI 不支持 NP**: kind < 0.20 的 kindnetd 不支持 NetworkPolicy
- **应对**: 确认 kind 版本 ≥ 0.20（install_kind.ps1 默认 v0.23.0）

---

## 步骤 4: 安全上下文验证（可选）

### 执行命令

```powershell
# 保留集群调试模式
.\scripts\test_networkpolicy_kind.ps1 -KeepCluster -SkipImageBuild -ImageTag tlm-ops-reporter:v1.2

# 获取 Pod 名
$pod = kubectl get pods -n monitoring -l "app.kubernetes.io/instance=tlm-ops" -o jsonpath="{.items[0].metadata.name}"

# 1. 非 root 运行
kubectl exec $pod -n monitoring -- id

# 2. 只读根文件系统
kubectl exec $pod -n monitoring -- sh -c "touch /test 2>&1"

# 3. PVC 挂载可写
kubectl exec $pod -n monitoring -- sh -c "touch /app/output/test && echo OK"

# 4. 日志目录只读
kubectl exec $pod -n monitoring -- sh -c "touch /app/logs/test 2>&1"
```

### 检查点

| 检查项 | 预期输出 | Pass 判据 | Fail 处理 |
|--------|----------|-----------|-----------|
| CP-4.1 非 root 运行 | `uid=1000(reporter)` | uid=1000，非 root | 检查 values.yaml podSecurityContext.runAsUser |
| CP-4.2 根文件系统只读 | `touch: /test: Read-only file system` | 写入被拒 | 检查 containerSecurityContext.readOnlyRootFilesystem=true |
| CP-4.3 PVC 可写 | `OK` | /app/output 可写 | 检查 outputVolume 挂载 |
| CP-4.4 日志目录只读 | `Read-only file system` | /app/logs 写入被拒 | 确认 logsVolume readOnly: true |
| CP-4.5 capabilities 丢弃 | `CapEff: 0000000000000000` | 所有 capabilities 已 drop | 检查 capabilities.drop: [ALL] |

### 🔍 P1-3 安全上下文三件套

```yaml
# 验证点：values.yaml 中的安全配置
containerSecurityContext:
  readOnlyRootFilesystem: true      # CP-4.2
  allowPrivilegeEscalation: false   # 禁止提权
  capabilities:
    drop:
      - ALL                         # CP-4.5
```

---

## 步骤 5: 清理

### 执行命令

```powershell
# 如未用 -KeepCluster，脚本已自动清理
# 如用了 -KeepCluster，手动清理:
helm uninstall tlm-ops -n monitoring
kubectl delete namespace monitoring
kind delete cluster --name tlm-np-test
```

### 检查点

| 检查项 | 预期输出 | Pass 判据 | Fail 处理 |
|--------|----------|-----------|-----------|
| CP-5.1 helm uninstall | `release "tlm-ops" uninstalled` | Release 已卸载 | `helm list -n monitoring` 确认 |
| CP-5.2 namespace 删除 | namespace monitoring 已删除 | `kubectl get ns monitoring` 返回 NotFound | `kubectl delete ns monitoring --force` |
| CP-5.3 kind 集群删除 | `Deleting cluster "tlm-np-test" ...` | 集群已删除 | `kind delete clusters --all` |
| CP-5.4 Docker 残留 | 无 tlm-np-test 相关容器 | `docker ps -a` 无残留 | `docker rm -f $(docker ps -aq --filter name=tlm-np-test)` |

---

## 全流程检查点汇总

| 步骤 | 检查点数 | 关键 Pass 判据 |
|------|----------|----------------|
| 1. 安装 kind + helm | 4 | kind v0.23.0 + helm v3.14.0 可用 |
| 2. 构建镜像 | 5 | docker build 成功 + HEALTHCHECK healthy |
| 3. kind 集群测试 | 10 | 5/5 测试通过（T2 DNS 成功 + T3 隔离失败） |
| 4. 安全上下文 | 5 | 非 root + 只读根 + PVC 可写 + 日志只读 + cap drop |
| 5. 清理 | 4 | 集群 + namespace + release 全部清理 |
| **合计** | **28** | **与 [k8s_verification_checklist.md](k8s_verification_checklist.md) 一致** |

---

## 模拟执行时间线（预估）

| 时间 | 步骤 | 操作 | 状态 |
|------|------|------|------|
| T+0min | 步骤 1 | 安装 kind（5 镜像源尝试） | ⏳ 待网络恢复 |
| T+5min | 步骤 2 | docker build + HEALTHCHECK 验证 | ⏳ 待网络恢复 |
| T+15min | 步骤 3 | kind 集群 + 5 项 NP 测试 | ⏳ 待 kind 安装 |
| T+25min | 步骤 4 | 安全上下文 4 项验证 | ⏳ 待步骤 3 |
| T+30min | 步骤 5 | 清理集群 | ⏳ - |
| T+32min | - | 完成 | - |

> **总耗时预估**: 30-45 分钟（含镜像构建），网络恢复后可直接按 [local_verification_guide.md](local_verification_guide.md) 执行。

---

## 一键执行命令（网络恢复后）

```powershell
# 完整流程一键执行
.\scripts\install_kind.ps1 && `
docker build -t tlm-ops-reporter:v1.2 -f docker/ops-reporter/Dockerfile . && `
.\scripts\test_networkpolicy_kind.ps1 -SkipImageBuild -ImageTag tlm-ops-reporter:v1.2
```

---

## 故障排查速查

| 现象 | 可能原因 | 诊断命令 | 解决方案 |
|------|----------|----------|----------|
| kind 下载全失败 | 网络不通 | `Test-NetConnection github.com -Port 443` | 浏览器+VPN 手动下载 |
| docker build 超时 | 基础镜像拉取慢 | `docker pull python:3.11.9-slim-bookworm` | 临时改 FROM python:3.11-slim |
| Pod CrashLoopBackOff | 镜像未加载到 kind | `kubectl describe pod` | `kind load docker-image tlm-ops-reporter:v1.2` |
| T2 DNS 失败 | CNI 不支持 NP | `kubectl get pods -n kube-system` | 升级 kind ≥ 0.20 |
| T3 外部访问成功 | NP 未生效 | `kubectl get networkpolicy -n monitoring` | 确认 networkPolicy.enabled=true |
| CP-4.2 写入成功 | readOnly 未生效 | `kubectl get pod -o yaml` | 检查 containerSecurityContext |

---

> **三义原则校验**:
> - [不易] 28 个检查点覆盖全部 P0/P1 改动（HEALTHCHECK + 安全上下文 + NetworkPolicy）
> - [变易] 每个检查点含 Fail 处理，适应不同故障场景
> - [简易] 检查点表格式呈现，Pass 判据明确，30s