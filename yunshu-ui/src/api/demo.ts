/**
 * Demo 页模拟接口
 * - 仅本地 mock 生效（VITE_MOCK_API=true 时由 devMock 插件拦截，后端无此真实接口）
 * - 用于演示 Input 错误提示 + 网络异常兜底
 */
import request from '@/utils/request'

/** 邮箱校验结果 */
export interface EmailCheckResult {
  valid: boolean
}

/**
 * 校验邮箱（走模拟接口，便于测试网络异常场景）
 * - timeout: 3s 覆盖全局 15s 超时，配合 mock 的 timeout 场景（10s 不响应）快速触发超时
 */
export function validateEmail(email: string): Promise<EmailCheckResult> {
  return request<EmailCheckResult>({
    url: '/demo/validate-email',
    method: 'GET',
    params: { email },
    timeout: 3000,
  })
}
