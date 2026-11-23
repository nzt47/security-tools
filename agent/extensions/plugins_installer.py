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
from . import plugin_policy
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
            if item.name.startswith("."):
                # .staging / .deps 等内部目录不是可安装插件
                continue
            if item.is_dir():
                info = self._read_plugin_info(item)
                if info:
                    discovered.append(info)

        return discovered

    def _signature_gate(self, install_path: Path, *, phase: str) -> Tuple[bool, str]:
        """安装/加载前的签名门（D4 接线点；模式见 plugin_policy.signature_mode）。"""
        mode = plugin_policy.signature_mode()
        if mode == "off":
            return True, ""
        if not install_path.is_dir():
            # 单文件包无法携带 plugin.sig.json；enforce 下明确拒绝，warn 下只告警。
            if mode == "enforce":
                return False, "插件包必须是目录（单文件包无法携带签名文件）"
            logger.warning(log_dict({'module_name': 'plugins_installer',
                                     'action': 'signature.single_file',
                                     'msg': '[插件安装器] 单文件包无法验签，warn 模式放行'}))
            return True, ""
        try:
            from . import plugin_signature
        except Exception as exc:  # noqa: BLE001 依赖缺失时不能拖垮宿主启动
            logger.error(log_dict({'module_name': 'plugins_installer',
                                   'action': 'signature.unavailable',
                                   'msg': f'[插件安装器] 验签模块不可用: {type(exc).__name__}'}))
            if mode == "warn":
                return True, ""
            return False, "插件签名校验不可用（PLUGIN_SIGNATURE_UNAVAILABLE）"
        try:
            envelope = plugin_signature.read_signature(install_path)
            keys = plugin_signature.load_trusted_keys(plugin_policy.TRUSTED_KEYS_PATH)
            result = plugin_signature.verify_package(
                install_path, envelope, keys,
                allow_retired=(phase == "load"),
                ignore_generated=(phase == "load"),
            )
        except plugin_signature.PluginSignatureError as exc:
            logger.warning(log_dict({'module_name': 'plugins_installer',
                                     'action': 'signature.failed', 'phase': phase,
                                     'code': exc.code,
                                     'msg': f'[插件安装器] 签名校验失败: {exc.code}'}))
            if mode == "warn":
                return True, ""
            return False, f"插件签名校验失败（{exc.code}）"
        except Exception as exc:  # noqa: BLE001 验签器故障必须 fail-closed
            logger.error(log_dict({'module_name': 'plugins_installer',
                                   'action': 'signature.error', 'phase': phase,
                                   'msg': f'[插件安装器] 签名校验器异常: {type(exc).__name__}'}))
            if mode == "warn":
                return True, ""
            return False, "插件签名校验失败（PLUGIN_SIGNATURE_ERROR）"
        logger.info(log_dict({'module_name': 'plugins_installer',
                              'action': 'signature.ok', 'phase': phase,
                              'key_id': str(result.get('key_id') or ''),
                              'file_count': int(result.get('file_count') or 0),
                              'msg': '[插件安装器] 签名校验通过'}))
        return True, ""

    def install_plugin(self, source: str) -> Tuple[bool, str]:
        """安装插件

        Args:
            source: 来源 (github:user/repo, url:https://..., local:/path,
                    pip:package-name)

        Returns:
            (成功标志, 消息)
        """
        logger.info(log_dict({'module_name': 'plugins_installer', 'action': 'source', 'msg': f'[插件安装器] 安装插件: {source}'}))
        # 【2026-11-22 P0】默认关闭 + 来源白名单：插件代码在主进程 import，同权限；
        # 在进程隔离/签名完成前，默认值必须是“一个都不装”。
        if not plugin_policy.plugin_packages_enabled():
            return False, ("第三方插件包暂未开放：进程隔离与签名校验尚未完成。"
                           "如确需调试，请显式设置 CP_PLUGIN_PACKAGES_ENABLED=1 并配置来源白名单。")
        if not plugin_policy.source_allowed(source):
            return False, ("插件来源不在白名单内（CP_PLUGIN_INSTALL_ALLOWLIST）。"
                           "白名单为空时拒绝一切安装。")
        base_source, ref = plugin_policy.split_github_ref(source)
        if ref and not plugin_policy.valid_github_ref(ref):
            return False, "插件 ref 不合法（只接受分支/标签/commit 的安全字符）"
        ext_type, location, subpath = self._engine.parse_source(base_source)
        if ext_type == "pip":
            return False, ("不支持从 pip: 直接安装插件包（会把依赖装进宿主环境）。"
                           "请改用 github:/local: 并在独立环境中安装依赖。")

        plugins_dir = _PLUGINS_DIR
        plugins_dir.mkdir(parents=True, exist_ok=True)

        # 【2026-11-23 签名】先落暂存目录，验签通过后才移动到最终目录；
        # 失败时的半成品只留在 .staging/，由 _abort 统一清理。
        staging_root = plugins_dir / ".staging" / uuid.uuid4().hex
        staging_root.mkdir(parents=True, exist_ok=False)
        final_target = None
        moved = False

        def _abort(message: str) -> Tuple[bool, str]:
            if moved and final_target is not None:
                if final_target.is_dir():
                    shutil.rmtree(final_target, ignore_errors=True)
                else:
                    try:
                        final_target.unlink()
                    except OSError:
                        pass
            shutil.rmtree(staging_root, ignore_errors=True)
            return False, message

        install_path = None

        if ext_type == "github":
            # 从 GitHub 下载（先到暂存目录）
            repo_name = Path(location).name
            if repo_name in ("", ".", ".."):
                return _abort("插件仓库名不合法")
            target_dir = staging_root / repo_name
            success = self._engine.download_from_github(location, subpath, target_dir, ref=ref)
            if not success:
                return _abort(f"GitHub 下载失败: {location}")
            install_path = target_dir

        elif ext_type == "url":
            # 从 URL 下载（先到暂存目录）
            target_dir = staging_root / "download"
            target_dir.mkdir(parents=True, exist_ok=True)
            success = self._engine.download_from_url(location, target_dir)
            if not success:
                return _abort(f"URL 下载失败: {location}")

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
                return _abort("下载内容中未发现有效插件结构")

        elif ext_type == "local":
            # 从本地路径安装（先复制到暂存目录）
            src = Path(location)
            if not src.exists():
                return _abort(f"路径不存在: {location}")

            target_name = src.name
            target_dir = staging_root / target_name

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
            return _abort("无法确定插件安装路径")

        # 【2026-11-23 签名】暂存目录内先验签，再读元数据/落最终目录
        sig_ok, sig_msg = self._signature_gate(Path(install_path), phase="install")
        if not sig_ok:
            return _abort(sig_msg)

        # 读取插件元数据
        plugin_info = {}
        if install_path:
            plugin_info = self._read_plugin_info(install_path) or {}

        plugin_id = plugin_info.get("id") or (install_path.name if install_path else location)
        plugin_name = plugin_info.get("name") or plugin_id

        # 检查是否已安装
        existing = self._store.get(ExtensionType.PLUGIN, plugin_id)
        if existing and existing.get("status") != ExtensionStatus.UNINSTALLED.value:
            return _abort(f"插件已安装: {plugin_name}")

        # 验签通过后才移动到最终目录
        final_target = plugins_dir / Path(install_path).name
        if final_target.exists():
            return _abort(f"插件目录已存在: {final_target.name}")
        try:
            shutil.move(str(install_path), str(final_target))
        except Exception:  # noqa: BLE001 失败时不暴露路径细节
            return _abort("插件安装目录写入失败")
        moved = True
        install_path = final_target

        # 安装依赖
        deps = plugin_info.get("dependencies", [])
        if deps:
            # 【2026-11-22 P0】依赖装到插件自己的 .deps/，不再污染宿主 Python 环境；
            # 任一依赖失败即整体失败（不再 warning 后继续登记为“已安装”）。
            deps_dir = (Path(install_path) / ".deps") if install_path and os.path.isdir(str(install_path)) else None
            if deps_dir is None:
                return _abort("单文件插件无法隔离依赖：请改为目录包，或预装依赖后再安装")
            failed = self._engine.install_dependencies(deps, target_dir=deps_dir)
            if failed:
                logger.error(log_dict({'module_name': 'plugins_installer', 'action': 'deps_failed',
                                       'msg': f'[插件安装器] 依赖安装失败: {failed}'}))
                return _abort(f"插件依赖安装失败: {', '.join(str(x) for x in failed)}")

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
        try:
            self._store.add(meta)
        except Exception:  # noqa: BLE001 登记失败不能留下半装目录
            return _abort("插件登记失败")

        shutil.rmtree(staging_root, ignore_errors=True)
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
        # 【2026-11-22 P0】加载同样受总开关/白名单约束：安装入口关了，已落盘的包也不能被
        # 直接 toggle 起来执行。
        if not plugin_policy.plugin_packages_enabled():
            return False, "第三方插件包暂未开放（进程隔离与签名校验尚未完成）。"
        if not plugin_policy.source_allowed(plugin_data.get("source", "")):
            return False, "插件来源不在白名单内（CP_PLUGIN_INSTALL_ALLOWLIST）。"

        # 【2026-11-23 签名】加载前复验：安装后被替换/篡改的包不能直接跑。
        sig_ok, sig_msg = self._signature_gate(Path(install_path), phase="load")
        if not sig_ok:
            self._store.update_status(
                ExtensionType.PLUGIN, plugin_id, ExtensionStatus.ERROR
            )
            return False, sig_msg

        # 【2026-11-22 P2】默认走独立子进程：插件代码不再进宿主进程；
        # 关掉开关才回退旧的进程内 import（仅排障用）。
        if plugin_policy.plugin_process_isolation_enabled():
            return self._load_plugin_isolated(plugin_id, plugin_data, install_path)

        try:
            # 添加到 Python 路径
            plugin_dir = os.path.dirname(install_path)
            if plugin_dir not in sys.path:
                sys.path.insert(0, plugin_dir)
            # 依赖只从插件自己的 .deps/ 取；不碰宿主 site-packages。
            deps_dir = Path(install_path) / ".deps" if os.path.isdir(str(install_path)) else None
            if deps_dir is not None and deps_dir.is_dir() and str(deps_dir) not in sys.path:
                sys.path.insert(0, str(deps_dir))

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

    def _load_plugin_isolated(self, plugin_id: str, plugin_data: dict,
                               install_path: str) -> Tuple[bool, str]:
        """把插件包加载到独立子进程，并注册代理工具（P2 进程隔离）。"""
        try:
            from agent.extensions.plugin_process import PluginProcess, PluginProcessError
        except Exception as exc:  # noqa: BLE001
            logger.error(log_dict({'module_name': 'plugins_installer', 'action': 'isolated.import_failed',
                                   'msg': f'[插件安装器] 进程隔离模块不可用: {type(exc).__name__}'}))
            self._store.update_status(ExtensionType.PLUGIN, plugin_id, ExtensionStatus.ERROR)
            return False, "插件进程隔离模块不可用"

        config = plugin_data.get("config")
        proc = PluginProcess(
            plugin_id, install_path,
            config=config if isinstance(config, dict) else {},
            timeout=plugin_policy.process_timeout(),
        )
        try:
            tools = proc.start()
        except PluginProcessError as exc:
            logger.error(log_dict({'module_name': 'plugins_installer', 'action': 'isolated.start_failed',
                                   'msg': f'[插件安装器] 插件进程启动失败: {exc.message}'}))
            self._store.update_status(ExtensionType.PLUGIN, plugin_id, ExtensionStatus.ERROR)
            return False, f"插件进程启动失败: {exc.message}"

        registered = []
        try:
            for tool in tools:
                name = str(tool.get("name") or "").strip()
                if not name:
                    continue
                if self._tool_register_fn:
                    self._tool_register_fn(
                        name=name,
                        description=str(tool.get("description") or ""),
                        handler=self._make_process_proxy(proc, name),
                        schema=tool.get("schema") if isinstance(tool.get("schema"), dict) else {},
                        source="plugin",
                        source_id=plugin_id,
                    )
                    registered.append(name)
        except Exception as exc:  # noqa: BLE001
            logger.error(log_dict({'module_name': 'plugins_installer', 'action': 'isolated.register_failed',
                                   'msg': f'[插件安装器] 插件工具注册失败: {type(exc).__name__}'}))
            proc.close()
            if self._tool_unregister_fn:
                try:
                    self._tool_unregister_fn(source="plugin", source_id=plugin_id)
                except Exception:
                    pass
            self._store.update_status(ExtensionType.PLUGIN, plugin_id, ExtensionStatus.ERROR)
            return False, "插件工具注册失败"

        self._loaded_plugins[plugin_id] = proc
        self._store.update_status(ExtensionType.PLUGIN, plugin_id, ExtensionStatus.ENABLED)
        logger.info(log_dict({'module_name': 'plugins_installer', 'action': 'isolated.loaded',
                               'msg': f"[插件安装器] 插件 '{plugin_id}' 已进程隔离加载（{len(registered)} 个工具）"}))
        return True, f"插件已加载（进程隔离）: {plugin_id}"

    @staticmethod
    def _make_process_proxy(proc, tool_name: str):
        """工具 handler 代理：调用子进程；异常收口为结构化错误（不抛到宿主）。"""
        def _proxy(**kwargs):
            try:
                return proc.call(tool_name, kwargs)
            except Exception as exc:  # noqa: BLE001  PluginProcessError 的 message 已脱敏
                message = getattr(exc, "message", None) or "插件工具执行失败"
                code = getattr(exc, "error_code", "PLUGIN_PROCESS_ERROR")
                return {"ok": False, "error": str(message), "error_code": str(code), "tool": tool_name}
        return _proxy

    def unload_plugin(self, plugin_id: str) -> Tuple[bool, str]:
        """卸载已加载的插件"""
        instance = self._loaded_plugins.pop(plugin_id, None)
        if instance is not None:
            try:
                from agent.extensions.plugin_process import PluginProcess
                if isinstance(instance, PluginProcess):
                    instance.close()
                elif hasattr(instance, "on_unload"):
                    instance.on_unload()
            except Exception as e:
                logger.warning(log_dict({'module_name': 'plugins_installer', 'action': 'log', 'msg': f'[插件安装器] 插件卸载回调失败: {type(e).__name__}'}))

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
            # 【2026-11-22 P0】原实现忽略 load_plugin 的返回值：加载失败仍写 ENABLED 并回“已启用”。
            # 现在加载失败必须如实返回失败，且状态不能被改成 ENABLED。
            ok, msg = self.load_plugin(plugin_id)
            if not ok:
                return False, msg, False
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
