/**
 * 工作台布局核心定义
 * ------------------------------------------------
 * 面板 ID 是持久化到 LocalStorage 的锚点（业务语义不变），
 * 组件/样式可以改，ID 不可改 —— 这是布局恢复的【不易】约束。
 *
 * 注：react-mosaic-component v7 使用 n 元树（type: 'split' + children[]），
 * 旧版 first/second 结构（LegacyMosaicNode）已被废弃。
 */
import type { MosaicNode } from 'react-mosaic-component';

/**
 * 三个固定面板：左侧导航 / 主内容（对话） / 代码编辑器
 *
 * 【已下线】原 `think`（右侧「思考过程」）面板 —— 思考过程与工具调用步骤现已
 * **内联显示在每条回复里**（MessageItem 的 💭 思考过程 / 🔧 工具调用块，数据同为
 * SSE 的 thinking 事件，且归属到具体那条回复，可折叠、可开关），右侧面板是同一份
 * 数据的重复视图，故面板下线（历史布局由 stripRetiredPanels 迁移清理）。
 */
export const PANEL = {
  NAV: 'nav',
  CHAT: 'chat',
  CODE: 'code',
} as const;

export type PanelId = (typeof PANEL)[keyof typeof PANEL];

export const PANEL_TITLES: Record<PanelId, string> = {
  [PANEL.NAV]: '导航',
  [PANEL.CHAT]: '主内容',
  // 【2026-10-31】右栏改为可切换右坞（编辑器/文档/子代理/浏览器/终端）；id 不变
  [PANEL.CODE]: '右栏',
};

/**
 * 右栏（右坞，panel id = code）—— **默认收起、点开插回布局树**（2026-11-15 定稿）。
 *
 * 演进（三次回调，别再走回头路）：
 *  1. 原本它是布局树里的常驻 13% 子格 ⇒ Owner 嫌常驻太占地方；
 *  2. 临时改成"覆盖式 320px 抽屉"（不进布局树、盖在主内容上）⇒ Owner 否掉：
 *     「右栏弹出主内容应收缩，不要让右栏挡在上面，并且右栏无法拖拽变宽」；
 *  3. 定稿 = **默认收起（树里没有它）+ 打开时插到最右当一个真 mosaic 列**：
 *     主内容跟着收缩、中间的分割条就是它的宽度拖拽手柄（零宽 + ±6px 命中区）。
 *     ⇒ 既有"省地方"又保住"不遮挡 + 可拖宽"。
 *
 * 下面这个常量只用于**布局水合迁移**：把历史持久化布局里的 code 摘掉
 * （等价于"默认收起"），用户点顶栏右栏图标即可插回。
 */
export const DOCK_PANEL_IDS: readonly string[] = [PANEL.CODE];

/**
 * 右坞默认宽度（px）：插回布局时按当前视口折算成百分比。
 * 【2026-11-19】320 → 360：5 个 Tab（编辑器/文档/子代理/浏览器/终端）实测需要
 * ≈341px 才排得下一行，320px 时「终端」被挤到第二行（Owner 实测）。
 */
export const DOCK_DEFAULT_WIDTH = 360;

/**
 * 右坞在**默认布局**里占的百分比（= DOCK_DEFAULT_WIDTH 在 1440 视口下的折算值）。
 * 【2026-11-19】Owner 改口径：右坞**默认打开**（此前是"默认收起、点顶栏图标插回"），
 * 并把默认 Tab 设为「终端 · 实时活动」。
 * 取 28（不是 25）：5 个 Tab 一行需要 ≈341px，1280 视口下 25% 只有 317px ⇒「终端」又被挤到
 * 第二行；28% 在 1280 = 356px、1440 = 403px、1920 = 537px，三档都排得下一行。
 */
export const DOCK_DEFAULT_PERCENT = 28;

/**
 * 左侧导航默认占根横向 split 的百分比。
 * 【2026-11-19】16 → 11：Owner 要求"比现在再缩三分之一左右，所有叶子一行放得下"。
 * 1440 下 228 → 158px，最长叶子（5 个汉字 ≈ 60px）+ 图标 13 + 间距 + 箭头 12 ≈ 121px，
 * 一行放得下；1280 下 141px 仍够。
 */
export const NAV_DEFAULT_PERCENT = 11;

/**
 * 默认布局：nav | chat，splitPercentages 各子区占比之和须为 100
 *
 * 【2026-11-19 口径变更】右栏**默认打开**（Owner：默认打开、默认落在「终端 · 实时活动」）：
 * 树里常驻 code 一列（默认 28%，三档视口都能放下 5 个 Tab；主内容收缩、中间分割条可拖宽）。
 * 顶栏「右栏」开关仍可关闭（关闭 = 从树里摘掉；再打开 = 插回最右）。
 * 历史（2026-11-15）曾定"默认收起"，本轮按 Owner 最新要求改回默认打开。
 */
export const DEFAULT_LAYOUT: MosaicNode<PanelId> = {
  type: 'split',
  direction: 'row',
  children: [PANEL.NAV, PANEL.CHAT, PANEL.CODE],
  splitPercentages: [
    NAV_DEFAULT_PERCENT,
    100 - NAV_DEFAULT_PERCENT - DOCK_DEFAULT_PERCENT,
    DOCK_DEFAULT_PERCENT,
  ],
};

/** LocalStorage 键（带版本号，升级布局结构时变更） */
export const LAYOUT_STORAGE_KEY = 'yunshu:mosaic:layout:v1';

/**
 * 已下线面板 ID：布局持久化迁移时从历史布局里剔除。
 *
 * 【Why 必须迁移，而不是等 sanitizeLayout 兜底】sanitizeLayout 对非法叶子返回 null，
 * 整棵子树/整份布局会被判为脏数据 → 回退默认布局，用户此前的拖拽比例、面板拆分全丢。
 * 这里先按"剔除下线面板 + 折叠单子节点"处理，其余布局意图原样保留。
 */
export const RETIRED_PANEL_IDS: readonly string[] = ['think'];

/**
 * 从**原始持久化数据**（未经 sanitize）中剔除一批面板。
 * 规则与 removePanelFromLayout 一致：split 删到剩 1 个子节点则上提、剩 0 则整支移除；
 * tabs 删到剩 1 个则折叠为叶子；splitPercentages 只有在与 children 数量对齐时才保留。
 *
 * 【Why 泛化】已下线面板（think）与"已搬去抽屉的面板"（code）是两种原因、同一处理：
 * 都要从历史布局里剔除**且不能整份判脏**（否则用户的拖拽比例全丢）。
 */
export function stripPanels(value: unknown, ids: readonly string[]): unknown {
  if (typeof value === 'string') {
    return ids.includes(value) ? null : value;
  }
  if (value && typeof value === 'object') {
    const node = value as Record<string, unknown>;
    if (node.type === 'split' && Array.isArray(node.children)) {
      const pcts = Array.isArray(node.splitPercentages) ? node.splitPercentages : undefined;
      // 每个子节点的"剔除结果 + 原比例"一起走，才能知道**活下来的**是哪些比例
      // （不能拿新 children 去 includes 老对象：被折叠的子节点已经不是同一个引用了）。
      const mapped = node.children.map((c, i) => ({ child: stripPanels(c, ids), pct: pcts?.[i] }));
      const kept = mapped.filter((x) => x.child !== null && x.child !== undefined);
      const children = kept.map((x) => x.child);
      if (children.length === 0) return null;
      if (children.length === 1) return children[0];
      // 【2026-11-19 修了个真缺陷】剔除子节点后**不能直接把比例丢掉**：
      // 丢掉 = 交给 mosaic 均分 ⇒ 用户开着右坞刷新一次，导航就从 16% 变成 50%
      // （Owner 实测"导航栏默认变得很宽"就是这条；触发条件是"右坞开着时刷新"）。
      // 正确做法：取活下来的那些比例，再等比归一到 100（16/84 + 右坞 22 ⇒ 仍是 16/84）。
      let keptPcts: number[] | undefined;
      if (pcts && pcts.length === node.children.length) {
        const vals = kept.map((x) => x.pct as number);
        const sum = vals.reduce((a, b) => a + b, 0);
        if (sum > 0) {
          if (Math.abs(sum - 100) < 1e-9) {
            // 没删掉任何"带宽度"的子节点：原样保留（浮点不能重算，72/28 会变 28.000000000000004）
            keptPcts = vals.slice();
          } else {
            // 删掉的那一格宽度归**最后一个**幸存者（典型场景：右坞收起 ⇒ 导航 16% 不动、
            // 主内容从 62% 回到 84%）。等比归一会让导航一起变宽（16 → 20.5%），那是错的。
            const others = vals.slice(0, -1).reduce((a, b) => a + b, 0);
            const rest = 100 - others;
            keptPcts = rest > 0
              ? [...vals.slice(0, -1), Number(rest.toFixed(4))]
              : vals.map((v) => Number(((v / sum) * 100).toFixed(4)));
          }
        }
      } else if (pcts && pcts.length === children.length) {
        // 历史数据本来就没对齐（老版本只存了外层比例）：保持旧口径 —— 长度一致才保留
        keptPcts = pcts.slice();
      }
      return {
        type: 'split',
        direction: node.direction,
        children,
        splitPercentages: keptPcts,
      };
    }
    if (node.type === 'tabs' && Array.isArray(node.tabs)) {
      const tabs = node.tabs.filter((t): t is string => typeof t === 'string' && !ids.includes(t));
      if (tabs.length === 0) return null;
      if (tabs.length === 1) return tabs[0];
      return { type: 'tabs', tabs, activeTabIndex: 0 };
    }
  }
  return null;
}

/** 剔除已下线面板（RETIRED_PANEL_IDS）—— 历史布局迁移的入口（保留给既有用例/调用方） */
export function stripRetiredPanels(value: unknown): unknown {
  return stripPanels(value, RETIRED_PANEL_IDS);
}

/**
 * 校验从 LocalStorage 反序列化的布局树。
 * 防御手段：损坏 / 版本不匹配的数据一律回退默认布局，避免白屏。
 */
export function sanitizeLayout(value: unknown): MosaicNode<PanelId> | null {
  // 叶子节点：必须是合法面板 ID
  if (typeof value === 'string') {
    return (Object.values(PANEL) as string[]).includes(value) ? (value as PanelId) : null;
  }
  if (value && typeof value === 'object') {
    const node = value as Record<string, unknown>;
    if (node.type === 'split') {
      if (node.direction !== 'row' && node.direction !== 'column') return null;
      if (!Array.isArray(node.children) || node.children.length < 2) return null;
      const children = node.children
        .map((c) => sanitizeLayout(c))
        .filter((c): c is MosaicNode<PanelId> => c !== null);
      if (children.length < 2) return null; // 子树非法则整体放弃
      // 占比数组必须与 children 数量一致才可信，否则交由 Mosaic 默认均分
      const splitPercentages = Array.isArray(node.splitPercentages)
        ? node.splitPercentages.filter(
            (n): n is number => typeof n === 'number' && n > 0 && n < 100,
          )
        : undefined;
      return {
        type: 'split',
        direction: node.direction,
        children,
        splitPercentages:
          splitPercentages && splitPercentages.length === children.length
            ? splitPercentages
            : undefined,
      };
    }
    if (node.type === 'tabs') {
      if (!Array.isArray(node.tabs)) return null;
      const tabs = (node.tabs as unknown[])
        .filter((t): t is string => typeof t === 'string')
        .filter((t) => (Object.values(PANEL) as string[]).includes(t)) as PanelId[];
      if (tabs.length < 2) return null;
      const activeTabIndex =
        typeof node.activeTabIndex === 'number' &&
        node.activeTabIndex >= 0 &&
        node.activeTabIndex < tabs.length
          ? node.activeTabIndex
          : 0;
      return { type: 'tabs', tabs, activeTabIndex };
    }
  }
  return null;
}

/** 布局树里是否含有某个面板叶子（含 tabs 组里的） */
export function layoutHasPanel(
  node: MosaicNode<PanelId> | null | undefined,
  panelId: PanelId,
): boolean {
  if (!node) return false;
  if (typeof node === 'string') return node === panelId;
  if (node.type === 'split') return node.children.some((c) => layoutHasPanel(c, panelId));
  if (node.type === 'tabs') return node.tabs.includes(panelId);
  return false;
}

/** 取 split 的占比（缺失/长度不齐时退回均分） */
function splitPercentagesOf(node: { children: MosaicNode<PanelId>[]; splitPercentages?: number[] }): number[] {
  const pcts = node.splitPercentages;
  if (pcts && pcts.length === node.children.length && pcts.every((p) => typeof p === 'number' && p > 0)) {
    return pcts;
  }
  return node.children.map(() => 100 / node.children.length);
}

/**
 * 从布局树中摘除指定面板（面板被分离为独立窗口 / 被开关隐藏时调用）。
 * 规则：
 *  - split 节点删除后剩 1 个子节点 → 上提为父级；剩 0 个 → null
 *  - tabs 节点删除后剩 1 个 tab → 折叠为叶子
 *  - 剩下多个子节点时，被删掉的占比**按剩余占比等比放大**（而不是一律均分）：
 *    均分会把「导航 16% / 主内容 84%」悄悄变成 50/50，用户会以为布局被改了。
 */
export function removePanelFromLayout(
  node: MosaicNode<PanelId> | null,
  panelId: PanelId,
): MosaicNode<PanelId> | null {
  if (!node) return null;
  if (typeof node === 'string') {
    return node === panelId ? null : node;
  }
  if (node.type === 'split') {
    const oldPcts = splitPercentagesOf(node);
    const kept: MosaicNode<PanelId>[] = [];
    const keptPcts: number[] = [];
    node.children.forEach((child, i) => {
      const next = removePanelFromLayout(child, panelId);
      if (next !== null) {
        kept.push(next);
        keptPcts.push(oldPcts[i]);
      }
    });
    if (kept.length === 0) return null;
    if (kept.length === 1) return kept[0];
    const sum = keptPcts.reduce((a, b) => a + b, 0);
    const splitPercentages = sum > 0 ? keptPcts.map((p) => (p * 100) / sum) : kept.map(() => 100 / kept.length);
    return { type: 'split', direction: node.direction, children: kept, splitPercentages };
  }
  if (node.type === 'tabs') {
    const tabs = node.tabs.filter((t) => t !== panelId);
    if (tabs.length === 0) return null;
    if (tabs.length === 1) return tabs[0];
    return { type: 'tabs', tabs, activeTabIndex: Math.min(node.activeTabIndex, tabs.length - 1) };
  }
  return node;
}

/**
 * 把面板插回布局树的**默认位置**（顶栏开关重新打开面板时用）。
 *  - 导航 → 根横向 split 的第 0 位（16%）；主内容 → 第 1 位（其余全部）；
 *  - 右坞（code）→ **最右**，默认 320px 折成的占比，且**不从导航身上抢宽度**
 *    （导航是框架列：1440 屏插回后是 16% / 62% / 22%，主内容让出 22%）；
 *  - 根不是横向 split 时包一层新的横向 split（例：只有 main 时恢复成 nav | main）；
 *  - 已在树里则原样返回（幂等）。
 */
export function addPanelToLayout(
  node: MosaicNode<PanelId> | null,
  panelId: PanelId,
  percent?: number,
): MosaicNode<PanelId> {
  if (!node) return panelId;
  if (layoutHasPanel(node, panelId)) return node;
  if (panelId === PANEL.CODE) return addDockToLayout(node, percent ?? 22);
  const pct = panelId === PANEL.NAV ? NAV_DEFAULT_PERCENT : 100 - NAV_DEFAULT_PERCENT;
  const rest = 100 - pct;
  const index = panelId === PANEL.NAV ? 0 : 1;
  // 单个叶子：包一层横向 split 即可（导航在前 / 主内容在后）
  if (typeof node === 'string') {
    return index === 0
      ? { type: 'split', direction: 'row', children: [panelId, node], splitPercentages: [pct, rest] }
      : { type: 'split', direction: 'row', children: [node, panelId], splitPercentages: [rest, pct] };
  }
  if (node.type === 'split' && node.direction === 'row') {
    const children = [...node.children];
    const oldPcts = splitPercentagesOf(node);
    const at = Math.min(index, children.length);
    const sum = oldPcts.reduce((a, b) => a + b, 0);
    const scaled = oldPcts.map((p) => (p * (100 - pct)) / sum);
    children.splice(at, 0, panelId);
    scaled.splice(at, 0, pct);
    return { type: 'split', direction: 'row', children, splitPercentages: scaled };
  }
  return index === 0
    ? { type: 'split', direction: 'row', children: [panelId, node], splitPercentages: [pct, rest] }
    : { type: 'split', direction: 'row', children: [node, panelId], splitPercentages: [rest, pct] };
}

/**
 * 把右坞（code）插到最右：主内容收缩让位，导航宽度保持不变。
 * 例：默认 [nav 16, chat 84] + 22% 右坞 ⇒ [nav 16, chat 62, code 22]。
 * 根只有单个叶子时包一层横向 split。
 */
function addDockToLayout(node: MosaicNode<PanelId>, pct: number): MosaicNode<PanelId> {
  const dockPct = Math.min(45, Math.max(10, pct));
  if (typeof node === 'string') {
    return {
      type: 'split',
      direction: 'row',
      children: [node, PANEL.CODE],
      splitPercentages: [100 - dockPct, dockPct],
    };
  }
  if (node.type === 'split' && node.direction === 'row') {
    const pcts = splitPercentagesOf(node);
    const hasNavHead = node.children[0] === PANEL.NAV;
    const navPct = hasNavHead ? pcts[0] : 0;
    const restSum = pcts.reduce((acc, p, i) => acc + (hasNavHead && i === 0 ? 0 : p), 0) || 1;
    const budget = Math.max(5, 100 - navPct - dockPct);
    const scaled = pcts.map((p, i) => (hasNavHead && i === 0 ? navPct : (p * budget) / restSum));
    return {
      type: 'split',
      direction: 'row',
      children: [...node.children, PANEL.CODE],
      splitPercentages: [...scaled, dockPct],
    };
  }
  return {
    type: 'split',
    direction: 'row',
    children: [node, PANEL.CODE],
    splitPercentages: [100 - dockPct, dockPct],
  };
}
