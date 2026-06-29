# 云枢智能体 - 文档目录结构说明

## 概述

本文档详细说明了云枢智能体项目的文档目录结构，帮助开发者和用户快速定位所需文档。

---

## 目录结构总览

```
docs/                              # 文档根目录
├── adr/                           # 架构决策记录
│   ├── README.md                  # ADR 说明文档
│   ├── adr-template.md            # ADR 模板
│   └── 003-error-handling-retry.md # 错误处理与重试决策记录
├── archive/                       # 归档文档（历史版本）
│   ├── *.md                       # 各种历史报告、旧版文档
├── deployment/                    # 部署相关文档
│   └── memory_module_deployment.md # 内存模块部署指南
├── logging/                       # 日志相关文档
│   └── compression_log_format.md  # 压缩日志格式说明
├── security/                      # 安全相关文档
│   ├── CHANGELOG.md               # 安全变更日志
│   ├── DEPLOYMENT_CHECKLIST.md    # 安全部署检查清单
│   ├── TROUBLESHOOTING.md         # 安全故障排查
│   ├── potential_risks_analysis.md # 潜在风险分析
│   └── secure_config_guide.md     # 安全配置指南
├── superpowers/                   # 超级能力模块
│   ├── design/                    # 设计文档
│   │   ├── README.md              # 设计文档说明
│   │   ├── P0_*.md                # P0 阶段设计文档
│   │   ├── P1_*.md                # P1 阶段设计文档
│   │   ├── P2_*.md                # P2 阶段设计文档
│   │   ├── P3_*.md                # P3 阶段设计文档
│   │   ├── P4_*.md                # P4 阶段设计文档
│   │   ├── P5_*.md                # P5 阶段设计文档
│   │   └── P6_*.md                # P6 阶段设计文档
│   ├── plans/                     # 计划文档
│   │   ├── 2026-*.md              # 日期命名的计划文档
│   │   └── PT*-*.md               # 测试计划文档
│   └── specs/                     # 规格文档
│       └── 2026-*.md              # 日期命名的规格文档
├── test_reports/                  # 测试报告
│   ├── TEST_COMPLETION_PLAN.md    # 测试完成计划
│   ├── TEST_SUPPLEMENT_PLAN.md    # 测试补充计划
│   ├── WEB_MODULE_TEST_REPORT.md  # Web 模块测试报告
│   ├── compression_logic_report.md # 压缩逻辑测试报告
│   ├── final_integration_report.md # 最终集成测试报告
│   └── security_test_report.md    # 安全测试报告
├── troubleshooting/               # 故障排查指南
│   └── compression_error_guide.md # 压缩错误排查指南
├── wiki/                          # 知识库
│   └── security_config_wiki.md    # 安全配置知识库
├── zh/                            # 中文文档
│   ├── 使用指南.md                 # 用户使用指南
│   ├── 可视化界面完整展示.md       # UI 展示文档
│   ├── 性能优化报告.md             # 性能优化报告
│   ├── 阶段一_感知底座.md          # 阶段一文档
│   ├── 阶段二_元认知引擎.md        # 阶段二文档
│   ├── 阶段三_记忆压缩机制.md      # 阶段三文档
│   ├── 阶段四_整合与反身智能.md    # 阶段四文档
│   └── 项目总结.md                 # 项目总结
├── README.md                      # 文档中心入口
├── OBSERVABILITY_COMPLETE.md      # 可观测性完整手册（整合版）
├── OBSERVABILITY_OPERATION_MANUAL.md # 可观测性操作手册
├── business_metrics_definition.md # 业务指标定义文档
├── chaos_engineering_guide.md     # 混沌工程指南
├── tracing_deployment.md          # 追踪部署指南
├── tracing_production_config.md   # 生产环境追踪配置
├── architecture.md                # 架构文档
├── error-handling-guide.md        # 错误处理指南
└── DEPLOYMENT_QUICK_CARD.md       # 快速部署卡片
```

---

## 文档分类说明

### 1. 快速入门类

| 文档 | 描述 | 位置 |
|------|------|------|
| 快速部署卡片 | 一键部署指南 | `DEPLOYMENT_QUICK_CARD.md` |
| 架构概述 | 系统架构总览 | `architecture.md` |
| 使用指南 | 中文用户手册 | `zh/使用指南.md` |

### 2. 架构设计类

| 文档 | 描述 | 位置 |
|------|------|------|
| 架构文档 | 系统架构设计 | `architecture.md` |
| 架构优化报告 | 架构优化建议 | `architecture_optimization_report.md` |
| 架构决策记录 | ADR 文档集合 | `adr/` |
| 阶段设计文档 | P0-P6 阶段设计 | `superpowers/design/` |

### 3. 用户指南类

| 文档 | 描述 | 位置 |
|------|------|------|
| 使用指南 | 中文使用手册 | `zh/使用指南.md` |
| 可视化界面展示 | UI 界面说明 | `zh/可视化界面完整展示.md` |
| 任务窗口提示词 | 任务窗口说明 | `zh/任务窗口提示词_TW01-TW03.md` |
| 项目总结 | 项目总体总结 | `zh/项目总结.md` |

### 4. 开发指南类

| 文档 | 描述 | 位置 |
|------|------|------|
| 错误处理指南 | 错误处理最佳实践 | `error-handling-guide.md` |
| 错误处理示例 | 错误处理代码示例 | `error-handler-examples.md` |
| 工具系统修复计划 | 工具系统维护 | `tool-system-repair-plan.md` |
| 规格文档 | 各功能模块规格 | `superpowers/specs/` |

### 5. 运维文档类

| 文档 | 描述 | 位置 |
|------|------|------|
| 可观测性操作手册 | 监控与追踪指南 | `OBSERVABILITY_OPERATION_MANUAL.md` |
| 可观测性完整手册 | 整合版可观测性文档 | `OBSERVABILITY_COMPLETE.md` |
| 追踪部署指南 | 分布式追踪部署 | `tracing_deployment.md` |
| 生产环境追踪配置 | 生产环境配置 | `tracing_production_config.md` |
| 业务指标定义 | 业务指标详解 | `business_metrics_definition.md` |
| 混沌工程指南 | 故障注入测试 | `chaos_engineering_guide.md` |
| 故障排查指南 | 压缩错误排查 | `troubleshooting/compression_error_guide.md` |

### 6. 测试文档类

| 文档 | 描述 | 位置 |
|------|------|------|
| 测试完成计划 | 测试计划 | `test_reports/TEST_COMPLETION_PLAN.md` |
| 测试补充计划 | 补充测试计划 | `test_reports/TEST_SUPPLEMENT_PLAN.md` |
| 安全测试报告 | 安全测试 | `test_reports/security_test_report.md` |
| 最终集成报告 | 集成测试 | `test_reports/final_integration_report.md` |
| Web 模块测试报告 | Web 模块测试 | `test_reports/WEB_MODULE_TEST_REPORT.md` |

### 7. 安全文档类

| 文档 | 描述 | 位置 |
|------|------|------|
| 安全配置指南 | 安全最佳实践 | `security/secure_config_guide.md` |
| 风险分析 | 潜在风险评估 | `security/potential_risks_analysis.md` |
| 安全检查清单 | 部署安全检查 | `security/DEPLOYMENT_CHECKLIST.md` |
| 安全变更日志 | 安全变更记录 | `security/CHANGELOG.md` |

### 8. 归档文档类

| 文档 | 描述 | 位置 |
|------|------|------|
| 历史报告 | 各种历史版本报告 | `archive/` |
| 旧版文档 | 已归档的旧版文档 | `archive/` |

---

## 文档命名规范

### 通用规范

1. **文件名**: 全部使用小写字母
2. **单词分隔**: 使用 `-` 连接单词
3. **日期格式**: `YYYY-MM-DD-xxx.md`
4. **版本标识**: `xxx_v1.0.md` 或 `xxx_v2.md`

### 特殊规范

| 类型 | 命名模式 | 示例 |
|------|----------|------|
| 设计文档 | `P<阶段>_<模块>.md` | `P1_核心调度与本地推理.md` |
| 规格文档 | `YYYY-MM-DD-<功能>-design.md` | `2026-05-28-agent-sidebar-design.md` |
| 计划文档 | `YYYY-MM-DD-<功能>-plan.md` | `2026-05-28-元认知引擎-plan.md` |
| 测试报告 | `TEST_<类型>_<内容>.md` | `TEST_COMPLETION_PLAN.md` |

---

## 文档状态说明

| 状态标识 | 含义 | 说明 |
|----------|------|------|
| ✅ 完整 | 文档已完成 | 内容完整，无需修改 |
| 🚧 进行中 | 文档编写中 | 部分内容可能不完整 |
| 📦 归档 | 已归档 | 历史文档，不再更新 |
| 🔄 待更新 | 需要更新 | 内容可能过时 |
| ❌ 缺失 | 文档缺失 | 需要创建 |

---

## 文档索引

### 按首字母排序

| 文档 | 位置 |
|------|------|
| architecture.md | `/` |
| business_metrics_definition.md | `/` |
| chaos_engineering_guide.md | `/` |
| DEPLOYMENT_QUICK_CARD.md | `/` |
| error-handling-guide.md | `/` |
| error-handler-examples.md | `/` |
| OBSERVABILITY_COMPLETE.md | `/` |
| OBSERVABILITY_OPERATION_MANUAL.md | `/` |
| tracing_deployment.md | `/` |
| tracing_production_config.md | `/` |

---

**文档版本**: v1.0  
**最后更新**: 2026年6月