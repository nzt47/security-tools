/**
 * 提示词影响因素面板 · 全局状态（Zustand + persist）
 * ------------------------------------------------
 * 持久化字段（LocalStorage 键 yunshu:prompt-lab:v1）：
 *  - values         ：各因素当前值（id 为锚点）
 *  - customFactors  ：用户自定义因素定义
 *  - llm            ：真实预览接口配置（endpoint/model；apiKey 本地存储，导出时遮蔽）
 * 反序列化均做结构校验，脏数据回退默认，避免白屏。
 *
 * 深度合并说明：系统提示词不再本地沙箱化（旧 systemParts/7 段组件已移除），
 * 「身份提示词」线上配置由 IdentityPromptPanel / useIdentityPrompt 直接管理，
 * 不进入本地持久化。
 */
import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';
import type {
  FactorCategory,
  FactorControl,
  FactorValue,
  PromptFactorDef,
} from '../lib/promptFactorTypes';
import { DEFAULT_VALUES, allFactors } from '../lib/promptFactors';

export interface LlmConfig {
  enabled: boolean;
  endpoint: string;
  apiKey: string;
  model: string;
  /** 上下文窗口大小（token），用于用量占比估算 */
  contextWindow: number;
}

const DEFAULT_LLM: LlmConfig = { enabled: false, endpoint: '', apiKey: '', model: 'gpt-4o-mini', contextWindow: 32768 };

interface PromptLabState {
  values: Record<string, FactorValue>;
  customFactors: PromptFactorDef[];
  llm: LlmConfig;
  setValue: (id: string, value: FactorValue) => void;
  addCustomFactor: (def: PromptFactorDef) => void;
  removeCustomFactor: (id: string) => void;
  resetValues: () => void;
  setLlm: (patch: Partial<LlmConfig>) => void;
}

/** 反序列化校验：values 仅保留已知因素 id 且值类型匹配 */
function sanitizeValues(raw: unknown): Record<string, FactorValue> {
  const defs = allFactors([]); // 默认因素作为 id 白名单
  const known = new Map(defs.map((d) => [d.id, d]));
  const out: Record<string, FactorValue> = {};
  if (raw && typeof raw === 'object') {
    for (const [id, v] of Object.entries(raw as Record<string, unknown>)) {
      const def = known.get(id);
      if (!def) continue;
      if (typeof def.defaultValue === 'number' && typeof v === 'number' && Number.isFinite(v)) {
        out[id] = v;
      } else if (typeof def.defaultValue === 'string' && typeof v === 'string') {
        out[id] = v;
      } else if (typeof def.defaultValue === 'boolean' && typeof v === 'boolean') {
        out[id] = v;
      }
    }
  }
  return out;
}

/** 反序列化校验：自定义因素必须是合法结构 */
function sanitizeCustomFactors(raw: unknown): PromptFactorDef[] {
  if (!Array.isArray(raw)) return [];
  const cats: FactorCategory[] = ['structure', 'language', 'context', 'model', 'evaluation'];
  const ctrls: FactorControl[] = ['slider', 'select', 'text', 'toggle'];
  return raw.filter(
    (f): f is PromptFactorDef =>
      !!f &&
      typeof f === 'object' &&
      typeof (f as PromptFactorDef).id === 'string' &&
      typeof (f as PromptFactorDef).name === 'string' &&
      typeof (f as PromptFactorDef).desc === 'string' &&
      cats.includes((f as PromptFactorDef).category) &&
      ctrls.includes((f as PromptFactorDef).control),
  );
}

/** 反序列化校验：LLM 配置 */
function sanitizeLlm(raw: unknown): LlmConfig {
  const r = (raw ?? {}) as Partial<LlmConfig>;
  return {
    enabled: r.enabled === true,
    endpoint: typeof r.endpoint === 'string' ? r.endpoint : '',
    apiKey: typeof r.apiKey === 'string' ? r.apiKey : '',
    model: typeof r.model === 'string' && r.model ? r.model : DEFAULT_LLM.model,
    contextWindow:
      typeof r.contextWindow === 'number' && Number.isFinite(r.contextWindow) && r.contextWindow > 0
        ? r.contextWindow
        : DEFAULT_LLM.contextWindow,
  };
}

export const usePromptLabStore = create<PromptLabState>()(
  persist(
    (set) => ({
      values: { ...DEFAULT_VALUES },
      customFactors: [],
      llm: { ...DEFAULT_LLM },

      setValue: (id, value) =>
        set((s) => ({ values: { ...s.values, [id]: value } })),

      addCustomFactor: (def) =>
        set((s) => ({ customFactors: [...s.customFactors, { ...def, custom: true }] })),

      removeCustomFactor: (id) =>
        set((s) => {
          const customFactors = s.customFactors.filter((f) => f.id !== id);
          const values = { ...s.values };
          delete values[id];
          return { customFactors, values };
        }),

      resetValues: () => set({ values: { ...DEFAULT_VALUES } }),

      setLlm: (patch) =>
        set((s) => ({ llm: { ...s.llm, ...patch } })),
    }),
    {
      name: 'yunshu:prompt-lab:v1',
      version: 1,
      storage: createJSONStorage(() => localStorage),
      partialize: (s): Pick<PromptLabState, 'values' | 'customFactors' | 'llm'> => ({
        values: s.values,
        customFactors: s.customFactors,
        llm: s.llm,
      }),
      merge: (persisted, current) => {
        const saved = persisted as Partial<
          Pick<PromptLabState, 'values' | 'customFactors' | 'llm'>
        >;
        return {
          ...current,
          values: { ...DEFAULT_VALUES, ...sanitizeValues(saved.values) },
          customFactors: sanitizeCustomFactors(saved.customFactors),
          llm: sanitizeLlm(saved.llm),
        };
      },
    },
  ),
);
