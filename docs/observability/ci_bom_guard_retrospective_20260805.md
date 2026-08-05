# BOM 污染防复发技术复盘报告（L3 CI 防线接入）

- **报告日期**: 2026-08-05
- **落地提交**: `b12f82a6`（feat(ci): ci.yml 集成 BOM 污染监控第二道防线 + 接入草案与复发根因复盘）
- **前置提交**: `77181b25`（guard_bom_pollution.py + M8 巡检）、`ac93aa6b`（hook 重构）
- **状态**: ✅ 方案落地，CI 实跑验证（run 31019046160）
- **配套文档**:
  - 根因分析: [bom_pollution_recurrence_postmortem_20260805.md](../troubleshooting/bom_pollution_recurrence_postmortem_20260805.md)
  - CI 接入草案: [bom_pollution_ci_guard_draft_20260805.md](../ci_guidelines/bom_pollution_ci_guard_draft_20260805.md)
  - 回归测试报告: [regression_test_report_hook_refactor_20260805.md](./regression_test_report_hook_refactor_20260805.md)

---

## 1. 背景：BOM 污染复发事件

2026-08-05，`scripts/run_l3_regression_tests.ps1` 在**数小时内连续两次**被并行会话/自动提交脚本写入叠加 BOM（EF BB BF x3 + CRLF），破坏 PS 5.1 块注释解析（BLOCK 级编码契约异常）。

关键事实：`git restore` 修复后几分钟内再次复发 → 这是**持续写入行为**而非一次性事故，暴露本地 hook 防线可绕过（`--no-verify` / `SKIP_ENCODING_CHECK=1` / 直接写盘绕过 commit 链）。

### 污染指纹

| 维度 | HEAD（正确） | 工作区（污染） |
|---|---|---|
| 文件头 | `EF BB BF`（1 BOM）+ `<#` | `EF BB BF` ×3 + `<#` |
| 行尾 | LF | CRLF |
| 正文 | — | 与 HEAD 一致（纯编码重写，非内容修改） |

---

## 2. 根因分析（五问法摘要）

| 层级 | 结论 |
|---|---|
| 直接原因 | 写入方对 `.ps1` 整文件编码重写（UTF-8 with BOM + CRLF） |
| hook 失效链路 | 直接写盘 / `git commit --no-verify` / `SKIP_ENCODING_CHECK=1` → 本地防线完全绕过 |
| 深层原因 | ① hook 可绕过；② 无 CI 层自动检查，污染可静默数小时；③ 无受保护文件清单概念（全仓扫描无差异化盯防）；④ 多会话共享仓库无写入纪律 |
| 为什么 restore 后复发 | 只修文件状态，未改写入方行为（修复症状而非根因） |
| **根本原因** | **共享仓库的编码契约缺少 hook 之外、不可绕过的自动防线，且受保护文件没有显式清单与持续盯防** |

---

## 3. 防护方案：纵深防御（L1→L4）

| 层 | 机制 | 可被绕过? | 触发时机 | 状态 |
|---|---|---|---|---|
| L1 | pre-commit hook ENCODING_CHECK | 是（--no-verify / SKIP_*） | 本地 commit | 已有 |
| L2 | maintenance_check **M3**（全仓）+ **M8**（受保护清单） | 需人工运行 | 巡检 | 已有（77181b25） |
| L3 | **ci.yml code-quality BOM 监控 step** | 否（CI 强制） | 每次 push/PR/schedule | **本次落地（b12f82a6）** |
| L4 | guard_bom_pollution.py 独立脚本（--json） | 否 | CI/脚本按需消费 | 已有 |

设计要点：
- L3 是 hook 被绕过后的**最终自动兜底**，退出码非零即阻断合入；
- 受保护清单 `WATCH_DEFAULT = REQUIRE_BOM_DEFAULT + 历史污染点`（当前 2 项：`scripts/dev/hook_fail_safe.psm1` + `scripts/run_l3_regression_tests.ps1`），形成"污染点 → 永久盯防"闭环，可 `--watch` 追加。

---

## 4. CI 接入内容（b12f82a6）

### 4.1 变更文件

| 文件 | 变更 |
|---|---|
| `.github/workflows/ci.yml` | `code-quality` job 末尾新增阻塞式 step（9→10 step） |
| `docs/ci_guidelines/bom_pollution_ci_guard_draft_20260805.md` | CI 配置修改草案（新增） |
| `docs/troubleshooting/bom_pollution_recurrence_postmortem_20260805.md` | 根因分析技术复盘（新增） |

### 4.2 ci.yml 新增 step（示意）

```yaml
- name: BOM 污染监控（受保护文件第二道防线）
  # Why 阻塞: 并行会话/自动脚本可用 --no-verify 或 SKIP_ENCODING_CHECK=1
  #   绕过 pre-commit hook, 本地防线失效后 CI 必须兜底
  run: |
    echo "=== BOM 污染监控(受保护文件清单) ==="
    python scripts/guard_bom_pollution.py --repo-root "$GITHUB_WORKSPACE"
```

### 4.3 关键设计决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 接入位置 | code-quality job 附加 step | 复用 checkout + Python 环境，零额外开销；同类检查（workflow_run 守卫）同 job 先例 |
| 阻塞 or 非阻塞 | **阻塞** | 防线语义：受保护文件污染必须失败，不允许静默入库 |
| 依赖安装 | 无需 | 纯标准库 + `sys.path[0]=scripts/` 自动解析 `ps_bom_contract` |
| 运行环境 | ubuntu-latest | pathlib 纯 Python 跨平台 |

---

## 5. 验证结果

### 5.1 本地验证（提交前）

| 验证项 | 方法 | 结果 |
|---|---|---|
| ci.yml YAML 语法 | `yaml.safe_load` 解析 | ✅ 10 个 job，code-quality 共 10 step（BOM step 位于末尾） |
| 监控脚本正常态 | `python scripts/guard_bom_pollution.py --repo-root .` | ✅ exit 0，受保护文件 2 个 → 污染 0 |
| 监控脚本污染态 | 构造叠加 BOM x3 | ✅ exit 1 + `[BLOCK]` 行（前置回归报告已验证） |
| pre-commit 全链 | 提交 b12f82a6 时实跑 | ✅ 链接预检 0 失效 + 锚点回归 4/4 + 核心不变量 12/12 |
| pre-push | 推送 origin + gitee | ✅ 核心不变量 12/12，双远端推送成功 |

### 5.2 CI 实跑验证（run 31019046160）

| 维度 | 结果 |
|---|---|
| 触发 | push master（b12f82a6）自动触发，run 31019046160（2026-08-05 15:11 UTC） |
| code-quality job | ✅ **success**（13/13 step 全部通过，含 BOM step） |
| BOM 监控 step | ✅ **success**（受保护文件清单无污染，退出码 0） |
| 证据链接 | [job 92350676145](https://github.com/nzt47/security-tools/actions/runs/31019046160/job/92350676145) |

> 追踪命令: `gh run view 31019046160 --job 92350676145`（run 全量完成后可查看 step 日志）
> 说明: BOM 监控 step 为 code-quality job 最后一个 step，前置 10 个检查（格式/排序/类型/风格/守卫反模式）全绿后执行；纯标准库脚本，<1s 完成。

### 5.3 归档证据链

```
污染复发(2 次) → 根因复盘(postmortem) → guard_bom_pollution.py(77181b25)
  → M8 巡检接入 → 回归报告(hook 重构) → ci.yml L3 防线(b12f82a6) → 本报告
```

---

## 6. 归档学习要点

1. **hook 是"提高门槛"而非"绝对保障"**：凡有 `--no-verify` 逃生通道的防线，必须在 CI 侧有不可绕过的对等防线（L3 兜底）。
2. **git restore 修复 ≠ 问题解决**：持续写入方不识别并干预，同款污染必然复发——识别并治理写入方行为才是根因修复。
3. **污染指纹可复用**：叠加 BOM + CRLF + 正文不变 = 编码重写行为特征，后续巡检可按此模式快速甄别。
4. **受保护清单是廉价的记忆**：一次污染 → 加入清单 → 永久盯防，成本远低于反复修复（"污染点 → 盯防"闭环）。
5. **单一事实源防二次复制**：所有 BOM 判定统一走 `ps_bom_contract`，检查/修复/监控三脚本不复制实现，杜绝"修了 A 漏了 B"。

---

## 7. 后续建议

| 项 | 建议 | 优先级 |
|---|---|---|
| 1 | 并行会话写盘纪律：`scripts/`、`packages/` 下 `.ps1/.psm1` 写入统一经 `fix_ps_bom.py --apply` 归一化 | P1 |
| 2 | L3 失败时触发通知（复用 slack_notify.py 链路） | P2 |
| 3 | 新污染事件后把文件加入 `WATCH_DEFAULT`（防三犯） | P1 |
| 4 | CI 全量 run 稳定后，将本 step 从 code-quality 抽离为独立 job 不再必要（保持现状） | P3 |

---

*本报告由三次提交（ac93aa6b → 77181b25 → b12f82a6）构成完整防线闭环，配套证据文档见开头链接。*
