/**
 * usePersonalityFactors —— 人格 6 维 ↔ 后端 /api/personality 的双向同步
 * ------------------------------------------------------------------
 * 【为什么有这个文件】Owner 2026-11-15：「人格能不能放进影响提示词因素里？」⟶ 选了轻量版
 * （§11.123 K-2 / §11.124 D-2）：**前端为主**，6 个 0–1 滑块住在「提示词影响因素实验室」
 * 的「人格」分类里，调了就真的写后端；后端人格模块不用动。
 *
 * 数据流（三条，缺一条就会"面板在动、后端没变"）：
 *   1. 首屏 GET /api/personality → 用 custom_params **覆盖** store 里的默认值
 *      （否则界面显示的是 lib 里的默认档，与线上不一致，拖动一次就把线上改成默认值）；
 *   2. 6 维值变化 → **防抖 400ms** → POST /api/personality/params
 *      （拖动时每一帧都打接口既慢又会把后端写爆；停手一次只发一次）；
 *   3. 每次只发**变动的那几维**（后端 update_params 是合并语义）——
 *      整份 6 键提交会在"先在人格 Tab 改 A、再回实验室拖 B"时把 A 悄悄覆盖回旧值。
 *
 * 【视图如实】人格参数进不进提示词，由系统提示词的 persona 节决定（默认关闭），
 * 本 hook 只管"写进后端"，不承诺"影响回答"；界面的黄条见 index.tsx。
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { getEnvelope, postEnvelope } from '@/api/envelope'
import { PERSONALITY, PERSONALITY_PARAMS } from '@/api/endpoints'
import {
  PERSONALITY_DIMENSIONS,
  personalityParamsDiff,
  personalityParamsOf,
} from '../../lib/promptFactors'
import type { FactorValue } from '../../lib/promptFactorTypes'

/** 防抖窗口（ms）：拖动滑块时只在停手后发一次 POST */
export const PERSONALITY_DEBOUNCE_MS = 400

/** GET /api/personality 的载荷（只声明本 hook 用到的两个键，其余原样忽略） */
export interface PersonalityPayload {
  current_profile?: string
  custom_params?: Record<string, number>
}

export interface PersonalitySync {
  /** 首屏读取中 */
  loading: boolean
  /** 正在写回后端 */
  saving: boolean
  /** 读/写失败的原文（空串 = 无错） */
  error: string
  /** 后端当前档位（写入后后端会置 custom）；读不到时为空串 */
  profile: string
  /** 是否已经尝试过读取（读失败也算；界面据此区分"默认值"与"后端值"） */
  loaded: boolean
  /** 最近一次写入成功的时刻（Date.now()），null = 还没写过 */
  syncedAt: number | null
}

const msgOf = (e: unknown): string => (e instanceof Error ? e.message : String(e))

/**
 * @param values     store 里的因素当前值（只读其中 personality.* 6 项）
 * @param setValues  store 的批量写入口（首屏用后端值覆盖默认值）
 */
export function usePersonalityFactors(
  values: Record<string, FactorValue>,
  setValues: (patch: Record<string, FactorValue>) => void,
): PersonalitySync {
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [profile, setProfile] = useState('')
  const [loaded, setLoaded] = useState(false)
  const [syncedAt, setSyncedAt] = useState<number | null>(null)

  /** 最新值（读取回填时要用；放进依赖会让 GET 随每次拖动重跑） */
  const valuesRef = useRef(values)
  /** 后端已知的那一份参数 = POST 的比对基线 */
  const baselineRef = useRef<Record<string, number> | null>(null)
  useEffect(() => {
    valuesRef.current = values
  }, [values])

  // ── 1. 首屏：GET 覆盖默认值 ──
  useEffect(() => {
    let alive = true
    getEnvelope<PersonalityPayload | null>(PERSONALITY)
      .then((d) => {
        if (!alive) return
        const custom = d?.custom_params ?? {}
        const patch: Record<string, FactorValue> = {}
        for (const dim of PERSONALITY_DIMENSIONS) {
          const v = custom[dim.key]
          // 只认数字（后端若回 null/字符串，宁可保留默认值，也不要写 NaN 进滑块）
          if (typeof v === 'number' && Number.isFinite(v)) {
            patch['personality.' + dim.key] = Math.min(1, Math.max(0, v))
          }
        }
        setValues(patch)
        // 基线 = 后端值 ∪ 本地未覆盖项：这样"刚读到就立刻回写一次"不会发生
        baselineRef.current = personalityParamsOf({ ...valuesRef.current, ...patch })
        setProfile(d?.current_profile ?? '')
        setLoaded(true)
      })
      .catch((e) => {
        if (!alive) return
        // 读不到就保留本地值（界面另有一句话说明"现在显示的不是后端值"）
        setError('读取人格参数失败：' + msgOf(e))
        baselineRef.current = personalityParamsOf(valuesRef.current)
        setLoaded(true)
      })
      .finally(() => {
        if (alive) setLoading(false)
      })
    return () => {
      alive = false
    }
  }, [setValues])

  // ── 2. 变化 → 防抖 400ms → 只发改动项 ──
  const params = useMemo(() => personalityParamsOf(values), [values])
  useEffect(() => {
    if (!loaded || baselineRef.current === null) return
    const diff = personalityParamsDiff(baselineRef.current, params)
    if (Object.keys(diff).length === 0) return
    const timer = setTimeout(() => {
      setSaving(true)
      postEnvelope<{ ok?: boolean; params?: Record<string, number> }>(PERSONALITY_PARAMS, { params: diff })
        .then((d) => {
          // 基线只并入"这次确实发出去的键"；期间又拖的那一维由下一轮 effect 再发
          const merged: Record<string, number> = { ...(baselineRef.current ?? {}), ...diff }
          if (d?.params) {
            for (const [k, v] of Object.entries(d.params)) {
              if (typeof v === 'number' && Number.isFinite(v)) merged[k] = v
            }
          }
          baselineRef.current = merged
          setProfile('custom')
          setSyncedAt(Date.now())
          setError('')
        })
        .catch((e) => setError('写回人格参数失败：' + msgOf(e)))
        .finally(() => setSaving(false))
    }, PERSONALITY_DEBOUNCE_MS)
    return () => clearTimeout(timer)
  }, [params, loaded])

  return { loading, saving, error, profile, loaded, syncedAt }
}
