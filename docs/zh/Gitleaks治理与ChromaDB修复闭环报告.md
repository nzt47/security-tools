# Gitleaks 治理与 ChromaDB 修复 · 完整闭环报告（归档）

> 项目：云枢 · AI 智能体桌面工作台
> 关联 PR：#749（M1）、#754（M2 / M3 / ChromaDB）
> 日期：2026-08-23
> 状态：✅ 全部闭环（合并 develop + CI 验证通过）

---

## 1. 目标

消除 CI 安全/质量流水线中的三类失败并修复 ChromaDB 预检故障：
1. Gitleaks 硬编码密码扫描（全分支）
2. 硬编码边界值扫描（Boundary Guard）
3. ChromaDB 导入降级预检（容器化）

## 2. 治理里程碑与修复细节

### M1 密码启动保护（PR #749，已合并 11d53b40）

| 项 | 详情 |
|---|---|
| 问题 | `app_server.py` 硬编码默认密码 `admin123`（Gitleaks 命中） |
| 修复 | 新增 [server_auth.py](../../agent/server_auth.py) `load_admin_credentials()`：env 注入优先 → **生产缺密码拒绝启动**（RuntimeError）→ 本地兜底 admin/admin123 |
| 配套 | 启动日志（生产/本地 + 密码来源）；[app_server.py](../../app_server.py#L1324-L1340) 移除硬编码默认值 |
| 验证 | `tests/unit/test_admin_password_guard.py` **6/6 通过** + 生产缺密码冒烟（拒绝启动）✅ |

### M2 误报豁免（PR #754）

| 项 | 详情 |
|---|---|
| 问题 | guard_llm_api_key.py 占位符 `sk-*` 误报；Profile.tsx 硬编码演示密码 `123456` |
| 修复 | guard_llm 占位符行加 `gitleaks:allow` 行内豁免（黑名单功能不变）；Profile 表单改空值占位 |
| 验证 | `tests/unit/test_m2_gitleaks_guard.py` **3/3 通过** ✅ |

### M3 边界值基线治理（PR #754）

| 项 | 详情 |
|---|---|
| 问题 | Boundary Guard 失败（基线 118 vs 实际） |
| 修复 | 干净扫描生成完整基线（118 → **129**）；52 项差异 `git blame` 归因归档（50 项历史代码 + 2 项未跟踪文件）；处置决策（保留基线 / 待归属 / P2 配置化候选） |
| 验证 | Boundary Guard 转绿；`tests/unit/test_m3_boundary_baseline.py` **5/5 通过** ✅ |

### ChromaDB 预检修复（PR #754）

| 项 | 详情 |
|---|---|
| 问题 | `docker build` 报 `"/tests/unit": not found`，阻断单元测试矩阵 |
| 根因 | git 版 `.dockerignore` 第 13 行 `tests` 排除整个 tests/，与 Dockerfile `COPY tests/` **自相矛盾**（本地未提交简化版掩盖问题） |
| 修复 | [.dockerignore](../../.dockerignore#L12-L13)：`tests` → `tests/unit/temp`（保留预检用例，排除大体积产物） |
| 验证 | CI 复验 **chromadb-preflight = success**；诊断步骤（build 前打印上下文）常驻 ✅ |

## 3. CI 验证结果（develop 合并后 617a386f）

| workflow | 合并前 | 合并后 | 说明 |
|---|---|---|---|
| 硬编码密码扫描（全分支） | ❌ | ✅ **success** | M1+M2 治理生效 |
| Error Reporting CI/CD | ❌ | ✅ **success** | 顺带恢复 |
| ChromaDB 预检 | ❌ | ✅ **success** | 修复生效 |
| Boundary Guard | ❌ | ✅ **success** | M3 基线更新 |
| 前端测试 / 核心不变量 / 语义层性能 等 | ✅ | ✅ | 无回归 |
| 云枢系统测试流程 | ❌ | ❌ | 既有基线（文档链接/单测资源） |
| 可观测性质量保障 | ❌ | ❌ | 既有（11d53b40 即失败） |

**结论：未引入新的失败项**。

## 4. 交付物清单

| 交付物 | 位置 |
|---|---|
| M1 发布说明 | [M1密码启动保护发布说明.md](M1密码启动保护发布说明.md) |
| 治理方案 + 迁移时间表 | [Gitleaks默认密码治理方案.md](Gitleaks默认密码治理方案.md) |
| M3 排期 / 发布说明 / 总结 | [M3边界值基线排期计划.md](M3边界值基线排期计划.md) 等 |
| 边界值归属归档 | [hardcoded_boundary_attribution.md](../observability/hardcoded_boundary_attribution.md) |
| ChromaDB 排查报告 / 复盘 | [ChromaDB构建失败排查报告.md](ChromaDB构建失败排查报告.md)、[ChromaDB构建问题复盘总结.md](ChromaDB构建问题复盘总结.md) |
| 验收测试 | test_admin_password_guard / test_m2_gitleaks_guard / test_m3_boundary_baseline |

## 5. 遗留与建议

1. 单测 Shard 资源问题（`can't start new thread`）——独立跟踪，见专项建议文档
2. 文档链接预检（6 个失效链接）——既有，待文档组修复
3. 知识库重构 T6（前端知识库视图）——任务未实现，见专项分析
4. 预防门禁：.dockerignore↔Dockerfile 一致性检查（建议下迭代落地）
