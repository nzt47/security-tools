# Release Notes — Context 一致性修复与 CI 预检（2026-08-16）

**分支**: develop | **范围**: 5 个提交（`d01c1df4` → `ec8adbf4`）
**性质**: L3 回归稳定性修复 + CI 防护，非版本发布

## 改进点总览

| 类别 | 提交 | 说明 |
|---|---|---|
| 修复：L3 存储后端降级 json | `d01c1df4` | `TRANSFORMERS_CACHE` 指向 `/app/.hf_cache/hub`（与 `_is_model_fully_cached` 检查路径对齐），编码器加载失败 → 降级 json 根因消除 |
| 新增：hf-mirror 模型预下载 | `d01c1df4` | `predownload_l3_hf_cache.ps1` 经 `HF_ENDPOINT=https://hf-mirror.com` 拉取 MiniLM/bge 到 `hf-cache` 卷（容器内 `huggingface.co` 直连不通） |
| 新增：context 预检脚本 | `628616cb` / `ec8adbf4` | `ci_l3_context_preflight.py` 四项校验（构建文件 / 关键模块 / context 目录 git 清洁度 / 已跟踪文件覆盖度），支持 `--json` 与 `PREFLIGHT_ROOT` 注入 |
| 接入：CI 构建前 fail fast | `628616cb` | `l3-docker-tests.yml` 的 `build-image` job 检出后执行预检，失败即中断（防 130 项测试全量 ERROR） |
| 测试：拦截逻辑守护 | `6e16bc82` / `6a8c52d2` / `ec8adbf4` | **15 用例**：校验通过/失败路径 + JSON 契约 + 边界场景 + CI 接入守护 + 端到端模拟（漂移→中断→修复→放行） |
| 文档：团队知识库 | `9b91c934` / `6a8c52d2` | 验证报告 / CI 接入指南（GH Actions + GitLab）/ 技术复盘 / README 章节 / CHANGELOG 条目 |

## 验证结果

- 判定链实测：`model_fully_cached False→True`、`st_ok False→True`、后端 `json→sqlite_vec`（dim=384）
- L3 sqlite-vec 回归：**124 passed / 0 failed** / 6 skipped（`--runslow`）
- 预检脚本：本地复验 4 项全过；端到端模拟 context 漂移正确触发构建中断（rc=1）
- 单元测试：**15 passed / 0 failed**

## 使用指引

```bash
# 本地复跑 L3 前预检
python scripts/ci_l3_context_preflight.py

# 模型缓存缺失时拉取（需网络可访问 hf-mirror.com）
powershell -ExecutionPolicy Bypass -File scripts/predownload_l3_hf_cache.ps1

# 单测守护
python -m pytest tests/unit/test_ci_l3_context_preflight.py -v
```

## 已知说明

- 镜像 context 漂移为并行开发环境性问题，CI 全新 checkout 天然规避；本地复跑建议先 `docker compose build test-sqlite-vec` 或运行时挂载 `./agent`
- 早前 `628616cb`/`9b91c934` 提交信息含 GBK 乱码（终端编码），不影响代码
