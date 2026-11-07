/**
 * 工具名归一与查行（叶子模块，2026-11-07）
 * ------------------------------------------------
 * 【为什么单独一份】"转化队列 → 去工具页启用"的深链里带的是**作业/卡片 id**
 * （`byted-seedance-video-generate`，连字符），而注册表里是 `byted_seedance_video_generate`
 * （下划线）。这层等值关系有两个调用点（工具页定位、将来的工具搜索），
 * 放在任一页面里都会形成"页面互相 import"，故下沉到叶子模块。
 *
 * 口径：**连字符与下划线视为同一个字**，再比大小写；末尾做一次后缀可容
 * （作业 id 常带前缀/段落，如 `tool:xxx` 之外的 `<来源>-<工具名>`）。
 */

/** 名字归一：去掉两端空白、转小写、抹平连字符/下划线 */
export function normToolName(raw: string): string {
  return (raw || '').trim().toLowerCase().replace(/[-_:]/g, '')
}

/** 在工具表里按名字找行（精确 → 归一 → 后缀可容） */
export function findToolByName<T extends { name: string }>(
  list: readonly T[],
  wanted: string,
): T | undefined {
  const raw = (wanted || '').trim()
  if (!raw) return undefined
  const exact = list.find((t) => t.name === raw)
  if (exact) return exact
  const target = normToolName(raw)
  if (!target) return undefined
  return list.find((t) => normToolName(t.name) === target)
    ?? list.find((t) => normToolName(t.name).endsWith(target))
}
