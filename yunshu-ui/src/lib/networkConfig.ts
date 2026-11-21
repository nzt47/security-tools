/**
 * 网络配置表单 —— 唯一序列化口径（2026-11-22）
 * ------------------------------------------------------------------
 * 【为什么单独一个纯函数】页面要同时做三件容易被写错的事：
 *  1. 只提交 `network` / `search` 两个分区（不再把整份 GET 响应回灌 ——
 *     那会把脱敏 api_key、llm_instances、change_log 一起写回去）；
 *  2. 数字字段必须是数字（字符串写进 int 字段会让后端比较/超时行为变怪）；
 *  3. 脱敏的 proxy_url（含 ***）**不能回写**，否则会把真实代理密码替换成掩码。
 * 这三条都在这里判，页面不重复。
 */
export interface NetworkDraft {
  timeout: string
  max_retries: string
  backoff_factor: string
  proxy_enabled: boolean
  /** 可能含 *** 掩码（GET 会脱敏 user:pass@） */
  proxy_url: string
}

export interface SearchDraft {
  enabled: boolean
  /** 搜索实例 id（后端按实例 id 校验；空 = 不指定，按优先级） */
  default_engine: string
  max_results: string
  timeout: string
  cache_ttl: string
}

/** 表单校验失败（带字段名；页面直接展示这句话，不进控制台） */
export class NetworkFormError extends Error {}

function asInt(label: string, raw: string, min: number): number {
  const text = String(raw ?? '').trim()
  const value = Number(text)
  if (text === '' || !Number.isFinite(value) || !Number.isInteger(value) || value < min) {
    throw new NetworkFormError(label + ' 必须是 ≥ ' + min + ' 的整数（收到：' + (text || '空') + '）')
  }
  return value
}

function asFloat(label: string, raw: string, min: number): number {
  const text = String(raw ?? '').trim()
  const value = Number(text)
  if (text === '' || !Number.isFinite(value) || value < min) {
    throw new NetworkFormError(label + ' 必须是 ≥ ' + min + ' 的数字（收到：' + (text || '空') + '）')
  }
  return value
}

/**
 * 表单 → `POST /api/network-config` 载荷（只含 network / search 两个分区）。
 *
 * 【为什么不发 engine_priority】它由 `apply_search_instances()` 按实例注册顺序
 * 自动重建（元素是搜索实例 id），手填没有意义、还会被下一次 apply 覆盖。
 */
export function buildNetworkPayload(net: NetworkDraft, search: SearchDraft): Record<string, unknown> {
  const network: Record<string, unknown> = {
    timeout: asInt('网络超时', net.timeout, 1),
    max_retries: asInt('重试次数', net.max_retries, 0),
    backoff_factor: asFloat('退避系数', net.backoff_factor, 0),
    proxy_enabled: net.proxy_enabled === true,
  }
  // 脱敏值不回写：不改动 = 保留原值；要替换必须重填完整地址（含密码）
  if (!String(net.proxy_url || '').includes('***')) {
    network.proxy_url = String(net.proxy_url || '').trim()
  }
  return {
    network,
    search: {
      enabled: search.enabled === true,
      default_engine: String(search.default_engine || '').trim(),
      max_results: asInt('搜索结果上限', search.max_results, 1),
      timeout: asInt('搜索超时', search.timeout, 1),
      cache_ttl: asInt('缓存 TTL', search.cache_ttl, 0),
    },
  }
}
