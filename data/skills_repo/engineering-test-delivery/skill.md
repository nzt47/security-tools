---
id: engineering-test-delivery
name: engineering-test-delivery
description: 'Use throughout the full life cycle of code development and generation
  to enforce quality-assurance and process-management discipline: production-grade
  code with comments and exception handling, systematic self-testing across functional/boundary/compatibility/performance/error
  paths, generation logs, automated test suites and an audit report — so the whole
  delivery stays traceable and easy to review and troubleshoot.'
content_type: markdown
category: custom
tags:
- imported
- external
- markdown
- 指令型
- 代码与工程
author: workbench
source: manual
status: published
enabled: true
description_zh: 在代码开发与生成的完整生命周期中，严格执行质量保障与过程管理规范，确保交付生产级高质量代码，并保证全流程可追溯、便于后续审查与排查。
---

# 工程化测试与过程管理规范

## 描述
在代码开发与生成的完整生命周期中，严格执行质量保障与过程管理规范，确保交付生产级高质量代码，并保证全流程可追溯、便于后续审查与排查。

## 使用场景
- 代码编写完成后的自测与质量验证阶段。
- 需要生成自动化测试代码的交付场景。
- 需要输出过程日志与审计报告的工程化交付场景。

## 指令

### 1. 代码输出规范
- **代码格式**：所有代码必须使用 Markdown 代码块包裹，并注明编程语言（如 python）。

注释规范：核心逻辑、复杂算法、关键配置必须包含清晰的中文/英文注释。
异常处理：禁止输出无错误处理的裸代码，必须包含必要的 Try-Catch/异常捕获机制。
防幻觉约束：严禁引用不存在的第三方库或虚构 API；若不确定，必须明确标注"需人工核实"。
2. 代码测试规范
全面自测机制：代码编写完成后，必须执行系统化验证，覆盖以下维度：
功能测试（主/分支流程）
边界测试（极端/异常输入）
兼容性测试（多环境/设备）
性能测试（负载/响应/资源占用）
错误处理测试（异常捕获与友好提示）
测试文档与修复：
输出详细的测试用例记录（包含：测试目的、输入数据、预期输出、实际输出）。
发现问题需立即定位修复，并执行回归测试验证。
生产级质量标准：
交付代码需无功能缺陷、无性能瓶颈、无安全隐患。
测试覆盖率需达标，未覆盖部分必须提供明确的风险评估说明。
3. 代码生成过程管理
过程日志记录：
在每次生成代码时，必须在代码块上方输出【生成日志摘要】。
日志必须包含：生成时间戳、内容描述与版本、生成参数、模型配置、关键状态变化（确保在当前会话中完整记录，以便追溯）。
自动化测试套件：
代码生成后，必须立即输出对应的自动化测试代码，覆盖：单元测试、集成测试、功能测试、性能测试及安全测试。
审计报告输出：
测试完成后，整理并输出详细的审计报告，包含：日志摘要、测试结果分析、覆盖率统计、问题清单（含优先级）、修复验证结果。
确保全流程可追溯，便于后续审查与排查。