# 回滚操作指南 — ChromaDB 导入降级预检工具包 v1.0.0-preflight

**适用发布**：`v1.0.0-preflight`（commit `6c83fb32`，master）
**适用场景**：发布后发现预检工具包引入回归（如 CI preflight job 误阻断、容器运行异常等），需将 master 回滚至发布前状态。

---

## 0. 回滚前确认（不易：先看清状态再动手）

```powershell
git fetch origin
git log --oneline master -3          # 确认 master HEAD 与预检提交位置
git status                           # 确认工作区干净（或自行备份未提交修改）
```

master 当前结构（回滚前）：

```
c13069ee Merge remote-tracking branch 'origin/master'
6c83fb32 feat(preflight): ... v1.0.0   ← 本次发布的预检工具包提交（回滚目标）
51d6aa0d feat(skills): Dynamic Few-shot 注入器
5b5c2089 docs(architecture): 自动更新模块依赖图 [skip ci]
```

---

## 1. 回滚方式 A — `git revert`（推荐，保留历史）

生成反向提交，历史保留，适合已推送的 master。

```powershell
git checkout master
git pull origin master                       # 确保本地与远程同步
git revert --no-edit 6c83fb32                # 生成反向提交，回滚预检工具包
git push origin master                       # 推送（注意 pre-push 需设置 TLM_HOOK_SOURCE_REPO）
```

**验证**：
```powershell
git log --oneline -1                         # 应看到 revert 提交
git diff 6c83fb32..HEAD --stat -- agent/preflight scripts/chromadb_preflight.ps1 Dockerfile
# 预期为空（revert 后与发布前一致）
```

**CI 影响**：`ci.yml` 中 `chromadb-preflight` job 被移除 → `unit-tests` 的 `needs` 依赖解除，恢复独立运行，不再被预检阻断。

## 2. 回滚方式 B — `reset` + force push（破坏性，不推荐）

彻底抹除发布提交，仅适用于**未推送**或**可接受历史重写**（master 有保护规则，通常需 bypass）。

```powershell
git checkout master
git reset --hard 51d6aa0d                    # 回到预检提交的父提交
git push --force origin master               # ⚠️ 需 bypass 分支保护规则，高风险
```

> 不推荐：master 已被并行会话复用（含 merge 提交 c13069ee），reset 会连带丢失其后的提交。

## 3. 部分回滚（保留工具包，仅降级 CI 阻断）

若只想解除 CI 阻断而保留本地工具包：

- 从 `ci.yml` 移除 `chromadb-preflight` job 及 `unit-tests` 的 `needs: [chromadb-preflight]`
- 提交并推送，`unit-tests` 恢复独立运行
- 本地仍可用 `python -m agent.preflight` / `scripts/view_chromadb_logs.ps1`

## 4. 标签处理

| 场景 | 操作 |
|------|------|
| 回滚后标签不再指向最新 | `git tag -d v1.0.0-preflight` + `git push origin :refs/tags/v1.0.0-preflight` |
| 修复后重新发布 | 重打标签 `git tag v1.0.0-preflight <新提交>` + `git push origin v1.0.0-preflight` |

> 注意：`v1.0.0`（并行会话的发布就绪检查）与 `v1.0.0-preflight` 是**两个独立标签**，回滚预检工具包不影响 `v1.0.0`。

## 5. 回滚后验证清单

- [ ] `git log --oneline -3` 确认回滚提交在 master 上
- [ ] CI 触发新 run：`chromadb-preflight` job 不存在，`unit-tests` 正常运行
- [ ] 本地回归：`python -m pytest tests/unit -q`（移除 preflight 后其余用例仍绿）
- [ ] `scripts/view_chromadb_logs.ps1` 不再可用（工具包已回滚，符合预期）
- [ ] 若需恢复发布：`git revert <revert提交>`（revert 的 revert）或按 §4 重打标签

## 6. 回滚后恢复（重新发布）

```powershell
git revert --no-edit <上一步的 revert 提交哈希>    # 恢复预检工具包
git push origin master
git tag v1.0.0-preflight <新提交哈希> && git push origin v1.0.0-preflight
```

---

## 附：回滚决策速查

| 情况 | 方式 | 风险 |
|------|------|------|
| 仅 CI 阻断异常 | §3 部分回滚 | 低 |
| 工具包功能性回归（已推送） | §1 revert | 低 |
| 工具包功能性回归（未推送） | §2 reset | 中（历史重写） |
| 需完全撤销发布痕迹 | §2 + §4 删标签 | 高 |
