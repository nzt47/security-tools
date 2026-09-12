/**
 * 开关中心测试（TASK-S7-01）
 * ------------------------------------------------------------------
 * 覆盖验收项（逐条对应任务书）：
 *   ① 分类分组 + 风险徽章 + **生效来源标签**（`source` + `source_label`）上屏
 *   ② `env` 锁定项：控件禁用 **且** `locked_reason` 以文本出现在行内
 *   ③ 机密项：只渲染后端脱敏串 + 已配置徽章；**没有明文输入框、没有 reveal 入口**
 *   ④ B 级：提交前必须出现 影响面/回滚方式/生效方式 确认块；提交后 202 ⇒ 待第二位
 *      人工确认（含 `pending_id`）并可走 `/confirm`
 *   ⑤ A 级开关：切换即以正确 key/value 调 `POST /api/cp/settings/<key>`
 *   ⑥ 搜索按 key/描述/分类过滤（防抖，用真实定时器 + `waitFor` 容忍）
 *
 * 纪律：**不使用假定时器**（仓库被时钟/时区用例坑过）；断言一律用正则容忍文案细节。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, fireEvent, waitFor } from '@testing-library/react'
import { GovernancePanels } from './index'
import { SettingsPanel } from './settings'

const api = vi.hoisted(() => ({
  fetchSettings: vi.fn(),
  changeSetting: vi.fn(),
  confirmSetting: vi.fn(),
  resetSetting: vi.fn(),
}))

vi.mock('@/lib/cpPanelsApi', () => api)

// ── 契约镜像夹具（字段名与 FROZEN 契约逐字一致）──────────────

type Item = Record<string, unknown>

const base = (over: Item): Item => ({
  key: 'K',
  category: 'orchestration_planning',
  category_label: '编排与规划',
  type: 'bool',
  risk: 'A',
  risk_label: 'A 可直接切',
  value: false,
  display_value: 'false',
  default: false,
  source: 'config',
  source_label: 'config.yaml',
  shadowed_by: [],
  locked: false,
  locked_reason: '',
  editable: true,
  effect: 'needs_restart',
  effect_label: '需重启容器后生效',
  needs_restart: true,
  env_name: 'K',
  env_present: false,
  config_path: 'k.enabled',
  env_only: false,
  secret: false,
  masked: false,
  configured: true,
  description: '样例开关',
  owner_module: 'agent/orchestrator/orchestrator.py',
  validator: { kind: 'bool' },
  requires_second_factor: false,
  requires_dual_approval: false,
  impact: '影响：聊天主链路是否走 PlanningCore 分支',
  rollback: '回滚：POST /api/cp/settings/K/reset',
  ...over,
})

/** A 级、config 生效来源 */
const A_ITEM = base({ key: 'PLANNING_WIRE_ENABLED', value: false, display_value: 'false' })

/** ★ env 锁定项（locked=true ⇒ editable=false，且必须显示 locked_reason） */
const LOCKED_ITEM = base({
  key: 'SAFETY_KERNEL_ENFORCE',
  value: true,
  display_value: 'true',
  source: 'env',
  source_label: '环境变量',
  locked: true,
  editable: false,
  locked_reason: '由环境变量 SAFETY_KERNEL_ENFORCE 锁定，只能改 .env 后重启容器',
  env_present: true,
  needs_restart: true,
})

/** ★ 机密项：value=null（永不返回明文）、display_value 已由后端脱敏 */
const SECRET_ITEM = base({
  key: 'OPENAI_API_KEY',
  type: 'str',
  risk: 'C',
  risk_label: 'C 只读脱敏',
  value: null,
  display_value: '已配置（sk-****abcd）',
  default: null,
  source: 'env',
  source_label: '环境变量',
  editable: false,
  secret: true,
  masked: true,
  configured: true,
  effect: 'hot',
  effect_label: '热生效',
  needs_restart: false,
  config_path: '',
  env_only: true,
  description: '模型调用密钥',
  impact: '',
  rollback: '',
})

/** ★ B 级：必须先确认（影响面/回滚方式/生效方式 + 二次认证） */
const B_ITEM = base({
  key: 'AUTONOMY_MAX_LEVEL',
  type: 'int',
  risk: 'B',
  risk_label: 'B 需二次确认',
  value: 2,
  display_value: '2',
  default: 1,
  source: 'default',
  source_label: '默认值',
  shadowed_by: ['ui_override'],
  effect: 'hot',
  effect_label: '热生效（下一轮循环读取）',
  needs_restart: false,
  requires_dual_approval: true,
  impact: '影响：自主体可执行的最大自治等级',
  rollback: '回滚：POST /api/cp/settings/AUTONOMY_MAX_LEVEL/reset',
  description: '自主体最大自治等级',
})

const VIEW = {
  ok: true,
  prefix: '/api/cp',
  generated_at: '2026-09-13T10:00:00+08:00',
  source_priority: ['env', 'ui_override', 'config', 'default'],
  counts: {
    total: 4,
    by_category: { self_healing_security: 1, orchestration_planning: 3 },
    by_risk: { A: 1, B: 1, C: 1 },
    editable: 2,
    locked: 1,
    overridden: 0,
  },
  categories: [
    { id: 'self_healing_security', label: '自愈与安全', count: 1 },
    { id: 'orchestration_planning', label: '编排与规划', count: 3 },
  ],
  items: [
    { ...LOCKED_ITEM, category: 'self_healing_security', category_label: '自愈与安全' },
    A_ITEM,
    B_ITEM,
    SECRET_ITEM,
  ],
  read_only_notice: 'C 级（密钥/凭据/路径）只读脱敏，永不返回明文',
}

beforeEach(() => {
  Object.values(api).forEach((fn) => fn.mockReset())
  api.fetchSettings.mockResolvedValue(VIEW)
  api.changeSetting.mockResolvedValue({
    ok: true, applied: true, key: A_ITEM.key, old: false, new: true,
    source: 'ui_override', effect: 'hot', effect_label: '热生效',
    audit: { seq: 12, self_hash: 'h' }, value: true, display_value: 'true',
  })
  api.resetSetting.mockResolvedValue({
    ok: true, reset: true, key: 'X', old: true, new: false,
    source: 'default', effect: 'hot', audit: { seq: 13, self_hash: 'h' },
  })
})

afterEach(cleanup)

/** 渲染面板（搜索防抖调到 10ms，避免等待；**仍走真实定时器**） */
function renderPanel() {
  return render(<SettingsPanel searchDebounceMs={10} />)
}

async function loaded() {
  await screen.findByText('开关中心')
  await screen.findByText(A_ITEM.key as string)
}

// ════════════════════════════════════════════════════════════

describe('1. 分组 / 风险徽章 / 生效来源', () => {
  it('按 payload 的 categories 分组渲染，带每类计数', async () => {
    renderPanel()
    await loaded()
    expect(screen.getByText('自愈与安全')).toBeInTheDocument()
    expect(screen.getByText('编排与规划')).toBeInTheDocument()
    // 每类计数取自 payload.items（筛选后）与 categories[].count（登记表）
    expect(screen.getByText(/3 项/)).toBeInTheDocument()
  })

  it('每行都有风险徽章（五态）与生效来源标签', async () => {
    renderPanel()
    await loaded()
    // 风险徽章文案来自后端 risk_label（芯片只显示 A/B/C，不另造措辞）
    expect(screen.getAllByText('A 可直接切').length).toBeGreaterThan(0)
    expect(screen.getByText('B 需二次确认')).toBeInTheDocument()
    expect(screen.getByText('C 只读脱敏')).toBeInTheDocument()
    // 五态：A ⇒ 绿(green)、B ⇒ 黄(yellow)、C ⇒ 灰(gray)
    const toneOfKey = (key: string) =>
      document
        .querySelector(`[data-cp-setting-key="${key}"] [data-cp-status-tone]`)
        ?.getAttribute('data-cp-status-tone')
    expect(toneOfKey(A_ITEM.key as string)).toBe('green')
    expect(toneOfKey(B_ITEM.key as string)).toBe('yellow')
    expect(toneOfKey(SECRET_ITEM.key as string)).toBe('gray')
    // 生效来源：契约的 source + source_label 都要上屏
    expect(screen.getByText(`来源：${A_ITEM.source_label}`)).toBeInTheDocument()
    expect(document.querySelector('[data-cp-setting-source="config"]')).not.toBeNull()
    expect(
      document.querySelector(`[data-cp-setting-key="${A_ITEM.key}"] [data-cp-setting-source="config"]`),
    ).not.toBeNull()
  })

  it('shadowed_by 非空 ⇒ 明说"被更高优先级来源覆盖"', async () => {
    renderPanel()
    await loaded()
    const note = document.querySelector(`[data-cp-shadowed-note="${B_ITEM.key}"]`)
    expect(note).not.toBeNull()
    // B 项目前 source=default，被 ui_override 覆盖
    expect(note?.textContent).toMatch(/当前被 .*覆盖/)
    expect(note?.textContent).toMatch(/ui_override/)
  })

  it('计数来自 payload.counts（缺位不臆造）', async () => {
    renderPanel()
    await loaded()
    const strip = document.querySelector('[data-cp-settings-counts="true"]')
    expect(strip?.textContent).toMatch(/登记总数/)
    expect(strip?.textContent).toMatch(/4/)
    expect(strip?.textContent).toMatch(/锁定/)
  })

  it('needs_restart / env_only 标记按契约字段渲染', async () => {
    renderPanel()
    await loaded()
    expect(document.querySelector(`[data-cp-needs-restart="${A_ITEM.key}"]`)).not.toBeNull()
    expect(document.querySelector(`[data-cp-env-only="${SECRET_ITEM.key}"]`)).not.toBeNull()
  })
})

describe('2. 锁定项（env 锁定）', () => {
  it('控件禁用，且 locked_reason 以文本显示在行内（不藏在 tooltip）', async () => {
    renderPanel()
    await loaded()
    const row = document.querySelector(`[data-cp-setting-key="${LOCKED_ITEM.key}"]`) as HTMLElement
    expect(row).not.toBeNull()
    expect(row.getAttribute('data-cp-setting-locked')).toBe('true')

    const control = row.querySelector('input[role="switch"]') as HTMLInputElement
    expect(control).not.toBeNull()
    expect(control).toBeDisabled()

    const reason = row.querySelector(`[data-cp-locked-reason="${LOCKED_ITEM.key}"]`)
    expect(reason).not.toBeNull()
    expect(reason?.textContent).toContain(LOCKED_ITEM.locked_reason as string)
  })

  it('锁定时该行没有任何提交入口（不是"看起来禁用"）', async () => {
    renderPanel()
    await loaded()
    const row = document.querySelector(`[data-cp-setting-key="${LOCKED_ITEM.key}"]`) as HTMLElement
    expect(row.querySelector('input[role="switch"]')).toBeDisabled()
    expect(row.querySelector('[data-cp-submit]')).toBeNull()
    expect(row.querySelector('[data-cp-open-confirm]')).toBeNull()
    expect(row.querySelector('[data-cp-reset]')).toBeNull()
    expect(api.changeSetting).not.toHaveBeenCalled()
  })
})

describe('3. 机密项（只读脱敏）', () => {
  it('渲染后端脱敏串 + 已配置徽章，且没有任何可输入控件', async () => {
    renderPanel()
    await loaded()
    const row = document.querySelector(`[data-cp-setting-key="${SECRET_ITEM.key}"]`) as HTMLElement
    expect(row.textContent).toContain('已配置（sk-****abcd）')
    expect(row.textContent).toMatch(/已配置/)
    expect(row.querySelector(`[data-cp-secret-value="${SECRET_ITEM.key}"]`)).not.toBeNull()
    // 没有输入框 / 开关 / reveal 按钮
    expect(row.querySelector('input')).toBeNull()
    expect(row.querySelector('button[data-cp-submit]')).toBeNull()
    expect(row.querySelector('[data-cp-reset]')).toBeNull()
  })

  it('页面级：机密项不产生任何输入控件，全页没有承载明文密钥的输入框', async () => {
    renderPanel()
    await loaded()
    const row = document.querySelector(`[data-cp-setting-key="${SECRET_ITEM.key}"]`) as HTMLElement
    expect(row.querySelectorAll('input, textarea, button')).toHaveLength(0)
    const leaked = Array.from(document.querySelectorAll('input')).some((el) =>
      (el as HTMLInputElement).value.includes('sk-'),
    )
    expect(leaked).toBe(false)
  })

  it('payload 的 read_only_notice 原样上屏', async () => {
    renderPanel()
    await loaded()
    expect(screen.getByText(VIEW.read_only_notice)).toBeInTheDocument()
  })
})

describe('4. B 级：提交前确认 + 202 待第二位人工', () => {
  it('未展开确认块时看不到影响面/回滚/生效方式；点"修改（需确认）"后才出现', async () => {
    renderPanel()
    await loaded()
    const row = document.querySelector(`[data-cp-setting-key="${B_ITEM.key}"]`) as HTMLElement
    expect(row.querySelector(`[data-cp-confirm-panel="${B_ITEM.key}"]`)).toBeNull()

    fireEvent.click(row.querySelector(`[data-cp-open-confirm="${B_ITEM.key}"]`) as HTMLElement)
    const panel = await waitFor(() => {
      const el = document.querySelector(`[data-cp-confirm-panel="${B_ITEM.key}"]`) as HTMLElement
      expect(el).not.toBeNull()
      return el
    })
    expect(panel.textContent).toMatch(/影响面/)
    expect(panel.textContent).toMatch(/回滚方式/)
    expect(panel.textContent).toMatch(/生效方式/)
    expect(panel.textContent).toContain(B_ITEM.impact as string)
    expect(panel.textContent).toContain(B_ITEM.effect_label as string)
    expect(panel.querySelector(`[data-cp-second-factor="${B_ITEM.key}"]`)).not.toBeNull()
  })

  it('B 级 switch 在确认前不可直接切换（避免绕过确认）', async () => {
    renderPanel()
    await loaded()
    const row = document.querySelector(`[data-cp-setting-key="${B_ITEM.key}"]`) as HTMLElement
    // int 类型没有 switch；改用 bool 型 B 项验证
    api.fetchSettings.mockResolvedValue({
      ...VIEW,
      items: [base({ key: 'B_BOOL', risk: 'B', risk_label: 'B 需二次确认', requires_dual_approval: true })],
    })
    cleanup()
    renderPanel()
    await screen.findByText('B_BOOL')
    const r2 = document.querySelector('[data-cp-setting-key="B_BOOL"]') as HTMLElement
    expect(r2.querySelector('input[role="switch"]')).toBeDisabled()
    expect(row).not.toBeNull()
  })

  it('提交确认块 ⇒ 调用 changeSetting；返回 202 ⇒ 显示待确认 + pending_id，可走 confirm', async () => {
    api.changeSetting.mockResolvedValue({
      ok: true, applied: false, pending: true, pending_id: 'setp-abc123',
      requires_dual_approval: true,
      message: 'B 级开关需第二位人工确认后方可生效',
    })
    api.confirmSetting.mockResolvedValue({
      ok: true, applied: true, key: B_ITEM.key, old: 2, new: 3,
      source: 'ui_override', effect: 'hot', effect_label: '热生效',
      audit: { seq: 21, self_hash: 'h' }, display_value: '3',
    })

    renderPanel()
    await loaded()
    const row = document.querySelector(`[data-cp-setting-key="${B_ITEM.key}"]`) as HTMLElement
    fireEvent.click(row.querySelector(`[data-cp-open-confirm="${B_ITEM.key}"]`) as HTMLElement)
    await screen.findByText(/影响面/)

    fireEvent.change(row.querySelector(`[data-cp-confirm-value="${B_ITEM.key}"]`) as HTMLElement, {
      target: { value: '3' },
    })
    fireEvent.change(row.querySelector(`[data-cp-second-factor="${B_ITEM.key}"]`) as HTMLElement, {
      target: { value: '123456' },
    })
    fireEvent.click(row.querySelector(`[data-cp-submit="${B_ITEM.key}"]`) as HTMLElement)

    await waitFor(() => expect(api.changeSetting).toHaveBeenCalledTimes(1))
    expect(api.changeSetting).toHaveBeenCalledWith(
      B_ITEM.key,
      expect.objectContaining({ value: 3, second_factor: '123456' }),
    )

    const pending = await screen.findByText('B 级开关需第二位人工确认后方可生效')
    expect(pending).toBeInTheDocument()
    expect(document.querySelector(`[data-cp-pending="${B_ITEM.key}"]`)).not.toBeNull()
    expect(document.querySelector(`[data-cp-pending="${B_ITEM.key}"]`)?.textContent)
      .toMatch(/setp-abc123/)

    // 第二位人工确认（用 202 返回的 pending_id）
    fireEvent.change(
      document.querySelector(`[data-cp-confirm-second-factor="${B_ITEM.key}"]`) as HTMLElement,
      { target: { value: '654321' } },
    )
    fireEvent.click(document.querySelector(`[data-cp-confirm-submit="${B_ITEM.key}"]`) as HTMLElement)
    await waitFor(() => expect(api.confirmSetting).toHaveBeenCalledTimes(1))
    expect(api.confirmSetting).toHaveBeenCalledWith(
      B_ITEM.key,
      expect.objectContaining({ pending_id: 'setp-abc123', second_factor: '654321' }),
    )
  })

  it('B 级被后端拒绝 ⇒ 原样显示 message（不改写措辞）', async () => {
    api.changeSetting.mockRejectedValue(
      Object.assign(new Error('HTTP 403'), {
        name: 'ApiError',
        code: 'second_factor_required',
        details: {
          ok: false,
          code: 'second_factor_required',
          message: '该操作需要二次认证码',
          decision: { allowed: false, reason: '缺少二次认证码' },
        },
      }),
    )
    renderPanel()
    await loaded()
    const row = document.querySelector(`[data-cp-setting-key="${B_ITEM.key}"]`) as HTMLElement
    fireEvent.click(row.querySelector(`[data-cp-open-confirm="${B_ITEM.key}"]`) as HTMLElement)
    await screen.findByText(/影响面/)
    fireEvent.click(row.querySelector(`[data-cp-submit="${B_ITEM.key}"]`) as HTMLElement)
    const err = await screen.findByText(/该操作需要二次认证码/)
    expect(err.textContent).toMatch(/second_factor_required/)
  })
})

describe('5. A 级开关直接提交', () => {
  it('切换开关 ⇒ POST /api/cp/settings/<key> 带正确的 key/value', async () => {
    renderPanel()
    await loaded()
    const row = document.querySelector(`[data-cp-setting-key="${A_ITEM.key}"]`) as HTMLElement
    const sw = row.querySelector('input[role="switch"]') as HTMLInputElement
    expect(sw).not.toBeDisabled()
    fireEvent.click(sw)

    await waitFor(() => expect(api.changeSetting).toHaveBeenCalledTimes(1))
    expect(api.changeSetting).toHaveBeenCalledWith(
      A_ITEM.key,
      expect.objectContaining({ value: true }),
    )
    // 生效回执
    expect(await screen.findByText(/已生效/, {}, { timeout: 3000 })).toBeInTheDocument()
  })

  it('ui_override 生效项提供"重置为默认"，走 /reset', async () => {
    const overridden = base({
      key: 'PLANNING_WIRE_ENABLED',
      source: 'ui_override',
      source_label: '界面覆盖',
      shadowed_by: ['config'],
      value: true,
      display_value: 'true',
    })
    api.fetchSettings.mockResolvedValue({ ...VIEW, items: [overridden] })
    renderPanel()
    await screen.findByText('PLANNING_WIRE_ENABLED')
    fireEvent.click(document.querySelector('[data-cp-reset="PLANNING_WIRE_ENABLED"]') as HTMLElement)
    await waitFor(() => expect(api.resetSetting).toHaveBeenCalledWith('PLANNING_WIRE_ENABLED'))
  })
})

describe('6. 搜索与筛选', () => {
  it('按 key/描述过滤（防抖后生效）', async () => {
    renderPanel()
    await loaded()
    expect(screen.getByText(SECRET_ITEM.key as string)).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('搜索开关'), { target: { value: 'OPENAI' } })
    await waitFor(() => {
      expect(screen.queryByText(A_ITEM.key as string)).toBeNull()
    })
    expect(screen.getByText(SECRET_ITEM.key as string)).toBeInTheDocument()
  })

  it('按分类中文名过滤', async () => {
    renderPanel()
    await loaded()
    fireEvent.change(screen.getByLabelText('搜索开关'), { target: { value: '自愈与安全' } })
    await waitFor(() => {
      expect(screen.queryByText(A_ITEM.key as string)).toBeNull()
    })
    expect(screen.getByText(LOCKED_ITEM.key as string)).toBeInTheDocument()
  })

  it('风险筛选芯片按 risk 过滤，计数来自 payload.counts.by_risk', async () => {
    renderPanel()
    await loaded()
    const chip = document.querySelector('[data-cp-risk-filter="C"]') as HTMLElement
    expect(chip.textContent).toMatch(/1/)
    fireEvent.click(chip)
    await waitFor(() => {
      expect(screen.queryByText(A_ITEM.key as string)).toBeNull()
    })
    expect(screen.getByText(SECRET_ITEM.key as string)).toBeInTheDocument()
  })

  it('无命中时给出空态（不渲染任何 0 占位数字）', async () => {
    renderPanel()
    await loaded()
    fireEvent.change(screen.getByLabelText('搜索开关'), { target: { value: 'zzz-not-exist' } })
    expect(await screen.findByText(/当前筛选下没有开关/)).toBeInTheDocument()
    expect(document.querySelector('[data-cp-clear-filters="true"]')).not.toBeNull()
  })
})

describe('8. 面板接线（GovernancePanelId = settings）', () => {
  it('GovernancePanels panel="settings" 真的渲染开关中心', async () => {
    render(<GovernancePanels panel="settings" />)
    expect(await screen.findByText('开关中心')).toBeInTheDocument()
    expect(api.fetchSettings).toHaveBeenCalled()
  })
})

describe('9. 大分组走虚拟滚动（§7 UI 五坑②：别全量实时渲染）', () => {
  it('单分类 >500 行 ⇒ VirtualList 生效，只挂载可视窗口内的行', async () => {
    const many = Array.from({ length: 520 }, (_, i) =>
      base({
        key: `BULK_${String(i).padStart(3, '0')}`,
        category: 'self_healing_security',
        category_label: '自愈与安全',
        description: `批量样例 ${i}`,
      }),
    )
    api.fetchSettings.mockResolvedValue({
      ...VIEW,
      counts: { ...VIEW.counts, total: 520, by_category: { self_healing_security: 520 } },
      categories: [{ id: 'self_healing_security', label: '自愈与安全', count: 520 }],
      items: many,
    })
    renderPanel()
    await screen.findByText('自愈与安全')
    const list = document.querySelector('[data-cp-virtualized="true"]')
    expect(list).not.toBeNull()
    expect(list?.getAttribute('data-cp-total')).toBe('520')
    // 只渲染窗口内的行（远小于 520）
    expect(document.querySelectorAll('[data-cp-setting-key]').length).toBeLessThan(60)
  })
})

describe('10. 实装形状兼容（service.ChangeOutcome.to_dict）', () => {
  it('生效回执没有顶层 display_value ⇒ 读 item.display_value 并标出来源', async () => {
    api.changeSetting.mockResolvedValue({
      ok: true, applied: true, pending: false, key: A_ITEM.key,
      old: false, new: true, source: 'ui_override', effect: 'hot',
      effect_label: '热生效', audit: { seq: 12, self_hash: 'h' },
      receipt: { reset: false, never_touched: ['.env', 'config.yaml'] },
      item: { ...A_ITEM, value: true, display_value: 'true', source: 'ui_override', source_label: '开关中心覆盖层' },
    })
    renderPanel()
    await loaded()
    const row = document.querySelector(`[data-cp-setting-key="${A_ITEM.key}"]`) as HTMLElement
    fireEvent.click(row.querySelector('input[role="switch"]') as HTMLInputElement)
    const applied = await screen.findByText(/已生效/, {}, { timeout: 3000 })
    expect(applied.getAttribute('data-cp-applied')).toBe(A_ITEM.key)
    expect(applied.textContent).toMatch(/ui_override/)
    expect(applied.textContent).toMatch(/热生效/)
  })

  it('重置成功用 receipt.reset=true 标记 ⇒ 渲染"已重置为默认"', async () => {
    const overridden = base({
      key: 'PLANNING_WIRE_ENABLED',
      source: 'ui_override',
      source_label: '开关中心覆盖层',
      shadowed_by: ['config'],
      value: true,
      display_value: 'true',
    })
    api.fetchSettings.mockResolvedValue({ ...VIEW, items: [overridden] })
    api.resetSetting.mockResolvedValue({
      ok: true, applied: true, pending: false, key: 'PLANNING_WIRE_ENABLED',
      old: true, new: false, source: 'default', effect: 'hot', effect_label: '热生效',
      audit: { seq: 13, self_hash: 'h' },
      receipt: { reset: true, detail: '覆盖层已清除', never_touched: ['.env', 'config.yaml'] },
      item: { ...overridden, value: false, display_value: 'false', source: 'default', source_label: '代码默认值' },
    })
    renderPanel()
    await screen.findByText('PLANNING_WIRE_ENABLED')
    fireEvent.click(document.querySelector('[data-cp-reset="PLANNING_WIRE_ENABLED"]') as HTMLElement)
    const done = await screen.findByText(/已重置为默认/)
    expect(done.getAttribute('data-cp-reset-done')).toBe('PLANNING_WIRE_ENABLED')
    expect(done.textContent).toMatch(/false/)
  })

  it('重置无覆盖层记录（applied=false + message）⇒ 原样显示后端说明', async () => {
    const overridden = base({
      key: 'PLANNING_WIRE_ENABLED',
      source: 'ui_override',
      source_label: '开关中心覆盖层',
      shadowed_by: ['config'],
    })
    api.fetchSettings.mockResolvedValue({ ...VIEW, items: [overridden] })
    api.resetSetting.mockResolvedValue({
      ok: true, applied: false, pending: false, key: 'PLANNING_WIRE_ENABLED',
      old: false, new: false, source: 'default', effect: 'hot', effect_label: '热生效',
      message: '该开关没有覆盖层记录，无需重置',
      receipt: { reset: false, detail: '无覆盖层记录' },
    })
    renderPanel()
    await screen.findByText('PLANNING_WIRE_ENABLED')
    fireEvent.click(document.querySelector('[data-cp-reset="PLANNING_WIRE_ENABLED"]') as HTMLElement)
    const noop = await screen.findByText('该开关没有覆盖层记录，无需重置')
    expect(noop.getAttribute('data-cp-noop')).toBe('PLANNING_WIRE_ENABLED')
  })

  it('来源优先级顺序来自 payload.source_priority（不自造）', async () => {
    renderPanel()
    await loaded()
    const legend = document.querySelector('[data-cp-source-priority="true"]')
    expect(legend?.textContent).toMatch(/env > ui_override > config > default/)
  })
})

describe('7. 加载 / 错误态沿用既有约定', () => {
  it('加载中显示"加载中…"', async () => {
    api.fetchSettings.mockImplementation(() => new Promise(() => {}))
    renderPanel()
    expect(screen.getByText(/加载中/)).toBeInTheDocument()
  })

  it('失败时显示后端错误文案（可重试）', async () => {
    api.fetchSettings.mockRejectedValue(new Error('HTTP 500'))
    renderPanel()
    expect(await screen.findByText(/HTTP 500/)).toBeInTheDocument()
    expect(screen.getByText(/刷新/)).toBeInTheDocument()
  })

  it('ok=false ⇒ 明确不可用（不渲染半套数据）', async () => {
    api.fetchSettings.mockResolvedValue({ ...VIEW, ok: false })
    renderPanel()
    expect(await screen.findByText(/开关登记表不可用/)).toBeInTheDocument()
  })
})
