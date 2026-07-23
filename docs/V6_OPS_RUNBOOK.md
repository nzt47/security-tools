# v6 Query 模式识别 — 运维上线操作手册

**版本**: v6 query_pattern (commit 244b49d7)
**适用对象**: 运维团队（SRE/On-call）
**目标**: 快速上线 v6 + 监控 + 一键回滚

---

## 1. 一句话概要

v6 在 RRF/reranker **之前**增加正则规则匹配，命中非技能意图（"帮我写诗"/"删除文件"等）直接返回空结果，跳过昂贵的向量检索，**负样本拒绝率从 68% 提升到 96%**，正样本 P@3=0.4444 不变。

---

## 2. 关键配置（必读）

| 配置项 | 默认值 | 说明 | 修改方式 |
|--------|--------|------|----------|
| `SKILL_QUERY_PATTERN_ENABLED` | `true` | v6 总开关 | 环境变量 |
| `SKILL_RERANK_MIN_SCORE` | `0.001` | rerank 阈值（v5.1 固化）| 环境变量 |

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
echo $SKILL_QUERY_PATTERN_ENABLED   # 应为空（默认 true）或 true
echo $SKILL_RERANK_MIN_SCORE         # 应为空（默认 0.001）或 0.001
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
# v6 query 模式识别（默认开启，可设 false/0/off/no 禁用）
SKILL_QUERY_PATTERN_ENABLED=true

# rerank 阈值（v5.1 固化，0.001 为最优）
SKILL_RERANK_MIN_SCORE=0.001
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

### 7.1 一键回滚（推荐，不重启服务）

```bash
# 方式 1: 临时禁用 v6（环境变量，立即生效，无需重启）
kubectl exec -n yunshu deployment/yunshu -- \
  env-set SKILL_QUERY_PATTERN_ENABLED=false

# 或直接进入 Pod 修改
kubectl exec -it -n yunshu deployment/yunshu -- bash
echo "SKILL_QUERY_PATTERN_ENABLED=false" >> /app/.env
# 若服务支持热加载，立即生效；否则需重启
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
kubectl exec -n yunshu deployment/yunshu -- env | grep SKILL_QUERY
# 若 SKILL_QUERY_PATTERN_ENABLED=false，改回 true

# 2. 检查调用方是否传 use_reranker=True 且 use_vector=True
kubectl logs -n yunshu deployment/yunshu | grep "match.extension_not_implemented"
# 若有 warning，说明调用方未正确传参
```

### Q2: 某类别命中率为 0？

**排查**:
```bash
# 检查该类别的正则是否被误删
grep "category" agent/skills_mgmt/loader.py | head -10
# 应看到 keyword_trap/translation/creative/math/similar 5 类
```

### Q3: 正样本 P@3 下降？

**立即回滚**:
```bash
export SKILL_QUERY_PATTERN_ENABLED=false
# 然后排查误伤:
grep "match.query_pattern.rejected" /var/log/agent/loader.log | \
  jq -r '.intent' | head -100
```

### Q4: 如何临时禁用某个类别？

**注释规则行**（单行回滚）:
```python
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

## 10. 相关文档

| 文档 | 用途 |
|------|------|
| [V6_MONITORING_CONFIG_CHECKLIST.md](file:///c:/Users/Administrator/agent/docs/V6_MONITORING_CONFIG_CHECKLIST.md) | 完整监控指标清单（10 章） |
| [RETRIEVAL_UPGRADE_V6_REPORT_20260723.md](file:///c:/Users/Administrator/agent/docs/RETRIEVAL_UPGRADE_V6_REPORT_20260723.md) | v6 技术报告（11 章） |
| [QUERY_PATTERN_V61_BOOKING_VALIDATION_PLAN.md](file:///c:/Users/Administrator/agent/docs/QUERY_PATTERN_V61_BOOKING_VALIDATION_PLAN.md) | v6.1 booking 规则验证方案 |
| [yunshu-v6-query-pattern-alerts.yml](file:///c:/Users/Administrator/agent/monitoring/prometheus/rules/yunshu-v6-query-pattern-alerts.yml) | Prometheus 告警规则 |

---

**文档版本**: 1.0
**生成日期**: 2026-07-23
**适用 commit**: 244b49d7
