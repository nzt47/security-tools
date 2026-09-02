# 云枢前端开发工作流备忘（2026-09-03）

统一工作台 = 一份 React 前端代码（`yunshu-ui/`）+ 两种宿主。本文件是日常开发/验收的速查。

## 一、双端口分工（标准工作流）

| 端口 | 是什么 | 什么时候用 |
|---|---|---|
| `http://localhost:5678/chat#/workbench` | Flask 生产部署：后端 API + 构建产物（`python app_server.py`） | **日常使用/验收/演示**（真实数据 + 真实 LLM） |
| `http://localhost:5173/static/#/workbench` | Vite dev server（真 HMR，React Fast Refresh） | **改前端代码时**；`/api` 自动代理到 5678，数据同源 |

- 5678 是"部署形态"：不监听源码、无 HMR。改源码后必须重新构建才生效（见下）。
- 5173 依赖 5678 后端在跑（API 代理），两者是同一份代码，UI 完全一致。

## 二、日常命令

```bash
# 在 yunshu-ui/ 下
npm run dev            # 起 5173（HMR 开发）
npm run check          # tsc 类型检查
npm run lint           # ESLint（0 errors 0 warnings 为验收标准）
npm test               # vitest（当前基线 64 文件 / 505 用例全绿）

# 后端（仓库根）：python app_server.py → 5678（启动约 40s，轮询就绪）

# 改完代码 → 部署到 5678（注意先停后端，避免 Windows 静态文件锁）：
#   Stop-Process -Name python -Force;  npm run build:flask;  重启 python app_server.py
# （Flask 缓存模板 yunshu.html，构建后必须重启后端）
```

## 三、浏览器冒烟脚本（真实 Edge/Chrome + CDP，可重复运行）

```bash
# 全部在仓库根执行；均以退出码 0/1 表示通过/失败，可做 CI 门禁
npm run smoke:wb     --prefix yunshu-ui     # 统一工作台 29 项验收（FAB/栏目/资产联动/知识库 CRUD）
npm run smoke:kb     --prefix yunshu-ui     # 知识库专项（检索/详情/新建弹层）
npm run smoke:route  --prefix yunshu-ui     # 路由收敛（旧路径重定向/login/403/Export）
node scripts/dev/login_flow_smoke.mjs --url http://127.0.0.1:5174/static/#/login   # 登录流（需先起 VITE_MOCK_API dev）
```

前置：后端 5678 运行 + 已 build:flask + 本机 Edge/Chrome。

## 四、踩过的坑（复现时可查）

1. **DevConsole FAB 不显示（5678）**：`.env.production` 的 `VITE_OBSERVABILITY_ENABLED` 优先级高于 `.env`（production mode）。要 5678 显示 → `.env.production` 设 `true`（本地文件不入库）。FAB 在**右上角**，展开靠 mousedown+mouseup（非 click）。
2. **知识库新建 422**：`slug` 必须等于后端 `slugify(title)`（会保留小写字母/数字/中文，**循环剥除尾部 `-<数字>` 段**）→ 标题尾加字母（如 `Smoke Test 20260903x`）才不会被剥成 `smoke-test`；`source`/`date`/`insight` 必填。
3. **构建时 Windows 静态文件锁**：后端运行中 `build:flask` 会 EPIPE——先停 python 再构建。
4. **运行数据不入库**：`data/sessions`、`messages.jsonl`、`knowledge/index*.md`、`visual_workflows.json` 已被 .gitignore 忽略（历史误跟踪已 `git rm --cached`）。
5. **Sentry 上报**：`VITE_SENTRY_DSN` 为空 = 自动禁用。填 DSN（GlitchTip 兼容）→ 重新 build → `initObservability()`（main.tsx 已接线）自动生效。
6. **后端全量 pytest 在本机跑不完**（~77% 时进程被资源终止，非业务失败）：用分片子集；`test_preflight_runner` 3 个失败为已知 Windows 环境问题（proc.stdout None）。

## 五、目录速记

- 导航树单一数据源：`yunshu-ui/src/workbench/hubNav.tsx`（新增栏目/页面在此挂载，组件放 `pages/hub/**`）
- 统一路由：`yunshu-ui/src/router/index.tsx`（`/` → `/workbench`；旧路径兜底重定向）
- 会话页组件：`src/workbench/WorkbenchChatPage.tsx` + `src/components/workbench/**`
- 冒烟脚本：`scripts/dev/*smoke*.mjs`
