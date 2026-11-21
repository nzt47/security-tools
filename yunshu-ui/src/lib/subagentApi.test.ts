/**
 * 角色热更新请求体守卫（2026-11-22）
 * ------------------------------------------------------------------
 * `buildRoleUpdatePayload` 是「编辑已有分身的角色」的**唯一**序列化口径。
 * 它要同时满足两件相反的事：
 *   · 正文不回显 ⇒ 留空必须**不发键**（后端沿用现值，不能把看不见的正文删掉）；
 *   · 但切回 template 档 / 选了不装角色 / 人显式勾"清空" ⇒ 必须**发空串**，
 *     否则这三条路永远切不回去（默认档配非空正文后端 400）。
 * 下面每条断言对应其中一条分支 —— 改坏任何一条都会在 vitest 里红。
 */
import { describe, expect, it } from 'vitest'
import { buildRoleUpdatePayload } from './subagentApi'

describe('buildRoleUpdatePayload', () => {
  it('输入正文 ⇒ 发 role_text（替换），档位原样', () => {
    expect(buildRoleUpdatePayload({ template: 'code_review', mode: 'template+text', text: ' 严格审查 ', clearText: false }))
      .toEqual({ role_template: 'code_review', role_mode: 'template+text', role_text: '严格审查' })
  })

  it('正文留空 + 非默认档 ⇒ 不发 role_text（保留现有正文）', () => {
    expect(buildRoleUpdatePayload({ template: 'code_review', mode: 'template+text', text: '   ', clearText: false }))
      .toEqual({ role_template: 'code_review', role_mode: 'template+text' })
    expect(buildRoleUpdatePayload({ template: 'code_review', mode: 'full-system', text: '', clearText: false }))
      .toEqual({ role_template: 'code_review', role_mode: 'full-system' })
  })

  it('切回 template 档 ⇒ 显式发空串（否则默认档配旧正文会被后端 400）', () => {
    expect(buildRoleUpdatePayload({ template: 'code_review', mode: 'template', text: '', clearText: false }))
      .toEqual({ role_template: 'code_review', role_mode: 'template', role_text: '' })
  })

  it('显式勾"清空" ⇒ 发空串', () => {
    expect(buildRoleUpdatePayload({ template: 'code_review', mode: 'full-system', text: '', clearText: true }))
      .toEqual({ role_template: 'code_review', role_mode: 'full-system', role_text: '' })
  })

  it('未装角色 ⇒ 模板空串 + 档位归一为 template + 正文空串（不留 full-system 的空角色）', () => {
    expect(buildRoleUpdatePayload({ template: '  ', mode: 'full-system', text: '', clearText: false }))
      .toEqual({ role_template: '', role_mode: 'template', role_text: '' })
  })

  it('正文有内容时"清空"标记不生效（以人最后输入为准）', () => {
    expect(buildRoleUpdatePayload({ template: 'research', mode: 'full-system', text: '新正文', clearText: true }))
      .toEqual({ role_template: 'research', role_mode: 'full-system', role_text: '新正文' })
  })
})
