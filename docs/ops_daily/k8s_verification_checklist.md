# K8s 真实环境验证检查清单

> **用途**: 在真实 K8s 集群或 kind 集群中验证 v1.2 发布的所有改动
> **前置条件**: 网络恢复 + kind 已安装（运行 [install_kind.ps1](../../scripts/install_kind.ps1)）
> **关联文档**: [RELEASE_NOTES_v1.2.md](RELEASE_NOTES_v1.2.md) §7 待验证项

---

## 验证概览

| 验证组 | 检查项数 | 优先级 | 依赖 |
|--------|----------|--------|------|
| A. 镜像构建验证 | 4 | P0 | Docker + 网络 |
| B. NetworkPolicy 集群测试 | 12 | P0 | kind 集群 + helm |
| C. 安全上下文验证 | 5 | P1 | K8s 集群 |
| D. 多架构镜像验证 | 3 | P2 | docker buildx |
| E. Helm Upgrade 回归 | 4 | P1 | 已部署的 v1.1 |

---

## A. 镜像构建验证（P2-1）

> **目标**: 验证 `python:3.11.9-slim-bookworm` 固定版本可成功构建
> **原因**: 网络问题导致本次未完成 `docker build` 验证

### A1. 基础构建
```bash
# 构建镜像
docker build -t tlm-ops-reporter:v1.2 -f docker/ops-reporter/Dockerfile .
```
- [ ] **A1-1**: 构建成功，无 ERROR
- [ ] **A1-2**: `python:3.11.9-slim-bookworm` 镜像正确拉取（检查构建日志）

### A2. 容器启动验证
```bash
# 启动容器验证
docker run --rm -d --name ops-test tlm-ops-reporter:v1.2
sleep 35
docker inspect --format='{{.State.Health.Status}}' ops-test
```
- [ ] **A2-1**: 容器启动成功
- [ ] **A2-2**: HEALTHCHECK 状态为 `healthy`（35s 后）

---

## B. NetworkPolicy 集群测试（P2-4）

> **目标**: 验证 NetworkPolicy 网络隔离语义
> **工具**: [test_networkpolicy_kind.ps1](../../scripts/test_networkpolicy_kind.ps1)
> **环境**: kind 集群（kindnetd CNI 原生支持 NetworkPolicy）

### 准备工作
```powershell
# 1. 安装 kind（如未安装）
.\scripts\install_kind.ps1

# 2. 运行完整测试（自动创建/清理集群）
.\scripts\test_networkpolicy_kind.ps1
```

### T1: Pod Ready 验证
- [ ] **T1-1**: `helm install` 成功（networkPolicy.enabled=true）
- [ ] **T1-2**: Pod 在 90s 内达到 Ready 状态

### T2: DNS 解析（应成功）✅ 关键
> 验证 egress DNS 规则放行 kube-dns UDP/TCP 53
```bash
kubectl exec <pod> -n monitoring -- \
  python -c "import socket; print(socket.gethostbyname('kubernetes.default.svc.cluster.local'))"
```
- [ ] **T2-1**: DNS 解析成功，返回 ClusterIP
- [ ] **T2-2**: 无 "name resolution" 错误

### T3: 外部访问隔离（应失败）✅ 关键
> 验证 egress 拒绝所有非 DNS 出站
```bash
kubectl exec <pod> -n monitoring -- \
  python -c "import urllib.request,socket; socket.setdefaulttimeout(5); urllib.request.urlopen('http://example.com')"
```
- [ ] **T3-1**: 请求超时或连接被拒（期望失败）
- [ ] **T3-2**: 错误类型为 `TimeoutError` 或 `ConnectionError`（非 DNS 失败）

### T4: NetworkPolicy 资源配置验证
```bash
kubectl get networkpolicy -n monitoring -o yaml
```
- [ ] **T4-1**: `policyTypes` = `[Ingress, Egress]`
- [ ] **T4-2**: `ingress` 为空数组（`[]`，拒绝所有入站）
- [ ] **T4-3**: `egress` 包含 DNS 规则（kube-dns + port 53 UDP/TCP）
- [ ] **T4-4**: `podSelector` 匹配 ops-reporter Pod 标签

### T5: 禁用 NetworkPolicy 回归
```bash
# 禁用 NP
helm upgrade tlm-ops ./deploy/helm/tlm-ops-reporter \
  -n monitoring --set networkPolicy.enabled=false

# 验证 NP 已删除
kubectl get networkpolicy -n monitoring

# 验证外部访问恢复
kubectl exec <pod> -n monitoring -- \
  python -c "import urllib.request; print(urllib.request.urlopen('http://example.com').status)"
```
- [ ] **T5-1**: NetworkPolicy 资源已删除（No resources found）
- [ ] **T5-2**: 外部访问恢复（返回 200）或 kind 节点网络限制（可接受）

---

## C. 安全上下文验证（P1-3）

> **目标**: 验证 containerSecurityContext 三件套生效

### C1. readOnlyRootFilesystem
```bash
# 尝试写根文件系统（应失败）
kubectl exec <pod> -n monitoring -- sh -c "touch /test-write && echo WRITE_OK || echo WRITE_BLOCKED"
```
- [ ] **C1-1**: 写入被拒（`Read-only file system`）

### C2. 非 root 运行
```bash
kubectl exec <pod> -n monitoring -- id
```
- [ ] **C2-1**: uid=1000(reporter)，非 root

### C3. Capabilities 丢弃
```bash
kubectl exec <pod> -n monitoring -- cat /proc/1/status | grep Cap
```
- [ ] **C3-1**: CapEff 为空或最小集（所有 capabilities 已丢弃）

### C4. PVC 挂载可写
```bash
# 验证 output 目录可写（PVC 挂载）
kubectl exec <pod> -n monitoring -- sh -c "touch /app/output/test && echo PVC_WRITE_OK"
```
- [ ] **C4-1**: PVC 挂载目录可写（readOnlyRootFilesystem 不影响 volume 挂载）

### C5. 日志目录只读
```bash
# 验证 logs 目录只读
kubectl exec <pod> -n monitoring -- sh -c "touch /app/logs/test 2>&1 && echo LOGS_WRITE_OK || echo LOGS_READ_ONLY"
```
- [ ] **C5-1**: logs 目录写入被拒（`Read-only file system`）

---

## D. 多架构镜像验证（P2-3）

> **目标**: 验证 docker buildx 双架构构建
> **环境**: 需启用 docker buildx

### D1. buildx 构建
```bash
# 启用 buildx
docker buildx create --use --name multiarch-builder
docker buildx inspect --bootstrap

# 双架构构建并推送
docker buildx build --platform linux/amd64,linux/arm64 \
  -t <registry>/tlm-ops-reporter:v1.2 \
  -f docker/ops-reporter/Dockerfile --push .
```
- [ ] **D1-1**: amd64 架构构建成功
- [ ] **D1-2**: arm64 架构构建成功
- [ ] **D1-3**: manifest list 推送到 registry 成功

---

## E. Helm Upgrade 回归（v1.1 → v1.2）

> **目标**: 验证从 v1.1 平滑升级到 v1.2

### E1. 升级前快照
```bash
# 记录 v1.1 状态
kubectl get pods -n monitoring -l app.kubernetes.io/instance=tlm-ops
kubectl get pvc -n monitoring
```
- [ ] **E1-1**: v1.1 Pod 正常运行

### E2. 执行升级
```bash
helm upgrade tlm-ops ./deploy/helm/tlm-ops-reporter \
  -n monitoring --set image.tag=v1.2
```
- [ ] **E2-1**: helm upgrade 成功
- [ ] **E2-2**: 新 Pod 拉取 v1.2 镜像并启动

### E3. 升级后验证
```bash
# 验证新 Pod Ready
kubectl wait --for=condition=Ready pod/<new-pod> -n monitoring --timeout=90s

# 验证 PVC 数据保留
kubectl exec <new-pod> -n monitoring -- ls /app/output/
```
- [ ] **E3-1**: 新 Pod Ready
- [ ] **E3-2**: PVC 数据未丢失（升级前生成的日报仍存在）

### E4. 回滚验证（可选）
```bash
helm rollback tlm-ops <revision> -n monitoring
```
- [ ] **E4-1**: 回滚成功（如需）

---

## 验证结果汇总

| 组 | 检查项 | 通过 | 失败 | 跳过 |
|----|--------|------|------|------|
| A. 镜像构建 | 4 | | | |
| B. NetworkPolicy | 12 | | | |
| C. 安全上下文 | 5 | | | |
| D. 多架构 | 3 | | | |
| E. Helm Upgrade | 4 | | | |
| **合计** | **28** | | | |

---

## 注意事项

1. **CoreDNS 适配**: 如集群使用 CoreDNS（非 kube-dns），需修改 [networkpolicy.yaml](../../deploy/helm/tlm-ops-reporter/templates/networkpolicy.yaml) 中 `k8s-app: kube-dns` 为 `k8s-app: coredns`
2. **CNI 要求**: NetworkPolicy 需 CNI 支持（Calico/Cilium/kindnetd/Flannel+插件），验证前确认集群 CNI
3. **kind 网络限制**: kind 节点访问外网可能受 Docker Desktop 网络限制，T5 外部访问恢复测试可能误报，以 NP 资源删除为准
4. **镜像拉取策略**: kind 测试用 `image.pullPolicy=Never`（本地加载），真实集群用 `IfNotPresent` 或 `Always`
5. **PVC 存储类**: kind 默认 standard 存储类，真实集群需确认 storageClass 可用

---

## 快速运行（kind 一键测试）

```powershell
# 1. 安装 kind
.\scripts\install_kind.ps1

# 2. 一键运行 B 组测试（自动建集群 + 测试 + 清理）
.\scripts\test_networkpolicy_kind.ps1

# 3. 保留集群调试
.\scripts\test_networkpolicy_kind.ps1 -KeepCluster

# 4. 跳过镜像构建（用已有镜像）
.\scripts\test_networkpolicy_kind.ps1 -SkipImageBuild -ImageTag tlm-ops-reporter:v1.2
```