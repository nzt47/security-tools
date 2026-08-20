/**
 * 登录页流程测试（含 mock 数据）
 * ------------------------------------------------------
 * 验证登录核心链路（mock 数据与 src/mocks/devMock.ts 对齐：admin/123456）：
 *   1. 成功：token 双写（localStorage + store）、携带 state.from 时跳回来源路径
 *   2. 成功（无来源）：默认跳转首页 /
 *   3. 失败（错误凭据）：不写 token、不跳转（错误提示由全局 axios 拦截器统一 toast）
 *   4. 空表单校验：未发请求，直接通过全局 Toast 提示
 * 说明：login 接口用 vi.mock 替换，不依赖 dev server 的 mock 中间件。
 */
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { webcrypto } from 'node:crypto'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { login } from '@/api/user'
import { getToken } from '@/utils/request'
import { useUserStore } from '@/store/userStore'
import Toaster from '@/components/Toaster'
import LoginPage from './index'

// mock 数据：与 devMock.ts 的 MOCK_USER / mock-token 格式一致
const MOCK_TOKEN = 'mock-token-test-123456'
const MOCK_USER = {
  id: 1,
  username: 'admin',
  nickname: '本地管理员',
  email: 'admin@yunshu.local',
}

// mock 登录接口（userStore 依赖的 getUserInfo 一并提供，避免模块加载报错）
vi.mock('@/api/user', () => ({
  login: vi.fn(),
  getUserInfo: vi.fn(),
}))

const mockLogin = vi.mocked(login)

/** 渲染登录页 + 全局 Toaster（Toast 提示由 Toaster 渲染，供断言）；传入 from 时模拟"守卫重定向携带的来源路径" */
function renderLogin(from?: string) {
  const initialEntries = from
    ? [{ pathname: '/login', state: { from: { pathname: from } } }]
    : ['/login']
  return render(
    <>
      <MemoryRouter initialEntries={initialEntries}>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/" element={<div>HOME_PAGE</div>} />
          <Route path="/workbench" element={<div>WORKBENCH_PAGE</div>} />
        </Routes>
      </MemoryRouter>
      <Toaster />
    </>,
  )
}

/** 填写表单并提交；提交后冲刷异步状态更新（login mock 的 resolve/reject 在微任务中触发 setState） */
async function submitLogin(username: string, password: string) {
  fireEvent.change(screen.getByLabelText('用户名'), { target: { value: username } })
  fireEvent.change(screen.getByLabelText('密码'), { target: { value: password } })
  fireEvent.click(screen.getByRole('button', { name: '登录' }))
  await act(async () => {})
}

beforeEach(() => {
  localStorage.clear()
  useUserStore.setState({ token: null, userInfo: null })
  mockLogin.mockReset()
})

// 【Why】jsdom 的 window.crypto 无 subtle 且为访问器属性，defineProperty 强制替换为 Node Web Crypto，
// 以便测试 AES-GCM 加密保存（登录页在函数内运行时读取全局 crypto，不影响模块加载）
beforeAll(() => {
  Object.defineProperty(globalThis, 'crypto', { value: webcrypto, configurable: true, writable: true })
})

afterAll(() => {
  vi.unstubAllGlobals()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('登录页', () => {
  it('登录成功：token 双写，携带来源路径时跳回来源', async () => {
    const infoSpy = vi.spyOn(console, 'info').mockImplementation(() => {})
    mockLogin.mockResolvedValue({ token: MOCK_TOKEN, user: MOCK_USER })
    renderLogin('/workbench')

    await submitLogin('admin', '123456')

    // 跳回来源路径 /workbench
    expect(await screen.findByText('WORKBENCH_PAGE')).toBeInTheDocument()

    // token 双写：localStorage（守卫/拦截器读取） + store（全局状态）
    expect(getToken()).toBe(MOCK_TOKEN)
    expect(useUserStore.getState().token).toBe(MOCK_TOKEN)
    expect(useUserStore.getState().userInfo?.nickname).toBe('本地管理员')
    // 登录日志埋点
    expect(infoSpy).toHaveBeenCalledWith(expect.stringContaining('[auth] 登录成功'))
  })

  it('登录成功且无来源路径：默认跳转首页 /', async () => {
    mockLogin.mockResolvedValue({ token: MOCK_TOKEN, user: MOCK_USER })
    renderLogin()

    await submitLogin('admin', '123456')

    expect(await screen.findByText('HOME_PAGE')).toBeInTheDocument()
  })

  it('登录失败：不写 token、不跳转（错误提示由全局 axios 拦截器统一 toast）', async () => {
    mockLogin.mockRejectedValue(new Error('用户名或密码错误'))
    renderLogin()

    await submitLogin('admin', 'wrong-password')

    expect(getToken()).toBeNull()
    expect(useUserStore.getState().token).toBeNull()
    // 仍在登录页（按钮存在，未发生跳转）
    expect(screen.getByRole('button', { name: '登录' })).toBeInTheDocument()
  })

  it('空表单校验：未提交接口，直接通过全局 Toast 提示', async () => {
    renderLogin()
    await submitLogin('', '')
    expect(await screen.findByRole('alert')).toHaveTextContent('请输入用户名和密码')
    expect(mockLogin).not.toHaveBeenCalled()
  })

  it('记住密码：登录成功后密码以 AES-GCM 密文保存，不落明文', async () => {
    mockLogin.mockResolvedValue({ token: MOCK_TOKEN, user: MOCK_USER })
    renderLogin()

    fireEvent.click(screen.getByLabelText('记住密码'))
    await submitLogin('admin', '123456')

    await screen.findByText('HOME_PAGE')
    const stored = JSON.parse(localStorage.getItem('yunshu-remember-login') ?? '{}')
    expect(stored.remember).toBe(true)
    expect(stored.username).toBe('admin')
    expect(stored.passwordCipher).toBeTruthy()
    // 密文中不应出现明文密码
    expect(JSON.stringify(stored)).not.toContain('123456')
  })

  it('未勾选记住密码：登录成功后不保存任何凭证', async () => {
    mockLogin.mockResolvedValue({ token: MOCK_TOKEN, user: MOCK_USER })
    renderLogin()

    await submitLogin('admin', '123456')

    await screen.findByText('HOME_PAGE')
    expect(localStorage.getItem('yunshu-remember-login')).toBeNull()
    expect(localStorage.getItem('yunshu-remember-key')).toBeNull()
  })

  it('记住密码：密钥丢失或密文损坏时仅回填用户名，不回填密码（容错降级）', async () => {
    // 模拟历史保存的密文，但密文为非法 base64（等价于密钥丢失/数据损坏）
    localStorage.setItem(
      'yunshu-remember-login',
      JSON.stringify({ remember: true, username: 'admin', passwordCipher: '!invalid-base64!' }),
    )
    renderLogin()

    // 异步解密失败 → 仅回填用户名，密码保持为空
    expect(await screen.findByDisplayValue('admin')).toBeInTheDocument()
    expect(screen.getByLabelText('密码')).toHaveValue('')
  })
})
