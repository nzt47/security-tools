import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'

export default tseslint.config(
  { ignores: ['dist', 'coverage'] },
  {
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    files: ['**/*.{ts,tsx}'],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
    },
    plugins: {
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      // 【2026-09-02】桶/工具文件普遍"同文件导出组件 + 函数/常量"（如 ui.tsx 导出
      // Card 组件与 hubGet、Toaster 导出 toast API 与组件）——Fast Refresh 对这类
      // 文件降级为整页刷新，收益低、警告噪音高，规则关闭（allowConstantExport 亦不足）。
      'react-refresh/only-export-components': 'off',
      '@typescript-eslint/no-explicit-any': 'warn',
      // 【2026-08-31 存量 lint 清理】`_` 前缀 = 有意命名但未使用的参数/变量
      // （桩实现、占位回调、catch 参数等惯用法），显式豁免避免逐行 disable。
      '@typescript-eslint/no-unused-vars': [
        'warn',
        {
          argsIgnorePattern: '^_',
          varsIgnorePattern: '^_',
          caughtErrorsIgnorePattern: '^_',
          ignoreRestSiblings: true,
        },
      ],
    },
  },
  {
    // 【2026-08-31 存量 lint 清理】测试代码允许 any：断言/夹具/模拟中松类型是
    // 测试惯用法，逐行治理收益低、噪声高；源码（src/** 非测试）仍严格。
    files: [
      '**/*.test.{ts,tsx}',
      '**/__tests__/**/*.{ts,tsx}',
      '**/*.test-d.{ts,tsx}',
    ],
    rules: {
      '@typescript-eslint/no-explicit-any': 'off',
    },
  },
)
