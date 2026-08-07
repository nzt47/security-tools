# Shard 5/6 性能测试失败 · 日志摘要与根因分析（2026-08-07）

> 分析对象：PR #407 第二轮 CI（job `92843686854`，HF_ENDPOINT 修正后）；此前 hf-mirror 版失败见 PR #401 评论。

## 1. 失败时间线（关键日志证据）

| 时间 (UTC+8) | 事件 | 证据 |
|---|---|---|
| 10:55:05 | env 生效 | `HF_ENDPOINT: https://huggingface.co` ✓ |
| 10:55:28 | embedding 预热开始 | `embedding.preheat.start`（pending_docs 70） |
| 10:55:58 | **worker 就绪超时** | `embedding.worker.ready_timeout`（timeout_sec 30.0, model `paraphrase-multilingual-MiniLM-L12-v2`） |
| 11:04:49 | 性能断言失败 ×3 | 模块注册 `52.79ms > 50ms`；采样中位数 `1039.85ms > 600ms`；采样间隔（同类） |
| 11:04:50 | 汇总 | `3 failed, 2225 passed, 11 skipped, 14 warnings in 583.11s` |

## 2. 根因链（关键修正）

```
runner 网络访问 HF 慢/不稳定（hf-mirror 连接拒绝 → 换官方源后仍慢）
  → embedding worker 模型加载 > 30s → ready_timeout（非 init_failed）
  → hybrid 降级纯 BM25（功能正常，非错误）
  → worker 进程持续占资源 + 测试环境整体性能劣化（免费 runner 慢实例）
  → 3 个性能断言失败（注册 52.79ms / 采样 1039.85ms）
```

**认知修正**：首轮失败是 `init_failed`（hf-mirror 连接拒绝，error 明确）；本轮是 `ready_timeout`（官方源可连但 30s 内模型未就绪）。两轮共性是**模型加载不成功 + 测试环境性能劣化**；性能断言失败是**环境劣化**的直接结果，非代码回归。

## 3. 已落地修复（PR #407，提交 218733f1 + 04ca5e0c）

| 层 | 措施 | 机制 |
|----|------|------|
| CI env | `AGENT_HYBRID_EMBEDDING=0` | 显式禁用 Embedding（纯 BM25），子进程探测/预热完全跳过 → 无 30s×N 等待，消除 worker 资源竞争 |
| CI env | `HF_ENDPOINT=https://huggingface.co` | 覆盖代码默认 hf-mirror.com（setdefault 不覆盖），网络恢复时走官方源 |
| 测试 | `_hf_model_available()` | 探测模型仓库 URL（`/api/models/BAAI/bge-m3`、resolve config.json），域名可达≠模型可下载 |
| 测试 | `SKIP_EMBEDDING_PERF` skipif | 未显式禁用且模型不可下载 → 跳过性能测试；受控禁用（CI）→ 正常执行验证约束 |

## 4. 剩余风险与建议

1. **runner 慢实例 flaky**：免费 runner 偶发慢实例仍可能导致性能断言失败——若复现，建议性能断言打 `@pytest.mark.slow` 或调阈值（并行会话 CI 领地定夺）
2. **模型缓存**：若需真正跑 embedding 路径，CI 应预缓存模型（cache action），而非每次在线加载
3. **PR #401 依赖**：文档链接预检失败（`ops_log_parallel_session_cleanup_20260806.md` 引用脚本未入库）需 PR #401 合并解除

## 5. 最新进展（第三轮 CI，2026-08-07 跟踪更新）

**embedding 根因已确认修复**：第三轮 run 中「性能测试」已 **SUCCESS**（AGENT_HYBRID_EMBEDDING=0 显式禁用 + 测试层 `_hf_model_available` skip 逻辑生效），Shard 5/6 与性能断言不再误报。

当前 PR #407 唯一 FAILURE 为「**文档链接预检与锚点回归测试**」——与 embedding 无关，阻塞源为 PR #401 未合并（`scripts/dev/cleanup_parallel_session_tmp.ps1` 尚未入 master，文档引用的链接仍指向不存在文件）。

阻塞链（截至本次更新）：
```
PR #401 未合并（OPEN）
  → 文档链接预检失败（master 缺少 cleanup_parallel_session_tmp.ps1）
    → PR #407 无法全绿 → 无法自动合并
```
待办：PR #401 合入 master 后，需手动触发一次 PR #407 CI 重跑（新 run 的文档链接预检指向已入库脚本即可通过），随后走自动合并监控。
