# Skills Check Workflow 真实触发验证报告

> 验证目的：确认 `fix/ci-scan-optimization` 分支优化后的 Skills Check workflow
> 在真实 GitHub Actions 环境（workflow_dispatch 触发）下全部通过，且扫描报告
> artifact 正常上传。

## 1. 验证基本信息

| 项 | 值 |
|---|---|
| 仓库 | `nzt47/security-tools` |
| 分支 | `fix/ci-scan-optimization`（@ `63cc01fc`） |
| 触发方式 | workflow_dispatch（手动触发，`gh workflow run "Skills Check" --ref fix/ci-scan-optimization`） |
| Run ID | **32652513646** |
| Run 结论 | ✅ **success** |
| Run 链接 | https://github.com/nzt47/security-tools/actions/runs/32652513646 |

## 2. Job 结果明细

| Job | 耗时 | 结论 | 关键步骤 |
|---|---|---|---|
| 定期全量扫描 | 25s | ✅ success | 一致性验证 ✓ / 动态加载风险扫描 ✓ / **上传扫描报告 ✓** / 单元测试 ✓ |
| 新旧格式元数据一致性 | 13s | ✅ success | 新旧格式元数据对比 ✓ / 三层架构加载+执行验证 ✓ |
| 动态加载风险阻断 (HIGH) | 0s | ⏭️ skipped | workflow_dispatch 触发不满足 `push master` 条件（预期） |
| Skills Gate (汇总门禁) | 34s | ✅ success | 参数组合行为测试 ✓ / 检查 Branch Protection 状态 ✓ / AdminDependencyChecker 41 例 ✓ |

## 3. Artifact 报告

| 项 | 值 |
|---|---|
| Artifact 名称 | `dynamic-load-scan-32652513646` |
| Artifact ID | 9496579563 |
| 大小 | 4,087 字节（未过期） |
| 下载 | https://github.com/nzt47/security-tools/actions/runs/32652513646/artifacts/9496579563 |

**报告内容摘要**（已下载校验，JSON 可解析）：

```
scanned_files   = 1710
high_risk_count = 4
medium_risk_count = 97
low_risk_count  = 20
```

**HIGH 明细（4 处，均为受控归档加载，dirname 表达式无法静态验证，保守保留）：**

| 文件 | 行 | 模式 |
|---|---|---|
| `scripts/cicd_pipeline.py` | 32-33 | `spec_from_file_location` / `module_from_spec`（加载 `docs/archive/agent_tests_20260810/test_tool_router.py`） |
| `scripts/stress_test_pipeline.py` | 47-48 | 同上 |

**降级验证**：`scripts/dst_scenario_demo.py`（PR #407 引入）的 `spec_from_file_location` 加载仓库内固定文件，已由受控路径降级规则 HIGH→MEDIUM，**不再出现在 HIGH 中**（优化 2 生效）。

## 4. 修复项验证矩阵

| 修复项 | 验证方式 | 结果 |
|---|---|---|
| 优化 1a：detect 步骤 `continue-on-error: true` | 真实 CI：HIGH 4 处存在时 job 继续执行后续步骤 | ✅ 生效 |
| 优化 1b：上传步骤 `if: always()` | 真实 CI：报告 artifact 正常上传 | ✅ 生效 |
| 优化 2：受控路径降级（字符串常量 + 仓库内文件） | 真实 CI：`dst_scenario_demo` HIGH→MEDIUM | ✅ 生效 |
| 日志（`DETECT_LOG_LEVEL` 控制 stderr 诊断） | 本地：DEBUG 显示判定过程，不污染 `--json` stdout | ✅ 生效 |
| ROOT 边界修复（`--root` 子目录扫描） | 本地：`--root scripts` 下 `dst_scenario_demo` 判定 `exists=True` | ✅ 生效 |
| Windows JSON 编码修复 | 本地：`--json` 报告可跨平台解析 | ✅ 生效 |
| pytest-asyncio 依赖补齐 | 真实 CI：单元测试步骤通过（此前 `Unknown config option: asyncio_mode` exit 4） | ✅ 生效 |
| 403 降级修复（rollback-protection.ps1） | 真实 CI：参数组合行为测试 9 例 + 检查 Branch Protection 状态通过 | ✅ 生效 |

## 5. 验证过程发现并修复的预存问题

真实触发暴露 2 个 **master 预存失败**（与本次扫描优化无关，但会阻断 CI）：

1. **单元测试步骤 exit 4**：`pytest.ini` 配置 `asyncio_mode = auto`（pytest-asyncio 插件选项），CI 依赖缺 `pytest-asyncio` → 已补依赖。
2. **参数组合行为测试 1/2/3 失败**：master 的 `rollback-protection.ps1` 无 403 降级（原属 PR #41 修复，未合并），`status` 调 gh api 403 抛异常 → 已合入 PR #41 的 403 修复（403 catch + 显式 `exit 0` + transcript 保留）。

## 6. 前置失败对照（同分支第 1 次触发，run 32652260823）

| 项 | 结果 |
|---|---|
| 动态加载风险扫描 + 上传 | ✅ 已生效（continue-on-error + if: always()） |
| 单元测试 | ❌ `asyncio_mode` 配置错误（已修复） |
| 参数组合行为测试 | ❌ 403（已修复） |

第 2 次触发（32652513646）后 4 个 job 全部 success。

## 7. 结论

- 优化后 Skills Check workflow 在真实 CI 环境全绿。
- 扫描报告 artifact 稳定上传（此前发现 HIGH 时报告永久丢失，现已修复）。
- 本分支已覆盖 PR #41（fix/ci-skills-check-403）的 403 修复内容，**建议直接合并本分支，避免与 PR #41 重复改动冲突**。

## 8. 复现命令

```bash
# 触发（任意分支）
gh workflow run "Skills Check" --ref fix/ci-scan-optimization

# 查看结果
gh run watch <run_id>

# 下载报告 artifact
gh run download <run_id> -n dynamic-load-scan-<run_id> -D <dir>
```
