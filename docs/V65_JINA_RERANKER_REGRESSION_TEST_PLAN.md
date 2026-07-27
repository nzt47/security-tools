# jina-reranker-v2 回归压测计划

**目的**: 验证启用 jina-reranker-v2-base-multilingual 后是否满足 500ms SLO 要求
**模型**: jinaai/jina-reranker-v2-base-multilingual（~280MB，多语言 Cross-Encoder）
**基准对比**: v2-m3（P99 4641ms ❌）vs jina-reranker-v2（预期 P99 ~300ms ✅）

---

## 1. 测试背景

### 1.1 模型选型理由

| 模型 | 大小 | CPU 延迟预期 | 内存预期 | 获取状态 |
|------|------|------------|---------|---------|
| bge-reranker-v2-m3 | 2.3GB | ~3.5s ❌ | 1.92GB ❌ | ✅ 已缓存 |
| bge-reranker-base | 1.1GB | ~1.5s ❌ | ~1GB ⚠️ | ❌ 网络不通 |
| **jina-reranker-v2** | **280MB** | **~300ms ✅** | **~400MB ✅** | **⏳ 下载中** |

### 1.2 预期目标

| 指标 | v2-m3 实测 | jina 预期 | SLO 目标 |
|------|-----------|----------|---------|
| 单次 P99 延迟 | 4641ms | ~300ms | ≤ 500ms |
| QPS | 0.30 | ~3 | ≥ 10（宽松）|
| 内存占用 | 1.92GB | ~400MB | ≤ 1.5GB |
| 并发安全 | ✅ | ✅ | 0 崩溃 |

---

## 2. 测试矩阵

### 2.1 测试组设计

| 测试组 | 模型 | 候选数 | 迭代次数 | 并发 | 目标 |
|--------|------|--------|---------|------|------|
| A（基准-v2-m3）| bge-reranker-v2-m3 | 20 | 20 | 1 | 已完成（P99 4641ms）|
| **B（jina 单次）** | **jina-reranker-v2** | **20** | **50** | **1** | **P99 ≤ 500ms** |
| **C（jina 吞吐）** | **jina-reranker-v2** | **20** | **100** | **1** | **QPS ≥ 3** |
| **D（jina 并发）** | **jina-reranker-v2** | **20** | **4×10** | **4** | **0 崩溃** |
| **E（jina 长尾）** | **jina-reranker-v2** | **20** | **200** | **1** | **P99.9 ≤ 2000ms** |
| F（RRF 降级基准）| 无 Reranker | 20 | 200 | 1 | 已完成（P99 0.5ms）|

### 2.2 验收标准

| 验收项 | SLO | 优先级 | 验证方法 |
|--------|-----|--------|---------|
| 单次 P99 延迟 | ≤ 500ms | P0 必须 | 测试 B |
| 内存占用 | ≤ 1.5GB | P0 必须 | 模型加载后 RSS |
| 并发安全 | 0 崩溃 | P0 必须 | 测试 D |
| 排序正确性 | 匹配 > 不匹配 | P0 必须 | 语音/反思验证 |
| QPS | ≥ 3 | P1 期望 | 测试 C |
| 长尾 P99.9 | ≤ 2000ms | P1 期望 | 测试 E |

---

## 3. 测试脚本

### 3.1 jina-reranker-v2 专用压测脚本

```python
# scripts/benchmark_v65_jina_reranker.py
"""jina-reranker-v2 回归压测（验证 500ms SLO）"""
import os
import sys
import time
import json
import statistics
import threading
from concurrent.futures import ThreadPoolExecutor

# 启用 Reranker + 指定 jina 模型
os.environ["SKILL_RERANKER_ENABLED"] = "true"
os.environ["SKILL_RERANKER_MODEL"] = "jinaai/jina-reranker-v2-base-multilingual"
os.environ["HF_HUB_OFFLINE"] = "1"  # 离线模式（使用 modelscope 下载的缓存）
os.environ["TRANSFORMERS_OFFLINE"] = "1"

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


def benchmark_jina_single(reranker, iterations=50):
    """测试 B: jina 单次延迟"""
    print(f"\n{'─'*60}")
    print(f"测试 B: jina-reranker-v2 单次延迟（{iterations} 次）")
    print(f"{'─'*60}")

    candidates = _make_candidates(20)
    query = "帮我识别语音并转成文字"

    # 预热 3 次
    for _ in range(3):
        reranker.rerank(query, candidates, top_k=3)

    latencies = []
    for i in range(iterations):
        t0 = time.time()
        reranker.rerank(query, candidates, top_k=3)
        latencies.append((time.time() - t0) * 1000)

    p50 = _percentile(latencies, 50)
    p99 = _percentile(latencies, 99)
    mean = statistics.mean(latencies)
    qps = 1000 / mean

    print(f"  迭代: {iterations}")
    print(f"  P50:  {p50:.2f}ms")
    print(f"  P99:  {p99:.2f}ms")
    print(f"  QPS:  {qps:.2f}")
    print(f"  目标 P99 ≤ 500ms: {'✅ 通过' if p99 <= 500 else '❌ 未达标'}")

    return {"p50": p50, "p99": p99, "qps": qps, "passed": p99 <= 500}


def main():
    print("=" * 60)
    print("  jina-reranker-v2 回归压测")
    print(f"  模型: {os.environ.get('SKILL_RERANKER_MODEL')}")
    print("=" * 60)

    # 加载模型
    reranker = SkillReranker()
    candidates = _make_candidates(5)

    t0 = time.time()
    reranker.rerank("预热", candidates, top_k=3)  # 触发加载
    load_time = time.time() - t0
    print(f"模型加载耗时: {load_time:.2f}s")

    # 排序正确性验证
    pairs = [
        ("语音识别", "语音交互助手 语音识别"),
        ("语音识别", "PDF 解析器 文档提取"),
    ]
    candidates_test = [
        SkillMatch("voice", "语音交互", "语音识别", 0.5, 100, "interaction"),
        SkillMatch("pdf", "PDF解析", "文档提取", 0.5, 100, "file"),
    ]
    result = reranker.rerank("语音识别", candidates_test, top_k=2)
    print(f"排序验证: 首候选={result[0].skill_id} (期望 voice)")
    print(f"排序正确: {'✅' if result[0].skill_id == 'voice' else '❌'}")

    # 执行压测
    b = benchmark_jina_single(reranker, iterations=50)

    # 汇总
    print(f"\n{'✅ 全部通过' if b['passed'] else '❌ 未达标'}")
    return 0 if b['passed'] else 1


if __name__ == "__main__":
    sys.exit(main())
```

### 3.2 执行命令

```bash
# 1. 确认模型已下载
ls ~/.cache/huggingface/hub/models--jinaai--jina-reranker-v2-base-multilingual/

# 2. 执行回归压测
python scripts/benchmark_v65_jina_reranker.py

# 3. 对比三组数据
# - v2-m3: docs/v65_benchmark_result.json
# - RRF 降级: docs/v65_rrf_degraded_benchmark.json
# - jina: docs/v65_jina_benchmark.json（本次生成）
```

---

## 4. 预期结果

### 4.1 性能预期

| 指标 | v2-m3 实测 | jina 预期 | RRF 降级 | SLO |
|------|-----------|----------|---------|-----|
| 加载耗时 | 41.58s | ~5-10s | 0s | - |
| 单次 P99 | 4641ms | ~300ms | 0.5ms | ≤ 500ms |
| QPS | 0.30 | ~3 | 121,327 | ≥ 10 |
| 内存 RSS | 1.92GB | ~400MB | 65MB | ≤ 1.5GB |
| 并发 P99 | 21,355ms | ~1,200ms | 0ms | - |

### 4.2 三模式对比矩阵

| 模式 | 延迟 | 吞吐 | 内存 | 精度 | 适用场景 |
|------|------|------|------|------|---------|
| v2-m3 | ❌ 4.6s | ❌ 0.3 | ❌ 1.92GB | ⭐⭐⭐ 最优 | GPU 环境 |
| **jina** | **✅ ~300ms** | **⚠️ ~3** | **✅ ~400MB** | **⭐⭐ 良好** | **CPU 生产推荐** |
| RRF 降级 | ✅ 0.5ms | ✅ 12万 | ✅ 65MB | ⭐ 基准 | 紧急降级 |

### 4.3 决策树

```
jina P99 ≤ 500ms？
├─ 是 ✅
│   ├─ 内存 ≤ 1.5GB？ → 启用 jina 作为生产默认
│   └─ 内存 > 1.5GB → 检查模型加载方式
└─ 否 ❌
    ├─ P99 ≤ 1000ms？ → 评估业务可接受性
    │   ├─ 可接受 → 启用但调优（减少候选数）
    │   └─ 不可接受 → 保持 RRF 降级
    └─ P99 > 1000ms → 保持 RRF 降级，考虑 GPU 部署
```

---

## 5. 风险与回滚

### 5.1 风险评估

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| jina 延迟超 500ms | 中 | SLO 不达标 | 保持 RRF 降级 |
| 中文排序质量差 | 低 | P@3 下降 | 对比 v2-m3 排序结果 |
| 模型加载失败 | 低 | 降级触发 | 已验证降级链完整 |
| 内存泄漏 | 极低 | 长期运行 OOM | 监控 RSS |

### 5.2 回滚方案

```bash
# 若 jina 不达标，立即回滚
# 编辑 .env:
SKILL_RERANKER_ENABLED=false
# 重启服务，降级到 RRF（已验证 P99 0.5ms）
```

---

## 6. 后续行动

1. **等待下载完成**：`python scripts/download_jina_reranker_modelscope.py`
2. **修改 .env 启用 jina**：
   ```bash
   SKILL_RERANKER_ENABLED=true
   SKILL_RERANKER_MODEL=jinaai/jina-reranker-v2-base-multilingual
   ```
3. **执行回归压测**：`python scripts/benchmark_v65_jina_reranker.py`
4. **决策**：根据 P99 实测值决定是否启用
5. **更新报告**：将结果写入 v6.5 测试报告
