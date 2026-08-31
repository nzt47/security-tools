/**
 * demo-ui.js —— Demo 插件客户端模块（任务 T4.2 进阶演示）
 *
 * 由后端插件 manifest 的 clientSlot.module（/plugins/demo-ui.js）声明，
 * 前端「加载 UI」按钮用动态 import() 加载本模块并调用 register(registry)。
 *
 * 约定（T4.2 §3）：模块导出 register(slotRegistry)。
 * - public/ 下的原生 ES 模块不做任何转译：不 import React/JSX，
 *   组件用 registry.createElement 创建；
 * - registry 附带 React / mountToSlot / extendProfile / openPanel 等能力
 *   （见 yunshu-ui/src/plugins/pluginDiscovery.ts 的 SlotRegistryFacade）。
 */
export function register(registry) {
  const { createElement, mountToSlot, extendProfile, openPanel } = registry;

  // 演示组件：纯 createElement（hyperscript），无需 JSX 转译
  function DemoWidget() {
    return createElement(
      'div',
      { className: 'demo-widget', 'data-testid': 'demo-client-ui', style: { padding: '16px 20px' } },
      createElement('h3', { style: { fontSize: 15, fontWeight: 600, margin: '0 0 8px' } }, 'Demo 插件动态 UI'),
      createElement(
        'p',
        { style: { fontSize: 13, lineHeight: 1.6, margin: 0, color: 'var(--text-muted, #666)' } },
        '本面板由 /plugins/demo-ui.js 动态装载：刷新发现插件 → 点「加载 UI」→ 挂入 panels 插槽。'
      ),
    );
  }

  const entryId = 'demo-ui';
  // 挂入 panels 插槽（面板切换器/PanelHost 消费），并追加到 profile 清单使其可见
  mountToSlot('panels', {
    id: entryId,
    title: 'Demo UI',
    icon: '🧩',
    component: DemoWidget,
    order: 999,
  });
  extendProfile('panels', { id: entryId, order: 999, hidden: false });
  // 打开新面板，让装载结果立即可见（panelsStore 变更触发切换器重渲染）
  openPanel(entryId);
}
