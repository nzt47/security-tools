# E4-5 第一批：Inspector（会话轨迹只读查看器）设计

> 2026-11-28。上游：`docs/plan/2026-11-24-dsh-parity-roadmap.md` §E4-5「Inspector/trajectory」。
> 本批只做**只读轨迹**：不动对话链路、不新增采集、不写任何会话数据。

## 1. 目标与不做

- 目标：把「这一轮模型看到了什么 / 调了什么工具 / 结果多大」变成可回看的只读视图，
  供 Owner 排查「为什么它这么做」；数据源就是会话存储里的 `messages`（含 `steps`），
  **不新采一份轨迹**（与 E1-4c 大纲/统计同一事实源）。
- 不做：不写消息、不改 `messages.jsonl`、不做回放/重放、不导出、不跨会话聚合、
  不读 `data/messages.jsonl` 的冻结池（无 session_id 的历史不参与）。

## 2. 开关与回滚

| 开关 | 默认 | 级别 | 说明 |
|---|---|---|---|
| `CP_INSPECTOR_ENABLED` | 关 | A | 关 ⇒ 路由回 `disabled`（**不读会话**），前端显示「未启用」。回滚 = 关开关（无数据形状变化）。 |

登记 `agent/settings/registry.py`（`scan_settings --check` 零缺口）。

## 3. 后端

- `agent/context/trajectory.py`（纯函数，无 Flask/磁盘）：
  - `build(messages, limit=50, offset=0)` → `{total, offset, limit, entries, truncated}`；
  - 每条消息一个 entry：`index / role / ts / chars / body / body_truncated / steps[]`；
  - `steps` 每项只保留白名单字段 `id / title / status / detail / at`，`detail` 截到 800 字符；
  - `body` 截到 2000 字符；单条消息最多 40 个 step；`limit` 夹到 1..200；
  - 顺序 = 会话存储顺序（旧→新），前端负责倒序展示；`total` 是窗口外总数，`truncated` 表示还有更早/更多。
- `GET /api/sessions/<session_id>/trajectory?limit=&offset=`（`routes_session_insight.py`）：
  - 开关关 ⇒ 200 `{ok:false, code:"disabled", error:"轨迹查看未启用（CP_INSPECTOR_ENABLED=0）"}`；
  - 会话不存在 ⇒ 404（与 outline/stats 同口径）；异常只回固定文案 + 日志；
  - **只读**；不新增写路由、不新增工具。

## 4. 前端

- `HistoryDrawer` 的洞察行加「轨迹」按钮（与「大纲 / 统计」同一排），点开才拉数据；
  - 面板逐条列出：role 徽标 / 时间 / 正文摘要（truncate + title 全文）/ steps（工具调用标题与结果摘要）；
  - 开关关时显示「轨迹查看未启用（CP_INSPECTOR_ENABLED=0）」；401/网络错只给稳定提示。
- 不新增页面（`ui_reachability` 零漂移）；新端点同批进 `endpoints.ts`（覆盖率脚本能匹配）。

## 5. 安全边界

- 只读、按会话取数；令牌与 outline/stats 同口径（enforce 下要 Bearer）。
- 截断是硬上限（正文 2000 / step 800 / 200 条消息），避免把整段上下文当响应体搬。
- 与对话界面同一可见性：内容本来就是该会话的消息与工具体现在对话里；不额外打码，
  但**不**新增跨会话查询面（只能按 `session_id` 取，且必须存在）。
- 冻结池不读（沿用 `safe_paths`/会话管理器的既有边界）。

## 6. 判据

1. 开关关：路由 200 + `disabled`，且不调用会话管理器（用例断言）；
2. 开关开：entries 顺序、截断、limit/offset 夹取、steps 白名单；
3. 404：会话不存在；异常：固定文案；
4. 前端：轨迹按钮只在点开时拉一次；关闭开关显示未启用；`npm run ui:check` 绿；
5. 派生：`route_ui_coverage` 重派生（+1 路由且有 UI 引用，pending 仍 0）；工具/清单/S0/τ 不变。

## 7. 分期

- 本批：Inspector v1（上文）。
- 之后：会话导入、ACP/SDK（server）、LiveShare（每项独立、默认关）。
## 9. 第三批（同日）：worktree / forge 只读投影（默认关）

- `agent/git_worktree.py`（新）：`collect()` 只跑**只读** git 子命令（`worktree list --porcelain`、
  `status --porcelain=v1`、`diff --stat`），返回 worktree 列表 + 每个的 status 计数与 diff --stat；
  最多 20 个、diff 截 4000 字符；git 缺失/不是仓库回稳定 code（`no_git` / `not_a_repo`）。
- 路由 `GET /api/git/worktrees`（挂在 `routes_changes.py`，与「工作区变更」同域）：默认关
  `CP_WORKTREE_PANEL_ENABLED`；关时**不读仓库**。
- 前端：本地资源 → 工作区 Tab 顶部「Git 工作树（只读）」卡片（分支 / detached / 脏标记 /
  status 计数 / diff --stat）。
- 边界：**只读**——不建/不删 worktree、不 checkout、不提交、不推远程；argv 全为服务端常量；
  路径只来自 git 输出，不接受用户传路径（后续「指定某个 worktree」也只能做列表名字的等值匹配）。
  「创建 worktree / 提交 / forge 请求」属 B 级，另开一片。
- 判据：`tests/unit/test_git_worktree.py` 8 条（解析 / 上限 / 真实临时仓库 / 失败码）、
  `test_worktree_route.py` 4 条、`local/index.test.tsx` 新增 2 条；`route_ui_coverage` 重派生
  （+1 路由且 ui_pending=false）。

## 8. 第二批（同日）：headless CLI（客户端，不开新监听面）

- `scripts/yunshu_cli.py`：`health` / `version` / `ask` 三个子命令，直接调**既有 REST**；
  令牌从 `YUNSHU_API_TOKEN` 或仓库 `.env` 的 `FLASK_API_TOKEN` 读（**永不打印**），
  Base URL 用 `YUNSHU_BASE_URL`（缺省 `http://127.0.0.1:5678`）。
- **为什么是客户端**：headless 的最短路径是复用已在跑的 REST（同一套令牌 / 审计 / 限流），
  而不是再开一个监听面。**不新增服务端路由、不新增工具、不改任何运行期行为**；
  删掉脚本对线上零影响。
- 边界：`ask` 是同步整轮（不流式）；HTTP 错误只回服务端 `error` 字段（截断 300 字符）；
  `health` 兼容 `/api/health` 的**传感器数组**（异常项 > 0 时退出码 1）。
- 判据：`tests/unit/test_yunshu_cli.py` 14 条（令牌来源 / 请求头 / 错误脱敏 / ask body /
  version 三处 / health 数组）；真机 `health` 与 `version` 均 exit 0。

