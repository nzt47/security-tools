"""
网络传感器 — 我的"社交神经"监测器

采集网络接口状态、IP 配置、流量统计、WiFi 详情、连接统计等信息。
网络是我的社交神经，它让我与外部世界保持联系。
"""
import psutil
import logging
import platform
import locale
import socket
import subprocess
import re
import time
from .sensor_reading import SensorReading, Severity, Category, normal, warning, critical

_SYSTEM = platform.system()
_SYS_ENC = locale.getpreferredencoding() or 'utf-8'

# 连接状态码 → 文字描述
_NET_STATUS = {
    0: "已断开", 1: "正在连接", 2: "已连接", 3: "正在断开", 4: "硬件不存在",
    5: "硬件已禁用", 6: "硬件故障", 7: "无介质", 8: "正在验证",
}


class NetworkSensor:
    """网络传感器，负责监测社交神经状态"""

    CAPABILITIES = {
        "name": "network",
        "description": "网络（社交神经）— 连接、WiFi、带宽、延迟",
        "category": Category.NETWORK,
        "platforms": ["Windows", "Linux", "Darwin"],
        "dependencies": ["psutil"],
    }

    def __init__(self):
        self._category = Category.NETWORK
        self._prev_io = None
        self._prev_io_time = 0
        self._hostname = socket.gethostname()

    def collect(self):
        """
        全面采集网络状态。
        返回 SensorReading 列表。
        """
        results = []
        try:
            results.extend(self._collect_interfaces())
        except Exception as e:
            logging.error(f"采集网络接口失败: {e}")
        try:
            results.extend(self._collect_io())
        except Exception as e:
            logging.warning(f"采集网络流量失败: {e}")
        try:
            results.extend(self._collect_adapter_info())
        except Exception as e:
            logging.debug(f"采集网卡详情失败: {e}")
        try:
            results.extend(self._collect_ip_config())
        except Exception as e:
            logging.debug(f"采集 IP 配置失败: {e}")
        try:
            results.extend(self._collect_wifi_info())
        except Exception as e:
            logging.debug(f"采集 WiFi 详情失败: {e}")
        try:
            results.extend(self._collect_connections())
        except Exception as e:
            logging.warning(f"采集网络连接失败: {e}")
        try:
            results.extend(self._collect_hostname())
        except Exception as e:
            logging.warning(f"采集主机名失败: {e}")
        return results

    # ─── 接口地址 ───────────────────────────────────────────────

    def _collect_interfaces(self):
        """采集网络接口地址信息 — 我的网络身份"""
        readings = []
        addrs = psutil.net_if_addrs()
        for iface_name, iface_addrs in addrs.items():
            for addr in iface_addrs:
                if addr.family == socket.AF_INET:  # IPv4
                    readings.append(normal(
                        f"net_iface_{iface_name}_ipv4", addr.address, "",
                        f"网卡 {iface_name} IPv4 地址", self._category,
                        {"netmask": addr.netmask, "broadcast": addr.broadcast}
                    ))
                elif addr.family == socket.AF_INET6:  # IPv6
                    readings.append(normal(
                        f"net_iface_{iface_name}_ipv6", addr.address.split('%')[0], "",
                        f"网卡 {iface_name} IPv6 地址", self._category
                    ))
                elif addr.family == 17:  # AF_LINK (MAC)
                    readings.append(normal(
                        f"net_iface_{iface_name}_mac", addr.address, "",
                        f"网卡 {iface_name} MAC 地址", self._category
                    ))
        return readings

    # ─── 网卡详情（WMI） ─────────────────────────────────────────

    def _collect_adapter_info(self):
        """
        采集网卡详细硬件信息。

        对应任务管理器 → 性能 → 以太网/WiFi：
        - 网卡型号/制造商
        - 链路速度
        - 连接状态
        - MAC 地址（硬件）
        来源: WMI Win32_NetworkAdapter
        """
        readings = []
        if _SYSTEM != "Windows":
            return readings
        try:
            import wmi
            c = wmi.WMI()
            for nic in c.Win32_NetworkAdapter():
                # 只采集物理网卡和有意义的虚拟网卡
                name = getattr(nic, 'Name', '') or ''
                adapter_type = getattr(nic, 'AdapterType', '') or ''
                net_enabled = getattr(nic, 'NetEnabled', None)
                # 跳过没有启用且没有适配器类型的
                if not name or (not net_enabled and not adapter_type):
                    continue

                pnp_id = getattr(nic, 'PNPDeviceID', '') or ''
                # 跳过软件环回和隧道适配器
                if 'MS_L2TP' in pnp_id or 'MS_PPTP' in pnp_id or 'MS_PPPOE' in pnp_id:
                    continue

                manufacturer = getattr(nic, 'Manufacturer', '') or ''
                speed = getattr(nic, 'Speed', None)  # 比特/秒
                mac = getattr(nic, 'MACAddress', '') or ''
                conn_status = getattr(nic, 'NetConnectionStatus', None)
                conn_id = getattr(nic, 'NetConnectionID', '') or name
                service_name = getattr(nic, 'ServiceName', '') or ''
                description = getattr(nic, 'Description', name)

                # 推断网卡类型
                if 'Wireless' in description or 'Wi-Fi' in description or 'WiFi' in description:
                    nic_type = "WiFi"
                elif 'Virtual' in name or 'Hyper-V' in name or 'VirtualAdapter' in pnp_id:
                    nic_type = "虚拟网卡"
                elif 'Bluetooth' in description:
                    nic_type = "蓝牙"
                else:
                    nic_type = "以太网"

                # 链路速度友好格式化
                speed_str = None
                try:
                    speed_val = int(speed) if speed is not None else None
                    if speed_val is not None and speed_val > 0 and speed_val < 9223372036854775807:
                        if speed_val >= 1_000_000_000:
                            speed_str = f"{speed_val / 1_000_000_000:.0f} Gbps"
                        elif speed_val >= 1_000_000:
                            speed_str = f"{speed_val / 1_000_000:.0f} Mbps"
                        elif speed_val >= 1_000:
                            speed_str = f"{speed_val / 1_000:.0f} Kbps"
                except (ValueError, TypeError):
                    pass

                # 连接状态
                status_text = _NET_STATUS.get(conn_status, f"未知({conn_status})") if conn_status is not None else "未知"
                is_connected = conn_status == 2

                # 安全命名（避免特殊字符）
                safe_name = re.sub(r'[^a-zA-Z0-9_]', '_', conn_id.replace(' ', '_'))

                readings.append(normal(
                    f"net_adapter_{safe_name}", description, "",
                    f"网卡: {description}", self._category,
                    {
                        "manufacturer": manufacturer,
                        "type": nic_type,
                        "speed": speed_str,
                        "mac": mac,
                        "status": status_text,
                        "connected": is_connected,
                        "adapter_id": conn_id,
                        "service": service_name,
                        "pnp_id": pnp_id,
                        "source": "WMI Win32_NetworkAdapter",
                    }
                ))

                # 链路速度单独读数
                if speed_str and speed_val:
                    raw_speed = speed_val / 1_000_000  # → Mbps
                    readings.append(normal(
                        f"net_speed_{safe_name}", round(raw_speed, 0), "Mbps",
                        f"{conn_id} 链路速度", self._category
                    ))

                # 连接状态显式读数
                readings.append(normal(
                    f"net_status_{safe_name}", status_text, "",
                    f"{conn_id} 连接状态", self._category
                ))

        except ImportError:
            pass
        except Exception as e:
            logging.debug(f"网卡详情采集异常: {e}")

        return readings

    # ─── IP 配置（WMI） ─────────────────────────────────────────

    def _collect_ip_config(self):
        """
        采集 IP 配置详情。

        对应 ipconfig /all：
        - 默认网关
        - DNS 服务器
        - DHCP 状态 / DHCP 服务器
        来源: WMI Win32_NetworkAdapterConfiguration
        """
        readings = []
        if _SYSTEM != "Windows":
            return readings
        try:
            import wmi
            c = wmi.WMI()
            for cfg in c.Win32_NetworkAdapterConfiguration(IPEnabled=True):
                desc = getattr(cfg, 'Description', '') or ''
                if not desc:
                    continue

                # 取 MAC 地址的前 8 位作为安全键
                mac = getattr(cfg, 'MACAddress', '') or ''
                safe_key = mac.replace(':', '')[:8] if mac else desc.replace(' ', '_')[:20]

                # 默认网关
                gateways = getattr(cfg, 'DefaultIPGateway', None)
                if gateways:
                    gw = gateways[0] if isinstance(gateways, (list, tuple)) else gateways
                    readings.append(normal(
                        f"net_gateway_{safe_key}", gw, "",
                        f"{desc} 默认网关", self._category,
                        {"source": "WMI Win32_NetworkAdapterConfiguration"}
                    ))

                # DNS 服务器
                dns_list = getattr(cfg, 'DNSServerSearchOrder', None)
                if dns_list and isinstance(dns_list, (list, tuple)):
                    for i, dns in enumerate(dns_list):
                        readings.append(normal(
                            f"net_dns_{safe_key}_{i+1}", dns, "",
                            f"{desc} DNS 服务器 #{i+1}", self._category,
                            {"source": "WMI Win32_NetworkAdapterConfiguration"}
                        ))

                # DHCP 状态
                dhcp_enabled = getattr(cfg, 'DHCPEnabled', None)
                if dhcp_enabled is not None:
                    readings.append(normal(
                        f"net_dhcp_{safe_key}", "是" if dhcp_enabled else "否", "",
                        f"{desc} DHCP 状态", self._category,
                        {"enabled": bool(dhcp_enabled), "source": "WMI"}
                    ))
                    if dhcp_enabled:
                        dhcp_server = getattr(cfg, 'DHCPServer', '') or ''
                        if dhcp_server:
                            readings.append(normal(
                                f"net_dhcp_server_{safe_key}", dhcp_server, "",
                                f"{desc} DHCP 服务器", self._category
                            ))
        except ImportError:
            pass
        except Exception as e:
            logging.debug(f"IP 配置采集异常: {e}")
        return readings

    # ─── WiFi 详情（netsh） ─────────────────────────────────────

    def _collect_wifi_info(self):
        """
        采集 WiFi 无线详情。

        对应 任务管理器 → 性能 → WiFi / netsh wlan show interfaces：
        - SSID / BSSID
        - WiFi 协议 (802.11ac/ax/n)
        - 信号强度 (%)
        - 信道 / 频段
        - 收发速率 (Mbps)
        - 加密类型
        来源: netsh wlan show interfaces
        """
        readings = []
        if _SYSTEM != "Windows":
            return readings
        try:
            # Windows 中文系统使用 GBK 编码解码 netsh 输出
            enc = _SYS_ENC
            r = subprocess.run(
                ["netsh", "wlan", "show", "interfaces"],
                capture_output=True, text=True, encoding=enc, timeout=10
            )
            if r.returncode != 0 or not r.stdout.strip():
                return readings

            # 可能有多个 WiFi 接口
            sections = re.split(r'\n\s*\n', r.stdout.strip())
            for section in sections:
                if 'SSID' not in section and '信号' not in section:
                    continue
                if '已连接' not in section and '状态' not in section:
                    continue

                # 提取字段
                ssid = self._extract_netsh_field(section, r'SSID\s*:\s*(.+)')
                bssid = self._extract_netsh_field(section, r'BSSID\s*:\s*(.+)')
                proto = self._extract_netsh_field(section, r'无线电类型\s*:\s*(.+)')
                channel = self._extract_netsh_field(section, r'信道\s*:\s*(\d+)')
                signal = self._extract_netsh_field(section, r'信号\s*:\s*(\d+)%')
                rx_rate = self._extract_netsh_field(section, r'接收速率\s*\(Mbps\)\s*:\s*([\d.]+)')
                tx_rate = self._extract_netsh_field(section, r'传输速率\s*\(Mbps\)\s*:\s*([\d.]+)')
                auth = self._extract_netsh_field(section, r'身份验证\s*:\s*(.+)')
                cipher = self._extract_netsh_field(section, r'密码\s*:\s*(.+)')
                net_type = self._extract_netsh_field(section, r'网络类型\s*:\s*(.+)')
                state = self._extract_netsh_field(section, r'状态\s*:\s*(.+)')
                name = self._extract_netsh_field(section, r'名称\s*:\s*(.+)')

                if not ssid:
                    continue

                readings.append(normal(
                    "net_wifi_ssid", ssid, "",
                    f"WiFi SSID: {ssid}", self._category,
                    {"source": "netsh wlan"}
                ))
                if bssid:
                    readings.append(normal(
                        "net_wifi_bssid", bssid, "",
                        f"WiFi BSSID: {bssid}", self._category
                    ))
                if proto:
                    readings.append(normal(
                        "net_wifi_protocol", proto.strip(), "",
                        f"WiFi 协议: {proto.strip()}", self._category
                    ))
                if channel:
                    readings.append(normal(
                        "net_wifi_channel", int(channel), "",
                        "WiFi 信道", self._category
                    ))
                if signal:
                    sig_val = int(signal)
                    sig_sev = Severity.WARNING if sig_val < 30 else (
                        Severity.CRITICAL if sig_val < 20 else Severity.NORMAL
                    )
                    readings.append(SensorReading(
                        "net_wifi_signal", sig_val, "%",
                        "WiFi 信号强度", self._category, sig_sev,
                        {"source": "netsh wlan"}
                    ))
                if rx_rate:
                    readings.append(normal(
                        "net_wifi_rx_rate", float(rx_rate), "Mbps",
                        "WiFi 接收速率", self._category
                    ))
                if tx_rate:
                    readings.append(normal(
                        "net_wifi_tx_rate", float(tx_rate), "Mbps",
                        "WiFi 发送速率", self._category
                    ))
                if auth:
                    readings.append(normal(
                        "net_wifi_auth", auth.strip(), "",
                        f"WiFi 认证: {auth.strip()}", self._category
                    ))
                if cipher:
                    readings.append(normal(
                        "net_wifi_cipher", cipher.strip(), "",
                        f"WiFi 加密: {cipher.strip()}", self._category
                    ))
                break  # 只处理第一个连接的接口
        except FileNotFoundError:
            pass
        except Exception as e:
            logging.debug(f"WiFi 详情采集异常: {e}")
        return readings

    @staticmethod
    def _extract_netsh_field(text, pattern):
        """从 netsh 输出中提取字段"""
        m = re.search(pattern, text)
        return m.group(1).strip() if m else None

    # ─── I/O 统计（含实时速率） ─────────────────────────────────

    def _collect_io(self):
        """采集网络 I/O 统计 — 我的社交活跃度"""
        readings = []
        now = time.time()
        io = psutil.net_io_counters(pernic=True)

        for iface_name, stats in io.items():
            # 避免 Hyper-V 内部虚拟网卡噪音
            if ('Pseudo' in iface_name or 'Loopback' in iface_name or
                'Bluetooth' in iface_name):
                continue

            safe_name = re.sub(r'[^a-zA-Z0-9_]', '_', iface_name)

            # 累计值
            readings.append(normal(
                f"net_bytes_sent_{safe_name}",
                round(stats.bytes_sent / (1024**2), 2), "MB",
                f"网卡 {iface_name} 累计发送量", self._category
            ))
            readings.append(normal(
                f"net_bytes_recv_{safe_name}",
                round(stats.bytes_recv / (1024**2), 2), "MB",
                f"网卡 {iface_name} 累计接收量", self._category
            ))
            readings.append(normal(
                f"net_packets_sent_{safe_name}", stats.packets_sent, "个",
                f"网卡 {iface_name} 发送数据包数", self._category
            ))
            readings.append(normal(
                f"net_packets_recv_{safe_name}", stats.packets_recv, "个",
                f"网卡 {iface_name} 接收数据包数", self._category
            ))
            readings.append(normal(
                f"net_errin_{safe_name}", stats.errin, "个",
                f"网卡 {iface_name} 接收错误数", self._category
            ))
            readings.append(normal(
                f"net_errout_{safe_name}", stats.errout, "个",
                f"网卡 {iface_name} 发送错误数", self._category
            ))
            readings.append(normal(
                f"net_dropin_{safe_name}", stats.dropin, "个",
                f"网卡 {iface_name} 接收丢包数", self._category
            ))
            readings.append(normal(
                f"net_dropout_{safe_name}", stats.dropout, "个",
                f"网卡 {iface_name} 发送丢包数", self._category
            ))

            # 差值计算实时速率（类似任务管理器的网络使用率）
            if self._prev_io and iface_name in self._prev_io:
                prev = self._prev_io[iface_name]
                dt = max(now - self._prev_io_time, 0.001)

                d_sent = stats.bytes_sent - prev.bytes_sent
                d_recv = stats.bytes_recv - prev.bytes_recv

                if d_sent > 0:
                    send_speed = d_sent / dt
                    readings.append(normal(
                        f"net_send_rate_{safe_name}",
                        round(send_speed / 1024, 1), "KB/s",
                        f"网卡 {iface_name} 当前发送速率", self._category
                    ))
                if d_recv > 0:
                    recv_speed = d_recv / dt
                    readings.append(normal(
                        f"net_recv_rate_{safe_name}",
                        round(recv_speed / 1024, 1), "KB/s",
                        f"网卡 {iface_name} 当前接收速率", self._category
                    ))

        self._prev_io = io
        self._prev_io_time = now
        return readings

    # ─── 连接统计 ───────────────────────────────────────────────

    def _collect_connections(self):
        """采集网络连接统计 — 我的社交关系"""
        readings = []
        conns = psutil.net_connections(kind='inet')
        states = {}
        for conn in conns:
            s = conn.status
            states[s] = states.get(s, 0) + 1
        total = len(conns)
        readings.append(normal(
            "net_connections_total", total, "个",
            "网络连接总数（我的社交关系数）", self._category,
            {"by_status": {k: v for k, v in states.items()}}
        ))
        for status, count in states.items():
            readings.append(normal(
                f"net_connections_{status}", count, "个",
                f"网络连接状态: {status}", self._category
            ))
        return readings

    # ─── 主机名 ─────────────────────────────────────────────────

    def _collect_hostname(self):
        """采集主机名"""
        readings = []
        readings.append(normal(
            "net_hostname", self._hostname, "",
            "主机名（我的名字）", self._category
        ))
        return readings
