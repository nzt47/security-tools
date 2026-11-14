/**
 * 主题类（topic）词表 —— 与后端 `agent/topics.py` **逐字同源**。
 *
 * 【为什么前端也要一份】UI 要在"主线装载哪些类""分身装载哪些类"两处让人选，
 * 而选项文案必须与后端闭集一致；写死在页面里就会出现两套词表。
 * 【改这里的纪律】必须同批改 `agent/topics.py`（后端会拒收词表外的值），
 * 并同批改 `tests/unit/test_topics_vocabulary_parity.py`（对拍用例）。
 */

export interface TopicOption {
  id: string
  label: string
  hint: string
}

export const TOPIC_VOCABULARY: TopicOption[] = [
  { id: 'general', label: '通用', hint: '兜底：未分类 / 跨域 / 旧数据（未声明即归它）' },
  { id: 'engineering', label: '工程', hint: '代码、构建、测试、发布、仓库' },
  { id: 'knowledge', label: '知识', hint: '知识沉淀、蒸馏、卡片、流程固化' },
  { id: 'ops', label: '运维', hint: '部署、调度、监控、告警' },
  { id: 'product', label: '产品', hint: '需求、方案、产品决策' },
]

/** 兜底类：未声明、旧数据、取不到上下文，全部归它（fail-closed） */
export const DEFAULT_TOPIC = 'general'

const KNOWN = new Set(TOPIC_VOCABULARY.map((t) => t.id))

/**
 * 归一化白名单（保序去重；**未知值丢弃**，空 ⇒ 只有 general）。
 *
 * 【关键】未知值不能回落 general —— 那会让"写错一个类名"变成"额外放行 general"，
 * 也就是因为 typo 把白名单变宽（fail-open）。后端 normalize_topics 同口径。
 */
export function normalizeTopics(value: unknown): string[] {
  const raw = Array.isArray(value)
    ? value
    : typeof value === 'string'
      ? value.split(',')
      : []
  const out: string[] = []
  for (const item of raw) {
    if (typeof item !== 'string') continue
    const id = item.trim().toLowerCase()
    if (!id || !KNOWN.has(id) || out.includes(id)) continue
    out.push(id)
  }
  return out.length ? out : [DEFAULT_TOPIC]
}

export function topicLabel(id: string): string {
  return TOPIC_VOCABULARY.find((t) => t.id === id)?.label ?? id
}
