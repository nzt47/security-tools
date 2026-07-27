# v6.5 RRF 降级对比测试方案

**目的**: 评估开启 RRF 降级（`SKILL_RERANKER_ENABLED=false`）后，系统整体 P99 延迟和吞吐量指标变化
**基准**: v6.5 Reranker 启用（v2-m3 CPU 推理）vs RRF 降级（无 Reranker）
**测试环境**: Windows CPU + 14.9GB RAM + 32GB 页面文件

---

## 1. 测试背景

### 1.1 当前状态

| 配置 | Reranker 状态 | 预期延迟 | 预期吞吐 |
|------|--------------|---------|---------|
| v6.5 启用（v2-m3）| ✅ 启用 | P99 4.6s ❌ | QPS 0.30 ❌ |
| **v6.5 降级（RRF）** | ❌ 禁用 | **P99 < 100ms ✅** | **QPS > 50 ✅** |

### 1.2 测试目标

验证降级到 RRF 后：
1. **延迟改善**: P99 从 4.6s 降至 < 100ms（目标降低 46 倍）
2. **吞吐提升**: QPS 从 0.30 提升至 > 50（目标提升 166 倍）
3. **功能保持**: P@3 精度不降（RRF 排序质量已验证）
4. **并发稳定**: 4 线程并发无崩溃

---

## 2. 测试方案设计

### 2.1 测试矩阵

| 测试组 | Reranker | fusion_mode | 候选数 | 迭代次数 | 并发 |
|--------|---------|-------------|--------|---------|------|
| A（基准）| 启用（v2-m3）| rrf_rerank | 20 | 30 | 1 |
| **B（降级）** | **禁用** | **rrf** | **20** | **200** | **1** |
| C（降级并发）| 禁用 | rrf | 20 | 100 | 4 |
| D（降级长尾）| 禁用 | rrf | 20 | 500 | 1 |

### 2.2 测试指标

| 指标 | 计算方法 | SLO 目标 |
|------|---------|---------|
| P50 延迟 | 50 分位数 | ≤ 30ms |
| P99 延迟 | 99 分位数 | ≤ 100ms |
| P99.9 延迟 | 99.9 分位数 | ≤ 200ms |
| QPS | 迭代次数 / 总耗时 | ≥ 50 |
| 内存占用 | 加载后 RSS | ≤ 500MB（无模型）|
| 成功率 | 成功数 / 总数 | 100% |
| P@3 精度 | top-3 命中率 | ≥ 0.4444 |

---

## 3. 测试脚本

### 3.1 降级模式压测脚本

```python
# scripts/benchmark_v65_rrf_degraded.py
"""v6.5 RRF 降级模式压测（对比 Reranker 启用模式）"""
import os
import sys
import time
import statistics
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

# 【关键】降级模式：禁用 Reranker
os.environ["SKILL_RERANKER_ENABLED"] = "false"

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from agent.skills_mgmt.reranker import SkillReranker
from agent.skills_mgmt.loader import SkillMatch


def _percentile(data, p):
    if not data:
        return 0.0
    sorted_data = sorted(data)
    k = (len(sorted_data) - 1) * p / 100
    f = int(k)
    c = min(f + 1, len(sorted_data) - 1)
    if f == c:
        return sorted_data[f]
    return sorted_data[f] + (sorted_data[c] - sorted_data[f]) * (k - f)


def _make_candidates(n=20):
    skills = [
        ("voice_interaction", "语音交互助手", "语音识别 语音转文字 语音合成", "interaction"),
        ("self_reflection", "自我反思", "复盘 改进建议 自我评估", "meta"),
        ("pdf_parser", "PDF 解析器", "PDF 文件解析 文档提取", "file"),
        ("memory_summary", "记忆摘要", "对话历史摘要 上下文压缩", "meta"),
        ("code_review", "代码审查", "代码质量审查 最佳实践", "dev"),
    ]
    candidates = []
    for i in range(n):
        idx = i % len(skills)
        sid, name, desc, cat = skills[idx]
        candidates.append(SkillMatch(
            skill_id=f"{sid}_{i}", name=name, description=desc,
            score=0.5 - i * 0.01, estimated_tokens=100,
            category=cat, tags=desc.split(),
        ))
    return candidates


def benchmark_degraded_single(iterations=200):
    """降级模式单次延迟"""
    print(f"\n{'─'*60}")
    print(f"测试 B: RRF 降级单次延迟（{iterations} 次）")
    print(f"{'─'*60}")

    reranker = SkillReranker()
    candidates = _make_candidates(20)
    query = "帮我识别语音并转成文字"

    # 预热
    for _ in range(5):
        reranker.rerank(query, candidates, top_k=3)

    latencies = []
    for i in range(iterations):
        t0 = time.time()
        reranker.rerank(query, candidates, top_k=3)
        latencies.append((time.time() - t0) * 1000)

    p50 = _percentile(latencies, 50)
    p99 = _percentile(latencies, 99)
    p999 = _percentile(latencies, 99.9)
    qps = 1000 / statistics.mean(latencies)

    print(f"  迭代: {iterations}")
    print(f"  P50:   {p50:.3f}ms")
    print(f"  P99:   {p99:.3f}ms")
    print(f"  P99.9: {p999:.3f}ms")
    print(f"  QPS:   {qps:.1f}")
    print(f"  目标 P99 ≤ 100ms: {'✅' if p99 <= 100 else '❌'}")
    print(f"  目标 QPS ≥ 50:    {'✅' if qps >= 50 else '❌'}")

    return {"p50": p50, "p99": p99, "p999": p999, "qps": qps}


def benchmark_degraded_concurrency(threads=4, per_thread=25):
    """降级模式并发"""
    print(f"\n{'─'*60}")
    print(f"测试 C: RRF 降级并发（{threads} 线程 × {per_thread} 次）")
    print(f"{'─'*60}")

    reranker = SkillReranker()
    candidates = _make_candidates(20)
    queries = ["语音识别", "反思回答", "解析PDF", "总结历史"]
    results = []
    errors = []
    barrier = threading.Barrier(threads)

    def worker(tid):
        try:
            barrier.wait()
            for i in range(per_thread):
                q = queries[tid % len(queries)]
                t0 = time.time()
                reranker.rerank(q, candidates, top_k=3)
                results.append((time.time() - t0) * 1000)
        except Exception as e:
            errors.append(str(e))

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=threads) as ex:
        list(ex.map(worker, range(threads)))
    total = time.time() - t0

    p99 = _percentile(results, 99)
    qps = len(results) / total

    print(f"  总请求: {len(results)}")
    print(f"  失败: {len(errors)}")
    print(f"  P99: {p99:.3f}ms")
    print(f"  QPS: {qps:.1f}")
    print(f"  目标 0 错误: {'✅' if len(errors) == 0 else '❌'}")

    return {"p99": p99, "qps": qps, "errors": len(errors)}


if __name__ == "__main__":
    print("=" * 60)
    print("  v6.5 RRF 降级模式对比压测")
    print(f"  SKILL_RERANKER_ENABLED={os.environ.get('SKILL_RERANKER_ENABLED')}")
    print("=" * 60)

    b = benchmark_degraded_single(iterations=200)
    c = benchmark_degraded_concurrency(threads=4, per_thread=25)

    print("\n" + "=" * 60)
    print("  对比汇总")
    print("=" * 60)
    print(f"  {'指标':<15} {'Reranker启用':<15} {'RRF降级':<15} {'改善':<10}")
    print(f"  {'P99 延迟':<15} {'4641ms':<15} {b['p99']:.2f}ms{'':<5} {4641/b['p99']:.1f}x")
    print(f"  {'QPS':<15} {'0.30':<15} {b['qps']:.1f}{'':<10} {b['qps']/0.30:.1f}x")
```

### 3.2 执行命令

```bash
# 1. 降级模式压测
python scripts/benchmark_v65_rrf_degraded.py

# 2. 对比基准（Reranker 启用，已有数据）
# 见 docs/v65_benchmark_result.json
```

---

## 4. 预期结果

### 4.1 延迟对比

| 指标 | Reranker 启用（v2-m3）| RRF 降级（预期）| 改善倍数 |
|------|---------------------|----------------|---------|
| P50 | 3426ms | ~0.5ms | ~6800x |
| P95 | 4499ms | ~1ms | ~4500x |
| P99 | 4641ms | ~2ms | ~2300x |
| P99.9 | 5632ms | ~5ms | ~1100x |
| Max | 5677ms | ~10ms | ~560x |

### 4.2 吞吐对比

| 指标 | Reranker 启用 | RRF 降级（预期）| 改善倍数 |
|------|--------------|----------------|---------|
| QPS（单线程）| 0.30 | ~1000 | ~3300x |
| QPS（4 并发）| ~1.2 | ~3000 | ~2500x |
| 平均延迟 | 3389ms | ~1ms | ~3400x |

### 4.3 资源占用对比

| 指标 | Reranker 启用 | RRF 降级（预期）| 改善 |
|------|--------------|----------------|------|
| 模型加载时间 | 41.58s | 0s（无模型）| ✅ 无需加载 |
| RSS 内存 | 1.92GB | ~100MB | 降低 95% |
| CPU 占用 | 99%（推理时）| < 5% | 降低 95% |

### 4.4 精度对比

| 指标 | Reranker 启用 | RRF 降级 | 说明 |
|------|--------------|---------|------|
| P@3 | 预期 +18.5% | 基准 0.4444 | Reranker 提升未实测 |
| 召回率 | 同上 | 基准 | RRF 已验证满足需求 |
| 拒绝率 | 100% | 100% | v6.1/v6.2 不受影响 |

---

## 5. 验收标准

### 5.1 必须通过（P0）

| 验收项 | SLO | 验证方法 |
|--------|-----|---------|
| P99 延迟 | ≤ 100ms | 单线程 200 次 rerank |
| QPS | ≥ 50 | 单线程 200 次连续 |
| 并发安全 | 0 错误 | 4 线程 × 25 次 |
| 功能正确 | 排序合理 | 语音匹配 > 不匹配 |
| 降级链 | sub-ms | 单元测试 33 passed |

### 5.2 期望达成（P1）

| 验收项 | SLO | 验证方法 |
|--------|-----|---------|
| P99.9 延迟 | ≤ 200ms | 500 次长尾测试 |
| 并发 QPS | ≥ 2000 | 4 线程压测 |
| 内存占用 | ≤ 200MB | psutil RSS |

---

## 6. 风险与回滚

### 6.1 风险评估

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|---------|
| RRF 精度不足 | 低 | P@3 下降 | v6.4 已验证 P@3=0.4444 |
| 降级逻辑 bug | 极低 | 主流程异常 | 33 单元测试覆盖 |
| 并发竞争 | 低 | 结果不一致 | rerank 无状态，纯排序 |

### 6.2 回滚方案

若降级模式出现异常：
```bash
# 立即回滚到 Reranker 启用
# 编辑 .env，将 SKILL_RERANKER_ENABLED 改回 true
# 重启服务
```

---

## 7. 后续行动

1. **立即执行**: 运行 `python scripts/benchmark_v65_rrf_degraded.py` 获取实测数据
2. **数据对比**: 将实测数据填入 §4 预期结果表格
3. **决策点**:
   - 若 P99 ≤ 100ms ✅ → 保持降级模式，等待 jina-reranker-v2 就绪
   - 若 P99 > 100ms ❌ → 分析根因，检查 RRF 实现
4. **长期**: jina-reranker-v2 下载完成后，重新启用 Reranker 并压测
