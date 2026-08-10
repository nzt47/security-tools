# Bug 追踪单 — 覆盖率 Artifact 链路失败（覆盖率分析）

> 追踪单 ID：BUG-20260809-002
> 创建日期：2026-08-09 ｜ 状态：**根治已实施，CI 验证中（2026-08-09 更新）**
> 来源：PR #77 结案 CI 复查中发现（master 33136c19 覆盖率分析 job 失败）

## 一、Bug 概要

| 项 | 值 |
|----|----|
| 失败 job | `覆盖率分析`（check-run `93356560620`，run `31355337983`） |
| 影响分支 | master（`33136c19` 及此前多个 commit） |
| 严重度 | 中（CI 覆盖率门禁失败，非功能缺陷） |
| 类型 | CI 流水线 artifact 链路问题 |
| 与 #77 / singleton 缺陷 | **无关**（独立遗留问题） |

## 二、失败详情（annotations 提取）

```
1. No files were found with the provided path: coverage-data/. No artifacts will be uploaded.
   （.github:16 — 覆盖率 artifact 目录为空/未生成）

2. Unable to download artifact(s): Artifact not found for name: coverage-report-sqlite-vec
   （.github:15 — combine/分析阶段下载 artifact 失败）
```

## 三、根因链（已由并行会话定位，详见关联报告）

**核心链条**：run 块 shell 默认 `set -e`，任何一步非零退出立即中止 → pytest 失败/无测试收集时后续 `mv .coverage` 不执行 → 覆盖率数据无法改名上传 → 分析阶段找不到 artifact → 失败。

| 子问题 | 根因 | 状态 |
|--------|------|------|
| omit 配置未生效 | `.data` 存绝对路径，`tests/*` 前缀 fnmatch 不匹配 | 修复方案已给出（`*/tests/*`） |
| Shard 串行段 exit 5 | 无 serial 测试的 shard 收集 0 个 → exit 5 → 中止 | 修复方案已给出（容错） |
| 性能测试 flake | `test_singleton_performance.py` 断言阈值 | 修复方案已给出（放宽/标记） |
| **artifact 未上传** | `set -e` 中止跳过 `mv` → 上传空跑 | 修复方案已给出（mv 容错 / `if: always()`） |

## 四、关联文档与任务

- **根因详查报告**：[shard_coverage_artifact_and_omit_rootcause_20260809.md](../../troubleshooting/shard_coverage_artifact_and_omit_rootcause_20260809.md)（并行会话，三线并查，根因已全部定位）
- **并行会话迭代任务**：并行会话持续推送覆盖率修复 commit（如 run `31357042906`：`fix(ci): 修复 performance 测试 import 副作用全局禁用日志致 Shard 4 串行段 10 失败`），**根治方案待实施**
- 状态判定：**修复中**（根因已定位，方案待落地，需并行会话在后续 commit 实施并验证）

## 五、验证/关闭条件

1. master 最新 head 上 `覆盖率分析` job 通过（无 artifact 缺失 annotation）
2. 全项目测试覆盖率 Shard 1-6 全部 success
3. 复现确认方式：`gh api repos/nzt47/security-tools/commits/<sha>/check-runs` 查看覆盖率相关 job

## 六、附注

- 33136c19 上单元测试矩阵 24/24 全绿（含 singleton 缺陷修复验证），覆盖率失败为独立链路问题
- 该问题不影响 PR #77 结案与本仓库其它功能交付
