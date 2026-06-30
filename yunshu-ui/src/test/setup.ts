/**
 * Vitest 全局测试 setup
 */
import '@testing-library/jest-dom';

// 确保 import.meta.env.DEV 在测试中为 true（vitest 默认即如此，显式声明便于理解）
if (import.meta.env.DEV === undefined) {
  (import.meta.env as any).DEV = true;
}
