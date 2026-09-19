/// <reference types="vite/client" />

/**
 * 构建标记：由 vite.config.ts 的 define 注入（构建时刻 ISO 字符串）。
 * 用途：界面上显示"当前页面跑的是哪一次构建" —— 前端修复上线后若页面未刷新，
 * 用户会一直跑旧 bundle（表现为"改了没用/还是老问题"），有构建戳即可一眼分辨。
 * 开发态（vite dev）下 define 同样生效。
 */
declare const __YUNSHU_BUILD__: string;

interface Window {
  /** 同 __YUNSHU_BUILD__，供控制台/自动化探针读取 */
  __YUNSHU_BUILD__?: string;
}
