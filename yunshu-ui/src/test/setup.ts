/**
 * Vitest 全局测试 setup
 */
import '@testing-library/jest-dom';
import { createMemoryStorage } from './utils/memoryStorage';

// 确保 import.meta.env.DEV 在测试中为 true（vitest 默认即如此，显式声明便于理解）
if (import.meta.env.DEV === undefined) {
  (import.meta.env as { DEV: boolean }).DEV = true;
}

// Node 22 实验性 Web Storage 会注入缺 clear/setItem 等方法的 localStorage stub，
// 覆盖 jsdom 原生实现，导致 persist（zustand 中间件）相关测试失败。
// 统一替换为语义完整的内存 Storage（createMemoryStorage，见 memoryStorage.ts）。
if (typeof globalThis.localStorage === 'undefined' || typeof (globalThis.localStorage as Storage).setItem !== 'function') {
  const mem = createMemoryStorage();
  Object.defineProperty(globalThis, 'localStorage', { value: mem, configurable: true, writable: true });
  Object.defineProperty(globalThis, 'sessionStorage', { value: mem, configurable: true, writable: true });
}
if (typeof globalThis.window !== 'undefined') {
  const mem = createMemoryStorage();
  try {
    if (!window.localStorage || typeof window.localStorage.setItem !== 'function') {
      Object.defineProperty(window, 'localStorage', { value: mem, configurable: true });
      Object.defineProperty(window, 'sessionStorage', { value: mem, configurable: true });
    }
  } catch {
    // window 属性不可配置时忽略（globalThis 已兜底）
  }
}

// jsdom 无 ResizeObserver（Dashboard/图表容器依赖它）；Node 22 全局探测会与
// jsdom 的 window 作用域不一致，单文件内 vi.stubGlobal 在全量并发时偶发丢失，
// 因此在 setup 层统一打桩（同时覆盖 globalThis 与 window），保证全量运行稳定。
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
if (typeof globalThis.ResizeObserver === 'undefined') {
  Object.defineProperty(globalThis, 'ResizeObserver', {
    value: ResizeObserverStub,
    configurable: true,
    writable: true,
  });
}
if (typeof globalThis.window !== 'undefined' && typeof window.ResizeObserver === 'undefined') {
  Object.defineProperty(window, 'ResizeObserver', {
    value: ResizeObserverStub,
    configurable: true,
  });
}
