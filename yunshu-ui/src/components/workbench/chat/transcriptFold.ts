/**
 * transcriptFold —— 「工作细节」折叠判定（纯函数，2026-11-28）
 * ------------------------------------------------------------------
 * 把 DSH 的三个能力位翻译成云枢的数据模型：
 *   · 一轮 = 一条 user 消息之后、下一条 user 之前的所有 assistant 消息；
 *   · 「历史轮次」= 从后端载入的轮次（id 带 hist- 前缀，见 useLayoutStore）；
 *   · 折叠 = 把该轮的「思考过程 + 工具调用」收成一行，回复本身照常显示。
 *
 * 这里只做判定，不碰 DOM：MessageItem 负责渲染，ChatPanel 负责算 TurnMeta。
 * 判定矩阵（与 DSH presentation-policy 同构）：
 *
 *   档位      完成即折叠   下次输入才折叠   折叠范围
 *   紧凑      是           —               全部完成轮
 *   标准      是           —               全部完成轮
 *   详细      仅历史轮     —               历史轮（新轮保持展开，除非已翻篇）
 *   冗长      否           —               —
 */
import type {
  CollapseTiming,
  FoldedReplies,
  TranscriptViewMode,
} from '../../../stores/useChatPrefsStore'

/** 一条消息在「轮次」里的位置（由 ChatPanel 一次算好传下来） */
export interface TurnMeta {
  /** 所属轮次 id = 该轮之前那条 user 消息 id（首轮无 user 时为 turn-0） */
  turnId: string
  /** 这一轮之后是否已经出现过用户消息（出现过 = 已"翻篇"） */
  turnClosed: boolean
  /** 同一轮里是不是最后一条 assistant 回复（折叠保留=仅最终时用来隐中间回复） */
  isLastAssistantInTurn: boolean
}

/** 仅依赖 role/id 的最小消息形状（便于纯函数测试） */
export interface TurnInput {
  id: string
  role: 'user' | 'assistant'
}

/**
 * 一次算出每条消息的轮次信息（O(n)）。
 * @param messages - 当前会话消息序列
 * @returns 与 messages 等长的轮次信息
 */
export function computeTurnMeta(messages: readonly TurnInput[]): TurnMeta[] {
  const turnIds: string[] = []
  let current = 'turn-0'
  for (const message of messages) {
    if (message.role === 'user') current = message.id
    turnIds.push(current)
  }
  // 反向扫一遍同时得到「后面还有 user」与「后面还有同轮 assistant」
  const closed = new Array<boolean>(messages.length).fill(false)
  const lastAssistant = new Array<boolean>(messages.length).fill(true)
  let sawUserAfter = false
  let lastAssistantIndex = -1
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    closed[i] = sawUserAfter
    if (messages[i].role === 'assistant') {
      if (lastAssistantIndex === -1 || turnIds[lastAssistantIndex] !== turnIds[i]) lastAssistant[i] = true
      else lastAssistant[i] = false
      lastAssistantIndex = i
    }
    if (messages[i].role === 'user') sawUserAfter = true
  }
  return messages.map((message, i) => ({
    turnId: turnIds[i],
    turnClosed: closed[i],
    isLastAssistantInTurn: lastAssistant[i],
  }))
}

export interface FoldInput {
  mode: TranscriptViewMode
  timing: CollapseTiming
  status: 'streaming' | 'done' | 'error' | undefined
  /** 是否来自后端历史（useLayoutStore.isHistoryMessageId） */
  isHistory: boolean
  /** ChatPanel 给的轮次信息；缺省按"未翻篇"处理（保守：不折叠） */
  turnClosed: boolean
  hasSteps: boolean
}

/**
 * 这一条 assistant 消息的「过程」是否应折叠成一行。
 * @param input - 档位 / 时机 / 消息状态 / 轮次位置
 * @returns true = 折叠；false = 按原样展开
 */
export function shouldFoldProcess(input: FoldInput): boolean {
  if (!input.hasSteps) return false
  if (input.status !== 'done') return false
  if (input.mode === 'verbose') return false
  if (input.timing === 'next-input' && !input.turnClosed) return false
  if (input.mode === 'detailed') return input.isHistory || input.turnClosed
  return true
}

/** 折叠行右侧的预览文案：紧凑档不给（只留步数），其余档给最后动作或首行推理 */
export function foldPreview(mode: TranscriptViewMode, steps: readonly { title?: string; detail?: string }[]): string {
  if (mode === 'compact' || steps.length === 0) return ''
  const last = steps[steps.length - 1]
  const title = (last.title ?? '').replace(/^工具调用：/, '').trim()
  const detailFirstLine = (last.detail ?? '').split(/\r?\n/).map((line) => line.trim()).find(Boolean) ?? ''
  const text = title || detailFirstLine
  if (!text) return ''
  return text.length > 40 ? text.slice(0, 40) + '…' : text
}

/**
 * 折叠时是否要把这条中间回复也藏起来（折叠保留 = 仅最终回复）。
 * @param foldedReplies - 用户选择
 * @param folded - 该消息的过程是否已折叠
 * @param meta - 轮次信息（缺省视为轮内唯一回复）
 */
export function shouldHideIntermediateReply(
  foldedReplies: FoldedReplies,
  folded: boolean,
  meta: TurnMeta | undefined,
): boolean {
  if (foldedReplies !== 'final' || !folded) return false
  if (!meta) return false
  return !meta.isLastAssistantInTurn
}
