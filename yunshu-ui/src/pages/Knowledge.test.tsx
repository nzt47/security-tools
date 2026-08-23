/**
 * 知识库前端测试（任务6）
 *
 * 验证范围：
 *  - 列表渲染与筛选
 *  - 状态角标四色（StatusBadge）
 *  - 详情抽屉（含入链/出链）
 *  - 搜索交互（融合检索命中展示）
 *  - 删除 409 入链保护提示
 *
 * 策略：mock ../api/knowledge 模块，避免真实 HTTP。
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import type { Card, CardDetail, HealthReport, KnowledgeHit } from '../api/knowledge-types';
import { ApiError } from '../lib/apiClient';
import StatusBadge from '../components/Knowledge/StatusBadge';
import CardDetailView from '../components/Knowledge/CardDetail';
import Knowledge from './Knowledge';

// ── mock API 模块 ──────────────────────────────────────────────
const apiMock = vi.hoisted(() => ({
  listCards: vi.fn(),
  getCard: vi.fn(),
  createCard: vi.fn(),
  updateCard: vi.fn(),
  deleteCard: vi.fn(),
  searchKnowledge: vi.fn(),
  getLint: vi.fn(),
}));

vi.mock('../api/knowledge', () => apiMock);

const BASE_CARD: Card = {
  title: '测试概念卡',
  slug: 'test-concept',
  status: 'current',
  type: 'concepts',
  source: '测试',
  date: '2026-08-08',
  tags: ['测试'],
  links: [],
  contradictions: [],
  insight: '一句话洞见',
  scope: '',
  content: '',
  metadata: {},
};

const BASE_DETAIL: CardDetail = {
  ...BASE_CARD,
  links: ['child-a'],
  incoming_links: ['parent-x'],
};

const BASE_REPORT: HealthReport = {
  checked_at: '2026-08-08T10:00:00',
  total_cards: 1,
  orphans: ['test-concept'],
  broken_links: [{ from_slug: 'a', to_slug: 'missing' }],
  index_drift: [],
  stale_cards: [],
  unresolved_conflicts: [],
  health_score: 92.5,
  suggestions: ['修复死链'],
};

describe('StatusBadge', () => {
  it('渲染四种状态文案且带 data-status 属性', () => {
    const { container } = render(
      <div>
        <StatusBadge status="draft" />
        <StatusBadge status="current" />
        <StatusBadge status="archive" />
        <StatusBadge status="unknown" />
      </div>,
    );
    expect(screen.getByText('草稿')).toBeTruthy();
    expect(screen.getByText('有效')).toBeTruthy();
    expect(screen.getByText('归档')).toBeTruthy();
    expect(screen.getByText('未知')).toBeTruthy();
    const badges = container.querySelectorAll('.kb-status-badge');
    expect(badges.length).toBe(4);
    expect(badges[0].getAttribute('data-status')).toBe('draft');
    expect(badges[1].getAttribute('data-status')).toBe('current');
    expect(badges[2].getAttribute('data-status')).toBe('archive');
    expect(badges[3].getAttribute('data-status')).toBe('unknown');
  });

  it('withText=false 时不渲染文字', () => {
    const { container } = render(<StatusBadge status="current" withText={false} />);
    expect(container.querySelector('.kb-status-badge')?.textContent).toBe('');
  });
});

describe('CardDetail 抽屉', () => {
  it('展示 frontmatter 字段、正文、入链与出链', () => {
    render(
      <CardDetailView card={BASE_DETAIL} onClose={() => {}} onOpenLink={() => {}} />,
    );
    expect(screen.getByText('测试概念卡')).toBeTruthy();
    expect(screen.getByText(/一句话洞见/)).toBeTruthy();
    expect(screen.getByText(/slug: test-concept/)).toBeTruthy();
    expect(screen.getByText('child-a')).toBeTruthy();   // 出链
    expect(screen.getByText('parent-x')).toBeTruthy();  // 入链
    expect(screen.getByText('出链 (1)')).toBeTruthy();
    expect(screen.getByText('入链 (1)')).toBeTruthy();
  });

  it('点击入链 chip 触发 onOpenLink', () => {
    const onOpen = vi.fn();
    render(<CardDetailView card={BASE_DETAIL} onClose={() => {}} onOpenLink={onOpen} />);
    fireEvent.click(screen.getByText('parent-x'));
    expect(onOpen).toHaveBeenCalledWith('parent-x');
  });

  it('点击遮罩触发 onClose', () => {
    const onClose = vi.fn();
    render(<CardDetailView card={BASE_DETAIL} onClose={onClose} />);
    fireEvent.click(document.querySelector('.kb-detail-overlay') as HTMLElement);
    expect(onClose).toHaveBeenCalled();
  });
});

describe('Knowledge 页面', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiMock.listCards.mockResolvedValue({ ok: true, cards: [BASE_CARD], count: 1 });
    apiMock.getLint.mockResolvedValue({ ok: true, report: BASE_REPORT });
    apiMock.getCard.mockResolvedValue({ ok: true, card: BASE_DETAIL });
  });

  it('初始化加载卡片列表与健康报告', async () => {
    render(<Knowledge />);
    await waitFor(() => {
      expect(screen.getByText('测试概念卡')).toBeTruthy();
    });
    expect(apiMock.listCards).toHaveBeenCalled();
    expect(apiMock.getLint).toHaveBeenCalled();
    // 健康分渲染
    await waitFor(() => {
      expect(screen.getByText('92.5')).toBeTruthy();
    });
  });

  it('知识库为空时不自动创建数据（空白状态）', async () => {
    apiMock.listCards.mockResolvedValue({ ok: true, cards: [], count: 0 });
    render(<Knowledge />);
    await waitFor(() => {
      expect(screen.getByText(/暂无卡片/)).toBeTruthy();
    });
    expect(apiMock.createCard).not.toHaveBeenCalled();
  });

  it('点击卡片打开详情抽屉', async () => {
    render(<Knowledge />);
    await waitFor(() => {
      expect(screen.getByText('测试概念卡')).toBeTruthy();
    });
    fireEvent.click(screen.getByText('测试概念卡'));
    await waitFor(() => {
      expect(screen.getByText('出链 (1)')).toBeTruthy();
    });
    expect(apiMock.getCard).toHaveBeenCalledWith('test-concept');
  });

  it('快速切换卡片时丢弃过期详情响应（竞态守卫）', async () => {
    let resolveA!: (v: unknown) => void;
    let resolveB!: (v: unknown) => void;
    apiMock.getCard
      .mockImplementationOnce(() => new Promise((res) => { resolveA = res; }))
      .mockImplementationOnce(() => new Promise((res) => { resolveB = res; }));

    const secondCard: Card = { ...BASE_CARD, slug: 'second', title: '第二张卡' };
    apiMock.listCards.mockResolvedValue({ ok: true, cards: [BASE_CARD, secondCard], count: 2 });

    render(<Knowledge />);
    await waitFor(() => {
      expect(screen.getByText('测试概念卡')).toBeTruthy();
    });

    // 先点 A（test-concept），随即点 B（second），A 响应后到
    fireEvent.click(screen.getByText('测试概念卡'));
    fireEvent.click(screen.getByText('第二张卡'));

    resolveB({ ok: true, card: { ...BASE_DETAIL, slug: 'second', title: '第二张卡', incoming_links: [] } });
    await waitFor(() => {
      expect(screen.getByText('入链 (0)')).toBeTruthy(); // B 详情已展示
    });

    // A 的过期响应到达：应被丢弃，不覆盖 B 详情
    resolveA({ ok: true, card: { ...BASE_DETAIL, incoming_links: ['parent-x'] } });
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByText('入链 (1)')).toBeNull();
    expect(screen.getByText('入链 (0)')).toBeTruthy();
  });

  it('搜索交互：输入问题并检索，展示命中结果与来源标记', async () => {
    const hits: KnowledgeHit[] = [
      {
        slug: 'test-concept',
        title: '测试概念卡',
        status: 'current',
        type: 'concepts',
        score: 0.85,
        rerank_score: 0.9,
        source_ref: '[来源: test-concept|current]',
        snippet: '关于双链的说明片段',
      },
    ];
    apiMock.searchKnowledge.mockResolvedValue({ ok: true, hits, result: '' });
    render(<Knowledge />);
    const input = screen.getByPlaceholderText('输入问题，检索知识库（RRF 融合）');
    fireEvent.change(input, { target: { value: '什么是双链' } });
    fireEvent.click(screen.getByText('检索'));
    // 命中区独有来源标记 [来源: slug|status] 出现即代表搜索已渲染
    await waitFor(() => {
      expect(screen.getByText('[来源: test-concept|current]')).toBeTruthy();
    });
    expect(apiMock.searchKnowledge).toHaveBeenCalledWith('什么是双链', 5);
    // 状态角标在命中项中可见（列表区也有角标，故用 getAll）
    expect(screen.getAllByText('有效').length).toBeGreaterThan(0);
  });

  it('删除 409 时弹出入链保护提示', async () => {
    const err = new ApiError('API_HTTP_ERROR', '卡片存在入链，删除被拒: test-concept', 409, {
      incoming_links: ['parent-x'],
    });
    apiMock.deleteCard.mockRejectedValue(err);
    const alertSpy = vi.spyOn(window, 'alert').mockImplementation(() => {});
    const confirmSpy = vi.spyOn(window, 'confirm').mockImplementation(() => true);
    render(<Knowledge />);
    await waitFor(() => {
      expect(screen.getByText('测试概念卡')).toBeTruthy();
    });
    fireEvent.click(screen.getByTitle('删除'));
    await waitFor(() => {
      expect(alertSpy).toHaveBeenCalled();
    });
    const alertMsg = alertSpy.mock.calls[0][0] as string;
    expect(alertMsg).toContain('parent-x');
    expect(alertMsg).toContain('删除被拒');
    confirmSpy.mockRestore();
    alertSpy.mockRestore();
  });

  it('新建卡片表单提交后刷新列表', async () => {
    apiMock.createCard.mockResolvedValue({ ok: true, card: BASE_CARD });
    render(<Knowledge />);
    await waitFor(() => {
      expect(screen.getByText('测试概念卡')).toBeTruthy();
    });
    fireEvent.click(screen.getByText('✚ 新建卡片'));
    await waitFor(() => {
      expect(screen.getByText('新建卡片')).toBeTruthy();
    });
    fireEvent.change(screen.getByPlaceholderText('卡片标题（slug 默认由此生成）'), {
      target: { value: '新卡标题' },
    });
    fireEvent.change(screen.getByPlaceholderText('唯一标识（创建后不可修改）'), {
      target: { value: 'new-card' },
    });
    fireEvent.change(screen.getByPlaceholderText('一句话核心洞见（必填）'), {
      target: { value: '新卡洞见' },
    });
    fireEvent.click(screen.getByText('创建'));
    await waitFor(() => {
      expect(apiMock.createCard).toHaveBeenCalled();
    });
    expect(apiMock.listCards).toHaveBeenCalled();
  });
});
