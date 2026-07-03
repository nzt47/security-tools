"""插件安装器 — 管理通用 Python 插件

插件是云枢的能力扩展包，通过 Python 代码提供新功能。
每个插件是一个 Python 包或模块，遵循统一的生命周期接口。

插件结构规范：
  my_plugin/
    ├── __init__.py         # 必须：包含 Plugin 类
    ├── plugin.json         # 推荐：插件元数据
    └── ...                 # 其他模块文件

plugin.json 格式：
  {
    "id": "my_plugin",
    "name": "我的插件",
    "version": "1.0.0",
    "description": "...",
    "entry": "my_plugin",
    "dependencies": ["requests>=2.0"]
  }

Plugin 类接口：
  class Plugin:
      def on_load(self, context: dict): ...      # 加载时调用
      def on_unload(self): ...                    # 卸载时调用
      def on_enable(self): ...                    # 启用时调用
      def on_disable(self): ...                   # 禁用时调用
      def get_commands(self) -> list: ...         # 返回注册的命令列表
      def get_tools(self) -> list: ...            # 返回注册的工具列表
"""

import importlib
import json
import uuid
import logging
import os
import sys
import shutil
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple

from agent.extensions.base import (
    ExtensionType, ExtensionStatus, ExtensionMetadata,
)
from agent.extensions.installer import InstallEngine
from agent.extensions.store import ExtensionStore
from agent.logging_utils import log_dict

logger = logging.getLogger(__name__)

def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]


# 插件存储目录
_PLUGINS_DIR = Path(__file__).parent.parent / "data" / "extensions_packages" / "plugins"


class PluginInstaller:
    """插件安装器 — 管理通用插件"""

    def __init__(self, store: ExtensionStore,
                 tool_register_fn=None, tool_unregister_fn=None):
        self._store = store
        self._engine = InstallEngine()
        # 已加载的插件实例
        self._loaded_plugins: Dict[str, Any] = {}
        self._tool_register_fn = tool_register_fn
        self._tool_unregister_fn = tool_unregister_fn

    # ── 插件管理 ──

    def list_installed_plugins(self) -> List[Dict]:
        """列出所有已安装的插件"""
        return self._store.list_all(ExtensionType.PLUGIN)

    def discover_local_plugins(self) -> List[Dict]:
        """发现插件目录中所有可安装的插件"""
        plugins_dir = _PLUGINS_DIR
        if not plugins_dir.exists():
            return []

        discovered = []
        for item in plugins_dir.iterdir():
            if item.is_dir():
                info = self._read_plugin_info(item)
                if info:
                    discovered.append(info)

        return discovered

    def install_plugin(self, source: str) -> Tuple[bool, str]:
        """安装插件

        Args:
            source: 来源 (github:user/repo, url:https://..., local:/path,
                    pip:package-name)

        Returns:
            (成功标志, 消息)
        """
        logger.info(log_dict({'module_name': 'plugins_installer', 'action': 'source', 'msg': f'[插件安装器] 安装插件: {source}'}))
        ext_type, location, subpath = self._engine.parse_source(source)

        plugins_dir = _PLUGINS_DIR
        plugins_dir.mkdir(parents=True, exist_ok=True)

        install_path = None

        if ext_type == "github":
            # 从 GitHub 下载
            repo_name = location.split("/")[-1]
            target_dir = plugins_dir / repo_name
            success = self._engine.download_from_github(location, subpath, target_dir)
            if not success:
                return False, f"GitHub 下载失败: {location}"
            install_path = target_dir

        elif ext_type == "url":
            # 从 URL 下载
            target_dir = plugins_dir / "temp_download"
            target_dir.mkdir(parents=True, exist_ok=True)
            success = self._engine.download_from_url(location, target_dir)
            if not success:
                shutil.rmtree(target_dir, ignore_errors=True)
                return False, f"URL 下载失败: {location}"

            # 如果下载的是单个文件，检查插件信息
            files = list(target_dir.iterdir())
            if len(files) == 1 and files[0].is_file():
                # 可能是插件文件，直接使用
                install_path = target_dir
            else:
                # 找到包含 plugin.json 或 __init__.py 的目录
                install_path = self._find_plugin_root(target_dir)
                if not install_path:
                    # 尝试使用子目录
                    for child in target_dir.iterdir():
                        if child.is_dir():
                            install_path = self._find_plugin_root(child)
                            if install_path:
                                break

            if not install_path:
                shutil.rmtree(target_dir, ignore_errors=True)
                return False, "下载内容中未发现有效插件结构"

        elif ext_type == "local":
            # 从本地路径安装
            src = Path(location)
            if not src.exists():
                return False, f"路径不存在: {location}"

            target_name = src.name
            target_dir = plugins_dir / target_name

            if target_dir.exists():
                return False, f"插件目录已存在: {target_name}"

            if src.is_file():
                target_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, target_dir / src.name)
            else:
                shutil.copytree(src, target_dir)

            install_path = target_dir

        elif ext_type == "pip":
            # pip 安装 — 在 Python 环境中安装
            package_name = location
            failed = self._engine.install_dependencies([package_name])
            if failed:
                return False, f"pip 安装失败: {package_name}"
            # 找到安装路径
            install_path = self._find_pip_package_path(package_name)
            if not install_path:
                # 即使没找到路径，也认为安装成功
                pass

        if install_path is None and ext_type != "pip":
            return False, "无法确定插件安装路径"

        # 读取插件元数据
        plugin_info = {}
        if install_path:
            plugin_info = self._read_plugin_info(install_path) or {}

        plugin_id = plugin_info.get("id") or (install_path.name if install_path else location)
        plugin_name = plugin_info.get("name") or plugin_id

        # 检查是否已安装
        existing = self._store.get(ExtensionType.PLUGIN, plugin_id)
        if existing and existing.get("status") != ExtensionStatus.UNINSTALLED.value:
            return False, f"插件已安装: {plugin_name}"

        # 安装依赖
        deps = plugin_info.get("dependencies", [])
        if deps:
            failed = self._engine.install_dependencies(deps)
            if failed:
                logger.warning(log_dict({'module_name': 'plugins_installer', 'action': 'failed', 'msg': f'[插件安装器] 部分依赖安装失败: {failed}'}))

        # 记录到扩展存储
        meta = ExtensionMetadata(
            ext_id=plugin_id,
            ext_type=ExtensionType.PLUGIN,
            name=plugin_name,
            version=plugin_info.get("version", "1.0.0"),
            description=plugin_info.get("description", ""),
            author=plugin_info.get("author", ""),
            source=source,
            source_url=source,
            install_path=str(install_path) if install_path else "",
            dependencies=plugin_info.get("dependencies", []),
            status=ExtensionStatus.INSTALLED,
            config=plugin_info.get("config", {}),
        )
        meta.touch()
        meta.installed_at = meta.created_at
        self._store.add(meta)

        logger.info(log_dict({'module_name': 'plugins_installer', 'action': 'plugin_id', 'msg': f'[插件安装器] 插件安装完成: {plugin_id}'}))
        return True, f"已安装插件: {plugin_name} (v{meta.version})"

    def load_plugin(self, plugin_id: str) -> Tuple[bool, str]:
        """加载已安装的插件（导入 Python 模块）"""
        plugin_data = self._store.get(ExtensionType.PLUGIN, plugin_id)
        if not plugin_data:
            return False, f"插件不存在: {plugin_id}"

        install_path = plugin_data.get("install_path", "")
        if not install_path or not os.path.exists(install_path):
            return False, f"插件路径不存在: {install_path}"

        try:
            # 添加到 Python 路径
            plugin_dir = os.path.dirname(install_path)
            if plugin_dir not in sys.path:
                sys.path.insert(0, plugin_dir)

            module_name = os.path.basename(install_path)
            if module_name.endswith(".py"):
                module_name = module_name[:-3]

            module = importlib.import_module(module_name)

            # 查找 Plugin 类
            plugin_class = None
            if hasattr(module, "Plugin"):
                plugin_class = module.Plugin
            elif hasattr(module, "plugin"):
                plugin_class = module.plugin

            if plugin_class:
                plugin_instance = plugin_class()
                context = {"plugin_id": plugin_id, "config": plugin_data.get("config", {})}
                if hasattr(plugin_instance, "on_load"):
                    plugin_instance.on_load(context)
                self._loaded_plugins[plugin_id] = plugin_instance

                # 注册插件提供的工具到全局工具表
                if hasattr(plugin_instance, "get_tools") and self._tool_register_fn:
                    try:
                        tools_list = plugin_instance.get_tools()
                        if tools_list:
                            count = 0
                            for tool_def in tools_list:
                                self._tool_register_fn(
                                    name=tool_def["name"],
                                    description=tool_def.get("description", ""),
                                    handler=tool_def["handler"],
                                    schema=tool_def.get("schema"),
                                    source="plugin",
                                    source_id=plugin_id,
                                )
                                count += 1
                            logger.info(log_dict({'module_name': 'plugins_installer', 'action': 'log', 'msg': f"[插件安装器] 插件 '{plugin_id}' 已注册 {count} 个工具"}))
                    except Exception as e:
                        logger.warning(log_dict({'module_name': 'plugins_installer', 'action': 'log', 'msg': f"[插件安装器] 插件 '{plugin_id}' 工具注册失败: {e}"}))

                self._store.update_status(
                    ExtensionType.PLUGIN, plugin_id, ExtensionStatus.ENABLED
                )
                logger.info(log_dict({'module_name': 'plugins_installer', 'action': 'plugin_id', 'msg': f'[插件安装器] 插件已加载: {plugin_id}'}))
                return True, f"插件已加载: {plugin_id}"

            # 即使没有 Plugin 类，也认为安装成功
            self._store.update_status(
                ExtensionType.PLUGIN, plugin_id, ExtensionStatus.ENABLED
            )
            return True, f"插件模块已导入: {module_name}"

        except Exception as e:
            logger.error(log_dict({'module_name': 'plugins_installer', 'action': 'plugin_id', 'msg': f'[插件安装器] 插件加载失败: {plugin_id}: {e}'}))
            self._store.update_status(
                ExtensionType.PLUGIN, plugin_id, ExtensionStatus.ERROR
            )
            return False, f"插件加载失败: {e}"

    def unload_plugin(self, plugin_id: str) -> Tuple[bool, str]:
        """卸载已加载的插件"""
        instance = self._loaded_plugins.pop(plugin_id, None)
        if instance and hasattr(instance, "on_unload"):
            try:
                instance.on_unload()
            except Exception as e:
                logger.warning(log_dict({'module_name': 'plugins_installer', 'action': 'log', 'msg': f'[插件安装器] 插件卸载回调失败: {e}'}))

        # 注销插件注册的工具
        if self._tool_unregister_fn:
            try:
                removed = self._tool_unregister_fn(source="plugin", source_id=plugin_id)
                if removed:
                    logger.info(log_dict({'module_name': 'plugins_installer', 'action': 'log', 'msg': f"[插件安装器] 插件 '{plugin_id}' 已注销 {removed} 个工具"}))
            except Exception as e:
                logger.warning(log_dict({'module_name': 'plugins_installer', 'action': 'log', 'msg': f"[插件安装器] 插件 '{plugin_id}' 工具注销失败: {e}"}))

        self._store.update_status(
            ExtensionType.PLUGIN, plugin_id, ExtensionStatus.DISABLED
        )
        logger.info(log_dict({'module_name': 'plugins_installer', 'action': 'plugin_id', 'msg': f'[插件安装器] 插件已卸载: {plugin_id}'}))
        return True, f"插件已卸载: {plugin_id}"

    def uninstall_plugin(self, plugin_id: str) -> Tuple[bool, str]:
        """完全卸载插件"""
        plugin_data = self._store.get(ExtensionType.PLUGIN, plugin_id)
        if not plugin_data:
            return False, f"插件不存在: {plugin_id}"

        # 先卸载
        self.unload_plugin(plugin_id)

        # 删除文件
        install_path = plugin_data.get("install_path", "")
        if install_path and os.path.exists(install_path):
            try:
                if os.path.isdir(install_path):
                    shutil.rmtree(install_path, ignore_errors=True)
                else:
                    os.remove(install_path)
            except Exception as e:
                logger.warning(log_dict({'module_name': 'plugins_installer', 'action': 'log', 'msg': f'[插件安装器] 删除插件文件失败: {e}'}))

        self._store.remove(ExtensionType.PLUGIN, plugin_id)
        logger.info(log_dict({'module_name': 'plugins_installer', 'action': 'plugin_id', 'msg': f'[插件安装器] 插件已完全卸载: {plugin_id}'}))
        return True, f"插件已完全卸载: {plugin_id}"

    def toggle_plugin(self, plugin_id: str, enabled: bool = None) -> Tuple[bool, str, bool]:
        """切换插件启用状态"""
        plugin_data = self._store.get(ExtensionType.PLUGIN, plugin_id)
        if not plugin_data:
            return False, f"插件不存在: {plugin_id}", False

        new_enabled = enabled if enabled is not None else not plugin_data.get("enabled", True)

        if new_enabled:
            self.load_plugin(plugin_id)
        else:
            self.unload_plugin(plugin_id)

        status = ExtensionStatus.ENABLED if new_enabled else ExtensionStatus.DISABLED
        self._store.update_status(ExtensionType.PLUGIN, plugin_id, status)

        action = "已启用" if new_enabled else "已禁用"
        return True, f"{action} 插件: {plugin_id}", new_enabled

    # ── 工具方法 ──

    def _find_plugin_root(self, directory: Path) -> Optional[Path]:
        """查找包含 plugin.json 或 __init__.py 的插件根目录"""
        if (directory / "plugin.json").exists() or (directory / "__init__.py").exists():
            return directory
        if (directory / "manifest.json").exists():
            return directory

        # 检查子目录
        for item in directory.iterdir():
            if item.is_dir():
                result = self._find_plugin_root(item)
                if result:
                    return result

        return None

    def _read_plugin_info(self, plugin_dir: Path) -> Optional[Dict]:
        """读取插件信息"""
        if not plugin_dir.exists():
            return None

        info = {
            "id": plugin_dir.name,
            "name": plugin_dir.name,
            "version": "0.0.0",
            "description": "",
            "author": "",
            "dependencies": [],
        }

        # plugin.json
        for name in ("plugin.json", "manifest.json"):
            path = plugin_dir / name
            if path.exists():
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    info.update(data)
                    return info
                except (json.JSONDecodeError, Exception):
                    pass

        # setup.py 或 pyproject.toml
        setup_py = plugin_dir / "setup.py"
        if setup_py.exists():
            try:
                content = setup_py.read_text(encoding="utf-8", errors="replace")
                import re
                m = re.search(r'version\s*=\s*["\']([^"\']+)["\']', content)
                if m:
                    info["version"] = m.group(1)
                m = re.search(r'description\s*=\s*["\']([^"\']+)["\']', content)
                if m:
                    info["description"] = m.group(1)
                m = re.search(r'author\s*=\s*["\']([^"\']+)["\']', content)
                if m:
                    info["author"] = m.group(1)
            except Exception:
                pass

        # __init__.py 中的元数据
        init_py = plugin_dir / "__init__.py"
        if init_py.exists():
            try:
                content = init_py.read_text(encoding="utf-8", errors="replace")
                import re
                for key in ("__version__", "__author__", "__description__"):
                    m = re.search(rf'{key}\s*=\s*["\']([^"\']+)["\']', content)
                    if m:
                        mapped = key.replace("__", "").replace("version", "version") \
                            .replace("author", "author").replace("description", "description")
                        if mapped in info:
                            info[mapped] = m.group(1)
            except Exception:
                pass

        return info

    def _find_pip_package_path(self, package_name: str) -> Optional[Path]:
        """查找 pip 安装的包路径"""
        try:
            module = importlib.import_module(package_name)
            path = getattr(module, "__file__", None)
            if path:
                return Path(path).parent
        except ImportError:
            pass
        return None
