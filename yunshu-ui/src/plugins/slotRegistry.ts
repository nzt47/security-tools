/**
 * 插槽注册表核心（任务 T2.1；T2.4 profile 配置驱动完善）
 *
 * 三个核心原语：
 * 1. registerSlot(slotId)          —— 声明一个插槽（布局中的占位区域）
 * 2. mountToSlot / unmountFromSlot —— 把组件挂进/摘出插槽
 * 3. getSlotEntries(slotId)        —— 读取插槽内按序排列的组件
 *
 * Profile（配置驱动，T2.4）：profile.json 是界面的**唯一组装配置**——
 * 插槽条目的顺序（order）、显隐（hidden）全部由 profile 决定。回退语义：
 * - profile.json 缺失/解析失败/结构无效 → 回退代码内 DEFAULT_PROFILE；
 * - 插槽在 profile 中未出现 → 渲染该插槽全部已挂载条目（缺省 order 100）；
 * - 某条目在 profile 中缺失 → 用组件默认 order/hidden；
 * - 某条目在 profile 中声明但未挂载 → 忽略（不报错，仅 warn 一次）。
 * 任何配置异常**不得抛错**阻断渲染，一律 console.warn。
 *
 * 参考实现：docs/yunshu-pluginization/PLAN-2-frontend-slots.md §2、§3
 */
import React from 'react';

/** 挂入插槽的组件条目 */
export interface SlotEntry {
  /** 组件唯一 id，如 'mascot' */
  id: string;
  /** 要渲染的组件（可接收 SlotHost 透传的 props，见 SlotHostProps.props） */
  component: React.ComponentType<any>;
  /** 排序，小在前，默认 100 */
  order?: number;
  /** profile 可置为 true 隐藏 */
  hidden?: boolean;
  /** 面板标题（面板切换器用；非面板插槽可省略） */
  title?: string;
  /** 面板图标（lucide 图标名或任意文本/emoji，可选） */
  icon?: string;
}

/** profile 中单个条目的配置（不含 component） */
export interface SlotProfileItem {
  id: string;
  order?: number;
  hidden?: boolean;
}

/** 插槽配置：slotId -> 该插槽内各组件条目的配置 */
export interface SlotProfile {
  [slotId: string]: SlotProfileItem[];
}

/**
 * 代码内默认 profile（回退兜底，T2.4）。
 * 与 src/plugins/profile.json 的 .slots 内容保持一致（有单测守护一致性）。
 * profile.json 缺失/损坏时界面按此挂载，保证「无 profile 也完整」。
 */
export const DEFAULT_PROFILE: SlotProfile = {
  topbar: [{ id: 'status', order: 10 }],
  sidebar: [
    { id: 'panels', order: 5 },
    { id: 'mascot', order: 10 },
    { id: 'sessions', order: 20 },
  ],
  main: [{ id: 'chat', order: 10 }],
  panels: [
    { id: 'skills', order: 10, hidden: true },
    { id: 'knowledge', order: 20, hidden: true },
    { id: 'devconsole', order: 30, hidden: true },
    { id: 'plugin-center', order: 40, hidden: true },
  ],
};

const slots = new Map<string, Map<string, SlotEntry>>();
// 初始即代码内默认 profile：加载完成前/加载失败时，界面始终按默认挂载渲染（回退语义）。
let profile: SlotProfile = DEFAULT_PROFILE;
/** 「声明但未挂载」warn 去重集合，避免每次加载重复刷屏 */
const warnedUnknown = new Set<string>();

/**
 * profile 变体文件表（Vite 惰性加载，T2.4）。
 * - `?raw` 拿到文件原始文本，运行时再 JSON.parse —— 解析失败可安全回退；
 * - 非 eager 的 glob：profile.json 被删除/改名时**不产生构建错误**，
 *   对应 loader 缺失 → reloadProfile 回退 DEFAULT_PROFILE；
 * - 变体（如 profile.alt.json）也由此表发现，供运行时切换。
 */
const profileLoaders = import.meta.glob('./profile*.json', {
  query: '?raw',
  import: 'default',
}) as Record<string, () => Promise<string>>;

/** 声明一个插槽；重复声明幂等，不会清空已挂载的条目 */
export function registerSlot(slotId: string): void {
  if (!slots.has(slotId)) slots.set(slotId, new Map());
}

/** 把组件条目挂入插槽；插槽不存在时自动创建 */
export function mountToSlot(slotId: string, entry: SlotEntry): void {
  registerSlot(slotId);
  slots.get(slotId)!.set(entry.id, entry);
}

/** 从插槽摘除组件条目；插槽或 id 不存在时静默忽略 */
export function unmountFromSlot(slotId: string, id: string): void {
  slots.get(slotId)?.delete(id);
}

/**
 * 读取插槽内全部条目（应用 profile 的 order/hidden，**不过滤** hidden；按 order 升序）。
 * 供需要「看到隐藏条目」的消费方使用（如面板切换器要渲染隐藏面板的开关按钮，
 * 并用 hidden 决定初始开关状态）。
 */
export function getAllSlotEntries(slotId: string): SlotEntry[] {
  const entries = [...(slots.get(slotId)?.values() ?? [])];
  const cfg = profile[slotId] ?? [];
  return entries
    .map((e) => {
      const c = cfg.find((c) => c.id === e.id);
      return {
        ...e,
        order: c?.order ?? e.order ?? 100,
        hidden: c?.hidden ?? e.hidden ?? false,
      };
    })
    .sort((a, b) => (a.order ?? 100) - (b.order ?? 100));
}

/**
 * 读取插槽内按序排列的组件条目：
 * - 应用 profile 的 order/hidden，profile 缺失的字段回退组件自身默认值；
 * - 过滤 hidden 条目（条目本身仍保留在注册表，保持「可配置」）；
 * - 按 order 升序排序。
 */
export function getSlotEntries(slotId: string): SlotEntry[] {
  return getAllSlotEntries(slotId).filter((e) => !e.hidden);
}

/**
 * 运行时向当前 profile 追加一条插槽配置（T4.2 动态装载）。
 * 用于「动态挂入的组件」在 profile 清单型消费方（如 PanelSwitcher 的
 * getManifestEntries 白名单）中可见；同 id 已存在时幂等跳过。
 * 注意：reloadProfile() 会整体替换 profile，动态追加的条目会随 reload 消失
 * （组件仍留在注册表，需重新追加/挂载）。
 */
export function extendProfile(slotId: string, item: SlotProfileItem): void {
  const cfg = profile[slotId] ?? [];
  if (cfg.some((c) => c.id === item.id)) return;
  profile = { ...profile, [slotId]: [...cfg, item] };
}

/**
 * 读取插槽内「profile 清单」条目（面板切换器专用，任务 T2.3）：
 * - 应用 profile 的 order/hidden，不过滤 hidden（切换器需要渲染每个按钮，
 *   hidden 仅决定「默认关闭」）；
 * - 仅返回 profile 数组中声明的条目 —— 从 profile.json 移除某面板条目，
 *   切换器就不再显示该按钮（验收项「修改 profile.json 隐藏某面板」）；
 * - profile 未配置该插槽时回退全部已挂载条目（无 profile 默认挂载）。
 */
export function getManifestEntries(slotId: string): SlotEntry[] {
  const all = getAllSlotEntries(slotId);
  const cfg = profile[slotId];
  if (!cfg) return all;
  const ids = new Set(cfg.map((c) => c.id));
  return all.filter((e) => ids.has(e.id));
}

/**
 * 归一化 profile 输入（T2.4）：
 * - 兼容两种形状：profile.json 的容器 { slots: { ... } }，或注册表平铺 { slotId: [...] }；
 * - 容器形状下 slots 必须是对象，否则整体视为无效（返回 null，调用方回退默认）；
 * - 逐槽逐条目校验：非法插槽/条目跳过并 warn，不抛错；
 * - 整体不是对象（null/数组/字符串…）时返回 null（调用方回退 DEFAULT_PROFILE）。
 */
export function normalizeProfile(input: unknown): SlotProfile | null {
  if (typeof input !== 'object' || input === null || Array.isArray(input)) return null;
  const obj = input as Record<string, unknown>;
  // 有 "slots" 键 → 容器形状：slots 必须是纯对象，否则结构无效
  if ('slots' in obj) {
    const container = obj.slots;
    if (typeof container !== 'object' || container === null || Array.isArray(container)) {
      return null;
    }
    return normalizeSlotMap(container as Record<string, unknown>);
  }
  return normalizeSlotMap(obj);
}

/** 归一化平铺的 slotId -> 条目数组 映射（逐项校验，非法项跳过并 warn） */
function normalizeSlotMap(raw: Record<string, unknown>): SlotProfile {
  const result: SlotProfile = {};
  for (const [slotId, list] of Object.entries(raw)) {
    if (!Array.isArray(list)) {
      console.warn(`[slotRegistry] profile 插槽 "${slotId}" 的配置不是数组，已忽略该插槽`, list);
      continue;
    }
    const items: SlotProfileItem[] = [];
    for (const item of list) {
      if (typeof item !== 'object' || item === null || Array.isArray(item)) {
        console.warn(`[slotRegistry] profile 插槽 "${slotId}" 含无效条目（非对象），已跳过`, item);
        continue;
      }
      const e = item as Record<string, unknown>;
      if (typeof e.id !== 'string' || e.id.length === 0) {
        console.warn(`[slotRegistry] profile 插槽 "${slotId}" 含缺失 id 的条目，已跳过`, e);
        continue;
      }
      items.push({
        id: e.id,
        ...(typeof e.order === 'number' ? { order: e.order } : {}),
        ...(typeof e.hidden === 'boolean' ? { hidden: e.hidden } : {}),
      });
    }
    result[slotId] = items;
  }
  return result;
}

/** 校验 profile 是否声明了「未挂载的条目」——忽略不渲染，仅 warn 一次（不报错） */
function warnUnknownDeclared(n: SlotProfile): void {
  for (const [slotId, list] of Object.entries(n)) {
    const mounted = slots.get(slotId);
    if (!mounted) continue;
    for (const c of list) {
      if (!mounted.has(c.id)) {
        const key = `${slotId}:${c.id}`;
        if (!warnedUnknown.has(key)) {
          warnedUnknown.add(key);
          console.warn(`[slotRegistry] profile 声明了未挂载的条目 "${slotId}.${c.id}"（已忽略，不渲染）`);
        }
      }
    }
  }
}

/**
 * 应用 profile（整体替换当前配置）。
 * 输入无效（非对象/结构非法）时静默回退 DEFAULT_PROFILE（仅 warn）。
 */
export function loadProfile(p: SlotProfile | unknown): void {
  const n = normalizeProfile(p);
  if (n) {
    profile = n;
    warnUnknownDeclared(n);
  } else {
    console.warn('[slotRegistry] loadProfile 收到无效输入，已回退 DEFAULT_PROFILE', p);
    profile = DEFAULT_PROFILE;
  }
}

/**
 * 从原始 JSON 文本加载 profile（T2.4）：
 * JSON 语法错误 / 结构无效 → warn 并回退 DEFAULT_PROFILE，返回 false；
 * 成功 → 应用并返回 true。
 */
export function loadProfileFromRaw(raw: string): boolean {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch (err) {
    console.warn('[slotRegistry] profile 解析失败（JSON 语法错误），已回退 DEFAULT_PROFILE', err);
    profile = DEFAULT_PROFILE;
    return false;
  }
  const n = normalizeProfile(parsed);
  if (!n) {
    console.warn('[slotRegistry] profile 结构无效，已回退 DEFAULT_PROFILE', parsed);
    profile = DEFAULT_PROFILE;
    return false;
  }
  profile = n;
  warnUnknownDeclared(n);
  return true;
}

/**
 * 运行时重载 profile（T2.4）：
 * - 默认重新加载 'profile.json'（SlotProvider 挂载时调用）；
 * - 可传变体文件名（如 'profile.alt.json'）做运行时切换，供调试/后续动态装载；
 * - 变体文件缺失 / 加载失败 → warn 并回退 DEFAULT_PROFILE，返回 false；
 * - 成功应用返回 true。注意：注册表非响应式，调用方需自行触发重渲染
 *   （SlotProvider 在加载完成后 force 一次）。
 */
export async function reloadProfile(variant = 'profile.json'): Promise<boolean> {
  const loader = profileLoaders[`./${variant}`];
  if (!loader) {
    console.warn(`[slotRegistry] reloadProfile: 未找到 profile 变体 "${variant}"，已回退 DEFAULT_PROFILE`);
    profile = DEFAULT_PROFILE;
    return false;
  }
  try {
    const raw = await loader();
    return loadProfileFromRaw(raw);
  } catch (err) {
    console.warn(`[slotRegistry] reloadProfile: 加载 "${variant}" 失败，已回退 DEFAULT_PROFILE`, err);
    profile = DEFAULT_PROFILE;
    return false;
  }
}

/** 读取当前 profile（未加载时为 DEFAULT_PROFILE） */
export function getProfile(): SlotProfile {
  return profile;
}
