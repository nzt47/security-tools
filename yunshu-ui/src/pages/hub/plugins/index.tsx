/**
 * 插件与扩展 —— 工作台「会话与配置」桶下的单一页面
 * ------------------------------------------------------------------
 * 原为「清单与配置 / 扩展呈现」两个 Tab（UI IA 整合阶段 1.2）。2026-10-15 合并为一页：
 *   · 两者同读 /api/plugins，「扩展呈现」的 schema 配置部分与「清单与配置」完全重复；
 *   · 「扩展呈现」唯一独有能力是自动挂载插件自带 UI（client_slot），已并入清单页的
 *     插件卡片（见 ../plugin-manage.tsx），extensions.tsx 随之删除；
 *   · 仍只有一个导航入口（hubNav 的 components/plugins），禁止双入口。
 */
import PluginManage from '../plugin-manage'

export default function PluginsPage() {
  return <PluginManage />
}
