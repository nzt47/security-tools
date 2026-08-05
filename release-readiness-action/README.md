# Release Readiness Action

发布就绪检查 GitHub Action — 将 [RELEASE_PROCESS_TEMPLATE.md](./RELEASE_PROCESS_TEMPLATE.md) §8 检查清单自动化。

其他项目可直接引用，作为发布前的强制卡点：**BLOCK 项 → 退出码 1 → 步骤失败 → job 失败 → 阻断下游发布流程**。

## 特性

- **10 项检查** 覆盖模板 §8 全量清单：工作树 / master 同步 / RELEASE_NOTES 章节 / 本地+远程 tag / Release 状态 / Release 事件 CI / 归档清单 / 流程模板 / 临时文件
- **三态分级**：PASS / WARN（记录风险不阻断）/ BLOCK（强制阻断，退出码 1）
- **CI 与本地同一脚本**：`GITHUB_WORKSPACE` 自动定位调用方仓库根，本地运行回退脚本父目录
- **退出码显式传播**：卡点失败即 job 失败，无需额外 `if: failure()` 判断
- `--json` 输出供上层消费，`--quiet` 仅显示 BLOCK 明细

## Inputs 参数说明

| 参数 | 必选 | 默认 | 类型 | 说明 |
|---|---|---|---|---|
| `version` | ✅ | — | string | 待发布版本号 / tag 名（如 `v1.5.0`）。release 或 push tag 事件下传 `${{ github.ref_name }}`，手动触发传具体版本号 |
| `remote` | — | `origin` | string | 远程列表（逗号分隔），校验 tag 是否已推送。单远程保持默认；双远程发布（如 origin + gitee）传 `origin,gitee` |

> 注意：`remote` 中列出的远程必须在调用方仓库已配置，否则对应 tag 校验会以 WARN 提示（不阻断）。

## Outputs

| 输出 | 值 | 说明 |
|---|---|---|
| `status` | `PASS` / `BLOCK` | `PASS` = 无 BLOCK 项；`BLOCK` = 存在阻断项（此时 job 已失败） |

## 使用示例

### 示例 1：发布前强制卡点（最小用法）

```yaml
on:
  release:
    types: [published]

jobs:
  pre-release-gate:
    runs-on: ubuntu-latest
    steps:
      # 调用方必须先检出代码（action 检查的是调用方仓库状态）
      - uses: actions/checkout@v6
      - uses: your-org/release-readiness-action@v1
        with:
          version: ${{ github.ref_name }}   # release 事件下 = tag 名
```

### 示例 2：阻断下游发布流程（needs 依赖）

`readiness-check` job 失败 → 下游 `publish` job 自动不执行（BLOCK 项即发布阻断）：

```yaml
jobs:
  readiness-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v6
      - uses: your-org/release-readiness-action@v1
        with:
          version: ${{ github.ref_name }}

  publish:
    needs: readiness-check          # 卡点通过才发布
    runs-on: ubuntu-latest
    steps:
      - run: echo "执行发布..."
```

### 示例 3：双远程发布校验 + 输出消费

```yaml
jobs:
  readiness-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v6
      - id: check
        uses: your-org/release-readiness-action@v1
        with:
          version: ${{ github.ref_name }}
          remote: origin,gitee
      - name: 打印卡点结果
        if: always()
        run: |
          echo "发布就绪状态: ${{ steps.check.outputs.status }}"
```

### 示例 4：手动触发跳过卡点（调试用）

`workflow_dispatch` 仅用于调试时不阻断（与 `release-docs.yml` 同模式）：

```yaml
- name: 运行发布就绪检查
  if: github.event_name != 'workflow_dispatch'
  uses: your-org/release-readiness-action@v1
  with:
    version: ${{ inputs.version }}
```

### 示例 5：本地运行（无 GitHub Actions 环境）

```bash
# 在调用方仓库根目录运行（自动定位仓库根）
python path/to/verify_release_readiness.py --version v1.5.0 --remote origin,gitee

# 仅显示 BLOCK 明细（CI 日志精简）
python path/to/verify_release_readiness.py --version v1.5.0 --quiet

# JSON 输出（供脚本消费）
python path/to/verify_release_readiness.py --version v1.5.0 --json
```

## 检查项说明（10 项）

| # | 检查项 | 判定 | 阻断 |
|---|---|---|---|
| 1 | 工作树干净 | `git status --porcelain` 为空 | **BLOCK** |
| 2 | master 与 origin 同步 | ahead/behind = 0 | WARN |
| 3 | RELEASE_NOTES 含 `## {version}` 章节 | 文件存在且章节存在 | **BLOCK** |
| 4 | 本地 tag 存在 | `refs/tags/{version}` 可解析 | **BLOCK** |
| 5 | tag 已推送至 `remote` | `ls-remote` 命中 | WARN |
| 6 | GitHub Release 已创建 | 非 draft / 非 prerelease | WARN |
| 7 | Release 事件 CI `conclusion=success` | `gh run list` 最近一次 | WARN |
| 8 | 归档清单存在 | `RELEASE_*ARCHIVE*.md` | **BLOCK** |
| 9 | 流程模板存在 | `RELEASE_PROCESS_TEMPLATE.md` | WARN |
| 10 | 临时文件无残留 | `*.tmp` / `.commit_msg_*` 等 | WARN |

> 检查 6/7 依赖 `gh` CLI 认证；CI 未配置 `GH_TOKEN` 时自动降级 WARN，不误阻断。

## 与发布流程模板配合

1. 按 [RELEASE_PROCESS_TEMPLATE.md](./RELEASE_PROCESS_TEMPLATE.md) 执行 1-7 阶段
2. 本 Action 自动校验 §8 检查清单（发布前强制卡点）
3. 参考实例：`nzt47/security-tools` v1.5.0-bm25-normalization（`RELEASE_V150_FINAL_ARCHIVE.md`）

## 发布为独立仓库（可选）

将本目录推送到独立仓库后，即可被其他项目通过 `uses: <owner>/<repo>@v1` 引用：

```bash
git init && git add . && git commit -m "feat: release readiness action"
git push origin master
# 打 tag 后可固定引用版本（如 v1.0.0）
```
