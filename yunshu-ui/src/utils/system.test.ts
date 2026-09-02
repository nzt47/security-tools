/**
 * system.ts 下载工具测试（含 mock 数据）
 * ------------------------------------------------------
 * 验证 downloadFile 的核心链路：
 *   1. 正常下载：Blob MIME 与内容正确、<a download> 文件名正确、触发点击、完成后回收对象 URL
 *   2. 日志埋点：下载前后打印 [download] 开始 / 完成
 *   3. 失败场景：点击抛错时向上抛出并回收 URL（不泄漏对象 URL）
 * jsdom 未实现 URL.createObjectURL，测试中统一打桩。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { downloadFile } from './system'

/** mock 数据：模拟一次 JSON 导出内容 */
const MOCK_NAME = 'test-export.json'
const MOCK_CONTENT = '{"rows":[{"id":1,"name":"样例"}]}'
const MOCK_MIME = 'application/json'

// jsdom 无 URL.createObjectURL / revokeObjectURL，打桩替换（参数标注 Blob，使 calls 元组类型可用）
const createObjectURL = vi.fn((_blob: Blob) => 'blob:mock-url')
const revokeObjectURL = vi.fn((_url: string) => {})

beforeEach(() => {
  createObjectURL.mockClear()
  revokeObjectURL.mockClear()
  // 合并原生 URL 上其余静态方法（如 parse），只替换 Blob 相关两个
  vi.stubGlobal('URL', Object.assign(Object.create(URL), {
    createObjectURL,
    revokeObjectURL,
  }))
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

/** jsdom 的 Blob 无 .text()，统一用 FileReader 读取文本 */
function readBlobText(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result))
    reader.onerror = () => reject(reader.error ?? new Error('FileReader 读取失败'))
    reader.readAsText(blob)
  })
}

describe('downloadFile', () => {
  it('触发 <a download> 下载：文件名 / Blob MIME / 点击 / URL 回收全链路正确', async () => {
    const infoSpy = vi.spyOn(console, 'info').mockImplementation(() => {})
    let clickedAnchor: HTMLAnchorElement | undefined
    // 【Why】jsdom 点击 <a download> 无真实下载，且可能触发"导航未实现"警告，故拦截并捕获实例
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      // no-this-alias：此处确需捕获被点击的锚点实例（jsdom 无真实下载）
      // eslint-disable-next-line @typescript-eslint/no-this-alias
      clickedAnchor = this
    })

    downloadFile(MOCK_NAME, MOCK_CONTENT, MOCK_MIME)

    // Blob：MIME 与内容正确
    const blobArg = createObjectURL.mock.calls[0][0] as Blob
    expect(blobArg.type).toBe(MOCK_MIME)
    expect(await readBlobText(blobArg)).toBe(MOCK_CONTENT)

    // 锚点：文件名与对象 URL 正确，且已触发点击
    expect(clickedAnchor).toBeDefined()
    expect(clickedAnchor!.download).toBe(MOCK_NAME)
    expect(clickedAnchor!.href).toBe('blob:mock-url')
    expect(clickedAnchor!.click).toHaveBeenCalledTimes(1)

    // 对象 URL 已回收，不泄漏内存
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:mock-url')

    // 日志埋点：下载前后均打印
    expect(infoSpy).toHaveBeenCalledWith(expect.stringContaining('[download] 开始'))
    expect(infoSpy).toHaveBeenCalledWith(expect.stringContaining('[download] 完成'))
  })

  it('BlobPart[] 输入：分片数组直接交付 Blob，拼接结果与整串一致', async () => {
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    // 【Why】不含 BOM 开头：jsdom 的 Blob 走 TextEncoder 会剥离字符串首字符 BOM（真实浏览器不剥离），
    // BOM 放置逻辑已由导出页测试（parts 数组 join 后以 \ufeff 开头）覆盖，此处只验分片拼接语义
    const parts = ['ID,用户名', '\n', '1,管理员', '\n', '2,访客']

    downloadFile('parts.csv', parts, 'text/csv;charset=utf-8')

    const blobArg = createObjectURL.mock.calls[0][0] as Blob
    expect(blobArg.type).toBe('text/csv;charset=utf-8')
    expect(await readBlobText(blobArg)).toBe('ID,用户名\n1,管理员\n2,访客')
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:mock-url')
  })

  it('下载点击抛错时向上抛出并回收 URL', () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {
      throw new Error('download aborted')
    })

    expect(() => downloadFile(MOCK_NAME, MOCK_CONTENT, MOCK_MIME)).toThrow('download aborted')
    // 异常路径同样回收对象 URL
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:mock-url')
    expect(errorSpy).toHaveBeenCalledWith(expect.stringContaining('[download] 失败'))
  })
})
