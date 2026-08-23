# 事故复盘知识库索引

> 本页是云枢项目 **事故复盘与文档模板** 的知识库索引。
> 归档事故复盘报告与可复用文档模板，供后续排查同类问题、撰写新复盘时快速复用。

---

## 📋 文档模板

| 文档 | 说明 |
|------|------|
| [事故复盘文档模板](incident_report_template.md) | 通用复盘 Markdown 模板，含事件摘要/根因分析/时间线/预防措施等八节结构，占位符即填即用 |

## 🔍 事故复盘案例

| 文档 | 事故 | 根因 | 关键契约 |
|------|------|------|---------|
| [CI pytest 插件缺失事故复盘](ci_pytest_plugins_incident_report.md) | 多个 CI job 缺 pytest 插件导致 workflow 失败 | pytest.ini 全局配置项依赖插件注册，`--strict-config` 下缺插件即硬错误 | 任何跑 pytest 的 job 必须覆盖 `pytest-timeout` + `pytest-asyncio`；平台专属测试加 skipif |
| [Pre-commit Hook BOM 叠加事故复盘](precommit_hook_bom_incident_report_v2.md) | PS 脚本叠加 UTF-8 BOM 导致解析失败、部署旧模板 | 文件头叠加多个 `EF BB BF` 破坏 `<#` 块注释 | PS 脚本恰 1 个 BOM；hook 无 BOM；py 无 BOM |

---

## 🔗 关联目录

- [CI 指南（ci_guidelines）](../ci_guidelines/)：workflow_run_guard 规范、pre-commit hook 复用指南、BOM 事故原始报告
- [CI 安全扫描 Wiki](ci_security_scan_wiki.md)：gitleaks 全分支扫描使用指南

---

## 📌 使用建议

1. 新事故复盘：复制 `incident_report_template.md`，替换占位符，归档至本目录。
2. 复盘完成后：在本索引表格中追加一行，保持索引与案例同步。
3. 涉及编码/CI 约定：同步更新 [CI 指南](../ci_guidelines/) 中的契约文档。
