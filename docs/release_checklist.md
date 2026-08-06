# Release 发布前最终检查清单（Checklist）

> 适用：`.github/workflows/release-auto.yml` 自动发布流程。
> 配套：[操作手册](release_workflow_manual.md)（排障/FAQ/验证记录）、[优化总结](release_workflow_summary.md)。
> 每次发布按序勾选，任一「❌」即中止并排查后再发布。

---

## A. 发布前置（代码与 CI）

- [ ] 本次要发布的变更已合并到 `master` 并推送
- [ ] CI 相关 job 全部通过（`ci.yml` / `observability-ci.yml` 等，注意 runner 排队属正常）
- [ ] `CHANGELOG.md` 内容符合预期（发布备注由 `update_changelog.py` 自动生成，确认无待合并条目遗漏）
- [ ] 版本号确认：语义化版本（如 `v1.1.0`），与 CHANGELOG/计划一致
- [ ] 本地 `git pull --rebase` 确保与远端同步（防并行会话/他人提交覆盖）

## B. 环境与密钥

- [ ] `GITEE_TOKEN` secret 已配置（GitHub → Settings → Secrets → Actions）
      — 未配置时 Gitee 同步 **Skipped 不告警**，确认这是预期行为
- [ ] workflow 级 `permissions: contents: write` + `issues: write` 已声明（alert 建 Issue 需要）
- [ ] `GITHUB_TOKEN` 无需配置（自动注入）

## C. 创建 tag（触发发布）

- [ ] tag 的 commit message **不以 `release(pypi)` 开头**（否则 guard 判定为子包发布，跳过主项目）
- [ ] 本地打 annotated tag：
      ```bash
      git tag -a vX.Y.Z -m "ci(release): 发布 vX.Y.Z"
      ```
- [ ] 推送 tag 触发工作流：
      ```bash
      git push origin vX.Y.Z
      ```
- [ ] （可选）Gitee 侧需要 tag 时：`git push gitee vX.Y.Z`
      — Gitee Release 由工作流自动同步，tag 缺失时 Gitee 创建会 404

## D. 发布过程监控（触发后立即看）

- [ ] Actions → 自动发布 → 打开本次运行
- [ ] **guard job**：确认输出 `skip=false`（放行）
      - `skip=true`（子包拦截）→ 停止，这不是主项目发布
- [ ] **auto-release**：
      - [ ] 发布备注生成成功（notes.md 行数正常）
      - [ ] GitHub Release 创建成功（`HTTP 201`；若见重试日志，确认最终成功）
      - [ ] Gitee 同步成功（重试耗尽才失败；`exit 0` 即成功）
- [ ] **alert-on-failure**：确认**未触发**（无新告警 Issue）
      - guard 失败或 auto-release 失败都会触发（needs 含 guard）

## E. 发布后验证

- [ ] GitHub Release 页面：tag、标题、正文（发布备注）正确
- [ ] Gitee Release 页面：存在且内容一致
- [ ] 从运行页 Artifacts 下载 `release-notes` 核对分类完整性
- [ ] 仓库 Issues 无「发布失败告警」新条目

## F. 失败处理速查（对照手册 §6 详排）

| 现象 | 处理 |
|---|---|
| GitHub/Gitee 409/422（tag 已存在 Release） | 幂等冲突不重试 → 用 `-Update` 更新模式 / PATCH 编辑接口 |
| 401（token 无效） | 重新生成 GITEE_TOKEN（勾选 projects 权限）→ 更新 secret |
| 404（仓库/tag 不存在） | 确认 tag 已推送、token 有仓库权限 |
| 重试 3 次×10s 耗尽 | 自动创建告警 Issue → 按 Issue 排查指引定位 |
| 网络超时/5xx/403 | 已走重试；若最终失败，查响应体 `message` 字段 |
| guard 失败（git 命令报错） | 现会触发告警（needs 含 guard）；查看 guard job 日志 |

## G. 发布后清理（仅测试发布需要）

- [ ] 测试 tag 本地删除：`git tag -d vX-test`
- [ ] GitHub 测试 Release 删除：`gh release delete vX-test --yes --cleanup-tag`
- [ ] Gitee 测试 Release 删除：API `DELETE /releases/{id}` + `git push gitee :refs/tags/vX-test`
- [ ] 本地模拟产物清理（mock 进程、`.sim-gh/` 临时文件、测试 tag）
