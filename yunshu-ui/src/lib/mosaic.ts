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
 * 右侧「右栏」从 mosaic 布局树搬到**覆盖式抽屉**（2026-11-15 B 步，Owner 拍板）：
 *  - 布局树里不再有 code 叶子（DEFAULT_LAYOUT 不含它，历史布局由迁移剔除）；
 *  - 打开/关闭由 useLayoutStore.rightDockOpen 控制（持久化，默认收起）；
 *  - 为什么不让它继续当一个 mosaic 子格：右栏 13% 会**挤压**主内容（1440 屏 ≈187px），
 *    而 Owner 要的是"点开滑出、不占常驻宽度"⇒ 抽屉才是这个语义。
 *  ★ 只是**换呈现**：panel id 仍是 code，拖拽拆分/边缘分离的 Mosaic 机制原样保留。
 */
export const DRAWER_PANEL_IDS: readonly string[] = [PANEL.CODE];

/** 左侧导航默认占根横向 split 的百分比（与 A 步前的观感一致） */
export const NAV_DEFAULT_PERCENT = 16;

/**
 * 默认布局：nav | chat，splitPercentages 各子区占比之和须为 100
 *
 * 【2026-11-15 B 步】右栏改为默认收起的覆盖式抽屉 ⇒ 默认树里只有 nav / chat：
 * - 横向：少一条 13% 的常驻列（1440 屏 ≈187px），这是本轮"省地方"的大头；
 * - 竖向：面板标题栏已换成 6px 抓取条（见 WorkbenchApp.renderToolbar）。
 */
export const DEFAULT_LAYOUT: MosaicNode<PanelId> = {
  type: 'split',
  direction: 'row',
  children: [PANEL.NAV, PANEL.CHAT],
  splitPercentages: [NAV_DEFAULT_PERCENT, 100 - NAV_DEFAULT_PERCENT],
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
      const children = node.children
        .map((c) => stripPanels(c, ids))
        .filter((c) => c !== null && c !== undefined);
      if (children.length === 0) return null;
      if (children.length === 1) return children[0];
      const pcts = Array.isArray(node.splitPercentages) ? node.splitPercentages : undefined;
      return {
        type: 'split',
        direction: node.direction,
        children,
        splitPercentages: pcts && pcts.length === children.length ? pcts : undefined,
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
 *  - 根不是横向 split 时包一层新的横向 split（例：只有 main 时恢复成 nav | main）；
 *  - 已在树里则原样返回（幂等）。
 */
export function addPanelToLayout(
  node: MosaicNode<PanelId> | null,
  panelId: PanelId,
): MosaicNode<PanelId> {
  if (!node) return panelId;
  if (layoutHasPanel(node, panelId)) return node;
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
