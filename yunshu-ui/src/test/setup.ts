/**
 * Vitest 全局测试 setup（当前项目）
 * 注：旧版 src/test/ 已随旧 App 归档至 legacy/，此处重建当前工程所需 setup。
 */
import '@testing-library/jest-dom';
import { createMemoryStorage } from './utils/memoryStorage';

// 确保 import.meta.env.DEV 在测试中为 true（vitest 默认即如此，显式声明便于理解）
if (import.meta.env.DEV === undefined) {
  (import.meta.env as unknown as { DEV: boolean }).DEV = true;
}

// 【Why】Node 22 实验性 Web Storage 会注入缺 clear 等方法的 stub，覆盖 jsdom 原生实现，
// 统一替换为内存版 Storage（实现见 src/test/utils/memoryStorage.ts），保证 persist 测试语义完整。
if (typeof localStorage === 'undefined' || typeof localStorage.clear !== 'function') {
  const memoryStorage = createMemoryStorage();
  try {
    Object.defineProperty(globalThis, 'localStorage', { value: memoryStorage, configurable: true });
  } catch {
    // 非可配置属性时退化为直接赋值（仅测试环境）
    (globalThis as Record<string, unknown>).localStorage = memoryStorage;
  }
}
