/**
 * 跨窗口状态同步适配器
 * ------------------------------------------------
 * 机制：主进程作为事件总线（ipc.ts 中 StateSync 通道），
 * 所有窗口将 messages/thinking 快照广播出去，由主进程转发给其它窗口。
 *
 * 防回环策略：
 *  - 主进程不把快照回传源窗口
 *  - 收到快照时先 JSON 对比，内容一致则跳过 setState，避免"广播→收到→再广播"死循环
 *
 * 【不易】防"信息倒退"（2026-09-19 修复）：
 *   本窗口正在流式生成（streaming）时**拒绝**应用对方快照 —— 分离窗口/另一标签页的
 *   快照可能产生于本窗口新消息之前，直接覆盖会把刚生成的回复（连同其
 *   steps = 思考过程 / 工具调用）整条抹掉，表现为"思考/工具先出现、过一会就没了"。
 *   同理，对方快照条数比本地更少（疑似旧快照）时也拒绝；但"清空会话"（0 条）语义
 *   需要传播，故 0 条快照在本地非流式时照常应用。
 *
 * 只在 Electron 环境生效；Web 模式返回空清理函数，纯 Web 行为不变（【不易】）。
 */
import { useLayoutStore } from '../stores/useLayoutStore';
import type { ChatMessage, ThinkingEvent } from '../stores/useLayoutStore';
import type { StateSyncPayload } from './ipc';
import { isElectron } from './types';

/** 广播节流间隔：流式分片约 20ms/片，150ms 合并一次足够保证"实时一致" */
const BROADCAST_INTERVAL_MS = 150;

/**
 * 启动跨窗口状态同步，返回清理函数（组件卸载 / 应用销毁时调用）。
 * 应在每个渲染进程（主窗口 + 独立窗口）的入口调用一次。
 */
export function startCrossWindowSync(): () => void {
  if (!isElectron()) return () => {};

  const api = window.electronAPI!;

  // ── 接收：合并其它窗口广播的快照 ──
  const unsubRecv = api.onStateSync((payload: StateSyncPayload) => {
    if (payload.type !== 'snapshot') return;
    const state = useLayoutStore.getState();
    const incomingCount = Array.isArray(payload.messages) ? payload.messages.length : 0;

    // 防信息倒退（见文件头【不易】）：本地正在流式，或对方快照明显更旧 → 丢弃
    if (state.streaming && incomingCount > 0) {
      console.warn('[云枢·sync] 本地正在流式生成，忽略其它窗口快照以免丢失本轮步骤');
      return;
    }
    if (incomingCount > 0 && incomingCount < state.messages.length) {
      console.warn(
        `[云枢·sync] 忽略更旧的快照（对方 ${incomingCount} 条 < 本地 ${state.messages.length} 条）`,
      );
      return;
    }

    const local = JSON.stringify([state.messages, state.thinking]);
    const incoming = JSON.stringify([payload.messages, payload.thinking]);
    if (local !== incoming) {
      // 快照可能早于本地类型定义，运行时形状一致（见 ipc.ts 契约）
      useLayoutStore.setState({
        messages: payload.messages as ChatMessage[],
        thinking: payload.thinking as ThinkingEvent[],
      });
    }
  });

  // ── 发送：本地状态变化后按节流广播全量快照 ──
  let dirty = false;
  const unsubStore = useLayoutStore.subscribe((state, prev) => {
    if (state.messages !== prev.messages || state.thinking !== prev.thinking) {
      dirty = true;
    }
  });

  const timer = setInterval(() => {
    if (!dirty) return;
    dirty = false;
    const state = useLayoutStore.getState();
    api.broadcastState({ type: 'snapshot', messages: state.messages, thinking: state.thinking });
  }, BROADCAST_INTERVAL_MS);

  return () => {
    unsubRecv();
    unsubStore();
    clearInterval(timer);
  };
}
