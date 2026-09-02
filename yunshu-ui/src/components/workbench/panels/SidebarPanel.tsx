/**
 * SidebarPanel —— 左侧导航 / 历史记录
 * 仅作框架占位：会话列表数据后续接入后端 /api/sessions
 */
import { useState } from 'react';
import { History, MessageSquare, LayoutDashboard, Wrench, BookOpen } from 'lucide-react';

const NAV_ITEMS = [
  { key: 'chat', label: '对话', icon: MessageSquare },
  { key: 'knowledge', label: '知识库', icon: BookOpen },
  { key: 'skills', label: '技能', icon: Wrench },
  { key: 'monitor', label: '监控', icon: LayoutDashboard },
] as const;

const MOCK_HISTORY = [
  { id: 's1', title: '性能优化方案讨论', time: '10:24' },
  { id: 's2', title: '知识库检索链路设计', time: '昨天' },
  { id: 's3', title: '多租户并发锁排查', time: '昨天' },
  { id: 's4', title: '监控告警规则配置', time: '08-12' },
];

export function SidebarPanel() {
  const [active, setActive] = useState<string>('chat');
  const [currentSession, setCurrentSession] = useState<string>('s1');

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* 品牌区 */}
      <div className="flex items-center gap-2.5 px-4 pb-3 pt-4">
        <div className="wb-logo-badge">
          <span className="text-[13px] font-semibold">枢</span>
        </div>
        <div>
          <div className="wb-brand-title">云枢 CLOUD HUB</div>
          <div className="font-mono text-[10px] uppercase tracking-[0.22em] text-slate-500">
            workbench v0.1
          </div>
        </div>
      </div>

      {/* 导航 */}
      <nav className="flex flex-col gap-1 px-3">
        {NAV_ITEMS.map(({ key, label, icon: Icon }) => (
          <button
            key={key}
            type="button"
            onClick={() => setActive(key)}
            className={`wb-nav-item ${active === key ? 'active' : ''}`}
          >
            <Icon size={14} />
            {label}
          </button>
        ))}
      </nav>

      <div className="my-3 border-t border-slate-800/70" />

      {/* 历史记录 */}
      <div className="flex items-center gap-1.5 px-4 pb-2 text-[10px] uppercase tracking-[0.2em] text-slate-500">
        <History size={11} />
        历史记录
      </div>
      <div className="wb-think-scroll min-h-0 flex-1 overflow-y-auto px-3 pb-3">
        {MOCK_HISTORY.map((s) => (
          <button
            key={s.id}
            type="button"
            onClick={() => setCurrentSession(s.id)}
            className={`wb-history-item ${currentSession === s.id ? 'active' : ''}`}
          >
            <span className="min-w-0 flex-1 truncate text-left">{s.title}</span>
            <span className="shrink-0 font-mono text-[10px] text-slate-600">{s.time}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
