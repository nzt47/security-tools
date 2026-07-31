# Reranker 热重载机制部署操作手册

> 生成时间：2026-07-31
> 适用版本：v6.5 SkillReranker（含热重载机制）
> 关联代码：[reranker.py](../agent/skills_mgmt/reranker.py) | [monitor_reranker_neg_sample.py](../scripts/monitor_reranker_neg_sample.py) | [start_reranker_monitor.ps1](../scripts/start_reranker_monitor.ps1)
> 关联测试：[test_reranker_hot_reload.py](../tests/unit/test_reranker_hot_reload.py) | [test_hot_reload_stability.py](../scripts/test_hot_reload_stability.py)
> 前置文档：[RERANKER_INT8_DEPLOYMENT_GUIDE.md](./RERANKER_INT8_DEPLOYMENT_GUIDE.md)

---

## 目录

1. [机制概述](#一机制概述)
2. [部署前检查](#二部署前检查)
3. [环境变量配置](#三环境变量配置)
4. [启动流程](#四启动流程)
5. [热重载验证](#五热重载验证)
6. [实时监控](#六实时监控)
7. [故障回滚方案](#七故障回滚方案)
8. [CI/CD 集成](#八cicd-集成)
9. [常见问题排查](#九常见问题排查)

---

## 一、机制概述

### 1.1 设计目标

支持 **`SKILL_RERANKER_ONNX_VARIANT`** 配置变更后**无需重启进程**即可生效，解决以下痛点：

| 传统方式 | 热重载方式 |
|---------|-----------|
| 修改 `.env` → 重启 app_server（30s+ 服务中断）| 修改 `.env` → 自动检测切换（≤30s 延迟，0 中断）|
| 切换期间请求全部降级到 RRF | 切换期间请求继续用旧会话推理 |
| 失败需手动回滚 `.env` + 重启 | 失败自动回滚，旧会话保留 |

### 1.2 工作原理

```
┌─────────────────────────────────────────────────────────┐
│  rerank() 调用入口                                       │
│      ↓                                                   │
│  _check_hot_reload()                                     │
│      ├─ 节流检查（30s 间隔）                              │
│      ├─ 读取 .env mtime                                  │
│      ├─ mtime 未变 → 返回                                │
│      └─ mtime 变化 → 重读 SKILL_RERANKER_ONNX_VARIANT   │
│          ├─ variant 未变 → 仅更新 mtime                  │
│          └─ variant 变化 → _hot_reload_onnx_variant()   │
│              ├─ RLock 加锁                               │
│              ├─ 保存旧 session/tokenizer 引用           │
│              ├─ 调用 _load_onnx() 加载新 variant        │
│              ├─ 成功 → 原子替换指针 + 更新 variant_loaded │
│              └─ 失败/异常 → 回滚旧会话 + 告警            │
└─────────────────────────────────────────────────────────┘
```

### 1.3 安全保证

| 场景 | 行为 |
|------|------|
| 新 variant 文件不存在 | 保留旧会话，记录 `hot_reload.failed_rollback` |
| 新 variant 加载异常 | 保留旧会话，记录 `hot_reload.exception_rollback` |
| 热重载期间并发推理 | RLock 保护指针替换，推理无锁安全（Python 引用计数） |
| `.env` 文件被删除 | `_get_env_mtime` 返回 0，静默跳过 |
| 节流期内多次修改 | 仅首次触发检查，后续跳过 |

---

## 二、部署前检查

### 2.1 模型文件清单

热重载要求**两个 variant 文件同时存在**于模型目录的 `onnx/` 子目录下：

| 文件 | 路径示例 | 用途 |
|------|---------|------|
| INT8 量化 | `<model_dir>/onnx/model_quantized.onnx` | 生产默认（P99 487ms） |
| FP32 原始 | `<model_dir>/onnx/model.onnx` | 高精度备选（P99 1080ms） |

**验证命令**：
```powershell
$modelDir = "C:/Users/Administrator/.cache/huggingface/hub/models--BAAI--bge-reranker-base"
Get-ChildItem "$modelDir/onnx" -Filter "*.onnx" | Select-Object Name, @{N='SizeMB';E={[math]::Round($_.Length/1MB,1)}}
```

预期输出（两个文件都存在）：
```
Name                    SizeMB
----                    ------
model.onnx              1061.0
model_quantized.onnx     266.0
```

### 2.2 依赖版本

```
onnxruntime>=1.16.0          # ONNX 推理引擎
transformers>=4.30.0          # Tokenizer 加载
```

### 2.3 测试基线

部署热重载前，确认现有测试全部通过：
```powershell
$env:SKILLS_OFFLINE="1"
python -m pytest tests/unit/test_reranker.py tests/unit/test_reranker_hot_reload.py -v
```

预期：**47 passed**（33 原有 + 14 热重载）。

---

## 三、环境变量配置

### 3.1 热重载相关配置（`.env`）

```ini
# ── Reranker 热重载 ──────────────────────────────────────
# 监听的 .env 文件路径（默认 ./\.env）
SKILL_RERANKER_ENV_FILE=.env

# mtime 轮询间隔秒数（默认 30）
# 越小检测越快但文件系统开销越大；生产建议 30，调试可设 5
SKILL_RERANKER_HOT_RELOAD_INTERVAL=30
```

### 3.2 完整生产配置示例

```ini
# ========================================
# v6.5 Skill Reranker 完整配置
# ========================================

# 基础配置
SKILL_RERANKER_ENABLED=true
SKILL_RERANKER_MODEL=C:/Users/Administrator/.cache/huggingface/hub/models--BAAI--bge-reranker-base
SKILL_RERANKER_USE_ONNX=true

# 当前 ONNX 变体（热重载切换目标）
# 可选值：model_quantized.onnx（INT8，默认）/ model.onnx（FP32）
SKILL_RERANKER_ONNX_VARIANT=model_quantized.onnx

# 超时与阈值
SKILL_RERANKER_TIMEOUT=30
SKILL_RERANKER_RERANK_TIMEOUT=3.0
SKILL_RERANKER_MIN_SCORE=0.05

# 热重载配置
SKILL_RERANKER_ENV_FILE=.env
SKILL_RERANKER_HOT_RELOAD_INTERVAL=30
```

### 3.3 配置优先级

```
显式 shell 环境变量（最高）
    ↓
.env 文件中的值
    ↓
reranker.py 内置默认值（最低）
```

**注意**：热重载**仅监听 `.env` 文件变化**，不监听 shell 环境变量变化（Windows 进程无法被外部修改环境变量）。

---

## 四、启动流程

### 4.1 一键启动（推荐）

```powershell
.\scripts\start_reranker_monitor.ps1
```

**执行流程**：
1. 加载 `.env` 到当前进程环境变量
2. 打印当前生效的 `SKILL_RERANKER_*` 配置摘要
3. 后台启动 `app_server.py`（stderr → `reranker.log`）
4. 前台启动 `monitor_reranker_neg_sample.py` 实时监控
5. `Ctrl+C` 退出时自动清理 app_server 子进程

### 4.2 分离启动（生产环境）

```powershell
# 终端 1：启动 app_server
python app_server.py 2> reranker.log

# 终端 2：启动监控
python scripts/monitor_reranker_neg_sample.py --log reranker.log
```

### 4.3 启动后验证

```powershell
# 1. 确认 app_server 已监听
Get-NetTCPConnection -LocalPort 5678 -State Listen

# 2. 触发一次 rerank 调用（通过 Web 界面或 curl）
curl http://127.0.0.1:5678/api/skills/match -X POST -d '{"query":"反思"}'

# 3. 检查日志确认热重载机制已激活
Select-String -Path reranker.log -Pattern "hot_reload|rerank.completed" | Select-Object -Last 5
```

预期日志包含 `tokenize_ms` 和 `inference_ms` 字段：
```json
{"action":"rerank.completed","duration_ms":487.2,"tokenize_ms":45.3,"inference_ms":421.8,...}
```

---

## 五、热重载验证

### 5.1 标准切换流程（INT8 → FP32）

**步骤 1**：确认当前 variant
```powershell
Select-String -Path .env -Pattern "SKILL_RERANKER_ONNX_VARIANT"
# 输出：SKILL_RERANKER_ONNX_VARIANT=model_quantized.onnx
```

**步骤 2**：修改 `.env`
```powershell
# 用任意编辑器修改 .env
notepad .env
# 将 SKILL_RERANKER_ONNX_VARIANT=model_quantized.onnx
# 改为 SKILL_RERANKER_ONNX_VARIANT=model.onnx
```

**步骤 3**：触发 rerank 调用（≤30s 内自动检测）

通过 Web 界面发送请求，或：
```powershell
1..5 | ForEach-Object {
    curl -s http://127.0.0.1:5678/api/skills/match -X POST -d '{"query":"测试查询"}' | Out-Null
    Start-Sleep -Milliseconds 500
}
```

**步骤 4**：验证切换成功
```powershell
Select-String -Path reranker.log -Pattern "hot_reload"
```

预期输出：
```
{"action":"hot_reload.detected","old_variant":"model_quantized.onnx","new_variant":"model.onnx",...}
{"action":"hot_reload.success","old_variant":"model_quantized.onnx","new_variant":"model.onnx"}
```

**步骤 5**：确认新 variant 生效
```powershell
# 检查后续 rerank 日志的 inference_ms（FP32 应显著大于 INT8）
Select-String -Path reranker.log -Pattern "rerank.completed" | Select-Object -Last 3
```

预期：`inference_ms` 从 ~420ms（INT8）变为 ~1000ms（FP32）。

### 5.2 自动化验证脚本

```powershell
# 运行单元测试（无需启动 app_server）
$env:SKILLS_OFFLINE="1"
python -m pytest tests/unit/test_reranker_hot_reload.py -v
```

预期：**14 passed**。

```powershell
# 运行稳定性测试（模拟并发修改 .env + 并发 rerank）
python scripts/test_hot_reload_stability.py --duration 60 --concurrency 8
```

预期输出：
```
[OK] 热重载稳定性测试通过
  总 rerank 调用: 480
  热重载触发: 12 次
  成功切换: 10 次
  失败回滚: 2 次（模拟 variant 不存在）
  并发冲突: 0 次
  总耗时: 60.5s
```

### 5.3 故意触发回滚（验证安全机制）

**步骤 1**：修改 `.env` 指向不存在的 variant
```powershell
# 修改 .env
SKILL_RERANKER_ONNX_VARIANT=nonexistent.onnx
```

**步骤 2**：触发 rerank
```powershell
curl http://127.0.0.1:5678/api/skills/match -X POST -d '{"query":"测试"}'
```

**步骤 3**：验证回滚
```powershell
Select-String -Path reranker.log -Pattern "hot_reload.failed_rollback"
```

预期：
```json
{"action":"hot_reload.failed_rollback","target_variant":"nonexistent.onnx","kept_variant":"model_quantized.onnx","reason":"new_variant_load_failed"}
```

**步骤 4**：确认服务未中断
```powershell
# 后续请求仍正常响应（用旧 variant）
curl http://127.0.0.1:5678/api/skills/match -X POST -d '{"query":"反思"}'
```

---

## 六、实时监控

### 6.1 启动监控

```powershell
# 方式 1：随 app_server 一起启动（推荐）
.\scripts\start_reranker_monitor.ps1

# 方式 2：独立启动（app_server 已运行）
.\scripts\start_reranker_monitor.ps1 -Mode monitor

# 方式 3：一次性报告
.\scripts\start_reranker_monitor.ps1 -Mode report
```

### 6.2 监控指标解读

| 指标 | 含义 | 告警阈值 |
|------|------|---------|
| `negative_ratio` | 负样本占比 | >30% WARNING，>50% CRITICAL |
| `p99_duration_ms` | P99 延迟 | >500ms CRITICAL |
| `avg_stddev` | 分数区分度 | <0.05 关注（区分度不足） |
| `tokenize_ms` | 分词耗时 | >100ms 关注 |
| `inference_ms` | 推理耗时 | >450ms 关注（INT8） |

### 6.3 告警示例

```
[14:32:15] 🔴 CRITICAL | P99 650ms > SLO 500ms，检查并发或切换更轻量模型
  窗口: 100 | 负样本: 5 (5.0%)
  P99: 650ms | 平均: 320ms
  avg_stddev: 0.1500 | 累计: 100 请求, 5 负样本
```

**处置流程**：
1. 检查 `tokenize_ms` vs `inference_ms` 定位瓶颈
2. 若 `inference_ms` 高 → 修改 `.env` 切换到更轻量 variant（如 INT8）
3. 等待 ≤30s 热重载生效
4. 观察监控 P99 是否下降

---

## 七、故障回滚方案

### 7.1 回滚决策矩阵

| 故障场景 | 影响 | 回滚方式 | 预计恢复时间 |
|---------|------|---------|------------|
| 新 variant 加载失败 | 服务无影响（自动回滚）| 自动 | 0s（即时） |
| 新 variant 性能劣化 | P99 升高 | 修改 `.env` 改回旧 variant | ≤30s |
| 热重载机制异常 | 服务无影响（停用热重载）| 设置 `SKILL_RERANKER_HOT_RELOAD_INTERVAL=999999` | 即时 |
| app_server 崩溃 | 服务中断 | 重启进程 | 10-30s |
| `.env` 损坏 | 配置丢失 | 从备份恢复 | 1-5min |

### 7.2 自动回滚（无需人工干预）

热重载机制内置自动回滚，**无需操作**。触发条件：
- 新 variant 文件不存在
- 新 variant 加载抛异常
- `_load_onnx()` 返回 False

验证日志：
```powershell
Select-String -Path reranker.log -Pattern "hot_reload.(failed_rollback|exception_rollback)"
```

### 7.3 手动回滚（性能劣化场景）

**场景**：切换到 FP32 后 P99 超 SLO，需切回 INT8。

**步骤 1**：修改 `.env`
```powershell
notepad .env
# 将 SKILL_RERANKER_ONNX_VARIANT=model.onnx
# 改回 SKILL_RERANKER_ONNX_VARIANT=model_quantized.onnx
```

**步骤 2**：触发 rerank（≤30s 自动切换）
```powershell
1..5 | ForEach-Object {
    curl -s http://127.0.0.1:5678/api/skills/match -X POST -d '{"query":"回滚测试"}' | Out-Null
    Start-Sleep -Milliseconds 500
}
```

**步骤 3**：验证回滚成功
```powershell
Select-String -Path reranker.log -Pattern "hot_reload.success" | Select-Object -Last 1
# 预期：new_variant 为 model_quantized.onnx
```

### 7.4 紧急停用热重载

**场景**：热重载机制本身异常，需立即停用。

```powershell
# 方式 1：修改 .env（推荐，无需重启）
# 在 .env 中添加：
# SKILL_RERANKER_HOT_RELOAD_INTERVAL=999999

# 方式 2：设置环境变量（需重启 app_server 生效）
$env:SKILL_RERANKER_HOT_RELOAD_INTERVAL="999999"
# 然后重启 app_server
```

**验证停用**：
```powershell
# 修改 .env 中 variant，触发 rerank，确认不出现 hot_reload 日志
Select-String -Path reranker.log -Pattern "hot_reload.detected" | Select-Object -Last 1
# 应无新增（间隔 999999s = 11.5 天，等同停用）
```

### 7.5 完全回滚到旧版本代码

**场景**：热重载机制引入问题，需回滚 reranker.py 源码。

```powershell
# 1. 查看提交历史
git log --oneline agent/skills_mgmt/reranker.py | Select-Object -First 10

# 2. 回滚到热重载引入前的版本
git log --oneline --grep="hot_reload" agent/skills_mgmt/reranker.py
# 找到引入热重载的 commit，回滚到其父 commit：
git revert <commit-hash>

# 3. 重启 app_server
# Ctrl+C 停止当前 app_server，重新启动
python app_server.py 2> reranker.log
```

---

## 八、CI/CD 集成

### 8.1 自动化测试 job

CI/CD 流水线已新增 `reranker-hot-reload` job，每次提交自动验证：

- 日志优化字段（`tokenize_ms` / `inference_ms`）
- 热重载机制（mtime 检测、variant 切换、失败回滚）
- 稳定性测试（并发 rerank + 并发 .env 修改）

查看流水线：
```powershell
gh run list --workflow=ci-cd.yml --limit 5
gh run view <run-id> --log --job=reranker-hot-reload
```

### 8.2 本地复现 CI 测试

```powershell
# 与 CI 完全一致的测试命令
$env:SKILLS_OFFLINE="1"
$env:PYTHONIOENCODING="utf-8"
python -m pytest tests/unit/test_reranker.py tests/unit/test_reranker_hot_reload.py -v --tb=short
python scripts/test_hot_reload_stability.py --duration 30 --concurrency 4 --ci-mode
```

### 8.3 CI 失败排查

若 `reranker-hot-reload` job 失败：

1. **查看失败日志**：
   ```powershell
   gh run view <run-id> --log-failed
   ```

2. **本地复现**：用 8.2 节命令本地运行

3. **常见失败原因**：
   - 测试用例新增断言未通过 → 修复代码或更新测试
   - mock 失效 → 检查 `patch` 路径
   - 环境变量污染 → 确认 `clean_env` fixture 清理完整

---

## 九、常见问题排查

### Q1：修改 `.env` 后为何没有触发热重载？

**排查步骤**：
```powershell
# 1. 确认 .env 路径正确
python -c "import os; print(os.environ.get('SKILL_RERANKER_ENV_FILE', './.env'))"

# 2. 确认 mtime 已变化
Get-Item .env | Select-Object Name, LastWriteTime

# 3. 确认 rerank 被调用（热重载仅在 rerank 时检查）
Select-String -Path reranker.log -Pattern "rerank.completed" | Measure-Object

# 4. 确认节流间隔未阻塞
Select-String -Path .env -Pattern "SKILL_RERANKER_HOT_RELOAD_INTERVAL"
```

### Q2：热重载日志显示 `failed_rollback` 怎么办？

```powershell
# 查看失败原因
Select-String -Path reranker.log -Pattern "hot_reload.failed_rollback" | Select-Object -Last 1
```

**常见原因**：
- `target_variant` 文件不存在 → 确认 `<model_dir>/onnx/<variant>` 存在
- `target_variant` 文件损坏 → 重新下载模型

### Q3：如何确认当前实际加载的 variant？

```powershell
# 查看 onnx.loaded 日志（首次加载）
Select-String -Path reranker.log -Pattern "onnx.loaded" | Select-Object -Last 1

# 查看 hot_reload.success 日志（热重载后）
Select-String -Path reranker.log -Pattern "hot_reload.success" | Select-Object -Last 1
```

### Q4：热重载会占用双倍内存吗？

**不会**。设计原则遵循【简易】：
- 新会话加载时，旧会话仍驻留（短暂双倍内存，约 2-5s）
- 加载成功后，旧会话指针被替换，Python GC 回收
- 加载失败时，新会话被清理，旧会话保留

峰值内存增量约 266MB（INT8）或 1GB（FP32），持续 2-5s。

### Q5：能否缩短 30s 检测延迟？

```ini
# 在 .env 中设置更短的间隔（如 5s）
SKILL_RERANKER_HOT_RELOAD_INTERVAL=5
```

**权衡**：
- 间隔越短，检测越快，但文件系统访问越频繁
- 生产建议 30s，调试时可设 5s
- 最小可设 1s（不建议生产使用）

---

## 附录：相关文档

- [RERANKER_INT8_DEPLOYMENT_GUIDE.md](./RERANKER_INT8_DEPLOYMENT_GUIDE.md) — INT8 部署基础配置
- [RERANKER_INT8_VS_FP32_SELECTION.md](./RERANKER_INT8_VS_FP32_SELECTION.md) — INT8/FP32 选型依据
- [V65_ONNX_DEPLOYMENT_PLAYBOOK.md](./V65_ONNX_DEPLOYMENT_PLAYBOOK.md) — ONNX 部署 playbook
- [V65_GPU_DEPLOYMENT_GUIDE.md](./V65_GPU_DEPLOYMENT_GUIDE.md) — GPU 部署指南
