# 云枢「过程蒸馏 → 工作流技能/工作流分层固化」最终交付报告（2026-09-05）

> 本报告汇总本交付轮（2026-09-05，对应推送 `eb49ab77` + 修复 `4fd73160`）全部成果：
> 让云枢自身具备「自动用子代理从知识库/素材阅读并蒸馏出可复现步骤序列，再固化为一
> 次性可执行的 workflow 脚本或长期复用的 Skill」的完整闭环能力，并完成
> **工作流技能（免 LLM 0-Token）与工作流（可按需混合 LLM）的彻底分层**。
> 供 stakeholders 最终验收与结案。

---

## 1. 项目概述

**原始诉求**：用户询问"工作流技能能否通过知识库导入其他 agent 编程过程从而形成工作流"，
并明确目标：**让云枢（Yunshu）本身拥有这种能力** —— 自动用子代理从知识库阅读并总结出
可复现的步骤序列，再把这套步骤固化成一个 workflow 脚本（一次性执行）或一个
Skill / agent preset（长期复用）。

**交付落点**：改造云枢产品代码（agent/ 后端 + yunshu-ui 前端），知识库素材源为
`agent/knowledge/` 目录（wiki/复盘/SKILL.md 均可）。

**最终分层定稿（A1+B）**：

| 资产 | 存放 | 执行方式 | Token |
|---|---|---|---|
| 工作流技能 | `data/learned_workflows.json`（LearnedWorkflow，DAG ≤10 步） | 工具执行器驱动，`skipped_llm=True` | **免 LLM（0-Token）** |
| 工作流（混合） | 同上，`workflow_type=hybrid`，允许 `need_llm` 步骤 | DAG 步级执行 + 步骤级 LLM runner（惰性注入） | 仅 need_llm 步骤消耗 |
| LLM 技能 | skills_mgmt 主轨 + `skills_repo/<id>/skill.md` 文件轨 | 语义层召回，正文注入 | LLM 侧 |

「导出为 LLM 参考」= 把工作流正文（markdown 步骤叙述）拷贝为 LLM 技能资产，
**不替换、不删除原工作流**（A2 方案已试并回退，见 §6）。

## 2. 交付范围与进度

| 阶段 | 内容 | 状态 | 结案 |
|---|---|---|---|
| 蒸馏管线 | `agent/process_distill/` 9 文件：sources → distiller(并行子代理) → merge → solidify | ✅ | 2026-09-05 |
| 二轮增强 | HTTP 路由 `/api/process-distill/*`、异步化、批量导入、LLM 稳定性（重试） | ✅ | 2026-09-05 |
| 工作流技能/工作流分层 | workflow_type=toolchain/hybrid + 步级 LLM + 冷启动死锁修复 | ✅ | 2026-09-05 |
| 自动清理 | `skills_mgmt/cleanup.py` 六轨清理 + 孤儿扫描 + 无用淘汰 + 调度（默认关） | ✅ | 2026-09-05 |
| legacy 迁移 | `data/skills.json` 合并进主轨；SkillRegistry 统一视图（主轨 JSON + 文件轨 front matter） | ✅ | 2026-09-05 |
| 面板三按钮修复 | 工作流技能面板执行/复制/删除 HTTP 500 清零 | ✅ | 2026-09-05 |
| 资产入库 | 15 个 `pd-*` 蒸馏方法论技能（双轨）+ 演示 workflow（`learned_workflows.json`） | ✅ | 2026-09-05 |
| CI 修复 | 架构规则校验循环依赖违规修复（PEP 562 懒加载） | ✅ | 2026-09-05（4fd73160） |
| **最终交付** | 推送双远端 + CI 验证 + 本报告 | 🔄 本报告 | 2026-09-05 |

## 3. 关键成果指标

| 指标 | 数值 |
|---|---|
| 蒸馏能力模块 | `agent/process_distill/` 9 文件（含懒加载 `__init__`），3 个主循环工具 + 2 个 HTTP 路由 |
| 固化产物 | workflow（LearnedWorkflow，`toolchain` 免 LLM）+ skill（双轨入库）双通道 |
| 会话相关单测 | **328 passed / 0 failed / 1 skipped**（14 个测试文件，Windows PYTHONUTF8=1） |
| 本地架构校验 | 7 规则 0 未豁免违规（修复后） |
| 技能清理轨道 | 6 轨（主轨/legacy×2/文件轨/classes/digest/extensions） |
| 蒸馏资产 | `data/skills_repo/pd-*.skill.md` × 15（方法论，双轨、已审核） |
| 变更规模 | 74 文件 +8178/-485 行（相对 `a744f179`），7 个提交 |
| 双远端同步 | origin（GitHub）+ gitee 均至 `4fd73160` |

## 4. 遇到的问题与解决方案

| 问题 | 根因 | 解决方案 |
|---|---|---|
| DeepSeek 端点间歇空响应（~25-40%） | 部分内容触发空返回 | distiller `_LLM_RETRIES=3` 指数退避重试 + `extract_text_steps` 规则兜底 |
| 工作流学习死锁（技能永不沉淀） | learner 初始置信度 0.3 < matcher 阈值 0.4，且 record_execution 对数公式递减 | 初值 0.4、相对演化、冷启动（success_count=0）置信系数 1.0 |
| 工作流技能面板三按钮 HTTP 500 | 全局 workflow service 未注入 tool_executor；convert 质量门异常未捕获 | `_svc()` 惰性注入 tool_executor + step-LLM runner；`except WorkflowConvertError → 400 QUALITY_GATE_FAILED` |
| UI 残留技能清理不掉 | legacy `data/skills.json` 与 `agent/data/extensions.json`（ExtensionStore）长期未清；运行中进程持陈旧内存态 | 六轨 `remove_skill_everywhere` 全清 + 后端重启加载 |
| A2（Skill 引用工作流）混同 | 与"工作流技能≠LLM技能"诉求冲突 | 回退 A1+B：两 Tab 分层，convert = 「导出为 LLM 参考」（markdown 拷贝） |
| CI 架构规则校验失败（未豁免 1 项） | `process_distill/__init__.py` 顶层 import service，service 反向 `from agent.process_distill import sources` 构成包级循环 | `__init__` 改 PEP 562 懒加载（与 skills_mgmt 同款），公开 API 不变；本地校验 0 违规、CI 转绿 |
| `.env` 未自动加载 | 独立脚本缺环境 | service `_ensure_env_loaded()`（复用 env_config_manager） |

## 5. 验证证据

**单元/集成测试**（Windows 本机，PYTHONUTF8=1，DISABLE_NATIVE_EXT=1）：
- 会话相关 14 文件：**328 passed / 0 failed / 1 skipped**（skip 为 `--runslow` 慢速项）
- 蒸馏相关最小集 37 passed；全量收集 214 用例基线通过

**架构**：`arch_rules --check` 本地 7 规则 **0 未豁免违规**；CI「架构规则校验」@ `4fd73160` ✅

**CI/CD**（GitHub Actions，@ `4fd73160`，2026-09-05/06 实测）：
**推送触发的 13 个 workflow 全部 success，0 失败**：
- 云枢系统测试流程（后端全量 pytest，Linux）✅
- 可观测性质量保障 ✅ · Error Reporting System CI/CD ✅ · 日志性能守护 ✅
- 架构规则校验 ✅（首轮失败 → `4fd73160` 修复后转绿）
- kwarg 扫描 → SonarQube ✅ · 关键字参数冲突扫描 (Docker) ✅
- lock-discipline-scan ✅ · 循环依赖校验 ✅ · 硬编码密码扫描（全分支）✅
- 核心不变量监控 ✅ · master commit 来源守卫 ✅ · 环境健康检查与工作区守卫 ✅

**端到端**（详见 `docs/zh/过程蒸馏能力交付总结_20260905.md` §三/§7.6/§7.7）：临时仓库蒸馏 wiki → 固化为 workflow + skill；`.superpowers/skills` 批量导入实测；评审-消化闭环"没内容"修复后正文可见。

## 6. 遗留问题与处理结论

| 遗留项 | 处理 |
|---|---|
| A2 方案（Skill 引用工作流执行） | 用户确认回退，A1+B 为定稿（§7.13 详细文档） |
| 技能清理调度器 | 默认关闭 + dry-run 保守上线（config `skills_mgmt.cleanup`），可按需开启 |
| 后端本地进程 | 收尾期间已停止；如需人工验收 UI 可重启（冷启动 60-90s） |
| 远程新增提交 | 已 rebase 吸收（含 `c8949eca` 依赖图自动提交），无分叉 |

## 7. 提交记录

```
4fd73160 fix(process_distill): __init__ 改 PEP 562 懒加载，消除包级循环依赖（CI 修复）
eb49ab77 chore(skills): 蒸馏技能资产入库 + 演示 workflow + 交付文档
659b3e2d feat(skills): 接线 skills-mgmt 路由与清理调度 + config cleanup 配置
ff1687e7 fix+feat(workflow): 免 LLM 工作流冷启动死锁修复 + 工作流技能/LLM 技能分层
e74b0d61 feat(skills): 自动清理能力 + legacy 统一注册表迁移
715c7fca feat(process-distill): 知识库→子代理蒸馏→workflow/skill 固化能力
287bd869 feat(skills): 技能中心内容查看/Markdown 批量导入/写令牌修复 + 吸收优先
```

## 8. 关键文件索引

- 详细交付文档：`docs/zh/过程蒸馏能力交付总结_20260905.md`（§7.1–§7.13）
- 蒸馏能力：`agent/process_distill/`、`agent/server_routes/routes_process_distill.py`
- 分层与执行：`agent/workflow_learning/`（learner/matcher/executor/service/skill_converter）
- 清理与注册表：`agent/skills_mgmt/`（registry/cleanup/cleanup_scheduler/service/store）
- 资产：`data/skills_repo/pd-*.skill.md` ×15、`data/learned_workflows.json`（演示 workflow）
- 脚本：`scripts/demo_process_distill.py`、`scripts/distill_superpowers.py`、`scripts/seed_demo_workflows.py`

---

## 9. 最终结案状态

| 项 | 状态 |
|---|---|
| 代码推送 | origin（GitHub）+ gitee 均同步至 `4fd73160` |
| CI/CD 验证 | 13/13 push workflow 全绿（含云枢系统测试流程全量 pytest） |
| 本地测试 | 会话相关 328 passed / 0 failed / 1 skipped |
| 架构校验 | 本地 + CI 均 0 未豁免违规 |
| 双轨资产 | 15 个 `pd-*` 技能（双轨入库）+ 演示 workflow 已入库 |
| 遗留问题 | 全部处理（见 §6）；后端本地进程未启动，如需人工验收 UI 可重启 |
| 结案 | ✅ 本交付轮结案（2026-09-05/06） |

**验收入口**：重启后端（`python app_server.py`，冷启动 60-90s）后，打开技能中心：
「工作流技能」Tab = 免 LLM 0-Token 可执行 workflow；「LLM 技能」Tab = 语义层技能；
对话内输入"把 .superpowers/skills 里的方法论蒸馏成我的技能"即可走蒸馏闭环
（详见 `docs/zh/过程蒸馏能力交付总结_20260905.md` §五 使用示例）。
