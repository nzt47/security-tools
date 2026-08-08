/**
 * 知识库组件测试（任务6 Step 4）
 *
 * 覆盖：列表渲染 / 状态角标 / 详情展示 / 搜索交互。
 * 参考 App.test.tsx 的 mock 模式：mock global.fetch 返回知识库各接口响应。
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, waitFor, fireEvent, within } from '@testing-library/react';
import Knowledge from '../pages/Knowledge';

// ── 测试数据 ──────────────────────────────────────────────

const CARD_A = {
  title: 'RRF 融合检索',
  slug: 'rrf-fusion',
  status: 'current',
  type: 'concepts',
  source: 'task4',
  date: '2026-08-01',
  tags: ['retrieval'],
  links: ['bm25'],
  contradictions: [],
  insight: 'RRF 稳定融合多路召回',
  scope: 'knowledge',
  content: 'Reciprocal Rank Fusion 是知识库检索的核心算法。',
  metadata: {},
};

const CARD_B = {
  title: 'BM25 基线',
  slug: 'bm25',
  status: 'draft',
  type: 'entities',
  source: 'manual',
  date: '2026-08-02',
  tags: [],
  links: [],
  contradictions: [],
  insight: '',
  scope: 'knowledge',
  content: '',
  metadata: {},
};

const LINT_OK = {
  ok: true,
  report: {
    checked_at: '2026-08-08T10:00:00',
    total_cards: 2,
    orphans: [],
    broken_links: [],
    index_drift: [],
    stale_cards: [],
    unresolved_conflicts: [],
    health_score: 100,
    suggestions: [],
  },
};

const GRAPH_OK = {
  ok: true,
  nodes: [
    { id: 'rrf-fusion', label: 'RRF 融合检索', type: 'concepts', status: 'current' },
    { id: 'bm25', label: 'BM25 基线', type: 'entities', status: 'draft' },
  ],
  edges: [{ source: 'rrf-fusion', target: 'bm25' }],
};

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
  } as Response;
}

function makeFetchMock() {
  return vi.fn((url: string, init?: RequestInit) => {
    // 详情：GET /api/knowledge/cards/<slug>（slug 非 cards）
    if (url.startsWith('/api/knowledge/cards/')) {
      const slug = url.split('/').pop();
      if (slug === CARD_A.slug) {
        return Promise.resolve(jsonResponse({ ok: true, card: { ...CARD_A, incoming_links: ['bm25'] } }));
      }
      return Promise.resolve(jsonResponse({ ok: false, error: `卡片不存在: ${slug}` }, 404));
    }
    // 列表：GET /api/knowledge/cards（含 ?status=&type= 过滤）
    if (url === '/api/knowledge/cards' || url.startsWith('/api/knowledge/cards?')) {
      return Promise.resolve(jsonResponse({ ok: true, cards: [CARD_A, CARD_B], count: 2 }));
    }
    if (url.startsWith('/api/knowledge/lint')) {
      return Promise.resolve(jsonResponse(LINT_OK));
    }
    if (url.startsWith('/api/knowledge/graph')) {
      return Promise.resolve(jsonResponse(GRAPH_OK));
    }
    if (url.startsWith('/api/knowledge/query')) {
      const body = init?.body ? JSON.parse(String(init.body)) : {};
      if (body.question === 'rrf') {
        return Promise.resolve(
          jsonResponse({
            ok: true,
            hits: [
              {
                slug: 'rrf-fusion',
                title: 'RRF 融合检索',
                status: 'current',
                type: 'concepts',
                score: 12.5,
                rerank_score: 0,
                source_ref: 'rrf-fusion',
                snippet: 'Reciprocal Rank Fusion 是核心算法。',
              },
            ],
          }),
        );
      }
      return Promise.resolve(jsonResponse({ ok: true, hits: [] }));
    }
    if (url.startsWith('/api/knowledge/index')) {
      return Promise.resolve(jsonResponse({ ok: true, content: '# 索引' }));
    }
    return Promise.resolve(jsonResponse({ ok: false, error: 'not found' }, 404));
  });
}

// ── 测试套件 ──────────────────────────────────────────────

describe('知识库主页', () => {
  let fetchMock: ReturnType<typeof makeFetchMock>;

  beforeEach(() => {
    fetchMock = makeFetchMock();
    global.fetch = fetchMock as any;
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('列表区渲染卡片标题与状态角标（current/draft 配色区分）', async () => {
    render(<Knowledge />);

    await waitFor(() => {
      expect(screen.getAllByText('RRF 融合检索').length).toBeGreaterThan(0);
      expect(screen.getAllByText('BM25 基线').length).toBeGreaterThan(0);
    });

    // 状态角标文案
    expect(screen.getByText('现行')).toBeInTheDocument();
    expect(screen.getByText('草稿')).toBeInTheDocument();

    // 配色类名区分
    const badges = screen.getAllByTestId('status-badge');
    const cls = badges.map((b) => b.className);
    expect(cls.some((c) => c.includes('kb-status-current'))).toBe(true);
    expect(cls.some((c) => c.includes('kb-status-draft'))).toBe(true);
  });

  it('健康区展示健康分（100）与卡片总数', async () => {
    render(<Knowledge />);

    await waitFor(() => {
      expect(screen.getByTestId('health-score')).toHaveTextContent('100');
    });
    expect(screen.getByText(/共 2 张卡片/)).toBeInTheDocument();
    // 五类问题计数均为 0
    expect(screen.getByText('未裁决矛盾：0')).toBeInTheDocument();
    expect(screen.getByText('断链：0')).toBeInTheDocument();
  });

  it('点击卡片打开详情抽屉，展示正文与出链', async () => {
    render(<Knowledge />);

    await waitFor(() => {
      expect(screen.getAllByText('RRF 融合检索').length).toBeGreaterThan(0);
    });

    fireEvent.click(screen.getAllByText('RRF 融合检索')[0]);

    const detail = await screen.findByTestId('card-detail');
    expect(detail).toBeInTheDocument();
    // 正文
    expect(within(detail).getByText('Reciprocal Rank Fusion 是知识库检索的核心算法。')).toBeInTheDocument();
    // 出链
    expect(within(detail).getByText('出链 (1)')).toBeInTheDocument();
    // bm25 出现在出链与入链（CARD_A.links 与 incoming_links 均含 bm25）
    expect(within(detail).getAllByText('bm25').length).toBeGreaterThan(0);
  });

  it('搜索交互：输入问题提交后展示命中结果与 [来源: slug|status] 标记', async () => {
    render(<Knowledge />);

    await waitFor(() => {
      expect(screen.getByPlaceholderText('输入问题，融合检索知识库...')).toBeInTheDocument();
    });

    const input = screen.getByPlaceholderText('输入问题，融合检索知识库...');
    fireEvent.change(input, { target: { value: 'rrf' } });
    fireEvent.click(screen.getByRole('button', { name: '检索' }));

    // [来源: slug|status] 标记（唯一文本，可证明搜索命中渲染）
    await waitFor(() => {
      expect(screen.getByText('[来源: rrf-fusion | current]')).toBeInTheDocument();
    });
  });
});
