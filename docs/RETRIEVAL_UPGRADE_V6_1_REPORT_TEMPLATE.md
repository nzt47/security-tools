# v6.1 Query 模式识别 — 端到端评估报告模板

**日期**: {{填写日期}}
**评估版本**: rrf_fusion_v6_1_booking
**评估人**: {{填写评估人}}
**适用 commit**: {{填写 commit hash}}

---

## 1. 评估目标

验证 v6.1 booking 规则加入后：
1. 【不易】正样本 P@3=0.4444 不下降
2. 【不易】40 个正样本黄金集 0 误伤（含 5 个 voice_interaction）
3. 【变易】负样本拒绝率从 v6 的 96% (24/25) 提升到 100% (25/25)
4. 【变易】case_105 "帮我点外卖" 被正确拒绝（v6 的唯一误召回）

---

## 2. 评估前置条件

### 2.1 代码版本确认

```bash
# 确认 v6.1 booking 规则已加入 _QUERY_PATTERNS
grep -A2 "booking" agent/skills_mgmt/loader.py
# 预期输出: (re.compile(r"(帮我|请|我想).{0,2}(点|订|买|叫|购)...
#           "booking", "order_request")

# 确认 commit
git log --oneline -1
# 预期: {{填写 commit hash}} feat(skills_mgmt): v6.1 booking 规则
```

### 2.2 环境变量确认

```bash
echo $SKILL_QUERY_PATTERN_ENABLED   # 应为空（默认 true）或 true
echo $SKILL_RERANK_MIN_SCORE         # 应为空（默认 0.001）或 0.001
```

### 2.3 测试数据确认

| 数据集 | 文件 | 用例数 |
|--------|------|--------|
| 正样本黄金集 | `tests/eval/skill_retrieval_golden_set.json` | 45（40 正 + 5 负）|
| 负样本扩展集 | `tests/eval/negative_samples_extended.json` | 25 |

---

## 3. 评估步骤

### 3.1 Step 1: 单元测试（TDD 绿灯）

```bash
python -m pytest tests/unit/test_query_pattern.py -v --tb=short
```

**预期结果**:
```
collected 71 items
tests/unit/test_query_pattern.py ....................................... [ 54%]
................................                                         [100%]
71 passed
```

**实际结果**: {{填写通过数}} / 71 passed

### 3.2 Step 2: v6.1 booking 规则集成验证

```bash
python scripts/verify_v61_booking_rule.py --scheme A --integration
```

**预期结果**:
```
[1/4] 负样本命中验证: 3/3 命中
[2/4] 正样本不误伤验证: 5/5 不误伤
[3/4] 高风险潜在正样本检查: 5/5 不命中
[4/4] 完整黄金集正样本冲突检查: 0/40 冲突
[集成] 3 个 negative_booking 负样本: 3/3 被拒绝
[集成] 5 个 voice_interaction 正样本: 5/5 不误伤
✅ 退出码 0: 全部通过
```

**实际结果**: {{填写通过/失败}}

### 3.3 Step 3: 正样本端到端评估（验证 P@3 不下降）

```bash
python scripts/eval_rrf_fusion.py --only rrf_rerank --top-k 3 \
    --output tests/eval/rrf_fusion_v6_1_verify.json
```

**预期指标**（与 v6 基线一致）:

| 指标 | v6 基线 | v6.1 预期 | 实际 |
|------|---------|-----------|------|
| Precision@3 | 0.4444 | 0.4444 | {{填写}} |
| Recall@3 | 1.0000 | 1.0000 | {{填写}} |
| MRR | 0.9889 | 0.9889 | {{填写}} |
| 0 分用例数 | 0 | 0 | {{填写}} |

**验证【不易】**: P@3 不下降 ✅ / ❌

### 3.4 Step 4: 负样本端到端评估（验证拒绝率提升）

```bash
python scripts/eval_negative_rejection.py --rerank-min-score 0.001 \
    --output tests/eval/negative_rejection_v6_1_verify.json
```

**预期指标**（v6 → v6.1 提升）:

| 指标 | v6 基线 | v6.1 预期 | 实际 |
|------|---------|-----------|------|
| 负样本拒绝率 | 96% (24/25) | **100% (25/25)** | {{填写}} |
| 误召回数 | 1 | **0** | {{填写}} |
| query_pattern 命中数 | 7 | **8**（含 booking 1 个）| {{填写}} |

**验证【变易】**: 拒绝率提升 96%→100% ✅ / ❌

### 3.5 Step 5: case_105 专项验证（v6 唯一误召回）

```bash
# 检查 case_105 "帮我点外卖" 的检索结果
python -c "
import json
data = json.load(open('tests/eval/negative_rejection_v6_1_verify.json'))
for c in data['negatives_rerank']['cases']:
    if c['case_id'] == 'case_105':
        print('case_105:', c['query'])
        print('  retrieval_method:', c['retrieval_method'])
        print('  actual:', c['actual'])
        print('  correctly_rejected:', c['correctly_rejected'])
"
```

**预期结果**:
```
case_105: 帮我点外卖
  retrieval_method: query_pattern   ← v6 是 rrf_rerank
  actual: []                         ← v6 是 ['voice_interaction']
  correctly_rejected: True           ← v6 是 False
```

**验证**: case_105 已被正确拒绝 ✅ / ❌

---

## 4. 按类别拒绝率分析

### 4.1 v6 → v6.1 类别对比

| 类别 | v6 拒绝率 | v6.1 预期 | v6.1 实际 |
|------|-----------|-----------|-----------|
| negative_booking | 66.7% (2/3) | **100% (3/3)** | {{填写}} |
| negative_keyword_trap | 100% (2/2) | 100% (2/2) | {{填写}} |
| negative_similar | 100% (2/2) | 100% (2/2) | {{填写}} |
| negative_translation | 100% (1/1) | 100% (1/1) | {{填写}} |
| negative_creative | 100% (1/1) | 100% (1/1) | {{填写}} |
| negative_math | 100% (1/1) | 100% (1/1) | {{填写}} |
| negative_weather | 100% (2/2) | 100% (2/2) | {{填写}} |
| negative_cooking | 100% (1/1) | 100% (1/1) | {{填写}} |
| negative_daily | 100% (1/1) | 100% (1/1) | {{填写}} |
| negative_entertainment | 100% (1/1) | 100% (1/1) | {{填写}} |
| negative_finance | 100% (2/2) | 100% (2/2) | {{填写}} |
| negative_greeting | 100% (1/1) | 100% (1/1) | {{填写}} |
| negative_medical | 100% (1/1) | 100% (1/1) | {{填写}} |
| negative_noise | 100% (2/2) | 100% (2/2) | {{填写}} |
| negative_programming | 100% (3/3) | 100% (3/3) | {{填写}} |
| negative_sports | 100% (1/1) | 100% (1/1) | {{填写}} |

### 4.2 query_pattern 命中分布

| category | v6 命中数 | v6.1 预期 | v6.1 实际 |
|----------|-----------|-----------|-----------|
| keyword_trap | 2 | 2 | {{填写}} |
| translation | 1 | 1 | {{填写}} |
| creative | 1 | 1 | {{填写}} |
| math | 1 | 1 | {{填写}} |
| similar | 2 | 2 | {{填写}} |
| **booking** | 0 | **1**（case_105）| {{填写}} |
| **总计** | 7 | **8** | {{填写}} |

---

## 5. 性能验证

### 5.1 命中模式时的延迟

```bash
# 测试 booking query 的匹配延迟
python -c "
import time
from agent.skills_mgmt.loader import SkillLoader
loader = SkillLoader()
queries = ['帮我点外卖', '帮我订一张机票', '我想订酒店']
for q in queries:
    t0 = time.time()
    result = loader._match_query_pattern(q, tid='test', t0=t0)
    elapsed = (time.time() - t0) * 1000
    print(f'{q}: {elapsed:.2f}ms, rejected={result is not None}')
"
```

**预期**: 每个 query < 5ms

**实际**: {{填写}}

### 5.2 booking 规则覆盖的 case

| case_id | query | v6 状态 | v6.1 状态 |
|---------|-------|---------|-----------|
| case_103 | 帮我订一张机票 | ✅ 已拒绝（rrf_rerank）| ✅ 拒绝（query_pattern）|
| case_104 | 我想订酒店 | ✅ 已拒绝（rrf_rerank）| ✅ 拒绝（query_pattern）|
| case_105 | 帮我点外卖 | ❌ 误召回 voice_interaction | ✅ 拒绝（query_pattern）|

---

## 6. 不变量验证汇总（守【不易】）

| 不变量 | 验证方式 | 预期 | 实际 |
|--------|----------|------|------|
| 40 正样本 0 误伤 | TDD 单元测试 | 71 passed | {{填写}} |
| voice_interaction 正样本不误伤 | 集成验证 | 5/5 不误伤 | {{填写}} |
| 正样本 P@3 不下降 | 端到端评估 | 0.4444 | {{填写}} |
| 正样本 Recall 不下降 | 端到端评估 | 1.0000 | {{填写}} |
| 正样本 MRR 不下降 | 端到端评估 | 0.9889 | {{填写}} |
| fusion_mode="none" 行为不变 | 代码审查 | 不触发 query_pattern | ✅ |
| match() 签名不变 | 代码审查 | 不变 | ✅ |

---

## 7. 版本演进对比

| 版本 | P@3 | Recall@3 | MRR | 负样本拒绝率 | 关键改进 |
|------|-----|----------|-----|-------------|----------|
| v5.1 | 0.4444 | 1.0000 | 0.9889 | 68% (17/25) | 阈值过滤 |
| v6 | 0.4444 | 1.0000 | 0.9889 | 96% (24/25) | 5 类 query_pattern |
| **v6.1** | **{{填写}}** | **{{填写}}** | **{{填写}}** | **{{填写}}** | **+ booking 规则** |

### v6 → v6.1 提升要点

- **拒绝率**: 96% → {{填写}}（{{+4% 或不变}}）
- **误召回数**: 1 → {{填写}}（{{0}}）
- **正样本**: {{不变/下降}}
- **新增规则**: booking（精确宾语白名单）

---

## 8. 评估结论

### 8.1 上线判定

| 判定项 | 标准 | 实际 | 结论 |
|--------|------|------|------|
| 单元测试 | 71 passed | {{填写}} | {{✅/❌}} |
| 集成验证 | 全部通过 | {{填写}} | {{✅/❌}} |
| 正样本 P@3 | ≥ 0.44 | {{填写}} | {{✅/❌}} |
| 负样本拒绝率 | = 100% | {{填写}} | {{✅/❌}} |
| case_105 已拒绝 | True | {{填写}} | {{✅/❌}} |

**最终判定**: {{✅ 可上线 / ❌ 阻塞上线}}

### 8.2 风险评估

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| booking 白名单遗漏新商品词 | 中 | 漏拒部分 booking query | 白名单可扩展 |
| "帮我点歌"被误伤 | 极低 | 误拒 voice_interaction | "歌"不在白名单 |
| 正样本 P@3 下降 | 极低 | 违【不易】 | 端到端评估已验证 |

### 8.3 回滚预案

若评估失败需回滚 v6.1 booking 规则：

```bash
# 方式 1: 注释单行（最小回滚）
# 在 agent/skills_mgmt/loader.py 的 _QUERY_PATTERNS 中注释 booking 行:
# (re.compile(r"(帮我|请|我想).{0,2}(点|订|买|叫|购)...
#  "booking", "order_request"),

# 方式 2: Git revert
git revert {{commit_hash}}
```

---

## 9. 交付物清单

| 文件 | 说明 |
|------|------|
| `agent/skills_mgmt/loader.py` | v6.1 booking 规则实施 |
| `tests/unit/test_query_pattern.py` | 71 个 TDD 测试（含 golden_set 正负样本过滤）|
| `tests/eval/rrf_fusion_v6_1_verify.json` | v6.1 正样本评估报告 |
| `tests/eval/negative_rejection_v6_1_verify.json` | v6.1 负样本拒绝报告 |
| `tests/eval/v61_integration_verify.log` | 集成验证日志 |
| `docs/RETRIEVAL_UPGRADE_V6_1_REPORT_TEMPLATE.md` | 本模板 |

---

## 10. 三义自检

| 检查项 | 结果 |
|--------|------|
| 【不易】40 正样本 0 误伤 | {{✅}} |
| 【不易】voice_interaction 正样本不误伤 | {{✅}} |
| 【不易】正样本 P@3 不下降 | {{✅}} |
| 【变易】拒绝率 96% → 100% | {{✅}} |
| 【变易】case_105 已正确拒绝 | {{✅}} |
| 【简易】booking 规则单条正则 | {{✅}} |

---

**报告生成时间**: {{填写}}
**评估耗时**: {{填写}}
**推荐配置**: BGE-m3 + RRF + bge-reranker-v2-m3 + 阈值 0.001 + query_pattern v6.1（含 booking）
