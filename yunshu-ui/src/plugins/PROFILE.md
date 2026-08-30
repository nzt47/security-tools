# PROFILE.md —— 界面组装配置说明（`src/plugins/profile.json`）

> 阶段 2（前端插槽化）的**唯一组装配置**：插槽条目的顺序（`order`）、显隐（`hidden`）
> 全部由 profile 决定。实现见 `src/plugins/slotRegistry.ts`，方案见
> `docs/yunshu-pluginization/PLAN-2-frontend-slots.md` §2、§3。

---

## 1. 文件位置与加载

| 文件 | 作用 |
|---|---|
| `src/plugins/profile.json` | 默认组装配置（界面生产形态） |
| `src/plugins/profile.alt.json` | 验证变体（交换 sidebar 顺序 / 隐藏面板），供演示与后续插件生态参考 |
| `src/plugins/slotRegistry.ts` | `DEFAULT_PROFILE`（代码内回退兜底）+ 加载/重载逻辑 |
| `src/plugins/SlotProvider.tsx` | 挂载时 `reloadProfile()` 加载 profile.json |

加载流程：`SlotProvider` 挂载 → `reloadProfile('profile.json')`（惰性读取文件原始文本 →
`JSON.parse` → 归一化）。注册表初始即为 `DEFAULT_PROFILE`，首帧渲染始终完整。

---

## 2. 文件格式

profile 文件外层是 `slots` 容器，每个插槽一个数组：

```jsonc
{
  "slots": {
    "topbar":  [{ "id": "status", "order": 10 }],
    "sidebar": [
      { "id": "panels", "order": 5 },
      { "id": "mascot", "order": 10 },
      { "id": "sessions", "order": 20 }
    ],
    "main":   [{ "id": "chat", "order": 10 }],
    "panels": [
      { "id": "skills", "order": 10, "hidden": true },
      { "id": "knowledge", "order": 20, "hidden": true },
      { "id": "devconsole", "order": 30, "hidden": true },
      { "id": "plugin-center", "order": 40, "hidden": true }
    ]
  }
}
```

> 注：`slots` 容器是 JSON 的「可读性」包装；`slotRegistry` 内部以
> `{ slotId: [...] }` 平铺结构工作，`normalizeProfile` 会自动展开容器。

### 条目字段

| 字段 | 必填 | 类型 | 默认 | 说明 |
|---|---|---|---|---|
| `id` | ✅ | string | — | 条目唯一 id（与 `mountToSlot` 挂载 id 对应） |
| `order` | 否 | number | `100` | 排序，小在前。缺省用组件默认 `order`，再缺省 100 |
| `hidden` | 否 | boolean | `false` | `true` 表示默认隐藏。缺省用组件默认 `hidden` |
| `title` / `icon` | 否 | string | — | 面板切换器按钮文案/图标（由挂载元数据提供，profile 不覆盖） |

---

## 3. 插槽与条目清单（默认 profile）

### `topbar` —— 顶部状态区（App 侧边栏头部）

| 条目 id | 组件 | 默认 order | 说明 |
|---|---|---|---|
| `status` | StatusIndicator | 10 | 系统状态指示（`SlotHost` 经 `props.status` 传入） |

### `sidebar` —— 左侧栏（自上而下）

| 条目 id | 组件 | 默认 order | 说明 |
|---|---|---|---|
| `panels` | PanelSwitcher | 5 | 面板切换器（技能管理/知识库/DevConsole 开关按钮 + 浮层） |
| `mascot` | Mascot + 情绪文案 | 10 | 云枢 Mascot，情绪由聊天流驱动 |
| `sessions` | 会话列表 | 20 | 会话列表（新建/切换会话，`SlotHost` 经 props 下发） |

### `main` —— 主聊天区

| 条目 id | 组件 | 默认 order | 说明 |
|---|---|---|---|
| `chat` | ChatWindow（含输入框） | 10 | 聊天窗口，streaming 时追加 typing 占位 |

### `panels` —— 浮层面板区（`hidden: true` = 默认关闭，按钮仍显示）

| 条目 id | 组件 | 默认 order | 默认 hidden | 说明 |
|---|---|---|---|---|
| `skills` | SkillManagement | 10 | true | 技能管理 & 工作流学习 |
| `knowledge` | Knowledge | 20 | true | 知识库 |
| `devconsole` | ObservabilityDevtools | 30 | true | DevConsole（自定位浮层，portal 到 body） |
| `plugin-center` | PluginPanel | 40 | true | 插件中心（T3.3：schema 驱动配置表单，`/api/plugins` 清单） |

> `panels` 数组即「面板切换器按钮清单」：**从数组移除某条目 → 切换器不再显示该按钮**；
> `hidden: true` → 初始关闭（按钮仍在，点击打开）。

---

## 4. 回退语义（配置异常静默降级）

任何配置异常**只 `console.warn`，绝不抛错**阻断渲染：

| 场景 | 行为 |
|---|---|
| `profile.json` 缺失 / 改名 / 加载失败 | `reloadProfile` warn → 使用代码内 `DEFAULT_PROFILE` |
| `profile.json` JSON 语法错误 / 结构无效 | `loadProfileFromRaw` warn → 回退 `DEFAULT_PROFILE` |
| 某插槽在 profile 中未出现 | 渲染该插槽**全部已挂载条目**（缺省 order 100） |
| 某条目在 profile 中缺失 | 用组件默认 `order` / `hidden` |
| 某条目在 profile 中声明但未挂载 | 忽略（不渲染，不报错，warn 一次） |
| 插槽配置不是数组 / 条目缺 `id` | 跳过该配置项并 warn |

`DEFAULT_PROFILE` 与 `profile.json` 内容保持一致，由单测守护；删除 `profile.json`
启动，界面仍完整（回退生效）。

---

## 5. 运行时切换变体（调试 / 后续动态装载）

`reloadProfile(variant?)` 支持运行时重载与变体切换（API 由 `@/plugins` 导出）：

```ts
import { reloadProfile } from '@/plugins';

await reloadProfile();                  // 重新加载默认 profile.json
await reloadProfile('profile.alt.json'); // 切换到 alt 变体
await reloadProfile('profile.json');    // 切回默认
```

- 返回 `Promise<boolean>`：成功应用 true；文件缺失/解析失败 false（已回退默认）。
- 注册表非响应式：`SlotProvider` 内部在加载完成后自动 force 一次重渲染；
  手动调用后界面即按新配置组装（面板切换器按新清单重建按钮与初始显隐）。

**dev 下验证方式（任选其一）：**

1. **改文件 + HMR**：直接编辑 `profile.json`（Vite 热更，界面自动按新配置重装）；
2. **控制台运行时切换**（无需改文件）：
   ```js
   // 浏览器 devtools 控制台
   import('/src/plugins/index.ts').then(m => m.reloadProfile('profile.alt.json'))
   ```
3. **文件名互换验证回退**：把 `profile.json` 改名/删除 → 刷新，界面按
   `DEFAULT_PROFILE` 完整渲染（控制台有 warn）；恢复文件后刷新即还原。

### `profile.alt.json` 与默认的差异（验证点）

| 插槽 | 默认 profile | alt 变体 | 可观察效果 |
|---|---|---|---|
| `sidebar` | panels(5) → mascot(10) → sessions(20) | mascot(5) → sessions(10) → panels(20) | 侧栏顺序交换：Mascot 置顶、切换器沉底 |
| `panels` | skills/knowledge/devconsole 均 hidden | skills `hidden:false`（默认打开）、knowledge hidden、**devconsole 不在清单** | 「技能管理」面板初始展开；切换器不再显示 DevConsole 按钮 |

---

## 6. 相关 API 速查（`src/plugins/slotRegistry.ts`）

| API | 说明 |
|---|---|
| `DEFAULT_PROFILE` | 代码内默认 profile（回退兜底，与 profile.json 一致） |
| `loadProfile(p)` | 直接应用 profile（支持 `{slots}` 容器或平铺结构；无效回退默认） |
| `loadProfileFromRaw(raw)` | 从 JSON 文本加载（解析失败回退默认，返回 boolean） |
| `reloadProfile(variant?)` | 运行时重载/切换 profile 变体（默认 'profile.json'） |
| `getProfile()` | 读取当前 profile（未加载时为 DEFAULT_PROFILE） |
| `getSlotEntries(slotId)` | 渲染用：应用 order/hidden、过滤 hidden、按序排列 |
| `getAllSlotEntries(slotId)` | 含 hidden 条目的全量读取（切换器/调试用） |
| `getManifestEntries(slotId)` | profile 数组即清单；未配置时回退全部已挂载 |
