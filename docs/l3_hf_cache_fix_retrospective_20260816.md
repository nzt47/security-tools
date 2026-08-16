# L3 存储后端降级 json 修复 — 完整技术复盘

> 日期：2026-08-15 ~ 2026-08-16
> 作者：CI 收尾会话（PR #634 合并后）
> 范围：问题定位 → 脚本生成 → 测试验证 → CI 防护

## 1. 背景

PR #634（develop → master）合并后，L3 Docker 回归测试中 `TestSqliteVecBackend`
持续失败：扩展本身可用（直接实例化 add/search/count 全通过），但**集成路径
降级 json 后端**。本复盘记录从现象到闭环的完整过程。

## 2. 问题定位（分阶段证据链）

### 2.1 阶段一：判定逻辑还原

读取 `memory/vector_store/vector_store.py` 后端选择链（L454-L488）：

```
_is_model_fully_cached(model_name)  → encoder_ok  → st_ok  → 后端选择
    │                                  │             │
    └ 检查 {HF_HOME}/hub/models--...   └ False 时     └ st_ok=True 才尝试
       /snapshots/ 权重文件               子进程在线探测    sqlite_vec/chromadb
```

关键判定：`st_ok = HAS_SENTENCE_TRANSFORMERS or encoder_ok`；
`model_fully_cached=True → encoder_ok=True`（无需实际加载）。

### 2.2 阶段二：独立排查脚本

生成 [`scripts/diag_sqlite_vec_fallback.py`](../scripts/diag_sqlite_vec_fallback.py)
（独立于 pytest，不加载 conftest 封禁），5 阶段探测：环境 → sqlite_vec 扩展 →
直接后端 → 编码器可用性（`st_ok`）→ 集成路径（子进程 `_get_shared_encoder`）。

本机（Windows）结果：`backend=sqlite_vec`，`st_ok=True`
（`HAS_ST=False` 但 `model_fully_cached=True` → `encoder_ok=True`）。

### 2.3 阶段三：容器内复现（网络根因）

在 L3 Docker 容器内运行诊断，复现 L3 失败路径：

| 环节 | 本机（Windows） | L3 容器内 |
|---|---|---|
| `model_fully_cached` | True | **False**（卷无模型） |
| `encoder_ok` / `st_ok` | True / True | **False / False** |
| 后端 | sqlite_vec | **json** |

归因链：容器内 `huggingface.co` **直连不通**（`Connection refused`）→ 构建阶段
模型预下载 0/3 失败 → `hf-cache` 卷无模型 → `model_fully_cached=False` →
`st_ok=False` → 直接 JSON fallback。

### 2.4 阶段四：镜像站下载 + 路径语义修复

- 实测本机 `hf-mirror.com` **可达**（`TcpTestSucceeded=True`），`huggingface.co` 不通
- 生成 [`scripts/predownload_l3_hf_cache.ps1`](../scripts/predownload_l3_hf_cache.ps1)，
  注入 `HF_ENDPOINT=https://hf-mirror.com` 复用 `predownload_models.py`，
  将 MiniLM-L12-v2 + bge-small-zh-v1.5 拉取到 `hf-cache` 卷
- **第二次发现**：即便模型进卷，compose 的 `test` 服务
  `TRANSFORMERS_CACHE=/app/.hf_cache`（无 `/hub` 后缀）与缓存落盘路径不一致
  → 编码器加载仍失败。修复为 `/app/.hf_cache/hub`（与 Dockerfile ENV、
  `predownload_models.py` 落盘位置、`_is_model_fully_cached` 检查路径四方对齐）

修复后容器内实测：`model_fully_cached=True → st_ok=True → backend=sqlite_vec`。

### 2.5 阶段五：context 漂移（二次故障）

回归首跑 130 项全 ERROR：`ModuleNotFoundError: agent.skills_mgmt.lineage`。
根因：挂载的 `tests/conftest.py`（最新）autouse fixture import `lineage.py`，
但镜像（构建于并行会话新增该模块之前）内无此文件 —— 镜像 context 与工作区
代码漂移。挂载 `./agent:/app/agent:ro` 后 124 passed / 0 failed 验证闭环。

## 3. 修复与防护措施

| # | 措施 | 文件 | 作用 |
|---|---|---|---|
| 1 | HF 缓存路径 hub 后缀 | `docker-compose.linux-test.yml` | 消除编码器加载失败 → json 降级 |
| 2 | hf-mirror 预下载脚本 | `scripts/predownload_l3_hf_cache.ps1` | 离线/镜像站拉取模型到卷 |
| 3 | context 预检脚本 | `scripts/ci_l3_context_preflight.py` | CI 构建前 fail fast（4 项校验） |
| 4 | 预检接入 CI | `.github/workflows/l3-docker-tests.yml` | build-image 检出后自动执行 |
| 5 | 自动化测试守护 | `tests/unit/test_ci_l3_context_preflight.py` | 11 用例守护拦截逻辑 |

## 4. 验证结果

- 判定链：`model_fully_cached False→True`、`st_ok False→True`、
  集成路径 `backend json→sqlite_vec`（`✅ sqlite-vec 后端启用 (dim=384)`）
- L3 sqlite-vec 回归：**124 passed / 0 failed / 6 skipped**（`--runslow`）
- 预检脚本本地复验：4 项全过（EXIT=0，文本 + JSON 双模式）
- 单测守护：11 用例全过（通过/失败路径 + JSON 契约 + 边界场景）

## 5. 经验教训

1. **路径语义是隐性契约**：`HF_HOME` vs `{HF_HOME}/hub` 不一致是三次故障的
   共同根源（下载落盘、检查路径、加载路径必须四方向对齐）。
2. **容器内网络 ≠ 本机网络**：直连不通不等于不可下载，国内镜像站（hf-mirror）
   是有效通道；诊断必须先实测连通性再下结论。
3. **镜像快照是时间点拷贝**：`COPY . .` 只反映构建时的 context；并行开发下
   必须用预检脚本 + 全新 checkout 保证一致性。
4. **测试须隔离工作区状态**：依赖"真实仓库干净"的用例脆弱，应注入模拟输入；
   预检的通过/失败路径都要测（本复盘教训：首版 3 个 clean 用例因自身编辑
   而误失败，重构为 mock 后稳定）。

## 6. 交付物清单

- 修复：`docker-compose.linux-test.yml`、`scripts/predownload_l3_hf_cache.ps1`
- 诊断：`scripts/diag_sqlite_vec_fallback.py`
- 防护：`scripts/ci_l3_context_preflight.py` + CI workflow 接入
- 测试：`tests/unit/test_ci_l3_context_preflight.py`（11 用例）
- 文档：`docs/ci_l3_context_sync_verify_20260816.md`（验证报告）、
  `docs/ci_preflight_integration_guide_20260816.md`（CI 接入指南）、
  `docs/l3_hf_cache_fix_retrospective_20260816.md`（本文档）
- 知识库：`README.md`「L3 Docker 测试」章节、`CHANGELOG.md` 条目
