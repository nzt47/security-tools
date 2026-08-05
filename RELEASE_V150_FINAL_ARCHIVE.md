# v1.5.0 发布最终归档清单

- **版本**: `v1.5.0-bm25-normalization`（BM25 短文档归一化优化，b=0.75 → 0.5）
- **发布日期**: 2026-08-05
- **Release Tag**: `v1.5.0-bm25-normalization`（annotated，指向 `9f6289f2`）
- **同步状态**: origin (GitHub) + gitee 双远程已推送；master 与 origin/master 同步

---

## 1. 发布链接

| 项 | 链接 |
|---|---|
| GitHub Release | https://github.com/nzt47/security-tools/releases/tag/v1.5.0-bm25-normalization |
| Release 事件 CI Run | https://github.com/nzt47/security-tools/actions/runs/31021421032 |
| 远程标签 (origin) | `refs/tags/v1.5.0-bm25-normalization` → `9f6289f2` |

## 2. 提交哈希清单

| 提交 | 说明 |
|------|------|
| `9de2fb45` | **核心变更**：vector_store.py b=0.75→0.5 + k1/b 可配置 + config.yaml BM25 权重 + 验证脚本与文档 |
| `66cdcd9e` | **CI 回归集成**：ci.yml 新增 verify_bm25_optimization + verify_bm25_extreme_cases 两个回归 step |
| `9f6289f2` | **归档提交（tag 指向）**：里程碑邮件 + commit 审计 + wiki 渲染验证 |
| `e85ebf54` | **发布补充**：proper-noun CI 集成 + P0 网格扫描任务 + RELEASE_NOTES + Mermaid 图表 |

## 3. 变更文件列表

### 3.1 9de2fb45 — 核心变更

| 文件 | 变更 |
|---|---|
| `memory/vector_store/vector_store.py` | `_DEFAULT_B` 0.75→0.5，InvertedIndex 支持 k1/b 构造参数 |
| `config.yaml` | `use_bm25: true` + `bm25: 0.2→0.5`（专有名词命中率 86%→100%） |
| `scripts/verify_bm25_optimization.py` | 基础对照验证脚本（+192） |
| `scripts/verify_bm25_extreme_cases.py` | 极端短文档场景验证脚本（+289） |
| `scripts/verify_bm25_proper_noun.py` | 专有名词精确匹配验证脚本（+349） |
| `scripts/set_bm25_weight.ps1` | BM25 权重设置脚本（+145） |
| `agent/skills_mgmt/bm25_searcher.py` | BM25SkillSearcher 实现（+349） |
| `tests/unit/test_vector_store_fallback.py` | BM25 长度归一化测试（+458） |
| `tests/unit/test_bm25_skill_searcher.py` | 技能检索器测试（+727） |
| `docs/BM25_B_PARAMETER_RATIONALE.md` | b 值选择依据技术文档（+230） |
| `docs/wiki/BM25_OPTIMIZATION_WIKI.md` | 优化总结报告（+280） |

### 3.2 66cdcd9e — CI 回归集成

| 文件 | 变更 |
|---|---|
| `.github/workflows/ci.yml` | 新增 verify_bm25_optimization + verify_bm25_extreme_cases 两个 BM25 回归 step（timeout 45→60） |

### 3.3 9f6289f2 — 归档提交（tag 指向）

| 文件 | 变更 |
|---|---|
| `docs/wiki/BM25_COMMIT_MESSAGE.md` | commit 审计指引（+125） |
| `docs/wiki/BM25_MILESTONE_EMAIL.md` | 里程碑邮件草稿（+97） |
| `scripts/verify_wiki_rendering.py` | wiki 渲染验证脚本（+147） |

### 3.4 e85ebf54 — 发布补充

| 文件 | 变更 |
|---|---|
| `.github/workflows/tool-retrieval-ci.yml` | proper-noun job + push/PR 触发路径（+56） |
| `RELEASE_NOTES.md` | v1.5.0 正式发布说明（+96） |
| `docs/TLM_REFACTOR_TASKS.md` | §7 P0 k1+b 网格扫描任务（+36） |
| `docs/wiki/BM25_FINAL_DELIVERY_CHECKLIST.md` | 最终交付清单（+94） |
| `docs/wiki/BM25_RELEASE_LOG.md` | 发布日志（+130） |
| `docs/wiki/BM25_TECHNICAL_RETROSPECTIVE.md` | 技术复盘（+111） |
| `docs/wiki/REPOSITORY_SNAPSHOT_REPORT.md` | 仓库快照报告（+84） |

## 4. CI 验证状态（Release 事件）

**Run #31021421032**（文档自动构建，event=release，head=`9f6289f2`）→ **✅ success**

| Job | 耗时 | 结果 |
|---|---|---|
| 构建 API 文档 | 24s | ✓ |
| 部署到 GitHub Pages | 13s | ✓ |
| 发布通知 | 3s | ✓ |

历史 release 事件运行（v1.0.1 / v1.1.0）均为 success，与本版本无关联。

## 5. 效果数据（供审计）

| 指标 | 优化前 (b=0.75) | 优化后 (b=0.5) | 改善 |
|------|----------------|----------------|------|
| 平均短/长得分比 | 1.98x | 1.48x | 降幅 25.0% |
| 极端场景（1-token vs 50-token） | 2.52x | 1.81x | 降幅 28.3% |
| 回归测试 | — | 基础 3/3 + 极端 5/5 + 单测 119 passed | 全部通过 |

## 6. 归档文档索引

- [RELEASE_NOTES.md](./RELEASE_NOTES.md)
- [docs/BM25_B_PARAMETER_RATIONALE.md](./docs/BM25_B_PARAMETER_RATIONALE.md)
- [docs/wiki/BM25_OPTIMIZATION_WIKI.md](./docs/wiki/BM25_OPTIMIZATION_WIKI.md)
- [docs/wiki/BM25_RELEASE_LOG.md](./docs/wiki/BM25_RELEASE_LOG.md)
- [docs/wiki/BM25_TECHNICAL_RETROSPECTIVE.md](./docs/wiki/BM25_TECHNICAL_RETROSPECTIVE.md)
- [docs/wiki/BM25_FINAL_DELIVERY_CHECKLIST.md](./docs/wiki/BM25_FINAL_DELIVERY_CHECKLIST.md)
- [docs/wiki/REPOSITORY_SNAPSHOT_REPORT.md](./docs/wiki/REPOSITORY_SNAPSHOT_REPORT.md)
- [docs/wiki/BM25_COMMIT_MESSAGE.md](./docs/wiki/BM25_COMMIT_MESSAGE.md)
- [docs/wiki/BM25_MILESTONE_EMAIL.md](./docs/wiki/BM25_MILESTONE_EMAIL.md)
- [docs/TLM_REFACTOR_TASKS.md](./docs/TLM_REFACTOR_TASKS.md)（§7 P0 k1+b 网格扫描任务，待启动）
