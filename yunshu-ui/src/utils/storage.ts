/**
 * 浏览器存储统一封装
 * ------------------------------------------------------
 * 【Why】收敛散落的裸 localStorage 调用，统一 JSON 解析容错、异常防护与键名规范。
 *
 * 设计决策（【不易】优先）：
 *  - 不做"自动加前缀"：既有契约键（token / yunshu-theme / yunshu-remember-* 等）
 *    为无前缀裸键，被路由守卫 / axios 拦截器 / 登录页直接读取，自动加前缀会破坏契约。
 *  - 改为「键名注册表 + 显式完整键名」：新键统一以 `yunshu:` 命名并先在 STORAGE_KEYS 登记。
 *  - 双通道：getRaw/setRaw（原样字符串，适合 token 等非 JSON 值）、getJSON/setJSON（结构化）。
 *  - 读写一律 try/catch：隐私模式 / 配额超限（QuotaExceededError）不抛致命异常。
 */
import { logger } from './logger'

/** 键名注册表：新键必须在此登记后再使用（键名即完整存储键，含既有契约键） */
export const STORAGE_KEYS = {
  /** 登录凭证（既有契约键：无前缀，守卫/拦截器/登录页直接读取） */
  TOKEN: 'token',
  /** 主题（既有契约键：'light' | 'dark'，默认深色） */
  THEME: 'yunshu-theme',
  /** 记住密码-密文（登录页 AES-GCM 加密后写入） */
  REMEMBER_LOGIN: 'yunshu-remember-login',
  /** 记住密码-密钥（登录页 Web Crypto 导出密钥） */
  REMEMBER_KEY: 'yunshu-remember-key',
  /** Web 联调 mock 独立窗口快照（mockElectron 跨标签页暂存） */
  MOCK_SNAPSHOT: 'yunshu:mock:snapshot',
  /** 代码编辑器面板持久化（CodeEditorPanel 跨窗口共享） */
  EDITOR_CODE: 'yunshu:editor:code:v1',
} as const

export type StorageKey = (typeof STORAGE_KEYS)[keyof typeof STORAGE_KEYS]

/** 从 localStorage 读取原样字符串；不存在或读取失败返回 null */
export function getRaw(key: string): string | null {
  try {
    return localStorage.getItem(key)
  } catch (err) {
    logger.warn(`[storage] getRaw 读取失败（key=${key}）：${err instanceof Error ? err.message : String(err)}`)
    return null
  }
}

/** 写入原样字符串（token 等非 JSON 值使用，避免被 JSON.stringify 加引号破坏契约） */
export function setRaw(key: string, value: string): void {
  try {
    localStorage.setItem(key, value)
  } catch (err) {
    logger.warn(`[storage] setRaw 写入失败（key=${key}）：${err instanceof Error ? err.message : String(err)}`)
  }
}

/** 读取结构化数据；不存在 / JSON 解析失败回退 fallback（对齐 zustand persist 的 sanitize 防御思路） */
export function getJSON<T>(key: string, fallback: T): T {
  const raw = getRaw(key)
  if (raw === null) return fallback
  try {
    return JSON.parse(raw) as T
  } catch (err) {
    logger.warn(
      `[storage] getJSON 解析失败（key=${key}），回退默认：${err instanceof Error ? err.message : String(err)}`,
    )
    return fallback
  }
}

/** 写入结构化数据（内部 JSON.stringify） */
export function setJSON<T>(key: string, value: T): void {
  try {
    setRaw(key, JSON.stringify(value))
  } catch {
    // JSON.stringify 对循环引用等会抛错，由 setRaw 的 logger.warn 覆盖（此处吞掉避免二次日志）
  }
}

/** 删除键 */
export function remove(key: string): void {
  try {
    localStorage.removeItem(key)
  } catch (err) {
    logger.warn(`[storage] remove 失败（key=${key}）：${err instanceof Error ? err.message : String(err)}`)
  }
}

/** 键是否存在 */
export function has(key: string): boolean {
  return getRaw(key) !== null
}

/** 统一出口 */
export const storage = { getRaw, setRaw, getJSON, setJSON, remove, has }
