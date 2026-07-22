# Skills 数据一致性 CI/CD 集成指南

本文档说明如何在 CI/CD 流水线中集成 `verify_migrated_skills.py` 和 `compare_skills_legacy_vs_repo.py`，确保每次提交后技能元数据的一致性。

## 脚本概览

| 脚本 | 用途 | 退出码 |
|---|---|---|
| `scripts/compare_skills_legacy_vs_repo.py` | 对比新旧格式元数据 | 0=一致, 1=有差异 |
| `scripts/verify_migrated_skills.py` | 三层架构加载+执行验证 | 0=PASS, 1=FAIL |
| `scripts/detect_dynamic_loads.py` | 扫描动态加载风险 | 0=无 HIGH, 1=有 HIGH |

## GitHub Actions 集成

### 基础验证（每次提交）

在 `.github/workflows/ci.yml` 中添加：

```yaml
name: CI
on: [push, pull_request]

jobs:
  skills-consistency:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - name: 安装依赖
        run: pip install watchdog  # --watch 模式需要
      - name: 新旧格式元数据对比
        run: python scripts/compare_skills_legacy_vs_repo.py
      - name: 三层架构加载验证
        run: python scripts/verify_migrated_skills.py
```

### PR 门禁（阻塞合并）

将验证脚本设为 required check：

```yaml
  skills-gate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - name: 一致性门禁
        run: |
          python scripts/compare_skills_legacy_vs_repo.py
          python scripts/verify_migrated_skills.py
          python scripts/detect_dynamic_loads.py
```

在 GitHub Settings → Branches → Branch protection rules 中将 `skills-gate` 设为 required status check。

### 定期全量扫描（定时任务）

每天凌晨跑一次完整扫描并上传报告：

```yaml
  nightly-scan:
    runs-on: ubuntu-latest
    if: github.event.schedule == '0 0 * * *'
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - name: 动态加载风险扫描
        run: python scripts/detect_dynamic_loads.py --json > dynamic_loads_report.json
      - name: 上传报告
        uses: actions/upload-artifact@v4
        with:
          name: dynamic-load-scan
          path: dynamic_loads_report.json
```

## --watch 模式的使用场景

`--watch` 模式主要用于**本地开发**，不推荐在 CI 中使用（CI 是一次性执行，不需要持续监控）。

### 本地开发流程

```bash
# 终端 1：启动 watch 监控
python scripts/verify_migrated_skills.py --watch

# 终端 2：编辑 skill.md
# 保存后终端 1 自动触发重新验证
```

### watch 模式的两种实现

| 模式 | 触发方式 | CPU 占用 | 延迟 | 依赖 |
|---|---|---|---|---|
| 事件驱动 (watchdog) | 文件系统事件 | 极低 | <1s | `pip install watchdog` |
| 轮询 (polling) | mtime 对比 | 低 | 2s | 无 |

watchdog 不可用时自动降级到 polling，无需手动切换。

## GitLab CI 集成

```yaml
stages:
  - validate

skills-consistency:
  stage: validate
  image: python:3.11
  script:
    - pip install watchdog
    - python scripts/compare_skills_legacy_vs_repo.py
    - python scripts/verify_migrated_skills.py
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
```

## 失败排查

### compare 报差异

```
[skills-consistency] WARN: 检测到差异 (exit=1)
  字段差异: scripted-selftest.description legacy=... repo=...
```

**原因**：skill.md 的 front matter 与 skills.json 的 description 不一致。

**修复**：同步两者的 description 字段后重新提交。

### verify 报 FAIL

```
L3  execute:   FAIL
     [FAIL] scripted-selftest: success=False exit=1
```

**原因**：脚本执行失败（exit code 非 0）或未输出 JSON。

**修复**：检查 `scripts/main.py` 是否从 stdin 读取参数、stdout 最后一行是否为合法 JSON。

### detect 报 HIGH 风险

```
[HIGH] 1 处
  scripts/archive/xxx.py:694
    函数: force_load_module
    建议: 改用 subprocess 或将外部脚本加入包路径后用 importlib.import_module
```

**原因**：生产代码中使用了从文件路径加载模块的方式。

**修复**：按建议改用 subprocess 调用，或将被加载脚本加入包路径。

## pytest 自动集成

`tests/conftest.py` 的 `setup_test_environment` fixture 已集成一致性检查，每次 `pytest` 启动时自动执行：

```
[skills-consistency] PASS: 字段对比结果: ALL_MATCH
```

不一致时只打印警告，不阻塞测试。如需强制阻塞，将 `_check_skills_consistency` 中的 `print` 改为 `pytest.fail`。
