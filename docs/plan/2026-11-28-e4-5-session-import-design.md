# E4-5 第四批：会话导入（外部导出 → 只读预览 → 显式导入）设计

> 2026-11-28。上游：`docs/plan/2026-11-28-e4-5-inspector-design.md` §7「之后：会话导入」。
> 本批只做**导入**：不改对话链路、不新增采集、不开新监听面。

## 1. 目标与不做

- 目标：把外部导出（云枢自己的导出 / DeepSeek 会话导出）变成**云枢里可读的会话**，
  分两步：**只读预览**（几个会话、多少条、哪些已经导过）→ **显式确认导入**（建会话 + 写消息）。
- 不做：不做反向同步、不做增量追更、不解析工具原始输出与系统提示词、不导入附件二进制、
  不做跨会话合并、不提供"覆盖已有会话"（导入只追加；重复按内容指纹跳过）。

## 2. 格式白名单（本批只认两种）

| 格式 | 形状 | 说明 |
|---|---|---|
| `yunshu.session.v1` | `{format, session:{title,created_at}, messages:[{role,content,timestamp}]}` | 云枢自己的会话导出形状，用于往返 |
| `deepseek.export.v1` | `[{id,title,messages:[...]}]`，也接受 `{conversations\|chats\|data\|items:[...]}`，或"单会话"（顶层带 `messages`） | DeepSeek 会话导出 |

- 字段别名：`title\|name\|chat_name`、`role\|message_role`（`ai`→assistant / `human`→user）、
  `content\|text`、`timestamp\|created_at\|inserted_at\|create_time`。
- 其余形状一律 `unsupported_format`（**不猜、不"尽力解析"**）：静默把别家的字段错读成空会话
  比直接拒收更坏。要加格式 = 同批加解析分支 + 夹具 + 本文表格 + CHANGELOG。
- 只接受 `.json` / `.zip`（ZIP 里只读 `.json`，最多 20 个）；**上传文件不落盘**，内存解析。

## 3. 开关与回滚

| 开关 | 默认 | 级别 | 说明 |
|---|---|---|---|
| `CP_SESSION_IMPORT_ENABLED` | 关 | A | 关 ⇒ 路由回 `disabled` 且**连上传文件都不读** |
| `CP_SESSION_IMPORT_WRITE_ENABLED` | 关 | A | 关 ⇒ 预览照常，apply 回 403 `write_disabled` |

回滚 = 关开关（已导入的会话按会话删除即可，不涉及任何数据迁移）。
两个开关都登记 `agent/settings/registry.py`（`scan_settings --check` 零缺口）。

## 4. 后端

- `agent/session_import.py`（新，**纯函数、零写入**）：
  - `parse_upload(data, filename)` → `{format, sessions, stats}`；
  - `preview(doc, known)` → 每会话只回 `title/messages/chars/first_ts/last_ts/fingerprint/duplicate`，
    **不回正文**；
  - `fingerprint(conv)` = 角色+正文的 sha256 前 16 位（**不含标题**：改名后再导一次仍算重复）。
- `POST /api/sessions/import`（`agent/server_routes/routes_session_import.py`，新）：
  - multipart：`file` + `mode=preview|apply` + `confirm=1`（apply 必填）；
  - `preview` 零写入；`apply` 需写开关 + confirm，持进程内锁（并发回 409 `busy`）；
  - 写路径：`create_session` → 逐条 `add_message(..., timestamp=原时间戳)` →
    `update_session_metadata(imported={format,file,at,fingerprint,messages})`；
    结束后**恢复导入前的"当前会话"**（导入不该把用户当前会话切走）；
  - 去重台账 `data/session_import/ledger.json`（运行期、gitignore；只存指纹/sid/标题/文件名）；
  - 错误只回稳定 `code` + 中文短句，异常原文不进响应体。

## 5. 前端

- 会话栏头部「导入会话」按钮（`data-testid=session-import-open`）→ `SessionImportDialog`：
  选文件即**只预览**；列表逐行显示标题 / 条数 / 「已导入」徽标；
  写开关关时显示「只能预览」且不给确认框；勾选「我确认写入」后才可点「确认导入」；
  成功后回调新会话 id（页面刷新列表、切回「未归属项目」并打开第一个新会话）。
- 不新增页面（`ui_reachability` 零漂移）；端点同批进 `endpoints.ts`（覆盖率脚本能匹配）。

## 6. 边界

- 只追加：不覆盖、不合并、不删除任何既有会话；重复只按内容指纹跳过。
- 上限：20MiB 上传、ZIP 20 个 .json、50 个会话、每会话 5000 条、单条正文 100k 字符、
  标题 120 字符；越界**截断并计数**（`stats.truncated_*`），不静默丢。
- 只导入 `user`/`assistant` 正文；`system`/`tool` 只计数后丢弃。
- 不落盘上传文件、不接受客户端路径、argv 全为常量。

## 7. 判据

1. 开关关：200 + `disabled`，且**不调用解析**（用例用 monkeypatch 断言）；
2. 预览：计数/去重标记/正文不出现；不建会话（`mgr.calls == 0`）；
3. 写闸：写开关关 403、无 confirm 400、并发 409；
4. 写入：消息角色与**原始时间戳**保留、`imported` 元数据落 meta、当前会话被恢复；
   同一份导出第二次导入 `created_count=0`；
5. 失败：500 固定文案（不含异常原文）、当前会话仍恢复；
6. 前端：预览渲染 / 开关关提示 / 勾选后才可导入 / 成功回调；`npm run ui:check` 绿；
7. 派生：`route_ui_coverage` 重派生（+1 路由且 ui_pending=false，pending 仍 0）；
   工具 / 清单 / S0 / τ 不变（159 / 109 / 5.9497 / 0.0295）。
