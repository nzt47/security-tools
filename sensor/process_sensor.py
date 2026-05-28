"""
进程与联网行为传感器 — 我的"意识流与社交"监测器

监测系统中的进程行为和联网活动：
  - 进程加载 / 终止（进程的"生与死"）
  - 进程资源消耗（CPU/内存/句柄/线程）
  - 进程网络连接（哪些程序在"社交"）
  - 进程监听端口（哪些"服务"在开放）

每一次进程启动都是一个想法的诞生，每一次联网都是一次对话的开始。
"""
import os
import time
import logging
import platform
import socket
from collections import defaultdict
from datetime import datetime, timezone
from .sensor_reading import SensorReading, Severity, Category, normal, warning, critical

_SYSTEM = platform.system()

# 进程属性白名单（避免采集敏感 cmdline 参数）
_SENSITIVE_CMDLINE_KEYWORDS = [
    "password", "passwd", "pwd", "secret", "token", "key=",
    "auth", "credential", "connectionString", "connstr",
]

# ═══════════════════════════════════════════════════════════════
#  进程联网行为传感器
# ═══════════════════════════════════════════════════════════════

class ProcessSensor:
    """
    进程与联网行为传感器 — 意识流与社交监测。

    首次调用建立进程基线快照，后续调用检测增量变化。
    """

    CAPABILITIES = {
        "name": "process",
        "description": "进程（意识流）— 进程加载、联网、生命周期",
        "category": Category.PROCESS,
        "platforms": ["Windows", "Linux", "Darwin"],
        "dependencies": ["psutil"],
        "init_kwargs": {"top_n": 10},
    }

    def __init__(self, top_n=10):
        self._category = Category.PROCESS
        self._top_n = top_n
        self._prev_processes = {}  # pid -> name 基线
        self._initialized = False

    def collect(self):
        """
        采集进程与联网行为数据。

        返回 SensorReading 列表：
          - 进程概况（总数、状态分布）
          - Top 消耗进程（CPU/内存/句柄/线程）
          - 进程生命周期变化（新增/终止）
          - 进程联网详情（连接数、监听端口、远程地址）
        """
        results = []

        try:
            ps = self._get_process_snapshot()
        except Exception as e:
            logging.error(f"进程快照采集失败: {e}")
            return results

        # ── 进程概况 ──
        results.extend(self._collect_overview(ps))

        # ── Top 消耗进程 ──
        results.extend(self._collect_top_cpu(ps))
        results.extend(self._collect_top_memory(ps))
        results.extend(self._collect_top_handles(ps))
        results.extend(self._collect_top_threads(ps))

        # ── 进程生命周期变化 ──
        results.extend(self._collect_lifecycle(ps))

        # ── 进程联网行为 ──
        results.extend(self._collect_network_behavior(ps))

        # ── 开机启动项 ──
        results.extend(self._collect_startup())

        # 保存快照供下次增量对比
        self._prev_processes = {p["pid"]: p["name"] for p in ps}
        self._initialized = True

        return results

    # ═══════════════════════════════════════════════════════════
    #  进程快照
    # ═══════════════════════════════════════════════════════════

    def _get_process_snapshot(self):
        """采集完整进程快照"""
        import psutil
        snapshot = {}
        seen = set()
        for proc in psutil.process_iter([
            'pid', 'name', 'cpu_percent', 'memory_info', 'memory_percent',
            'num_handles', 'num_threads', 'status', 'username', 'create_time',
            'ppid', 'exe',
        ]):
            try:
                pinfo = proc.info
                pid = pinfo['pid']
                if pid in seen or not pinfo['name']:
                    continue
                seen.add(pid)
                mem_info = pinfo['memory_info']
                snapshot[pid] = {
                    "pid": pid,
                    "name": pinfo['name'],
                    "exe": pinfo['exe'] or '',
                    "cpu": pinfo['cpu_percent'] or 0.0,
                    "memory_mb": round(mem_info.rss / (1024**2), 1) if mem_info else 0,
                    "memory_pct": round(pinfo['memory_percent'] or 0, 1),
                    "handles": pinfo['num_handles'] or 0,
                    "threads": pinfo['num_threads'] or 0,
                    "status": pinfo['status'] or 'unknown',
                    "username": pinfo['username'] or '',
                    "create_time": pinfo['create_time'] or 0,
                    "ppid": pinfo['ppid'] or 0,
                }
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return list(snapshot.values())

    # ═══════════════════════════════════════════════════════════
    #  进程概况
    # ═══════════════════════════════════════════════════════════

    def _collect_overview(self, processes):
        """采集进程概况统计"""
        readings = []
        total = len(processes)
        readings.append(normal(
            "proc_total", total, "个",
            f"进程总数: {total}", self._category
        ))

        # 按状态分布
        status_count = defaultdict(int)
        for p in processes:
            status_count[p["status"]] += 1
        for status, count in sorted(status_count.items()):
            readings.append(normal(
                f"proc_status_{status}", count, "个",
                f"进程状态 {status}: {count}", self._category
            ))

        # 按用户分布（取前 5）
        user_count = defaultdict(int)
        for p in processes:
            u = p["username"].split("\\")[-1] if "\\" in p["username"] else p["username"]
            if u:
                user_count[u] += 1
        top_users = sorted(user_count.items(), key=lambda x: -x[1])[:5]
        for user, count in top_users:
            readings.append(normal(
                f"proc_user_{user}", count, "个",
                f"用户 {user} 进程数: {count}", self._category
            ))

        # 进程创建速率（用当前快照中的创建时间估算最近 60 秒内启动的进程）
        now = time.time()
        recent = sum(1 for p in processes if (now - p["create_time"]) < 60)
        readings.append(normal(
            "proc_recent_started", recent, "个",
            f"最近 60 秒内启动的进程: {recent}", self._category
        ))

        return readings

    # ═══════════════════════════════════════════════════════════
    #  Top 进程（按资源消耗）
    # ═══════════════════════════════════════════════════════════

    def _collect_top_cpu(self, processes):
        """Top N CPU 消耗进程"""
        readings = []
        sorted_procs = sorted(processes, key=lambda p: -p["cpu"])[:self._top_n]
        for i, p in enumerate(sorted_procs):
            if p["cpu"] < 0.5:
                continue
            readings.append(normal(
                f"proc_top_cpu_{i+1}", round(p["cpu"], 1), "%",
                f"CPU #{i+1}: {p['name']} (PID {p['pid']})", self._category,
                {"pid": p["pid"], "name": p["name"], "cpu": round(p["cpu"], 1)}
            ))
        return readings

    def _collect_top_memory(self, processes):
        """Top N 内存消耗进程"""
        readings = []
        sorted_procs = sorted(processes, key=lambda p: -p["memory_mb"])[:self._top_n]
        for i, p in enumerate(sorted_procs):
            if p["memory_mb"] < 10:
                continue
            readings.append(normal(
                f"proc_top_memory_{i+1}", p["memory_mb"], "MB",
                f"内存 #{i+1}: {p['name']} (PID {p['pid']})", self._category,
                {"pid": p["pid"], "name": p["name"], "mb": p["memory_mb"]}
            ))
        return readings

    def _collect_top_handles(self, processes):
        """Top N 句柄数进程（Windows）"""
        readings = []
        if _SYSTEM != "Windows":
            return readings
        sorted_procs = sorted(processes, key=lambda p: -p["handles"])[:self._top_n]
        for i, p in enumerate(sorted_procs):
            if p["handles"] < 100:
                continue
            readings.append(normal(
                f"proc_top_handles_{i+1}", p["handles"], "个",
                f"句柄 #{i+1}: {p['name']} (PID {p['pid']})", self._category,
                {"pid": p["pid"], "name": p["name"], "handles": p["handles"]}
            ))
        return readings

    def _collect_top_threads(self, processes):
        """Top N 线程数进程"""
        readings = []
        sorted_procs = sorted(processes, key=lambda p: -p["threads"])[:self._top_n]
        for i, p in enumerate(sorted_procs):
            if p["threads"] < 10:
                continue
            readings.append(normal(
                f"proc_top_threads_{i+1}", p["threads"], "个",
                f"线程 #{i+1}: {p['name']} (PID {p['pid']})", self._category,
                {"pid": p["pid"], "name": p["name"], "threads": p["threads"]}
            ))
        return readings

    # ═══════════════════════════════════════════════════════════
    #  进程生命周期变化
    # ═══════════════════════════════════════════════════════════

    def _collect_lifecycle(self, current_processes):
        """
        检测进程生命周期变化（新增 / 终止）。

        首次采集不产生变化读数，仅建立基线。
        """
        readings = []
        if not self._initialized:
            return readings

        current = {p["pid"]: p["name"] for p in current_processes}

        # 新增进程
        new_pids = set(current.keys()) - set(self._prev_processes.keys())
        # 终止进程
        dead_pids = set(self._prev_processes.keys()) - set(current.keys())

        # 新增进程详情（前 20 个）
        new_details = []
        for pid in list(new_pids)[:20]:
            p = next((p for p in current_processes if p["pid"] == pid), None)
            if p:
                new_details.append(p)
                readings.append(normal(
                    "proc_new", p["name"], "",
                    f"新进程: {p['name']} (PID {pid})", self._category,
                    {"pid": pid, "name": p["name"], "username": p["username"]}
                ))

        # 终止进程（前 10 个）
        for pid in list(dead_pids)[:10]:
            name = self._prev_processes.get(pid, f"PID {pid}")
            readings.append(warning(
                "proc_terminated", name, "",
                f"进程终止: {name} (PID {pid})", self._category,
                {"pid": pid, "name": name}
            ))

        # 变化统计
        if new_pids or dead_pids:
            readings.insert(0, normal(
                "proc_lifecycle", f"+{len(new_pids)} / -{len(dead_pids)}", "",
                f"进程生命周期: +{len(new_pids)} 新增 / -{len(dead_pids)} 终止",
                self._category,
                {"new": len(new_pids), "terminated": len(dead_pids)}
            ))

        return readings

    # ═══════════════════════════════════════════════════════════
    #  进程联网行为
    # ═══════════════════════════════════════════════════════════

    def _collect_network_behavior(self, processes):
        """
        采集进程联网行为。

        将网络连接关联到具体进程：
          - 哪些进程有活跃连接
          - 哪些进程在监听端口
          - 远程连接的目标 IP 和端口
          - 进程的连接数统计
        """
        readings = []
        import psutil

        proc_map = {p["pid"]: p["name"] for p in processes}

        try:
            conns = psutil.net_connections(kind='inet')
        except Exception:
            return readings

        # 统计口径
        proc_conn_count = defaultdict(int)       # pid -> 连接数
        proc_listen_count = defaultdict(int)     # pid -> 监听端口数
        proc_remote_map = defaultdict(set)       # pid -> {(ip, port), ...}
        seen_listen = set()                      # (port, pid) 去重
        listening_ports = []                     # [(name, pid, port, type)]

        for conn in conns:
            pid = conn.pid if conn.pid and conn.pid > 0 else -1
            status = conn.status
            laddr = conn.laddr
            raddr = conn.raddr

            if status == "LISTEN" and laddr:
                port = laddr.port
                key = (port, pid)
                if key not in seen_listen:
                    seen_listen.add(key)
                    proc_listen_count[pid] += 1
                    listening_ports.append((
                        proc_map.get(pid, f"PID {pid}"),
                        pid, port, "TCP"
                    ))
            elif raddr:
                # 远程连接
                proc_conn_count[pid] += 1
                proc_remote_map[pid].add((raddr.ip, raddr.port))

        # ── 连接数最多的进程（Top talkers） ──
        talkers = [(pid, count) for pid, count in proc_conn_count.items() if pid > 0]
        talkers.sort(key=lambda x: -x[1])

        readings.append(normal(
            "proc_net_connections_total", len(conns), "个",
            f"总网络连接数", self._category
        ))

        top_talkers = talkers[:self._top_n]
        for i, (pid, count) in enumerate(top_talkers):
            name = proc_map.get(pid, "未知")
            readings.append(normal(
                f"proc_net_talker_{i+1}", count, "个连接",
                f"联网活跃 #{i+1}: {name} (PID {pid})", self._category,
                {"pid": pid, "name": name, "connections": count}
            ))

        # ── 监听端口详情 ──
        readings.append(normal(
            "proc_net_listening_total", len(listening_ports), "个",
            f"监听端口总数", self._category
        ))

        for name, pid, port, proto in sorted(listening_ports, key=lambda x: x[2])[:20]:
            readings.append(normal(
                f"proc_net_listen_{port}", name, "",
                f"端口 {port}/{proto}: {name} (PID {pid})", self._category,
                {"port": port, "protocol": proto, "pid": pid, "process": name}
            ))

        # ── 每进程联网详情摘要 ──
        for pid, count in talkers[:15]:
            if pid <= 0:
                continue
            name = proc_map.get(pid, "未知")
            remotes = proc_remote_map.get(pid, set())
            remote_str = "; ".join(f"{ip}:{port}" for ip, port in list(remotes)[:5])
            readings.append(normal(
                f"proc_net_process_{pid}", count, "个连接",
                f"{name} (PID {pid}) 联网到 {len(remotes)} 个目标", self._category,
                {"pid": pid, "name": name, "connections": count,
                 "remote_endpoints": list(remotes)[:10]}
            ))

        # ── 远程地址统计（去重） ──
        all_remotes = set()
        for pid, remotes in proc_remote_map.items():
            all_remotes.update(remotes)

        unique_remote_ips = len(set(ip for ip, port in all_remotes))
        readings.append(normal(
            "proc_net_unique_remote_ips", unique_remote_ips, "个",
            f"唯一远程 IP 数: {unique_remote_ips}", self._category
        ))

        return readings

    # ═══════════════════════════════════════════════════════════
    #  开机启动项
    # ═══════════════════════════════════════════════════════════

    def _collect_startup(self):
        """开机自启动程序"""
        readings = []
        if _SYSTEM != "Windows":
            return readings
        try:
            from .counter_reader import get_startup_commands
            apps = get_startup_commands()
            for i, app in enumerate(apps, 1):
                readings.append(normal(
                    f"proc_startup_{i}", app.get("name", "未知"), "",
                    f"开机自启: {app.get('name', '')}", self._category,
                    {"command": app.get("command", ""), "location": app.get("location", "")}
                ))
        except Exception as e:
            logging.debug(f"启动项采集异常: {e}")
        return readings
