/**
 * Vitest 全局测试 setup（当前项目）
 * 注：旧版 src/test/ 已随旧 App 归档至 legacy/，此处重建当前工程所需 setup。
 */
import '@testing-library/jest-dom';

// 确保 import.meta.env.DEV 在测试中为 true（vitest 默认即如此，显式声明便于理解）
if (import.meta.env.DEV === undefined) {
  (import.meta.env as unknown as { DEV: boolean }).DEV = true;
}
