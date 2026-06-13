# 云枢系统测试管理系统

## 📋 概述

本项目为云枢(Yunshu)系统提供了一套完整的P0优先级自动化测试管理方案，包括：

- ✅ 基于pytest的统一测试框架
- ✅ 完善的测试用例设计规范
- ✅ 智能的测试数据管理策略
- ✅ 系统化的测试覆盖率追踪
- ✅ 完整的CI/CD流程集成
- ✅ 可视化的测试报告生成
- ✅ 自动化的告警通知机制

## 🚀 快速开始

### 1. 安装依赖

```bash
pip install pytest pytest-cov pytest-mock pytest-asyncio
pip install -e .
```

### 2. 运行测试

```bash
# 运行所有测试
pytest

# 仅运行快速测试
pytest -m "not slow"

# 仅运行单元测试
pytest tests/unit/

# 带覆盖率运行
pytest --cov=agent --cov-report=html
```

### 3. 使用便捷脚本

```bash
# Windows
run_tests.bat all
run_tests.bat unit
run_tests.bat quick

# Python脚本
python test_manager.py test --unit
python test_manager.py coverage
python test_manager.py report
```

## 📁 项目结构

```
tests/
├── unit/                    # 单元测试
│   ├── test_memory.py
│   ├── test_permission.py
│   └── test_monitoring.py
├── integration/             # 集成测试
│   └── test_v2_features.py
├── e2e/                     # 端到端测试
├── fixtures/                # 测试数据
│   ├── test_config.json
│   └── test_cases.json
├── conftest.py            # pytest配置
├── pytest.ini              # pytest主配置
├── coverage_checker.py     # 覆盖率检查
└── test_reporter.py       # 报告生成器
```

## 📊 测试用例标记

| 标记 | 说明 | CI策略 |
|------|------|--------|
| `@pytest.mark.p0` | P0优先级，必须通过 | 阻止合并 |
| `@pytest.mark.p1` | P1优先级，建议通过 | 警告 |
| `@pytest.mark.unit` | 单元测试 | 必须通过 |
| `@pytest.mark.integration` | 集成测试 | 必须通过 |
| `@pytest.mark.slow` | 慢速测试 | 默认跳过 |
| `@pytest.mark.quick` | 快速测试 | 默认执行 |
| `@pytest.mark.requires_llm` | 需要LLM服务 | 需要环境 |

## 📈 覆盖率目标

| 模块 | 目标覆盖率 | 状态 |
|------|----------|------|
| agent/ (核心) | 80%+ | 🔄 |
| agent/memory/ | 85%+ | 🔄 |
| agent/permission/ | 90%+ | 🔄 |
| agent/monitoring/ | 75%+ | 🔄 |
| **全局** | **70%+** | 🔄 |

## 🔧 CI/CD集成

### GitHub Actions工作流

测试在以下时机自动执行：

- ✅ 代码推送到 `main`、`develop` 分支
- ✅ Pull Request 到 `main`、`develop` 分支
- ⏰ 每天凌晨2点（完整测试）

### 质量门禁

| 检查项 | 阈值 | 行为 |
|--------|------|------|
| 单元测试 | ≥95%通过 | 阻止合并 |
| 集成测试 | ≥90%通过 | 阻止合并 |
| P0测试 | 100%通过 | 阻止合并 |
| 覆盖率 | ≥70% | 阻止合并 |
| 安全扫描 | 0高危 | 阻止合并 |

## 📝 报告与告警

### 测试报告

- **HTML报告**: `test_reports/htmlcov/index.html`
- **JSON报告**: `test_reports/test_report_*.json`
- **覆盖率报告**: `test_reports/coverage_report.json`

### 告警配置

支持多种告警渠道：

```python
alert_config = AlertConfig(
    enabled=True,
    email_enabled=True,
    email_recipients=["team@example.com"],
    slack_enabled=True,
    slack_webhook_url="https://hooks.slack.com/...",
    webhook_enabled=True,
    webhook_urls=["https://example.com/webhook"]
)
```

## 📚 相关文档

- [测试管理方案详细文档](./TEST_MANAGEMENT_PLAN.md)
- [pytest配置参考](./pytest.ini)
- [测试用例设计规范](./tests/TEST_CASE_DESIGN_GUIDELINES.py)

## 🔍 常见问题

### Q: 如何运行特定模块的测试？

```bash
pytest tests/unit/test_memory.py
pytest tests/integration/test_v2_features.py -v
```

### Q: 如何跳过慢速测试？

```bash
pytest -m "not slow"
```

### Q: 如何生成覆盖率报告？

```bash
pytest --cov=agent --cov-report=html --cov-report=xml
python tests/coverage_checker.py
```

### Q: 如何配置告警通知？

编辑 `tests/conftest.py` 中的 `AlertConfig` 配置。

## 📞 支持

如有问题，请联系测试团队或提交Issue。

---

**版本**: v1.0.0
**更新日期**: 2026-05-31
