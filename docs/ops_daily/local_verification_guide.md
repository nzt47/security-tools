# 本地验证简易操作手册

> **用途**: 网络恢复后，在本地快速验证 v1.2 全部改动的简明操作指南
> **预计耗时**: 30-45 分钟（含镜像构建）
> **前置条件**: Docker 已安装 + 网络可用
> **详细检查清单**: [k8s_verification_checklist.md](k8s_verification_checklist.md)（28 项）

---

## 步骤总览

| 步骤 | 操作 | 耗时 | 依赖 |
|------|------|------|------|
| 1 | 安装 kind + helm | 5 min | 网络 |
| 2 | 构建镜像 | 10 min | 网络（拉取基础镜像） |
| 3 | kind 集群测试 | 10 min | 步骤 1+2 |
| 4 | 安全上下文验证 | 5 min | 步骤 3 |
| 5 | 清理 | 2 min | - |

---

## 步骤 1: 安装 kind + helm

```powershell
# 安装 kind（多镜像源自动尝试，网络恢复后直接运行）
.\scripts\install_kind.ps1

# 验证安装
kind version
helm version    # helm 已预装（v3.14.0）
```

**预期输出**: kind 版本号 + helm 版本号

> 如 kind 安装失败，参考脚本末尾的手动下载指引（浏览器访问 GitHub 下载）。

---

## 步骤 2: 构建镜像

```powershell
# 构建 v1.2 镜像（固定基础镜像 python:3.11.9-slim-bookworm）
docker build -t tlm-ops-reporter:v1.2 -f docker/ops-reporter/Dockerfile .

# 验证镜像 + HEALTHCHECK
docker run --rm -d --name ops-test tlm-ops-reporter:v1.2
Start-Sleep -Seconds 35
docker inspect --format='{{.State.Health.Status}}' ops-test
docker stop ops-test
```

**预期输出**:
- 构建成功无 ERROR
- HEALTHCHECK 状态 = `healthy`

> ⚠️ 如基础镜像拉取超时，临时改 Dockerfile `FROM python:3.11-slim` 重试（仅测试用）。

---

## 步骤 3: kind 集群测试（NetworkPolicy）

```powershell
# 一键运行 5 项测试（自动创建/部署/测试/清理集群）
.\scripts\test_networkpolicy_kind.ps1 -SkipImageBuild -ImageTag tlm-ops-reporter:v1.2
```

**预期输出**:
```
[PASS] T1 : Pod Ready
[PASS] T2 : DNS 解析（egress DNS 放行）       ← DNS 应成功
[PASS] T3 : 外部访问隔离（egress 拒绝）       ← 外部访问应失败
[PASS] T4 : NP 资源配置
[PASS] T5 : 禁用 NP 回归

结果: 5 / 5 通过
```

**关键验证点**:
- T2 DNS 解析**必须成功**（验证 kube-dns egress 放行）
- T3 外部访问**必须失败**（验证 egress 拒绝非 DNS 出站）

> 如需保留集群调试: `.\scripts\test_networkpolicy_kind.ps1 -KeepCluster -SkipImageBuild -ImageTag tlm-ops-reporter:v1.2`

---

## 步骤 4: 安全上下文验证（可选）

> 在步骤 3 保留的集群中执行（加 `-KeepCluster` 参数）

```bash
# 获取 Pod 名
$pod = kubectl get pods -n monitoring -l "app.kubernetes.io/instance=tlm-ops" -o jsonpath="{.items[0].metadata.name}"

# 1. 验证非 root 运行
kubectl exec $pod -n monitoring -- id
# 预期: uid=1000(reporter)

# 2. 验证只读根文件系统（写入应失败）
kubectl exec $pod -n monitoring -- sh -c "touch /test 2>&1"
# 预期: Read-only file system

# 3. 验证 PVC 挂载可写
kubectl exec $pod -n monitoring -- sh -c "touch /app/output/test && echo OK"
# 预期: OK

# 4. 验证日志目录只读
kubectl exec $pod -n monitoring -- sh -c "touch /app/logs/test 2>&1"
# 预期: Read-only file system
```

---

## 步骤 5: 清理

```powershell
# 如未用 -KeepCluster，脚本已自动清理
# 如用了 -KeepCluster，手动清理:
helm uninstall tlm-ops -n monitoring
kubectl delete namespace monitoring
kind delete cluster --name tlm-np-test
```

---

## 故障排查

| 问题 | 原因 | 解决方案 |
|------|------|----------|
| kind 下载失败 | 网络问题 | 浏览器访问 GitHub 下载，或用 VPN |
| 镜像构建超时 | python:3.11.9-slim 拉取慢 | 临时改 `FROM python:3.11-slim` |
| T2 DNS 失败 | CNI 不支持 NetworkPolicy | 确认 kind 版本 ≥0.20（kindnetd 支持 NP） |
| T3 外部访问成功 | NetworkPolicy 未生效 | 检查 `networkPolicy.enabled=true` |
| Pod 启动失败 | 镜像未加载到 kind | 运行 `kind load docker-image tlm-ops-reporter:v1.2` |

---

## 一键执行（高级）

如已安装 kind + helm + 镜像已构建:

```powershell
# 完整流程一键执行
.\scripts\test_networkpolicy_kind.ps1 -SkipImageBuild -ImageTag tlm-ops-reporter:v1.2
```

---

## 参考文档

- [RELEASE_NOTES_v1.2.md](RELEASE_NOTES_v1.2.md) — 完整发布说明
- [k8s_verification_checklist.md](k8s_verification_checklist.md) — 28 项详细检查清单
- [test_networkpolicy_kind.ps1](../../scripts/test_networkpolicy_kind.ps1) — NP 测试脚本
- [install_kind.ps1](../../scripts/install_kind.ps1) — kind 安装