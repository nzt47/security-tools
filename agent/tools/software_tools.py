"""工具注册模块 — 软件管理工具（搜索、安装、列出、卸载）"""
import logging
import os
from agent import tools as _tools

logger = logging.getLogger(__name__)


def register_all(dl):
    """注册所有软件管理工具

    Args:
        dl: DigitalLife 实例（用于访问 self 属性）
    """

    # ════════════════════════════════════════════════════════════
    #  软件管理器初始化
    # ════════════════════════════════════════════════════════════

    if not hasattr(dl, '_software_mgr'):
        from agent.software_manager import SoftwareManager
        from agent.software_backends import (
            ChocolateyBackend, PipBackend, NpmBackend,
            WebDownloadBackend, GitHubBackend,
        )
        dl._software_mgr = SoftwareManager()

        # 注册 Chocolatey 后端（仅 Windows）
        if os.name == "nt":
            try:
                dl._software_mgr.register_backend(ChocolateyBackend())
            except Exception as e:
                logger.warning(f"Chocolatey 后端注册失败: {e}")

        # 注册 pip 后端
        try:
            dl._software_mgr.register_backend(PipBackend())
        except Exception as e:
            logger.warning(f"pip 后端注册失败: {e}")

        # 注册 npm 后端
        try:
            dl._software_mgr.register_backend(NpmBackend())
        except Exception as e:
            logger.warning(f"npm 后端注册失败: {e}")

        # 注册 Web 下载后端
        try:
            from agent.web import HttpClient, SearchEngine
            web_backend = WebDownloadBackend(
                http_client=HttpClient({"timeout": 30}),
                search_engine=SearchEngine(),
            )
            dl._software_mgr.register_backend(web_backend)
        except Exception as e:
            logger.warning(f"Web下载后端注册失败: {e}")

        # 注册 GitHub Releases 后端
        try:
            dl._software_mgr.register_backend(GitHubBackend())
        except Exception as e:
            logger.warning(f"GitHub后端注册失败: {e}")

    # ════════════════════════════════════════════════════════════
    #  软件管理工具
    # ════════════════════════════════════════════════════════════

    @_tools.register("software_search", "搜索可安装的软件包。支持 Chocolatey(Windows应用)/pip(Python包)/npm(Node.js包)/GitHub Releases 等多种来源。不指定 backend 则搜索所有来源", schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词"},
            "backend": {
                "type": "string",
                "enum": ["chocolatey", "pip", "npm", "web_download", "github"],
                "description": "指定搜索来源（可选）",
            },
        },
        "required": ["query"],
    })
    def _software_search(**kwargs):
        query = kwargs.get("query", "")
        backend = kwargs.get("backend")
        if not query:
            return {"ok": False, "error": "请提供搜索关键词（query）"}
        return dl._software_mgr.search(query, backend=backend)

    @_tools.register("software_install", "安装软件包。支持自动选择最佳安装方式。不在白名单的软件需设置 confirm=true 以确认安装风险。", schema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "软件名称"},
            "backend": {
                "type": "string",
                "enum": ["chocolatey", "pip", "npm", "web_download", "github"],
                "description": "指定安装来源（可选）",
            },
            "version": {"type": "string", "description": "指定版本号（可选）"},
            "confirm": {"type": "boolean", "description": "当软件不在白名单中时，设为 true 可确认风险并继续安装（默认 false）"},
        },
        "required": ["name"],
    })
    def _software_install(**kwargs):
        name = kwargs.get("name", "")
        backend = kwargs.get("backend")
        version = kwargs.get("version")
        confirm = kwargs.get("confirm", False)
        if not name:
            return {"ok": False, "error": "请提供要安装的软件名称（name）"}

        perm = dl._permission.check_action(f"software_install:{name}", f"安装软件 {name}")
        if not perm.allowed:
            return {"ok": False, "error": f"权限系统拒绝: {perm.reason}", "blocked": True}

        # 白名单检查：不在白名单时需要 confirm 确认
        if not dl._software_mgr.is_whitelisted(name):
            if not confirm:
                safety = dl._permission.check_text(f"安装软件 {name}")
                if safety.get("level") == "critical":
                    return {
                        "ok": False,
                        "error": f"「{name}」不在软件安装白名单中，且被安全系统阻止。",
                        "blocked": True, "safety": safety,
                    }
                return {
                    "ok": False, "warning": True,
                    "error": f"「{name}」不在白名单中。如确认安全，请设置 confirm=true 并重新调用。",
                    "name": name,
                }
            # confirm=True 时自动加入白名单
            dl._software_mgr.add_to_whitelist(name)

        return dl._software_mgr.install(name, backend=backend, version=version, auto_confirm=True)

    @_tools.register("software_list", "列出已安装的软件包列表", schema={
        "type": "object",
        "properties": {
            "backend": {
                "type": "string",
                "enum": ["chocolatey", "pip", "npm", "web_download", "github"],
                "description": "指定来源（可选）",
            },
        },
    })
    def _software_list(**kwargs):
        backend = kwargs.get("backend")
        return dl._software_mgr.list_installed(backend=backend)

    @_tools.register("software_uninstall", "卸载已安装的软件包。", schema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "软件名称"},
            "backend": {
                "type": "string",
                "enum": ["chocolatey", "pip", "npm", "web_download", "github"],
                "description": "指定来源（可选）",
            },
        },
        "required": ["name"],
    })
    def _software_uninstall(**kwargs):
        name = kwargs.get("name", "")
        backend = kwargs.get("backend")
        if not name:
            return {"ok": False, "error": "请提供要卸载的软件名称（name）"}

        perm = dl._permission.check_action(f"software_uninstall:{name}", f"卸载软件 {name}")
        if not perm.allowed:
            return {"ok": False, "error": f"权限系统拒绝: {perm.reason}", "blocked": True}

        return dl._software_mgr.uninstall(name, backend=backend)
