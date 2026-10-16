# 部署可观测与一键更新（2026-10-16）

## 为什么有这份文档

2026-10-16 实测了一次"重启比想象中复杂"的事故，根因不是重启本身，而是**部署模型缺一层**：

- 服务由 start_yunshu.bat 直接以 python app_server.py 跑在**工作树**里，没有发布产物、没有版本固定。
  于是"PR 合到远端"不等于"线上生效"——主仓工作树停在 7e434725（落后 14 个提交）而远端已合并。
- 看护进程 scripts/watchdog_yunshu.py 只探 /api/health、失败就重启**同一个** app_server.py，
  从不 git fetch；它能"自愈"，但会一直自愈在旧代码上。
- 前端产物（templates/yunshu.html + static/assets）是 npm run build:flask 的**另一个**独立步骤，
  与后端版本可任意漂移。
- 手动"杀进程 + 拉起"会与看护进程的"3 次探活失败→杀陈旧监听者→再拉起"竞态，出现两个进程抢 5678。

## 现在的三件套

### 1. GET /api/version（plugins/version_info.py）

只读返回"本进程加载的哪一版"：

    backend.commit / commit_subject / committed_at / dirty / dirty_files
    frontend.bundle / built_at / index_html_present
    process.pid / started_at / python / cwd

口径要点：backend.commit 取自**进程导入 agent/build_info.py 那一刻**（代表本进程加载的代码），
不是每次请求现查工作树；工作树之后被改不会改变本进程的答案。端点受共享令牌保护
（未加入鉴权豁免清单），调用时带 X-API-Token。

### 2. python scripts/deploy_workbench.py（一键更新 + 重启 + 验证）

默认流程：git fetch → 解析 origin/master → 若本地脏路径与待更新文件相交则**拒绝**
（不 hard reset、不 stash）→ **写回滚记录** → git merge --ff-only → 前端按需重建
（index 缺失，或本次更新动了 yunshu-ui/ 或 templates/yunshu.html，或**构建戳与目标 commit
不一致**，或产物比 yunshu-ui/src 下最新源码旧）→ 与看护进程协作重启 → 等 /api/health
→ 读 /api/version 报告实际运行的 commit 与前端 bundle。

    python scripts/deploy_workbench.py --check        # 干跑，只报告将做什么
    python scripts/deploy_workbench.py                # 正常更新 + 重启
    python scripts/deploy_workbench.py --no-build     # 跳过前端重建
    python scripts/deploy_workbench.py --no-restart   # 只更新代码/前端
    python scripts/deploy_workbench.py --smoke        # 重启后对 /api/health + /api/version + /chat 冒烟
    python scripts/deploy_workbench.py --rollback     # 回滚到上次部署前的 commit

重启协作：若 logs/watchdog_yunshu.lock 指向的看护进程还活着，则**只杀监听者、由它拉起**，
避免双主竞争；它未在超时内拉起才回退为直接启动。

#### 2a. 回滚记录与 --rollback

真正动代码**之前**，把 `{from_commit, to_commit, ref, started_at, ...}` **原子写**入
`logs/deploy_last.json`（先写 .tmp 再 replace）。所以即使 ff 成功但构建/重启中途崩溃，
也有回滚依据；写不进记录就**拒绝继续**（返回码 9）。

`--rollback` 读该记录把代码退回 `from_commit`：默认用 **git reset --keep**（只在"两个 commit
间有差异、且本地又改过"的文件上中止，不会像 --hard 那样把 data/ 等运行期脏文件一起丢弃），
`--hard` 才显式用硬重置。回滚目标必须在本地可达（否则提示先 fetch）；已回滚过的记录会被
拒绝重复回滚（避免连点）。回滚后同样按需重建前端并重启。

#### 2b. 前端构建戳（替代 mtime 判断）

前端重建成功后写 `static/assets/.build-stamp.json`：

    {"commit": "<目标 commit>", "built_at": "<ISO 时间>", "source": "deploy_workbench"}

判断"产物是否陈旧"**优先看构建戳**（与目标 commit 一致才跳过重建），mtime 比较退为兜底
（覆盖"改了源码但没走本脚本构建"）。相比纯 mtime，它不受 touch/时钟偏移影响，且能识别
"上次构建失败后残留的旧产物"。该文件落在 `static/assets/`（gitignore），不会把工作区弄脏。
`/api/version` 的 `frontend.build_commit` / `frontend.built_at_source` 会把它读出来，
便于核对前后端是否同版。

### 3. 看护进程记录 repo HEAD（scripts/watchdog_yunshu.py）

每条 watchdogs 留痕（watchdog_start / health_check / restart_result）都带 repo_head，
可用 jq/grep 对账"这次重启前后是不是同一个 commit"，从而看见代码漂移：

    Get-Content logs/watchdog_yunshu.jsonl | Select-String repo_head

## 仍未做（如实标注）

- **没有 release worktree + 原子切换**：运行期状态（.env、data/、logs/）当前就在工作树里，
  直接做软链切换风险更大，故这一步先不做；等运行期状态与代码分家后再上。
- 前端与后端的"同一个部署单元"仍靠 deploy_workbench.py 的顺序保证（先 ff 代码、再重建前端、
  最后重启）+ 构建戳事后核对，**不是强校验**：/api/version 会把前后端 commit 都报出来，deploy
  在不一致时只告警不阻断。
- `--smoke` 是**可选**的（默认关）：只有显式传入才探 /api/health + /api/version + /chat；
  它只判 HTTP 200，不做业务断言。
- 看护进程只**记录** HEAD 漂移，不会自动更新代码（有意：自动 pull 会把"进程活着"与
  "代码变更"耦合起来，风险更大）。
