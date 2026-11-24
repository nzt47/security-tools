# E0-1 设计：权限预设 + OS 级沙箱（2026-11-24）

> 路线图 `docs/plan/2026-11-24-dsh-parity-roadmap.md` §3 Phase 0 / E0-1 的设计页。
> 一个 Epic 一组小提交；本文只写**决定与边界**，实现细节在代码 docstring。
> 参照：DSH `permission-presets` / `sandbox-policy` / `sandbox-windows-acl` / `fs-sandbox`
> （0.2.2-rc.5，实测包源码；结论下面逐条标注来源）。

## 1. 问题

现状三条缝：

1. `CP_SANDBOX_SHELL_MODE`（off/shadow/enforce）只做**危险命令模式匹配**，默认 shadow 只审计，
   且**没有 OS 级写限制**——即使 enforce，`rm` 之外的任意写（`python -c open(...,'w')`）照写。
2. 文件工具（`write_file` / `edit` / `apply_patch` / `workspace_delete`）只挡"系统保护目录 +
   凭据文件"，**不挡工作区之外**；`safe_resolve_path` 的 realpath+前缀检查也不是内核边界。
3. 插件有 AppContainer，但**宿主自己的 shell/fs 没有任何 OS 约束**（唯一有 OS 边界的地方）。

DSH 的答案（实测源码/README）：

- `permission-presets`：一个 Permissions 选择 = sandbox-mode + approval-policy 两件事；
- `sandbox-windows-acl`：`CreateRestrictedToken(WRITE_RESTRICTED)` + 每工作区一个确定性写 SID
  （`S-1-4-x-y`）+ 私有 temp SID，ACL 授权即写允许表；**fail-closed，任何 Win32 失败都不降级**；
- `fs-sandbox`：文件工具是**可信代码里的策略围栏**（不是内核边界），与 shell 共用同一可写根集合。

## 2. 决定

### 2.1 预设枚举与落点

| 预设 | 文件工具 | shell 子进程 | 说明 |
|---|---|---|---|
| `read-only` | **拒绝一切写**（含工作区内） | 受限令牌，**无写 SID** → 工作区/临时目录都不可写 | 观察/审阅档 |
| `workspace-write` | 只允许工作区根 + 临时目录 | 受限令牌，写 SID 只授权工作区根 + 私有 temp | 默认推荐档 |
| `full-access` | 不设围栏（现状） | 现状（`CP_SANDBOX_SHELL_MODE` 照旧） | 本次升级的**部署默认** |

- **env**：`CP_PERMISSION_PRESET`（B 级，enum，hot 生效）+ `CP_PERMISSION_SANDBOX_ROOT`
  （C 级，只读路径；默认空 = 仓库根）。两条都进 `agent/settings/registry.py`（零缺口门禁）。
- **落点**：治理与运营 → 开关中心（registry 自动渲染）。`_enum(..., notes=...)` 给每个选项一句说明；
  改档走 B 级二次认证 + 双人确认 + 审计，落值只写覆盖层（不改 `.env`）。
- **默认 `full-access` 的理由**：本次是机制落地，不是行为变更；CI（干净 worktree、无覆盖层）
  与现网都不该因为一次合并突然改变写行为。生产是否切 `workspace-write` 由 Owner 在设置中心拍板，
  切换是 hot 的，回滚一键（reset 覆盖层）。
- **approval-policy**：E0-1 只在 `status` 里给出建议映射（strict/standard/minimal），
  **不改**现有 L1/L2/confirm 与审批链（那是 E1-1 计划模式的活）。这一点如实写进 handoff，不冒充已对齐。

### 2.2 文件围栏（策略层，与 DSH `fs-sandbox` 同构）

- 唯一入口 `agent/sandbox/fs_fence.py::check_write(path, tool)`：
  `realpath` 规范化 → 与可写根做**祖先包含**（`os.path.commonpath` 语义的边界版本）→ 拒绝时返回
  `{"ok": False, "blocked": True, "sandbox": "<mode>", "error": "[sandbox: file access denied under <mode> mode]"}`。
- 接入点：`file_tools.write_file`、`search_tools.edit_content`、`test_tools.apply_patch`（逐个目标文件，
  任一被拒即整单不写）、`workspace_tools.write_workspace` / `delete_workspace`。
- 拒绝必须**记审计**（`sandbox_fs_denied`，只记工具名 + 路径指纹，不记内容）。
- 已知缺口（不冒充）：`decompress`/`compress` 的输出路径本次未接（列 handoff 后续项）；
  `run_program` 走 OS 受限（见 2.3），`git` 在 `read-only` 下拒绝写动作。

### 2.3 shell / 进程的 OS 强制（Windows 受限令牌）

- 令牌：`CreateRestrictedToken(当前主令牌, DISABLE_MAX_PRIVILEGE|WRITE_RESTRICTED,
  restricting = [logon SID, Everyone, 写组 SID])`。
  - `logon SID + Everyone` 是**保活组**：缺了早期 DLL 初始化直接 `0xC0000142`（实测；DSH 同结论）；
  - 写组 SID 才是真正授权的写身份：ACL 上没有它的写 ACE，写就被 OS 拒；
  - `read-only` 的 restricting 列表去掉写组 SID（**不授予任何写身份**）。
- 写身份：本机本地组 `YunshuSandboxW`（无成员，仅作 SID 载体；需要管理员权限创建）。
  - 为什么不用 DSH 的 `S-1-4-x-y`：`icacls` 只认 LSA 能解析的名字/SID，自定义 SID 无法用它做
    递归授权；改用**可解析的组 SID + icacls 原生递归**，代价是机器上多一个无成员组。
  - 授权：`icacls <root> /grant YunshuSandboxW:(OI)(CI)(F) /T /Q /C`；工作区根是 **standing**（跨会话复用，
    marker 命中即 O(1)），私有 temp 是 per-run（退出即删）。
  - 新文件靠 `(OI)(CI)` 继承自动获得 ACE，不需要每次重授。
- 默认 DACL：把写组 SID 的 F ACE 合并进**受限令牌的默认 DACL**（`SetTokenInformation(TokenDefaultDacl)`）。
  不合并时孙进程建匿名管道会被 pass-2 拒绝（DSH 的 EPERM 教训）；实测 `python -c subprocess.run(capture_output=True)`
  在合并前 rc=1、合并后可用。
- 进程创建：`CreateProcessAsUserW`（令牌是自己的受限版，不需要 SE_ASSIGNPRIMARYTOKEN 特权）+
  继承句柄 + `CREATE_UNICODE_ENVIRONMENT` + Job（KILL_ON_JOB_CLOSE + 进程数上限）+ 超时 Terminate。
  - **不能带 `CREATE_NO_WINDOW` / `CREATE_NEW_CONSOLE`**：会 `STATUS_DLL_INIT_FAILED(0xC0000142)`（DSH README
    同条，已本机复现）；子进程因此共享宿主控制台，stdio 用**文件句柄**重定向（不是命名管道）。
- **fail-closed**：令牌/组/授权/启动任一失败 → 不执行命令，返回结构化错误 + 审计
  `sandbox_unavailable` / `sandbox_shell_denied`；非 Windows 或组创建失败同样 fail-closed（不静默降级）。
- 覆盖面：`shell_execute`（含 background 分支，走同一 executor）+ `run_program`（受限令牌 spawn，
  返回 pid 后不阻塞）。`decompress` 等宿主内写不在此列（2.2 已记）。

### 2.4 安全边界（如实写）

- `WRITE_RESTRICTED` 只交叉**写**访问：读、执行、网络都不受限（与 DSH 一致；读侧围栏是后续 Epic）。
- 本实现**未加 Low integrity 标签**（DSH 有）：Low 标签要递归改 SACL（同样一次全树代价），
  而写允许表已由 `WRITE_RESTRICTED` + 写组 SID 定义；作为后续加固项记录，不声称等同。
- `Everyone` 在 restricting 列表里（保活组）⇒ 凡是 ACL 给 Everyone 写的对象仍可写（DSH 同边界，
  靠 Low 标签封的那部分我们没有封）。`Authenticated Users` 不在列表（封掉 WMI/CIM 与 C:\ 根建树逃逸）。
- 控制台共享：受限子进程不能有独立控制台（见上），stdout/stderr 用文件句柄捕获。

## 3. 数据结构 / 状态

| 位置 | 内容 | 是否提交 |
|---|---|---|
| `agent/data/sandbox/grant_state.json` | `{group_sid, python_dir?, grants: {root: {sid, granted_at}}}`（marker，命中即跳过递归授权） | 否（运行期） |
| `agent/data/sandbox/tmp/<run-id>/` | 受限子进程的 TEMP/TMP/HOME（per-run，退出清理） | 否 |
| `agent/data/sandbox/runs/<run-id>.out/.err` | stdio 捕获文件（读后删） | 否 |

## 4. 回滚

1. 设置中心把 `CP_PERMISSION_PRESET` reset → 覆盖层清除 → 回落 `full-access`（hot，立即生效）；
2. `python scripts/sandbox_grant.py --revoke`：撤 ACE（`icacls /remove:g`）+ 删组 + 删 marker；
3. 代码回滚 = revert 本次提交；插件 AppContainer 语义**完全不变**（两套令牌互不引用）。

## 5. 验收（干净 worktree + 真机探针）

1. 单测：预设解析/优先级、围栏包含关系（正反例、`..`、symlink、大小写、8.3 短名）、
   拒绝文案与审计载荷、令牌 SID 解析（纯函数）、`apply_patch` 任一目标被拒即不写。
2. 真机探针 `.local/verify_e0_1_permission_preset.py`（可重跑，临时目录）：
   - `workspace-write`：shell 写工作区内成功；写 `%USERPROFILE%` 下未授权文件被拒（exit≠0 且文件不存在）；
     `write_file` 写工作区内成功、写外部被拒且返回 `blocked`；
   - `read-only`：shell 写工作区内被拒；`write_file` 任意路径被拒；
   - `full-access`：两者都恢复现状；
   - 每档都核对审计事件落库。
3. 干净 worktree 五项派生 `--check` 全 0（改工具实现体 ⇒ 重派生能力清单 + 盘点表）。
4. 部署三处一致 + CI 30/30 + code scanning 0。

## 6. 实现落地后的实测与偏差（2026-11-24，必须如实读）

1. **`CREATE_NO_WINDOW` 不能带**（已在 2.3 写明）：本机复现 `0xC0000142`；去掉后 cmd /
   powershell / python 均正常。代价是受限子进程共享宿主控制台。
2. **bash 在受限令牌下不可用**（本次新发现，DSH README 未覆盖）：
   - WSL 的 `C:\Windows\System32\bash.exe` → `Bash/Service/CreateInstance/E_ACCESSDENIED`；
   - Git Bash（MSYS2）→ `fatal error - couldn't create shared memory`（共享内存段 pass-2 被拒）。
   ⇒ 处置：**仅当预设需要围栏时**，`_detect_shell` 的无特征命令默认 shell 由 bash 改为 **cmd**；
   显式 `shell=bash` 的调用仍会尝试，失败时返回可执行提示（`bash_unavailable=true`），
   绝不静默换成别的 shell。full-access 下检测逻辑逐字不变。这是本 Epic 的**已知限制**，
   不是"已对齐"：受限档下 unix 风格命令需要模型改用 cmd/powershell。
3. **marker 不能只信路径**：工作区被删掉重建后路径不变但 ACE 没了。`is_granted()` 现在
   在 marker 命中后**再做一次单目录 `icacls` 校验**（几十毫秒），否则会得到莫名其妙的
   "Access is denied"（探针踩过）。
4. **默认 DACL 合并是必需的**：不合并时受限子进程创建匿名管道被拒（`python -c subprocess.run(...)`
   rc=1）；合并写身份 SID（read-only 档合并 Everyone）后可用。
5. **`icacls` 递归成本**：本机 20k 文件实测 ~10s；本仓库 80 万+ 文件（含 .worktrees/data）
   首次授权是分钟级。⇒ 提供 `scripts/sandbox_grant.py` 一次性预热（marker 命中后 O(1)），
   未预热时受限执行 **fail-closed** 并提示运行该脚本。**默认 full-access，生产切档前先预热。**
6. **读数与观测面**：`agent.sandbox.status()`（聚合预设/可写根/身份/ready）、
   `scripts/sandbox_grant.py --status`、审计事件 `sandbox_fs_denied` / `sandbox_shell_confined` /
   `sandbox_unavailable`。探针 `.local/verify_e0_1_permission_preset.py` 14 项全过（真机）。
7. **本次没做（不要当成已做）**：Low integrity 标签（见 2.4）、`decompress`/`compress` 的
   输出路径围栏、审批策略真正接线（2.1）、非 Windows 的 bwrap/seatbelt（当前直接 fail-closed）。

