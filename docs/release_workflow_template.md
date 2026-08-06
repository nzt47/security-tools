# Release 工作流可复用模板（guard + alert-on-failure）

> 来源：`.github/workflows/release-auto.yml`（本仓库 nzt47/security-tools 已实战验证）。
> 用途：其他发布类项目直接复制适配，避免重踩「静默失败/无告警中断」坑。
> 配套：ps1 脚本 `TimeoutSec=30` 防御见[操作手册 §6.1](release_workflow_manual.md)。
> 适配必读：本文件「兼容性检查」一节。

---

## 1. 模板适用场景

- 项目有 tag 驱动自动发布（`push: tags: ['v*']`）
- 仓库同时存在「主项目发布」与「子包发布」tag，需要守卫区分（可选，无子包可删 guard）
- 发布失败需要自动建告警 Issue（GITHUB_TOKEN 零依赖）

## 2. guard job 模板（子包 tag 守卫）

```yaml
jobs:
  guard:
    name: 子包 tag 守卫
    runs-on: ubuntu-latest
    timeout-minutes: 5
    outputs:
      skip: ${{ steps.g.outputs.skip }}
    steps:
      - name: 检出代码（含 tag 历史）
        uses: actions/checkout@v6
        with:
          fetch-depth: 0        # 必须：git log -1 需要 tag 的 commit 历史
      - name: 判断是否为子包发布 tag
        id: g
        run: |
          set -euo pipefail
          MSG=$(git log -1 --format=%s "${GITHUB_REF_NAME}")
          echo "tag commit message: $MSG"
          if echo "$MSG" | grep -qiE '^release\(pypi\)'; then   # ← 适配点：子包前缀正则
            echo "skip=true" >> "$GITHUB_OUTPUT"
            echo "::warning::子包发布 tag，跳过主项目自动发布"
          else
            echo "skip=false" >> "$GITHUB_OUTPUT"
          fi
```

## 3. alert-on-failure job 模板（失败告警）

```yaml
  alert-on-failure:
    name: 发布失败告警（创建 GitHub Issue）
    # 需包含 guard：guard 失败时 auto-release 因 needs 失败被跳过（skipped），
    # 原 `needs: auto-release` 下 if: failure() 对 skipped 返回 false → 无告警静默中断。
    # 含 guard 后：guard 失败 → failure() 为 true → 正常触发告警；skip=true 拦截仍不告警
    needs: [guard, auto-release]          # ← 若项目删除了 guard，此处只留 auto-release
    if: failure()
    runs-on: ubuntu-latest
    timeout-minutes: 5
    steps:
      - name: 创建告警 Issue
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}    # 自动注入，无需配置
        run: |
          set -euxo pipefail
          VERSION="${{ needs.auto-release.outputs.version }}"
          [ -n "$VERSION" ] || VERSION="${{ github.ref_name }}"   # guard 失败时 outputs 为空，兜底
          RUN_URL="${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}"
          if ISSUE_URL=$(gh issue create \
              --title "发布失败告警: $VERSION (${{ github.workflow }})" \
              --body "- 版本: $VERSION
              - 运行链接: $RUN_URL
              - 触发方式: ${{ github.event_name }}
              - 失败时间: $(date -u +'%Y-%m-%dT%H:%M:%SZ')"); then
            echo "==> 告警 Issue 创建成功: $ISSUE_URL"
          else
            RC=$?
            echo "::error::gh issue create 失败（退出码 $RC）"
            exit "$RC"          # else 内捕获 $?，防 bash if 吞退出码（手册 §9）
          fi
```

## 4. 配套注意事项（复制时必须一起带）

1. **workflow 级 permissions**：
   ```yaml
   permissions:
     contents: write    # auto-release 需要（GitHub Release API）
     issues: write      # alert-on-failure 需要（gh issue create）
   ```
2. **auto-release job 必须输出 version** 供告警引用：
   ```yaml
   outputs:
     version: ${{ steps.ver.outputs.VERSION }}
   ```
3. **curl 调用必带三件套**（防静默失败，手册 §6.1/Q3）：
   ```bash
   CODE=$(curl -s -o gh_resp.json -w '%{http_code}' --max-time 30 -X POST ...) || CODE=500
   [ -s gh_resp.json ]   # 读取响应体前容错
   ```
4. **Gitee 同步脚本**若复用 `create_gitee_release.ps1`：`Invoke-Gitee` 必须带 `TimeoutSec = 30`
   默认值（否则 API 挂起时无限等待拖满 step 超时）。
5. **GITEE_TOKEN**：不同项目各自配置 secret；未配置时 Gitee step 用
   `if: env.GITEE_TOKEN != ''` 安全跳过（不告警，属预期）。

## 5. 兼容性检查清单（复制到新项目前逐项核对）

| # | 检查项 | 说明 / 适配 |
|---|---|---|
| 1 | 子包 tag 约定 | guard 正则 `^release\(pypi\)` 是本仓库约定；其他项目按自己的子包前缀调整，**勿用宽泛关键词**（会误拦主项目，教训见手册 §2） |
| 2 | 无子包项目 | 删除 guard job，alert-on-failure `needs: auto-release` 即可（不存在 guard 失败场景） |
| 3 | 事件触发 | `on.push.tags: ['v*']` 会匹配项目内所有 `v*` tag——确认无其他用途的 v* tag（如子包） |
| 4 | 全量 git 历史 | 两处 `checkout` 都必须 `fetch-depth: 0`，否则 `git log`/`git tag` 结果不完整 |
| 5 | GITHUB_TOKEN 权限 | 无需 PAT；但 `permissions:` 必须在 workflow 顶层声明，**若项目有其他 job 用了更严格权限会覆盖**，注意合并 |
| 6 | version 兜底 | guard 失败时 `needs.auto-release.outputs.version` 为空，模板已用 `ref_name` 兜底 |
| 7 | timeout-minutes | guard 5 / auto-release 10 / alert 5，按项目 API 响应速度调整（`--max-time 30` 与之匹配） |
| 8 | gh CLI | `ubuntu-latest` 预装 gh；若换 runner 需自行安装 |
| 9 | Gitee 相关 | 无 Gitee 需求可整体删除 Gitee step 与 `GITEE_TOKEN` 引用（`needs`/注释同步清理） |
| 10 | 告警敏感度 | `if: failure()` 对 needs 中**失败**触发、**跳过**不触发——这是设计语义，勿改成 `always()`（会把 skip 拦截也告警） |

## 6. 已验证行为速查（供复制后对照测试）

| 场景 | 预期行为 |
|---|---|
| 主项目 tag（skip=false） | 正常发布，成功后不告警 |
| 子包 tag（skip=true） | 跳过发布，不告警 |
| guard 失败（git 命令出错） | auto-release 跳过 + alert 触发（needs 含 guard）✓ |
| auto-release 任一步失败 | 重试耗尽 → alert 触发 |
| GITEE_TOKEN 未配置 | Gitee step Skipped，不告警 |
