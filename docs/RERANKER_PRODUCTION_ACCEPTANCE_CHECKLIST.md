# Reranker 生产环境部署验收检查清单

> 版本：v1.0 | 日期：2026-08-01
> 适用：v6.5 SkillReranker（含热重载机制 + 分阶段日志埋点）
> 关联文档：~~RERANKER_HOT_RELOAD_DEPLOYMENT_GUIDE.md~~ (已移除)
> 关联代码：[reranker.py](../agent/skills_mgmt/reranker.py) | ~~monitor_reranker_neg_sample.py~~ (已移除)

---

## 使用说明

- 本清单用于**生产环境部署后的验收验证**
- 每项执行后勾选 `[x]`，失败记录 `[-]` 并附原因
- **P0 项必须全部通过**方可放行；P1 项失败需评估影响后决定
- 验收人签字：____________  日期：____________

---

## 阶段一：部署前置检查（P0）

### 1.1 模型文件完整性

- [ ] **INT8 量化模型存在**
  ```powershell
  $modelDir = "C:/Users/Administrator/.cache/huggingface/hub/models--BAAI--bge-reranker-base"
  Test-Path "$modelDir/onnx/model_quantized.onnx"
  ```
  预期：`True`

- [ ] **FP32 原始模型存在（热重载切换目标）**
  ```powershell
  Test-Path "$modelDir/onnx/model.onnx"
  ```
  预期：`True`

- [ ] **模型文件大小合理**
  ```powershell
  Get-ChildItem "$modelDir/onnx" -Filter "*.onnx" | Select-Object Name, @{N='SizeMB';E={[math]::Round($_.Length/1MB,1)}}
  ```
  预期：`model_quantized.onnx` ~266MB，`model.onnx` ~1061MB

### 1.2 依赖环境

- [ ] **Python 版本 ≥ 3.10**
  ```powershell
  python --version
  ```

- [ ] **onnxruntime 已安装**
  ```powershell
  python -c "import onnxruntime; print(onnxruntime.__version__)"
  ```
  预期：版本号 ≥ 1.16.0

- [ ] **transformers 已安装**
  ```powershell
  python -c "import transformers; print(transformers.__version__)"
  ```
  预期：版本号 ≥ 4.30.0

### 1.3 配置文件

- [ ] **`.env` 文件存在且可读**
  ```powershell
  Test-Path .env
  ```
  预期：`True`

- [ ] **`SKILL_RERANKER_ENABLED=true`**
  ```powershell
  Select-String -Path .env -Pattern "^SKILL_RERANKER_ENABLED=true"
  ```

- [ ] **`SKILL_RERANKER_USE_ONNX=true`**
  ```powershell
  Select-String -Path .env -Pattern "^SKILL_RERANKER_USE_ONNX=true"
  ```

- [ ] **`SKILL_RERANKER_ONNX_VARIANT` 已配置**
  ```powershell
  Select-String -Path .env -Pattern "^SKILL_RERANKER_ONNX_VARIANT="
  ```
  预期：值为 `model_quantized.onnx` 或 `model.onnx`

- [ ] **热重载配置存在（可使用默认值）**
  ```powershell
  Select-String -Path .env -Pattern "SKILL_RERANKER_HOT_RELOAD_INTERVAL"
  ```
  预期：未配置时使用默认 30s，或显式配置

### 1.4 单元测试基线

- [ ] **reranker 单元测试通过**
  ```powershell
  $env:SKILLS_OFFLINE="1"
  python -m pytest tests/unit/test_reranker.py tests/unit/test_reranker_hot_reload.py -v
  ```
  预期：**49 passed**（33 原有 + 16 热重载，含 action 名契约校验）

---

## 阶段二：启动后功能验证（P0）

### 2.1 服务启动

- [ ] **app_server 成功启动**
  ```powershell
  python app_server.py 2> reranker.log &
  Start-Sleep 5
  Get-NetTCPConnection -LocalPort 5678 -State Listen
  ```
  预期：端口 5678 有 LISTEN 状态连接

- [ ] **首次 rerank 调用成功**
  ```powershell
  curl -s http://127.0.0.1:5678/api/skills/match -X POST -d '{"query":"反思"}'
  ```
  预期：返回 JSON 响应，无错误

### 2.2 日志优化验证

- [ ] **`rerank.completed` 日志包含 `tokenize_ms` 字段**
  ```powershell
  Select-String -Path reranker.log -Pattern "rerank.completed" | Select-Object -Last 1
  ```
  预期：JSON 日志含 `"tokenize_ms"` 字段

- [ ] **`rerank.completed` 日志包含 `inference_ms` 字段**
  预期：同上日志含 `"inference_ms"` 字段

- [ ] **耗时数值合理（INT8 模式）**
  预期：`tokenize_ms` < 100ms，`inference_ms` < 500ms，`duration_ms` < 600ms

### 2.3 ONNX 加载验证

- [ ] **`onnx.loaded` 日志出现**
  ```powershell
  Select-String -Path reranker.log -Pattern "onnx.loaded" | Select-Object -Last 1
  ```
  预期：JSON 日志含 `"action":"onnx.loaded"` + `"load_time_s"` 字段

- [ ] **加载耗时 < 30s**
  预期：`load_time_s` < 30

---

## 阶段三：热重载机制验证（P0）

### 3.1 标准切换流程（INT8 → FP32）

- [ ] **记录当前 variant**
  ```powershell
  Select-String -Path .env -Pattern "SKILL_RERANKER_ONNX_VARIANT" | Select-Object -Last 1
  ```
  记录当前值：____________

- [ ] **修改 `.env` 切换到 FP32**
  ```powershell
  notepad .env
  # 将 SKILL_RERANKER_ONNX_VARIANT=model_quantized.onnx
  # 改为 SKILL_RERANKER_ONNX_VARIANT=model.onnx
  ```

- [ ] **触发 rerank 调用（≤30s 内自动检测）**
  ```powershell
  1..5 | ForEach-Object {
      curl -s http://127.0.0.1:5678/api/skills/match -X POST -d '{"query":"测试"}' | Out-Null
      Start-Sleep -Milliseconds 500
  }
  ```

- [ ] **`hot_reload.detected` 日志出现**
  ```powershell
  Select-String -Path reranker.log -Pattern "hot_reload.detected" | Select-Object -Last 1
  ```
  预期：JSON 日志含 `"old_variant"` + `"new_variant"` 字段

- [ ] **`hot_reload.success` 日志出现**
  ```powershell
  Select-String -Path reranker.log -Pattern "hot_reload.success" | Select-Object -Last 1
  ```
  预期：`"new_variant":"model.onnx"`

- [ ] **新 variant 实际生效（inference_ms 显著变化）**
  ```powershell
  Select-String -Path reranker.log -Pattern "rerank.completed" | Select-Object -Last 1
  ```
  预期：`inference_ms` 从 ~420ms（INT8）变为 ~1000ms（FP32）

### 3.2 故障回滚验证（无效 variant）

- [ ] **修改 `.env` 指向不存在的 variant**
  ```powershell
  notepad .env
  # 将 SKILL_RERANKER_ONNX_VARIANT=model.onnx
  # 改为 SKILL_RERANKER_ONNX_VARIANT=nonexistent.onnx
  ```

- [ ] **触发 rerank 调用**
  ```powershell
  curl -s http://127.0.0.1:5678/api/skills/match -X POST -d '{"query":"测试"}'
  ```

- [ ] **`hot_reload.failed_rollback` 日志出现**
  ```powershell
  Select-String -Path reranker.log -Pattern "hot_reload.failed_rollback" | Select-Object -Last 1
  ```
  预期：JSON 日志含：
  - `"target_variant":"nonexistent.onnx"`
  - `"kept_variant":"model.onnx"`
  - `"load_error":"onnx_file_not_found: ..."`（**日志追踪新增字段**）

- [ ] **服务未中断（旧会话保留）**
  ```powershell
  curl -s http://127.0.0.1:5678/api/skills/match -X POST -d '{"query":"反思"}'
  ```
  预期：正常返回结果，无错误

- [ ] **恢复到有效 variant**
  ```powershell
  notepad .env
  # 改回 SKILL_RERANKER_ONNX_VARIANT=model_quantized.onnx
  ```
  等待热重载生效，确认 `hot_reload.success` 出现

### 3.3 异常回滚验证（P1）

- [ ] **模拟加载异常触发 `exception_rollback`**
  （需手动制造异常场景，如临时移动 tokenizer 文件）

- [ ] **`hot_reload.exception_rollback` 日志包含 `traceback` 字段**
  ```powershell
  Select-String -Path reranker.log -Pattern "hot_reload.exception_rollback" | Select-Object -Last 1
  ```
  预期：JSON 日志含 `"traceback":"Traceback (most recent call last):..."`（**日志追踪新增字段**）

### 3.4 稳定性测试

- [ ] **运行稳定性测试（60s）**
  ```powershell
  python scripts/test_hot_reload_stability.py --duration 60 --concurrency 8
  ```
  预期：
  - 成功率 ≥ 95%
  - 异常数 = 0
  - 无效 variant 切换正确触发回滚

---

## 阶段四：监控告警验证（P0）

### 4.1 实时监控启动

- [ ] **monitor 脚本成功启动**
  ```powershell
  .\scripts\start_reranker_monitor.ps1 -Mode monitor
  ```
  预期：显示实时监控界面，无报错

- [ ] **监控数据正确采集**
  预期：监控窗口显示：
  - 窗口大小 > 0
  - 负样本占比计算正确
  - P99 延迟显示

### 4.2 告警阈值验证

- [ ] **P99 > 500ms 告警触发**
  （切换到 FP32 模式，触发高延迟告警）
  ```powershell
  # 修改 .env 为 FP32，等待热重载
  # 观察监控输出
  ```
  预期：显示 `🔴 CRITICAL | P99 XXXms > SLO 500ms`

- [ ] **负样本 > 30% 告警触发**
  （构造低质量查询触发负样本告警）
  预期：显示 `🟡 WARNING | 负样本占比 XX% > 30%`

- [ ] **负样本 > 50% 告警触发**
  预期：显示 `🔴 CRITICAL | 负样本占比 XX% > 50%`

### 4.3 Prometheus 指标验证（P1）

- [ ] **`/metrics` 端点可访问**
  ```powershell
  curl -s http://127.0.0.1:5678/metrics | Select-String "yunshu_reranker"
  ```
  预期：返回 reranker 相关 Prometheus 指标

- [ ] **`yunshu_reranker_hot_reload_total` 指标存在**
  ```powershell
  curl -s http://127.0.0.1:5678/metrics | Select-String "hot_reload_total"
  ```
  预期：包含 `status="success"` / `status="failed"` / `status="exception"` 标签

- [ ] **`yunshu_reranker_load_total` 指标存在**
  预期：包含 `backend="onnx"` + `status="success"` 标签

### 4.4 Grafana 仪表盘验证（P1）

- [ ] **reranker 仪表盘可访问**
  预期：Grafana 显示 reranker 相关面板

- [ ] **热重载事件面板有数据**
  预期：显示最近的热重载成功/失败次数

---

## 阶段五：故障恢复验证（P0）

### 5.1 手动回滚

- [ ] **修改 `.env` 回滚 variant**
  ```powershell
  # 从 FP32 改回 INT8
  notepad .env
  # SKILL_RERANKER_ONNX_VARIANT=model_quantized.onnx
  ```

- [ ] **热重载自动生效（≤30s）**
  ```powershell
  Select-String -Path reranker.log -Pattern "hot_reload.success" | Select-Object -Last 1
  ```
  预期：`"new_variant":"model_quantized.onnx"`

- [ ] **延迟恢复正常**
  预期：`inference_ms` 回到 ~420ms（INT8 水平）

### 5.2 紧急停用热重载

- [ ] **设置超长间隔停用热重载**
  ```powershell
  # 在 .env 中添加
  # SKILL_RERANKER_HOT_RELOAD_INTERVAL=999999
  ```
  预期：修改 variant 后不再触发 `hot_reload.detected`

### 5.3 进程重启恢复

- [ ] **重启 app_server 后配置生效**
  ```powershell
  # Ctrl+C 停止 app_server
  # 重新启动
  python app_server.py 2> reranker.log
  ```
  预期：`onnx.loaded` 日志显示正确的 variant

---

## 阶段六：CI/CD 集成验证（P1）

### 6.1 CI 流水线

- [ ] **代码已推送到远程仓库**
  ```powershell
  git log --oneline -1
  ```
  预期：最新 commit 包含 reranker 热重载改动

- [ ] **CI 流水线已触发**
  ```powershell
  gh run list --repo nzt47/security-tools --limit 5
  ```
  预期：显示最新 push 触发的 workflow 运行

- [ ] **reranker 单元测试在 CI 中通过**（若 ci-cd.yml 修复后）
  预期：`reranker-hot-reload` job 显示 ✅

### 6.2 本地 CI 复现

- [ ] **本地复现 CI 测试命令**
  ```powershell
  $env:SKILLS_OFFLINE="1"
  $env:PYTHONIOENCODING="utf-8"
  python -m pytest tests/unit/test_reranker.py tests/unit/test_reranker_hot_reload.py -v
  python scripts/test_hot_reload_stability.py --duration 30 --concurrency 4 --ci-mode
  ```
  预期：49 passed + 稳定性测试通过

---

## 验收总结

### 通过标准

| 级别 | 通过标准 | 实际结果 |
|------|---------|---------|
| P0（必须） | 阶段一/二/三/四/五全部通过 | ____/____ 项通过 |
| P1（建议） | 阶段六 + Prometheus/Grafana 验证 | ____/____ 项通过 |

### 验收结论

- [ ] **通过**：所有 P0 项通过，可放行生产
- [ ] **有条件通过**：P0 通过，P1 部分失败，附条件说明
- [ ] **不通过**：P0 存在失败项，需修复后重新验收

### 失败项记录

| 失败项 | 原因 | 修复方案 | 负责人 |
|--------|------|---------|--------|
|        |      |         |        |

### 签字

- 验收人：____________  日期：____________
- 开发人：____________  日期：____________
- 运维人：____________  日期：____________

---

## 附录：快速验证脚本

一键执行 P0 核心验证项：

```powershell
# 快速验证脚本（不启动 app_server，仅检查配置和日志）
$modelDir = "C:/Users/Administrator/.cache/huggingface/hub/models--BAAI--bge-reranker-base"
Write-Host "=== 1. 模型文件 ===" -ForegroundColor Cyan
@("$modelDir/onnx/model_quantized.onnx", "$modelDir/onnx/model.onnx") | ForEach-Object {
    $exists = Test-Path $_
    Write-Host ("  {0}: {1}" -f (Split-Path $_ -Leaf), $(if ($exists) {"✅"} else {"❌"}))
}

Write-Host "=== 2. .env 配置 ===" -ForegroundColor Cyan
@("SKILL_RERANKER_ENABLED", "SKILL_RERANKER_USE_ONNX", "SKILL_RERANKER_ONNX_VARIANT") | ForEach-Object {
    $val = (Select-String -Path .env -Pattern "^$_=" | Select-Object -First 1).Line
    Write-Host ("  {0}: {1}" -f $_, $(if ($val) {"✅ $val"} else {"❌ 未配置"}))
}

Write-Host "=== 3. 单元测试 ===" -ForegroundColor Cyan
$env:SKILLS_OFFLINE="1"
python -m pytest tests/unit/test_reranker.py tests/unit/test_reranker_hot_reload.py -q 2>&1 | Select-String "passed|failed"
```
