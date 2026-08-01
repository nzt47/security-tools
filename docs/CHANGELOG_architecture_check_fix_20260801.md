# 架构规则校验依赖图更新修复 — 变更说明

**日期**: 2026-08-01
**提交**: c5508d31 → f0b4e0ad
**影响范围**: `.github/workflows/architecture-check.yml`

## 背景

架构规则校验工作流的"提交依赖图文档"步骤原条件为 `github.event_name == 'push'`，导致：

1. **竞态失败**：push 触发时，工作流基于旧 master 生成依赖图并推送，若期间远程有新提交，`git push` 被 non-fast-forward 拒绝（批次 4 提交 `23cc20ff` 即因此失败）
2. **手动触发无效**：workflow_dispatch 手动触发时，条件不满足，依赖图生成后跳过提交步骤，无法更新远程

## 修复内容

### c5508d31 — 工作流条件扩展

```yaml
# 修改前
if: github.event_name == 'push' && (github.ref == 'refs/heads/main' || github.ref == 'refs/heads/master')

# 修改后
if: (github.event_name == 'push' || github.event_name == 'workflow_dispatch') && (github.ref == 'refs/heads/main' || github.ref == 'refs/heads/master')
```

新增 `workflow_dispatch` 事件支持，使手动触发的架构规则校验也能自动推送依赖图更新。

### f0b4e0ad — 依赖图自动更新（CI 推送）

修复后手动触发架构规则校验，依赖图自动生成并推送：

- **节点数**: 269 → 270（+1，新增 `agent.config.etcd_config_client`）
- **边数**: 599
- **架构违规**: 0 项未豁免（4 项已豁免循环依赖为已知技术债务）

## 验证结果

| 检查项 | 结果 |
|--------|------|
| 架构规则校验（远程 CI） | ✅ success, 0 违规 |
| 架构规则校验（本地） | ✅ 通过, 0 项未豁免违规 |
| 依赖图文件更新 | ✅ `8e7b24f6` → `f0b4e0ad` |

## 注意事项

- 此修改仅影响架构规则校验工作流，不涉及业务代码
- 后续手动触发架构规则校验（workflow_dispatch）将自动更新依赖图
