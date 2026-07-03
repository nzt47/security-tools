# P0 Workflow 重建脚本 — 快速摘要

> **完整文档**: [p0_workflow_rebuild_runbook.md](p0_workflow_rebuild_runbook.md)
> **生成时间**: 2026-07-04
> **阅读时长**: ~3 分钟

---

## 这是什么？

一个应急脚本，用于解决 GitHub Actions 平台对 P0 安全验证 workflow 的持续性缓存故障。通过删除旧 workflow 文件并用新文件名创建，强制生成新 workflow ID，绕过平台缓存。

## 什么时候用？

仅当以下**全部**条件满足时：

1. P0 回归测试 Job 持续因 "Set up job" 失败（48 小时内 16/17 次失败）
2. 已尝试 4 种策略均无效：`rerun-failed-jobs` API、`workflow_dispatch`、修改 Job 名称、修改 job_id
3. 距首次失败已超过 24 小时，平台仍未恢复

## 怎么用？

```bash
# 1. 先模拟（必执行，零副作用）
python scripts/rebuild_p0_workflow.py --dry-run

# 2. 正式执行
python scripts/rebuild_p0_workflow.py          # 交互模式
python scripts/rebuild_p0_workflow.py --yes     # 跳过确认
```

脚本自动完成 8 步：检查 → 备份 → 创建新文件 → 删除旧文件 → 提交推送 → 等待新 workflow → 等待首次运行 → 轮询验证。

## 前置权限

- 对 `nzt47/security-tools` 仓库有 push 权限
- `~/.git-credentials` 中有有效 GitHub token
- 当前在 `phase2-visibility-convergence` 分支
- workflow 文件无未提交变更

## 关键风险

| 风险 | 等级 | 说明 |
|------|------|------|
| 强制删除误操作 | ✅ 安全 | `git rm` 未用 `-f`，遇到修改会拒绝 |
| dry-run 副作用 | ✅ 安全 | 已验证零副作用（SHA256 比对） |
| 备份完整性未校验 | ⚠️ 中等 | 操作前手动 `diff` 比对 |
| 路径硬编码 | ⚠️ 中等 | 修改常量前需 code review |

## 出问题怎么回滚？

| 场景 | 操作 |
|------|------|
| 平台仍未恢复 | 记录失败 + 联系 GitHub 支持（场景 A） |
| 新 workflow 有问题 | 从备份或 git 历史恢复旧文件（场景 B） |
| 整个操作需撤销 | `git revert` 回退到重建前 commit（场景 C） |
| 备份+历史都丢失 | 从验证报告手工重建（场景 D） |

**完整回滚决策树**: 见 [完整文档第四章 4.4 节](p0_workflow_rebuild_runbook.md#44-回滚决策树)

## 验证状态

- ✅ 32 个单元测试全部通过
- ✅ dry-run 模式零副作用（git 状态 SHA256 比对一致）
- ✅ 删除逻辑风险评估完成（8 项风险矩阵）
- ✅ 操作手册含 4 种回滚场景 + 完整决策树

## 相关文件

| 文件 | 说明 |
|------|------|
| `scripts/rebuild_p0_workflow.py` | 重建脚本（457 行） |
| `tests/unit/test_rebuild_p0_workflow.py` | 单元测试（32 个用例） |
| `docs/security/p0_workflow_rebuild_runbook.md` | 完整验证报告与操作手册 |
| `docs/security/p0_final_verification_report.md` | P0 最终验证报告（17 次运行记录） |

---

**有疑问？先读完整文档 [p0_workflow_rebuild_runbook.md](p0_workflow_rebuild_runbook.md)。**
