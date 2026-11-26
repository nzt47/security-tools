# E0-3 设计：会话检查点 + 每轮 workspace 变更快照（2026-11-25）

> 路线图 `docs/plan/2026-11-24-dsh-parity-roadmap.md` §3 Phase 0 / E0-3。
> 参照 DSH `session-checkpoint-policy`（模型请求/工具副作用前的语义检查点）与
> `workspace-changes`（每轮 git 快照 + 逐文件比较）。

## 1. 问题

现状只有**审计链**（谁在什么时候做了什么）和工具自带的 `undo_hint`（怎么撤销单个动作），
缺两件事：

1. **「本轮到底改了哪些文件」**：一次多文件修改后，没有逐文件 diff 可看；E1-3 的
   「变更文件卡」没有数据源；
2. **「回到某个时间点对照」**：没有会话级检查点，只能靠 git 手工 diff。

## 2. 决定

### 2.1 存储（运行期，不提交）

`agent/data/checkpoints/<UTC 时间戳>-<8 hex>/`：
- `manifest.json`：`{schema, id, created_at, trigger_tool, workspace_root, git_head,
  changed:[{path, status, size, sha256, captured, binary}], diff_bytes, truncated, limits}`
- `status.txt`：`git status --porcelain` 的**过滤后**原文（见 2.3）
- `diff.patch`：`git diff`（有界；超过上限只留 stat 摘要并标 `truncated`）
- `files/<相对路径>`：被改动文件的**整文件捕获**（仅文本、≤ `CP_CHECKPOINT_MAX_FILE_BYTES`）

### 2.2 何时快照（唯一汇聚点 + 后台 + 去抖）

- 挂在 `tools.call()`（唯一汇聚点）：当本次工具属于**会改工作区**的集合
  （write_file / edit / apply_patch / workspace_write / workspace_delete / compress /
  decompress / git / run_program / shell_execute / software_install / schedule_task /
  skill_script_run）且结果 `ok` 时，调 `maybe_snapshot(tool)`。
- **后台线程 + 去抖**：`git status`/`git diff` 在本机 80 万文件仓库要秒级，不能阻塞工具返回；
  默认同一进程内最快每 `CP_CHECKPOINT_DEBOUNCE_SECONDS`（120s）一次，且同一时刻只跑一个。
- 失败只记 WARNING（快照是旁路，绝不影响工具可用性）。

### 2.3 过滤与上界（为什么必须过滤）

本仓 tracked 里有运行期状态（`data/feedback.db`、`data/logs/*.db`、`data/system_prompt*`、
`data/tools_config.json` 等），`git status` 每轮都在变。快照只收**源码/文档类改动**，
跳过：`data/`（除 `data/tool_definitions/`、`data/agent_lines/`；未跟踪的运行期文件同样挡掉）、`agent/data/`、
`static/assets/`、`.local/`、`test_reports/`、`_ci_logs/`、`_scratch/`、`.worktrees/`、
`.pytest_tmp/`、`.file_backups/`、`.ui_shots/`、`rag/`、`.ci_watch*`。上界：最多 `CP_CHECKPOINT_MAX_FILES`（默认 50）个文件、
单文件 `CP_CHECKPOINT_MAX_FILE_BYTES`（默认 1 MiB）、diff 总计 2 MiB。

### 2.4 读取/对比（本次交付只读）

- `list_checkpoints(limit)`：按时间倒序列出（id/时间/触发工具/改动数/是否截断）。
- `load_manifest(id)` / `read_file(id, path)`：读清单与捕获的文件内容（路径校验：只接受
  清单里出现过的相对路径）。
- `compare(id, other=None)`：与**另一个检查点**或**当前工作区**逐文件比对
  （added/removed/modified/unchanged），返回结构化列表 + diff 字节数。
- CLI：`scripts/checkpoint_cli.py list|show <id>|diff <id> [--against <id>]|snapshot [--note]`。

### 2.5 本次不加 HTTP 路由 / UI（有意）

加 3 条无 UI 引用的 GET 路由会触发 `route_ui_coverage` 的未引用面与 triage 维护，
而「变更文件卡」的交互（逐文件 diff、打开/定位）本身属于 **E1-3**。因此本轮只交付
**能力 + CLI + 探针**；HTTP/UI 与 E1-3 一起做（那时的路由天然有 UI 引用，不产生未引用债）。

### 2.6 回滚

- `CP_CHECKPOINT_ENABLED=0` ⇒ 完全停止快照（hook 直接 return），已落盘的检查点保留可读；
- 代码回滚 = revert 提交；检查点目录是运行期状态，删掉即可（不影响主链路）。

## 3. 开关（全部 A 级、hot）

| env | 默认 | 说明 |
|---|---|---|
| `CP_CHECKPOINT_ENABLED` | true | 总开关 |
| `CP_CHECKPOINT_MAX_FILES` | 50 | 单次快照最多捕获的文件数（1–500） |
| `CP_CHECKPOINT_MAX_FILE_BYTES` | 1048576 | 单文件捕获上限（4 KiB–16 MiB） |
| `CP_CHECKPOINT_DEBOUNCE_SECONDS` | 120 | 同进程最小间隔（10–3600） |

## 4. 验收

- 单测：过滤规则、上界、去抖、manifest 形状、compare（与另一检查点/当前工作区）、
  路径穿越拒绝、非 git 仓库 fail-soft。
- 真机探针 `.local/verify_e0_3_checkpoints.py`：改 2 个文件 + 删 1 个 → `snapshot()` →
  清单含逐文件状态与 sha256、捕获内容可读、`compare` 与当前工作区一致；随后还原探针改动。
- 干净 worktree：`scan_settings --check` 零缺口、`sync_capability_manifest --check` 一致。


## 5. 保留策略与按会话读取（E1-3 余项，2026-11-26）

### 5.1 保留（防无界增长）

- 每次快照成功后顺手清理一次（失败绝不影响快照）：`prune(keep, days)`。
- **两条规则任一超限即删**：份数超过 `CP_CHECKPOINT_KEEP`（默认 50，5–500）或
  年龄超过 `CP_CHECKPOINT_MAX_AGE_DAYS`（默认 30 天，1–365）。
- **永远保留最新一份**（index 0 受保护）：避免把"刚生成、正要给变更卡看"的那份删掉。
- 运维可 `prune(dry_run=True)` 预览；`purge(id)` 仍可单删。

### 5.2 按会话读取（「只看本会话 / 看全部」）

- 读取侧新增 `scope=session`：只在 `manifest.session_id` **严格相等**的快照里取
  （`list_checkpoints(..., session_id=)` / `changes(..., session_id=)`）。
- 老快照没有 `session_id` ⇒ **只出现在「看全部」**里，不冒充任何会话；
  指定了别的会话的 `checkpoint` 且 `scope=session` ⇒ 404 `not_in_session`（不静默换一份）。
- 本会话还没有快照 ⇒ 200 + 空清单 + 说明文案（前端渲染一句提示，不整卡消失）。
- 卡片时序：流结束后**首拉 + 1.5s 再拉一次**（检查点是后台去抖快照，首拉可能还是上一轮）。
