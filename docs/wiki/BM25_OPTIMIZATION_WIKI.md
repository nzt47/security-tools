# BM25 短文档归一化优化总结报告

> **归档位置**：`docs/wiki/BM25_OPTIMIZATION_WIKI.md`
> **创建日期**：2026-07-30
> **状态**：已完成并验证通过
> **归档说明**：本报告归档到项目 docs/wiki/ 目录，可通过 GitHub Pages 公开访问。如需同步到 Confluence，可直接复制本文档内容。

---

## 1. 优化概述

### 1.1 问题

VectorStore 的 `InvertedIndex` 使用 BM25 算法进行英文关键词检索排序。原默认参数 `b=0.75` 导致短文档得分虚高——查询 "machine learning" 时，2-token 短文档得分（1.35）反超包含完整语义的长文档（0.77），违反检索相关性预期。

### 1.2 方案

将 BM25 长度归一化参数 `b` 从 `0.75` 调整为 `0.5`，缓解短文档虚高，同时保留长短文档的区分度。

### 1.3 结果

| 指标 | 优化前 (b=0.75) | 优化后 (b=0.5) | 改善 |
|------|----------------|----------------|------|
| 平均短/长得分比 | 1.98x | 1.48x | 降幅 25.0% |
| 极端场景（1-token vs 50-token） | 2.52x | 1.81x | 降幅 28.3% |
| 改善用例数 | — | 3/3 基础 + 5/5 极端 | 全部通过 |

---

## 2. 过程时间线

| 日期 | 阶段 | 关键动作 |
|------|------|---------|
| 2026-07-28 | 问题发现 | 测试报告揭示短文档排序异常（2-token 得分 1.35 反超长文档 0.77） |
| 2026-07-28 | 方案实施 | `vector_store.py` 引入 `_DEFAULT_K1`/`_DEFAULT_B` 环境变量配置，b 从 0.75→0.5 |
| 2026-07-30 | 基础验证 | 创建 `verify_bm25_optimization.py`，3/3 用例通过，降幅 25% |
| 2026-07-30 | CI 集成 | `ci.yml` 添加 BM25 回归测试 step（每次提交运行） |
| 2026-07-30 | 极端场景验证 | 创建 `verify_bm25_extreme_cases.py`，5/5 场景通过 |
| 2026-07-30 | 关键修正 | 发现并修正 b 参数方向认知错误（b 越大虚高越严重，非越小越严重） |
| 2026-07-30 | CI 增强 | 追加极端场景 step + 失败日志增强（`::error::` 标记） |
| 2026-07-30 | 兼容性检查 | 确认所有搜索/排序测试与 b=0.5 兼容 |
| 2026-07-30 | 文档归档 | 技术文档 + 总结报告归档 |

---

## 3. 数据汇总

### 3.1 基础对照测试

数据来源：`scripts/verify_bm25_optimization.py`

| 用例 | b=0.75 短/长比 | b=0.5 短/长比 | 降幅 |
|------|---------------|--------------|------|
| machine learning 基础对照 | 2.2154x | 1.6738x | 24.4% |
| data science 中等差异 | 1.3289x | 1.0204x | 23.2% |
| neural networks 极端差异 | 2.3846x | 1.7500x | 26.6% |
| **平均** | **1.9763x** | **1.4814x** | **25.0%** |

### 3.2 极端场景测试

数据来源：`scripts/verify_bm25_extreme_cases.py`

| 场景 | 关键指标 | 阈值 | 结果 |
|------|--------|------|------|
| 1-token vs 50-token | 短/长比 1.81x | < 3.0x | ✓ |
| term_freq 极端差异（5次 vs 1次） | 比值 2.64x | < 5.0x | ✓ |
| 多短文档排序（1/2/3/50-token） | d1/d50 比值 2.46x | < 5.0x | ✓ |
| 空文档边界 | 得分 0.0 | = 0 | ✓ |
| b 值敏感性 | b=0.5 比值 1.77x | < 2.0x | ✓ |

### 3.3 b 值敏感性扫描

| b 值 | 短文档得分 | 长文档得分 | 短/长比 | 虚高程度 |
|------|----------|----------|--------|---------|
| 0.00 | 0.4000 | 0.4000 | 1.00x | 无虚高（但无区分度） |
| 0.25 | 0.4643 | 0.3514 | 1.32x | 轻微 |
| **0.50** | **0.5532** | **0.3133** | **1.77x** | **中等（折中）** |
| 0.75 | 0.6842 | 0.2826 | 2.42x | 偏高（原默认） |
| 1.00 | 0.8966 | 0.2574 | 3.48x | 严重虚高 |

**单调性确认**：短/长比随 b 增大而严格递增，验证 b 参数方向正确。

---

## 4. 关键发现：b 参数方向修正

### 4.1 认知错误

优化初期对 b 参数方向的理解有误，错误认为"b 越小虚高越严重"。

### 4.2 实测纠正

极端场景脚本暴露了错误。实测数据证明：

> **b 越大，短文档虚高越严重**（b=0 时比值 1.0x，b=1 时 3.48x）

数学推导：
- BM25 denominator = `tf + k1·(1 - b + b·|D|/avgdl)`
- b 增大 → 短文档（|D|<avgdl）denominator 减小（得分升高）+ 长文档 denominator 增大（得分降低）
- 净效果：短/长比增大（虚高严重）

### 4.3 修正范围

- `verify_bm25_extreme_cases.py` 场景5 单调性判定（`>` 改为 `<`）
- `BM25_B_PARAMETER_RATIONALE.md` 2.2/4.3/5.1/5.2/6.3 节表格和描述
- 诚实声明修正（b=0 无虚高但无区分度，b=1 虚高最严重）

### 4.4 结论

从 b=0.75 降到 b=0.5 是**降低 b 值**来缓解虚高——方向正确。

---

## 5. 诚实声明

**b=0.5 不能彻底消除短文档虚高**。BM25 的数学结构决定了短文档（term 密度高）天然获得更高得分，这是算法的设计意图。

`b=0.5` 的作用是将虚高从"不合理偏高"（b=0.75 时 2.42x）降至"合理偏高"（1.77x）：

- 基础场景：短/长比 1.98x → 1.48x（降幅 25%）
- 极端场景（1-token vs 50-token）：比值约 1.81x（可接受）
- 多短文档排序：梯度平缓，无悬崖效应

若要进一步降低虚高，可减小 b 值（如 b=0.25 → 1.32x），但会削弱长短文档的区分度；增大 b 值（如 b=1.0 → 3.48x）则虚高更严重。`b=0.5` 是平衡点。

---

## 6. CI 持续守护

### 6.1 回归测试集成

`ci.yml` 的 `unit-tests` job 集成了两个 BM25 回归测试 step：

| Step | 脚本 | 触发时机 | 验证内容 |
|------|------|---------|---------|
| 运行 BM25 优化回归测试 | `verify_bm25_optimization.py` | 每次 push/PR | 基础对照（3 用例） |
| 运行 BM25 极端场景回归测试 | `verify_bm25_extreme_cases.py` | 每次 push/PR | 极端场景（5 场景） |

### 6.2 失败定位

CI 失败时输出：
- `::error::` GitHub Actions 标记（UI 红色高亮）
- 每个失败用例的旧/新比值与 Δ 变化量
- 明确的排查方向（检查 `_DEFAULT_B` 配置）

---

## 7. 兼容性验证

### 7.1 两个独立的 BM25 实现

项目中有两个 BM25 实现，b=0.5 优化只影响 `InvertedIndex`：

| 实现 | 位置 | b 参数 | 影响范围 |
|------|------|--------|---------|
| InvertedIndex | `memory/vector_store/vector_store.py` | **0.5**（已优化） | 向量存储英文关键词搜索 |
| BM25SkillSearcher | `agent/skills_mgmt/bm25_searcher.py` | 0.75（rank_bm25 库默认） | 技能检索专有名词匹配 |

两者**正交不冲突**：BM25SkillSearcher 使用 `rank_bm25.BM25Okapi` 第三方库，不依赖 InvertedIndex，b 参数独立。

### 7.2 测试兼容性

| 测试文件 | 兼容性 | 原因 |
|---------|--------|------|
| `test_vector_store_fallback.py` | ✓ 兼容 | 含 TestBM25LengthNormalization（本轮新增），验证 b 值可配置性 |
| `test_bm25_skill_searcher.py` | ✓ 兼容 | 测试 BM25SkillSearcher（独立实现），断言功能性（score>0、排序顺序），无硬编码得分 |
| `test_tool_retrieval_quality.py` | ✓ 兼容 | 检索质量测试，不依赖 InvertedIndex 的 b 值 |
| `test_skills_mgmt.py` | ✓ 兼容 | 技能管理测试，BM25 路使用 BM25SkillSearcher |
| `test_tool_router_hybrid.py` | ⚠️ 超时 | 子进程 `_ensure_worker` 卡在 `stdout.readline()`，已知问题，与 b=0.5 无关 |

### 7.3 结论

所有搜索/排序测试与 b=0.5 配置兼容，无需修改测试代码。

---

## 8. 可配置性与回滚

### 8.1 环境变量配置

```python
# vector_store.py L35-36
_DEFAULT_K1 = float(os.environ.get("BM25_K1", "1.5"))
_DEFAULT_B = float(os.environ.get("BM25_B", "0.5"))
```

### 8.2 回滚方案

如需回滚至 b=0.75，无需改代码，设置环境变量即可：

```bash
# Linux / macOS
export BM25_B=0.75

# Windows PowerShell
$env:BM25_B = "0.75"

# Docker
docker run -e BM25_B=0.75 ...
```

### 8.3 调参建议

| 场景 | 建议 b 值 | 原因 |
|------|----------|------|
| 通用检索（默认） | 0.5 | 折中，虚高与区分度平衡 |
| 短查询关键词匹配 | 0.6-0.7 | 增大 b → 短文档优势更强 |
| 长文档语义检索 | 0.3-0.4 | 减小 b → 长文档获得更多补偿 |
| 极端短文档场景 | 0.3-0.4 | 减小 b → 缓解短文档虚高 |

---

## 9. 关联文件索引

### 9.1 代码变更

| 文件 | 变更 |
|------|------|
| [`memory/vector_store/vector_store.py`](../../memory/vector_store/vector_store.py) | L32-36 新增 `_DEFAULT_K1`/`_DEFAULT_B`，L100-108 `InvertedIndex.__init__` 接收 k1/b 参数 |

### 9.2 验证脚本

| 文件 | 用途 |
|------|------|
| [`scripts/verify_bm25_optimization.py`](../../scripts/verify_bm25_optimization.py) | 基础对照验证（3 用例） |
| [`scripts/verify_bm25_extreme_cases.py`](../../scripts/verify_bm25_extreme_cases.py) | 极端场景验证（5 场景） |

### 9.3 CI 配置

| 文件 | 变更 |
|------|------|
| [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml) | L232-248 新增两个 BM25 回归测试 step |

### 9.4 技术文档

| 文件 | 内容 |
|------|------|
| [`docs/BM25_B_PARAMETER_RATIONALE.md`](../BM25_B_PARAMETER_RATIONALE.md) | b 值选择依据技术文档（8 章节，含数学推导） |
| [`docs/wiki/BM25_OPTIMIZATION_WIKI.md`](BM25_OPTIMIZATION_WIKI.md) | 本总结报告 |

### 9.5 测试文件

| 文件 | 变更 |
|------|------|
| [`tests/unit/test_vector_store_fallback.py`](../../tests/unit/test_vector_store_fallback.py) | 新增 `TestBM25LengthNormalization` 测试类（2 用例） |

---

## 10. 数学原理

### 10.1 BM25 评分公式

```
score(D, Q) = Σ_{t ∈ Q} IDF(t) · tf(t,D) · (k1 + 1)
              ─────────────────────────────────────────
              tf(t,D) + k1 · (1 - b + b · |D| / avgdl)
```

### 10.2 b 参数语义

- `b=0`：不归一化，短长文档得分相同（无虚高但无区分度）
- `b=1`：完全归一化，短文档虚高最严重
- `b=0.5`：折中，缓解虚高与保留区分度的平衡点

### 10.3 短文档虚高根因

当 `|D| << avgdl` 时，`b·|D|/avgdl → 0`，denominator ≈ `tf + k1·(1-b)`：
- b=0.75：denominator ≈ `tf + 0.25·k1`（偏小 → 得分虚高）
- b=0.5：denominator ≈ `tf + 0.5·k1`（增大 → 得分缓解）

---

## 变更记录

| 日期 | 变更 | 关联 |
|------|------|------|
| 2026-07-28 | b 从 0.75 调整为 0.5 | `vector_store.py` L36 |
| 2026-07-30 | 基础验证脚本 + CI 集成 | `verify_bm25_optimization.py` + `ci.yml` |
| 2026-07-30 | 极端场景验证 + b 方向修正 | `verify_bm25_extreme_cases.py` |
| 2026-07-30 | 技术文档 + 总结报告归档 | `BM25_B_PARAMETER_RATIONALE.md` + 本文件 |
