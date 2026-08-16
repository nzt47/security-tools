# CI 环境 L3 修复验证报告（镜像 context 同步规避方案）

> 日期：2026-08-16
> 关联：PR #634 收尾 · L3 存储后端降级 json 根因修复 · `fix(ci)` d01c1df4

## 1. 修复内容回顾

| 修复项 | 文件 | 说明 |
|---|---|---|
| HF 缓存路径（hub 后缀） | `docker-compose.linux-test.yml` | `predownload-models`/`test` 服务 `TRANSFORMERS_CACHE`/`SENTENCE_TRANSFORMERS_HOME` 指向 `/app/.hf_cache/hub`，与 `vector_store._is_model_fully_cached` 检查路径及 `predownload_models.py` 落盘位置一致 |
| 预下载脚本 | `scripts/predownload_l3_hf_cache.ps1` | 复用 `predownload_models.py`，经 `HF_ENDPOINT=https://hf-mirror.com` 镜像站把 bge/MiniLM 拉取到 `hf-cache` 卷（`huggingface.co` 直连不通，实测镜像站可达） |

## 2. 本地验证结果（2026-08-16）

### 2.1 判定逻辑验证（diag_sqlite_vec_fallback.py，容器内）

| 环节 | 修复前 | 修复后 |
|---|---|---|
| 卷内模型 | 无 | MiniLM-L12-v2 (384d) + bge-small-zh-v1.5 |
| `model_fully_cached` | `False` | `True` |
| `encoder_ok` / `st_ok` | `False` / `False` | `True` / `True` |
| 集成路径 `backend` | `json`（降级） | `sqlite_vec`（`✅ sqlite-vec 后端启用 (dim=384)`） |

### 2.2 回归测试（sqlite-vec 模式，5 个核心文件）

```
124 passed, 6 skipped in 17.78s（退出码 0）
```

6 个 skipped 均为 `test_memory_vector_store.py` 中需 `--runslow` 的慢速用例，属预期。

## 3. 发现的问题：镜像 context 同步漂移

### 3.1 现象

首次回归（未挂载 `./agent`）130 项测试全部 ERROR：

```
ModuleNotFoundError: No module named 'agent.skills_mgmt.lineage'
```

根因链：`tests/unit/conftest.py` 的 autouse fixture `_isolate_evolution_archive`（L511-530）import `agent.skills_mgmt.lineage` → 旧镜像（构建于并行会话新增该模块之前）内无此文件 → import 失败 → 全部测试 setup ERROR。

### 3.2 根因

- `docker-compose.linux-test.yml` 各测试服务**仅挂载 `./tests`**，项目代码 `/app/agent` 取自镜像层（`Dockerfile.linux-test` `COPY . .` 构建时的工作区快照）。
- 并行会话在镜像构建期间新增/更新了 `agent/skills_mgmt/*.py`（如 `lineage.py`、`meta_editor.py` 等 13 个文件），但这些文件**未进入 Docker build context**（context 打包在构建开始时进行，与并行会话写入存在竞态）。
- 结果：镜像内 `skills_mgmt/` 仅 28 个文件（工作区 43 个），挂载的最新 `conftest.py` 与陈旧的镜像代码不一致。

### 3.3 判定（非修复回归）

该问题与 HF 缓存路径修复无关，是**并行开发环境下镜像与工作区代码漂移**导致的环境性故障。验证时通过运行时挂载 `./agent:/app/agent:ro`（工作区 = HEAD，git 干净）消除漂移后，124 用例全通过，证明修复闭环成立。

## 4. CI 环境规避方案

CI（GitHub Actions）中 L3 任务在**全新 checkout** 上运行，天然规避并行漂移，但仍建议以下措施防御：

### 方案 A（推荐，CI 侧）——构建前校验关键模块存在性

在 L3 工作流 `docker compose build` 之前增加 pre-flight 校验，防止镜像缺模块导致回归误判：

```yaml
- name: Preflight check module completeness
  run: |
    for f in agent/skills_mgmt/lineage.py agent/skills_mgmt/meta_editor.py; do
      test -f "$f" || { echo "MISSING: $f"; exit 1; }
    done
```

### 方案 B（推荐，本地侧）——构建/复跑前强制同步镜像

`run_l3_regression_tests.ps1` 的 `-Rebuild` 语义强化：构建前 `git status --porcelain agent/` 校验无未提交修改（或提示 `-Rebuild` 会打包当前工作区），避免用陈旧镜像跑最新 conftest：

```powershell
# run_l3_regression_tests.ps1 新增前置检查（伪码）
if (-not (Test-Path "agent/skills_mgmt/lineage.py")) {
    Write-Error "关键模块缺失：先执行 git pull / 还原工作区后重试"
    exit 3
}
```

### 方案 C（可选，长期）——运行时挂载 agent 消除镜像漂移

在 `docker-compose.linux-test.yml` 各测试服务增加 `./agent:/app/agent:ro` 挂载，使测试代码与工作区实时一致（`tests/` 已是此策略）。代价：镜像层 `COPY . .` 的 agent 不再被使用，测试语义从"镜像内代码"变为"工作区代码"，与 `tests/` 挂载策略一致。

### 方案 D（可选，脚本侧）——补全工作区代码进镜像的可靠方式

`.dockerignore` 已排除大量运行时目录，但**不排除** `agent/`。若需保证镜像含全部代码，可改为显式逐项 COPY：

```dockerfile
COPY agent/ agent/
COPY memory/ memory/
COPY scripts/ scripts/
# 其余（data/ 等运行时产物）不 COPY
```

替代 `COPY . .`，从根上消除"工作区杂项漂移进镜像 / 新文件漏进镜像"两类问题。

## 5. 结论

- 修复已本地验证闭环：`model_fully_cached=True → st_ok=True → backend=sqlite_vec`，回归 **124 passed / 0 failed**。
- CI 全新 checkout 天然规避 context 漂移；建议叠加方案 A（pre-flight 校验）与方案 D（显式 COPY）作为防御。
- 本地复跑 L3 前，建议执行 `docker compose -f docker-compose.linux-test.yml build test-sqlite-vec` 或按方案 C 挂载 `agent/`，确保镜像与工作区代码一致。
