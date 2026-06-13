"""MCP 服务安装器 — 管理 Model Context Protocol 服务

MCP 服务是云枢可以连接的外部能力提供者。
通过 NetworkConfigManager 进行配置管理。

支持：
  - 从内置注册表安装 MCP 服务模板
  - 从 GitHub 安装自定义 MCP 服务
  - 手动配置 MCP 服务（地址、端口、协议）
  - 测试服务连通性
  - 启用/禁用服务
"""

import logging
import socket
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime

from agent.extensions.base import (
    ExtensionType, ExtensionStatus, ExtensionMetadata, BUILTIN_EXTENSIONS,
)
from agent.extensions.installer import InstallEngine
from agent.extensions.store import ExtensionStore

logger = logging.getLogger(__name__)


class McpInstaller:
    """MCP 服务安装器"""

    def __init__(self, store: ExtensionStore, network_config_mgr=None):
        self._store = store
        self._engine = InstallEngine()
        self._network_config_mgr = network_config_mgr

    def set_network_config_mgr(self, network_config_mgr):
        """设置网络配置管理器引用"""
        self._network_config_mgr = network_config_mgr

    # ── MCP 服务管理 ──

    def list_installed_mcp(self) -> List[Dict]:
        """列出已安装的 MCP 服务"""
        if self._network_config_mgr:
            try:
                return self._network_config_mgr.get_mcp_services()
            except Exception as e:
                logger.warning(f"[MCP安装器] 获取 MCP 服务失败: {e}")
        return []

    def install_builtin_mcp(self, service_id: str) -> Tuple[bool, str]:
        """从内置注册表安装 MCP 服务模板

        Args:
            service_id: 服务 ID（如 "filesystem"）

        Returns:
            (成功标志, 消息)
        """
        # 查找内置 MCP 服务
        builtin = None
        for s in BUILTIN_EXTENSIONS.get("mcp", []):
            if s["id"] == service_id and s.get("builtin"):
                builtin = s
                break

        if not builtin:
            return False, f"未找到内置 MCP 服务: {service_id}"

        if not self._network_config_mgr:
            return False, "网络配置管理器未初始化"

        try:
            # 构建服务配置
            service = {
                "name": builtin["name"],
                "description": builtin["description"],
                "protocol": builtin.get("protocol", "http"),
                "address": builtin.get("address", ""),
                "port": builtin.get("port", 8080),
                "command": builtin.get("command", ""),
                "args": builtin.get("args", []),
                "enabled": True,
            }

            # 如果是 stdio 协议（本地命令），不需要地址
            if service.get("protocol") == "stdio":
                service["address"] = "localhost"
            # 如果是网络服务，使用默认地址
            elif not service.get("address"):
                service["address"] = "localhost"

            result = self._network_config_mgr.add_mcp_service(service)

            # 记录到扩展存储
            meta = ExtensionMetadata(
                ext_id=service_id,
                ext_type=ExtensionType.MCP,
                name=builtin["name"],
                description=builtin["description"],
                source="builtin",
                status=ExtensionStatus.ENABLED,
                config=service,
            )
            meta.touch()
            meta.installed_at = meta.created_at
            self._store.add(meta)

            logger.info(f"[MCP安装器] 已安装内置 MCP 服务: {service_id}")
            return True, f"已安装 MCP 服务: {builtin['name']}"

        except ValueError as e:
            return False, f"安装失败: {e}"
        except Exception as e:
            logger.error(f"[MCP安装器] 安装失败: {e}")
            return False, f"安装失败: {e}"

    def install_mcp_service(
        self, name: str, address: str = "localhost", port: int = 8080,
        protocol: str = "http", description: str = "",
        command: str = "", args: Optional[List[str]] = None,
    ) -> Tuple[bool, str]:
        """手动安装 MCP 服务

        Args:
            name: 服务名称
            address: 服务地址
            port: 服务端口
            protocol: 协议 (http/https/stdio)
            description: 服务描述
            command: stdio 模式下要执行的命令
            args: stdio 模式下的命令行参数

        Returns:
            (成功标志, 消息)
        """
        if not self._network_config_mgr:
            return False, "网络配置管理器未初始化"

        try:
            service = {
                "name": name,
                "description": description,
                "address": address,
                "port": port,
                "protocol": protocol,
                "command": command,
                "args": args or [],
                "enabled": True,
            }

            result = self._network_config_mgr.add_mcp_service(service)

            # 记录到扩展存储
            ext_id = result.get("id", name.lower().replace(" ", "_"))
            meta = ExtensionMetadata(
                ext_id=ext_id,
                ext_type=ExtensionType.MCP,
                name=name,
                description=description,
                source=f"manual:{protocol}://{address}:{port}",
                status=ExtensionStatus.ENABLED,
                config=service,
            )
            meta.touch()
            meta.installed_at = meta.created_at
            self._store.add(meta)

            logger.info(f"[MCP安装器] 已安装 MCP 服务: {name}")
            return True, f"已添加 MCP 服务: {name}"

        except ValueError as e:
            return False, f"添加失败: {e}"
        except Exception as e:
            logger.error(f"[MCP安装器] 添加失败: {e}")
            return False, f"添加失败: {e}"

    def install_mcp_from_source(self, source: str) -> Tuple[bool, str]:
        """从来源安装 MCP 服务

        支持的来源:
          - github:user/repo → 下载并配置 MCP 服务 (npm install 或 pip install)
          - npm:package-name → npm install 并配置 stdio 命令
          - pip:package-name → pip install 并配置 Python 命令
        """
        ext_type, location, _ = self._engine.parse_source(source)

        if ext_type == "npm":
            # npm 包 MCP 服务
            package_name = location
            success = self._engine.install_npm_package(package_name, Path.home() / ".claude" / "mcp")
            if not success:
                return False, f"npm 安装失败: {package_name}"

            return self.install_mcp_service(
                name=package_name,
                protocol="stdio",
                command="npx",
                args=["-y", package_name],
                description=f"通过 npm 安装的 MCP 服务: {package_name}",
            )

        elif ext_type == "pip":
            # pip 包 MCP 服务
            package_name = location
            success = self._engine.install_pip_package(package_name)
            if not success:
                return False, f"pip 安装失败: {package_name}"

            return self.install_mcp_service(
                name=package_name,
                protocol="stdio",
                command="python",
                args=["-m", package_name],
                description=f"通过 pip 安装的 MCP 服务: {package_name}",
            )

        elif ext_type == "github":
            # GitHub 仓库 — 先 clone 然后检测类型
            temp_dir = Path.home() / ".claude" / "mcp" / location.replace("/", "_")
            temp_dir.mkdir(parents=True, exist_ok=True)

            success = self._engine.download_from_github(location, "", temp_dir)
            if not success:
                return False, f"GitHub 下载失败: {location}"

            pkg_type = self._engine.detect_package_type(temp_dir)
            if pkg_type == "node":
                return self.install_mcp_service(
                    name=location.split("/")[-1],
                    protocol="stdio",
                    command="npx",
                    args=[str(temp_dir)],
                    description=f"从 GitHub 安装的 MCP 服务: {location}",
                )
            elif pkg_type == "python":
                return self.install_mcp_service(
                    name=location.split("/")[-1],
                    protocol="stdio",
                    command="python",
                    args=["-m", location.split("/")[-1]],
                    description=f"从 GitHub 安装的 MCP 服务: {location}",
                )
            else:
                return False, f"无法识别的 MCP 包类型: {pkg_type}"

        return False, f"不支持的来源: {source}"

    def uninstall_mcp(self, service_id: str) -> Tuple[bool, str]:
        """卸载 MCP 服务"""
        if not self._network_config_mgr:
            return False, "网络配置管理器未初始化"

        try:
            success = self._network_config_mgr.delete_mcp_service(service_id)
            if success:
                self._store.remove(ExtensionType.MCP, service_id)
                logger.info(f"[MCP安装器] 已卸载 MCP 服务: {service_id}")
                return True, f"已卸载 MCP 服务"
            return False, f"服务不存在: {service_id}"
        except Exception as e:
            logger.error(f"[MCP安装器] 卸载失败: {e}")
            return False, f"卸载失败: {e}"

    def toggle_mcp(self, service_id: str, enabled: bool = None) -> Tuple[bool, str, bool]:
        """切换 MCP 服务启用状态"""
        if not self._network_config_mgr:
            return False, "网络配置管理器未初始化", False

        try:
            service = self._network_config_mgr.get_mcp_service(service_id)
            if not service:
                return False, f"服务不存在: {service_id}", False

            new_enabled = enabled if enabled is not None else not service.get("enabled", True)
            self._network_config_mgr.update_mcp_service(service_id, {"enabled": new_enabled})

            status = ExtensionStatus.ENABLED if new_enabled else ExtensionStatus.DISABLED
            self._store.update_status(ExtensionType.MCP, service_id, status)

            action = "已启用" if new_enabled else "已禁用"
            return True, f"{action} MCP 服务", new_enabled
        except Exception as e:
            return False, f"操作失败: {e}", False

    def test_connection(self, service_id: str) -> Tuple[bool, str]:
        """测试 MCP 服务连通性"""
        if not self._network_config_mgr:
            return False, "网络配置管理器未初始化"

        try:
            service = self._network_config_mgr.get_mcp_service(service_id)
            if not service:
                return False, f"服务不存在: {service_id}"

            # stdio 模式测试
            if service.get("protocol") == "stdio":
                command = service.get("command", "")
                if not command:
                    return False, "stdio 模式未配置命令"
                import subprocess
                try:
                    result = subprocess.run(
                        [command] + service.get("args", []),
                        capture_output=True, text=True, timeout=10,
                    )
                    return True, f"进程已启动 (exit code: {result.returncode})"
                except FileNotFoundError:
                    return False, f"命令不存在: {command}"
                except subprocess.TimeoutExpired:
                    return False, "进程启动超时"

            # HTTP 模式测试
            address = service.get("address", "localhost")
            port = service.get("port", 8080)
            try:
                sock = socket.create_connection((address, port), timeout=5)
                sock.close()
                return True, f"连接成功: {address}:{port}"
            except (socket.timeout, ConnectionRefusedError, OSError) as e:
                return False, f"连接失败: {e}"

        except Exception as e:
            return False, f"测试失败: {e}"

    # ── 发现 ──

    def discover_available_mcp(self) -> Dict[str, List[Dict]]:
        """发现所有可用的 MCP 服务"""
        builtin_mcp = BUILTIN_EXTENSIONS.get("mcp", [])
        installed = self.list_installed_mcp()
        installed_names = {s.get("name") for s in installed}

        available = []
        for s in builtin_mcp:
            available.append({
                **s,
                "installed": s["id"] in installed_names or s["name"] in installed_names,
                "type": "mcp",
            })

        return {
            "builtin_mcp": available,
            "installed_mcp": installed,
        }
