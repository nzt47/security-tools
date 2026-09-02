import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tsconfigPaths from "vite-tsconfig-paths";
import { traeBadgePlugin } from 'vite-plugin-trae-solo-badge';
import { mockApiPlugin, mockDemoPlugin } from './src/mocks/devMock';

// https://vite.dev/config/
export default defineConfig({
  base: '/static/',
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
    // 管理后台本地接口 Mock（mock 中间件先于 proxy 执行，命中 /auth/login、/user/info
    // 等管理 API 时不再转发后端）：VITE_MOCK_API=true 时启用；false 走真实后端
    ...(process.env.VITE_MOCK_API === 'true' ? [mockApiPlugin({ loginReturnUser: process.env.VITE_MOCK_LOGIN_RETURN_USER !== 'false' })] : []),
    // 组件演示/导出接口（/api/demo/*、/api/export/users）：后端无此路由，dev 下始终启用
    mockDemoPlugin(),
  ],
})
