# v1.2.0 发布清单 — 版本一致性核查

> **生成日期**: 2026-07-19
> **基线**: Chart.yaml version 1.1.0 → 1.2.0
> **核查范围**: Helm Chart 全部版本引用点 + Dockerfile 基础镜像 + 文档示例
> **关联文档**: [RELEASE_NOTES_v1.2.md](RELEASE_NOTES_v1.2.md)、[local_verification_guide.md](local_verification_guide.md)

---

## 1. 版本变更概览

| 项 | 旧值 | 新值 | 变更类型 |
|----|------|------|----------|
| Chart.yaml `version` | 1.1.0 | 1.2.0 | Chart SemVer 主版本位 |
| Chart.yaml `appVersion` | "1.1" | "1.2" | 应用版本 |
| values.yaml `image.tag` | "v1.1" | "v1.2" | 容器镜像标签 |
| README.md 示例参数 | v1.1 | v1.2 | 文档示例同步 |
| Dockerfile 基础镜像 | python:3.11-slim | python:3.11.9-slim-bookworm | 固定补丁版本（P2-1） |

---

## 2. 版本引用全扫描结果

### 2.1 ✅ 已正确（无需改动）

| 文件 | 行号 | 内容 | 版本 |
|------|------|------|------|
| Chart.yaml | 5 | `version: 1.2.0` | 1.2.0 |
| Chart.yaml | 6 | `appVersion: "1.2"` | 1.2 |
| README.md | 88 | `-t <registry>/tlm-ops-reporter:v1.2` | v1.2 |
| README.md | 99 | `-t tlm-ops-reporter:v1.2` | v1.2 |
| README.md | 109 | `--set image.tag=v1.2` | v1.2 |
| Dockerfile | 16 | `FROM python:3.11.9-slim-bookworm` | 3.11.9（固定） |

### 2.2 🔧 本次修复的版本不一致

| 文件 | 行号 | 修复前 | 修复后 | 原因 |
|------|------|--------|--------|------|
| values.yaml | 12 | `tag: "v1.1"` | `tag: "v1.2"` | 默认镜像标签须与 appVersion 同步 |
| README.md | 23 | `--set image.tag=v1.1` | `--set image.tag=v1.2` | 自定义参数示例须与默认值一致 |
| README.md | 51 | `\| image.tag \| v1.1 \|` | `\| image.tag \| v1.2 \|` | 配置参数表默认值须与 values.yaml 一致 |

> **三义校验**: 三处均为 v1.1→v1.2 的机械同步，不改变任何业务语义（[简易]）；修复后 Chart.yaml/values.yaml/README.md 三者版本完全一致（[不易] 守住版本契约）。

---

## 3. 依赖版本检查

### 3.1 Dockerfile 基础镜像

| 依赖 | 当前版本 | 是否固定 | 是否需更新 |
|------|----------|----------|------------|
| python 基础镜像 | 3.11.9-slim-bookworm | ✅ 补丁版本+发行版代号 | ❌ 无需更新（P2-1 已完成固定） |

**结论**: Dockerfile 无其他依赖版本需同步更新。

### 3.2 Helm Chart 依赖（dependencies）

Chart.yaml 中无 `dependencies` 字段，本 Chart 不依赖其他 Chart。

**结论**: 无 Chart 依赖需更新。

### 3.3 应用层依赖（requirements.txt）

ops-reporter 容器仅依赖 Python 标准库（urllib/socket/json/subprocess）+ Prometheus client，无第三方版本绑定。

**结论**: 无应用层依赖版本需同步。

### 3.4 K8s API 版本兼容性

| 资源 | apiVersion | 兼容性 |
|------|------------|--------|
| Deployment | `apps/v1` | K8s 1.9+（稳定） |
| ConfigMap | `v1` | 所有版本 |
| PVC | `v1` | 所有版本 |
| NetworkPolicy | `networking.k8s.io/v1` | K8s 1.7+（稳定） |
| ServiceMonitor | `monitoring.coreos.com/v1` | 需 Prometheus Operator |

**结论**: 所有 API 版本均为稳定版，无弃用风险。

---

## 4. 发布前检查清单

### 4.1 版本一致性（必检）

- [x] Chart.yaml `version` = 1.2.0
- [x] Chart.yaml `appVersion` = "1.2"
- [x] values.yaml `image.tag` = "v1.2"
- [x] README.md 所有示例参数 = v1.2
- [x] README.md 配置参数表默认值 = v1.2
- [x] Dockerfile 基础镜像已固定（python:3.11.9-slim-bookworm）

### 4.2 Helm Chart 语法（必检，网络恢复后）

```bash
helm lint deploy/helm/tlm-ops-reporter/
helm template tlm-ops deploy/helm/tlm-ops-reporter/ --namespace monitoring > /tmp/v1.2-rendered.yaml
```

- [ ] `helm lint` 0 failures
- [ ] `helm template` 渲染成功，无模板错误
- [ ] 渲染产物中 image.tag = v1.2

### 4.3 镜像构建（网络恢复后）

- [ ] `docker build -t tlm-ops-reporter:v1.2 -f docker/ops-reporter/Dockerfile .` 成功
- [ ] 容器启动后 HEALTHCHECK 35s 内变 healthy
- [ ] （可选）多架构构建 `docker buildx build --platform linux/amd64,linux/arm64` 成功

### 4.4 集群测试（网络恢复后）

- [ ] kind 集群 NetworkPolicy 5 项测试通过（[test_networkpolicy_kind.ps1](../../scripts/test_networkpolicy_kind.ps1)）
- [ ] 安全上下文验证（非 root + readOnlyRootFilesystem + capabilities drop ALL）

### 4.5 升级回归（如有 v1.1 部署）

- [ ] `helm upgrade tlm-ops ./deploy/helm/tlm-ops-reporter -n monitoring --set image.tag=v1.2` 成功
- [ ] PVC 数据保留（升级前日报未丢失）
- [ ] Pod 在 90s 内 Ready

---

## 5. 版本不一致根因分析

本次发现 3 处 v1.1 残留，根因：

1. **values.yaml**: Chart.yaml 升版时未同步更新 `image.tag` 默认值
2. **README.md 示例**: 文档示例参数未随默认值更新
3. **README.md 配置表**: 配置参数表默认值列未同步

**改进建议**: 后续版本升级时，执行以下 grep 一键扫描所有版本引用：

```powershell
# 版本升级扫描命令（以 v1.2 → v1.3 为例）
Select-String -Path "deploy\helm\tlm-ops-reporter\**" -Pattern "v1\.2|1\.2\.0|appVersion.*1\.2" -CaseSensitive:$false
```

---

## 6. 本次修复的提交内容

| 文件 | 变更 |
|------|------|
| Chart.yaml | version 1.1.0→1.2.0, appVersion "1.1"→"1.2"（前序 commit） |
| values.yaml | image.tag "v1.1"→"v1.2" |
| README.md | 2 处 v1.1→v1.2（示例 + 配置表） |

---

## 7. 结论

| 检查项 | 结论 |
|--------|------|
| Chart.yaml 版本是否已更新 | ✅ 1.2.0 |
| 其他依赖版本是否需更新 | ❌ 无（Dockerfile 已固定，无 Chart 依赖，无应用层依赖） |
| 版本引用是否全部一致 | ✅ 已修复 3 处不一致，现 6 处引用全部为 v1.2 |
| 是否可发布 | ⏳ 待网络恢复后完成镜像构建 + 集群测试 |

> **三义原则校验**:
> - [不易] Chart/version/appVersion/image.tag 四维版本契约现已一致
> - [变易] 无新增依赖，升级路径清晰（helm upgrade --set image.tag=v1.2）
> - [简易] 3 处机械修复，零业务逻辑变更
