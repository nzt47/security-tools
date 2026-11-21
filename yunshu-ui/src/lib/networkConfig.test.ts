/**
 * 网络配置请求体守卫（2026-11-22）
 * ------------------------------------------------------------------
 * 三条不能错的规则：
 *  1. 只提交 network / search 两个分区（不把整份 GET 响应回灌）；
 *  2. 数字字段必须是数字；
 *  3. 脱敏的 proxy_url（含 ***）不回写，否则会把真实代理密码替换成掩码。
 */
import { describe, expect, it } from 'vitest'
import { NetworkFormError, buildNetworkPayload } from './networkConfig'

const NET = { timeout: '30', max_retries: '3', backoff_factor: '0.5', proxy_enabled: false, proxy_url: '' }
const SEARCH = { enabled: true, default_engine: 'inst-1', max_results: '10', timeout: '20', cache_ttl: '300' }

describe('buildNetworkPayload', () => {
  it('只发 network / search 两个分区，数字转成 number', () => {
    const body = buildNetworkPayload(NET, SEARCH)
    expect(Object.keys(body).sort()).toEqual(['network', 'search'])
    expect(body.network).toEqual({
      timeout: 30, max_retries: 3, backoff_factor: 0.5, proxy_enabled: false, proxy_url: '',
    })
    expect(body.search).toEqual({
      enabled: true, default_engine: 'inst-1', max_results: 10, timeout: 20, cache_ttl: 300,
    })
  })

  it('脱敏代理地址（含 ***）不回写（保留后端现值）', () => {
    const body = buildNetworkPayload({ ...NET, proxy_url: 'http://***@10.0.0.1:8080', proxy_enabled: true }, SEARCH)
    expect(body.network).not.toHaveProperty('proxy_url')
    expect((body.network as Record<string, unknown>).proxy_enabled).toBe(true)
  })

  it('合法数字串 / 小数退避都能解析', () => {
    const body = buildNetworkPayload({ ...NET, timeout: ' 45 ', backoff_factor: '1.25' }, { ...SEARCH, max_results: '5' })
    expect((body.network as Record<string, unknown>).timeout).toBe(45)
    expect((body.network as Record<string, unknown>).backoff_factor).toBe(1.25)
    expect((body.search as Record<string, unknown>).max_results).toBe(5)
  })

  it.each([
    ['网络超时', { ...NET, timeout: '' }, SEARCH],
    ['网络超时', { ...NET, timeout: 'abc' }, SEARCH],
    ['网络超时', { ...NET, timeout: '0' }, SEARCH],
    ['重试次数', { ...NET, max_retries: '-1' }, SEARCH],
    ['退避系数', { ...NET, backoff_factor: '' }, SEARCH],
    ['搜索结果上限', NET, { ...SEARCH, max_results: '0' }],
    ['缓存 TTL', NET, { ...SEARCH, cache_ttl: '1.5' }],
  ])('非法输入 ⇒ NetworkFormError 且点名「%s」', (label, net, search) => {
    expect(() => buildNetworkPayload(net, search)).toThrow(NetworkFormError)
    try {
      buildNetworkPayload(net, search)
    } catch (e) {
      expect((e as Error).message).toContain(label)
    }
  })

  it('不发 engine_priority（由实例注册顺序自动维护）', () => {
    const body = buildNetworkPayload(NET, SEARCH)
    expect(body.search).not.toHaveProperty('engine_priority')
  })
})
