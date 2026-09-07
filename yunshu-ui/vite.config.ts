import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tsconfigPaths from "vite-tsconfig-paths";
import electron from 'vite-plugin-electron/simple';
import { mockApiPlugin, mockDemoPlugin } from './src/mocks/devMock';

/**
 * 云枢 · 双模式构建（Electron 模式自 2026-09-07 接入 master）
 * ------------------------------------------------
 * Web 模式（默认）：base=/static/，供 Flask 同域部署；不带 Electron 壳。
 * Electron 模式（ELECTRON=1）：
 *   - base='./'：生产以 file:// 加载 dist/index.html 时资源走相对路径
 *   - 启用 vite-plugin-electron：构建主进程/预加载并产出 dist-electron
 * 启动方式：
 *   Web      : npm run dev / npm run build
 *   Electron : $env:ELECTRON="1"; npm run dev   （dev 由插件自动拉起 Electron）
 *
 * 本地 Mock：.env 中 VITE_MOCK_API=true 时启用，拦截登录/用户信息接口（仅 dev）。
 */
// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  const isElectron = !!process.env.ELECTRON;

  return {
    base: isElectron ? './' : '/static/',
    build: {
      sourcemap: 'hidden',
      // Code Splitting：第三方大库抽独立 vendor chunk，利用浏览器长期缓存
      rollupOptions: {
        output: {
          manualChunks: {
            'vendor-react': ['react', 'react-dom', 'react-router-dom', 'zustand'],
            'vendor-mosaic': ['react-mosaic-component'],
            'vendor-markdown': ['react-markdown', 'remark-gfm', 'rehype-highlight', 'highlight.js'],
            'vendor-anim': ['framer-motion'],
            'vendor-charts': ['recharts'],
            'vendor-http': ['axios'],
          },
        },
      },
    },
    server: {
      // 强制监听 IPv4 127.0.0.1：Vite 默认只绑 IPv6 ::1，会导致
      // http://127.0.0.1:5173 连接被拒（localhost 解析到 IPv4 时同样失败）
      host: '127.0.0.1',
      // 将 /api 请求代理到后端 Flask 服务（端口 5678）
      // 解决前端 dev server (5173) 与后端 API 跨域问题
      proxy: {
        '/api': {
          target: 'http://127.0.0.1:5678',
          changeOrigin: true,
        },
      },
    },
    plugins: [
      react({
        babel: {
          plugins: [
            'react-dev-locator',
          ],
        },
      }),
      tsconfigPaths(),
      // 管理后台本地接口 Mock（mock 中间件先于 proxy 执行，命中 /auth/login、/user/info
      // 等管理 API 时不再转发后端）：VITE_MOCK_API=true 时启用；false 走真实后端
      ...(env.VITE_MOCK_API === 'true' ? [mockApiPlugin({ loginReturnUser: env.VITE_MOCK_LOGIN_RETURN_USER !== 'false' })] : []),
      // 组件演示/导出接口（/api/demo/*、/api/export/users）：后端无此路由，dev 下始终启用
      mockDemoPlugin(),
      // Electron 壳仅在 ELECTRON=1 时启用；Web 构建/开发完全不受影响
      ...(isElectron
        ? [
            electron({
              main: {
                entry: 'electron/main.ts',
              },
              preload: {
                input: 'electron/preload.ts',
                // preload 必须以 CJS 格式输出：Electron 按 ESM 加载 .mjs 时 require 未定义，
                // 会导致 preload 崩溃、electronAPI 注入失败（安装版"独立窗口"功能不可用）。
                vite: {
                  build: {
                    rollupOptions: {
                      output: {
                        format: 'cjs',
                        entryFileNames: '[name].cjs',
                      },
                    },
                  },
                },
              },
              renderer: {},
            }),
          ]
        : []),
    ],
  };
});
