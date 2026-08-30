/**
 * SlotProvider —— 挂载时加载 profile（任务 T2.1；T2.4 profile 驱动完善）
 *
 * - 注册表初始即为代码内 DEFAULT_PROFILE（回退兜底），首帧渲染始终完整；
 * - 挂载时异步 reloadProfile()：profile.json 缺失/解析失败/结构无效时
 *   内部静默回退 DEFAULT_PROFILE，任何配置异常只 warn 不抛错；
 * - 加载完成后触发一次重渲染，让 SlotHost / PanelSwitcher 按新 profile 组装
 *   （注册表非响应式，需显式 force）；
 * - 运行时可用 reloadProfile('profile.alt.json') 切换变体（供调试/后续动态装载）。
 *
 * 说明：profile 在**模块初始化**即落到 DEFAULT_PROFILE（与 profile.json 内容
 * 一致），因此即使异步加载，首帧布局/面板初始显隐也已正确，无「回退→校正」闪动。
 */
import React, { useEffect, useReducer } from 'react';
import { reloadProfile } from './slotRegistry';

export function SlotProvider({ children }: { children: React.ReactNode }) {
  // profile 异步加载完成后 force 一次重渲染（幂等；StrictMode 双调用/HMR 均安全）
  const [, force] = useReducer((x: number) => x + 1, 0);

  useEffect(() => {
    let cancelled = false;
    void reloadProfile().then(() => {
      if (!cancelled) force();
    });
    return () => {
      cancelled = true;
    };
  }, []);

  return <>{children}</>;
}
