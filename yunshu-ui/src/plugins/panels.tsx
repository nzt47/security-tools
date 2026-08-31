/**
 * 面板挂载（任务 T2.3）
 *
 * SkillManagement / Knowledge / DevConsole 以 { id, title, component, icon }
 * 元数据注册进 panels 插槽，由 PanelSwitcher 统一驱动（开关按钮 + PanelHost）：
 *
 * - skills     → SkillManagement（技能管理 & 工作流学习）
 * - knowledge  → Knowledge（知识库）
 * - devconsole → ObservabilityDevtools（DevConsole + StateInspector；内部管理
 *                requestInterceptor 生命周期：挂载时启用、卸载时恢复）
 *
 * mountPanels() 同时把 PanelSwitcher 挂进 sidebar 插槽（替换原 T2.2 的
 * SkillBtnEntry / KnowledgeBtnEntry 两个硬编码按钮，行为不变、三面板统一）。
 *
 * 说明：面板清单/顺序/显隐由 profile.json 的 panels 数组配置
 * （getManifestEntries 实现「数组即按钮清单，缺失回退全部挂载」）。
 */
import React from 'react';
import SkillManagement from '../components/SkillsMgmt/SkillManagement';
import Knowledge from '../pages/Knowledge';
import ObservabilityDevtools from '../components/ObservabilityDevtools';
import PluginPanel from './PluginPanel';
import { mountToSlot } from './slotRegistry';
import { SLOT_IDS } from './slots';
import { usePanelsStore } from './panelsStore';
import { PanelSwitcher } from './PanelSwitcher';
import './panels.css';

/** 面板条目 id 常量 */
export const PANEL_IDS = {
  skills: 'skills',
  knowledge: 'knowledge',
  devconsole: 'devconsole',
  pluginCenter: 'plugin-center',
} as const;

/**
 * 内容面板浮层框架：标题栏（title/icon）+ 关闭按钮 + 内容区。
 * 供 SkillManagement / Knowledge 等「块状内容」面板使用；
 * DevConsole 是自定位浮层（portal 到 body），不使用本框架。
 */
export const PanelFrame: React.FC<{
  id: string;
  title?: string;
  icon?: string;
  children: React.ReactNode;
}> = ({ id, title, icon, children }) => {
  const close = usePanelsStore((s) => s.close);
  return (
    <div className="panel-overlay" data-panel={id}>
      <div className="panel-overlay-head">
        <span className="panel-overlay-title">
          {icon && <span className="panel-overlay-icon">{icon}</span>}
          {title ?? id}
        </span>
        <button
          type="button"
          className="panel-overlay-close"
          aria-label={`关闭${title ?? id}`}
          onClick={() => close(id)}
        >
          ✕
        </button>
      </div>
      <div className="panel-overlay-body">{children}</div>
    </div>
  );
};

const SkillPanel: React.FC = () => (
  <PanelFrame id={PANEL_IDS.skills} title="技能管理" icon="⚙">
    <SkillManagement />
  </PanelFrame>
);

const KnowledgePanel: React.FC = () => (
  <PanelFrame id={PANEL_IDS.knowledge} title="知识库" icon="📚">
    <Knowledge />
  </PanelFrame>
);

// DevConsole：自定位浮层（FAB + 可展开面板，portal 到 body），无需 PanelFrame
const DevConsolePanel: React.FC = () => <ObservabilityDevtools />;

// 插件中心（T3.3）：schema 驱动配置表单（SchemaRenderer），使用标准 PanelFrame 框架
const PluginCenterPanel: React.FC = () => (
  <PanelFrame id={PANEL_IDS.pluginCenter} title="插件中心" icon="🧩">
    <PluginPanel />
  </PanelFrame>
);

/**
 * 挂载面板系统（幂等；App.tsx 模块顶层调用一次，渲染前执行，HMR 重载安全）：
 * - PanelSwitcher → sidebar 插槽（原技能管理/知识库按钮位置）；
 * - 三个面板 → panels 插槽。
 */
// eslint-disable-next-line react-refresh/only-export-components -- 插件注册表模块刻意混合导出组件与常量/函数
export function mountPanels(): void {
  mountToSlot(SLOT_IDS.sidebar, { id: 'panels', component: PanelSwitcher, order: 5 });
  mountToSlot(SLOT_IDS.panels, {
    id: PANEL_IDS.skills,
    title: '技能管理',
    icon: '⚙',
    component: SkillPanel,
    order: 10,
  });
  mountToSlot(SLOT_IDS.panels, {
    id: PANEL_IDS.knowledge,
    title: '知识库',
    icon: '📚',
    component: KnowledgePanel,
    order: 20,
  });
  mountToSlot(SLOT_IDS.panels, {
    id: PANEL_IDS.devconsole,
    title: 'DevConsole',
    icon: '🐛',
    component: DevConsolePanel,
    order: 30,
  });
  mountToSlot(SLOT_IDS.panels, {
    id: PANEL_IDS.pluginCenter,
    title: '插件中心',
    icon: '🧩',
    component: PluginCenterPanel,
    order: 40,
  });
}
