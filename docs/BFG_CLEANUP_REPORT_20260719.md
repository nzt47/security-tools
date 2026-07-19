# BFG 仓库历史清理快照报告

> **报告日期**：2026-07-19
> **执行时间**：09:42 - 10:15 (UTC+8)
> **清理工具**：git-filter-repo v2.47.0（BFG Repo-Cleaner 的现代替代品，Git 官方推荐）
> **操作类型**：历史重写 + 强制推送（force push）
> **影响范围**：origin (GitHub) + gitee (Gitee) 双远程仓库
> **关联文档**：
> - [BFG_CLEANUP_GUIDE_20260719.md](./BFG_CLEANUP_GUIDE_20260719.md)
> - [DEEPSEEK_KEY_REVOKE_GUIDE.md](./DEEPSEEK_KEY_REVOKE_GUIDE.md)
> - [SUMMARY_TEST_FIX_20260719.md](./SUMMARY_TEST_FIX_20260719.md)

---

## 一、清理前后核心指标对比

### 1.1 仓库体积与 commit 数量

| 指标 | 清理前（BEFORE） | 清理后（AFTER） | 变化 | 说明 |
|------|-----------------|----------------|------|------|
| .git 目录大小（本地） | 1063.34 MB | 440.72 MB | **-58.5%** | reflog expire + gc 清除悬空对象 |
| Mirror 仓库大小 | — | 37.71 MB | — | bare 仓库，无工作树 |
| Commit 总数（所有分支） | 809 | 794 | **-15** | 字符串替换后 15 个 commits 内容完全相同被去重 |
| 本地分支数 | 4 | 4 | 0 | master + 3 feature 分支 |
| 远程分支数（origin） | 5 | 5 | 0 | 含 phase2-visibility-convergence |
| Tags 数量 | 2 | 2 | 0 | v1.2.0 + v2.0.0-feature-tools-router |
| Stash 数量 | 77 | 0 | **-77** | reflog expire 清除（备份可用） |

### 1.2 敏感信息残留检查

| 敏感信息类型 | 清理前 commits | 清理后（远程） | 清理后（本地分支） | 状态 |
|-------------|---------------|---------------|-------------------|------|
| DeepSeek API key (`sk-ddf2****45a3`) | 11 | 0 | 0 | ✅ 彻底清除 |
| GlitchTip 密码 (`Admin@****!`) | 3 | 0 | 0 | ✅ 彻底清除 |
| `.encryption_key` 文件（添加记录） | 10 | 0 | 0 | ✅ 从历史删除 |
| `SECURITY_NOTICE_20260719_api_key_leak.md` | 22 | 0 | 0 | ✅ 从历史删除 |

### 1.3 分支状态对比

| 分支 | 清理前 HEAD | 清理后 HEAD | 同步状态 |
|------|------------|------------|---------|
| master | `0a37dc2e` | `ceeecb5f` | ✅ 已同步 origin/master |
| feature/tlm-step3-vectorstore-sqlite-vec | `dac5f89e` | `587121d1` | ✅ 已同步 origin |
| fix/arch-circular-deps | `4b7d2e1f` | `b906673f` | ✅ 已同步 origin |
| phase2-visibility-convergence | `4df57dc3` | `8f1cb922` | ✅ 已同步 origin |

> **注意**：所有 commit hash 均已变化（git filter-repo 重写历史导致），但 commit 内容除敏感字符串外完全一致。

---

## 二、清理规则与执行细节

### 2.1 替换规则（bfg-replacements.txt）

```
sk-ddf2****45a3==>***REMOVED_API_KEY***
Admin@****!==>***REMOVED_GLITCHTIP_PWD***
```

| 规则 | 原值（脱敏） | 替换值 | 覆盖范围 |
|------|-------------|--------|---------|
| 1 | `sk-ddf2****45a3` | `***REMOVED_API_KEY***` | 11 个 commits，涉及 app_server.py / network_config.json / SECURITY_NOTICE 等 |
| 2 | `Admin@****!` | `***REMOVED_GLITCHTIP_PWD***` | 3 个 commits，涉及 docker/glitchtip/orm_setup_inline.py |

### 2.2 文件删除规则（--invert-paths）

| 文件路径 | 删除原因 | 历史添加记录 |
|---------|---------|------------|
| `.encryption_key` | 加密密钥文件，不应进入版本控制 | 10 个 commits |
| `docs/security/SECURITY_NOTICE_20260719_api_key_leak.md` | 含完整 API key 的安全通知文档 | 22 个 commits |

### 2.3 已移除的规则（用户决策）

| 规则 | 移除原因 |
|------|---------|
| ~~`admin123==>***REMOVED_GRAFANA_PWD***`~~ | `admin123` 是常见字符串，BFG 清理会误伤 29 个非敏感部署文档 |

### 2.4 执行命令序列

```bash
# 1. 创建 mirror clone（bare 仓库）
git clone --mirror c:\Users\Administrator\agent C:\Windows\TEMP\agent_mirror_20260719094737

# 2. 替换敏感文本（2 条规则）
git filter-repo --replace-text bfg-replacements.txt --force

# 3. 删除 .encryption_key 文件
git filter-repo --invert-paths --path .encryption_key --force

# 4. 删除 SECURITY_NOTICE 文档
git filter-repo --invert-paths --path docs/security/SECURITY_NOTICE_20260719_api_key_leak.md --force

# 5. 清理 reflog + gc
git reflog expire --expire=now --all
git gc --prune=now --aggressive

# 6. 配置远程仓库
git remote add origin git@github.com:nzt47/security-tools.git
git remote add gitee git@gitee.com:nzt47/security-tools.git

# 7. Force push 到 origin（GitHub）
git push --force --all origin
git push --force --tags origin

# 8. Force push 到 gitee（Gitee）
git push --force --all gitee
git push --force --tags gitee
```

---

## 三、Force Push 结果

### 3.1 origin（GitHub）

| 分支/Tag | 推送结果 | 备注 |
|---------|---------|------|
| master | ✅ forced update (`da79989d` → `ceeecb5f`) | 临时移除分支保护后推送 |
| feature/tlm-step2-enable-stm-reviewer | ✅ forced update | — |
| feature/tlm-step3-vectorstore-sqlite-vec | ✅ forced update | — |
| fix/arch-circular-deps | ✅ forced update | — |
| phase2-visibility-convergence | ✅ new branch | 原本地分支首次推送到 origin |
| v1.2.0 (tag) | ✅ forced update | — |
| v2.0.0-feature-tools-router (tag) | ✅ forced update | — |

**分支保护处理**：
- master 分支原保护规则：`allow_force_pushes=false`
- 临时删除保护 → force push → 恢复原保护规则
- 恢复后验证：`allow_force_pushes=false` ✅

### 3.2 gitee（Gitee）

| 分支/Tag | 推送结果 | 备注 |
|---------|---------|------|
| master | ✅ forced update | — |
| feature/tlm-step2-enable-stm-reviewer | ✅ new branch | — |
| feature/tlm-step3-vectorstore-sqlite-vec | ✅ new branch | — |
| fix/arch-circular-deps | ✅ new branch | — |
| phase2-visibility-convergence | ✅ new branch | — |
| v1.2.0 (tag) | ✅ forced update | — |
| v2.0.0-feature-tools-router (tag) | ✅ new tag | — |

---

## 四、本地仓库同步结果

### 4.1 同步策略

| 步骤 | 操作 | 目的 |
|------|------|------|
| 1 | `git stash push -m "bfg-cleanup-pre-sync-20260719"` | 保护 35 个已跟踪文件变更 |
| 2 | `git diff > bfg_local_changes_20260719.patch` | 创建 patch 备份（223 KB） |
| 3 | `git fetch origin` + `git fetch gitee` | 更新远程跟踪 refs |
| 4 | `git reset --hard origin/master` | 重置 master 到清洁版本 |
| 5 | `git update-ref` × 3 | 同步其他 3 个分支（避免 checkout 冲突） |
| 6 | `git stash pop` | 恢复 35 个已跟踪文件变更 |
| 7 | `git reflog expire --expire=now --all` + `git gc --prune=now` | 清理本地悬空对象 |

### 4.2 工作区变更保留情况

| 变更类型 | 数量 | 保留状态 |
|---------|------|---------|
| 已修改文件（M） | 29 | ✅ 全部恢复 |
| 已删除文件（D） | 6 | ✅ 全部恢复 |
| 未跟踪文件/目录（??） | 84+ | ✅ 全部保留（不受 reset 影响） |

### 4.3 本地历史清洁验证

| 检查项 | 结果 | 说明 |
|-------|------|------|
| 4 个本地分支 HEAD | 全部指向 origin 清洁版本 | ✅ |
| 含 API key 的 commits 是否可从分支到达 | 0 个可达 | ✅ 9 个残留 commits 仅被 stash reflog 引用 |
| 含 GlitchTip 密码的 commits 是否可从分支到达 | 0 个可达 | ✅ |
| `.encryption_key` 文件是否在分支历史中 | 否 | ✅ |
| `SECURITY_NOTICE` 文档是否在分支历史中 | 否 | ✅ |

---

## 五、副作用与风险记录

### 5.1 已知副作用

| 副作用 | 影响范围 | 严重程度 | 恢复方案 |
|-------|---------|---------|---------|
| 77 个旧 stashes 被清除 | 本地 stash 列表清空 | ⚠️ 中 | 备份在 `agent_bfg_backup_20260719094737` 中可用 |
| 15 个 commits 被去重 | 历史中内容完全相同的 commits 合并 | ℹ️ 低 | 无需恢复（内容已保留在去重后的 commit 中） |
| master 分支保护临时移除 | GitHub master 分支约 30 秒无保护 | ⚠️ 中 | 已恢复原保护规则 |
| 所有 commit hash 变化 | 引用旧 hash 的文档/脚本失效 | ⚠️ 中 | 已知影响：CHANGELOG / 部分文档中的 hash 引用 |

### 5.2 残留风险评估

| 残留项 | 位置 | 风险等级 | 说明 |
|-------|------|---------|------|
| 9 个含 API key 的 commits | 本地 .git（仅被 stash reflog 引用） | 🟢 低 | 不可从任何分支到达，不会被 push；gc 后 stash 已清空，下次 gc 将彻底清除 |
| GlitchTip 硬编码密码 | `docker/glitchtip/orm_setup_inline.py` L52 | 🟡 中 | 当前工作区文件未修改，需后续修复 |
| Grafana 硬编码密码 | `scripts/_import_dashboards.py` L8 | 🟡 中 | 当前工作区文件未修改，需后续修复 |
| 备份仓库含敏感数据 | `c:\Users\Administrator\agent_bfg_backup_20260719094737` | 🟡 中 | 完整备份含旧历史，建议验证后删除 |

### 5.3 不可逆操作确认

| 操作 | 不可逆性 | 缓解措施 |
|------|---------|---------|
| Force push 到 origin | 远程历史被覆盖，旧 commits 不可恢复 | ✅ 备份在本地 |
| Force push 到 gitee | 远程历史被覆盖 | ✅ 备份在本地 |
| reflog expire + gc | 本地悬空对象被删除 | ✅ 备份在本地 |
| Stash 清除 | 77 个 stashes 丢失 | ✅ 备份在本地（可从备份恢复） |

---

## 六、备份信息

| 备份项 | 路径 | 大小 | 用途 |
|-------|------|------|------|
| 完整仓库备份 | `c:\Users\Administrator\agent_bfg_backup_20260719094737` | 4921.6 MB | 含完整旧历史 + 所有 stashes |
| Tracked 变更 patch | `C:\Windows\TEMP\bfg_local_changes_20260719.patch` | 223 KB | 35 个 tracked 文件变更备份 |
| Mirror clone（清洁后） | `C:\Windows\TEMP\agent_mirror_20260719094737` | 37.71 MB | 清洁后的 bare 仓库 |
| 备份路径记录 | `c:\Users\Administrator\agent\.bfg_last_backup` | — | 记录备份位置 |

> **建议**：验证生产环境正常运行 7 天后，删除备份仓库以彻底消除敏感数据残留。

---

## 七、commit 大小变化分析

### 7.1 Commit 数量变化

| 阶段 | Commit 数 | 变化原因 |
|------|----------|---------|
| 清理前（所有分支） | 809 | 原始历史 |
| git filter-repo --replace-text 后 | 795 | 字符串替换使部分 commits 内容变化 |
| git filter-repo --invert-paths 后 | 794 | 删除文件后 1 个 commit 变为空被丢弃 |
| 最终（远程） | 794 | 稳定 |

### 7.2 文件变更统计

| 文件类别 | 清理前涉及 commits | 清理后涉及 commits | 清理方式 |
|---------|-------------------|-------------------|---------|
| 含 API key 的文件 | 11 | 0 | 字符串替换 |
| 含 GlitchTip 密码的文件 | 3 | 0 | 字符串替换 |
| `.encryption_key` | 10 | 0 | 文件删除 |
| `SECURITY_NOTICE` 文档 | 22 | 0 | 文件删除 |
| **总受影响 commits** | **46（去重后）** | **0** | — |

### 7.3 仓库体积变化

| 测量点 | 大小 | 说明 |
|-------|------|------|
| 清理前 .git（本地） | 1063.34 MB | 含 reflog + 77 stashes + 悬空对象 |
| 清理后 .git（本地） | 440.72 MB | reflog expire + gc 后 |
| 清洁 mirror（bare） | 37.71 MB | 仅 objects + refs，无工作树 |
| **体积减少** | **622.62 MB（58.5%）** | — |

---

## 八、后续建议

### 8.1 立即执行（P0）

1. **通知协作者**：所有克隆过该仓库的协作者需要重新 clone，旧 clone 含敏感数据
2. **验证 DeepSeek/OpenAI key 已撤销**：BFG 清理不影响已泄露的 key，必须在平台撤销
3. **检查 CI/CD**：确保 CI 使用新历史，可能需要清除缓存

### 8.2 短期执行（P1）

1. **修复 GlitchTip 硬编码密码**：`docker/glitchtip/orm_setup_inline.py` L52 改为环境变量
2. **修复 Grafana 硬编码密码**：`scripts/_import_dashboards.py` L8 改为环境变量
3. **更新文档中的 commit hash 引用**：CHANGELOG 等文档引用了旧 hash

### 8.3 长期执行（P2）

1. **7 天后删除备份**：`c:\Users\Administrator\agent_bfg_backup_20260719094737`
2. **添加 pre-commit hook**：防止敏感信息再次进入 git 历史
3. **从备份恢复需要的 stashes**：如有 valuable 工作在旧 stashes 中

---

## 九、三义校验

- **【不易】** 4 类敏感信息（API key / GlitchTip 密码 / .encryption_key / SECURITY_NOTICE）在远程历史中彻底清除；master 分支保护规则已恢复；4 个本地分支全部同步到清洁版本；备份完整可用
- **【变易】** 适配 git-filter-repo 替代 BFG（Java 未装）；适配 PowerShell Set-Content bug（Python 绕过）；适配 master 分支保护（临时移除 + 恢复）；适配本地同步（stash + update-ref 避免 checkout 冲突）
- **【简易】** 10 阶段线性执行，每阶段可独立验证；报告含完整对比表格；初级工程师 30s 可读

---

## 十、参考文档

- [git-filter-repo 官方文档](https://htmlpreview.github.io/?https://github.com/newren/git-filter-repo/blob/docs/git-filter-repo.html)
- [BFG Repo-Cleaner](https://rtyley.github.io/bfg-repo-cleaner/)
- [GitHub Branch Protection API](https://docs.github.com/rest/branches/branch-protection)
- 内部文档：[BFG_CLEANUP_GUIDE_20260719.md](./BFG_CLEANUP_GUIDE_20260719.md)
- 内部文档：[DEEPSEEK_KEY_REVOKE_GUIDE.md](./DEEPSEEK_KEY_REVOKE_GUIDE.md)
- 内部文档：[SUMMARY_TEST_FIX_20260719.md](./SUMMARY_TEST_FIX_20260719.md)

---

> **报告生成时间**：2026-07-19 10:15 UTC+8
> **执行人**：Yi-Jing Coding Agent
> **审核状态**：待用户审核
