/**
 * 系统级能力封装（Web 实现）
 * ----------------------------------------------------------
 * 【Why】统一收口浏览器系统能力，为 Electron 迁移预留替换点：
 *   - 文件下载：Web 用 <a download> + Blob；Electron 迁移时只需替换本函数
 *     为主进程 dialog.showSaveDialog + fs.writeFile，业务页面零改动。
 * 后续系统级能力（打开外部链接、系统通知等）同样在此扩展。
 */
/**
 * 下载文件（统一系统级能力入口）
 * - content 支持字符串或 BlobPart[]：大数据量导出时把分片数组直接交给 Blob 拼接，
 *   避免在 JS 侧先拼出整份大字符串（峰值内存约为原来的 1/2，仅多一份 Blob 拷贝）。
 */
export function downloadFile(name: string, content: string | BlobPart[], mime: string): void {
  const blob = new Blob(Array.isArray(content) ? content : [content], { type: mime })
  console.info(`[download] 开始：${name}（${mime}，${blob.size} 字节）`)
  const url = URL.createObjectURL(blob)
  try {
    const a = document.createElement('a')
    a.href = url
    a.download = name
    a.click()
    console.info(`[download] 完成：${name}，已触发浏览器下载`)
  } catch (err) {
    // 下载失败向上抛出（调用方可提示用户），对象 URL 在 finally 中兜底回收
    console.error(`[download] 失败：${name}：${err instanceof Error ? err.message : String(err)}`)
    throw err
  } finally {
    URL.revokeObjectURL(url)
  }
}
// TODO: 后续替换为 Electron 下载（主进程 dialog.showSaveDialog + fs.writeFile）
