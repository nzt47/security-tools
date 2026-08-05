# Release 发布流程模板

> **用途**: 标准发布流程复用模板（基于 v1.5.0-bm25-normalization 实际发布流程总结）
> **参考实例**: [RELEASE_V150_FINAL_ARCHIVE.md](./RELEASE_V150_FINAL_ARCHIVE.md)
> **适用**: 版本发布（如 v1.6.0 / v2.0.0），含 Tag、GitHub Release、CI 验证、归档

---

## 0. 占位符约定

| 占位符 | 说明 | 示例 |
|---|---|---|
| `{{VERSION}}` | 版本号 | `v1.5.0-bm25-normalization` |
| `{{TITLE}}` | Release 标题 | `v1.5.0 BM25 短文档归一化优化 (b=0.75 → 0.5)` |
| `{{TARGET_COMMIT}}` | Tag 指向提交 | `9f6289f2` |
| `{{RELEASE_NOTES_FILE}}` | 发布说明文件 | `RELEASE_NOTES.md` |
| `{{ARCHIVE_FILE}}` | 归档清单文件 | `RELEASE_V150_FINAL_ARCHIVE.md` |

## 1. 前置检查

```powershell
# 1.1 工作树状态（确认无未提交的本次变更）
git status --short

# 1.2 确认待发布提交已在 master 且已推送
git log --oneline -5
git fetch origin
git rev-list --count origin/master..master    # 期望 0

# 1.3 回归测试（本地冒烟）
pytest tests/unit/ -q
```

> 【不易】发布前必须确认：工作树干净、master 与 origin/master 同步、回归测试通过。

## 2. 更新发布说明

- 在 `{{RELEASE_NOTES_FILE}}` 顶部追加 `## {{VERSION}}（日期）` 章节
- 记录：问题背景 / 解决方案 / 效果数据表 / 变更文件表 / 兼容性与回滚方式
- 提交：`git commit -m "docs(release): {{VERSION}} 发布说明"`

## 3. 创建并推送 Tag

```powershell
# 3.1 创建 annotated tag（含签名者信息，推荐）
git tag -a {{VERSION}} -m "Release {{VERSION}}: {{TITLE}}" {{TARGET_COMMIT}}

# 3.2 验证 tag 指向
git rev-list -n 1 {{VERSION}}

# 3.3 推送双远程（origin + gitee）
git push origin {{VERSION}} --no-verify
git push gitee  {{VERSION}} --no-verify

# 3.4 双远程验证（annotated tag 双条目：refs/tags/ + ^{} dereferenced）
git ls-remote --tags origin {{VERSION}}
git ls-remote --tags gitee  {{VERSION}}
```

> 【简易】annotated tag 用 `-a`，`ls-remote` 应同时看到 `refs/tags/{{VERSION}}` 与 `^{}` 两个条目。

## 4. 创建 GitHub Release

```powershell
# 4.1 提取发布说明章节到临时文件（用 python 避免 PowerShell 编码坑）
python -c "import io; s=io.open('{{RELEASE_NOTES_FILE}}',encoding='utf-8').read(); st=s.index('## {{VERSION}}'); ed=s.index('### 历史版本'); io.open('.release_notes_tmp.md','w',encoding='utf-8').write(s[st:ed].rstrip())"

# 4.2 创建 Release（关联已推送 tag）
gh release create {{VERSION}} --title "{{TITLE}}" --notes-file .release_notes_tmp.md

# 4.3 验证（draft=false / prerelease=false）
gh release view {{VERSION}} --json tagName,name,isDraft,isPrerelease,publishedAt,url

# 4.4 清理临时文件
Remove-Item .release_notes_tmp.md
```

## 5. 验证 Release 事件 CI

```powershell
# 5.1 拉取 release 事件触发的工作流
gh run list --event release --limit 5

# 5.2 等待完成并确认 success（--exit-status: 非 0 即失败）
gh run watch <RUN_ID> --exit-status --interval 15

# 5.3 记录结论（job 明细）
gh run view <RUN_ID> --json status,conclusion,displayTitle,event,headSha,url
```

> 【变易】Release 发布会触发仓库配置的 release 事件工作流（如文档自动构建）。确认 `conclusion=success` 后再进入归档。

## 6. 生成归档清单

归档清单（`{{ARCHIVE_FILE}}`）应包含：

- [ ] 发布链接：GitHub Release URL + Release 事件 CI Run URL
- [ ] 提交哈希清单：核心变更 / 归档 / 补充提交（用 `git log -S` 或 `git log -- <file>` 核对归属）
- [ ] 变更文件列表：**按提交分组，核心代码/配置不得遗漏**（用 `git show --name-only --format="" <commit>` 全量核对）
- [ ] CI 验证状态表（job 明细 + 耗时）
- [ ] 效果数据表（供审计）
- [ ] 归档文档索引

> 【不易】变更文件列表核对必须用 `git show --name-only --format=""` 全量比对，避免遗漏核心代码/配置（v1.5.0 曾漏掉 `config.yaml` BM25 权重与 `ci.yml` 回归 step）。

## 7. 收尾清理

```powershell
# 7.1 确认无临时文件残留（*.tmp / .commit_msg_* / .release_notes_*）
git status --short

# 7.2 提交归档清单
git add {{ARCHIVE_FILE}}
git commit -F .commit_msg_tmp.md --no-verify   # 或 git commit -m "docs(release): {{VERSION}} 归档"

# 7.3 推送归档（如需）
git push origin master --no-verify
```

## 8. 检查清单（Checklist）

- [ ] 前置检查：工作树干净 / master 同步 / 测试通过
- [ ] `{{RELEASE_NOTES_FILE}}` 已更新
- [ ] Tag 已创建（annotated）并推送 origin + gitee
- [ ] GitHub Release 已创建（非 draft / 非 prerelease）
- [ ] Release 事件 CI `conclusion=success`
- [ ] 归档清单已生成且变更文件列表全量核对无遗漏
- [ ] 临时文件已清理
