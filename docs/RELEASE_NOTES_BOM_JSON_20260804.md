# 发布说明：BOM 诊断 + JSON 日志输出功能线

**发布日期**: 2026-08-04
**状态**: ✅ 已完成
**入库提交**: `03b929e3`（主功能）、`a2458b58`（发布链路补齐）
**关联文档**: `docs/ci_guidelines/git_hook_bom_guide.md`

## 变更摘要

1. **BOM 诊断（`-BomDiag`）**：CI 预检拦截失败时输出 BOM/路径/锚点剥离诊断，与本地 `TLM_HOOK_VERBOSE=1` 同源，解决线上难以定位的编码类失败。
2. **JSON 结构化日志（`-JsonOutput`）**：hook 日志输出单行 JSON（`{"ts","level","event","msg","data"}`），供 ELK/Filebeat 采集；`msg` 保留 `[BROKEN]/[BLOCK]/[OK]` 文本标记，回归断言兼容。
3. **统一日志入口**：`git_precommit_check.ps1` 与 `precheck_docs.ps1` 新增同构 `Write-Log` 函数；修复空 `$ForegroundColor` 触发 `Write-Host` 参数绑定警告的缺陷。
4. **CI 集成**：`ci.yml` 文档链接预检 step 改用 `git_precommit_check.ps1 -BomDiag -JsonOutput`。

## 验证结果

| 检查项 | 结果 |
|--------|------|
| 完整预检（`-TargetRepo .`） | ✅ `[OK] 预检通过`，exit 0 |
| 锚点回归 | ✅ 4/4 通过 |
| 核心不变量（12 项） | ✅ 12/12（pre-push 校验） |
| 链接检查 | ✅ 685 文件 / 598 链接 / 0 失效 |
| Windows CI 预检 job | ✅ success |

## 影响范围

- 仅影响 **pre-commit / CI 链接预检 step** 的日志输出与失败诊断方式，回归断言逻辑不变。
- 与更新日志（`99996dba`）、技术备注（`910cfa00`）等 docs 提交无冲突、无依赖。

## 遗留问题

- `CI 失败通知` workflow 引用的 `visiblelabs/dingtalk-action` 解析失败（`repository not found`）——需替换为可用 Action，待后续跟进。
