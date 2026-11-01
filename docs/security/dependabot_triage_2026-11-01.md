# Dependabot 分诊：12 条 chromadb（2026-11-01）

> 交接文档 §11.35 C 的 P1-1。实测时间 **2026-11-01 03:2x**（`gh api` 逐页拉取）。
> 本文件只做**判定与证据**，不改依赖；处置动作（dismiss）待 Owner 一句话。

## 1. 盘点（与交接文档口径的差异）

| 口径 | 条数 | 说明 |
|---|---|---|
| §11.35 C 记录 | 30（2 critical / 10 high / 10 medium / 8 low） | **无法复现** |
| 本次实测 `state=open` | **12（6 critical / 6 high）** | 全部 `chromadb`；无 medium / low |
| `state=dismissed` / `auto_dismissed` | 0 / 0 | 此前没人 dismiss 过 |
| 开放 dependabot **PR** | 0 | 上游没有修复版本 ⇒ dependabot 也不开 PR |

实测口径：`gh api --paginate "repos/nzt47/security-tools/dependabot/alerts?state=open&per_page=100"`。
12 = **4 条 advisory × 3 个 manifest**；三个 manifest 其实指向同一个 pin：

| manifest | 实际内容 |
|---|---|
| `requirements.txt` | `chromadb==1.5.9`（唯一 pin） |
| `requirements-test.txt` | 第 10 行 `-r requirements.txt`（仅继承） |
| `requirements-dev.txt` | 同样 `-r requirements.txt`（仅继承） |

⇒ 真实待处置面 = **1 个包 / 1 个版本**，其余是同一问题的重复计数。

| # | advisory | 级别 | 攻击面 |
|---|---|---|---|
| 130 / 80 / 30 | GHSA-36p7-vc44-83pf (CVE-2026-45833) | critical | `PATCH /api/v2/tenants/…/collections/{id}` + `trust_remote_code=true` + 恶意模型仓库 ⇒ RCE（需 UPDATE_COLLECTION 权限） |
| 129 / 79 / 29 | GHSA-xph7-9rjv-w5fr (CVE-2026-45831) | high | `SimpleRBACAuthorizationProvider` 只校验权限名、不校验 tenant/db/collection ⇒ 跨租户越权 |
| 128 / 78 / 28 | GHSA-2wm9-hf6c-p5cr (CVE-2026-45830) | high | 已认证用户可任意读写删**任意租户** collection 数据 |
| 108 / 58 / 8 | GHSA-f4j7-r4q5-qw2c (CVE-2026-45829) | critical | **pre-auth** RCE：`POST /api/v2/tenants/{t}/databases/{db}/collections` + `trust_remote_code=true` |

**没有修复版本**：4 条 advisory 的 `first_patched_version` 全为 `null`，vulnerable range 上界都是 `<= 1.5.9`，
而 PyPI 上 `chromadb` 最新就是 **1.5.9**（2026-05-05 发布）
⇒ 「升级到修复版」这条路**当前不存在**，不是我们拖了没升。

## 2. 可达性判定：四条都打不到（证据）

四条 advisory 的脆弱代码全部在 **Chroma 的 HTTP 服务端 API**（`/api/v2/...`）与**多租户授权层**。
本仓库只用**嵌入式**客户端，不跑 Chroma 服务进程：

| 判定项 | 证据 |
|---|---|
| 只用 `PersistentClient`（进程内 sqlite 后端） | `agent/skills_mgmt/vector_adapter.py:505`、`agent/memory_optimized.py:473`（后者已废弃）、`scripts/migrate_to_sqlite_vec.py:176` |
| 无 `chromadb.HttpClient` / 无 `chroma run` / 无 server 入口 | 全仓 grep：命中仅 `agent/web/http_client.py`（我们自己的出域客户端，与 chromadb 无关） |
| 无 `trust_remote_code` | 全仓 0 命中 |
| 无多租户 / 无 auth provider | 未配置 `SimpleRBACAuthorizationProvider`，单进程单库（`data/chroma*` 本地目录） |
| 对外监听面只有工作台 | 5678 是 Flask 工作台；无 8000/chroma 端口 |

结论：**12 条都属「脆弱代码未被使用（not_used）」**，不是「暂时没补丁所以险」。

## 3. 处置：已执行（2026-11-01 09:38）

Owner 授权「你看着办」⇒ 12 条已按 `not_used` **全部 dismiss**：当前 `state=open` = **0**，
`state=dismissed` = **12（全部 not_used）**。命令留档（可复现 / 可逆）：

```powershell
$ids = 130,129,128,108,80,79,78,58,30,29,28,8
foreach ($n in $ids) {
  gh api -X PATCH "repos/nzt47/security-tools/dependabot/alerts/$n" -f state=dismissed `
    -f dismissed_reason=not_used `
    -f dismissed_comment="嵌入式 chromadb PersistentClient，无 Chroma 服务端/多租户/trust_remote_code；见 docs/security/dependabot_triage_2026-11-01.md"
}
```

可逆：`gh api -X PATCH repos/nzt47/security-tools/dependabot/alerts/<n> -f state=open`。

- **不要为了这几条去升 chromadb**：1.5.9 已是 PyPI 最新，且是**被声明的脆弱上界**；凭空换版本没有安全收益，只有回归风险。
- **不要删 `chromadb` 依赖**：它仍是技能检索的兜底后端（`vector_adapter._native_chroma`），主线向量库已迁 sqlite-vec，
   但删除属于行为变更，得单独一轮评估。

## 4. 复核触发条件（出现任一条就必须重开这 12 条）

- 仓库里出现 `chromadb.HttpClient` / 起 Chroma 服务进程 / 暴露 `/api/v2` 端口；
- 配置任何 auth provider（`SimpleRBACAuthorizationProvider` 等）或多租户；
- 出现 `trust_remote_code`（任何调用）或"从远端仓库拉 embed 模型"的新需求；
- `chromadb` 发布 > 1.5.9（届时先看 advisory 是否改了 range，再按 §8 陷阱 19 查 `requires_dist` 决定是否升）。

## 5. 顺带发现（不属 P1-1，单独上报）

- **secret scanning 有 1 条 open**：`#1 github_personal_access_token`，2026-06-29 03:50 检出，
  `validity=unknown`，API 未返回 `locations`（权限所限）。**需要 Owner 决定是否吊销/轮换该 PAT**
  （工作台不涉及该 token；与 `FLASK_API_TOKEN` 无关）。
- **code scanning 有 641 条 open**：远超"CodeQL 噪音"的量级，需要单独盘点（是否同一规则在主分支反复叠加、
  是否 default setup 与 workflow 双跑）。本轮未展开。
