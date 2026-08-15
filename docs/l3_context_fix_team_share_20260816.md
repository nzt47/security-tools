# 技术分享：L3 存储后端降级 json 之谜 — 一次 context 一致性修复的全过程

> 面向团队 | 2026-08-16 | 分享人：CI 收尾会话
> 关联文档：验证报告 / 技术复盘 / 发布说明（见文末清单）

## 一、开场故事

PR #634 合并后，L3 Docker 回归中 `TestSqliteVecBackend` 持续红灯：**sqlite_vec 扩展
本身完全可用**（直接实例化增删查全通过），但**集成路径却降级成 json 后端**。
看起来像"代码坏了"，实际是一场横跨**网络、缓存路径语义、镜像构建快照**三层的追踪。

## 二、排查过程（三步定位）

### Step 1：还原判定链

`VectorStore` 选择后端的核心代码只有三行逻辑：

```
model_fully_cached → encoder_ok → st_ok → sqlite_vec / chromadb / json
```

关键认知：`st_ok` 不要求真加载模型，只要**缓存完整**（`_is_model_fully_cached`
检查 `{HF_HOME}/hub/models--*/snapshots/` 有权重文件）即为 True。

### Step 2：容器内外对比定位网络

| 环境 | `model_fully_cached` | `st_ok` | 后端 |
|---|---|---|---|
| 本机（Windows） | True | True | sqlite_vec |
| L3 容器 | **False** | **False** | **json** |

差异指向：容器内模型没缓存。实测网络——`huggingface.co` **连接拒绝**，
而国内镜像 `hf-mirror.com` **可达**。

### Step 3：修复后二次故障（context 漂移）

模型进卷后仍全量失败？`ModuleNotFoundError: agent.skills_mgmt.lineage`。
镜像 `COPY . .` 只打包**构建开始时**的 context，并行开发新提交的模块
没进镜像，而挂载的 `conftest.py`（最新版）引用了它 → 130 项测试全 ERROR。

## 三、三个核心教训（可直接复用）

### 1. 缓存路径语义是隐性契约

`HF_HOME` 与 `{HF_HOME}/hub` 混用导致三处 miss：
下载落盘、检查路径、加载路径。**凡是涉及缓存目录的配置，必须四方对齐**
（下载脚本、检查函数、Dockerfile ENV、compose ENV），且补一条注释说明 Why。

### 2. 容器内网络 ≠ 本机网络

"连不上 huggingface.co" 不等于"无法下载模型"。排查顺序：
先实测可达性（`Test-NetConnection`），再找替代通道（镜像站/离线包）。
本案例镜像站把 470MB 的 MiniLM 顺利拉回。

### 3. 镜像快照是时间点拷贝

并行开发下"新提交的模块没进镜像"是常态。对策不是记住手动同步，
而是**让 CI 在构建前自动校验**（fail fast），把 130 项测试白跑的损失
降到构建前的 1 秒。

## 四、防护体系（本次沉淀）

```
预检脚本（4 项校验）→ CI 接入（build 前，--json + 非零退出中断）
    → 15 个单测守护（校验路径 + 边界 + CI 接入 + 端到端模拟）
    → 知识库（README 章节 / 复盘 / 发布说明）
```

演示要点：临时删掉 `agent/skills_mgmt/lineage.py` → 跑
`python scripts/ci_l3_context_preflight.py` → 看它 1 秒内报错并返回 1。

## 五、数据说话

- 判定链：`json → sqlite_vec`（dim=384）
- L3 回归：**124 passed / 0 failed**
- 单测守护：**15 passed**（含端到端模拟"漂移→中断→修复→放行"）
- 成本：预检把问题发现从"构建 17 分钟 + 测试"提前到"构建前 1 秒"

## 六、行动建议

1. **本地复跑 L3 前**：`python scripts/ci_l3_context_preflight.py` 或
   `docker compose build test-sqlite-vec` 同步镜像
2. **模型缓存缺失时**：`scripts/predownload_l3_hf_cache.ps1`（hf-mirror）
3. **CI 一旦红灯**：先看预检 JSON 里 `git_clean` / `critical_modules` 哪项失败

## 附：文档清单

- [验证报告](ci_l3_context_sync_verify_20260816.md) — 判定逻辑 + 证据表
- [技术复盘](l3_hf_cache_fix_retrospective_20260816.md) — 五阶段证据链 + 教训
- [CI 接入指南](ci_preflight_integration_guide_20260816.md) — GH Actions / GitLab 片段
- [发布说明](releases/context_consistency_preflight_20260816.md) — 改进点总览
- [闭环总结](l3_context_fix_summary_20260816.md) — 完整交付清单
