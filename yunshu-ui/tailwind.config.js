/** @type {import('tailwindcss').Config} */

export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    container: {
      center: true,
    },
    extend: {
      // ── 语义色 Token（工作台深色基线）──────────────────────────────
      // 组件一律用 bg-card / text-foreground / border-border 这类**语义类**；
      // 具体取值由 src/index.css 的 :root CSS 变量给出（hsl 分量 + <alpha-value>，
      // 因此 bg-card/50 这类透明度修饰符同样可用）。
      //
      // 【2026-10-15】这些语义色此前**只在 TSX 里被引用、配置里从未定义** ⇒
      // bg-card / text-foreground / border-border / bg-primary / text-danger …
      // 全部是空类（Tailwind 生不出对应 CSS）。系统管理段（components/ui 组件 +
      // system/* 页面）因此没有卡片底色与边框，白底则来自另一批硬编码类。
      // 本次把语义色按本仓注释所称的"tailwind.config.js / index.css 单一来源"补齐，
      // 并同步把硬编码浅色页改为深色（见 pages/Dashboard、pages/Export、pages/system/*）。
      colors: {
        background: 'hsl(var(--background) / <alpha-value>)',
        foreground: 'hsl(var(--foreground) / <alpha-value>)',
        border: 'hsl(var(--border) / <alpha-value>)',
        overlay: 'hsl(var(--overlay) / <alpha-value>)',
        card: {
          DEFAULT: 'hsl(var(--card) / <alpha-value>)',
          foreground: 'hsl(var(--card-foreground) / <alpha-value>)',
        },
        muted: {
          DEFAULT: 'hsl(var(--muted) / <alpha-value>)',
          foreground: 'hsl(var(--muted-foreground) / <alpha-value>)',
        },
        secondary: {
          DEFAULT: 'hsl(var(--secondary) / <alpha-value>)',
          foreground: 'hsl(var(--secondary-foreground) / <alpha-value>)',
        },
        primary: {
          DEFAULT: 'hsl(var(--primary) / <alpha-value>)',
          foreground: 'hsl(var(--primary-foreground) / <alpha-value>)',
        },
        danger: {
          DEFAULT: 'hsl(var(--danger) / <alpha-value>)',
          foreground: 'hsl(var(--danger-foreground) / <alpha-value>)',
        },
        success: 'hsl(var(--success) / <alpha-value>)',
      },
      boxShadow: {
        card: '0 1px 2px 0 rgb(0 0 0 / 0.4), 0 1px 3px 0 rgb(0 0 0 / 0.3)',
      },
    },
  },
  plugins: [],
};
