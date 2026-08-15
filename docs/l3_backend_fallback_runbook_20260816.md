# L3 后端降级与 Context 漂移 — 故障排查手册（Runbook）

> 标准化 SOP | 2026-08-16 定稿 | 适用：L3 Docker 回归 / 向量存储后端相关故障
> 经验来源：2026-08-15~16 完整修复（详见 [技术复盘](l3_hf_cache_fix_retrospective_20260816.md)）

## 1. 适用范围

L3 Docker 回归测试（`run_l3_regression_tests.ps1 -Mode sqlite-vec`）出现以下任一症状时使用本手册。

## 2. 症状库

| 编号 | 症状 | 典型输出 |
|---|---|---|
| S1 | 集成路径后端降级 json | 测试期望 `sqlite_vec` 实际得 `json`；`存储后端: json` |
| S2 | 全部测试 setup ERROR | `ModuleNotFoundError: No module named 'agent.skills_mgmt.lineage'`（或类似关键模块） |
| S3 | 编码器加载失败/超时 | `Sentence Transformers not installed or import timeout` / `couldn't connect to huggingface.co` |

## 3. 诊断流程（按序执行，命中即止）

```
STEP 1  运行预检脚本 → 4 项全过？ ────否──→ 按 §5 修复对应项
                    │是
STEP 2  容器内跑诊断脚本 diag_sqlite_vec_fallback.py
                    │
        ┌───────────┴──────────────────────────┐
   backend=sqlite_vec                     backend=json
        │                                        │
   ↓ STEP 3 跳过                        st_ok=False ？───是──→ 走 A 支（模型缓存问题）
        │                                        │否
   排查测试代码/用例                     backend=json 但 st_ok=True
                                            → 走 B 支（编码器加载/context）
```

**A 支 — 模型缓存问题**（S1/S3）：`model_fully_cached=False` 是根因。

**B 支 — 编码器加载/context 问题**（S2）：`_get_shared_encoder` 返回 None
（缺模块 / 路径不一致 / 镜像陈旧）。

## 4. 诊断命令速查

```bash
# 0. 预检（30 秒内定位 4 类问题）
python scripts/ci_l3_context_preflight.py --json

# 1. 容器内判定链诊断（st_ok / model_fully_cached / backend）
docker compose -f docker-compose.linux-test.yml run --rm --no-deps \
  --entrypoint python test /app/scripts/diag_sqlite_vec_fallback.py

# 2. 查看卷内模型缓存
docker compose -f docker-compose.linux-test.yml run --rm --no-deps \
  --entrypoint python test /app/scripts/predownload_models.py --list

# 3. 实测网络可达性（先于"换通道"决策）
Test-NetConnection hf-mirror.com -Port 443   # PowerShell
Test-NetConnection huggingface.co -Port 443
```

## 5. 修复步骤（按症状）

### S1/S3 → 模型缓存缺失
1. 确认网络：`hf-mirror.com` 可达（`huggingface.co` 直连通常不通）
2. 拉取模型到卷：
   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts/predownload_l3_hf_cache.ps1
   ```
3. **核对缓存路径四方对齐**（历史根因）：
   `TRANSFORMERS_CACHE`/`SENTENCE_TRANSFORMERS_HOME` 必须 = `{HF_HOME}/hub`，
   且与 `_is_model_fully_cached` 检查路径、`predownload_models.py` 落盘一致
4. 复验：重跑诊断脚本，期望 `backend=sqlite_vec`

### S2 → context 漂移 / 缺模块
1. 确认 `agent/skills_mgmt/lineage.py` 等关键模块在仓库存在且已入库
   （`git ls-files agent/skills_mgmt/ | grep lineage`）
2. 工作区必须干净：`git status --porcelain agent/ memory/ scripts/ tests/` 为空
3. 本地复跑前同步镜像：`docker compose -f docker-compose.linux-test.yml build test-sqlite-vec`
   （或按方案 C 运行时挂载 `./agent:/app/agent:ro`）
4. CI 场景：全新 checkout 天然规避；确保预检步骤在 `build-image` 前执行

## 6. 验证清单（全部通过才算修复）

- [ ] 预检脚本 4 项全过（EXIT=0）
- [ ] 诊断脚本：`model_fully_cached=True`、`st_ok=True`、`backend=sqlite_vec`
- [ ] 回归测试：`124 passed / 0 failed`（sqlite-vec 模式）
- [ ] 单测守护：`pytest tests/unit/test_ci_l3_context_preflight.py` → 15 passed
- [ ] 离线加载验证：容器内 `SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2')` 成功（`LOAD_OK`）

## 7. 根因对照表

| 现象 | 根因 | 判定线索 | 修复 |
|---|---|---|---|
| `backend=json`, `model_fully_cached=False` | 卷无模型 | 网络不通 + `--list` 空 | hf-mirror 预下载 |
| `backend=json`, 缓存路径 miss | compose/Dockerfile 路径语义错 | 诊断显示缓存完整但加载失败 | 统一为 `{HF_HOME}/hub` |
| 全量 ERROR 缺模块 | 镜像 context 陈旧 | 预检 `critical_modules`/`git_clean` 失败 | 同步镜像/挂载 agent |
| 编码器超时 | 在线加载卡死（无网络 + 无缓存） | 日志 `Connection refused` | 预下载 + 离线模式 |

## 8. 防回归（已内置）

- CI 预检：`build-image` 前 `--json` + 非零退出即中断
- 15 单测守护：校验逻辑 / 边界 / CI 接入 / 端到端模拟
- 知识库：README「L3 Docker 测试」章节 + 本手册

## 附：关键脚本清单

| 脚本 | 用途 | 何时用 |
|---|---|---|
| `scripts/ci_l3_context_preflight.py` | 4 项一致性预检 | 任何 L3 运行前 / CI |
| `scripts/diag_sqlite_vec_fallback.py` | 判定链诊断 | S1/S3 |
| `scripts/predownload_l3_hf_cache.ps1` | 模型拉取（hf-mirror） | 卷无模型 |
