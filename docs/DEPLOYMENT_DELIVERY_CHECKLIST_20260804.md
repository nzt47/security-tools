# 部署交付清单 — Docker 热重载 + OMP 配置（2026-08-04）

> 交付目标：docker-compose.yml 热重载配置、回归测试脚本、环境变量对照表，三者已提交并推送，远程与本地完全同步。

## 1. 交付提交

| 提交 | 内容 | 文件 | 状态 |
|------|------|------|------|
| `8a8750a8` | feat(deploy): docker-compose 热重载配置 + 回归脚本 + 环境变量对照表 | 3 files, +241/-6 | ✅ 已推送 |
| `6d46c373` | docs(ci): 修复 git_hook_ci_upgrade_summary 失效链接 | 1 file | ✅ 已推送 |
| `a2458b58` | fix(ci): 修复 1.1.4 发布链路遗留警告 + license 元数据补齐 | 5 files | ✅ 已推送 |

- 本地 `HEAD` == `origin/master` == `a2458b58`（完全同步，`git pull` → Already up to date）
- pre-commit 三关全过：文档链接 598 个 0 失效 / 锚点回归 4 passed / 核心不变量 12/12
- pre-push 不变量校验 12/12 通过

## 2. 已验证关键指标（回归测试 16/16 PASS）

| 类别 | 指标 | 实测值 | 状态 |
|------|------|--------|------|
| .env 配置 | `SKILL_RERANKER_ENABLED` / `USE_ONNX` / `ONNX_VARIANT` / `HOT_RELOAD_INTERVAL` / `OMP_NUM_THREADS` / `MKL_NUM_THREADS` | 均为生产值 | ✅ 6/6 |
| compose 透传 | `SKILL_RERANKER_MODEL` = 容器路径，非 Windows 路径 | `/root/.cache/huggingface/...` | ✅ 2/2 |
| 容器状态 | 运行 + 健康 | `Up 32 minutes (healthy)` | ✅ 2/2 |
| 容器内 env | `OMP_NUM_THREADS` / `MKL_NUM_THREADS` | 4 / 4 | ✅ 2/2 |
| 热重载代码 | reranker.py `_hot_reload` | 11 处 | ✅ 1/1 |
| ONNX 模型 | `model_quantized.onnx` 存在 + 变体数 ≥ 2 | 变体 7 个（可热切换） | ✅ 2/2 |
| OMP 生效 | `torch.get_num_threads()` | 4 | ✅ 1/1 |

回归命令：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\dev\verify_config_regression.ps1
```

## 3. Docker Compose 关键配置变更

| 配置项 | 变更 | 说明 |
|--------|------|------|
| image | `agent-test-sqlite-vec:hot-reload` | 增量镜像（含 `_hot_reload` 代码），替代原 build 方式 |
| entrypoint | 启动前幂等补装 flask/waitress/prometheus_flask_exporter | 镜像缺 Web 依赖 |
| `SKILL_RERANKER_ENABLED` | 默认 `false` → `true` | Linux Docker 环境安全启用热重载 |
| `SKILL_RERANKER_MODEL` | 固定容器路径（不读 .env） | jina 模型 onnx/ 含 7 个变体 |
| `OMP_NUM_THREADS` / `MKL_NUM_THREADS` | 4 / 4 | 预防 0xC0000005 DLL 线程竞争崩溃 |
| healthcheck | 覆盖镜像自带（缩进错误导致 unhealthy） | 探测 `/api/health`，start_period 60s |
| platform / init | linux/amd64 / init: true | 架构一致 + 信号优雅处理 |

## 4. 待归档文档链接

| 文档 | 路径 | 说明 |
|------|------|------|
| 环境变量对照表 | [docs/CONFIG_ENV_REFERENCE.md](CONFIG_ENV_REFERENCE.md) | 热重载 8 项 + OMP/MKL 2 项 + 分环境模型路径 + 快速核对命令 |
| 热重载最终验收报告 | [docs/RERANKER_HOT_RELOAD_FINAL_ACCEPTANCE_REPORT.md](RERANKER_HOT_RELOAD_FINAL_ACCEPTANCE_REPORT.md) | 三态验证（加载/热切换/回滚）结果 |
| Reranker CI 守卫最终报告 | [docs/observability/reranker_ci_guard_final_report_20260804.md](observability/reranker_ci_guard_final_report_20260804.md) | CI 集成与守卫结论 |
| 回归测试脚本 | [scripts/dev/verify_config_regression.ps1](../scripts/dev/verify_config_regression.ps1) | 7 类 16 项自动化校验，PASS/FAIL 汇总 |
| 部署配置 | [docker-compose.yml](../docker-compose.yml) | 生产部署定义 |

## 5. 风险与注意事项

- **回滚风险**：`git pull --rebase` / `commit -- <paths>` 曾导致 docker-compose.yml 工作区被还原（memory 记录），提交前需确认配置仍为热重载版本
- **自动提交**：工作区存在自动 commit + push 行为（本轮 `6d46c373`/`a2458b58` 系后台脚本产生），提交后若本地出现非预期新提交，属已知模式
- **模型路径易错**：容器内必须用容器路径，不能复用 `.env` 的 Windows 路径（bge-v2-m3 缓存无 ONNX，热重载不可用）
