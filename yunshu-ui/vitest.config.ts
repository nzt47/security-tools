/**
 * Vitest 配置
 *
 * 复用 Vite 的 React 插件与路径别名，jsdom 环境支持 DOM API。
 * 覆盖率门槛 80%，对齐项目质量约束。
 */
import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import tsconfigPaths from 'vite-tsconfig-paths';

export default defineConfig({
  plugins: [react(), tsconfigPaths()],
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    css: false,
    coverage: {
      provider: 'v8',
      reporter: ['text', 'html', 'lcov'],
      include: [
        'src/config/observability.ts',
        'src/utils/requestInterceptor.ts',
        'src/components/DevConsole/**/*.ts',
        'src/components/DevConsole/**/*.tsx',
        'src/components/StateInspector/**/*.ts',
        'src/components/StateInspector/**/*.tsx',
        'src/components/ObservabilityDevtools/**/*.tsx',
      ],
      exclude: [
        'src/**/*.test.*',
        'src/**/*.d.ts',
        'src/test/**',
        'src/components/**/index.ts',
        'src/components/**/types.ts',
      ],
      thresholds: {
        lines: 80,
        functions: 80,
        branches: 70,
        statements: 80,
      },
    },
  },
});
