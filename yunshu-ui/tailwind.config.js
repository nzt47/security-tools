/** @type {import('tailwindcss').Config} */
/**
 * 云枢 · TailwindCSS 配置
 * ------------------------------------------------
 * 1. tailwind.preset.cjs：工作台既有「玻璃拟态 + 科技感」令牌（yunsu-* / glass-*），保持不动。
 * 2. 下方 extend 追加全局「语义 Token」层（深浅双模式）：
 *    - 色值定义在 src/index.css（:root = 浅色 / .dark = 深色），组件只引用语义类名。
 *    - 组件一律走语义色，严禁硬编码色值（规则见 frontend-rules.md）。
 */
import preset from './tailwind.preset.cjs';

export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  presets: [preset],
  theme: {
    container: {
      center: true,
    },
    extend: {
      // 语义色（RGB 三元组定义在 index.css，/ <alpha-value> 支持 bg-primary/90 这类透明度写法）
      colors: {
        background: "rgb(var(--background) / <alpha-value>)", // 页面底色
        foreground: "rgb(var(--foreground) / <alpha-value>)", // 主文本
        card: {
          DEFAULT: "rgb(var(--card) / <alpha-value>)",         // 卡片底色
          foreground: "rgb(var(--card-foreground) / <alpha-value>)", // 卡片内文本
        },
        primary: {
          DEFAULT: "rgb(var(--primary) / <alpha-value>)",      // 主操作色（蓝）
          foreground: "rgb(var(--primary-foreground) / <alpha-value>)",
        },
        muted: {
          DEFAULT: "rgb(var(--muted) / <alpha-value>)",        // 次级填充（hover / 次级容器）
          foreground: "rgb(var(--muted-foreground) / <alpha-value>)", // 次级文本
        },
        danger: {
          DEFAULT: "rgb(var(--danger) / <alpha-value>)",       // 危险操作
          foreground: "rgb(var(--danger-foreground) / <alpha-value>)",
        },
        success: {
          DEFAULT: "rgb(var(--success) / <alpha-value>)",      // 成功提示
          foreground: "rgb(var(--success-foreground) / <alpha-value>)",
        },
        border: "rgb(var(--border) / <alpha-value>)",          // 统一描边
        overlay: "rgb(var(--overlay) / <alpha-value>)",        // 弹窗遮罩（常用 /50）
      },
      // 统一圆角：控件 rounded-md（6px）/ 容器 rounded-lg（8px）
      borderRadius: {
        md: "0.375rem",
        lg: "0.5rem",
      },
      // 统一卡片阴影（深浅两套值见 index.css 的 --shadow-card / --shadow-card-hover）
      boxShadow: {
        card: "var(--shadow-card)",
        "card-hover": "var(--shadow-card-hover)",
      },
    },
  },
  plugins: [],
};
