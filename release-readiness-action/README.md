# Release Readiness Action

发布就绪检查 GitHub Action — 将 [RELEASE_PROCESS_TEMPLATE.md](./RELEASE_PROCESS_TEMPLATE.md) §8 检查清单自动化。

其他项目可直接引用，作为发布前的强制卡点：**BLOCK 项 → 退出码 1 → job 失败 → 阻断下游发布流程**。

## 特性

- 10 项检查覆盖模板 §8 全量清单：工作树 / master 同步 / RELEASE_NOTES 章节 / 本地+远程 tag / Release 状态 / Release 事件 CI / 归档清单 / 流程模板 / 临时文件
- 三态分级：PASS / WARN（记录风险不阻断）/ BLOCK（强制阻断）
- CI 与本地同一脚本（`GITHUB_WORKSPACE` 自动定位调用方仓库根）
- `--json` 输出供上层消费，`--quiet` 仅显示 BLOCK 明细

## 快速开始

```yaml
jobs:
  pre-release-gate:
    runs-on: ubuntu-latest
    steps:
      # 调用方必须先检出代码（action 检查的是调用方仓库状态）
      - uses: actions/checkout@v6
      - uses: your-org/release-readiness-action@v1
        with:
          version: ${{ github.ref_name }}   # release 事件下 = tag 名
          remote: origin,gitee               # 可选，默认 origin
```

## Inputs

| 参数 | 必选 | 默认 | 说明 |
|---|---|---|---|
| `version` | ✅ | — | 版本号 / tag 名（如 `v1.5.0`） |
| `remote` | — | `origin` | 远程列表（逗号分隔），多远程推送校验用 |

## Outputs

| 输出 | 说明 |
|---|---|
| `status` | 最终状态：`PASS`（无 BLOCK）或 `BLOCK` |

## 本地运行（无 GitHub Actions 环境）

```bash
python verify_release_readiness.py --version v1.5.0 --remote origin,gitee
```

## 与发布流程模板配合

1. 按 [RELEASE_PROCESS_TEMPLATE.md](./RELEASE_PROCESS_TEMPLATE.md) 执行 1-7 阶段
2. 本 Action 自动校验 §8 检查清单（发布前强制卡点）
3. 参考实例：`nzt47/security-tools` v1.5.0-bm25-normalization（`RELEASE_V150_FINAL_ARCHIVE.md`）

## 发布为独立仓库（可选）

将本目录推送到独立仓库后，即可被其他项目通过 `uses: <owner>/<repo>@v1` 引用：

```bash
git init && git add . && git commit -m "feat: release readiness action"
git push origin master
# 打 tag 后可固定引用版本
```
