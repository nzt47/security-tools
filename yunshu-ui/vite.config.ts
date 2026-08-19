import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tsconfigPaths from "vite-tsconfig-paths";
import { traeBadgePlugin } from 'vite-plugin-trae-solo-badge';
import electron from 'vite-plugin-electron/simple';
import { mockApiPlugin, mockDemoPlugin } from './src/mocks/devMock';

/**
 * 云枢 · 双模式构建
 * ------------------------------------------------
 * Web 模式（默认）：base=/static/，供 Flask 同域部署；不带 Electron 壳。
 * Electron 模式（ELECTRON=1）：
 *   - base='./'：生产以 file:// 加载 dist/index.html 时资源走相对路径
 *   - 启用 vite-plugin-electron：构建主进程/预加载并产出 dist-electron
 * 启动方式：
 *   Web      : npm run dev / npm run build
 *   Electron : $env:ELECTRON=1; npm run dev  （dev 由插件自动拉起 Electron）
 *
 * 本地 Mock：.env 中 VITE_MOCK_API=true 时启用，拦截登录/用户信息接口（仅 dev）。
 */
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  const isElectron = !!process.env.ELECTRON;

  // https://vite.dev/config/
  return {
    base: isElectron ? './' : '/static/',
    build: {
      sourcemap: false, // 桌面版无线上排障需求，关闭以削减 asar 体积（原 'hidden' 仍产出 .map）
    },
    server: {
      // 端口：默认 5173，可用 .env 的 VITE_DEV_SERVER_PORT 覆盖（被占用时 Vite 自动 +1）
      port: Number(process.env.VITE_DEV_SERVER_PORT) || 5173,
      // 将 /api 请求代理到后端服务，解决前端 dev server (5173) 与后端 API 跨域问题
      // 目标默认本机 Flask 后端(5678)，可用 .env 的 VITE_API_PROXY_TARGET 覆盖
      proxy: {
        '/api': {
          target: env.VITE_API_PROXY_TARGET || 'http://127.0.0.1:5678',
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
      traeBadgePlugin({
        variant: 'dark',
        position: 'bottom-right',
        prodOnly: true,
        clickable: true,
        clickUrl: 'https://www.trae.ai/solo?showJoin=1',
        autoTheme: true,
        autoThemeTarget: '#root'
      }),
      tsconfigPaths(),
      // 本地接口 Mock（mock 中间件先于 proxy 执行，命中 /auth/login、/user/info 时不再转发后端）
      // 登录/用户鉴权接口：VITE_MOCK_API=true 时启用（无后端兜底）；false 走真实后端
      ...(env.VITE_MOCK_API === 'true' ? [mockApiPlugin()] : []),
      // 组件演示接口（/api/demo/*）：后端无此路由，dev 下始终启用，便于验证网络异常场景
      mockDemoPlugin(),
      // Electron 壳仅在 ELECTRON=1 时启用，Web 构建/开发完全不受影响
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
})
