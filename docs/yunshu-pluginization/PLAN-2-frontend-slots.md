# PLAN-2：前端插槽化（阶段 2）

> 配套总览：`docs/yunshu-pluginization/README.md`
> 目标文件：`yunshu-ui/src/`
> 约束：**界面视觉与交互行为不变**，只是组装方式从「手写布局 + 布尔开关」变为「插槽 + 注册表 + profile 配置」。
> 说明：`App.tsx`（381 行）当前用 `useState` 布尔开关切换 SkillManagement / Knowledge 等面板，本阶段把它拆成插槽体系。

---

## 1. 设计总览

```
profile.json ──► loadProfile() ──► SlotRegistry（全局注册表）
                                      │
            ┌─────────────────────────┼────────────────────────┐
            ▼                         ▼                        ▼
   registerSlot('sidebar')   mountToSlot('sidebar', Mascot)  <SlotHost slotId="sidebar" />
```

**三个核心原语：**

1. `registerSlot(slotId, opts?)` — 声明一个插槽（布局中的占位区域）
2. `mountToSlot(slotId, entry) / unmountFromSlot(slotId, id)` — 把组件挂进/摘出插槽
3. `<SlotHost slotId="..."/>` — 渲染某个插槽内按序排列的所有组件

**Profile（配置驱动）**：`profile.json` 描述「哪些插槽启用、每个插槽里挂哪些组件、顺序、显隐」，运行时加载后决定最终界面。没有 profile 时使用代码内默认挂载（回退）。

---

## 2. 核心实现（`yunshu-ui/src/plugins/slotRegistry.ts`，约 60 行）

```ts
import React from 'react';

export interface SlotEntry {
  id: string;                    // 组件唯一 id，如 'mascot'
  component: React.ComponentType; // 要渲染的组件
  order?: number;                // 排序，小在前，默认 100
  hidden?: boolean;              // profile 可置为 true 隐藏
}

export interface SlotProfile {
  [slotId: string]: Array<{ id: string; order?: number; hidden?: boolean }>;
}

const slots = new Map<string, Map<string, SlotEntry>>();
let profile: SlotProfile = {};

export function registerSlot(slotId: string): void {
  if (!slots.has(slotId)) slots.set(slotId, new Map());
}

export function mountToSlot(slotId: string, entry: SlotEntry): void {
  registerSlot(slotId);
  slots.get(slotId)!.set(entry.id, entry);
}

export function unmountFromSlot(slotId: string, id: string): void {
  slots.get(slotId)?.delete(id);
}

export function getSlotEntries(slotId: string): SlotEntry[] {
  const entries = [...(slots.get(slotId)?.values() ?? [])];
  const cfg = profile[slotId] ?? [];
  return entries
    .map(e => {
      const c = cfg.find(c => c.id === e.id);
      return { ...e, order: c?.order ?? e.order ?? 100, hidden: c?.hidden ?? e.hidden ?? false };
    })
    .filter(e => !e.hidden)
    .sort((a, b) => (a.order ?? 100) - (b.order ?? 100));
}

export function loadProfile(p: SlotProfile): void {
  profile = p;
}

export function getProfile(): SlotProfile {
  return profile;
}
```

```tsx
// yunshu-ui/src/plugins/SlotHost.tsx
import React from 'react';
import { getSlotEntries } from './slotRegistry';

export function SlotHost({ slotId, className }: { slotId: string; className?: string }) {
  const entries = getSlotEntries(slotId);
  return (
    <div className={className} data-slot={slotId}>
      {entries.map(e => {
        const C = e.component;
        return <C key={e.id} />;
      })}
    </div>
  );
}
```

**Provider 注入**（`src/plugins/SlotProvider.tsx`，可选）：

```tsx
import React, { useEffect } from 'react';
import { loadProfile } from './slotRegistry';
import defaultProfile from './profile.json';

export function SlotProvider({ children }: { children: React.ReactNode }) {
  useEffect(() => { loadProfile(defaultProfile); }, []);
  return <>{children}</>;
}
```

---

## 3. profile.json 格式（`yunshu-ui/src/plugins/profile.json`）

```json
{
  "slots": {
    "topbar":   [{ "id": "status", "order": 10 }, { "id": "modeSwitch", "order": 20 }],
    "sidebar":  [{ "id": "mascot", "order": 10 }, { "id": "history", "order": 20 }],
    "main":     [{ "id": "chat", "order": 10 }],
    "panels":   [{ "id": "skills", "order": 10, "hidden": true },
                 { "id": "knowledge", "order": 20, "hidden": true },
                 { "id": "devconsole", "order": 30, "hidden": true }]
  }
}
```

> `hidden: true` 的面板仍注册在插槽里（保持「可配置」），但默认不显示；切换入口（如侧边栏按钮）由「面板切换器」组件统一驱动。

---

## 4. App.tsx 改造策略

| 现状 | 改造后 |
|---|---|
| `App.tsx` 内手写 `<div>` 布局 | `App.tsx` 只渲染 `<SlotHost slotId="topbar"/>` `<SlotHost slotId="sidebar"/>` `<SlotHost slotId="main"/>` `<SlotHost slotId="panels"/>` |
| `skillMgmtOpen` / `knowledgeOpen` 布尔开关 | 面板切换器组件：读取 `panels` 插槽条目，渲染开关按钮，点击切换显隐（显隐状态放 zustand store 或本地 state） |
| 聊天状态（messages/sessions） | 保留在 App 或迁移到 `store/useChatStore.ts`（已有），插槽组件通过 props/store 消费 |

**迁移顺序（每步保持可运行）：**

1. T2.1 先建 registry + 单测（不接 App）。
2. T2.2 外壳插槽化：topbar（StatusIndicator）、sidebar（Mascot/HistoryPanel）、main（ChatWindow）挂进插槽，行为不变。
3. T2.3 面板插槽化：SkillManagement / Knowledge / DevConsole 注册进 `panels` 插槽，布尔开关改为面板切换器。
4. T2.4 profile 驱动：`hidden`/`order` 生效，无 profile 时回退默认挂载。

---

## 5. 插槽清单（本阶段定义）

| 插槽 id | 用途 | 初始挂载 |
|---|---|---|
| `topbar` | 顶部状态区 | StatusIndicator、模式切换 |
| `sidebar` | 左侧栏 | Mascot、HistoryPanel、SessionsDropdown |
| `main` | 主聊天区 | ChatWindow、ChatInput |
| `panels` | 浮层面板区 | SkillManagement、Knowledge、DevConsole |

> 插槽 id 是本方案的**稳定契约**，后续阶段 3/4 的插件面板也挂到这些插槽（或新增 `plugin` 插槽）。

---

## 6. 回归策略

1. 每个任务跑 `cd yunshu-ui && npx tsc -b --noEmit && npx vitest run`。
2. 手动冒烟：`npm run dev` 启动，核对聊天、面板开关、会话切换行为与改造前一致。
3. 每任务一个 git 提交。
