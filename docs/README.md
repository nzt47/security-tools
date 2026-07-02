# 云枢智能体 - 项目文档中心

欢迎来到云枢智能体的文档中心。本页面提供项目的完整文档索引，帮助您快速找到所需的文档资料。

---

## 📚 文档目录结构

```
docs/
├── adr/                    # 架构决策记录 (Architecture Decision Records)
│   ├── 003-error-handling-retry.md
│   └── README.md
├── archive/                # 归档文档（历史版本、旧版报告）
├── deployment/             # 部署相关文档
├── logging/                # 日志相关文档
├── security/               # 安全相关文档
│   ├── CHANGELOG.md
│   ├── DEPLOYMENT_CHECKLIST.md
│   ├── confluence_sync_status_confirmation.md  # Confluence 同步确认单
│   ├── p0_deployment_verification_report.md    # P0 部署验证报告
│   ├── p0_security_retrospective.md            # P0 安全修复复盘
│   ├── potential_risks_analysis.md
│   ├── secure_config_guide.md
│   └── security_coding_checklist.md            # 安全编码规范
├── superpowers/            # 超级能力模块
│   ├── design/             # 设计文档
│   ├── plans/              # 计划文档
│   └── specs/              # 规格文档
├── test_reports/           # 测试报告
├── troubleshooting/        # 故障排查指南
├── wiki/                   # 知识库
└── zh/                     # 中文文档
```

---

## 🔗 快速链接

### 🏁 快速入门

| 文档 | 描述 |
|------|------|
| [快速部署卡片](DEPLOYMENT_QUICK_CARD.md) | 一键部署指南 |
| [架构概述](architecture.md) | 系统架构总览 |
| [使用指南](zh/使用指南.md) | 中文用户手册 |

### 🏗️ 架构设计

| 文档 | 描述 |
|------|------|
| [架构文档](architecture.md) | 系统架构设计 |
| [架构优化报告](architecture_optimization_report.md) | 架构优化建议 |
| [架构合规性审计](superpowers/specs/2026-06-22-架构合规性审计报告.md) | 架构合规性审核 |

### 📖 用户指南

| 文档 | 描述 |
|------|------|
| [使用指南](zh/使用指南.md) | 中文使用手册 |
| [可视化界面展示](zh/可视化界面完整展示.md) | UI 界面说明 |
| [任务窗口提示词](zh/任务窗口提示词_TW01-TW03.md) | 任务窗口说明 |

### 🛠️ 开发指南

| 文档 | 描述 |
|------|------|
| [错误处理指南](error-handling-guide.md) | 错误处理最佳实践 |
| [错误处理示例](error-handler-examples.md) | 错误处理代码示例 |
| [工具系统修复计划](tool-system-repair-plan.md) | 工具系统维护 |

### 📊 运维文档

| 文档 | 描述 |
|------|------|
| [可观测性操作手册](OBSERVABILITY_OPERATION_MANUAL.md) | 监控与追踪指南 |
| [可见性改造总结报告](observability/visibility_improvement_summary.md) | D2/D3/D5 指标改造全过程与修复记录 |
| [追踪部署指南](tracing_deployment.md) | 分布式追踪部署 |
| [生产环境追踪配置](tracing_production_config.md) | 生产环境配置 |
| [业务指标定义](business_metrics_definition.md) | 业务指标详解 |
| [混沌工程指南](chaos_engineering_guide.md) | 故障注入测试 |

### 🧪 测试文档

| 文档 | 描述 |
|------|------|
| [测试完成计划](test_reports/TEST_COMPLETION_PLAN.md) | 测试计划 |
| [安全测试报告](test_reports/security_test_report.md) | 安全测试 |
| [最终集成报告](test_reports/final_integration_report.md) | 集成测试 |

### 🔒 安全文档

| 文档 | 描述 |
|------|------|
| [安全配置指南](security/secure_config_guide.md) | 安全最佳实践 |
| [风险分析](security/potential_risks_analysis.md) | 潜在风险评估 |
| [安全检查清单](security/DEPLOYMENT_CHECKLIST.md) | 部署安全检查 |

#### 🚨 P0 安全修复专题（2026-07-02）

P0-SEC-001（Bearer Token 脱敏失败）与 P0-SEC-002（贪婪正则吞噬 URL 参数）的完整修复记录。

| 文档 | 描述 |
|------|------|
| [P0 安全修复补丁包说明](../patches/p0_security/README.md) | 补丁包概述、修复方案、受影响模块、测试验证 |
| [P0 安全修复完整部署验证报告](security/p0_deployment_verification_report.md) | CI 执行日志、测试覆盖率统计、Git 提交历史、CI 防护体系 |
| [P0 安全修复复盘报告](security/p0_security_retrospective.md) | 问题根因复盘、修复过程、经验教训 |
| [Confluence 同步任务执行状态确认单](security/confluence_sync_status_confirmation.md) | 文档同步任务执行过程、CI 流水线状态、最终结论 |
| [安全编码规范](security/security_coding_checklist.md) | 敏感数据脱敏编码规范 |

**相关代码与配置**：

| 文件 | 说明 |
|------|------|
| [tests/regression/test_p0_security_fix.py](../tests/regression/test_p0_security_fix.py) | 68 个 P0 防复发回归测试用例 |
| [.github/workflows/p0-security.yml](../.github/workflows/p0-security.yml) | P0 安全验证 CI 工作流（5 个 Job） |
| [scripts/scan_sensitive_regex.py](../scripts/scan_sensitive_regex.py) | 贪婪正则静态扫描脚本 |
| [patches/p0_security/p0_security_test_extension.patch](../patches/p0_security/p0_security_test_extension.patch) | P0 防复发测试扩展纯 diff 补丁 |

**CI 防护体系**：修改敏感数据相关模块时，CI 会自动触发 5 个验证 Job（静态扫描、P0 回归测试、补丁完整性、跨模块一致性、总结）。详见 [P0 安全验证工作流](https://github.com/nzt47/security-tools/actions/workflows/p0-security.yml)。

---

## 🔍 搜索指引

### 按主题搜索

| 主题 | 关键词 | 推荐文档 |
|------|--------|----------|
| **可观测性** | tracing, metrics, observability | [OBSERVABILITY_OPERATION_MANUAL.md](OBSERVABILITY_OPERATION_MANUAL.md) |
| **分布式追踪** | trace, span, opentelemetry | [tracing_deployment.md](tracing_deployment.md) |
| **业务指标** | metrics, dashboard, business | [business_metrics_definition.md](business_metrics_definition.md) |
| **混沌工程** | chaos, fault, injection | [chaos_engineering_guide.md](chaos_engineering_guide.md) |
| **部署** | deployment, deploy, install | [DEPLOYMENT_QUICK_CARD.md](DEPLOYMENT_QUICK_CARD.md) |
| **安全** | security, secure, risk | [security/secure_config_guide.md](security/secure_config_guide.md) |
| **错误处理** | error, retry, handler | [error-handling-guide.md](error-handling-guide.md) |

### 按目录浏览

```
快速入门 → deployment/, DEPLOYMENT_QUICK_CARD.md
架构设计 → architecture.md, superpowers/design/
用户指南 → zh/使用指南.md
开发指南 → error-handling-*.md, tool-system-repair-plan.md
运维文档 → OBSERVABILITY_OPERATION_MANUAL.md, tracing_*.md
测试文档 → test_reports/
安全文档 → security/
```

---

## 📋 文档质量检查

### 文档状态

| 类别 | 文档数 | 状态 |
|------|--------|------|
| 架构设计 | 5 | ✅ 完整 |
| 快速入门 | 3 | ✅ 完整 |
| 用户指南 | 5 | ✅ 完整 |
| 开发指南 | 8 | ✅ 完整 |
| 运维文档 | 12 | ✅ 完整 |
| 测试文档 | 6 | ✅ 完整 |
| 安全文档 | 5 | ✅ 完整 |
| 归档文档 | 90+ | 📦 归档 |

### 更新频率

- **核心文档**: 持续更新
- **架构文档**: 按需更新
- **测试报告**: 版本发布时更新
- **归档文档**: 不再更新

---

## 🤝 贡献指南

欢迎贡献文档！请遵循以下规范：

1. **文档格式**: 使用 Markdown 格式
2. **命名规范**: 文件名使用小写字母，单词之间用 `-` 分隔
3. **语言**: 技术文档使用英文，用户指南使用中文
4. **目录结构**: 按类别放置到对应目录

---

## 📞 联系我们

如有文档相关问题或建议，请联系开发团队。

**文档版本**: v1.0  
**最后更新**: 2026年6月  
**适用版本**: 云枢智能体 v2.x