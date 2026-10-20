/**
 * 插件与扩展 —— 工作台「会话与配置」桶下的单一页面
 * ------------------------------------------------------------------
 * 原为「清单与配置 / 扩展呈现」两个 Tab（UI IA 整合阶段 1.2），2026-10-15 合并为一页。
 * 2026-10-20：「扩展市场」Tab 整页删除（用户批准）—— 已安装项的启停/卸载/配置已
 * 回到各类型归属页（插件包 → 本页；扩展技能 → 技能中心；MCP → 工具调用·MCP 系统；
 * 通道 → 系统管理·消息中心），本页只保留插件清单（/api/plugins 的 manifest +
 * schema 配置 + 插件自带界面）。仍只有一个导航入口（hubNav 的 components/plugins）。
 */
import PluginManage from '../plugin-manage'

export default function PluginsPage() {
  return <PluginManage />
}
