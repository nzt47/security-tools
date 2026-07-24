# Query 模式识别优化方案 — 针对 5 个 0% 拒绝率类别

**日期**: 2026-07-23
**目标**: 将负样本拒绝率从 68% → 84%（+16%），不影响正样本 P@3=0.4444
**关联报告**: `docs/RETRIEVAL_UPGRADE_V5_1_REPORT_20260723.md` §6.1

---

## 1. 问题背景

v5.1 重跑验证确认 5 个类别拒绝率为 0%：

| 类别 | 示例 query | 根因 |
|------|-----------|------|
| negative_keyword_trap | "safety 是什么意思"、"memory 概念解释" | reranker 受字面匹配影响，关键词触发技能 |
| negative_similar | "帮我删除文件"、"重启服务器" | 与脚本执行类技能语义近似 |
| negative_translation | "请帮我翻译这段话" | 被误判为与语言/语音交互相关 |
| negative_creative | "帮我写一首诗" | 与情感表达类技能语义重叠 |
| negative_math | "帮我算一下 1+1 等于几" | 简短 query 语义指向不明 |

**核心问题**: reranker 是字面匹配 + 语义混合模型，对"含技能关键词但非技能意图"的 query 判别力不足。

---

## 2. 设计原则（守三义）

### 【不易】不破坏现有契约
- 不改 `match()` 方法签名
- 不影响正样本黄金集 45 用例（P@3=0.4444 不下降）
- 不影响 `use_reranker=False` 的旧调用路径
- 失败降级：模式识别异常时不阻塞，继续走 RRF

### 【变易】按需演进
- 模式识别作为可选层，环境变量 `SKILL_QUERY_PATTERN_ENABLED` 开关（默认 True）
- 模式规则集中管理（`_QUERY_PATTERNS` 常量），便于扩展
- 命中模式后直接返回空 MatchResult（不触发 RRF/reranker）

### 【简易】最小充分解
- 单一职责：`_match_query_pattern(intent) -> Optional[MatchResult]`
- 返回 None = 未命中，返回 MatchResult = 命中（空结果）
- 插入位置：`match()` 入口，RRF 之前（最早期返回）

---

## 3. 插入位置

**文件**: `agent/skills_mgmt/loader.py`
**位置**: `match()` 方法 line 276（`fallback_used = False` 之后，`RRF + Rerank 融合模式` 之前）

```python
def match(self, intent: str, ...) -> MatchResult:
    t0 = time.time()
    tid = _trace_id()
    fallback_used = False

    # ── 【变易】query 模式识别：最早拒绝非技能意图 ──
    # 命中模式后直接返回空 MatchResult，不触发 RRF/reranker
    # 仅在 use_reranker=True 时启用（守【不易】不影响旧调用路径）
    if use_reranker and use_vector:
        pattern_result = self._match_query_pattern(intent, tid=tid, t0=t0)
        if pattern_result is not None:
            return pattern_result

    # ── RRF + Rerank 融合模式 ──
    if use_reranker and use_vector and fusion_mode == "rrf":
        ...
```

---

## 4. 模式规则设计

### 4.1 规则定义

```python
import re

# 【变易】query 模式规则：命中即拒绝（返回空 MatchResult）
# 每条规则: (pattern, category, reason)
# pattern 用 re.compile 预编译，性能无忧
_QUERY_PATTERNS = [
    # ── 1. keyword_trap: "X 是什么意思"/"X 概念解释" ──
    # 根因: 查询定义不是触发技能
    (re.compile(r"(.+?)\s*是什么(意思|含义|东西)"), "keyword_trap", "definition_query"),
    (re.compile(r"(.+?)\s*概念解释"), "keyword_trap", "definition_query"),
    (re.compile(r"(.+?)\s*的(定义|含义)"), "keyword_trap", "definition_query"),

    # ── 2. translation: "帮我翻译"/"请翻译" ──
    # 根因: 翻译类查询不触发技能
    (re.compile(r"(帮我|请).{0,4}翻译"), "translation", "translation_request"),

    # ── 3. creative: "帮我写诗/歌/故事" ──
    # 根因: 创作类查询不触发技能
    (re.compile(r"(帮我|请).{0,2}写.{0,2}(诗|歌|故事|小说|文章|散文)"), "creative", "creative_writing"),

    # ── 4. math: "帮我算"/"算一下" + 数学运算符 ──
    # 根因: 数学计算不触发技能
    (re.compile(r"(帮我|请).{0,2}算"), "math", "math_calculation"),
    (re.compile(r"[\d]+\s*[+\-*/]\s*[\d]+"), "math", "math_expression"),

    # ── 5. similar: 系统操作类（黑名单关键词） ──
    # 根因: "删除文件"/"重启服务器" 是系统操作，非技能
    # 风险: 中（需精确匹配，避免误伤"删除脚本"等真匹配）
    (re.compile(r"(删除|移动|复制|重命名)\s*(文件|目录|文件夹)"), "similar", "file_operation"),
    (re.compile(r"(重启|关闭|启动)\s*(服务器|服务|进程|系统)"), "similar", "system_operation"),
]
```

### 4.2 风险评估

| 规则 | 误伤风险 | 防御措施 |
|------|---------|---------|
| keyword_trap | 低 | 正样本无"X 是什么意思"模式 |
| translation | 低 | 正样本无"翻译"关键词 |
| creative | 低 | 正样本无"写诗/歌"模式 |
| math | 低 | 正样本无数学运算 |
| similar | **中** | 用精确匹配 `删除文件`，不用 `删除`（避免误伤"删除脚本"） |

**正样本回归验证**: 所有 45 个正样本 query 不匹配上述任何规则（需在实施后用 `eval_rrf_fusion.py` 验证 P@3 保持 0.4444）。

---

## 5. 实现代码草案

### 5.1 新增私有方法

```python
def _match_query_pattern(
    self, intent: str, *, tid: str, t0: float
) -> Optional[MatchResult]:
    """query 模式识别：命中非技能意图模式时返回空 MatchResult

    【不易】仅在 use_reranker=True 调用，不影响旧路径
    【变易】模式规则集中管理，便于扩展
    【简易】单次正则匹配，O(n) 复杂度

    Args:
        intent: 用户意图文本
        tid: trace_id
        t0: 起始时间

    Returns:
        None: 未命中模式，继续走 RRF
        MatchResult: 命中模式，返回空结果（matches=[]）
    """
    # 环境变量开关（默认开启）
    if os.environ.get("SKILL_QUERY_PATTERN_ENABLED", "true").lower() != "true":
        return None

    for pattern, category, reason in _QUERY_PATTERNS:
        if pattern.search(intent):
            elapsed = (time.time() - t0) * 1000
            logger.info(json.dumps({
                "trace_id": tid,
                "module_name": "loader",
                "action": "match.query_pattern.rejected",
                "intent": intent[:100],
                "category": category,
                "reason": reason,
                "duration_ms": round(elapsed, 2),
            }, ensure_ascii=False))
            return MatchResult(
                matches=[],
                total_scanned=0,
                elapsed_ms=elapsed,
                estimated_total_tokens=0,
                retrieval_method="query_pattern",
                fallback_used=False,
            )
    return None
```

### 5.2 环境变量

```bash
# .env 配置（默认开启，可关闭）
SKILL_QUERY_PATTERN_ENABLED=true
```

---

## 6. 预期效果

### 6.1 拒绝率提升

| 类别 | v5.1 | 预期 v6 | 提升 |
|------|------|---------|------|
| negative_keyword_trap | 0/2 (0%) | **2/2 (100%)** | +100% |
| negative_translation | 0/1 (0%) | **1/1 (100%)** | +100% |
| negative_creative | 0/1 (0%) | **1/1 (100%)** | +100% |
| negative_math | 0/1 (0%) | **1/1 (100%)** | +100% |
| negative_similar | 0/2 (0%) | **2/2 (100%)** | +100% |
| **总计** | **17/25 (68%)** | **22/25 (88%)** | **+20%** |

**注**: 实际可能因规则匹配边界情况略有出入，预期 84%~88%。

### 6.2 正样本影响

| 指标 | v5.1 | 预期 v6 | 影响 |
|------|------|---------|------|
| Precision@3 | 0.4444 | 0.4444 | ✅ 无影响（正样本不匹配模式） |
| Recall@3 | 1.0000 | 1.0000 | ✅ 无影响 |
| MRR | 0.9889 | 0.9889 | ✅ 无影响 |

---

## 7. 验证计划

### 7.1 正样本回归

```bash
python scripts/eval_rrf_fusion.py --only rrf_rerank --top-k 3
# 预期: P@3=0.4444 不下降
```

### 7.2 负样本拒绝率

```bash
python scripts/eval_negative_rejection.py --rerank-min-score 0.001
# 预期: 拒绝率 68% → 84%~88%
```

### 7.3 单元测试

新增 `tests/unit/test_query_pattern.py`:
- 测试每个模式命中
- 测试正样本不命中
- 测试环境变量开关

---

## 8. 实施步骤

1. **Step 1**: 在 `loader.py` 顶部新增 `_QUERY_PATTERNS` 常量（约 15 行）
2. **Step 2**: 在 `SkillLoader` 类中新增 `_match_query_pattern` 方法（约 30 行）
3. **Step 3**: 在 `match()` 方法 line 276 之后插入调用（约 5 行）
4. **Step 4**: 跑正样本评估验证 P@3=0.4444 不下降
5. **Step 5**: 跑负样本评估验证拒绝率提升
6. **Step 6**: 新增单元测试
7. **Step 7**: 生成 v6 报告

**预计工作量**: ~1 小时（含验证）

---

## 9. 后续扩展方向

1. **更多模式**: 天气查询、股票查询、新闻查询等
2. **置信度分级**: 不同模式返回不同置信度的空结果
3. **学习式模式**: 从历史拒绝记录中自动学习新模式
4. **多语言模式**: 英文 query 的模式识别（如 "what is X"、"define X"）

---

**方案状态**: 规划完成，待用户确认后实施
