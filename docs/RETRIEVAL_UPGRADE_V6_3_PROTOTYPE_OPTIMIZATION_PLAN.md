# v6.3 Prototype 冲突优化计划 — 样本清洗与扩充

**日期**: 2026-07-24
**前置版本**: v6.2（阈值 0.71，正样本 0 误伤，负样本覆盖率 64%）
**目标版本**: v6.3（解决 prototype 冲突，支持更低阈值，提升覆盖率至 ≥ 80%）

---

## 1. 背景与问题

### 1.1 v6.2 遗留问题

v6.2 校准脚本（`calibrate_v62_threshold.py`）发现 2 个 prototype 与技能 description 的冲突：

| Prototype 类别 | 冲突技能 | 相似度 | 阈值（0.71）下是否安全 |
|---------------|---------|--------|----------------------|
| programming | context_aware | 0.5029 | ✅ 安全（< 0.71） |
| programming | voice_interaction | 0.5111 | ✅ 安全（< 0.71） |

**当前影响**：在阈值 0.71 下，2 个冲突均安全（相似度 < 0.71），不影响正样本 0 误伤。

**v6.3 动机**：
1. 若未来希望降低阈值至 0.65 以提升负样本覆盖率，这 2 个冲突会成为瓶颈
2. prototype 语义纯净度不足，存在"语义漂移"风险（长期运行后累积偏移）
3. 25 个负样本中 9 个漏判（v6.2 覆盖率 64%），需通过优化 prototype + 降低阈值提升至 ≥ 80%

### 1.2 冲突根因分析

#### 冲突 1: programming vs context_aware (0.5029)

**context_aware 技能 description**：
> 持续追踪对话主题、用户意图与时间线的演变，在上下文发生切换或漂移时自动调整回应策略与记忆检索范围

**programming prototype 样本（5 个）**：
1. `def print_hello_world function` — 英文代码片段
2. `java python c++ programming` — 编程语言列表
3. `how to implement quick sort` — 英文算法问题
4. `写一个 python 函数` — 中文编程请求
5. `如何实现二分查找` — 中文算法问题

**根因**：
- context_aware description 含通用动词"调整/演变/切换"，与 programming 的"实现/写一个"在中文动词语义空间有重叠
- BGE-m3 对中文动词的语义编码较粗粒度，"实现"与"调整"在"改变状态"的语义维度上相近
- 英文样本（3/5）与中文技能 description 的跨语言相似度贡献不均匀

#### 冲突 2: programming vs voice_interaction (0.5111)

**voice_interaction 技能 description**：
> 通过语音输入与输出与用户进行交互，支持语音转文字识别、文字转语音合成与语音指令路由，适用于免手操作与无障碍场景

**根因**：
- voice_interaction description 含"输入与输出/识别/路由"，与 programming 的 `function`/`implement` 在"输入输出处理"语义维度有重叠
- "识别"在编程语境中也有"解析/parse"含义，导致语义混淆
- programming prototype 的英文样本 `def print_hello_world function` 中 `function` 一词与语音指令路由的"功能"概念在 BGE-m3 编码空间相近

---

## 2. 优化目标

### 2.1 【不易】核心约束（不可破坏）
1. 正样本 0 误伤（v6.2 已达成，v6.3 必须保持）
2. 负样本拒绝率 100%（25/25）
3. prototype 与所有技能 description 相似度 < 0.45（比 v6.2 的 0.5 更严格，留 margin）
4. 失败降级机制保留

### 2.2 【变易】优化目标
1. **冲突消除**：programming prototype 与 context_aware/voice_interaction 相似度降至 < 0.45
2. **覆盖率提升**：负样本覆盖率从 64% (16/25) 提升至 ≥ 80% (20/25)
3. **阈值降低**：支持阈值从 0.71 降至 0.65（留正样本 margin）
4. **漏判减少**：9 个 v6.2 漏判负样本中至少覆盖 4 个

### 2.3 【简易】设计原则
1. 不改代码（仅改 prototype JSON 数据）
2. 不新增依赖
3. 复用 v6.2 校准脚本验证

---

## 3. 样本清洗方案

### 3.1 programming 类别清洗

**清洗策略**：移除英文样本 + 移除通用动词样本，改为纯中文具体编程问题。

#### 3.1.1 移除样本（3 个）

| 原样本 | 移除原因 |
|--------|---------|
| `def print_hello_world function` | 英文 + `function` 与 voice_interaction 的"功能"语义重叠 |
| `java python c++ programming` | 英文 + 语言列表过于宽泛，与技能 description 的"语言"无具体区分 |
| `how to implement quick sort` | 英文 + `implement` 与 context_aware 的"调整策略"动词重叠 |

#### 3.1.2 保留样本（2 个）

| 保留样本 | 保留原因 |
|---------|---------|
| `写一个 python 函数` | 中文，编程意图明确 |
| `如何实现二分查找` | 中文，算法问题明确 |

#### 3.1.3 新增样本（5 个）

| 新增样本 | 选择理由 |
|---------|---------|
| `python 列表怎么去重` | 具体语法问题，避免"实现"等通用动词 |
| `这段代码报错了 syntax error` | 调试场景，与技能 description 词汇无重叠 |
| `git 怎么回退上一个 commit` | 版本控制问题，纯开发场景 |
| `sql 查询语句怎么写` | 数据库查询，与技能语义空间正交 |
| `正则表达式匹配邮箱` | 正则问题，具体技术领域 |

#### 3.1.4 清洗后 programming 样本（7 个）

```json
{
  "category": "programming",
  "description": "编程技术问题类 — 非技能意图（v6.3 清洗：移除英文样本，改为纯中文具体编程问题）",
  "samples": [
    "写一个 python 函数",
    "如何实现二分查找",
    "python 列表怎么去重",
    "这段代码报错了 syntax error",
    "git 怎么回退上一个 commit",
    "sql 查询语句怎么写",
    "正则表达式匹配邮箱"
  ]
}
```

**预期效果**：
- 与 context_aware 相似度：0.5029 → 预计 < 0.42（移除"implement"动词后）
- 与 voice_interaction 相似度：0.5111 → 预计 < 0.40（移除"function"后）

### 3.2 其他类别复核（无冲突，保持不变）

v6.2 校准显示仅 programming 类别有冲突，其余 9 类（weather/noise/entertainment/finance/cooking/sports/medical/daily/greeting）与技能相似度均 < 0.5，**保持不变**（守【简易】，不过度优化）。

---

## 4. 样本扩充方案

### 4.1 扩充目标

针对 v6.2 漏判的 9 个负样本，分析其 prototype 覆盖缺口，扩充对应类别样本。

#### v6.2 漏判负样本分析（阈值 0.71）

| case_id | query | 漏判 sim | 应归属类别 | 当前 prototype 覆盖 |
|---------|-------|---------|----------|------------------|
| case_103 | 帮我订一张机票 | 0.6720 | booking | ❌ 无 booking prototype（v6.1 规则已覆盖） |
| case_104 | 我想订酒店 | 0.6185 | booking | ❌ 无 booking prototype（v6.1 规则已覆盖） |
| case_105 | 帮我点外卖 | 0.6719 | booking | ❌ 无 booking prototype（v6.1 规则已覆盖） |
| case_118 | 帮我删除文件 | 0.6209 | similar | ❌ 无 similar prototype（v6.1 规则已覆盖） |
| case_119 | 重启服务器 | 0.5879 | similar | ❌ 无 similar prototype（v6.1 规则已覆盖） |
| case_120 | safety 是什么意思 | 0.4932 | keyword_trap | ❌ 无 keyword_trap prototype（v6.1 规则已覆盖） |
| case_121 | memory 概念解释 | 0.4671 | keyword_trap | ❌ 无 keyword_trap prototype（v6.1 规则已覆盖） |
| case_122 | 请帮我翻译这段话 | 0.7066 | translation | ❌ 无 translation prototype（v6.1 规则已覆盖） |
| case_124 | 帮我算一下 1+1 等于几 | 0.6040 | math | ❌ 无 math prototype（v6.1 规则已覆盖） |

**关键发现**：9 个漏判负样本全部已被 v6.1 正则规则覆盖！这意味着 v6.2 embedding 层的实际目标（v6.1 未覆盖的 15 个负样本）覆盖率已达 13/15 = 86.7%。

### 4.2 扩充策略：补全 v6.2 已覆盖但样本不足的类别

针对 v6.2 已命中但样本数较少（3-4 个）的类别，扩充样本以提升泛化能力，支持未来新句式。

#### 4.2.1 weather 类别扩充（5 → 8 个）

| 新增样本 | 选择理由 |
|---------|---------|
| `北京明天天气` | 含地名，验证地理泛化 |
| `需要带伞吗` | 间接天气询问，句式变化 |
| `温度多少度` | 简化句式，验证省略主语 |

#### 4.2.2 entertainment 类别扩充（4 → 7 个）

| 新增样本 | 选择理由 |
|---------|---------|
| `有什么好看的电影` | 电影推荐，扩充娱乐子类 |
| `给我讲个故事` | 故事请求，与 creative 区分（creative 已被 v6.1 规则覆盖） |
| `有什么好听的音乐推荐` | 音乐推荐，句式变化 |

#### 4.2.3 medical 类别扩充（4 → 7 个）

| 新增样本 | 选择理由 |
|---------|---------|
| `咳嗽怎么办` | 症状询问，扩充症状覆盖 |
| `胃疼吃什么药` | 疼痛 + 用药，组合句式 |
| `发烧多少度需要看医生` | 含数字阈值，验证条件句 |

#### 4.2.4 cooking 类别扩充（4 → 7 个）

| 新增样本 | 选择理由 |
|---------|---------|
| `糖醋排骨怎么做` | 新菜式，扩充菜谱覆盖 |
| `面条怎么煮` | 主食类，扩充品类 |
| `烤箱温度设置多少` | 设备操作，句式变化 |

### 4.3 不扩充的类别（守【简易】）

| 类别 | 不扩充原因 |
|------|----------|
| noise | 4 个样本已足够，噪声类无句式变化需求 |
| finance | 4 个样本已覆盖股票/汇率/加密货币/黄金主要场景 |
| sports | 4 个样本已覆盖跑步/腹肌/篮球/游泳主要场景 |
| daily | 4 个样本已覆盖时间/日期/放假主要场景 |
| greeting | 4 个样本已覆盖问候/身份/名字主要场景 |

---

## 5. 实施步骤

### 5.1 Step 1: 更新 prototype JSON

**修改文件**：`tests/eval/negative_intent_prototypes.json`

**变更内容**：
1. programming 类别：移除 3 个英文样本，新增 5 个中文具体编程问题
2. weather/entertainment/medical/cooking 类别：各新增 3 个样本
3. 版本号：`v6.2-prototypes` → `v6.3-prototypes`
4. threshold_default：0.75 → 0.65（清洗后预期支持更低阈值）

### 5.2 Step 2: 重新校准

```bash
python scripts/calibrate_v62_threshold.py \
    --output tests/eval/v63_threshold_calibration.json
```

**预期结果**：
- programming 与 context_aware 相似度：< 0.45（从 0.5029 降低）
- programming 与 voice_interaction 相似度：< 0.45（从 0.5111 降低）
- 推荐阈值：0.62-0.68（从 0.6873 降低，因正样本 max sim 可能也降低）
- 正样本 0 误伤
- 负样本覆盖率：≥ 80%（从 64% 提升）

### 5.3 Step 3: 端到端验证

```bash
SKILL_NEGATIVE_INTENT_THRESHOLD=0.65 \
python scripts/verify_v62_negative_intent.py \
    --output tests/eval/v63_verify_report.json
```

**预期结果**：
- 正样本 0 误伤（守【不易】）
- 负样本拒绝率 100%
- v6.2 embedding 层命中数：≥ 16（从 13 提升）
- 负样本平均延迟：≤ 150ms

### 5.4 Step 4: 回归测试

```bash
python -m pytest tests/unit/test_negative_intent.py -v
```

**预期结果**：71 passed（TDD 测试不应受影响，因 mock adapter 不依赖真实 prototype）

### 5.5 Step 5: 文档更新

- 更新 `RETRIEVAL_UPGRADE_V6_2_REPORT.md` 附录 v6.3 优化记录
- 更新 `V6_OPS_RUNBOOK.md` §10.2 推荐阈值（若校准后变更）

---

## 6. 风险评估与回退

### 6.1 风险 1: 清洗后正样本 max sim 反而上升

**可能性**：低（移除英文样本不应影响中文正样本相似度）

**应对**：校准后若正样本 max sim > 0.71，回滚到 v6.2 prototype

### 6.2 风险 2: 新增样本引入新冲突

**可能性**：中（新增编程样本可能与 scripted-selftest 技能冲突）

**应对**：校准脚本的 `check_prototype_skill_conflict` 会自动检测，冲突 > 0.45 时移除该样本

### 6.3 风险 3: 阈值 0.65 导致正样本误伤

**可能性**：中（阈值降低后 margin 缩小）

**应对**：若校准验证发现误伤，采用 0.68 作为折中阈值（仍优于 v6.2 的 0.71）

### 6.4 回退预案

若 v6.3 校准或验证失败：
1. **快速回退**：`git checkout v6.2-commit -- tests/eval/negative_intent_prototypes.json`
2. **环境变量回退**：`SKILL_NEGATIVE_INTENT_THRESHOLD=0.71`（用 v6.2 阈值配合 v6.3 prototype，安全降级）

---

## 7. 预期收益

### 7.1 量化指标对比

| 指标 | v6.2 基线 | v6.3 预期 | 改进 |
|------|----------|----------|------|
| prototype 与技能最大相似度 | 0.5111 | < 0.45 | -12%+ |
| 推荐阈值 | 0.71 | 0.62-0.68 | -4%~13% |
| 负样本覆盖率 | 64% (16/25) | ≥ 80% (20/25) | +16%+ |
| v6.2 embedding 层命中数 | 13 | ≥ 16 | +3+ |
| 正样本误伤数 | 0 | 0 | 保持（守【不易】） |
| 负样本平均延迟 | 114ms | ≤ 150ms | 略增（可接受） |

### 7.2 定性收益

1. **prototype 语义纯净度提升**：移除英文样本消除跨语言语义漂移
2. **泛化能力增强**：扩充样本覆盖更多句式变化
3. **阈值 margin 扩大**：正负样本分布间隔更大，阈值选择更稳健
4. **可维护性提升**：样本注释更清晰，便于未来扩展

---

## 8. 三义自检

| 检查项 | 结果 |
|--------|------|
| 【不易】不改 production 代码 | ✅ 仅改 prototype JSON 数据 |
| 【不易】正样本 0 误伤 | ✅ 校准脚本自动验证 |
| 【不易】失败降级保留 | ✅ 不影响 detector 逻辑 |
| 【变易】prototype 数据外部化 | ✅ 沿用 v6.2 设计 |
| 【变易】阈值可配置 | ✅ 环境变量调整 |
| 【简易】无新依赖 | ✅ 复用 v6.2 校准脚本 |
| 【简易】最小改动 | ✅ 仅改 1 个 JSON 文件 |
| 【简易】结构对齐 v6.2 | ✅ 校准/验证流程不变 |

---

## 9. 实施依赖

```
[Step 1: 更新 prototype JSON] ── 无依赖
       │
       ▼
[Step 2: 重新校准] ── 依赖 Step 1
       │
       ▼
[Step 3: 端到端验证] ── 依赖 Step 2
       │
       ▼
[Step 4: 回归测试] ── 依赖 Step 1（并行）
       │
       ▼
[Step 5: 文档更新] ── 依赖 Step 3
```

**建议顺序**：1 → 2 → 3 → 4（并行）→ 5

---

## 10. 关键文件清单

**修改文件**：
- `tests/eval/negative_intent_prototypes.json` — 更新 programming 类别 + 扩充 4 类样本

**新增文件**：
- `tests/eval/v63_threshold_calibration.json` — v6.3 校准报告
- `tests/eval/v63_verify_report.json` — v6.3 端到端验证报告
- `docs/RETRIEVAL_UPGRADE_V6_3_PROTOTYPE_OPTIMIZATION_PLAN.md` — 本文档

**不修改文件**：
- `agent/skills_mgmt/negative_intent_detector.py` — 代码不变
- `agent/skills_mgmt/loader.py` — 代码不变
- `scripts/calibrate_v62_threshold.py` — 脚本不变（复用）
- `scripts/verify_v62_negative_intent.py` — 脚本不变（复用）

---

## 11. 后续展望（v6.4+ 候选）

1. **prototype 动态扩充**：从 tool_trace 挖掘高频非技能 query，自动加入 prototype（需人工审核）
2. **多语言 prototype**：补充英文/混合语言样本（当前 v6.3 移除英文样本是为消除冲突，未来可独立建立英文 prototype 集）
3. **prototype 加权**：不同类别设置不同阈值（如 medical 更严格 0.70，weather 可放宽 0.60）
4. **在线学习**：根据线上 P@3 反馈动态调整 prototype 权重（需告警闭环）
5. **prototype 版本管理**：支持 A/B 测试（新旧 prototype 并行运行，对比效果）
