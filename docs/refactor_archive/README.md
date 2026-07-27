# Refactor Archive - 已淘汰代码参考备份

本目录保存被有意 refactor 淘汰但可能仍有参考价值的代码。
备份目的：防止误把"有意 refactor"判为"异常进程破坏"，并提供回滚参考。

## loader_v6_query_patterns_1159d88f-prior.py

- **来源 commit**: `8dd0da39`（commit 1159d88f 的父提交，仍含完整 v6 代码）
- **refactor commit**: `1159d88f` "refactor(tlm): 清理 loader Query 模式 + 优化 reranker 阈值 + v64 恢复补丁"
- **淘汰日期**: 2026-07-27
- **淘汰原因**: 为 TLM 三层路由让路（commit message 明示）
- **行数**: 1278 行（vs HEAD refactor 后 1058 行，差异 +220 行核心代码）

### 包含的 v6 代码

- `_QUERY_PATTERNS`: 5 个 0% 拒绝率类别的正则规则（keyword_trap / translation / creative / math / similar / booking）
- `_match_query_pattern`: 最早拒绝非技能意图的方法
- `_match_intent_by_embedding`: v6.2 语义拒绝层（BGE-m3 embedding 相似度）
- `_get_negative_intent_detector`: NegativeIntentDetector 懒加载单例
- v6.4 预热修复: `_try_vector_match` 中 `adapter.ensure_indexed()` 调用

### 用途

- **历史参考**: v6 query 模式识别规则的实现细节
- **回滚参考**: 如需恢复 v6 路径，可对照此文件
- **防护目的**: 避免误把"有意 refactor"判为"异常进程破坏"

### ⚠️ 重要警告

此备份仅供参考，**不应直接 `git checkout` 还原**——那会撤销 `1159d88f` 的有意 refactor，
且作者已在 `scripts/verify_v64_vector_recovery.py` 中添加 `encode_query` monkey-patch
专门防止这种误回滚。

## 历史背景

2026-07-28 会话中，曾因未先查 commit history 确认 HEAD 本身状态，
误把 `1159d88f` 的有意 refactor 判为"异常进程破坏"，
执行了 `git checkout 8dd0da39 -- loader.py` 错误恢复 264 行代码。

发现 commit message 明示"为 TLM 三层路由让路"后，已通过 `git checkout HEAD -- loader.py`
撤销恢复，回到正确状态。本备份文件作为该事件的历史参考留存。
