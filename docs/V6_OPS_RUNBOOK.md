# v6 Query 模式识别 — 运维上线操作手册

**版本**: v6 query_pattern (commit 244b49d7)
**适用对象**: 运维团队（SRE/On-call）
**目标**: 快速上线 v6 + 监控 + 一键回滚

> ⚠ **【2026-10-03 更正 · 请先读这段】** 本手册里出现的 `SKILL_QUERY_PATTERN_ENABLED`（§2、§4.2、§7.1、§8）
> **在生产代码里不存在** —— 全仓 `git grep` 只有 `tests/unit/test_query_pattern.py`、
> `scripts/verify_v61_booking_rule.py` 与 `docs/refactor_archive/` 的归档件引用它，
> `agent/skills_mgmt/loader.py` 的 `_match_query_pattern` 从未读过这个环境变量。
> ⇒ **设它 / 导它 / 往 .env 里写它，都没有任何效果**：既禁不掉 v6 正则层，也就不能当"一键回滚"用。
> 真正可用的回滚是 §7.2 的 git revert，或 §8 Q4 的"注释规则行"。
> 该开关的原设计意图见 `docs/QUERY_PATTERN_OPTIMIZATION_PLAN_20260723.md`；
> 要让它复活属于"恢复 v6.1 意图层"的独立立项（见 `docs/closeout/监控死规则与陈旧看板清理_20261002.md` §4）。

---

## 1. 一句话概要

v6 在 RRF/reranker **之前**增加正则规则匹配，命中非技能意图（"帮我写诗"/"删除文件"等）直接返回空结果，跳过昂贵的向量检索，**负样本拒绝率从 68% 提升到 96%**，正样本 P@3=0.4444 不变。

---

## 2. 关键配置（必读）

| 配置项 | 默认值 | 说明 | 修改方式 |
|--------|--------|------|----------|
| ~~`SKILL_QUERY_PATTERN_ENABLED`~~ | — | ⚠ **不存在**：生产代码从未读它（见文首更正）⇒ 写进 .env 无任何效果 | — |
| ~~`SKILL_RERANK_MIN_SCORE`~~ | — | ⚠ **【2026-10-03 实测】该名字不存在**：全仓无任何读取方（`git grep -n SKILL_RERANK_MIN_SCORE` 只命中文档与 `scripts/eval_negative_rejection.py:147` 的写入）。写进 .env 无任何效果 | — |
| `SKILL_RERANKER_MIN_SCORE` | `0.001` | **真正生效的** rerank 阈值（v5.1 已固化为代码默认值）| 环境变量 |

**修改配置原则**: 所有配置通过 `.env` 文件修改，其他文件通过环境变量引用（守【不易】）。

---

## 3. 上线前检查清单

```bash
# ── 1. 单元测试通过 ──
python -m pytest tests/unit/test_query_pattern.py -q
# 预期: 76 passed

# ── 2. 正样本 P@3 不下降 ──
python scripts/eval_rrf_fusion.py --only rrf_rerank --top-k 3
# 预期: Precision@3 = 0.4444

# ── 3. 负样本拒绝率提升 ──
python scripts/eval_negative_rejection.py --rerank-min-score 0.001
# 预期: 拒绝率 96% (24/25)

# ── 4. 环境变量确认 ──
# ⚠ 【2026-10-03】SKILL_QUERY_PATTERN_ENABLED 在生产代码里不存在（无消费方）：
#    下面这行只会回显空值，**不能当作"v6 已启用"的证据**，也不影响任何行为，故注释掉。
# echo $SKILL_QUERY_PATTERN_ENABLED
# ⚠ 【2026-10-03 实测】原写的是 SKILL_RERANK_MIN_SCORE —— **该名字在代码里无读取方**，
#    回显空值不代表"默认生效"。真正被读的是 SKILL_RERANKER_MIN_SCORE
#    （定义处 agent/skills_mgmt/reranker.py:155-157；settings 登记处 agent/settings/registry.py:1520）：
echo $SKILL_RERANKER_MIN_SCORE       # 应为空（默认 0.001）或 0.001
```

---

## 4. 上线步骤

### 4.1 部署代码

```bash
# 拉取 v6 commit
git pull origin feature/tlm-step3-vectorstore-sqlite-vec
git checkout 244b49d7  # v6 commit

# 确认 loader.py 含 v6 实现
grep "_match_query_pattern" agent/skills_mgmt/loader.py
# 预期输出: def _match_query_pattern(...) 及 match() 中的调用
```

### 4.2 配置 .env

```bash
# 追加到 .env（若不存在）
cat >> .env <<'EOF'
# 【2026-10-03 更正】SKILL_QUERY_PATTERN_ENABLED 在生产代码里**不存在**（无消费方）。
#   写进 .env 不会报错，但也不会切换任何行为 —— 故这里不再提供该行。
#   将来若恢复 v6.1 意图层并真的接线该开关，再把 SKILL_QUERY_PATTERN_ENABLED=true 加回来。

# rerank 阈值（v5.1 固化，0.001 为最优）
# ⚠ 【2026-10-03 实测更正】原写 SKILL_RERANK_MIN_SCORE —— 该名字无任何读取方，写了不生效。
#   真正生效的名字是 SKILL_RERANKER_MIN_SCORE（reranker.py:155-157 读取，默认已是 0.001）：
# SKILL_RERANK_MIN_SCORE=0.001      # ⛔ 无效名字，已注释
SKILL_RERANKER_MIN_SCORE=0.001
EOF

# 重启服务使配置生效
systemctl restart yunshu-agent
# 或 kubectl rollout restart deployment/yunshu -n yunshu
```

### 4.3 部署 Prometheus 告警规则

```bash
# 复制告警规则到 Prometheus 配置目录
cp monitoring/prometheus/rules/yunshu-v6-query-pattern-alerts.yml \
   /etc/prometheus/rules/

# 校验规则语法
promtool check rules /etc/prometheus/rules/yunshu-v6-query-pattern-alerts.yml
# 预期: SUCCESS: 8 rules found

# reload Prometheus
curl -X POST http://prometheus:9090/-/reload
# 或 systemctl reload prometheus
```

---

## 5. 上线后验证（第 1 小时）

### 5.1 功能验证

```bash
# 1. 查看日志是否有 query_pattern 命中
kubectl logs -n yunshu deployment/yunshu --tail=100 | grep "match.query_pattern.rejected"
# 预期: 出现 {"action":"match.query_pattern.rejected","category":"...",...}

# 2. 测试一个负样本 query（应被拒绝）
curl -X POST http://yunshu:8000/match \
  -d '{"query":"帮我写一首诗","use_reranker":true,"use_vector":true}'
# 预期: 返回 matches=[]，retrieval_method="query_pattern"

# 3. 测试一个正样本 query（应正常匹配）
curl -X POST http://yunshu:8000/match \
  -d '{"query":"请帮我反思刚才的回答","use_reranker":true,"use_vector":true}'
# 预期: 返回 self_reflection，retrieval_method="rrf_rerank"
```

### 5.2 监控指标验证

| Prometheus 查询 | 预期值 | 异常处理 |
|-----------------|--------|----------|
| `rate(yunshu_skill_match_count{method="query_pattern"}[5m])` | > 0 | 若为 0，检查环境变量 |
| `histogram_quantile(0.99, yunshu_skill_match_latency_ms_bucket)` | < 1000ms | 若 > 1000ms，检查 GPU |
| 告警 `YunshuV6QueryPatternHitRateDropped` | 未触发 | 若触发，见 §7 回滚 |

---

## 6. 日常监控要点

### 6.1 Grafana Dashboard（必看）

| Panel | 查询 | 正常范围 |
|-------|------|----------|
| query_pattern 命中率 | `rate(match_count{method="query_pattern"}[5m]) / rate(match_count[5m])` | 10%-30% |
| 5 类命中分布 | `sum by (category)(rate(query_pattern_hits_total[5m]))` | 5 类均非 0 |
| 延迟对比 | query_pattern vs rrf_rerank | query_pattern 应快 100x |
| 正样本 P@3 | CI 评估任务 | 0.4444 |

### 6.2 告警分级响应

| 级别 | 响应时间 | 告警示例 | 处理 |
|------|----------|----------|------|
| **P0 critical** | 立即 | P@3<0.40 / 命中率突降突增 | 见 §7 立即回滚 |
| **P1 warning** | 1 小时 | 单类别归零 / 延迟>50ms / RRF激增 | 排查规则/正则 |
| **P2 info** | 日报 | 单类别占比>60% / 环境变量被禁用 | 审计/确认 |

### 6.3 每日审计（人工）

```bash
# 抽样 100 条命中日志，检查误伤
grep "match.query_pattern.rejected" /var/log/agent/loader.log | \
  jq -r '.intent' | shuf -n 100

# 误伤标准: query 实际是技能意图但被误拒
# 误伤率阈值: < 0.1%（1000 次命中不超过 1 次误伤）
```

---

## 7. 回滚预案（重点）

### 7.1 ~~一键回滚（推荐，不重启服务）~~ ⚠ **本节无效，勿照抄**

> **【2026-10-03 实测更正】** `SKILL_QUERY_PATTERN_ENABLED` 在生产代码里不存在
> （`agent/skills_mgmt/loader.py` 无任何读取点；只有测试与 `docs/refactor_archive/` 引用）
> ⇒ **下面这些命令敲下去不会有任何效果**：v6 正则层照常生效，等于"回滚了个寂寞"。
> 真正的回滚请用 §7.2（git revert 代码）或 §8 Q4（注释规则行，按类别禁用）。

```bash
# ⛔ 已作废：本命令不会禁用 v6（该环境变量无消费方，敲了没反应）
# kubectl exec -n yunshu deployment/yunshu -- \
#   env-set SKILL_QUERY_PATTERN_ENABLED=false

# ⛔ 同样已作废：
# kubectl exec -it -n yunshu deployment/yunshu -- bash
# echo "SKILL_QUERY_PATTERN_ENABLED=false" >> /app/.env
```

### 7.2 完整回滚（需重启服务）

```bash
# 方式 2: Git revert（回退代码到 v5.1）
git revert 244b49d7  # v6 commit
git push origin feature/tlm-step3-vectorstore-sqlite-vec

# 重新部署
kubectl rollout restart deployment/yunshu -n yunshu
```

### 7.3 回滚验证

```bash
# 1. query_pattern 日志应消失
kubectl logs -n yunshu deployment/yunshu --tail=100 | grep "match.query_pattern.rejected"
# 预期: 无输出

# 2. 负样本拒绝率应回落到 68%（v5.1 基线）
python scripts/eval_negative_rejection.py --rerank-min-score 0.001
# 预期: 拒绝率 68% (17/25)

# 3. 正样本 P@3 保持 0.4444（不受影响）
python scripts/eval_rrf_fusion.py --only rrf_rerank --top-k 3
# 预期: Precision@3 = 0.4444
```

---

## 8. 常见问题（FAQ）

### Q1: query_pattern 命中率为 0？

**排查**:
```bash
# 1. 检查环境变量
# ⚠ 【2026-10-03】SKILL_QUERY_PATTERN_ENABLED 在生产代码里不存在（无消费方）⇒
#    "命中率为 0"与它无关，查这个变量查不出原因。下面改查已真实接线的 SKILLS_* 旋钮。
kubectl exec -n yunshu deployment/yunshu -- env | grep SKILLS_

# 2. 检查调用方是否传 use_reranker=True 且 use_vector=True
# ⚠ 【2026-10-03 实测更正】原第 2 条查的 match.extension_not_implemented **在现行代码中不存在**：
#    agent/skills_mgmt/loader.py 里它只出现在 :838 的一句注释中（说明该 action 属于 v6.1 时代
#    已被删除的分支），全仓其余命中都在 docs/refactor_archive/ 与 tests/eval/*.log 历史日志里。
#    ⇒ 这条 grep **永远不会有输出**，用它排查等于白查。
#    现行真实可查的替代 action（2026-10-03 在 loader.py 里逐条核实）：
#      · match.reranker_not_applied        loader.py:883  ← 与本节场景最对应：调用方传了 use_reranker=True
#                                                          但 use_vector=False 时记录，reason="reranker requires use_vector=True"
#      · match.rrf_fallback_to_tfidf       loader.py:835  ← RRF 融合失败降级 TF-IDF 单路
#      · match.vector_fallback_to_tfidf    loader.py:875  ← 向量腿不可用降级 TF-IDF
kubectl logs -n yunshu deployment/yunshu | grep -E "match.reranker_not_applied|match.rrf_fallback_to_tfidf|match.vector_fallback_to_tfidf"
# 若有 warning，说明调用方未正确传参（这三条降级同时会打指标 yunshu_skill_match_fallback_total）
kubectl logs -n yunshu deployment/yunshu | grep "match.reranker_not_applied" | jq -r '.reason' | head -5
```

### Q2: 某类别命中率为 0？

**排查**:
```bash
# 检查该类别的正则是否被误删
# ⚠ 【2026-10-03 实测】loader.py 里现在**没有类别正则表**，也没有 _QUERY_PATTERNS / _match_query_pattern：
#    该变量在 2026-10-03 的检索时点全仓零命中（agent/ 下只有 orchestrator/message_handler.py 的
#    SIMPLE_QUERY_PATTERNS，与 v6 无关）；v6 的 query_pattern 层本身已经不在了。
#    ⇒ 下面这条 grep 只会捞到日志里出现的 "category" 字样，查不出"某类别正则被误删"。
grep "category" agent/skills_mgmt/loader.py | head -10
# 现行对应能力：正则意图层已移除，改用 RRF 融合 + quality gate（rrf.quality_gate.* 日志）与
# （若已接线）negative_intent 语义拒绝层。查这些动作：
kubectl logs -n yunshu deployment/yunshu | grep -E "rrf.quality_gate|negative_intent"
```

### Q3: 正样本 P@3 下降？

**立即回滚**（⚠ 【2026-10-03 更正】原来的 `export SKILL_QUERY_PATTERN_ENABLED=false` **无效**：
该环境变量在生产代码里不存在，敲了不会有任何效果）:
```bash
# 正确做法：git revert 整个 v6 commit（见 §7.2）。
# ⚠ 【2026-10-03 实测】原来并列的"① 注释掉 loader.py 的 _QUERY_PATTERNS 里误伤的规则行"**已不可执行**：
#    该变量/正则表在当前 loader.py 中**不存在**（见 Q2），无从注释。
# 然后排查误伤（注意：match.query_pattern.rejected 这个 action 同样已不存在，见 Q1 的说明）:
grep -E "match.reranker_not_applied|match.rrf_fallback_to_tfidf" /var/log/agent/loader.log | \
  jq -r '.intent' | head -100
```

### Q4: 如何临时禁用某个类别？

**注释规则行**（单行回滚）:

> ⛔ **【2026-10-03 实测：本节已不可执行】** 下面注释所指的 `agent/skills_mgmt/loader.py` 里的
> `_QUERY_PATTERNS`（正则类别表）**在当前代码中不存在** —— 全仓 `git grep -n "_QUERY_PATTERNS"` 只命中
> `agent/orchestrator/message_handler.py` 的同名无关常量 `SIMPLE_QUERY_PATTERNS`。
> v6.1 的 query_pattern 层与其正则表已随重构移除 ⇒ **没有"某个类别的正则行"可以注释**。
> 要按粒度禁用某类意图，现行可用的只有：① 调 `SKILL_RERANKER_MIN_SCORE`（**不是** `SKILL_RERANK_MIN_SCORE`，后者不存在）/ `SKILLS_FUSION_*` 等
> **真实存在**的旋钮（见 §2）；② 若 negative_intent 语义层已接线，调其阈值。
> 恢复 v6.1 意图层属独立立项，见 `docs/QUERY_PATTERN_OPTIMIZATION_PLAN_20260723.md`。

```python
# ⛔ 已作废：_QUERY_PATTERNS 在当前 loader.py 中不存在，以下写法无从下手
# 在 agent/skills_mgmt/loader.py 的 _QUERY_PATTERNS 中
# 注释掉对应类别的正则行即可
# 例如禁用 similar:
# (re.compile(r"(删除|移动...)\s*(文件|目录)..."), "similar", "file_operation"),
```

---

## 9. 联系方式

| 角色 | 职责 | 联系方式 |
|------|------|----------|
| skills_mgmt owner | v6 代码/规则维护 | <填写> |
| SRE On-call | 告警响应/回滚 | <填写> |
| 评估脚本维护 | CI 回归测试 | <填写> |

---

## 10. v6.2 补充章节 — negative_intent 语义拒绝层

> 本章节为 v6.2 增量内容，与 v6.1 章节并存。v6.2 在 v6.1 正则规则之后、RRF+Reranker 之前增加 BGE-m3 语义拒绝层。

### 10.1 v6.2 一句话概要

v6.2 在 v6.1 正则规则未命中后，用 BGE-m3 embedding 与 10 类非技能意图 prototype 计算余弦相似度，命中则提前拒绝，**负样本平均延迟从 ~600ms 降至 ≤ 200ms**，正样本 P@3=0.4444 不变。

### 10.2 v6.2 关键配置

> ⚠ **【2026-10-03 实测 · 本节的开关一律不生效，别照抄】**
> `SKILL_NEGATIVE_INTENT_ENABLED` 的唯一读取方是 `agent/skills_mgmt/negative_intent_detector.py`（:292 / :370），
> 而该模块**在本仓没有任何生产调用方**（loader 里的懒加载钩子随 commit `1159d88f` 被主动删除；
> 依赖它的 5 条告警规则也已在 2026-10-02 整组删除）⇒ **设这个变量、或执行 §10.4/§10.5 里的 `env-set` 回滚命令，
> 都不会有任何效果** —— 属"过期运维指引"。恢复路径见该模块文件头（先接线、再补开关读取）。
> 下面保留原文作历史记录，请勿据此操作。

| 配置项 | 默认值 | 推荐值 | 说明 | 修改方式 |
|--------|--------|--------|------|----------|
| `SKILL_NEGATIVE_INTENT_ENABLED` | `true` | ~~`true`~~（**无读取方，设了无效**） | v6.2 总开关 | 环境变量（当前无效） |
| `SKILL_NEGATIVE_INTENT_THRESHOLD` | `0.75` | **`0.71`** | 相似度阈值（校准后采用 0.71）| 环境变量 |

**配置原则**：所有配置通过 `.env` 文件修改（守【不易】）。

**阈值选择依据**：校准脚本（`calibrate_v62_threshold.py`）分析显示正样本 max sim = 0.7021（case_013 "建议"），采用 0.71 略高于此值，确保正样本 0 误伤（守【不易】）。负样本覆盖率 64%，但 9 个漏判负样本全部已被 v6.1 正则规则覆盖。

### 10.3 v6.2 上线前检查清单

```bash
# ── 1. 单元测试通过 ──
python -m pytest tests/unit/test_negative_intent.py -q
# 预期: ~71-95 passed

# ── 2. 阈值校准（BGE-m3 相似度分布分析）──
python scripts/calibrate_v62_threshold.py --dry-run
# 预期: 样本集结构正常

python scripts/calibrate_v62_threshold.py \
    --output tests/eval/v62_threshold_calibration.json
# 预期: 输出推荐阈值 + 正负样本分布 + 0 prototype 冲突

# ── 3. 端到端 4 阶段验证 ──
python scripts/verify_v62_negative_intent.py \
    --output tests/eval/v62_verify_report.json
# 预期: 正样本 P@3=0.4444 + 负样本拒绝率 100% + v6.2 命中数 ≥ 8

# ── 4. 环境变量确认 ──
echo $SKILL_NEGATIVE_INTENT_ENABLED    # 空（默认 true）或 true
echo $SKILL_NEGATIVE_INTENT_THRESHOLD  # 空（默认 0.75）或校准值
```

### 10.4 v6.2 上线步骤

#### 10.4.1 部署代码

```bash
git pull origin feature/tlm-step3-vectorstore-sqlite-vec
git checkout <v6.2-commit>  # 校准验证后回填

# 确认 v6.2 文件存在
ls agent/skills_mgmt/negative_intent_detector.py
ls tests/eval/negative_intent_prototypes.json
ls scripts/calibrate_v62_threshold.py
ls scripts/verify_v62_negative_intent.py
```

#### 10.4.2 配置 .env

```bash
# 追加到 .env（基于校准结果，采用 0.71 阈值）
cat >> .env <<'EOF'
# v6.2 语义拒绝层（默认开启）
SKILL_NEGATIVE_INTENT_ENABLED=true
# 相似度阈值（校准后采用 0.71，守【不易】0 正样本误伤）
SKILL_NEGATIVE_INTENT_THRESHOLD=0.71
EOF

systemctl restart yunshu-agent
```

#### 10.4.3 部署 v6.2 告警规则

```bash
# 告警规则已追加到 yunshu-v6-query-pattern-alerts.yml
# 含 5 条 v6.2 告警：P0-4 误伤 / P0-5 检测器降级 / P1-4 延迟 / P1-5 命中率 / P2-4 禁用
promtool check rules monitoring/prometheus/rules/yunshu-v6-query-pattern-alerts.yml
kubectl apply -f monitoring/prometheus/rules/yunshu-v6-query-pattern-alerts.yml
```

### 10.5 v6.2 回滚预案

#### 10.5.1 一键回滚（秒级，推荐）

```bash
# 仅禁 v6.2 embedding 层，v6.1 规则层与 RRF+Reranker 不受影响
kubectl exec -n yunshu <pod> -- env-set SKILL_NEGATIVE_INTENT_ENABLED=false   # ⚠ 无生产读取方 ⇒ 不生效（见 §10.2 顶注）
# 或在 .env 中
echo "SKILL_NEGATIVE_INTENT_ENABLED=false" >> .env && systemctl restart yunshu-agent
```

#### 10.5.2 完整回滚（Git revert）

```bash
git revert <v6.2-commit>
# 保留 v6.1，回滚 v6.2 全部改动
```

### 10.6 v6.2 常见问题

#### Q5: v6.2 命中率为 0？

**排查**：
```bash
# 检查环境变量
kubectl exec -n yunshu <pod> -- env | grep SKILL_NEGATIVE_INTENT

# 检查 prototype 文件
kubectl exec -n yunshu <pod> -- ls -la tests/eval/negative_intent_prototypes.json

# 检查 BGE-m3 加载日志
kubectl logs -n yunshu <pod> | jq 'select(.module_name=="negative_intent_detector") | .action'
# 预期 action: prototypes.loaded
```

#### Q6: 正样本 P@3 下降（v6.2 误伤）？

**紧急处理**：
```bash
# 1. 立即禁用 v6.2
kubectl exec -n yunshu <pod> -- env-set SKILL_NEGATIVE_INTENT_ENABLED=false   # ⚠ 无生产读取方 ⇒ 不生效（见 §10.2 顶注）

# 2. 检查误伤样本
kubectl logs -n yunshu <pod> | jq 'select(.action=="detect.rejected") | .intent' | head -100

# 3. 重跑校准，提高阈值
kubectl exec -n yunshu <pod> -- python scripts/calibrate_v62_threshold.py --threshold 0.80
```

#### Q7: v6.2 延迟 > 300ms？

**排查**：
```bash
# BGE-m3 编码耗时
kubectl logs -n yunshu <pod> | jq 'select(.action=="detect.rejected") | .duration_ms' | sort -n | tail -10

# CPU 占用
kubectl top pod -n yunshu

# 若 CPU 不足，临时禁用 v6.2
kubectl exec -n yunshu <pod> -- env-set SKILL_NEGATIVE_INTENT_ENABLED=false   # ⚠ 无生产读取方 ⇒ 不生效（见 §10.2 顶注）
```

### 10.7 v6.2 监控指标

| 指标 | 类型 | 说明 |
|------|------|------|
| `yunshu_skill_match_count{method="negative_intent"}` | gauge | v6.2 命中时上报 value=0 |
| `yunshu_negative_intent_hits_total{category}` | counter | 各类别命中数（建议新增） |
| `yunshu_negative_intent_duration_ms` | histogram | 检测延迟（建议新增） |
| `yunshu_negative_intent_detector_failed_total` | counter | 检测器降级计数（建议新增） |

---

## 11. 相关文档

| 文档 | 用途 |
|------|------|
| ~~[V6_MONITORING_CONFIG_CHECKLIST.md](file:///c:/Users/Administrator/agent/docs/V6_MONITORING_CONFIG_CHECKLIST.md)~~ ⚠ **【2026-10-03 实测】该文件不存在**（`docs/` 下无此文件）| — |
| [RETRIEVAL_UPGRADE_V6_REPORT_20260723.md](file:///c:/Users/Administrator/agent/docs/RETRIEVAL_UPGRADE_V6_REPORT_20260723.md) | v6 技术报告（11 章） |
| [RETRIEVAL_UPGRADE_V6_1_REPORT.md](file:///c:/Users/Administrator/agent/docs/RETRIEVAL_UPGRADE_V6_1_REPORT.md) | v6.1 booking 规则评估报告 |
| [RETRIEVAL_UPGRADE_V6_2_REPORT.md](file:///c:/Users/Administrator/agent/docs/RETRIEVAL_UPGRADE_V6_2_REPORT.md) | v6.2 语义拒绝层评估报告 |
| ~~[QUERY_PATTERN_V61_BOOKING_VALIDATION_PLAN.md](file:///c:/Users/Administrator/agent/docs/QUERY_PATTERN_V61_BOOKING_VALIDATION_PLAN.md)~~ ⚠ **【2026-10-03 实测】该文件不存在**；对应脚本 `scripts/verify_v61_booking_rule.py` 仍在 | — |
| [v6.2_query_intent_generalization_plan.md](file:///c:/Users/Administrator/agent/.trae/documents/v6.2_query_intent_generalization_plan.md) | v6.2 实施计划 |
| [yunshu-v6-query-pattern-alerts.yml](file:///c:/Users/Administrator/agent/monitoring/prometheus/rules/yunshu-v6-query-pattern-alerts.yml) | Prometheus 告警规则（含 v6.2 增量） |

---

**文档版本**: 1.1（追加 v6.2 章节）
**生成日期**: 2026-07-24
**适用 commit**: v6.1 = 244b49d7 / v6.2 = TBD（校准验证后回填）
