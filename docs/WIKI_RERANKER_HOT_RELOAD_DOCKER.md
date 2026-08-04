# Wiki: Reranker 热重载修复 + Docker 配置优化

> 内部 Wiki 页面 · 2026-08-04 · 面向团队后续查阅与排障
> 本文整合本次热重载修复与 Docker 配置优化的**关键步骤**，详细指标见文末相关文档。

## 1. 背景与目标

- **问题**：Reranker（PyTorch 推理）在 Windows CPU 环境下触发 `0xC0000005`（ACCESS_VIOLATION）崩溃——torch/sqlite-vec 的 DLL 线程竞争 + OpenMP 线程数不受控。
- **方案**：生产迁移到 Linux Docker 容器；推理切换为 **ONNX Runtime**（C++ 引擎，无 GIL/线程问题，豁免子进程隔离约束）；并实现 **ONNX 变体热重载**（免重启切换量化模型）。

## 2. 热重载修复关键步骤（agent/skills_mgmt/reranker.py）

| 步骤 | 实现要点 | 为什么（Why） |
|------|----------|---------------|
| 1. ONNX 优先加载 | `<model_dir>/onnx/<variant>` → `ort.InferenceSession`，失败自动降级 PyTorch | jina quantized P99 258ms，30.8x 加速 |
| 2. 惰性检查 + 节流 | `_last_reload_check` 时间戳，`SKILL_RERANKER_HOT_RELOAD_INTERVAL`（默认 30s）内不重复检查，**无后台线程** | 避免每次请求扫描 env |
| 3. 锁外加载 + 锁内交换 | 新 session **在 RLock 外**构建（耗时 I/O），加载成功后**锁内**原子交换引用 | 持锁不碰 I/O（守硬约束），锁内仅保护内存状态变更 |
| 4. 失败回滚 | 新 variant 加载失败 → 保留旧 session，`_onnx_variant_loaded` 不变，记录 `_last_load_error/_last_load_traceback` | 无效 variant 不导致服务降级 |
| 5. 防无限重试 | `_onnx_variant_attempted` 记录最近尝试，失败 variant 不反复加载 | 避免错误配置下反复尝试 |

热重载切换示例：env 改 `SKILL_RERANKER_ONNX_VARIANT=model_int8.onnx` → 下次惰性检查时加载并热切换，无需重启容器。

## 3. Docker 配置优化关键步骤（docker-compose.yml）

| 步骤 | 配置 | 说明 |
|------|------|------|
| 1. 增量镜像 | `image: agent-test-sqlite-vec:hot-reload` | 基于 latest 仅 COPY 代码，替代 apt 构建（网络受限时构建卡死） |
| 2. entrypoint 补依赖 | 启动前幂等 `pip install flask waitress prometheus_flask_exporter` | 镜像缺 Web 依赖 |
| 3. 启用热重载 | `SKILL_RERANKER_ENABLED=true` + `USE_ONNX=true` | Linux 环境安全启用 |
| 4. 模型路径固定 | `SKILL_RERANKER_MODEL=/root/.cache/huggingface/...`（**不读 .env 的 Windows 路径**） | jina 模型 onnx/ 含 7 个变体，热重载依赖 |
| 5. OMP/MKL 限线程 | `OMP_NUM_THREADS=4` / `MKL_NUM_THREADS=4` | 预防 DLL 线程竞争崩溃；与 linux-test.yml 一致 |
| 6. healthcheck 覆盖 | 探测 `http://127.0.0.1:5678/api/health`，start_period 60s | 镜像自带多行 python -c 缩进错误导致 unhealthy |
| 7. 架构与信号 | `platform: linux/amd64` + `init: true` | 架构一致 + 容器内信号优雅处理，避免 SQLite WAL 残留 |

配置原则：**`.env` 为唯一数据源**，compose 用 `${VAR:-default}` 引用；所有配置修改走 .env，代码只读环境变量。

## 4. 验证步骤（每次配置变更后必跑）

```powershell
# 1) 容器健康
docker compose ps            # 期望 Up (healthy)

# 2) 完整回归（7 类 16 项）
powershell -ExecutionPolicy Bypass -File .\scripts\dev\verify_config_regression.ps1
# 期望输出: PASS: 16 FAIL: 0

# 3) 核心不变量（pre-commit/pre-push 自动执行）
python .\scripts\verify_core_invariants.py   # 期望 12/12 PASS

# 4) 三态热重载验证（详见验收报告）
#    正常加载 → 热切换 variant → 无效 variant 回滚，三态均需通过
```

当前实测基准：容器 `Up (healthy)`、容器内 OMP/MKL=4、`torch.get_num_threads()==4`、ONNX 变体 7 个（可热切换）。

## 5. 常见坑与运维要点

| 坑 | 现象 | 处理 |
|----|------|------|
| 模型路径用错 | 热重载不可用（bge 缓存无 ONNX） | 容器内必须用固定容器路径，勿复用 .env Windows 路径 |
| `git pull --rebase` / `commit -- <paths>` | docker-compose.yml 工作区被还原为旧版本 | 提交前核对 compose 仍为热重载版本；用 `git add` + 普通 `git commit` |
| 自动提交 | 工作区出现非预期新提交（后台脚本） | 属已知行为，核对内容合法后再推送 |
| 容器 unhealthy | healthcheck 误判 | 确认使用本仓库 healthcheck 覆盖（见 §3 步骤 6） |

## 6. 相关文档

| 文档 | 内容 |
|------|------|
| [环境变量对照表](CONFIG_ENV_REFERENCE.md) | 热重载 8 项 + OMP/MKL 2 项 + 分环境模型路径 + 快速核对命令 |
| [热重载最终验收报告](RERANKER_HOT_RELOAD_FINAL_ACCEPTANCE_REPORT.md) | 三态验证（加载/热切换/回滚）测试数据 |
| [部署交付清单](DEPLOYMENT_DELIVERY_CHECKLIST_20260804.md) | 已验证指标 16/16 + 待归档链接 |
| [0xC0000005 崩溃分析](TLM_0xC0000005_CRASH_ANALYSIS.md) | 崩溃根因（线程竞争）背景 |
| [ONNX 部署 Playbook](V65_ONNX_DEPLOYMENT_PLAYBOOK.md) | ONNX 推理部署全流程 |
| [回归测试脚本](../scripts/dev/verify_config_regression.ps1) | 7 类 16 项自动化校验 |
