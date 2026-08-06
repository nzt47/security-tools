# 发布日志 — ChromaDB 导入降级预检工具包 v1.0.0-preflight

| 项 | 值 |
|----|----|
| **版本标签** | `v1.0.0-preflight` |
| **关联提交** | `6c83fb32`（master） |
| **发布日期** | 2026-08-06 |
| **测试基线** | 114 passed / 0 failed（4.63s） |
| **覆盖率** | 100%（agent.knowledge + agent.preflight，269 stmts） |

---

## 背景

chromadb 1.5.9 + pydantic_settings 在部分环境（含本地 Windows）存在 **daemon 线程持解释器 import 锁死锁**：旧实现用 `daemon 线程 + join(30s)` 超时导入 chromadb，超时后卡死线程仍持有全局 import 锁，后续任何 `import chromadb.errors` 都会死锁，pytest `--timeout=60` 直接超时。

本工具包以「**子进程探测 + terminate() 超时控制**」根除该隐患，并将验证逻辑沉淀为可复用的预检工具包。

## 核心功能

### 1. 统一入口 `python -m agent.preflight`
- CLI 与 pytest 共用同一 `run_preflight()` 实现（单事实源，消除 demo/pytest 双份断言）
- 12 条路径检查全 mock，不依赖真实 chromadb（含 30s 超时模拟场景）
- 退出码约定：`0` = 全部通过；`1` = 任一失败或故障演练触发

### 2. 三段式导入（memory_optimized._create_client）
- **子进程探测** → 主进程导入 → **Mock 兜底**（MockChromaClient）
- 模块级缓存 `_CHROMADB_IMPORT_OK`：首次探测后不再重复起子进程
- 4 条决策日志：`probe_start → probe_ok → ready|chromadb|client_failed|timeout`

### 3. 轻量容器化（Dockerfile）
- `python:3.12-slim` + pytest，仅 COPY `agent/` + `tests/`，**零重依赖**（不装 torch/chromadb 约 3GB 依赖）
- `.dockerignore` 排除 memory/data/sandbox/scripts/docs

### 4. CI 阻断语义（ci.yml `chromadb-preflight` job）
- preflight job 失败 → 下游 6 shard 单元测试矩阵全部 `Skipped`（`needs` 阻断）
- 本地 Windows 与 CI Linux 同路径（`DISABLE_NATIVE_EXT=1` 屏蔽 native 扩展）

### 5. 运维配套
- `scripts/view_chromadb_logs.ps1`：按 action 过滤决策日志，一眼看清 `_create_client` 走了哪条分支
- `scripts/chromadb_preflight.sh/.ps1`：薄壳入口（CLI + pytest）
- `scripts/run_unit_tests.ps1`：一键全量单元测试

## 故障演练场景

| 场景 | 注入方式 | 预期行为 |
|------|----------|----------|
| **CI 阻断** | `PREFLIGHT_FAKE_FAIL`（任意非空值） | 预检返回 1，CI 中 unit-tests 被 needs 阻断跳过 |
| **子进程 30s 超时** | mock `subprocess.run` 抛 `TimeoutExpired` | 真实 `_probe_import` 返回 False → 全链路降级 MockChromaClient |
| **探测不可用** | `_probe_import` 返回 False | 直接 MockChromaClient，不进入主进程导入 |
| **主进程 import 失败** | `builtins.__import__` 抛 ImportError | MockChromaClient |
| **Settings 导入失败** | `chromadb.config` 抛 ImportError | MockChromaClient |
| **客户端创建失败** | `PersistentClient` 抛 RuntimeError | MockChromaClient |
| **子进程启动失败** | `subprocess.run` 抛 OSError | 探测 False + 写入缓存 |

## 缺陷回归记录（本次发布修复）

1. chromadb daemon 线程持 import 锁死锁 → 子进程探测 + terminate + 缓存（根因修复）
2. `view_chromadb_logs.ps1` 引用已删除 demo 脚本 → 改为 `python -m agent.preflight`
3. `PREFLIGHT_FAKE_FAIL` 环境变量残留污染 pytest 子进程 → `_run_cli()` 显式剔除
4. 覆盖率 94% → 100%：补齐 `--verbose`/失败输出/`python -m` 入口/防御分支
5. 文档相对链接深度错误（`../` 应为 `../../../`）→ 10 处修复，链接预检通过

## 复现命令

```powershell
# 全量验证（114 用例 + 覆盖率）
python -m pytest tests/unit -q --cov=agent.knowledge --cov=agent.preflight --cov-report=term-missing

# 预检 CLI（12 条路径）
python -m agent.preflight

# 故障演练（模拟 CI 阻断）
$env:PREFLIGHT_FAKE_FAIL = "1"; python -m agent.preflight; Remove-Item Env:PREFLIGHT_FAKE_FAIL

# 决策日志过滤
.\scripts\view_chromadb_logs.ps1 -Filter "probe"
```

## 关联文档

- [测试报告_v1.0.0.md](测试报告_v1.0.0.md) — 完整测试范围与验证矩阵
- [CI_预检工具集成指南.md](CI_预检工具集成指南.md) — GitHub Actions 配置与故障演练步骤
- [任务0_核心逻辑速查.md](任务0_核心逻辑速查.md) — knowledge 契约层速查
