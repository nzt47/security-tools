/**
 * prompt-lab 子组件单元测试
 * - FactorControl：四控件渲染与值回调
 * - FactorCard：自定义因素删除按钮
 * - RadarChart：SVG 渲染与 aria-label
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup } from '@testing-library/react'
import FactorControl from './FactorControl'
import FactorCard from './FactorCard'
import RadarChart from './RadarChart'
import type { PromptFactorDef } from '../../lib/promptFactorTypes'

afterEach(cleanup)

describe('FactorControl', () => {
  it('slider 渲染并回调数值', () => {
    const onChange = vi.fn()
    const def: PromptFactorDef = {
      id: 't',
      category: 'model',
      name: '温度',
      desc: '',
      control: 'slider',
      defaultValue: 0.5,
      min: 0,
      max: 1,
      step: 0.1,
    }
    render(<FactorControl def={def} value={0.5} onChange={onChange} />)
    const slider = screen.getByRole('slider', { name: '温度' })
    fireEvent.change(slider, { target: { value: '0.8' } })
    expect(onChange).toHaveBeenCalledWith(0.8)
  })

  it('select 渲染并回调选项值', () => {
    const onChange = vi.fn()
    const def: PromptFactorDef = {
      id: 's',
      category: 'language',
      name: '语气',
      desc: '',
      control: 'select',
      defaultValue: 'formal',
      options: [
        { value: 'formal', label: '正式' },
        { value: 'casual', label: '随意' },
      ],
    }
    render(<FactorControl def={def} value="formal" onChange={onChange} />)
    fireEvent.change(screen.getByRole('combobox', { name: '语气' }), { target: { value: 'casual' } })
    expect(onChange).toHaveBeenCalledWith('casual')
  })

  it('toggle 渲染并切换布尔值', () => {
    const onChange = vi.fn()
    const def: PromptFactorDef = {
      id: 'tg',
      category: 'context',
      name: '联网搜索',
      desc: '',
      control: 'toggle',
      defaultValue: false,
    }
    render(<FactorControl def={def} value={false} onChange={onChange} />)
    // toggle 按钮可访问名为内容文案（关闭/开启），点击后回调 true
    fireEvent.click(screen.getByRole('button', { name: '关闭' }))
    expect(onChange).toHaveBeenCalledWith(true)
  })
})

describe('FactorCard', () => {
  it('自定义因素显示删除按钮，点击触发 onRemove', () => {
    const onRemove = vi.fn()
    const def: PromptFactorDef = {
      id: 'c1',
      category: 'structure',
      name: '自定义',
      desc: '测试',
      control: 'text',
      defaultValue: '',
      custom: true,
    }
    render(<FactorCard def={def} value="" onChange={vi.fn()} onRemove={onRemove} />)
    fireEvent.click(screen.getByTitle('删除此自定义因素'))
    expect(onRemove).toHaveBeenCalledWith('c1')
  })
})

describe('RadarChart', () => {
  it('渲染 SVG 与 aria-label', () => {
    render(<RadarChart data={[{ label: '清晰', value: 80 }]} />)
    expect(screen.getByRole('img', { name: '效果评估雷达图' })).toBeInTheDocument()
    expect(screen.getByText('清晰')).toBeInTheDocument()
  })
})
