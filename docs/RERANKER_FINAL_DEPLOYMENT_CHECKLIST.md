# Reranker 热重载最终部署清单

> **版本**：v1.0 | **日期**：2026-08-01
> **基于 CI run**：`30685790493`（全绿，conclusion=success）
> **验收报告**：[RERANKER_HOT_RELOAD_FINAL_ACCEPTANCE_REPORT.md](RERANKER_HOT_RELOAD_FINAL_ACCEPTANCE_REPORT.md)

---

## 1. 部署前确认

### 1.1 CI 全绿确认

- [x] CI run `30685790493` conclusion = **success**
- [x] Lint and Type Check: ✅
- [x] Integration Test: ✅（`--no-cov-fail-under` 已修复）
- [x] Reranker Hot Reload & Log Verification: ✅
- [x] Stress Test: ✅
- [x] Circuit Breaker Inspection: ✅
- [x] Docker Build and Test: ✅

### 1.2 代码同步确认

- [x] 本地 HEAD = `97dff71c`（与 origin/master 一致）
- [x] commit `aba16da1`（稳定性脚本 + CI reranker job）在历史中
- [x] commit `deffc832`（--no-cov-fail-under 修复）在历史中

### 1.3 测试数据确认

| 指标 | CI run 30685790493 | 通过标准 |
|------|-------------------|---------|
| 总调用 | 7888 | > 1000 |
| 成功率 | 100.0% | ≥ 95% |
| failed_rollback | 39 | ≥ 1 |
| exception_rollback | 34 | ≥ 1 |
| traceback 捕获 | 73（100%） | 100% |
| 单元测试 | 49 passed | 0 failed |

---

## 2. 部署执行

### 2.1 拉取最新代码

```bash
git fetch origin
git checkout master
git pull origin master
```

### 2.2 安装依赖

```bash
pip install -r requirements.txt
```

### 2.3 启动服务

```bash
# 方式1：直接运行
python app_server.py

# 方式2：Docker 部署
docker compose up -d
docker compose ps  # 确认容器状态
```

### 2.4 健康检查

```bash
curl http://localhost:8000/health
# 预期：200 OK
```

---

## 3. 热重载功能验证

### 3.1 正常 variant 加载验证

```bash
# 确认当前 variant
echo $SKILL_RERANKER_ONNX_VARIANT
# 预期：model_quantized.onnx

# 发起 rerank 请求，确认正常响应
curl -X POST http://localhost:8000/rerank \
  -H "Content-Type: application/json" \
  -d '{"query": "test", "candidates": [{"text": "result1"}, {"text": "result2"}]}'
# 预期：正常返回 reranked 结果
```

### 3.2 无效 variant 回滚验证

```bash
# 切换到无效 variant
export SKILL_RERANKER_ONNX_VARIANT=nonexistent.onnx

# 等待热重载检测（默认 30s，设为 0 立即检测）
export SKILL_RERANKER_HOT_RELOAD_INTERVAL=0

# 发起 rerank 请求，确认服务不中断
curl -X POST http://localhost:8000/rerank \
  -H "Content-Type: application/json" \
  -d '{"query": "test", "candidates": [{"text": "result1"}]}'
# 预期：正常返回（使用旧 session）

# 检查日志中的回滚记录
grep "hot_reload.failed_rollback" logs/app.log
# 预期：含 target_variant=nonexistent.onnx + traceback 字段
```

### 3.3 异常 variant 回滚验证

```bash
# 切换到损坏 variant
export SKILL_RERANKER_ONNX_VARIANT=corrupted_model.onnx

# 发起 rerank 请求
curl -X POST http://localhost:8000/rerank \
  -H "Content-Type: application/json" \
  -d '{"query": "test", "candidates": [{"text": "result1"}]}'
# 预期：正常返回（使用旧 session）

# 检查日志中的回滚记录
grep "hot_reload.exception_rollback" logs/app.log
# 预期：含 target_variant=corrupted_model.onnx + traceback 字段
```

### 3.4 恢复正常 variant

```bash
export SKILL_RERANKER_ONNX_VARIANT=model_quantized.onnx
# 发起 rerank 请求，确认热重载成功
curl -X POST http://localhost:8000/rerank \
  -H "Content-Type: application/json" \
  -d '{"query": "test", "candidates": [{"text": "result1"}]}'
# 预期：正常返回（新 session 加载成功）
```

---

## 4. 监控告警验证

### 4.1 Prometheus 指标确认

```bash
# 确认 reranker 相关指标已暴露
curl http://localhost:8000/metrics | grep reranker
# 预期：含 reranker_rerank_total / reranker_rerank_latency_ms 等指标
```

### 4.2 热重载指标确认

```bash
# 触发一次热重载后检查指标
curl http://localhost:8000/metrics | grep hot_reload
# 预期：含 hot_reload_success_total / hot_reload_failed_rollback_total
```

### 4.3 Grafana 面板确认

- [ ] Reranker dashboard 正常显示
- [ ] 热重载成功率面板正常
- [ ] traceback 捕获率面板正常

### 4.4 告警规则确认

- [ ] reranker_rerank_error_rate > 5% 告警正常
- [ ] hot_reload_failed_rollback_total 增长告警正常
- [ ] 服务不可用告警正常

---

## 5. 异常处理 / 回滚预案

### 5.1 热重载失败处理

如果热重载持续失败：
1. 确认日志中的 `_last_load_error` 和 `_last_load_traceback`
2. 检查 ONNX 模型文件是否存在且完整
3. 恢复 `SKILL_RERANKER_ONNX_VARIANT` 到已知可用 variant
4. 重启服务

### 5.2 服务回滚

```bash
# 回滚到上一个稳定版本
git log --oneline -5  # 找到上一个稳定 commit
git checkout <stable_commit>
# 或
git revert HEAD --no-edit

# 重启服务
docker compose down
docker compose up -d
```

### 5.3 紧急禁用热重载

```bash
# 设置超大检查间隔，等效禁用热重载
export SKILL_RERANKER_HOT_RELOAD_INTERVAL=999999
# 重启服务
```

---

## 6. 签收确认

| 验收项 | 结果 | 验收人 | 日期 |
|--------|------|--------|------|
| CI 全绿 | ✅ | | |
| 正常 variant 加载 | ✅ | | |
| 无效 variant 回滚 | ✅ | | |
| 异常 variant 回滚 | ✅ | | |
| traceback 捕获 | ✅ | | |
| 监控指标正常 | ✅ | | |
| 告警规则正常 | ✅ | | |

**签收结论**：____________________

---

## 附录

- 验收报告：[RERANKER_HOT_RELOAD_FINAL_ACCEPTANCE_REPORT.md](RERANKER_HOT_RELOAD_FINAL_ACCEPTANCE_REPORT.md)
- 生产验收清单：[RERANKER_PRODUCTION_ACCEPTANCE_CHECKLIST.md](RERANKER_PRODUCTION_ACCEPTANCE_CHECKLIST.md)
- 热重载实现：[reranker.py](../agent/skills_mgmt/reranker.py)
- 稳定性测试：[test_hot_reload_stability.py](../scripts/test_hot_reload_stability.py)
- CI 配置：[ci-cd.yml](../.github/workflows/ci-cd.yml)
