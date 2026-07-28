# Issue 草稿：清理 network_config.py 历史类型错误以启用 mypy 阻塞检查

> **状态**：草稿（待 `gh issue create` 创建）
> **拟议标签**：`tech-debt`, `type-safety`, `ci-hardening`
> **拟议里程碑**：v1.3.0
> **来源**：v1.2.1-fix-secure-manager-return 后续改进项
> **跟踪文档**：[`docs/dashboards/ci_health_dashboard.md`](file:///c:/Users/Administrator/agent/docs/dashboards/ci_health_dashboard.md) 第四节

---

## 标题

`[tech-debt] 清理 agent/network_config.py 的 29 个 mypy 类型错误，转为 CI 阻塞检查`

## 正文

### 背景

v1.2.1 修复 `get_env_config_manager()` 缺少 `return` 导致 9 个 CI 测试失败后，为预防同类 P1 故障重演，将 `env_config_manager.py` 与 `network_config.py` 纳入 mypy `--warn-no-return --warn-return-any` 严格检查范围。

`env_config_manager.py` 已清零历史类型债，CI 阻塞检查零误报生效。但 `network_config.py` 当前存在 **29 个历史类型错误**，直接阻塞会破坏 CI 流水线，故暂时以 `|| true` 非阻塞方式运行（见 [`.github/workflows/ci.yml`](file:///c:/Users/Administrator/agent/.github/workflows/ci.yml#L71-L76) TODO）。

本 Issue 跟踪这 29 个错误的清理工作，目标：**清零后去掉 `|| true`，转为阻塞**。

### 错误分类清单（29 个）

| 错误类型 | 错误码 | 数量 | 修复难度 | 修复策略 |
|----------|--------|------|----------|----------|
| 隐式 Optional (`def f(x: str = None)`) | `[assignment]` | 4 | 低 | 改为 `str \| None = None` |
| 变量先 `= None` 后赋 dict | `[assignment]` | 6 | 中 | 补类型标注或重构初始化路径 |
| None 不可索引/赋值 | `[index]` | 5 | 中 | 同上，根因相同 |
| 返回值类型不匹配 | `[return-value]` | 3 | 中 | 函数声明 `dict` 实际返回 `dict \| None`，收紧返回类型 |
| Returning Any | `[no-any-return]` | 5 | 低 | 加 `cast()` 或修正返回类型 |
| 参数类型不匹配 | `[arg-type]` | 4 | 中 | `object` → `str` 收紧 |
| 缺少类型标注 | `[var-annotated]` | 1 | 低 | `updates: dict = {}` |
| 属性不存在 | `[attr-defined]` | 1 | 低 | `object.startswith` 需 type narrow |

**错误位置**（按行号）：`176, 231, 232, 236, 241, 321, 337, 353, 364, 365, 366, 679, 691, 774, 930, 931, 975, 1006, 1042, 1044, 1052, 1184, 1212, 1259, 1402`（部分行含多个错误）。

### 验收标准

- [ ] 29 个 mypy 错误全部清零
- [ ] `mypy agent/network_config.py --warn-no-return --warn-return-any --ignore-missing-imports --follow-imports=silent` 退出码为 0
- [ ] `.github/workflows/ci.yml` 移除 `|| true`，转为阻塞
- [ ] `pyproject.toml` 注释更新（移除"非阻塞"说明）
- [ ] `docs/dashboards/ci_health_dashboard.md` 第四节"阻塞项与技术债跟踪"表中 network_config 行状态更新为 ✅ 阻塞
- [ ] 全量回归测试通过（`SKILLS_OFFLINE=1 pytest tests/unit/test_network_config.py tests/unit/test_network_config_save_regression.py`）
- [ ] PR 包含 before/after mypy 输出对比

### 修复原则（三义约束）

- **【不易】** 不改变运行时行为——仅类型标注调整，禁止借机重构业务逻辑
- **【变易】** 优先低难度项（隐式 Optional / Returning Any / var-annotated 共 10 个）建立信心，再处理中难度项
- **【简易】** 每个错误最小修复，不引入 `# type: ignore`（除非有明确注释说明不可修复原因）

### 风险

| 风险 | 缓解 |
|------|------|
| 类型标注收紧可能暴露隐藏 bug | 这是收益而非风险，发现即修复 |
| `cast()` 滥用掩盖真实问题 | 优先 type narrow，`cast()` 仅用于第三方库返回 Any |
| 大范围改动影响测试 | 每批修复后跑回归测试，分多个 PR 提交 |

### 预计工作量

- 低难度 10 个：~1 小时
- 中难度 19 个：~3-4 小时
- 验证 + PR review：~1 小时
- **合计**：~5-6 小时，建议拆分为 2-3 个 PR

### 相关文档

- 看板跟踪：[`docs/dashboards/ci_health_dashboard.md`](file:///c:/Users/Administrator/agent/docs/dashboards/ci_health_dashboard.md)
- v1.2.1 修复审查：[`docs/reviews/env_config_manager_return_fix_review.md`](file:///c:/Users/Administrator/agent/docs/reviews/env_config_manager_return_fix_review.md)
- mypy 配置：[`pyproject.toml`](file:///c:/Users/Administrator/agent/pyproject.toml#L131-L148) 第 131-148 行

---

## 创建命令（草稿稳定后执行）

```bash
gh issue create \
  --title "[tech-debt] 清理 agent/network_config.py 的 29 个 mypy 类型错误，转为 CI 阻塞检查" \
  --body-file docs/proposals/network_config_type_debt_cleanup.md \
  --label "tech-debt,type-safety,ci-hardening"
```

> 若标签不存在，先创建：`gh label create tech-debt --color FBCA04 && gh label create type-safety --color 0E8A16 && gh label create ci-hardening --color B60205`
