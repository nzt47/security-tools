"""扩展管理器 — 统一的扩展安装、管理、协调入口

ExtensionManager 是云枢扩展系统的核心外观类，
将所有类型的扩展安装器整合为一个统一的调用接口。

使用方式:
    from agent.extensions.manager import ExtensionManager
    mgr = ExtensionManager()

    # 安装一个技能
    mgr.install("skill", "self_reflection")

    # 安装一个 MCP 服务
    mgr.install("mcp", "filesystem")

    # 列出所有已安装扩展
    mgr.list_all()

    # 通过对话告知云枢：
    # "请安装 GitHub MCP 服务，让我能操作 GitHub"
    # → 内部调用 mgr.install("mcp", "github")
"""

import logging
import json
import uuid
from typing import Optional, Dict, Any, List, Tuple

from agent.extensions.base import ExtensionType, ExtensionStatus, BUILTIN_EXTENSIONS
from agent.extensions.store import ExtensionStore
from agent.extensions.skills_installer import SkillsInstaller
from agent.extensions.mcp_installer import McpInstaller
from agent.extensions.channels_installer import ChannelInstaller
from agent.extensions.plugins_installer import PluginInstaller
from agent.logging_utils import log_dict

logger = logging.getLogger(__name__)

def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]



class ExtensionManager:
    """扩展管理器 — 统一安装、配置、管理所有扩展类型"""

    def __init__(self, network_config_mgr=None):
        """初始化扩展管理器

        Args:
            network_config_mgr: NetworkConfigManager 实例（可选，用于 MCP 服务管理）
        """
        self._store = ExtensionStore()
        self._network_config_mgr = network_config_mgr

        # 类型安装器
        self._installers: Dict[ExtensionType, Any] = {}

        # 工具注册回调（由数字生命设置，用于插件自动注册工具）
        self._tool_register_fn = None
        self._tool_unregister_fn = None

    def _get_installer(self, ext_type: ExtensionType):
        """获取或创建指定类型的安装器"""
        if ext_type not in self._installers:
            if ext_type in (ExtensionType.SKILL, ExtensionType.CLAUDE_SKILL):
                self._installers[ext_type] = SkillsInstaller(self._store)
            elif ext_type == ExtensionType.MCP:
                inst = McpInstaller(self._store, self._network_config_mgr)
                self._installers[ext_type] = inst
            elif ext_type == ExtensionType.CHANNEL:
                self._installers[ext_type] = ChannelInstaller(self._store)
            elif ext_type == ExtensionType.PLUGIN:
                self._installers[ext_type] = PluginInstaller(
                    self._store,
                    tool_register_fn=self._tool_register_fn,
                    tool_unregister_fn=self._tool_unregister_fn,
                )

        return self._installers[ext_type]

    def set_network_config_mgr(self, network_config_mgr):
        """设置网络配置管理器（懒注入，用于初始化顺序管理）"""
        self._network_config_mgr = network_config_mgr
        mcp_inst = self._installers.get(ExtensionType.MCP)
        if mcp_inst:
            mcp_inst.set_network_config_mgr(network_config_mgr)

    # ════════════════════════════════════════════════════════════
    # 统一安装接口
    # ════════════════════════════════════════════════════════════

    def install(
        self, ext_type: str, ext_id_or_source: str,
        **kwargs
    ) -> Dict:
        """安装扩展（统一入口）

        Args:
            ext_type: 扩展类型 ("skill", "claude_skill", "mcp", "channel", "plugin")
            ext_id_or_source: 扩展 ID 或安装来源
            **kwargs: 类型特定的额外参数

        Returns:
            {"ok": bool, "message": str, ...}
        """
        try:
            etype = ExtensionType(ext_type)
        except ValueError:
            return {"ok": False, "message": f"未知扩展类型: {ext_type}"}

        installer = self._get_installer(etype)

        try:
            if etype == ExtensionType.SKILL:
                # 先尝试内置技能
                success, message = installer.add_builtin_skill(ext_id_or_source)
                if not success:
                    # 只有提供了自定义名称才创建自定义技能，
                    # 避免 LLM 传错 ID 时产生幽灵技能
                    raw_name = kwargs.get("name") or ""
                    if raw_name.strip():
                        name = raw_name
                        desc = kwargs.get("description", "")
                        params = kwargs.get("params", {})
                        success, message = installer.add_custom_skill(
                            ext_id_or_source, name, desc, params
                        )
                    # 未提供名称时保留 add_builtin_skill 返回的错误信息
                return {"ok": success, "message": message, "type": "skill"}

            elif etype == ExtensionType.CLAUDE_SKILL:
                success, message = installer.install_claude_skill(
                    ext_id_or_source,
                    skill_name=kwargs.get("name"),
                )
                return {"ok": success, "message": message, "type": "claude_skill"}

            elif etype == ExtensionType.MCP:
                # 先尝试内置 MCP
                success, message = installer.install_builtin_mcp(ext_id_or_source)
                if not success:
                    # 再从来源安装
                    success, message = installer.install_mcp_from_source(ext_id_or_source)
                return {"ok": success, "message": message, "type": "mcp"}

            elif etype == ExtensionType.CHANNEL:
                # 先尝试内置通道
                success, message = installer.install_builtin_channel(ext_id_or_source)
                if not success:
                    # 手动安装
                    channel_type = kwargs.get("channel_type", "webhook")
                    name = kwargs.get("name", ext_id_or_source)
                    desc = kwargs.get("description", "")
                    config = kwargs.get("config", {})
                    success, message = installer.install_channel(
                        ext_id_or_source, name, channel_type, desc, config
                    )
                return {"ok": success, "message": message, "type": "channel"}

            elif etype == ExtensionType.PLUGIN:
                success, message = installer.install_plugin(ext_id_or_source)
                return {"ok": success, "message": message, "type": "plugin"}

        except Exception as e:
            logger.error(log_dict({'module_name': 'manager', 'action': 'ext_type.ext_id_or_source', 'msg': f'[扩展管理器] 安装失败: {ext_type}/{ext_id_or_source}: {e}'}))
            return {"ok": False, "message": f"安装失败: {e}", "type": ext_type}

    # ════════════════════════════════════════════════════════════
    # 统一管理接口
    # ════════════════════════════════════════════════════════════

    def uninstall(self, ext_type: str, ext_id: str) -> Dict:
        """卸载扩展"""
        try:
            etype = ExtensionType(ext_type)
        except ValueError:
            return {"ok": False, "message": f"未知扩展类型: {ext_type}"}

        installer = self._get_installer(etype)

        try:
            if etype == ExtensionType.SKILL:
                success, msg = installer.remove_skill(ext_id)
            elif etype == ExtensionType.CLAUDE_SKILL:
                success, msg = installer.uninstall_claude_skill(ext_id)
            elif etype == ExtensionType.MCP:
                success, msg = installer.uninstall_mcp(ext_id)
            elif etype == ExtensionType.CHANNEL:
                success, msg = installer.uninstall_channel(ext_id)
            elif etype == ExtensionType.PLUGIN:
                success, msg = installer.uninstall_plugin(ext_id)
            else:
                return {"ok": False, "message": f"不支持的类型: {ext_type}"}

            return {"ok": success, "message": msg, "type": ext_type}
        except Exception as e:
            logger.error(log_dict({'module_name': 'manager', 'action': 'ext_type.ext_id', 'msg': f'[扩展管理器] 卸载失败: {ext_type}/{ext_id}: {e}'}))
            return {"ok": False, "message": f"卸载失败: {e}"}

    def toggle(self, ext_type: str, ext_id: str, enabled: bool = None) -> Dict:
        """切换扩展启用/禁用状态"""
        try:
            etype = ExtensionType(ext_type)
        except ValueError:
            return {"ok": False, "message": f"未知扩展类型: {ext_type}"}

        installer = self._get_installer(etype)

        try:
            if etype == ExtensionType.SKILL:
                success, msg, state = installer.toggle_skill(ext_id, enabled)
            elif etype == ExtensionType.CLAUDE_SKILL:
                # Claude 技能通过文件系统管理，暂不支持运行时切换
                return {"ok": False, "message": "Claude Code 技能暂不支持运行时切换"}
            elif etype == ExtensionType.MCP:
                success, msg, state = installer.toggle_mcp(ext_id, enabled)
            elif etype == ExtensionType.CHANNEL:
                success, msg, state = installer.toggle_channel(ext_id, enabled)
            elif etype == ExtensionType.PLUGIN:
                success, msg, state = installer.toggle_plugin(ext_id, enabled)
            else:
                return {"ok": False, "message": f"不支持的类型: {ext_type}"}

            return {"ok": success, "message": msg, "enabled": state, "type": ext_type}
        except Exception as e:
            logger.error(log_dict({'module_name': 'manager', 'action': 'ext_type.ext_id', 'msg': f'[扩展管理器] 切换状态失败: {ext_type}/{ext_id}: {e}'}))
            return {"ok": False, "message": f"操作失败: {e}"}

    def configure(self, ext_type: str, ext_id: str, config: Dict) -> Dict:
        """配置扩展参数"""
        try:
            etype = ExtensionType(ext_type)
        except ValueError:
            return {"ok": False, "message": f"未知扩展类型: {ext_type}"}

        installer = self._get_installer(etype)

        try:
            if etype == ExtensionType.SKILL:
                success, msg = installer.update_skill_params(ext_id, config)
            elif etype == ExtensionType.MCP:
                # MCP 配置更新通过 NetworkConfigManager
                if self._network_config_mgr:
                    self._network_config_mgr.update_mcp_service(ext_id, config)
                    success, msg = True, "已更新 MCP 服务配置"
                else:
                    success, msg = False, "网络配置管理器未初始化"
            elif etype == ExtensionType.CHANNEL:
                success, msg = installer.configure_channel(ext_id, config)
            elif etype == ExtensionType.PLUGIN:
                success, msg = self._store.update_config(etype, ext_id, config), "已更新插件配置"
            else:
                return {"ok": False, "message": f"不支持的类型: {ext_type}"}

            return {"ok": success, "message": msg, "type": ext_type}
        except Exception as e:
            logger.error(log_dict({'module_name': 'manager', 'action': 'ext_type.ext_id', 'msg': f'[扩展管理器] 配置失败: {ext_type}/{ext_id}: {e}'}))
            return {"ok": False, "message": f"配置失败: {e}"}

    # ════════════════════════════════════════════════════════════
    # 查询接口
    # ════════════════════════════════════════════════════════════

    def list_all(self, ext_type: Optional[str] = None) -> List[Dict]:
        """列出所有扩展，可按类型筛选"""
        if ext_type:
            try:
                etype = ExtensionType(ext_type)
            except ValueError:
                return [{"ok": False, "message": f"未知类型: {ext_type}"}]
            return self._store.list_all(etype)
        return self._store.list_all()

    def get(self, ext_type: str, ext_id: str) -> Optional[Dict]:
        """获取单个扩展信息"""
        try:
            etype = ExtensionType(ext_type)
        except ValueError:
            return None
        return self._store.get(etype, ext_id)

    def get_installed_by_type(self) -> Dict[str, List[Dict]]:
        """按类型分组获取所有已安装扩展"""
        skills_inst = self._get_installer(ExtensionType.SKILL)
        claude_inst = self._get_installer(ExtensionType.CLAUDE_SKILL)
        mcp_inst = self._get_installer(ExtensionType.MCP)
        channel_inst = self._get_installer(ExtensionType.CHANNEL)
        plugin_inst = self._get_installer(ExtensionType.PLUGIN)

        return {
            "skills": skills_inst.list_installed_skills(),
            "claude_skills": claude_inst.list_claude_skills(),
            "mcp_services": mcp_inst.list_installed_mcp(),
            "channels": channel_inst.list_installed_channels(),
            "plugins": plugin_inst.list_installed_plugins(),
            "store_skills": self._store.list_all(ExtensionType.SKILL),
            "store_claude_skills": self._store.list_all(ExtensionType.CLAUDE_SKILL),
            "store_mcp": self._store.list_all(ExtensionType.MCP),
            "store_channels": self._store.list_all(ExtensionType.CHANNEL),
            "store_plugins": self._store.list_all(ExtensionType.PLUGIN),
        }

    def discover_all(self) -> Dict[str, List[Dict]]:
        """发现所有可用扩展"""
        skills_inst = self._get_installer(ExtensionType.SKILL)
        mcp_inst = self._get_installer(ExtensionType.MCP)
        channel_inst = self._get_installer(ExtensionType.CHANNEL)

        return {
            **skills_inst.discover_available_skills(),
            **mcp_inst.discover_available_mcp(),
            **channel_inst.discover_available_channels(),
            "local_plugins": self._get_installer(ExtensionType.PLUGIN).discover_local_plugins(),
        }

    # ════════════════════════════════════════════════════════════
    # 通道消息发送
    # ════════════════════════════════════════════════════════════

    def send_channel_message(
        self, channel_id: str, message: str, **kwargs
    ) -> Dict:
        """通过通道发送消息"""
        channel_inst = self._get_installer(ExtensionType.CHANNEL)
        success, msg = channel_inst.send_message(channel_id, message, **kwargs)
        return {"ok": success, "message": msg}

    # ════════════════════════════════════════════════════════════
    # 工具注册表桥接
    # ════════════════════════════════════════════════════════════

    def connect_tool_registry(self, register_fn, unregister_fn):
        """连接工具注册表，使插件安装/卸载时自动注册/注销工具

        Args:
            register_fn: 注册回调
                signature: (name, description, handler, schema, source, source_id)
            unregister_fn: 注销回调
                signature: (source, source_id)
        """
        self._tool_register_fn = register_fn
        self._tool_unregister_fn = unregister_fn
        # 如果 PluginInstaller 已创建，立即传递回调
        plugin_inst = self._installers.get(ExtensionType.PLUGIN)
        if plugin_inst:
            plugin_inst._tool_register_fn = register_fn
            plugin_inst._tool_unregister_fn = unregister_fn
        logger.info(log_dict({'module_name': 'manager', 'action': 'log', 'msg': '[扩展管理器] 工具注册表已连接'}))

    # ════════════════════════════════════════════════════════════
    # 生命周期
    # ════════════════════════════════════════════════════════════

    def load_all_plugins(self) -> List[str]:
        """加载所有已启用插件"""
        plugin_inst = self._get_installer(ExtensionType.PLUGIN)
        loaded = []
        for p in self._store.list_all(ExtensionType.PLUGIN):
            if p.get("status") == ExtensionStatus.ENABLED.value:
                success, _ = plugin_inst.load_plugin(p["ext_id"])
                if success:
                    loaded.append(p["ext_id"])
        return loaded

    def cleanup(self):
        """清理所有扩展资源"""
        for ext_type, installer in self._installers.items():
            if ext_type == ExtensionType.PLUGIN:
                for p in self._store.list_all(ExtensionType.PLUGIN):
                    if p.get("ext_id") in installer._loaded_plugins:
                        installer.unload_plugin(p["ext_id"])
        logger.info(log_dict({'module_name': 'manager', 'action': 'log', 'msg': '[扩展管理器] 已清理所有扩展资源'}))
