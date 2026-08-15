# Context 一致性修复 — 完整闭环总结

> 日期：2026-08-15 ~ 2026-08-16 | 分支：develop | 提交：`d01c1df4` ~ `e06a2f2c`（7 个）

## 一、问题

L3 Docker 回归中 `TestSqliteVecBackend` 持续失败：sqlite_vec 扩展本身可用，
但**集成路径降级 json 后端**；后续又出现 130 项测试全量 ERROR。

## 二、闭环（问题 → 修复 → 防护 → 守护）

| 阶段 | 动作 | 结果 |
|---|---|---|
| 1. 定位 | `diag_sqlite_vec_fallback.py` 五阶段诊断，还原 `model_fully_cached → st_ok → backend` 判定链 | 本机 `sqlite_vec`，容器内 `json` |
| 2. 根因 | 容器内 `huggingface.co` 直连不通 → 模型未缓存 → `st_ok=False` | 镜像站 `hf-mirror.com` 可达 |
| 3. 修复 | compose 缓存路径指向 `{HF_HOME}/hub`；`predownload_l3_hf_cache.ps1` 经镜像站拉模型 | `model_fully_cached=True → st_ok=True → sqlite_vec` |
| 4. 防护 | `ci_l3_context_preflight.py` 四项校验，接入 CI 构建前 fail fast | 漂移在构建前拦截 |
| 5. 守护 | 15 个单测（校验路径 + 边界 + CI 接入 + 端到端模拟） | 防回归 |

## 三、验证结果

- 判定链：`json → sqlite_vec`（dim=384）
- L3 回归：**124 passed / 0 failed**（6 skipped 为 `--runslow`）
- 预检 + 单测：4 项全过 / **15 passed**
- 端到端模拟：context 漂移正确触发构建中断（rc=1），修复后放行（rc=0）

## 四、交付物

```
scripts/diag_sqlite_vec_fallback.py       # 诊断
scripts/predownload_l3_hf_cache.ps1       # 模型预下载（hf-mirror）
scripts/ci_l3_context_preflight.py        # CI 预检
tests/unit/test_ci_l3_context_preflight.py  # 15 用例守护
docs/ci_l3_context_sync_verify_20260816.md         # 验证报告
docs/ci_preflight_integration_guide_20260816.md    # CI 接入指南
docs/l3_hf_cache_fix_retrospective_20260816.md     # 技术复盘
docs/releases/context_consistency_preflight_20260816.md  # 发布说明
README.md（知识库章节）· CHANGELOG.md（变更记录）· .github/workflows/l3-docker-tests.yml（CI 接入）
```

## 五、长期保障

- CI 预检在 `build-image` 前执行（`--json` + 非零退出即中断）
- 测试守护：CI 接入位置被改动或预检逻辑被误改 → 立即红灯
- 知识库沉淀：路径语义 / context 漂移教训已写入 README 与复盘文档

## 六、遗留说明

- 并行会话 `yunshu-ui` 前端与 `app_server.py` 在活跃开发中（独立于本修复）
- 早前 3 个提交信息含 GBK 乱码（`d01c1df4`/`9b91c934`/`628616cb`，终端编码所致，不影响代码，可后续修正）
