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
（不 hard reset、不 stash）→ git merge --ff-only → 前端按需重建
（index 缺失，或本次更新动了 yunshu-ui/ 或 templates/yunshu.html，或产物比 yunshu-ui/src 下最新源码旧 —— 后者覆盖「上次构建失败/中断」的残留）→ 与看护进程协作重启
→ 等 /api/health → 读 /api/version 报告实际运行的 commit 与前端 bundle。

    python scripts/deploy_workbench.py --check        # 干跑，只报告将做什么
    python scripts/deploy_workbench.py                # 正常更新 + 重启
    python scripts/deploy_workbench.py --no-build     # 跳过前端重建
    python scripts/deploy_workbench.py --no-restart   # 只更新代码/前端

重启协作：若 logs/watchdog_yunshu.lock 指向的看护进程还活着，则**只杀监听者、由它拉起**，
避免双主竞争；它未在超时内拉起才回退为直接启动。

### 3. 看护进程记录 repo HEAD（scripts/watchdog_yunshu.py）

每条 watchdogs 留痕（watchdog_start / health_check / restart_result）都带 repo_head，
可用 jq/grep 对账"这次重启前后是不是同一个 commit"，从而看见代码漂移：

    Get-Content logs/watchdog_yunshu.jsonl | Select-String repo_head

## 仍未做（如实标注）

- **没有 release worktree + 原子切换**：运行期状态（.env、data/、logs/）当前就在工作树里，
  直接做软链切换风险更大，故这一步先不做；等运行期状态与代码分家后再上。
- 前端与后端的"同一个部署单元"目前靠 deploy_workbench.py 的顺序保证（先 ff 代码、再重建前端、
  最后重启），没有强校验二者 commit 一致；/api/version 只把两者都报出来供人/脚本核对。
- 看护进程只**记录** HEAD 漂移，不会自动更新代码（有意：自动 pull 会把"进程活着"与
  "代码变更"耦合起来，风险更大）。
