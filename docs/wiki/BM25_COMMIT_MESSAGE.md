# BM25 优化 Commit Message 整理

> 用途：供后续回滚或审计参考
> 归档位置：`docs/wiki/BM25_COMMIT_MESSAGE.md`
> 日期：2026-07-30
> **重要说明**：经 git 核查，本次 BM25 优化的全部代码变更已在提交 `9de2fb45`（feat: restore reranker worktree and fix ci-cd config）中进入 HEAD。当前工作区相对 HEAD 仅存在行尾符（LF/CRLF）差异，无内容变更。下文整理的 commit message 用于**审计与回滚定位**。

---

## 建议 Commit Message（整体变更）

```text
perf(vector_store): BM25 长度归一化 b 0.75->0.5 缓解短文档虚高 + CI 回归守护

【问题】InvertedIndex 使用 BM25 检索英文关键词时，b=0.75 导致短文档得分虚高，
查询 "machine learning" 时 2-token 短文档得分(1.35)反超长文档(0.77)。

【修复】b 默认值 0.75->0.5，支持 BM25_K1/BM25_B 环境变量动态配置
- memory/vector_store/vector_store.py:
  - L32-36 新增 _DEFAULT_K1/_DEFAULT_B（读环境变量，默认 1.5/0.5）
  - InvertedIndex.__init__ 接收 k1/b 参数，fallback 到模块默认常量
  - _compute_bm25 使用 self._k1/self._b
- 效果: 平均短/长得分比 1.98x->1.48x（降幅 25%），极端场景 28.3%

【验证】新增回归测试
- scripts/verify_bm25_optimization.py: 基础对照 3 用例全通过
- scripts/verify_bm25_extreme_cases.py: 极端场景 5 场景全通过
- tests/unit/test_vector_store_fallback.py: 新增 TestBM25LengthNormalization

【CI】每次提交自动运行两个 BM25 回归 step，失败输出 ::error:: 标记
- .github/workflows/ci.yml: L232-248

【回滚】export BM25_B=0.75 即可恢复原行为（无需改代码）

【诚实声明】b=0.5 缓解而非消除短文档虚高（BM25 数学结构使然）
```

---

## 按文件拆分的 Commit Message

### 1. vector_store.py（核心算法变更）

```text
perf(vector_store): BM25 长度归一化 b 0.75->0.5 缓解短文档虚高

- 新增模块级 _DEFAULT_K1(1.5)/_DEFAULT_B(0.5)，读 BM25_K1/BM25_B 环境变量
- InvertedIndex 支持 k1/b 构造参数，动态调参无需改代码
- _compute_bm25 评分使用实例参数
- 平均短/长得分比 1.98x->1.48x（降幅 25%）
- 回滚方式: BM25_B=0.75
```

### 2. ci.yml（CI 回归守护）

```text
ci(unit-tests): 新增 BM25 优化与极端场景回归测试 step

- verify_bm25_optimization.py: 基础对照 3 用例
- verify_bm25_extreme_cases.py: 极端场景 5 场景
- 失败时输出 ::error::/::warning:: GitHub Actions 标记 + 用例详情
```

### 3. 验证脚本与测试（验证资产）

```text
test(vector_store): BM25 b=0.5 优化验证脚本与单测

- scripts/verify_bm25_optimization.py: 短/长文档对照实验（3 用例）
- scripts/verify_bm25_extreme_cases.py: 极端场景验证（1-token/term_freq/空文档/b 扫描）
- tests/unit/test_vector_store_fallback.py: TestBM25LengthNormalization（2 用例）
- 全部验证通过: 基础 3/3 + 极端 5/5 + 单测 119 passed
```

### 4. 文档（审计归档）

```text
docs(vector_store): BM25 b 参数选择依据 + 优化总结报告归档

- docs/BM25_B_PARAMETER_RATIONALE.md: b 值数学推导 + 实测数据 + 决策依据
- docs/wiki/BM25_OPTIMIZATION_WIKI.md: 完整优化过程/数据/结论
- docs/wiki/BM25_MILESTONE_EMAIL.md: 团队同步邮件草稿
```

---

## 回滚操作指引

### 方式一：环境变量（推荐，无需改代码）

```bash
export BM25_B=0.75    # Linux/macOS
$env:BM25_B = "0.75"  # Windows PowerShell
```

### 方式二：git revert（回滚到 b=0.75 提交前）

```bash
# 查看包含 BM25 变更的提交
git log --oneline -S "BM25_B" -- memory/vector_store/vector_store.py
# 9de2fb45 feat: restore reranker worktree and fix ci-cd config

# 若需精确回滚 vector_store.py 到 b=0.75 版本（谨慎操作，建议先备份）
git log --oneline -- memory/vector_store/vector_store.py  # 找到 b=0.75 的上一个提交
```

### 方式三：git show 审计（查看变更详情）

```bash
git show 9de2fb45 -- memory/vector_store/vector_store.py .github/workflows/ci.yml
```

---

## 审计要点

| 项 | 说明 |
|----|------|
| 变更入口 | 提交 `9de2fb45` |
| 核心文件 | `memory/vector_store/vector_store.py`（b=0.5 默认值） |
| CI 配置 | `.github/workflows/ci.yml`（两个 BM25 回归 step） |
| 验证资产 | `scripts/verify_bm25_*.py` + `tests/unit/test_vector_store_fallback.py` |
| 文档 | `docs/BM25_B_PARAMETER_RATIONALE.md` + `docs/wiki/BM25_*.md` |
| 回滚 | `BM25_B=0.75` 环境变量，零代码改动 |
| 兼容性 | 119 项搜索/排序测试全通过；与 BM25SkillSearcher 正交 |
