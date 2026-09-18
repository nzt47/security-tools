/**
 * 可调用性标注展示层测试（工具 / 技能「可被 LLM 调用」三档标识）
 * ------------------------------------------------------------------
 * 覆盖验收项：
 *   ① 徽章文案直接取后端 `mark`，不重算判定；
 *   ② 悬浮说明组装 reason / conditions / notes / permission_level / host_executor，
 *      **缺失项不留空行**（空行会在 title 里显示成空白段）；
 *   ③ `callability` 为 `{}` / `mark` 缺失 ⇒ 徽章**不渲染**（退化为现状，不报错）；
 *   ④ 图例文案优先用后端 `callability_note`，取不到用本地兜底常量。
 */

import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import {
  CALLABILITY_LEGEND_FALLBACK,
  MARK_BLOCKED,
  MARK_CALLABLE,
  MARK_CONDITIONAL,
  callabilityColor,
  callabilityCounts,
  callabilityLegend,
  callabilityMark,
  callabilityTitle,
  hasCallabilityMark,
  indexCallability,
  type CallabilityInfo,
} from './callability'
import { CallabilityBadge } from '../pages/hub/components/ui'

/** 一条「条件可调用」的工具标注（字段取自 data/capability_manifest.json 的真实样本） */
const CONDITIONAL: CallabilityInfo = {
  tool_name: 'connect_mcp',
  tool_type: 'tool',
  llm_callable: true,
  callable_mode: 'auto',
  schema_registered: true,
  host_executor: 'agent.tools.ext_tools:_connect_mcp',
  permission_level: 'restricted',
  sandbox_allowed: false,
  reason: '',
  mark: MARK_CONDITIONAL,
  reachable: true,
  trigger: 'model',
  reason_kind: 'needs_approval',
  blockers: [],
  soft_blockers: [],
  conditions: ['属审批边界（govern 平面 / extend 效果 / critical 风险）：调用会挂审批单，需人工确认后原样重试'],
  notes: ['sandbox_allowed=false：不允许在沙箱（受限会话）中执行'],
  declared_in: 'data/tool_definitions/connect_mcp.yaml',
}

describe('callabilityMark / hasCallabilityMark', () => {
  it('mark 缺失或 callability 为 {} ⇒ 空串（调用方据此不渲染徽章）', () => {
    expect(callabilityMark(undefined)).toBe('')
    expect(callabilityMark(null)).toBe('')
    expect(callabilityMark({})).toBe('')
    expect(callabilityMark({ tool_name: 'x', llm_callable: true })).toBe('')
    expect(hasCallabilityMark({})).toBe(false)
  })

  it('文案原样取自后端，不做二次判定', () => {
    expect(callabilityMark({ mark: ` ${MARK_BLOCKED} ` })).toBe(MARK_BLOCKED)
    expect(hasCallabilityMark({ mark: MARK_CALLABLE })).toBe(true)
  })
})

describe('callabilityColor', () => {
  it('三档标识映射到既有配色习惯', () => {
    expect(callabilityColor(MARK_CALLABLE)).toBe('green')
    expect(callabilityColor(MARK_CONDITIONAL)).toBe('amber')
    expect(callabilityColor(MARK_BLOCKED)).toBe('red')
  })

  it('按语义匹配，不依赖不可见的变体选择符（U+FE0F）', () => {
    expect(callabilityColor('⚠ 条件可调用')).toBe('amber')
    expect(callabilityColor('未知标识')).toBe('slate')
    expect(callabilityColor(undefined)).toBe('slate')
  })
})

describe('callabilityTitle', () => {
  it('多行组装触发方式/权限/调用模式/条件/说明/执行器/声明，且无空行', () => {
    const title = callabilityTitle(CONDITIONAL, 'connect_mcp')
    const lines = title.split('\n')
    expect(lines[0]).toBe(`${MARK_CONDITIONAL} · connect_mcp`)
    expect(lines).toContain('触发方式：模型发起')
    expect(lines).toContain('权限 受限（restricted） · 调用模式 模型自主')
    expect(lines).toContain(`条件：${CONDITIONAL.conditions![0]}`)
    expect(lines).toContain(`说明：${CONDITIONAL.notes![0]}`)
    expect(lines).toContain('执行器：agent.tools.ext_tools:_connect_mcp')
    expect(lines).toContain('声明：data/tool_definitions/connect_mcp.yaml')
    // 空 reason 不产生“原因：”行；整串不含空行
    expect(lines.some((l) => l.startsWith('原因：'))).toBe(false)
    expect(lines.every((l) => l.trim() !== '')).toBe(true)
  })

  it('不可达条目给出 reason，缺省字段不占行', () => {
    const title = callabilityTitle({ mark: MARK_BLOCKED, llm_callable: false, reason: '无 JSON Schema' }, 'x')
    expect(title).toBe(`${MARK_BLOCKED} · x\n不可调用原因：无 JSON Schema`)
  })

  it('非模型发起（技能这类）用"触发说明"而非"不可调用原因"', () => {
    // 这是本次语义修正的关键：⚠️ 的技能是"可达但不由模型发起"，不是"坏了"
    const title = callabilityTitle({
      mark: MARK_CONDITIONAL,
      llm_callable: false,
      callable_mode: 'manual',
      trigger: 'system',
      reason_kind: 'not_model_initiated',
      reason: '纯提示词技能：由 ContextInjector 按意图注入，模型不发起调用',
    }, 'context_aware')
    const lines = title.split('\n')
    expect(lines[0]).toBe(`${MARK_CONDITIONAL} · context_aware`)
    expect(lines).toContain('触发方式：系统 / 宿主触发（非模型发起）')
    expect(lines).toContain('调用模式 仅人工/系统')
    expect(lines.some((l) => l.startsWith('触发说明：纯提示词技能'))).toBe(true)
    expect(lines.some((l) => l.startsWith('不可调用原因：'))).toBe(false)
  })

  it('不可达条目用"不可调用原因"并标出"无（不可达）"', () => {
    const title = callabilityTitle({
      mark: MARK_BLOCKED, llm_callable: false, trigger: 'none',
      reason_kind: 'unreachable', reason: '无执行器（宿主注册点里找不到执行入口）',
    }, 'ghost')
    expect(title).toContain('触发方式：无（不可达）')
    expect(title).toContain('不可调用原因：无执行器（宿主注册点里找不到执行入口）')
  })

  it('无 info ⇒ 空串（不生成空 title）', () => {
    expect(callabilityTitle(undefined)).toBe('')
    expect(callabilityTitle({})).toBe('')
  })
})

describe('callabilityLegend / callabilityCounts', () => {
  it('图例优先用后端文案，缺失回落到本地常量', () => {
    expect(callabilityLegend('后端文案')).toBe('后端文案')
    expect(callabilityLegend('')).toBe(CALLABILITY_LEGEND_FALLBACK)
    expect(callabilityLegend(undefined)).toBe(CALLABILITY_LEGEND_FALLBACK)
  })

  it('计数按固定顺序（✅ → ⚠️ → ❌），空表为空串', () => {
    expect(callabilityCounts({ [MARK_BLOCKED]: 1, [MARK_CALLABLE]: 80, [MARK_CONDITIONAL]: 10 }))
      .toBe(`${MARK_CALLABLE} 80 · ${MARK_CONDITIONAL} 10 · ${MARK_BLOCKED} 1`)
    expect(callabilityCounts({})).toBe('')
    expect(callabilityCounts(null)).toBe('')
  })
})

describe('indexCallability（仓库口径 + 运行时口径合并）', () => {
  it('多批合并，同名以后出现的为准（仓库口径覆盖运行时口径）', () => {
    const runtime = [{ tool_name: 'skill', mark: '⚠️ 条件可调用', scope: 'runtime' }]
    const repo = [{ tool_name: 'scripted-selftest', mark: '⚠️ 条件可调用', scope: 'repo' }]
    const map = indexCallability(runtime, repo)
    expect(Object.keys(map).sort()).toEqual(['scripted-selftest', 'skill'])
    expect(map['skill'].scope).toBe('runtime')
  })

  it('同名冲突时后者胜（清单文件是权威）', () => {
    const map = indexCallability(
      [{ tool_name: 'x', mark: '❌ 不可调用', scope: 'runtime' }],
      [{ tool_name: 'x', mark: '⚠️ 条件可调用', scope: 'repo' }],
    )
    expect(map['x'].mark).toBe('⚠️ 条件可调用')
    expect(map['x'].scope).toBe('repo')
  })

  it('缺失/空批不报错，且跳过没有 tool_name 的条目', () => {
    expect(indexCallability(undefined, null, [])).toEqual({})
    expect(indexCallability([{ mark: '⚠️ 条件可调用' }])).toEqual({})
  })
})

describe('CallabilityBadge（退化行为）', () => {
  it('mark 缺失 ⇒ 不渲染任何节点', () => {
    const { container } = render(<CallabilityBadge info={{}} name="x" />)
    expect(container.innerHTML).toBe('')
  })

  it('info 缺失 ⇒ 不渲染任何节点', () => {
    const { container } = render(<CallabilityBadge />)
    expect(container.innerHTML).toBe('')
  })

  it('有 mark ⇒ 渲染文案与多行悬浮说明', () => {
    const { container, getByText } = render(<CallabilityBadge info={CONDITIONAL} name="connect_mcp" />)
    const el = getByText(MARK_CONDITIONAL)
    expect(el.getAttribute('title')).toContain('执行器：agent.tools.ext_tools:_connect_mcp')
    // 颜色沿用既有配色习惯（amber = 审批边界）
    expect(container.querySelector('.text-amber-400')).not.toBeNull()
  })

  it('compact ⇒ 只渲染字形，但悬浮说明仍是完整多行文案', () => {
    // 装配预览的工具 chip 一行里已有 name + plane，整句标识会把 chip 撑长
    const { container, getByText } = render(
      <CallabilityBadge info={CONDITIONAL} name="connect_mcp" compact />,
    )
    const el = getByText('⚠️')
    expect(el.textContent).toBe('⚠️')
    expect(el.getAttribute('title')).toBe(callabilityTitle(CONDITIONAL, 'connect_mcp'))
    expect(container.textContent).not.toContain('条件可调用')
  })

  it('compact + mark 缺失 ⇒ 依然不渲染任何节点', () => {
    const { container } = render(<CallabilityBadge info={{}} name="x" compact />)
    expect(container.innerHTML).toBe('')
  })
})
