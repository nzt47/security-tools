# scripts 批次 1 测试用例清单（2026-08-10）

> 状态：**草稿**。5 个门禁/治理类脚本测试骨架已生成并通过本地验证。
> 关联：[scripts_incremental_test_plan_20260809.md](scripts_incremental_test_plan_20260809.md)（批次 1 计划）

---

## 1. 完成情况总览

| 脚本 | 测试文件 | 用例数 | 覆盖率 | 备注 |
|------|---------|-------|--------|------|
| `observability_quality_gate.py` (351行) | `tests/unit/test_scripts_quality_gate.py` | 27 | **90%** | 缺失：print_summary 输出断言、main 全链路合并、异常分支 |
| `check_scripts_coverage.py` (37行) | `tests/unit/test_scripts_coverage_gate.py` | 11 | **100%** | xml 解析 + 三档阈值判定全覆盖 |
| `ci_guard_types.py` (98行) | `tests/unit/test_scripts_ci_guard_types.py` | 16 | **100%** | 契约校验全分支；1 个 xfail 暴露被测缺陷 |
| `config_snapshot.py` (93行) | `tests/unit/test_scripts_config_snapshot.py` | 10 | **100%** | 依赖 monkeypatch 隔离，不污染真实配置 |
| `csv_to_md_table.py` (79行) | `tests/unit/test_scripts_csv_to_md.py` | 13 | **100%** | 含 `re` 注入 workaround（见 §3） |

合计：**78 passed + 1 xfailed**（本地 `pytest tests/unit/test_scripts_*.py` 验证）

## 2. 测试点清单

### 2.1 observability_quality_gate.py —— 门禁聚合核心
- collect_reports：目录不存在 / 正常收集 / 坏 JSON 跳过不中断
- check_config_validation：报告缺失(skip) / passed / failed
- check_unit_tests：缺失(skip) / found(passed)
- check_coverage：缺失(skip) / totals 格式达标 / 未达标(failed) / 顶层 coverage 键 / percent_covered 键 / 无百分比(skip)
- check_e2e_tests：require=True 缺失(failed) / require=False 缺失(skipped) / passed / failed
- check_prometheus_integration：缺失(skip) / passed / failed
- run_all_checks：全链路 passed / e2e 失败导致 failed / 报告文件落盘
- main：全部通过 exit 0 / e2e 缺失 exit 1 / --require-e2e-pass false 解析

### 2.2 check_scripts_coverage.py —— scripts 层红线门禁
- 解析失败三分支：文件缺失 / 坏 xml / 缺 line-rate 属性 → exit 1 + `::error::`
- 缺口 ≥ warn-gap → exit 1；缺口 < warn-gap → exit 0 + `::warning::`
- 边界：缺口 == warn-gap 时（严格 `<`）→ 仅告警
- 达标 / 恰在红线 → exit 0 + `✅`
- CLI 覆盖：--fail-under 60 / --warn-gap 0

### 2.3 ci_guard_types.py —— CI 输出契约校验
- 非 dict 输入 / tool 标识不匹配
- timestamp：非字符串 / 非法 ISO / 合法（含 Z 后缀）
- steps：缺失 / 空 / 元素非 dict / 缺必需字段 / exit_code 非 int / 未知 step 名
- overall：缺失 / status 非法 / exit_code 非 int / status-exit_code 不一致（pass=0, fail≠0）
- guard_verify 一致性：overall.exit_code ≠ 最后 guard_verify → 错误；无 guard_verify → 不校验
- 合法报告 → 空列表

### 2.4 config_snapshot.py —— 配置快照生成
- 快照结构键齐全 / total_paths == 规则数 / metadata 按 rule.path 建条目 / config 含 get_all 结果
- include_runtime=True → runtime_included 键
- git SHA：成功 / 空输出 → 'unknown' / 命令异常 → 'unknown'
- main：--output 写文件 / --include-runtime 透传

### 2.5 csv_to_md_table.py —— CSV → Markdown
- 基本转换（表头 + 分隔行 + 数据行）/ BOM 处理 / 单元格 `|` 转义
- 空文件 / 仅表头 → exit；文件不可读 → exit
- build_section：H3 标题格式
- upsert_into_report：首次插入（锚点前）/ 同标题幂等替换 / 报告不可读 → exit
- main：仅打印 / 插入报告 / CSV 不存在 → exit

## 3. 发现的被测代码缺陷（未改被测代码，待确认）

| # | 脚本 | 缺陷 | 影响 | 建议 |
|---|------|------|------|------|
| 1 | `csv_to_md_table.py` | `import re` 仅在 `__main__` 局部导入，模块级函数 `upsert_into_report` 直接调用会 `NameError` | 被其他模块 import 复用时报错 | 将 `import re` 上移到模块顶层 |
| 2 | `ci_guard_types.py` L94-102 | guard_verify 一致性检查遍历 steps 时未跳过非 dict 元素 → `AttributeError` | 违反 docstring「失败返回错误列表, 不抛异常」契约；畸形输入可导致校验崩溃 | 一致性检查处加 `isinstance(s, dict)` 过滤（测试已标 xfail 追踪） |

> 按【不易】约束：测试草稿以 workaround/xfail 规避，不改动被测代码；修复需用户确认。

## 4. 待完善项（非阻塞）

- `observability_quality_gate.py` 90%：补 print_summary 输出断言、run_all_checks 中 check 函数抛异常分支（L322-325）
- CI 接入：见计划 §3.2，将 `tests/unit/test_scripts_*.py` 追加到「优化脚本单元测试」step

## 5. 验收记录

- 本地验证：`python -m pytest tests/unit/test_scripts_*.py` → **78 passed, 1 xfailed**
- 覆盖率验证：`pytest --cov=scripts --cov-config=.coveragerc_scripts --cov-report=term`（口径与 CI 门禁一致）
