# CI 健康度看板

> **用途**：追踪 CI 流水线回归测试通过率与耗时趋势，识别长期退化项与技术债。
>
> **更新频率**：每次合入 main 分支后由 CI 自动追加一行（人工补填亦可）。
>
> **维护人**：研发团队 owner（轮值）。
>
> **数据来源**：GitHub Actions `单元测试 (Python 3.10)` job 的 `test-results-unit-py3.10` artifact + `junit.xml`。

---

## 一、关键指标定义

| 指标 | 定义 | 健康阈值 | 告警阈值 |
|------|------|----------|----------|
| 通过率 (Pass Rate) | `passed / (passed + failed + errors)` | ≥ 99% | < 95% |
| 单测总耗时 (Total Duration) | junit.xml `<testsuite time="...">` 累计 | ≤ 300s | > 600s |
| 失败用例数 (Failures) | `failed + errors` | 0 | ≥ 1 |
| 跳过用例数 (Skipped) | `skipped` | ≤ 总数 5% | > 总数 10% |
| mypy 阻塞模块数 | env_config_manager + network_config | 0（全绿） | ≥ 1 |
| 覆盖率 (Coverage) | `coverage.xml` line coverage | ≥ 70% | < 40% |

---

## 二、合入趋势记录

> 每次合入 main 追加一行。**Trend** 列：`↑` 改善 / `→` 持平 / `↓` 退化。

| 日期 | Commit (短) | 通过率 | 失败 | 跳过 | 耗时(s) | 覆盖率 | mypy阻塞 | Trend | 备注 |
|------|-------------|--------|------|------|---------|--------|----------|-------|------|
| 2026-07-29 | `v1.2.1-fix-secure-manager-return` | 100% (66/66) | 0 | 0 | 22.78 | — | 0 | ↑ | 修复 9 个 SecureManager CI 失败；fixture 提取至 conftest.py |
| 2026-08-03 | `8685bb5` | 99.0% (1489/1504) | 0 | 15 | 77.48 | — | 0 | ↑ | fix(ci): 分片排除 sandbox 测试文件并修复 model_cache_utils 导入路� |
| 2026-08-04 | `64f2257` | 99.0% (1489/1504) | 0 | 15 | 69.23 | — | 0 | ↑ | feat(memory): TLM-L3 Markdown 双向同步 + gitleaks CI 修复 (#163) |
| 2026-08-04 | `5c77ac1` | 99.0% (1489/1504) | 0 | 15 | 77.33 | — | 0 | ↑ | fix(ci): 修复 Web 模块测试依赖缺失 |
| YYYY-MM-DD | `<sha7>` | — | — | — | — | — | — | — | 模板占位行，请替换 |

---

## 三、测试文件明细

> 按文件粒度追踪通过率与耗时，定位退化热点。

| 测试文件 | 用例数 | 通过 | 失败 | 跳过 | 耗时(s) | 最近失败日期 | 备注 |
|----------|--------|------|------|------|---------|--------------|------|
| `tests/unit/test_network_config.py` | 28 | 28 | 0 | 0 | ~12 | 2026-07-29 前 | v1.2.1 修复后稳定 |
| `tests/unit/test_network_config_save_regression.py` | 22 | 22 | 0 | 0 | ~10 | — | 历史回归套件 |
| `tests/unit/test_env_hot_reload.py` | — | — | — | — | — | — | 待补填 |
| `tests/unit/test_env_config_manager.py` | 2 | 2 | 0 | 0 | <1 | — | 单例 return 回归测试 |
| `<其他文件>` | — | — | — | — | — | — | 请补充 |

---

## 四、阻塞项与技术债跟踪

> 记录已知非阻塞项，待条件成熟后转为阻塞。

| 项目 | 当前状态 | 目标状态 | 阻塞原因 | 跟踪 Issue | 计划转阻塞日期 |
|------|----------|----------|----------|------------|----------------|
| `env_config_manager.py` mypy 严格检查 | ✅ 阻塞 | 阻塞 | — | — | 已达成 (2026-07-29) |
| `network_config.py` mypy 严格检查 | ⚠️ 非阻塞 (`\|\| true`) | 阻塞 | 29 个历史类型错误（隐式 Optional / None 不可索引 / Returning Any） | 待创建 | 待债务清理 |
| `_mock_env_config_in_ci` fixture 复用 | ✅ 已提取至 conftest.py | — | — | — | 已完成 (2026-07-29) |

### network_config.py 29 个类型错误分类（待修复清单）

| 错误类型 | 错误码 | 数量 | 修复难度 | 备注 |
|----------|--------|------|----------|------|
| 隐式 Optional (`def f(x: str = None)`) | `[assignment]` | 4 | 低 | 改为 `str \| None = None` |
| 变量先 `= None` 后赋 dict | `[assignment]` | 6 | 中 | 需补类型标注或重构初始化 |
| None 不可索引/赋值 | `[index]` | 5 | 中 | 同上，根因相同 |
| 返回值类型不匹配 | `[return-value]` | 3 | 中 | 函数声明 `dict` 实际返回 `dict \| None` |
| Returning Any | `[no-any-return]` | 5 | 低 | 加 `cast()` 或修正返回类型 |
| 参数类型不匹配 | `[arg-type]` | 4 | 中 | `object` → `str` 收紧 |
| 缺少类型标注 | `[var-annotated]` | 1 | 低 | `updates: dict = {}` |
| 属性不存在 | `[attr-defined]` | 1 | 低 | `object.startswith` 需 type narrow |

---

## 五、告警规则

| 触发条件 | 动作 | 通知渠道 |
|----------|------|----------|
| 通过率 < 95% | 创建 GitHub Issue (`ci-regression` 标签) | GitHub Issues + 钉钉机器人 |
| 单测总耗时 > 600s | 在 PR 评论贴出耗时 Top 5 测试 | GitHub PR 评论 |
| mypy 阻塞模块数 ≥ 1 | CI 直接失败，阻塞合入 | GitHub Checks |
| 覆盖率 < 40% | CI 直接失败（`fail_under = 40`） | GitHub Checks |

> 钉钉/邮件通知配置详见 `.github/workflows/ci.yml` 与 project_memory 中 CI failure notifications 约定。

---

## 六、填充说明

### 6.1 从 junit.xml 提取数据

```bash
# 通过率
python -c "
import xml.etree.ElementTree as ET
tree = ET.parse('test-results/junit.xml')
ts = tree.getroot()
total = int(ts.get('tests'))
failed = int(ts.get('failures')) + int(ts.get('errors'))
skipped = int(ts.get('skipped'))
passed = total - failed - skipped
print(f'通过率: {passed}/{total} ({passed/total*100:.1f}%)')
print(f'耗时: {float(ts.get(\"time\")):.2f}s')
"
```

### 6.2 从 coverage.xml 提取覆盖率

```bash
python -c "
import xml.etree.ElementTree as ET
tree = ET.parse('coverage.xml')
root = tree.getroot()
print(f'覆盖率: {float(root.get(\"line-rate\"))*100:.1f}%')
"
```

### 6.3 追加趋势行模板

```markdown
| 2026-MM-DD | `<sha7>` | XX.X% (N/N) | N | N | N.NN | NN% | N | ↑/→/↓ | 简要说明 |
```

---

## 七、相关文档

- CI 流水线配置：[`.github/workflows/ci.yml`](file:///c:/Users/Administrator/agent/.github/workflows/ci.yml)
- 最新一次 CI 报告：[`docs/reports/ci_pipeline_report_20260729.md`](file:///c:/Users/Administrator/agent/docs/reports/ci_pipeline_report_20260729.md)
- v1.2.1 修复发布说明：[`docs/releases/`](file:///c:/Users/Administrator/agent/docs/releases/)
- 测试环境隔离最佳实践：[`docs/best_practices/test_env_isolation_pattern.md`](file:///c:/Users/Administrator/agent/docs/best_practices/test_env_isolation_pattern.md)
- 代码审查摘要：[`docs/reviews/env_config_manager_return_fix_review.md`](file:///c:/Users/Administrator/agent/docs/reviews/env_config_manager_return_fix_review.md)

---

## 八、变更日志

| 日期 | 变更 | 操作人 |
|------|------|--------|
| 2026-07-29 | 初始化看板模板；记录 v1.2.1 修复基线；登记 network_config mypy 技术债 | Yi-Jing Coding Agent |
