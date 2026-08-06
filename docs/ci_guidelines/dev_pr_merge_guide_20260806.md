# 开发团队 PR 合入操作指南（避免 push 竞争）

- **日期**: 2026-08-06
- **背景**: `d55abd03` 直接 push master 触发 guard ORIGIN-04 拦截 + 云枢"更新 CI 健康度
  看板"job 并发 push 竞争失败（non-fast-forward）后沉淀的团队纪律文档
- **适用范围**: 所有向 `master` 合入代码的开发人员 / CI workflow 作者

---

## 1. 背景与教训

2026-08-06 推送 `d55abd03` 到 master 引发两类问题：

1. **guard ORIGIN-04 拦截**：人工 commit 直接 push master（无关联 PR）→ 在 enforce
   模式下被 BLOCK，产生 CI 失败通知噪音。
2. **看板 push 竞争**：`update-ci-dashboard` job 基于触发时的远端 HEAD，作业运行期间
   远端 master 被并行 workflow 推进，直接 `git push` 报 non-fast-forward。

**核心纪律**：master 是**受保护合入目标**，一律走 PR；CI 自动提交类（看板等）需
`pull --rebase` + 重试防御。

## 2. 硬性规则（守 ORIGIN-04 契约）

| # | 规则 | 理由 |
|---|---|---|
| R1 | **禁止人工直接 push master** | guard ORIGIN-04（enforce 下 BLOCK；dry-run 下产生噪音） |
| R2 | master 合入统一走 PR | PR 提供人工身份与提交的关联证据 |
| R3 | CI 自动写回 master 的 job 必须 `pull --rebase` + 重试 | 并发 workflow 推进远端导致 non-fast-forward |
| R4 | CI 自动提交用 `[skip ci]` | 避免递归触发 CI 死循环 |

## 3. 标准合入流程（10 步）

```powershell
# 1) 基于最新 master 拉分支
git fetch origin
git checkout -b feat/your-change origin/master

# 2) 本地提交前设置 hook 跨仓库寻址（必需）
$env:TLM_HOOK_SOURCE_REPO = "C:\Users\Administrator\agent"

# 3) 修改代码 → 暂存（只暂存本次文件，避免混入并行会话改动）
git add <files>
git commit -m "type(scope): 简述变更"

# 4) 推送分支（pre-push hook 自动校验核心不变量）
git push -u origin feat/your-change

# 5) 创建 PR（base=master）
gh pr create --base master --head feat/your-change --title "..." --body "..."

# 6) 等待 CI 全绿
gh pr checks feat/your-change --watch

# 7) 处理失败：flaky 性能断言 → 放宽阈值（走 PR）；环境类 → 重试；业务类 → 修复
# 8) squash 合入（保持 master 线性历史）
gh pr merge --squash --delete-branch

# 9) 合入后确认 guard（PR 合入满足 ORIGIN-04，不会 BLOCK）
# 10) 清理本地分支
git branch -d feat/your-change
```

## 4. 避免 push 竞争的具体手段

### 4.1 人工合入（R2）

- 合入**只用** `gh pr merge --squash`，由 GitHub 处理远端一致性，天然无竞争。
- 禁止 `git push origin master` 人工直推。

### 4.2 CI 自动写回（R3，本次已落地）

参考 `ci.yml` `update-ci-dashboard` job（已修复）：commit 后

```bash
for i in 1 2 3; do
  if git pull --rebase origin master; then
    if git push origin master; then
      echo "已推送看板更新 (attempt $i)"
      exit 0
    fi
  fi
  echo "[dashboard] push 竞争失败 (attempt $i/3)，5s 后重试"
  sleep 5
done
echo "::warning::看板更新 3 次重试仍失败，本次跳过（下次推送自动补齐）"
```

要点：
- `fetch-depth: 0`（checkout 需完整历史才能 rebase）
- 权限 `contents: write` + 默认 GITHUB_TOKEN（不递归触发 CI）
- 3 次重试耗尽后 `::warning::` 跳过而非 exit 非 0（可丢失更新不阻塞 CI）

## 5. 冲突处理指引

| 场景 | 处理 |
|---|---|
| PR 有冲突 | `gh pr edit` 后本地 `git rebase origin/master` 解决，force-push 分支（分支可 force，master 不可） |
| CI 自动提交 rebase 冲突 | 看板追加场景不会触发（行级追加自动合并）；理论冲突时本次跳过，下次补齐 |
| 性能断言 flaky | 按 `docs/observability/ci_fix_validation_report_20260806.md` 流程：识别 → 分析余量 → 放宽阈值 → 走 PR |

## 6. 命令速查

| 操作 | 命令 |
|---|---|
| 创建 PR | `gh pr create --base master --head <分支> --title "..." --body "..."` |
| 查看 PR checks | `gh pr checks <分支> --watch` |
| squash 合入 | `gh pr merge --squash --delete-branch` |
| 查看守卫模式 | `gh variable get COMMIT_ORIGIN_GUARD_MODE` |
| 切换守卫模式 | `gh variable set COMMIT_ORIGIN_GUARD_MODE -b dry-run`（或 `-b enforce`） |

## 7. 相关文档

- 本次问题跟踪单：`docs/troubleshooting/ci_failures_tracking_20260806.md`
- 守卫拦截修复指南：`docs/troubleshooting/commit_origin_guard_fix_guide_20260806.md`
- 性能断言 flaky 先例：`docs/observability/ci_fix_validation_report_20260806.md`
