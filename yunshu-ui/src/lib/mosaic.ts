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

/** 四个固定面板：左侧导航 / 主对话流 / 右侧思考过程 / 代码编辑器 */
export const PANEL = {
  NAV: 'nav',
  CHAT: 'chat',
  THINK: 'think',
  CODE: 'code',
} as const;

export type PanelId = (typeof PANEL)[keyof typeof PANEL];

export const PANEL_TITLES: Record<PanelId, string> = {
  [PANEL.NAV]: '导航 · 历史',
  [PANEL.CHAT]: '对话',
  [PANEL.THINK]: '思考过程',
  [PANEL.CODE]: '代码编辑器',
};

/** 默认布局：nav | chat | (think / code)，splitPercentages 各子区占比之和须为 100 */
export const DEFAULT_LAYOUT: MosaicNode<PanelId> = {
  type: 'split',
  direction: 'row',
  children: [
    PANEL.NAV,
    {
      type: 'split',
      direction: 'row',
      children: [
        PANEL.CHAT,
        {
          type: 'split',
          direction: 'column',
          children: [PANEL.THINK, PANEL.CODE],
          splitPercentages: [55, 45],
        },
      ],
      splitPercentages: [72, 28],
    },
  ],
  splitPercentages: [16, 84],
};

/** LocalStorage 键（带版本号，升级布局结构时变更） */
export const LAYOUT_STORAGE_KEY = 'yunshu:mosaic:layout:v1';

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

/**
 * 从布局树中摘除指定面板（面板被分离为独立窗口后调用）。
 * 规则：
 *  - split 节点删除后剩 1 个子节点 → 上提为父级；剩 0 个 → null
 *  - tabs 节点删除后剩 1 个 tab → 折叠为叶子
 *  - children 变化后 splitPercentages 重置为均分（与 children 数量对齐，避免校验失败）
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
    const children = node.children
      .map((c) => removePanelFromLayout(c, panelId))
      .filter((c): c is MosaicNode<PanelId> => c !== null);
    if (children.length === 0) return null;
    if (children.length === 1) return children[0];
    const splitPercentages = children.map(() => 100 / children.length);
    return { type: 'split', direction: node.direction, children, splitPercentages };
  }
  if (node.type === 'tabs') {
    const tabs = node.tabs.filter((t) => t !== panelId);
    if (tabs.length === 0) return null;
    if (tabs.length === 1) return tabs[0];
    return { type: 'tabs', tabs, activeTabIndex: Math.min(node.activeTabIndex, tabs.length - 1) };
  }
  return node;
}
