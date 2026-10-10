# 云枢工作台 UI 信息架构评审与整合方案（草稿）

> **文档日期**：2026-10-11
> **版本**：草稿 v0.1
> **状态**：**待评审**。本文只给方案与迁移步骤，**不动任何 UI 代码**。
> **关联**：`yunshu-ui/src/workbench/hubNav.tsx`（导航唯一数据源）、
> `yunshu-ui/src/workbench/hubNav.test.ts`、`yunshu-ui/src/workbench/hubNav.search.test.ts`、
> `yunshu-ui/src/features/`（自动发现）、`reports/route_ui_coverage.json`（后端路由覆盖产物）。

---

## 0. 结论速览

| 议题 | 结论 | 优先级 |
|---|---|---|
| 1. 「人格配置」并入「提示词实验室」 | **建议合并**（persona 组只剩 1 个子项） | P1 |
| 2. 「扩展中心」与「系统组件 / 插件管理」 | **建议合并**为「插件与扩展」（两 Tab） | P1 |
| 3. 「经验库」移入「记忆管理」 | **建议移入**，与「知识库系统」并列；**不合并组件** | P1 |
| 4. 「路由覆盖」归位 | **不放「全景看板」**；建议「治理面板」或「系统管理」 | P2 |
| 5. 顶层收敛 + 深链兼容 | 建议 17 项 → 6–8 个语义桶，保留旧 key 映射 | P3 |
| 6. 后端未呈现路由 | 先标注「该进 / 不该进」，再分批补，不做无差别补齐 | P2 |

---

## 1. 现状

导航唯一数据源：`yunshu-ui/src/workbench/hubNav.tsx` 的 `HUB_NAV`。
顶层 **17 项** = 4 个叶子 + 11 个分组 + 2 个自动发现项：

- 叶子：会话任务 / 提示词实验室 / 技能中心 / 工具调用 / 经验库 / 网络配置
- 分组：全景看板(4) / 治理面板(8) / 记忆管理(4) / 人格与提示词(**1**) / 循环工程(2) /
  装配车间(2) / 资产管理(8) / 系统组件(2) / 系统管理(8)
- 自动发现：`src/features/<key>/index.tsx`（带 `manifest`）由 `featureRegistry` 构建期
  静态发现，**追加在 HUB_NAV 末尾**。当前只有两项：
  - `extensions`「扩展中心」order=900
  - `route-coverage`「路由覆盖」order=910

深链：`?panel=<key>`；`navKeyFromSearch` 对**不在导航树里的 key 一律拒绝**
（否则手改地址栏会渲染空白页）。

---

## 2. 顺序锁与守卫（迁移必改清单）

**任何 IA 调整都等于同时改下面这些断言**，否则 CI 必红：

| 文件 | 锁住的内容（原话） |
|---|---|
| `workbench/hubNav.test.ts` | 「技能中心」紧跟「提示词实验室」；`topKeys.slice(0,4)` **精确等于** session→prompt-lab→skills-center→tools；`memory.children` **精确等于** `[memory/manual, memory/auto, memory/knowledge, memory/search]`；「工具调用」在「全景看板」前；assets 恰好 8 项 |
| `workbench/hubNav.search.test.ts` | 「治理面板」**紧跟**「全景看板」（防"滚不到就找不到"回退）；「审批收件箱」仍是其子项；`filterNav` 不就地改写 HUB_NAV |
| `workbench/featureRegistry.test.ts` | 自动发现机制的契约 |
| `WorkbenchApp.tsx` | HUB_NAV 的运行期消费方 |

`hubNav.tsx` 里对「经验库」位置的注释写明：当初放在治理面板之后是为了**零测试改动**——
本方案要动它，改测试是预期内的成本。

---

## 3. 问题清单（均有代码证据）

- **P1 单子项分组**：`persona`「人格与提示词」只剩「人格配置」1 个子项（其两个兄弟
  「身份提示词」「LLM 通信」早已并入提示词实验室）。
- **P2 同域多入口**：
  - 记忆：记忆管理 / 资产管理·记忆数据 / 治理面板·记忆技能库
  - 技能：技能中心 / 资产管理·技能与工作流
  - 提示词：提示词实验室 / 资产管理·提示词库
  - 工具：工具调用 / 资产管理·工具资源
- **P3 假分组**：循环工程(2)、系统组件(2) 分组收益低。
- **P4 顶层过长**：17 项，侧栏滚动；代码注释里已有"滚不到就找不到治理面板"的**实测缺陷**记录。
- **P5 三个检索面**：记忆管理·搜索、知识库系统自带 `/api/knowledge/query`、经验库自带 `/api/experience/search`。
- **P6 同源重复**：`扩展中心` 与 `插件管理` 读同一份 `/api/plugins`。
- **P7 审计混入运行时导航**：`路由覆盖` 是**构建期产物**（提交物 + 新鲜度守卫 + 棘轮），
  却和运行时监控并列。

---

## 4. 逐项方案

### 4.1 「人格配置」并入「提示词实验室」

- **证据**：`personality.tsx` 管 6 维（语气/情感/简练/主动/幽默/同理心）+ 预设
  （`/api/personality`）；`prompt-lab/index.tsx` 已是"顶栏 Tab + 左因素/右预览"，
  且已容纳 `IdentityPromptPanel`（身份提示词）与 `LlmMonitorPanel`（LLM 通信）。
- **做法**：在 prompt-lab 的 Tab 条新增第 3 个 Tab「人格」，内容即现 `PersonalityPage`；
  删除 `persona` 分组。
- **语义**：人格是"它是谁"（画像），因素实验室是"它怎么答"（行为），二者同属"模型侧配置"，
  合页合理。
- **代价**：改 `hubNav.test.ts`；旧深链 `?panel=persona/personality` 需兼容映射。

### 4.2 「扩展中心」与「插件管理」合并

| | 插件管理（`pages/hub/plugin-manage.tsx`） | 扩展中心（`features/extensions/index.tsx`） |
|---|---|---|
| 数据源 | `/api/plugins` + `/api/plugins/reload` | 同左 |
| 交互 | 逐插件展开、Schema 表单、令牌、重载 | 把所有声明 schema 的插件**铺一屏** |
| 特有 | 「可配置 / 前端插槽」徽标 | 挂载插件自带 UI（`client_slot.module` → `registerPluginModule`） |

- **做法**：合并为「插件与扩展」一个导航项，内部两 Tab：
  **① 清单与配置**（现插件管理）/ **② 扩展呈现与自带 UI**（现扩展中心）。
  保留 `registerPluginModule` 链路（它是插件自带 UI 的唯一入口），合并 `reload` 动作，避免两个刷新按钮。
- **代价**：删除 `features/extensions` 后自动发现只剩 `route-coverage` 一个实例，
  `featureRegistry.test.ts` 需同步；`order: 900` 一并移除。

### 4.3 「经验库」移入「记忆管理」（与「知识库系统」并列）

两者是"记忆"族里**两个不同物种**，数据模型、检索算法、质量机制都不同：

| 维度 | 经验库 `/api/experience/*` | 知识库系统 `/api/knowledge/*` |
|---|---|---|
| 数据单位 | 一条**任务经验**：task / task_type / stack / diffs / pitfalls[{symptom,cause,fix,verified_by}] | 一张**知识卡片**：frontmatter + 正文 + 出链/入链 |
| 来源 | 离线 CLI 从**历史会话**提炼（extract.py），面板做审阅 | 人工/导入新建编辑 |
| 回答 | "这类活怎么干、别再踩什么坑"（过程与纠错） | "这是什么、和什么有关"（内容与概念） |
| 检索 | `/search`（score / legs 多路） | `/query`（RRF 融合 + 双链扩展 + rerank） |
| 结构 | 平铺语料 | **双链图谱**（`/graph`） |
| 质量 | verified(pass/fail/unverified) + review + **批次回滚** | `/lint` 健康分（孤儿/死链/索引漂移/超期未访问）+ 删除 409 入链保护 |
| 定位 | 执行经验的**审查台** | 知识的**编辑器 + 检索引擎** |

- **做法**：把顶层 `experience` 降为 `memory/experience`，排在 `memory/knowledge` 之后：
  `[memory/manual, memory/auto, memory/knowledge, memory/experience, memory/search]`。
- **明确不合并组件**：两者 schema/检索/质量机制不同，合成一个页面只会拧巴。
- **代价**：必须改 `hubNav.test.ts` 里那条**精确相等**的 `memory.children` 断言；
  旧深链 `?panel=experience` → `memory/experience` 映射。

### 4.4 「路由覆盖」归位（不是全景看板）

- **证据**：`features/route-coverage/index.tsx` 读 `GET /api/audit/route-ui-coverage`，
  即提交产物 `reports/route_ui_coverage.json`；由 `scripts/audit/route_ui_coverage.py` 离线生成，
  有新鲜度守卫与"未分类活体路由**只允许收缩**"的棘轮门禁。
- **判断**：全景看板四子项（健康仪表盘/全景感知/系统监控/日志查看）都是**运行时运维**视图；
  路由覆盖是**构建期质量审计**，语义不匹配。
- **建议**：放入**治理面板**（已有「能力地图」「审计导出」，同类语义），
  作为「能力地图」下的一个 Tab 或独立子项；若更强调"开发质量"，放**系统管理**（审计/日志旁）亦可。

---

## 5. 目标导航树（建议，供评审）

```text
会话相关
├── 会话任务
├── 提示词实验室            （新增 Tab：人格）
├── 技能中心
└── 工具调用

运行与治理
├── 全景看板（健康 / 感知 / 监控 / 日志）
├── 治理面板（流水线 / 审批 / 能力地图[+路由覆盖] / ROI / 事故 / 记忆技能库 / 审计 / 开关）
└── 循环工程（心跳 / 定时）        ← 也可并入全景看板

记忆与资产
├── 记忆管理（手动 / 自动 / 知识库系统 / 经验库 / 搜索）
├── 资产管理（8 类，**只读盘点**）
└── 知识库检索（可选：与记忆搜索统一）

分身
└── 装配车间（创建与组装 / 系统自我复制）

系统
├── 系统组件（模块列表 / 插件与扩展）
├── 网络配置
└── 系统管理（仪表盘 / 导出 / 用户 / 角色 / 菜单 / 审计 / 消息 / 日志）
```

（代码块仅示意分组，不改变叶子项自身的组件实现。）

---

## 6. 迁移顺序

**阶段 0 —— 深链兼容层（先做，避免断链）**
- 在 `WorkbenchApp` 的 key 解析处加**旧 key → 新 key** 映射表：
  `persona/personality → prompt-lab`、`experience → memory/experience`、
  `components/plugins|extensions → components/plugins`（合并后）。
- 验收：旧链接仍落到正确页面；`navKeyFromSearch` 的"非法 key 拒绝"语义不变。

**阶段 1 —— 三个低风险合并（各自独立 PR，便于回滚）**
1. 人格 → 提示词实验室（改 `hubNav.test.ts`）
2. 扩展中心 ⊕ 插件管理（改 `featureRegistry.test.ts`）
3. 经验库 → 记忆管理（改 `hubNav.test.ts` 的 memory.children 断言）

**阶段 2 —— 路由覆盖归位**（治理面板或系统管理；改 `hubNav.search.test.ts` 如涉及相邻关系）

**阶段 3 —— 顶层收敛到 6–8 桶**（最后做，风险最大；需要一次性更新全部顺序断言）

每个阶段的验收：`pnpm -C yunshu-ui test` 全绿 + 侧栏搜索能命中新 key + 旧深链可用。

---

## 7. 后端未呈现的路由（数据与做法）

来自 `reports/route_ui_coverage.json`：

- 总量：**456** 条路由，其中**活体 233**、死拷贝 223。
- 按类别：ui 113 / cli_script 21 / runtime_only 24 / unreferenced 298。
- **活体但 UI 无引用**：**186** 条（ui 47 / cli_script 8 / runtime_only 22 / unreferenced 156）。

可优先评审的候选（都有后端实现、当前无 UI 引用）：

| 路由 | 归属文件 | 建议 |
|---|---|---|
| `/api/browser/screenshot`、`/api/browser/close` | `agent/server_routes/routes_workspace.py` | 工作区能力，可做成会话侧面板 |
| `/api/clipboard` | `routes_workspace.py` | 同上 |
| `/api/cognitive/status` | `routes_panorama.py` | 适合全景看板 |
| `/api/cognitive/prompt`、`/api/cognitive/reject`、`/api/cognitive/translate` | `cognitive/flask_adapter.py` | 认知闭环面板（先定是否该进 UI） |
| `/api/config/logs` | `routes_config.py` | 系统管理·日志旁 |
| `/api/dashboard/summary` | `plugins/admin_api.py` | 管理仪表盘 |

**做法（重要）**：不要无差别补齐。`cli_script` / `runtime_only` 里很多是**故意**不进 UI 的。
建议在「路由覆盖」页给每条加一个**该进 UI / 不该进**的标注列，只把"该进却没进"的生成待办，
并保持"未分类活体路由只允许收缩"的棘轮不放宽。

---

## 8. 风险与不变量（不得回退）

- `HUB_NAV` 是**单一数据源**；`filterNav` 不得就地改写（搜一次之后导航永久变短是最难查的状态污染）。
- `navKeyFromSearch` 必须继续拒绝非法 key。
- 「治理面板」必须**一屏可达**（历史实测缺陷的正面判据），任何收敛不得把它推回侧栏底部。
- 工作台内扩展，**不另起独立 Web App**（UI 五坑④）。
- 深链是外部契约：合并/移动必须带兼容映射，否则收藏与外部引用全断。

---

## 9. 待决策问题（需人类拍板）

1. 顶层收敛的目标：6 / 7 / 8 个桶？是否接受一次性更新全部顺序断言？
2. 「经验库」是移入「记忆管理」，还是与「技能中心」并列（二者都与执行相关）？
3. 「路由覆盖」最终落「治理面板」还是「系统管理」？
4. 三个检索面（记忆搜索 / 知识库 query / 经验搜索）是否统一为一个「全库检索」入口？
5. 深链兼容映射保留多久（一个版本 / 长期）？
6. 后端未呈现路由：是否授权按"该进 UI"标注后分批补，先补哪几条？
