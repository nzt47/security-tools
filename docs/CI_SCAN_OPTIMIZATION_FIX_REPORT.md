# CI 扫描优化修复说明（Skills Check 定期全量扫描）

## 1. 背景

Skills Check workflow（`.github/workflows/skills-check.yml`）的「定期全量扫描」job 在 `workflow_dispatch` / `schedule` 触发下持续失败：

```
定期全量扫描 → 动态加载风险扫描 (JSON 报告) → Process completed with exit code 1
```

失败原因：`scripts/detect_dynamic_loads.py` 在 `--json` 模式下扫描到 HIGH 风险动态加载时按设计返回退出码 1（`return 1 if report.high_risk else 0`），且该 job 未设置 `continue-on-error`，导致整个 job 失败；上传步骤无 `if: always()`，报告也随之下传失败，无法拿到排查证据。

## 2. 根因分析

### 2.1 HIGH 风险来源（`dst_scenario_demo.py`，PR #407 引入）

`scripts/dst_scenario_demo.py:22-25`（DST 指代消解演示脚本）通过 `spec_from_file_location` / `module_from_spec` 加载仓库内固定文件，绕过 `agent.orchestrator` 循环导入：

```python
# 【不易】dialog_state 仅依赖标准库，用 importlib 直接加载模块文件，
#         绕过 agent.orchestrator.__init__ 的循环导入（lifecycle_manager↔digital_life）
_spec = importlib.util.spec_from_file_location(
    "dialog_state", "agent/orchestrator/dialog_state.py")
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
```

- 目标路径是**代码内字符串常量**，指向仓库内已存在文件，参数不可被外部控制 → 属于**受控动态加载**，检测器按模式匹配误判为 HIGH。
- 该文件为非测试脚本（`in_test=False`），不触发既有「测试代码 HIGH 降级 MEDIUM」规则 → 误报阻断 CI。

### 2.2 同类受控加载（master 已存在，保守保持 HIGH）

| 文件 | 行号 | 目标 | 说明 |
|---|---|---|---|
| `scripts/cicd_pipeline.py` | 32-33 | `ARCHIVED_TOOL_ROUTER` → `docs/archive/agent_tests_20260810/test_tool_router.py` | 路径由 `os.path.join(os.path.dirname(...))` 表达式拼接，静态无法折叠验证 |
| `scripts/stress_test_pipeline.py` | 47-48 | `_ARCHIVED_TOOL_ROUTER`（同上） | 同上 |

这两处虽同为受控归档加载，但路径为 `dirname/abspath` 表达式，**静态识别不可行**（不做非常量路径降级，守住安全边界），由优化 1 兜底（job 不失败 + 报告照常上传）。

### 2.3 分支重建背景

本次排查还确认：`fix/ci-skills-check-403` 分支曾因含 PR #407 的基础重建为 `7bbb3277`（区别于本地验证过的 `e3c83f16`），导致 CI 检出的代码扫描出 HIGH。此为排查干扰项，非本优化范围。

## 3. 本次改动

### 3.1 优化 1：`skills-check.yml` — 定期全量扫描 job 职责修正

「定期全量扫描」job 的职责是**扫描 + 上传报告供人工排查**，HIGH 阻断职责专属 `dynamic-load-gate`（push master 门禁）。本次修正：

```yaml
      - name: 动态加载风险扫描 (JSON 报告)
        # [变易] HIGH 风险不阻断本 job: 本 job 职责是"扫描 + 上传报告"供人工排查,
        # HIGH 阻断职责专属 dynamic-load-gate (push master 时门禁).
        # 否则发现 HIGH 时报告永远不会生成/上传, 失去排查依据.
        continue-on-error: true
        run: python scripts/detect_dynamic_loads.py --json > dynamic_loads_report.json

      - name: 上传扫描报告
        # [不易] 报告必须总是上传: 无论扫描是否发现 HIGH, 都保留证据供人工审查.
        if: always()
        uses: actions/upload-artifact@v7
        with:
          name: dynamic-load-scan-${{ github.run_id }}
          path: dynamic_loads_report.json
          retention-days: 30
```

**效果**：
- 扫描到 HIGH 时 job 不再失败，报告必定上传（`if: always()`），人工可下载排查。
- `dynamic-load-gate`（push master）仍按 `continue-on-error: false` 阻断合并，门禁语义不变。

### 3.2 优化 2：`detect_dynamic_loads.py` — 受控动态加载降级

在 `DynamicLoadVisitor` 增加「受控路径降级」：当 HIGH 模式（`spec_from_file_location` / `load_source`）的加载路径为**字符串常量且指向仓库内已存在文件**时，降级为 MEDIUM。核心逻辑：

```python
# visit_Call 匹配到 HIGH 模式后:
if risk == "HIGH" and self._is_controlled_spec_load(node, pattern):
    self._controlled_files.add(self._rel_path())   # 记录受控文件
    risk = "MEDIUM"
elif pattern == "module_from_spec" and self._rel_path() in self._controlled_files:
    risk = "MEDIUM"                                 # 成对的 module_from_spec 跟随降级

def _is_controlled_spec_load(self, node: ast.Call, pattern: str) -> bool:
    # 提取路径参数: spec_from_file_location 的位置参数 args[1] 或关键字 location
    #           load_source 的位置参数 args[1] (pathname)
    # 判定: 参数为字符串常量 + 相对仓库根解析后是真实存在的文件 → 受控
    # 安全边界: 绝对路径 / 非常量路径 (可能被外部控制) 一律不降级, 保持 HIGH
```

**安全边界（不易）**：
- 仅放宽「路径为代码常量且指向仓库内已有文件」的场景。
- 绝对路径、变量/表达式路径（可能被外部控制）保持 HIGH。
- `module_from_spec` 单独出现（无受控 `spec_from_file_location` 前置）保持 HIGH。

## 4. 验证结果

在 `fix/ci-scan-optimization` 分支（基于 `origin/master` f8c6a03a，含 `dst_scenario_demo.py`）本地验证：

| 项 | 优化前 | 优化后 |
|---|---|---|
| `dst_scenario_demo.py:22,24` | HIGH | **MEDIUM**（降级生效） |
| HIGH 总数 | 6（master 基础） | 4（仅 `cicd_pipeline.py` / `stress_test_pipeline.py` 受控归档加载，保守保留） |
| 退出码（有 HIGH 时） | 1 | 1（由优化 1 的 `continue-on-error` 兜底，job 不失败） |

## 5. 验证建议（PR 合并后）

1. `workflow_dispatch` 触发 Skills Check，确认「定期全量扫描」job **success** 且 artifact `dynamic-load-scan-<run_id>` 正常上传。
2. 下载报告确认 HIGH 4 处（归档加载）与 MEDIUM 均被记录。
3. push 含 HIGH 动态加载的代码到 master，确认 `dynamic-load-gate` 仍阻断（门禁未放松）。

## 6. 后续建议（可选，不在本 PR 范围）

- 归档测试类加载（`cicd_pipeline.py` / `stress_test_pipeline.py`）若需彻底消除误报，可在检测器增加「路径指向 `docs/archive/` 目录则视为受控」规则（与 `EXCLUDE_DIRS` 排除 archive 语义一致）。
- Gitleaks workflow（`hardcoded-password-scan.yml`）存在独立配置问题（artifact 名含 `/`、PR 评论权限不足），建议单独 PR 修复。

## 7. 回滚方案

- 仅回滚优化 1：删除 `skills-check.yml` 中 `continue-on-error: true` 与 `if: always()` 两行。
- 仅回滚优化 2：移除 `detect_dynamic_loads.py` 中 `_controlled_files` / `_rel_path` / `_is_controlled_spec_load` 及 `visit_Call` 中降级分支。

## 8. 变更文件清单

| 文件 | 改动 |
|---|---|
| `.github/workflows/skills-check.yml` | 定期全量扫描：detect 步骤加 `continue-on-error: true`；上传步骤加 `if: always()` |
| `scripts/detect_dynamic_loads.py` | 新增受控路径降级（字符串常量 + 仓库内文件）；`module_from_spec` 跟随降级 |
| `docs/CI_SCAN_OPTIMIZATION_FIX_REPORT.md` | 本说明文档 |
