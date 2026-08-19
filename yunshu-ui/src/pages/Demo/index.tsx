/**
 * DemoPage —— 基础组件展示页（Button / Input / Card + ThemeToggle）
 * 目的：预览语义 Token 在深浅双模式下的视觉效果；新组件接入前可先在此验证。
 */
import { useState } from 'react'
import { Button, Card, Input, ThemeToggle } from '@/components/ui'
import { validateEmail } from '@/api/demo'

export default function DemoPage() {
  const [username, setUsername] = useState('')
  const [submitting, setSubmitting] = useState(false)

  const [email, setEmail] = useState('')
  const [emailError, setEmailError] = useState('')
  const [checkingEmail, setCheckingEmail] = useState(false)

  /** 把异常映射为可读文案，便于测试网络异常场景 */
  function toErrorMessage(err: unknown): string {
    const status = (err as { response?: { status?: number } } | null)?.response?.status
    if (status === 500) return '服务异常（模拟 HTTP 500）'
    const msg = err instanceof Error ? err.message : ''
    if (msg.includes('Network Error')) return '网络异常：无法连接接口服务'
    return msg || '邮箱校验失败'
  }

  /** 失焦调用模拟接口校验邮箱（覆盖：业务错误 / HTTP 500 / 慢响应 / 断网） */
  async function handleEmailBlur() {
    const value = email.trim()
    if (!value) {
      setEmailError('')
      return
    }
    setCheckingEmail(true)
    try {
      await validateEmail(value)
      setEmailError('')
    } catch (err) {
      setEmailError(toErrorMessage(err))
    } finally {
      setCheckingEmail(false)
    }
  }

  return (
    <div className="mx-auto max-w-3xl space-y-6 p-6">
      {/* 头部：标题 + 主题切换 */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-foreground">基础组件演示</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            所有颜色取自语义 Token，点击右上角图标切换深浅模式。
          </p>
        </div>
        <ThemeToggle />
      </div>

      {/* Button：variant */}
      <Card className="p-5">
        <h2 className="text-sm font-medium text-muted-foreground">Button · variant</h2>
        <div className="mt-4 flex flex-wrap gap-3">
          <Button variant="primary">主操作</Button>
          <Button variant="default">次级操作</Button>
          <Button variant="danger">危险操作</Button>
          <Button variant="ghost">幽灵按钮</Button>
        </div>
      </Card>

      {/* Button：size / loading / disabled */}
      <Card className="p-5">
        <h2 className="text-sm font-medium text-muted-foreground">Button · size / loading / disabled</h2>
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <Button size="sm" variant="primary">小号</Button>
          <Button size="md" variant="primary">中号</Button>
          <Button loading>加载中</Button>
          <Button disabled>禁用</Button>
        </div>
      </Card>

      {/* Input：label / error / disabled */}
      <Card className="space-y-4 p-5">
        <h2 className="text-sm font-medium text-muted-foreground">Input · label / error</h2>
        <Input
          label="用户名"
          placeholder="请输入用户名"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
        />
        <div>
          <Input
            label="邮箱"
            placeholder="name@example.com"
            value={email}
            error={emailError}
            loading={checkingEmail}
            onChange={(e) => {
              setEmail(e.target.value)
              if (emailError) setEmailError('')
            }}
            onBlur={handleEmailBlur}
          />
          <p className="mt-1 text-xs text-muted-foreground">
            失焦触发模拟接口校验：不含 @ 业务报错；含 network 返回 HTTP 500；含 timeout 触发网络超时（3s
            中止）；slow 开头延迟 3s（观察右侧加载图标）。
          </p>
        </div>
        <Input label="禁用状态" placeholder="不可编辑" disabled />
      </Card>

      {/* Card 容器 + 提交交互 */}
      <Card className="p-5">
        <h2 className="text-sm font-medium text-muted-foreground">Card · shadow-card</h2>
        <p className="mt-2 text-sm text-foreground">
          统一 rounded-lg + border-border + shadow-card，深浅模式自动跟随 Token。
        </p>
        <div className="mt-4 flex justify-end">
          <Button
            variant="primary"
            loading={submitting}
            onClick={() => {
              setSubmitting(true)
              window.setTimeout(() => setSubmitting(false), 1500)
            }}
          >
            模拟提交
          </Button>
        </div>
      </Card>
    </div>
  )
}
