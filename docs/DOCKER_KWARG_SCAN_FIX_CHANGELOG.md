# Docker kwarg 扫描健壮性修复变更说明

> **来源 stash**: `stash@{0}: docker-fix-8files`
> **涉及文件**: 4 个
> **变更性质**: CI 健壮性修复（权限/CRLF/误判三类实际问题）

---

## 一、修复背景

Docker kwarg 扫描（`kwarg-docker-scan.yml`）在 CI 运行中暴露三个问题：

1. **容器内写权限缺失**：scanner 用户对宿主挂载根目录无写权限，`OUTPUT_FILE=/project/xxx.json` 写入失败
2. **CRLF 致 shebang 失败**：Windows 编辑器写入 `\r\n`，Linux 容器内 `#!/bin/sh\r` 找不到解释器（`exec: no such file or directory`）
3. **exit 1 误判**：扫描器异常崩溃（OOM/段错误/权限错误）也返回 exit 1，被误判为 `high_risk_detected`，导致 CI 误阻断

---

## 二、变更详情

### 2.1 `.github/workflows/kwarg-docker-scan.yml` — Docker 挂载与报告路径修复

| 修复点 | Before | After | 原理 |
|--------|--------|-------|------|
| 挂载模式 | `-v workspace:/project` | `-v workspace:/project:ro` | 【不易】最小权限：源码只读，防容器篡改 |
| 报告输出 | `OUTPUT_FILE=/project/xxx.json` | `OUTPUT_FILE=/output/xxx.json` | 【变易】预创建 777 临时目录挂载为 /output，scanner 用户可写 |
| exit 1 判断 | `exit!=0 → 直接阻断` | `exit 1 + 报告存在 + HIGH>0 → 阻断；否则 → 扫描器异常` | 【不易】区分真 HIGH 风险 vs 扫描器崩溃，避免误判 |
| Python 调用 | 多行 `python3 -c` | 单行 `python3 -c` | 【简易】避免 YAML run 块多行缩进致 IndentationError |

### 2.2 `.github/workflows/ci-failure-notify.yml` — 恢复通知机制

**新增**：
- `关键字参数冲突扫描 (Docker)` 加入 workflow_run 监控列表
- `docker-scan-recover-notify` job：仅在「上次 failure → 本次 success」时发钉钉通知

**设计原则**：
- 【不易】只在状态变化时通知，避免每次成功都发噪声
- 【变易】用 `github-script` 查历史 run 判断真实状态变化

### 2.3 `packages/kwarg_scanner/Dockerfile` — CRLF 清理

```dockerfile
# Before
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# After
RUN chmod +x /usr/local/bin/docker-entrypoint.sh && sed -i 's/\r$//' /usr/local/bin/docker-entrypoint.sh
```

**原理**：【不易】Windows 编辑器可能写入 `\r\n`，Linux 容器内 shebang `#!/bin/sh\r` 找不到解释器。构建时强制去除 CRLF，从源头消除问题。

### 2.4 `packages/kwarg_scanner/docker-entrypoint.sh` — exit 1 校验增强

| Before | After |
|--------|-------|
| exit 1 → 直接判定 `high_risk_detected` → 阻断 | exit 1 → 检查报告是否存在 + HIGH 计数 > 0 → 确认后才阻断；否则视为扫描器崩溃（exit 3） |

**新增逻辑**：
- 读取 `$OUTPUT_FILE` 中的 `summary.HIGH` 计数
- HIGH > 0 → `high_risk_detected`（真阻断）
- HIGH = 0 或无报告 → `E_SCAN_CRASHED`（扫描器异常，exit 3，不误判为 HIGH）

---

## 三、风险评估

| 风险项 | 评估 | 缓解 |
|--------|------|------|
| 只读挂载导致扫描器无法读取 | ✅ 无风险 | `:ro` 仅禁止写，读取不受影响 |
| 777 临时目录权限过宽 | ⚠ 可接受 | CI runner 临时环境，无敏感数据；job 结束自动清理 |
| 恢复通知噪声 | ✅ 已控制 | 仅状态变化（failure→success）才通知 |
| exit 3 新增退出码 | ✅ 向后兼容 | 原 exit 2 已存在，CI 对非 0/1 退出码统一处理为失败 |

---

## 四、验证计划

- [ ] YAML 语法检查（`python -c "import yaml; yaml.safe_load(...)"`)
- [ ] Shell 语法检查（`bash -n docker-entrypoint.sh`）
- [ ] Dockerfile 构建测试（`docker build`）
- [ ] CI 全量运行确认无回归

---

## 五、回滚方案

如修复引入新问题，回滚步骤：

```bash
# 撤销 stash 应用（如尚未提交）
git checkout -- .github/workflows/kwarg-docker-scan.yml \
                 .github/workflows/ci-failure-notify.yml \
                 packages/kwarg_scanner/Dockerfile \
                 packages/kwarg_scanner/docker-entrypoint.sh

# 如已提交
git revert <commit-hash>
```
