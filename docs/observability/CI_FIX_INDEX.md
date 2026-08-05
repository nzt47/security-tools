# CI 修复记录索引（自动生成）

> 由 `scripts/publish_fix_to_docs.py` 维护，按时间倒序。
> 推送 `docs/**` 变更会触发 deploy-pages.yml 部署到 GitHub Pages。

| Commit | 修复点 | 日期 |
|--------|--------|------|
| `5a803e2` | fix(tests): 修复 Shard 4 幂等性回归与文档链接预检误失败 | 2026-08-05 |
| `e859f22` | fix(ci): 重建 ci_guard_types 契约校验 + safe_git_revert stdout 纯净化 + 巡检工具转正 | 2026-08-05 |
| `bec0426` | fix(ci): 恢复被误覆盖的 simulate_ci_pipeline 原版, 新脚本改名 simulate_ci_guard_pipeline | 2026-08-05 |
