# TLM 核心模块测试覆盖率分析报告

> **生成时间**: 2026-07-29 01:22:49  
> **数据来源**: coverage.xml（2026-07-12 生成）+ L3 测试日志补充  
> **全局行覆盖率**: 49.1% (27,696/56,432)

> **重要说明**: coverage.xml 生成于 2026-07-12，部分新增模块（VectorStore/SqliteVecBackend）
> 在该文件中缺失，已用 L3 Docker 测试日志（2026-07-29）的参考覆盖率补充。
> EnvConfigManager 暂无覆盖率数据（待测）。建议运行 L3 全量测试重新生成 coverage.xml。

---

## 核心模块覆盖率明细

| 模块 | 文件路径 | 覆盖率 | 覆盖/总行 | 缺失行数 | 阈值 | 状态 | 数据来源 |
|------|---------|--------|----------|---------|------|------|---------|
| EnvConfigManager | `agent/env_config_manager.py` | 0.0% | 参考值 | - | 80% | ⚠️ | 待测（无参考数据） |
| VectorStore | `memory/vector_store/vector_store.py` | 44.0% | 参考值 | - | 80% | ❌ | 参考值（L3 Docker 测试日志（2026-07-29）） |
| NetworkConfig | `agent/network_config.py` | 70.3% | 463/659 | 196 | 80% | ❌ | coverage.xml（真实数据） |
| LongTermMemory | `agent/memory/long_term_memory.py` | 75.8% | 147/194 | 47 | 80% | ❌ | coverage.xml（真实数据） |
| SqliteVecBackend | `memory/vector_store/sqlite_vec_backend.py` | 89.0% | 参考值 | - | 80% | ✅ | 参考值（L3 Docker 测试日志（2026-07-29）） |

## 覆盖率不足 80% 的核心模块分析

共 **3** 个核心模块覆盖率不足 80%，需补充测试用例：

### VectorStore (44.0%)

- **文件**: `memory/vector_store/vector_store.py`
- **描述**: 向量存储抽象层 - 语义检索入口
- **数据来源**: 参考值（L3 Docker 测试日志（2026-07-29））
- **缺失行数**: 暂无行级数据（参考值来自 L3 测试日志）

**优化建议**:
- 补充 `_init_chroma()` 失败降级路径测试（Rust 后端不兼容场景）
- 添加 ChromaDB 不可用时 BM25 fallback 完整测试
- 补充 `add()` / `search()` 异常输入测试（None、空列表、超大输入）
- 添加并发写入测试（验证线程安全）

### NetworkConfig (70.3%)

- **文件**: `agent/network_config.py`
- **描述**: 网络配置管理
- **数据来源**: coverage.xml（真实数据）
- **缺失行数**: 196 行
- **缺失行号**: 170, 220, 246, 251-252, 282-283, 292-294, 386-387, 433-434, 438, 442, 455, 473, 475-477, 479-481, 484-486, 488-489, 492-493, 495-497, 499-501, 529, 536-537, 540-545, 547-550, 560, 573, 596, 601, 612, 619-620, 628, 634, 636, 666-667, 698-699, 703-704, 709-711, 722-723, 781, 813, 871-873, 878-879, 881... (共 196 行)

**优化建议**:
- 清理历史类型债（29 个 mypy 错误，详见 ci.yml TODO 注释）
- 补充网络配置异常路径测试（DNS 解析失败、连接超时）
- 添加配置热更新测试（运行时修改 .env 的行为验证）

### LongTermMemory (75.8%)

- **文件**: `agent/memory/long_term_memory.py`
- **描述**: TLM 三层记忆架构核心 - 长期记忆存储
- **数据来源**: coverage.xml（真实数据）
- **缺失行数**: 47 行
- **缺失行号**: 28, 38, 147, 221-222, 253, 260-262, 274, 285, 312, 319-321, 342, 358, 373-374, 394, 402-403, 406-407, 413, 429, 453-455, 467, 478-480, 493-495, 518-520, 524-527, 534-537

**优化建议**:
- 补充 `search()` / `search_semantic_vec_knn()` 的边界测试（空查询、维度不匹配）
- 添加 vec0 表降级路径测试（sqlite-vec 不可用时回退纯 Python）
- 补充 `_blob_to_embedding` 五种格式兼容性测试（BLOB/JSON TEXT/memoryview/str/list）
- 添加 `_normalize_vector` 零向量输入测试

## 覆盖率数据缺失模块（待测）

以下模块在 coverage.xml 中未找到，且无 L3 测试日志参考数据：

### EnvConfigManager

- **文件**: `agent/env_config_manager.py`
- **描述**: 环境配置管理 - 单例工厂（历史 P1 故障模块）
- **状态**: 待测（需运行 L3 测试获取覆盖率数据）

**重要性说明**: 此模块是历史 P1 故障模块（v1.2.1-fix-secure-manager-return），
单例工厂 return 缺失曾导致生产故障，必须优先补全覆盖率数据。

**获取覆盖率方法**:
```bash
.\scripts\run_l3_regression_tests.ps1 -Mode all -Rebuild
```

---

## 报告说明

- **数据来源**: coverage.xml（2026-07-12）+ L3 Docker 测试日志（2026-07-29）参考值
- **覆盖率计算**: `line_rate = 已覆盖行数 / 总有效行数`
- **阈值标准**: 核心模块 ≥ 80%（业务关键路径）
- **生成工具**: `scripts/generate_coverage_html_report.py`
- **三义原则**: 不易(真实数据不编造) · 变易(多数据源融合) · 简易(单一脚本双格式输出)
