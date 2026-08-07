# 任务6：前端知识库视图与 API

**任务ID**: T6
**阶段**: 6（前端层）
**前置依赖**: 任务2（卡片 CRUD）、任务4（融合检索）
**预计工作量**: 3-4 天
**输出类型**: 后端 API + 前端 React 页面 + 测试

---

## 一、目标描述

在 yunshu-ui 中新增知识库视图：卡片列表/搜索/详情/关系图入口 + 健康报告页；补齐知识库 CRUD API 与前端接线。保持现有会话/技能管理功能不变（【不易】约束：`App.tsx` 现有页面与组件不动，只做增量）。

---

## 二、执行步骤

### Step 1：后端知识库 API

新建 `agent/server_routes/routes_knowledge.py`（注册方式参考 `routes_memory.py` 的模式），提供：

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/knowledge/cards` | 卡片列表（支持 `?status=&type=` 过滤） |
| GET | `/api/knowledge/cards/<slug>` | 卡片详情（含 links、contradictions） |
| POST | `/api/knowledge/cards` | 创建卡片（body 为任务0 Card dict） |
| PATCH | `/api/knowledge/cards/<slug>` | 更新卡片（支持状态迁移 `transition`） |
| DELETE | `/api/knowledge/cards/<slug>` | 删除卡片（有入链时 409 返回原因） |
| GET | `/api/knowledge/index` | 获取 index.md 内容 |
| GET | `/api/knowledge/lint` | 健康报告（调用任务5 lint_all） |
| GET | `/api/knowledge/graph` | 节点-边数据：`{nodes: [{id, label, type, status}], edges: [{source, target}]}` |

**查询路由升级**：`POST /api/knowledge/query` 走任务4 融合检索（若任务4 已完成接线则本任务复用，不需重复实现）。

错误处理约定（与现有路由一致）：
- 不存在 slug → 404 JSON `{"error": "..."}`，不抛 HTML 错误。
- schema 校验失败 → 422 JSON 含违规项列表。
- 有入链删除 → 409 JSON 含入链列表。

### Step 2：前端知识库页面

在 `yunshu-ui/src/` 新增：

- `pages/Knowledge.tsx`：知识库主页，三区布局：
  1. **搜索区**：输入框调 `/api/knowledge/query`，展示融合检索结果（含状态角标与来源）。
  2. **列表区**：调 `/api/knowledge/cards`，按类型/状态筛选，点击打开详情。
  3. **健康区**：调 `/api/knowledge/lint`，展示健康分与问题列表。
- `components/Knowledge/CardDetail.tsx`：详情抽屉，展示 frontmatter 字段、正文、入链/出链列表、矛盾列表。
- `components/Knowledge/StatusBadge.tsx`：状态角标（draft/current/archive/unknown 四种配色）。
- `components/Knowledge/CardForm.tsx`：新建/编辑卡片表单（可选，若本期只做只读视图则省略，并在成果中注明）。

**导航接入**：在侧边栏（参考 `yunshu-ui/src/App.tsx` 的"技能管理"按钮模式）增加"知识库"入口按钮，点击切换视图；会话主界面保持不动。

### Step 3：前端 API 封装

在 `yunshu-ui/src/` 新建 `api/knowledge.ts`（fetch 封装，风格参考现有 `hooks/useChatStream.ts`），导出 `listCards/getCard/createCard/updateCard/deleteCard/getLint/getGraph/searchKnowledge`。

### Step 4：联调与测试

- 后端测试：`tests/unit/test_routes_knowledge.py`，覆盖全部路由正常/404/422/409 分支。
- 前端测试：`yunshu-ui/src/test/` 下新增组件测试（列表渲染、状态角标、详情展示、搜索交互），参考现有 `App.test.tsx` 模式。
- 联调：启动 Flask 服务 + yunshu-ui dev server，人工验收四页面（列表/详情/搜索/健康报告）。

运行命令：

```bash
# 后端路由测试
$env:PYTHONIOENCODING="utf-8"
python -m pytest tests/unit/test_routes_knowledge.py -p no:cacheprovider --no-header

# 前端测试
cd yunshu-ui
npx vitest run
```

---

## 三、预期成果

1. 知识库页面：卡片列表/搜索/详情/健康报告四视图。
2. 完整知识 CRUD + lint + graph API。
3. 前后端联调通过，会话主界面回归正常。

## 四、评估标准

- [ ] 后端路由测试全绿；前端组件测试全绿。
- [ ] 列表/详情/搜索/健康报告四视图均可正常渲染与交互（人工验收清单逐项勾选）。
- [ ] 知识库页面与现有会话页面互不干扰（切换正常、会话功能回归验收）。
- [ ] API 对不存在 slug 返回 404 JSON、schema 违规返回 422、有入链删除返回 409，均不抛 HTML 错误。
- [ ] 卡片状态角标（draft/current/archive/unknown）在 UI 可见且配色区分。
- [ ] 检索结果展示 `[来源: slug|status]` 标记。
- [ ] 既有前端测试（App.test.tsx 等）回归通过。

## 五、交付物清单

| 文件 | 说明 |
|------|------|
| `agent/server_routes/routes_knowledge.py` | 知识库 API 路由 |
| `yunshu-ui/src/pages/Knowledge.tsx` | 知识库主页 |
| `yunshu-ui/src/components/Knowledge/*` | CardDetail / StatusBadge / CardForm |
| `yunshu-ui/src/api/knowledge.ts` | 前端 API 封装 |
| `tests/unit/test_routes_knowledge.py` | 后端路由测试 |
| `yunshu-ui/src/test/*` | 前端组件测试 |
