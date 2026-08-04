# Develop CI 稳定性监控配置说明（2026-08-04）

> 目的：在 Docker kwarg 扫描误报修复（commit `0055a3f8`）合入 develop 后,
> 连续监控 **3 次 develop 推送**的关键 CI workflow 稳定性,
> 防止"修复后短暂转绿,后续推送又退化"的隐性回归。
>
> 关联：
> - 误报修复记录：[todo_followup_20260804.md](./todo_followup_20260804.md) 待办项 1
> - BOM 修复总结：[bom_fix_links_cleanup_summary_20260803.md](./bom_fix_links_cleanup_summary_20260803.md)

---

## 一、监控机制设计

### 1.1 三义分析

| 维度 | 设计决策 |
|------|----------|
| **不易** | 监控必须自动触发、有明确退出条件（3 次后停止）、不破坏现有 CI |
| **变易** | 监控 workflow 列表可配置（计数器文件 `monitored_workflows` 字段）、监控次数可重置 |
| **简易** | workflow 只做编排（checkout→等待→调用脚本→commit→issue），逻辑全在 Python 脚本 |

### 1.2 核心组件

| 文件 | 职责 |
|------|------|
| [.github/workflows/develop-ci-stability-monitor.yml](../../.github/workflows/develop-ci-stability-monitor.yml) | 监控 workflow：push 到 develop 触发，编排各步骤 |
| [.github/scripts/develop_stability_monitor.py](../../.github/scripts/develop_stability_monitor.py) | 监控脚本：查询 gh run list、汇总稳定性、递减计数器 |
| [.github/monitoring/develop_stability_counter.json](../../.github/monitoring/develop_stability_counter.json) | 计数器文件：记录剩余监控次数、监控 workflow 列表、历史快照 |

### 1.3 监控范围（5 个关键 workflow）

与 [ci-failure-notify.yml](../../.github/workflows/ci-failure-notify.yml) 监听列表对齐 + 修复相关 workflow：

| Workflow 文件 | 名称 | 监控理由 |
|---------------|------|----------|
| `kwarg-docker-scan.yml` | 关键字参数冲突扫描 (Docker) | **本次修复核心** |
| `kwarg-conflict-check.yml` | 关键字参数冲突扫描 | 同源扫描器,防止非 Docker 版本退化 |
| `ci.yml` | 云枢系统测试流程 | develop 主 CI,曾因超时/依赖问题失败 |
| `hardcoded-password-scan.yml` | 硬编码密码扫描（全分支） | 安全基线 |
| `hook-failsafe-e2e.yml` | tlm-hook-failsafe E2E | BOM 修复关联,PS 5.1/7 契约 |

---

## 二、工作流程

```
develop push
    │
    ▼
[1] checkout 代码（含计数器 + 脚本）
    │
    ▼
[2] 等待 5 分钟（让关键 workflow 启动并运行）
    │
    ▼
[3] 调用 develop_stability_monitor.py
    │  ├─ 读取计数器 remaining_checks
    │  ├─ remaining=0 → skip（输出 should_run=false）
    │  ├─ gh run list 查询 5 个 workflow 在当前 commit 的状态
    │  ├─ 生成 Markdown 报告 + 稳定性结论
    │  └─ 递减计数器,记录 history（force_check 模式不递减）
    │
    ▼
[4] git commit 计数器更新（[skip ci] 避免循环触发）
    │
    ▼
[5] 创建/更新 Issue 报告
       ├─ 非最终报告 → 追加评论到现有 Issue
       ├─ 首次/最终 → 创建新 Issue（label: ci-stability-monitor）
       └─ 最终且稳定 → 自动关闭 Issue
```

### 2.1 稳定性判定规则

- **stable=true**：无 failure 且无 cancelled（not_triggered 不阻塞,因部分 workflow 可能未在本次 commit 触发）
- **stable=false**：存在 failure 或 cancelled
- **is_final=true**：正常递减后 remaining=0（force_check 不触发 is_final）

### 2.2 计数器递减规则

| 场景 | remaining 变化 | is_final |
|------|----------------|----------|
| 正常 push 触发（remaining=3） | 3 → 2 | false |
| 正常 push 触发（remaining=1） | 1 → 0 | true（监控结束） |
| remaining=0 的 push | 不执行（skip） | - |
| `workflow_dispatch` force_check=true | 不递减 | false |

---

## 三、Issue 报告机制

### 3.1 Issue 标题

- 中间报告：`Develop CI 稳定性监控报告 (1/3)`、`(2/3)`
- 最终报告：`Develop CI 稳定性监控 - 最终报告 (3/3)`

### 3.2 Issue 标签

- `ci-stability-monitor`：监控专用标签
- `auto-generated`：自动生成标记

### 3.3 Issue 内容示例

```markdown
## CI 稳定性快照

- **Commit**: abc1234
- **状态**: ✅ 稳定
- **失败数**: 0
- **剩余检查**: 2

### 关键 Workflow 状态

| Workflow | Status | Conclusion | Run ID |
|----------|--------|------------|--------|
| kwarg-docker-scan.yml | completed | success | 30882099762 |
| ...
```

最终报告额外包含「🏁 监控已结束」小节,稳定时自动关闭 Issue。

---

## 四、本地验证

已通过 3 轮 dry-run 测试（fake commit SHA）：

| 测试场景 | 输入 | 期望输出 | 结果 |
|----------|------|----------|------|
| 正常流程 | remaining=3, fake SHA | should_run=true, new_remaining=2, history+1 | ✅ |
| 计数器归零 | remaining=0 | should_run=false, 不查询 workflow | ✅ |
| 强制检查 | remaining=0, force_check=true | should_run=true, remaining 保持 0, is_final=false | ✅ |
| force_check 不消耗计数器 | remaining=3, force_check=true | remaining 保持 3 | ✅ |

测试后计数器已恢复初始状态（remaining=3, history=[]）。

---

## 五、部署步骤

### 5.1 同步到 develop 分支

监控 workflow 必须存在于 develop 分支才能被 develop push 触发：

```bash
# 当前文件在 master,需同步到 develop
git checkout develop
git merge master --no-ff -m "chore(monitor): 合入 develop CI 稳定性监控"
git push origin develop
```

或用 cherry-pick（若 master 有其他不相关变更）：

```bash
git checkout develop
git cherry-pick <commit-sha-of-monitor-files>
git push origin develop
```

### 5.2 验证首次触发

develop push 后,在 GitHub Actions 页面确认：
1. `Develop CI 稳定性监控` workflow 被触发
2. 5 分钟等待后,脚本查询 5 个 workflow 状态
3. 计数器文件被 commit 更新（`[skip ci]`）
4. 创建 Issue #N（标题含 `(1/3)`）

### 5.3 重新启用监控（监控结束后）

编辑 [.github/monitoring/develop_stability_counter.json](../../.github/monitoring/develop_stability_counter.json)：

```json
{
  "remaining_checks": 3,
  "total_checks": 3,
  "started_at": "<新日期>",
  "last_check_at": null,
  "last_check_commit": null,
  "monitored_workflows": [...],
  "history": []
}
```

提交后下次 develop push 会重新开始 3 次监控。

---

## 六、防循环触发机制

| 风险 | 防护措施 |
|------|----------|
| 计数器 commit 再次触发本 workflow | commit message 含 `[skip ci]` |
| 并发 push 导致计数器写冲突 | `concurrency.cancel-in-progress: false`（排队执行,不取消） |
| gh CLI 查询失败 | 脚本捕获异常,返回 not_triggered,不中断 |
| 计数器文件损坏 | 脚本 JSON 解析失败时 log + skip,不阻塞 develop push |

---

## 七、与现有 CI 通知体系的关系

| Workflow | 职责 | 与本监控关系 |
|----------|------|-------------|
| [ci-failure-notify.yml](../../.github/workflows/ci-failure-notify.yml) | 实时通知单次 CI 失败 | 互补：实时告警 vs 周期性稳定性汇总 |
| 本监控 | 3 次推送的稳定性趋势 + 最终报告 | 不重叠,关注"修复后是否持续稳定" |

本监控不替代 ci-failure-notify 的实时告警,而是在修复后的关键窗口期提供"趋势性"保障,确认修复真正落地。
