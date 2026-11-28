/**
 * serviceVersion —— 服务版本读数（/api/version）与「页面是否落后」判定
 * ------------------------------------------------------------------
 * 后端早已返回 commit / build_commit / bundle（见 agent/build_info.py），
 * 但前端此前一处都没消费：部署后仍开着旧标签页时，页面跑旧 bundle、打新后端，
 * 表现为「改了没用 / 行为诡异」。本模块只做两件事：
 *   1. 取服务版本（只读 GET，无新路由）；
 *   2. 用**页面自己加载的 bundle 名**与后端当前 bundle 比较 ⇒ 是否该提示刷新。
 *
 * 【为什么不比 commit】bundle 名是页面真正执行的代码身份；只换后端不换前端时
 * commit 会变但页面无需刷新，拿 commit 比会误报。
 * 【为什么开发模式不提示】dev 下页面脚本是 /src/main.tsx，没有 index-*.js，
 * pageBundle() 返回 null ⇒ 全部判定降级为「不提示」，不打扰本地开发。
 */
import { request } from '@/lib/apiClient'

/** 后端读数的形状（只声明前端用到的字段；其余字段允许存在） */
export interface BackendInfo {
  commit?: string | null
  commit_subject?: string | null
  committed_at?: string | null
  dirty?: boolean
  dirty_files?: string[]
  error?: string | null
}

export interface FrontendInfo {
  bundle?: string | null
  build_commit?: string | null
  built_at?: string | null
  built_at_source?: string | null
  index_html_present?: boolean
  error?: string | null
}

export interface ProcessInfo {
  pid?: number
  python?: string
  started_at?: string | null
  cwd?: string
}

export interface ServiceVersion {
  backend: BackendInfo
  frontend: FrontendInfo
  process: ProcessInfo
}

/** 轮询间隔：5 分钟（部署不频繁，够用且不吵） */
export const VERSION_POLL_MS = 5 * 60 * 1000

/** 读取服务版本；非 2xx / 信封缺 data 时抛错，由调用方决定怎么降级 */
export async function fetchServiceVersion(): Promise<ServiceVersion> {
  const payload = await request<{ code?: number; data?: ServiceVersion }>('/api/version')
  if (!payload || typeof payload !== 'object' || !payload.data) {
    throw new Error('版本接口返回为空')
  }
  return payload.data
}

/**
 * 页面当前执行的主 bundle 名（如 index-CmCuhlu3.js）。
 * 生产构建由 templates/yunshu.html 以 <script type="module" src="/static/assets/index-*.js"> 引入；
 * dev / 测试环境没有这个标签 ⇒ 返回 null，调用方据此跳过一切「版本落后」提示。
 */
export function pageBundle(): string | null {
  if (typeof document === 'undefined') return null
  const scripts = Array.from(document.querySelectorAll<HTMLScriptElement>('script[src*="/assets/index-"]'))
  for (const script of scripts) {
    const src = script.getAttribute('src') ?? ''
    const match = src.match(/index-[\w-]+\.js/)
    if (match) return match[0]
  }
  return null
}

/** 页面是否落后于服务端（两侧都拿到 bundle 名且不同才算落后） */
export function isPageStale(page: string | null, server: string | null | undefined): boolean {
  if (!page || !server) return false
  return page !== server
}

/** ISO 时间 → 本地可读（解析失败原样返回，绝不渲染 Invalid Date） */
export function formatIso(iso: string | null | undefined): string {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  return date.toLocaleString('zh-CN', { hour12: false })
}

/** commit 短号（9 位，对齐 git 习惯） */
export function shortCommit(commit: string | null | undefined): string {
  if (!commit) return '—'
  return commit.slice(0, 9)
}
