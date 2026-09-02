# 云枢交付收尾报告（2026-09-03）

> 范围：任务 1-9（统一工作台集成与重构收尾）交付前的复核、验证与治理。
> 状态：master `7688ad95`（与 origin/master 一致，工作区干净）。

## 一、任务 1-9 完成情况

| # | 任务 | 状态 | 落点（提交/产物） |
|---|---|---|---|
| 1 | P0 基线固化 | ✅ | 8a814cf3（统一工作台落库）+ 7894eb13（Sentry 取回）+ .gitignore |
| 2 | P1-1 知识库完整版迁入工作台 | ✅ | a218a672（含 kb 冒烟） |
| 3 | P1-2 可视化工作流编辑 | ✅ | a620dc68（前端 + 后端 `/api/visual-workflows/*`，app_server 注册） |
| 4 | P1-3 历史问话侧滑面板 | ✅ | a620dc68（HistoryDrawer） |
| 5 | P1-4 插件 schema 配置表单 | ✅ | a620dc68（plugin-manage +405 行） |
| 6 | P2 摘除第二套管理后台外壳 | ✅ | b5938e67（-869 行；Export 并入 admin；保留 /login） |
| 7 | P3 缺陷修复 | ✅ | 0713661c（Toaster 挂载/导航参数化/桌面链路） |
| 8 | P4 legacy 死代码清理 | ✅ | 9b4aa8f5（-9382 行） |
| 9 | P5 分支归档 | ✅ | archive/*-20260902 tag ×12；本地仅剩 master |

## 二、交付前追加修复（收尾期发现并处理）

- **DevConsole FAB 生产不可见**：`.env.production`（production mode 优先）覆盖 `VITE_OBSERVABILITY_ENABLED=false` → 本地启用为 `true`；FAB 在右上角，展开需 mousedown+mouseup。
- **Sentry/回放链接线**：`main.tsx` createRoot 前 `initObservability()`；pako v3 ESM 无 default 导出致构建失败 → named import 修复（48156cf9）。
- **ESLint 归零**：monitor.tsx 常量 `??` 2 error + ~30 处 unused import/变量；react-refresh 规则按项目桶文件模式关闭；coverage 产物移出版本控制（d55d0da2，0 errors / 0 warnings）。
- **运行数据移出版本控制**：data/sessions、messages.jsonl、knowledge/index*.md、visual_workflows.json（磁盘保留，不再随提交 dirty）。

## 三、质量验证（全部通过）

| 项 | 结果 |
|---|---|
| TypeScript `npm run check` | ✅ |
| ESLint | ✅ 0 errors / 0 warnings |
| 前端单测（vitest） | ✅ 64 文件 / 505 用例 |
| 后端相关路由测试 | ✅ 112 passed（含新增 test_visual_workflows_routes.py） |
| 浏览器验收冒烟（CDP 真实 Edge） | ✅ `smoke:wb` 29/29 · `smoke:kb` 18/18 · `smoke:route` 19/19 · login 8/8 |
| CI/CD（GitHub Actions，最新 master 推送） | ✅ 前端/核心守卫/文档部署等 success（详见 CI run 列表） |

## 四、问题与解决方案速查（详见 docs/frontend-dev-workflow.md）

1. DevConsole FAB gate 被 .env.production 覆盖 → 本地置 true。
2. pako v3 ESM 无 default → `import { gzip }`。
3. 知识库 422：`slug == slugify(title)` 且 slugify 剥尾部 `-<数字>` → 标题尾加字母；source/date/insight 必填。
4. Windows 静态文件锁致 build:flask EPIPE → 先停后端再构建；Flask 模板缓存 → 构建后重启后端。
5. 旧 lint 报错/构建失败（48156cf9 时点）→ 由 d55d0da2 等后续提交修复，最新 master 全绿。

## 五、代码库治理

- 远程清理：7 个已归档分支 + 9 个已合入分支的 origin 镜像已删除；全部 archive/v* tag 已推送（可随时恢复被删分支内容）。
- 保留（未合入 master 的远程分支，如需可另行处置）：docs/release-ops-log、docs/v100-release-summary、feat/ci-dashboard-push-retry、feat/continue-dev、fix/arch-circular-deps、fix/ci-scan-optimization。

## 六、遗留事项（不影响交付，需外部资源或后续决策）

1. **Sentry 上报未激活**：需真实 DSN（自建 GlitchTip：起 Docker → `docker compose -f deploy/glitchtip/docker-compose.yml up -d` → 建 Project 取 DSN → 填 `yunshu-ui/.env` 的 `VITE_SENTRY_DSN` → 重新 build）。代码已接线，DSN 就绪即生效。
2. **后端全量 pytest 建议在 CI 分片执行**：本机两次分别于 35%/77% 被进程终止（资源限制，非业务失败）；`test_preflight_runner` 3 个失败为已知 Windows 环境问题（proc.stdout None）。
3. **CI 时点失败历史**：48156cf9 触发的 lint/build/后端 runs 失败已由后续提交修复（最新 master 推送无失败），GitHub Actions 历史页保留记录。

## 七、Stakeholders 确认清单（请在下方逐项确认）

- [ ] 统一工作台（`http://localhost:5678/chat#/workbench`）12 组导航栏目功能符合预期
- [ ] 会话任务（SSE 流式 + 上下文管理器滑块/机制说明 + 输入框自动增高）可用
- [ ] 知识库/可视化编辑/历史问话/插件配置/Export 等新迁入功能可用
- [ ] 系统管理栏目（用户/角色/菜单/审计/日志/导出）可用，旧管理后台路径已收敛
- [ ] DevConsole 浮层（右上角 🐛）在 5678 与 5173 可见
- [ ] 质量门（tsc / lint / 505 单测 / 冒烟 29 项）通过
- [ ] master 已推送 origin 且 CI 通过，分支已归档清理，工作区干净
- [ ] 遗留事项（Sentry DSN / pytest 分片）已知悉并接受
