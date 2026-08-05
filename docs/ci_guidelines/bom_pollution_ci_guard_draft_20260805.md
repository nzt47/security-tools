# CI 配置修改草案：ci.yml 集成 BOM 污染监控步骤

- **日期**: 2026-08-05
- **状态**: 已实施（待 CI 实跑验证）
- **关联提交**: 本次 `ci.yml` 修改（与 guard_bom_pollution.py、复盘文档同批）
- **关联脚本**: `scripts/guard_bom_pollution.py`（2026-08-05 已提交 `77181b25`）

---

## 1. 变更概述

在 `.github/workflows/ci.yml` 的 `code-quality` job 末尾（`workflow_run 守卫反模式检查` 之后）新增一个**阻塞式**步骤 `BOM 污染监控（受保护文件第二道防线）`，复用现有 checkout + Python 环境直接运行 `scripts/guard_bom_pollution.py`。

### 变更 diff（示意）

```yaml
       - name: workflow_run 守卫反模式检查
         run: |
           echo "=== 检查 workflow_run 守卫反模式 ==="
           pip install pyyaml
           python scripts/lint_workflow_guard.py .github/workflows

+      - name: BOM 污染监控（受保护文件第二道防线）
+        # Why 阻塞: 并行会话/自动脚本可用 `git commit --no-verify` 绕过 hook
+        run: |
+          echo "=== BOM 污染监控(受保护文件清单) ==="
+          python scripts/guard_bom_pollution.py --repo-root "$GITHUB_WORKSPACE"
```

---

## 2. 设计决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 接入位置 | `code-quality` job 附加 step | 已有 checkout + setup-python + 依赖安装，零额外启动开销；同类检查（workflow_run 守卫）同 job 先例 |
| 独立 job vs step | step | 独立 job 需重复 checkout/setup（~1min 开销），BOM 检查 <1s，不值得 |
| 阻塞 or 非阻塞 | **阻塞**（退出码非零 → job 失败） | 防线语义：受保护文件污染必须失败，不允许静默入库 |
| 触发范围 | 继承 ci.yml 全部事件（push/PR/schedule） | 任意分支污染都该被抓，无需额外 `on:` |
| 依赖安装 | 无需 | guard_bom_pollution.py 纯标准库 + `import ps_bom_contract`（同目录，`python scripts/x.py` 时 sys.path[0]=scripts/ 自动解析） |
| 运行环境 | ubuntu-latest（code-quality 既定 runner） | 脚本跨平台（pathlib 纯 Python），无需 Windows |

---

## 3. 预期行为

### 成功路径（仓库健康）
```
=== BOM 污染监控(受保护文件清单) ===
---
受保护文件 2 个 → 污染 0
```
→ step 绿，job 继续。

### 失败路径（受保护文件被叠加 BOM / 缺 BOM / 非法 UTF-8）
```
=== BOM 污染监控(受保护文件清单) ===
[BLOCK] scripts/run_l3_regression_tests.ps1: 叠加 BOM x3 → x1 (head: EF BB BF EF BB BF EF BB)
---
受保护文件 2 个 → 污染 1
```
→ 退出码 1，step 红，`code-quality` job 失败，PR 无法合入 / push 阻断。

### 诊断定位
失败时 GitHub Actions 日志直接可见 `[BLOCK] <文件>: 原因` 行，定位到具体被污染文件后执行：

```bash
git restore <文件>                          # 恢复到 HEAD 的正确编码
# 或
python scripts/fix_ps_bom.py --apply --repo-root .   # 去叠加 BOM
```

---

## 4. 回滚方案

若 CI 出现误报（如受保护清单过严），回滚方式二选一：

1. **临时豁免**：移除/注释 `code-quality` 中的 BOM 监控 step，下个提交恢复。
2. **扩展清单**：若合法文件被误判，检查 `WATCH_DEFAULT` 是否误含该文件（当前仅 2 项：`scripts/dev/hook_fail_safe.psm1` + `scripts/run_l3_regression_tests.ps1`）。

> 注意：`guard_bom_pollution.py` 自身已在本地与 maintenance_check M8 双态验证（正常 exit 0 / 污染 exit 1），误报面极小。

---

## 5. 验证计划

| 项 | 方式 | 预期 |
|---|---|---|
| 语法校验 | 推送后观察 GitHub Actions | `code-quality` job 绿，BOM 监控 step 显示污染 0 |
| 防复发验证 | 人工在分支构造叠加 BOM 后推送 | `code-quality` 失败，日志含 `[BLOCK]` 行 |
| 回归无副作用 | 既有 CI 全量检查 | 其余 step/job 不受影响（step 独立、无 env 变更） |

---

## 6. 防线层次（现状全景）

| 层 | 机制 | 可被绕过? | 触发时机 |
|---|---|---|---|
| L1 | pre-commit hook ENCODING_CHECK（本地） | 是（`--no-verify`/`SKIP_ENCODING_CHECK=1`） | 本地 commit |
| L2 | maintenance_check M3 全仓 + M8 受保护清单 | 需人工运行 | 巡检 |
| L3 | **ci.yml BOM 监控 step（本次新增）** | 否（CI 侧强制） | 每次 push/PR/schedule |

---

*配套文档: [BOM 污染复发根因分析技术复盘](../troubleshooting/bom_pollution_recurrence_postmortem_20260805.md)*
