/**
 * 剪贴板工具
 * ------------------------------------------------------
 * 统一复制入口：navigator.clipboard 优先（现代环境 / Electron），
 * 降级 textarea + execCommand（file:// 等无 clipboard API 的场景）。
 * 返回 Promise<boolean>：成功 true，失败 false（由调用方决定是否提示）。
 */

/** 复制文本到剪贴板；返回是否成功 */
export async function copyText(text: string): Promise<boolean> {
  // 主路径：异步剪贴板 API（HTTPS / Electron 可用）
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text)
      return true
    } catch {
      // 权限拒绝等失败时继续尝试降级路径
    }
  }

  // 降级路径：隐藏 textarea + execCommand('copy')
  try {
    const textarea = document.createElement('textarea')
    textarea.value = text
    // 移出视口且不可见，避免滚动跳动
    textarea.setAttribute('readonly', '')
    textarea.style.position = 'fixed'
    textarea.style.opacity = '0'
    document.body.appendChild(textarea)
    textarea.select()
    const ok = document.execCommand('copy')
    document.body.removeChild(textarea)
    return ok
  } catch {
    return false
  }
}
