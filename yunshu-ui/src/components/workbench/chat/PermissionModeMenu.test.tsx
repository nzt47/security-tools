/**
 * PermissionModeMenu 测试 —— 「权限档位」四档（对齐 DSH）
 * ------------------------------------------------------------------
 * 钉住：默认「自动审查」；展开可见四档；选择写回 useChatPrefsStore（随请求体发后端）。
 */
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { PermissionModeMenu } from './PermissionModeMenu'
import { useChatPrefsStore } from '../../../stores/useChatPrefsStore'

afterEach(() => {
  cleanup()
  useChatPrefsStore.setState({ permissionMode: 'auto' })
})

describe('PermissionModeMenu · 权限档位四档', () => {
  it('默认自动审查，展开可选四档并写回 store', () => {
    render(<PermissionModeMenu />)
    expect(screen.getByText('权限：自动审查')).toBeTruthy()

    fireEvent.click(screen.getByTestId('permission-mode-menu'))
    for (const m of ['read_only', 'workspace', 'auto', 'full']) {
      expect(screen.getByTestId(`permission-mode-${m}`)).toBeTruthy()
    }

    fireEvent.click(screen.getByTestId('permission-mode-full'))
    expect(useChatPrefsStore.getState().permissionMode).toBe('full')
  })

  it('选择后按钮文案随之更新', () => {
    render(<PermissionModeMenu />)
    fireEvent.click(screen.getByTestId('permission-mode-menu'))
    fireEvent.click(screen.getByTestId('permission-mode-read_only'))
    expect(screen.getByText('权限：仅可查看')).toBeTruthy()
  })
})
