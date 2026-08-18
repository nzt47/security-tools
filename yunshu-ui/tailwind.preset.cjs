/**
 * 云枢 · 玻璃拟态 + 科技感主题预设（TailwindCSS Preset · CJS）
 * =========================================================
 * 独立配置片段，直接应用到整个项目：
 *
 *   // tailwind.config.js（ESM）
 *   import preset from './tailwind.preset.cjs';
 *   export default { presets: [preset], content: [...] };
 *
 * 提供三类资产：
 *  1. 语义色板 yunsu-*（与 styles/workbench.css 的 --wb-* 变量同源）
 *  2. 字体 / 光效 / 科技字距令牌
 *  3. 玻璃拟态工具类（.glass-panel / .glass-border / .text-gradient-tech 等）
 * 深色基底建议在全局样式补：body { background: #04060d; color: #e2e8f0; }
 */

/** 云枢色板（青-天蓝主色，避免常见紫渐变套路） */
const yunsuColors = {
  bg: "#04060d",                                // 深空底色
  "bg-2": "#0a101f",                            // 次底色
  panel: "rgba(13, 18, 32, 0.55)",              // 玻璃面板
  "panel-strong": "rgba(10, 14, 26, 0.72)",
  border: "rgba(148, 163, 184, 0.14)",
  "border-strong": "rgba(34, 211, 238, 0.35)",
  accent: "#22d3ee",                            // 主强调色（青）
  "accent-2": "#38bdf8",                        // 次强调色（天蓝）
  text: "#e2e8f0",
  dim: "#64748b",
};

module.exports = {
  darkMode: "class",
  theme: {
    extend: {
      colors: { yunsu: yunsuColors },
      fontFamily: {
        display: ['"Chakra Petch"', '"Noto Sans SC"', "system-ui", "sans-serif"],
        mono: ['"JetBrains Mono"', "ui-monospace", "Menlo", "monospace"],
      },
      letterSpacing: {
        tech: "0.14em", // 科技感标题字距
      },
      boxShadow: {
        glass: "0 8px 32px rgba(0, 0, 0, 0.35), inset 0 1px 0 rgba(255, 255, 255, 0.04)",
        glow: "0 0 18px rgba(34, 211, 238, 0.18)",
        "glow-strong": "0 0 24px rgba(34, 211, 238, 0.28)",
      },
    },
  },
  plugins: [
    // 玻璃拟态组件类：直接用 className="glass-panel" 即可获得毛玻璃面板
    function glassPreset({ addComponents }) {
      addComponents({
        // 毛玻璃面板：半透明 + 背景模糊 + 光边
        ".glass-panel": {
          borderRadius: "12px",
          border: "1px solid rgba(148, 163, 184, 0.14)",
          background: "rgba(13, 18, 32, 0.55)",
          backdropFilter: "blur(14px)",
          WebkitBackdropFilter: "blur(14px)",
          boxShadow: "0 8px 32px rgba(0, 0, 0, 0.35), inset 0 1px 0 rgba(255, 255, 255, 0.04)",
        },
        // 仅玻璃描边（用于容器内嵌卡片）
        ".glass-border": {
          borderRadius: "10px",
          border: "1px solid rgba(34, 211, 238, 0.25)",
          background: "rgba(10, 14, 26, 0.5)",
        },
        // 青色科技渐变文字
        ".text-gradient-tech": {
          fontWeight: "600",
          background: "linear-gradient(90deg, #a5f3fc, #7dd3fc)",
          WebkitBackgroundClip: "text",
          backgroundClip: "text",
          color: "transparent",
        },
        // 顶部深空渐变背景
        ".bg-deep-space": {
          backgroundImage: [
            "radial-gradient(1100px 520px at 8% -8%, rgba(56,189,248,0.14), transparent 60%)",
            "radial-gradient(900px 480px at 105% 15%, rgba(34,211,238,0.10), transparent 55%)",
            "linear-gradient(180deg, #04060d, #0a101f)",
          ].join(","),
        },
        // 青色辉光强调（状态点 / 激活元素）
        ".glow-accent": {
          boxShadow: "0 0 18px rgba(34, 211, 238, 0.18)",
        },
        // 科技感细滚动条
        ".scrollbar-tech": {
          scrollbarWidth: "thin",
          scrollbarColor: "rgba(148, 163, 184, 0.25) transparent",
          "&::-webkit-scrollbar": { width: "6px" },
          "&::-webkit-scrollbar-thumb": {
            borderRadius: "999px",
            background: "rgba(148, 163, 184, 0.25)",
          },
        },
      });
    },
  ],
};
