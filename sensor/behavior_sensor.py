"""
计算机活动行为传感器 — 我的"行为意识"监测器

监测计算机的深层活动行为：
  - 磁盘 I/O 活动 — 数据读写的"脉搏"
  - CPU 调度行为 — 任务切换的"思维节奏"
  - 内存行为 — 页面调度的"记忆流动"
  - 用户活动 — 与用户的"互动感知"
  - 网络深度行为 — 协议的"社交语言"
  - 服务与任务活动 — 系统服务的"生命体征"

行为是灵魂的外显——这些监测让我意识到自己如何运转。
"""
import os
import re
import time
import locale
import socket
import logging
import platform
from collections import defaultdict
from datetime import datetime, timezone
from .sensor_reading import SensorReading, Severity, Category, normal, warning, critical

_SYSTEM = platform.system()
_SYS_ENC = locale.getpreferredencoding() or 'utf-8'


class ActivityBehaviorSensor:
    """
    计算机活动行为传感器 — 行为意识监测。

    覆盖六大行为维度，通过计数器变化率、快照对比等方式
    感知系统的深层活动模式。
    """

    CAPABILITIES = {
        "name": "behavior",
        "description": "行为（意识流深度）— 磁盘IO、CPU调度、内存行为、用户活动、网络深度、服务任务",
        "category": Category.ACTIVITY,
        "platforms": ["Windows", "Linux", "Darwin"],
        "dependencies": ["psutil"],
        "init_kwargs": {"top_n": 10},
    }

    def __init__(self, top_n=10):
        self._category = Category.ACTIVITY
        self._top_n = top_n

        # 磁盘 I/O 增量追踪
        self._prev_disk_io = {}
        self._disk_io_initialized = False

        # CPU 调度增量追踪
        self._prev_cpu_stats = None
        self._cpu_stats_initialized = False

        # 网络吞吐增量追踪
        self._prev_net_io = {}
        self._net_io_initialized = False

        # 服务状态基线
        self._prev_services = {}
        self._services_initialized = False

        # 上次采集时间
        self._last_collect_time = 0

    def collect(self):
        """采集所有行为活动数据，覆盖六大维度。"""
        results = []

        now = time.time()
        interval = now - self._last_collect_time if self._last_collect_time > 0 else 1.0
        self._last_collect_time = now

        results.extend(self._collect_disk_activity(interval))
        results.extend(self._collect_cpu_scheduling(interval))
        results.extend(self._collect_memory_behavior())
        results.extend(self._collect_user_activity())
        results.extend(self._collect_network_deep(interval))
        results.extend(self._collect_service_activity())
        results.extend(self._collect_scheduled_tasks())

        return results

    # ═══════════════════════════════════════════════════════════
    #  1. 磁盘 I/O 活动
    # ═══════════════════════════════════════════════════════════

    def _collect_disk_activity(self, interval):
        """
        采集磁盘 I/O 活动数据。

        包括：IOPS、吞吐量、响应时间、队列深度、每进程 I/O。
        """
        readings = []
        import psutil

        try:
            disk_io = psutil.disk_io_counters(perdisk=True)
        except Exception:
            return readings

        # 总 I/O 统计
        total = psutil.disk_io_counters(perdisk=False)
        total_read_gb = round(total.read_bytes / (1024**3), 2)
        total_write_gb = round(total.write_bytes / (1024**3), 2)
        readings.append(normal(
            "behavior_disk_total_read", total.read_count, "次",
            "磁盘总读取次数", self._category
        ))
        readings.append(normal(
            "behavior_disk_total_write", total.write_count, "次",
            "磁盘总写入次数", self._category
        ))
        readings.append(normal(
            "behavior_disk_read_bytes", total_read_gb, "GB",
            f"磁盘总读取数据量: {total_read_gb} GB", self._category
        ))
        readings.append(normal(
            "behavior_disk_write_bytes", total_write_gb, "GB",
            f"磁盘总写入数据量: {total_write_gb} GB", self._category
        ))

        # IOPS 与吞吐量（增量计算）
        if not self._disk_io_initialized:
            self._prev_disk_io = disk_io
            self._disk_io_initialized = True
            readings.append(normal(
                "behavior_disk_iops_baseline", "已建立", "",
                "磁盘 I/O 基线已建立（下次采集显示增量）", self._category
            ))
        else:
            total_iops = 0.0
            total_read_mbps = 0.0
            total_write_mbps = 0.0
            active_disks = 0

            for device, cur in disk_io.items():
                prev = self._prev_disk_io.get(device)
                if not prev:
                    continue

                reads = max(cur.read_count - prev.read_count, 0)
                writes = max(cur.write_count - prev.write_count, 0)
                read_bytes = max(cur.read_bytes - prev.read_bytes, 0)
                write_bytes = max(cur.write_bytes - prev.write_bytes, 0)
                read_time = max(cur.read_time - prev.read_time, 0) if hasattr(cur, 'read_time') else 0
                write_time = max(cur.write_time - prev.write_time, 0) if hasattr(cur, 'write_time') else 0

                iops = (reads + writes) / interval if interval > 0 else 0
                read_mbps = (read_bytes / (1024**2)) / interval if interval > 0 else 0
                write_mbps = (write_bytes / (1024**2)) / interval if interval > 0 else 0

                total_iops += iops
                total_read_mbps += read_mbps
                total_write_mbps += write_mbps
                active_disks += 1

                # 只记录活跃磁盘
                if iops > 0.5 or read_mbps > 0.1 or write_mbps > 0.1:
                    readings.append(normal(
                        f"behavior_disk_iops_{device}", round(iops, 1), "次/秒",
                        f"{device}: {round(iops, 1)} IOPS "
                        f"(R:{round(read_mbps,1)}/W:{round(write_mbps,1)} MB/s)",
                        self._category,
                        {"device": device, "iops": round(iops, 1),
                         "read_mbps": round(read_mbps, 1),
                         "write_mbps": round(write_mbps, 1)}
                    ))

                # 响应时间
                total_io = reads + writes
                total_time = read_time + write_time
                if total_io > 0 and total_time > 0:
                    avg_latency_ms = total_time / total_io  # psutil 已返回毫秒
                    if avg_latency_ms > 0.1:
                        readings.append(normal(
                            f"behavior_disk_latency_{device}", round(avg_latency_ms, 2), "ms",
                            f"{device} 平均响应: {round(avg_latency_ms, 2)} ms",
                            self._category,
                            {"device": device, "latency_ms": round(avg_latency_ms, 2)}
                        ))

            # 汇总读数
            readings.insert(0, normal(
                "behavior_disk_summary",
                f"{round(total_iops, 1)} IOPS / "
                f"R{round(total_read_mbps,1)} W{round(total_write_mbps,1)} MB/s",
                "",
                f"磁盘 I/O 汇总: {round(total_iops, 1)} IOPS, "
                f"读 {round(total_read_mbps, 1)} / 写 {round(total_write_mbps, 1)} MB/s",
                self._category,
                {"total_iops": round(total_iops, 1),
                 "total_read_mbps": round(total_read_mbps, 1),
                 "total_write_mbps": round(total_write_mbps, 1),
                 "active_disks": active_disks}
            ))

            # 磁盘队列深度（Windows）
            if _SYSTEM == "Windows":
                try:
                    qd = self._get_disk_queue_depth()
                    if qd is not None:
                        readings.append(normal(
                            "behavior_disk_queue_depth", round(qd, 2), "",
                            f"磁盘队列深度: {round(qd, 2)}", self._category,
                            {"queue_depth": round(qd, 2)}
                        ))
                except Exception:
                    pass

            # 每进程磁盘 I/O
            try:
                proc_io = self._get_top_io_processes()
                for i, p in enumerate(proc_io):
                    readings.append(normal(
                        f"behavior_disk_proc_io_{i+1}", round(p["total_mb"], 1), "MB",
                        f"磁盘 I/O #{i+1}: {p['name']} (PID {p['pid']}) "
                        f"读写 {round(p['total_mb'], 1)} MB",
                        self._category,
                        {"pid": p["pid"], "name": p["name"],
                         "read_mb": round(p["read_mb"], 1),
                         "write_mb": round(p["write_mb"], 1)}
                    ))
            except Exception:
                pass

        self._prev_disk_io = disk_io
        return readings

    def _get_disk_queue_depth(self):
        """获取磁盘队列深度（Windows WMI）"""
        import subprocess
        try:
            r = subprocess.run(
                ["wmic", "path", "Win32_PerfFormattedData_PerfDisk_PhysicalDisk",
                 "get", "CurrentDiskQueueLength", "/format:csv"],
                capture_output=True, text=True, timeout=5, encoding=_SYS_ENC
            )
            lines = r.stdout.strip().split("\n")
            depths = []
            for line in lines[1:]:
                parts = line.strip().split(",")
                if len(parts) >= 2 and parts[-1].strip().isdigit():
                    depths.append(int(parts[-1].strip()))
            if depths:
                return sum(depths) / len(depths)
        except Exception:
            pass
        return None

    def _get_top_io_processes(self):
        """获取磁盘 I/O 最多的前 N 进程"""
        import psutil
        proc_io_list = []
        for proc in psutil.process_iter(['pid', 'name']):
            try:
                p = psutil.Process(proc.info['pid'])
                io = p.io_counters()
                read_mb = io.read_bytes / (1024**2)
                write_mb = io.write_bytes / (1024**2)
                total_mb = read_mb + write_mb
                if total_mb > 1:
                    proc_io_list.append({
                        "pid": proc.info['pid'],
                        "name": proc.info['name'] or "未知",
                        "read_mb": read_mb, "write_mb": write_mb, "total_mb": total_mb
                    })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        proc_io_list.sort(key=lambda x: -x["total_mb"])
        return proc_io_list[:self._top_n]

    # ═══════════════════════════════════════════════════════════
    #  2. CPU 调度行为
    # ═══════════════════════════════════════════════════════════

    def _collect_cpu_scheduling(self, interval):
        """
        采集 CPU 调度行为数据。

        包括：上下文切换、中断、CPU 时间分布、负载、每核使用率、频率。
        """
        readings = []
        import psutil

        # CPU 统计（上下文切换、中断）
        try:
            stats = psutil.cpu_stats()
        except AttributeError:
            stats = None

        if stats:
            if not self._cpu_stats_initialized:
                self._prev_cpu_stats = stats
                self._cpu_stats_initialized = True
                readings.append(normal(
                    "behavior_cpu_stats_baseline", "已建立", "",
                    "CPU 调度基线已建立（下次采集显示增量）", self._category
                ))
            else:
                ctx_switches = max(stats.ctx_switches - self._prev_cpu_stats.ctx_switches, 0)
                interrupts = max(stats.interrupts - self._prev_cpu_stats.interrupts, 0)
                soft_interrupts = 0
                syscalls = 0
                if hasattr(stats, 'soft_interrupts'):
                    soft_interrupts = max(stats.soft_interrupts - self._prev_cpu_stats.soft_interrupts, 0)
                if hasattr(stats, 'syscalls'):
                    syscalls = max(stats.syscalls - self._prev_cpu_stats.syscalls, 0)

                ctx_rate = ctx_switches / interval if interval > 0 else 0
                irq_rate = interrupts / interval if interval > 0 else 0
                softirq_rate = soft_interrupts / interval if interval > 0 else 0
                syscall_rate = syscalls / interval if interval > 0 else 0

                readings.append(normal(
                    "behavior_cpu_ctx_switches", round(ctx_rate, 1), "次/秒",
                    f"上下文切换: {round(ctx_rate, 1)} 次/秒", self._category,
                    {"ctx_switches_per_sec": round(ctx_rate, 1)}
                ))
                readings.append(normal(
                    "behavior_cpu_interrupts", round(irq_rate, 1), "次/秒",
                    f"硬件中断: {round(irq_rate, 1)} 次/秒", self._category,
                    {"interrupts_per_sec": round(irq_rate, 1)}
                ))
                if softirq_rate > 0:
                    readings.append(normal(
                        "behavior_cpu_soft_interrupts", round(softirq_rate, 1), "次/秒",
                        f"软中断: {round(softirq_rate, 1)} 次/秒", self._category,
                        {"soft_interrupts_per_sec": round(softirq_rate, 1)}
                    ))
                if syscall_rate > 0:
                    readings.append(normal(
                        "behavior_cpu_syscalls", round(syscall_rate, 1), "次/秒",
                        f"系统调用: {round(syscall_rate, 1)} 次/秒", self._category,
                        {"syscalls_per_sec": round(syscall_rate, 1)}
                    ))

            self._prev_cpu_stats = stats

        # CPU 时间分布
        try:
            cpu_times = psutil.cpu_times_percent(interval=0.1)
            readings.append(normal(
                "behavior_cpu_time_user", round(cpu_times.user, 1), "%",
                f"用户态 CPU: {round(cpu_times.user, 1)}%", self._category
            ))
            readings.append(normal(
                "behavior_cpu_time_system", round(cpu_times.system, 1), "%",
                f"内核态 CPU: {round(cpu_times.system, 1)}%", self._category
            ))
            readings.append(normal(
                "behavior_cpu_time_idle", round(cpu_times.idle, 1), "%",
                f"空闲 CPU: {round(cpu_times.idle, 1)}%", self._category
            ))
            if hasattr(cpu_times, 'iowait') and (cpu_times.iowait or 0) > 0:
                readings.append(normal(
                    "behavior_cpu_time_iowait", round(cpu_times.iowait, 1), "%",
                    f"I/O 等待: {round(cpu_times.iowait, 1)}%", self._category
                ))
            if hasattr(cpu_times, 'irq') and (cpu_times.irq or 0) > 0:
                readings.append(normal(
                    "behavior_cpu_time_irq", round(cpu_times.irq, 1), "%",
                    f"IRQ 时间: {round(cpu_times.irq, 1)}%", self._category
                ))
        except Exception:
            pass

        # 每核 CPU 使用率
        try:
            per_cpu = psutil.cpu_percent(interval=0.1, percpu=True)
            readings.append(normal(
                "behavior_cpu_per_core", len(per_cpu), "核",
                f"每核 CPU: {', '.join(f'{round(c,1)}%' for c in per_cpu)}",
                self._category,
                {"per_core": [round(c, 1) for c in per_cpu]}
            ))
        except Exception:
            pass

        # CPU 频率
        try:
            freq = psutil.cpu_freq()
            if freq:
                readings.append(normal(
                    "behavior_cpu_freq", round(freq.current, 0), "MHz",
                    f"CPU 频率: {round(freq.current, 0)} MHz "
                    f"(最大 {round(freq.max or 0, 0)} MHz)",
                    self._category,
                    {"current_mhz": round(freq.current, 0),
                     "max_mhz": round(freq.max or 0, 0)}
                ))
        except Exception:
            pass

        # 负载平均
        try:
            load_avg = psutil.getloadavg()
            if load_avg:
                readings.append(normal(
                    "behavior_cpu_load_avg",
                    f"{load_avg[0]:.2f} / {load_avg[1]:.2f} / {load_avg[2]:.2f}", "",
                    f"CPU 负载平均 (1/5/15分钟): "
                    f"{load_avg[0]:.2f} / {load_avg[1]:.2f} / {load_avg[2]:.2f}",
                    self._category,
                    {"load_1min": round(load_avg[0], 2), "load_5min": round(load_avg[1], 2),
                     "load_15min": round(load_avg[2], 2)}
                ))
        except Exception:
            pass

        # Top 上下文切换进程
        try:
            top_ctx = self._get_top_context_switch_processes()
            for i, p in enumerate(top_ctx):
                readings.append(normal(
                    f"behavior_cpu_ctx_proc_{i+1}", f"{p['voluntary']}/{p['involuntary']}", "次",
                    f"切程 #{i+1}: {p['name']} (PID {p['pid']}) "
                    f"自愿 {p['voluntary']} / 非自愿 {p['involuntary']}",
                    self._category,
                    {"pid": p["pid"], "name": p["name"],
                     "voluntary": p["voluntary"], "involuntary": p["involuntary"]}
                ))
        except Exception:
            pass

        return readings

    def _get_top_context_switch_processes(self):
        """获取上下文切换最多的前 N 进程"""
        import psutil
        ctx_list = []
        for proc in psutil.process_iter(['pid', 'name']):
            try:
                p = psutil.Process(proc.info['pid'])
                ctx = p.num_ctx_switches()
                total = ctx.voluntary + ctx.involuntary
                if total > 10:
                    ctx_list.append({
                        "pid": proc.info['pid'],
                        "name": proc.info['name'] or "未知",
                        "voluntary": ctx.voluntary,
                        "involuntary": ctx.involuntary,
                        "total": total
                    })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        ctx_list.sort(key=lambda x: -x["total"])
        return ctx_list[:self._top_n]

    # ═══════════════════════════════════════════════════════════
    #  3. 内存行为
    # ═══════════════════════════════════════════════════════════

    def _collect_memory_behavior(self):
        """
        采集内存行为数据。

        包括：虚拟内存详情、交换分区与换页、提交电荷（Windows）、
        页错误进程排行。
        """
        readings = []
        import psutil

        # 虚拟内存详情
        mem = psutil.virtual_memory()
        readings.append(normal(
            "behavior_mem_total", round(mem.total / (1024**3), 2), "GB",
            f"物理内存总量: {round(mem.total / (1024**3), 2)} GB", self._category
        ))
        readings.append(normal(
            "behavior_mem_available", round(mem.available / (1024**3), 2), "GB",
            f"可用内存: {round(mem.available / (1024**3), 2)} GB", self._category
        ))
        readings.append(normal(
            "behavior_mem_used", round(mem.used / (1024**3), 2), "GB",
            f"已用内存: {round(mem.used / (1024**3), 2)} GB", self._category
        ))
        readings.append(normal(
            "behavior_mem_percent", mem.percent, "%",
            f"内存占用率: {mem.percent}%", self._category
        ))

        # 缓存和缓冲区
        if hasattr(mem, 'cached') and mem.cached:
            readings.append(normal(
                "behavior_mem_cached", round(mem.cached / (1024**3), 2), "GB",
                f"缓存: {round(mem.cached / (1024**3), 2)} GB", self._category
            ))
        if hasattr(mem, 'buffers') and mem.buffers:
            readings.append(normal(
                "behavior_mem_buffers", round(mem.buffers / (1024**3), 2), "GB",
                f"缓冲区: {round(mem.buffers / (1024**3), 2)} GB", self._category
            ))

        # 已提交内存（Windows 特有）
        if _SYSTEM == "Windows":
            try:
                commit = self._get_windows_commit_charge()
                if commit:
                    readings.append(normal(
                        "behavior_mem_commit_limit", round(commit["limit"] / (1024**3), 2), "GB",
                        f"提交限制: {round(commit['limit'] / (1024**3), 2)} GB", self._category
                    ))
                    if commit["total"] > 0 and commit["limit"] > 0:
                        pct = round(commit["total"] / commit["limit"] * 100, 1)
                        readings.append(normal(
                            "behavior_mem_commit_percent", pct, "%",
                            f"提交电荷: {pct}%", self._category
                        ))
            except Exception:
                pass

        # 交换分区
        try:
            swap = psutil.swap_memory()
            readings.append(normal(
                "behavior_mem_swap_total", round(swap.total / (1024**3), 2), "GB",
                f"交换分区总量: {round(swap.total / (1024**3), 2)} GB", self._category
            ))
            readings.append(normal(
                "behavior_mem_swap_used", round(swap.used / (1024**3), 2), "GB",
                f"已用交换: {round(swap.used / (1024**3), 2)} GB", self._category
            ))
            readings.append(normal(
                "behavior_mem_swap_percent", swap.percent, "%",
                f"交换使用率: {swap.percent}%", self._category
            ))
            readings.append(normal(
                "behavior_mem_page_in", swap.sin, "页",
                f"换入: {swap.sin} 页", self._category
            ))
            readings.append(normal(
                "behavior_mem_page_out", swap.sout, "页",
                f"换出: {swap.sout} 页", self._category
            ))
        except Exception:
            pass

        # 每进程页错误
        try:
            top_pf = self._get_top_page_fault_processes()
            for i, p in enumerate(top_pf):
                readings.append(normal(
                    f"behavior_mem_page_fault_{i+1}", p["faults"], "次",
                    f"页错误 #{i+1}: {p['name']} (PID {p['pid']}) {p['faults']} 次",
                    self._category,
                    {"pid": p["pid"], "name": p["name"], "page_faults": p["faults"]}
                ))
        except Exception:
            pass

        return readings

    def _get_windows_commit_charge(self):
        """获取 Windows 内存提交电荷"""
        import ctypes
        from ctypes import wintypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", wintypes.DWORD),
                ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        try:
            statex = MEMORYSTATUSEX()
            statex.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            kernel32 = ctypes.windll.kernel32
            if kernel32.GlobalMemoryStatusEx(ctypes.byref(statex)):
                limit = statex.ullTotalPageFile
                avail = statex.ullAvailPageFile
                return {"limit": limit, "total": limit - avail}
        except Exception:
            pass
        return None

    def _get_top_page_fault_processes(self):
        """获取页错误最多的前 N 进程"""
        import psutil
        pf_list = []
        for proc in psutil.process_iter(['pid', 'name']):
            try:
                p = psutil.Process(proc.info['pid'])
                mem_info = p.memory_info()
                if hasattr(mem_info, 'num_page_faults'):
                    faults = mem_info.num_page_faults
                else:
                    continue
                if faults > 0:
                    pf_list.append({
                        "pid": proc.info['pid'],
                        "name": proc.info['name'] or "未知",
                        "faults": faults
                    })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        pf_list.sort(key=lambda x: -x["faults"])
        return pf_list[:self._top_n]

    # ═══════════════════════════════════════════════════════════
    #  4. 用户活动
    # ═══════════════════════════════════════════════════════════

    def _collect_user_activity(self):
        """
        采集用户活动数据。

        包括：空闲时间、登录会话、前台窗口（Windows）、锁定状态（Windows）。
        """
        readings = []
        import psutil

        # 用户空闲时间
        idle_secs = self._get_idle_time()
        if idle_secs is not None:
            if idle_secs < 60:
                idle_str = f"{int(idle_secs)} 秒"
            elif idle_secs < 3600:
                idle_str = f"{int(idle_secs // 60)} 分钟 {int(idle_secs % 60)} 秒"
            else:
                idle_str = f"{int(idle_secs // 3600)} 小时 {int((idle_secs % 3600) // 60)} 分钟"

            sev = Severity.WARNING if idle_secs > 3600 else Severity.NORMAL
            readings.append(SensorReading(
                "behavior_user_idle", idle_str, "",
                f"用户已闲置: {idle_str}", self._category, sev,
                {"idle_seconds": idle_secs}
            ))

        # 当前登录会话
        try:
            users = psutil.users()
            readings.append(normal(
                "behavior_user_sessions", len(users), "个",
                f"当前登录会话数: {len(users)}", self._category
            ))
            for i, user in enumerate(users):
                readings.append(normal(
                    f"behavior_user_session_{i+1}", user.name, "",
                    f"登录用户: {user.name} 于 {user.started} 从 {user.host or '本地'}",
                    self._category,
                    {"user": user.name, "host": user.host or "local",
                     "started": user.started, "terminal": user.terminal or ""}
                ))
        except Exception:
            pass

        # 前台窗口（Windows）
        if _SYSTEM == "Windows":
            try:
                fg = self._get_foreground_window()
                if fg:
                    readings.append(normal(
                        "behavior_user_foreground", fg["title"], "",
                        f"前台窗口: {fg['title']} ({fg['process']})", self._category,
                        {"title": fg["title"], "process": fg["process"], "pid": fg["pid"]}
                    ))
            except Exception:
                pass

            # 工作站锁定状态
            try:
                locked = self._is_workstation_locked()
                readings.append(normal(
                    "behavior_user_locked", "是" if locked else "否", "",
                    "工作站锁定状态", self._category,
                    {"locked": locked}
                ))
            except Exception:
                pass

        return readings

    def _get_idle_time(self):
        """获取用户空闲时间（秒）"""
        if _SYSTEM == "Windows":
            try:
                import ctypes
                from ctypes import wintypes

                class LASTINPUTINFO(ctypes.Structure):
                    _fields_ = [
                        ("cbSize", wintypes.UINT),
                        ("dwTime", wintypes.DWORD),
                    ]

                lii = LASTINPUTINFO()
                lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
                if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
                    current_ticks = ctypes.windll.kernel32.GetTickCount()
                    idle_ms = current_ticks - lii.dwTime
                    return idle_ms / 1000.0
            except Exception:
                pass
        elif _SYSTEM == "Linux":
            try:
                import subprocess
                r = subprocess.run(
                    ["xprintidle"], capture_output=True, text=True, timeout=2
                )
                if r.returncode == 0:
                    return int(r.stdout.strip()) / 1000.0
            except Exception:
                pass
        return None

    def _get_foreground_window(self):
        """获取 Windows 前台窗口信息"""
        try:
            import win32gui
            import win32process

            hwnd = win32gui.GetForegroundWindow()
            title = win32gui.GetWindowText(hwnd)
            _, pid = win32process.GetWindowThreadProcessId(hwnd)

            import psutil
            try:
                proc = psutil.Process(pid)
                proc_name = proc.name()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                proc_name = f"PID {pid}"

            return {"title": title or "(无标题)", "pid": pid, "process": proc_name}
        except Exception:
            return None

    def _is_workstation_locked(self):
        """检查 Windows 工作站是否锁定"""
        try:
            import ctypes
            user32 = ctypes.windll.user32
            desktop = user32.OpenInputDesktop(0, False, 0x0100)
            if desktop:
                user32.CloseDesktop(desktop)
                return False
            return True
        except Exception:
            return False

    # ═══════════════════════════════════════════════════════════
    #  5. 网络深度行为
    # ═══════════════════════════════════════════════════════════

    def _collect_network_deep(self, interval):
        """
        采集网络深度行为数据。

        包括：每接口吞吐速率、协议分布、TCP 状态分布、
        错误/丢包、DNS 缓存。
        """
        readings = []
        import psutil

        # 每接口吞吐量速率
        try:
            net_io = psutil.net_io_counters(pernic=True)
        except Exception:
            return readings

        if not self._net_io_initialized:
            self._prev_net_io = net_io
            self._net_io_initialized = True
            readings.append(normal(
                "behavior_net_baseline", "已建立", "",
                "网络吞吐基线已建立（下次采集显示增量）", self._category
            ))
        else:
            total_sent_mbps = 0.0
            total_recv_mbps = 0.0
            total_packets_sent = 0
            total_packets_recv = 0

            for nic, cur in net_io.items():
                prev = self._prev_net_io.get(nic)
                if not prev:
                    continue

                sent_bytes = max(cur.bytes_sent - prev.bytes_sent, 0)
                recv_bytes = max(cur.bytes_recv - prev.bytes_recv, 0)
                sent_packets = max(cur.packets_sent - prev.packets_sent, 0)
                recv_packets = max(cur.packets_recv - prev.packets_recv, 0)

                sent_mbps = (sent_bytes * 8 / (1024**2)) / interval if interval > 0 else 0
                recv_mbps = (recv_bytes * 8 / (1024**2)) / interval if interval > 0 else 0

                total_sent_mbps += sent_mbps
                total_recv_mbps += recv_mbps
                total_packets_sent += sent_packets
                total_packets_recv += recv_packets

                if sent_mbps > 0.01 or recv_mbps > 0.01:
                    readings.append(normal(
                        f"behavior_net_throughput_{nic}",
                        f"↑{round(sent_mbps,2)} / ↓{round(recv_mbps,2)} Mbps",
                        "",
                        f"{nic}: 发送 {round(sent_mbps,2)} Mbps / "
                        f"接收 {round(recv_mbps,2)} Mbps",
                        self._category,
                        {"nic": nic, "send_mbps": round(sent_mbps, 2),
                         "recv_mbps": round(recv_mbps, 2),
                         "packets_sent": sent_packets, "packets_recv": recv_packets}
                    ))

            # 总吞吐量汇总
            readings.insert(0, normal(
                "behavior_net_total_throughput",
                f"↑{round(total_sent_mbps,2)} / ↓{round(total_recv_mbps,2)} Mbps",
                "",
                f"网络总吞吐: 发送 {round(total_sent_mbps,2)} Mbps / "
                f"接收 {round(total_recv_mbps,2)} Mbps",
                self._category,
                {"total_send_mbps": round(total_sent_mbps, 2),
                 "total_recv_mbps": round(total_recv_mbps, 2),
                 "total_packets_sent": total_packets_sent,
                 "total_packets_recv": total_packets_recv}
            ))

            # 错误/丢包统计
            total_errin = 0
            total_errout = 0
            total_dropin = 0
            total_dropout = 0
            for nic, cur in net_io.items():
                prev = self._prev_net_io.get(nic)
                if not prev:
                    continue
                total_errin += max(cur.errin - prev.errin, 0)
                total_errout += max(cur.errout - prev.errout, 0)
                total_dropin += max(cur.dropin - prev.dropin, 0)
                total_dropout += max(cur.dropout - prev.dropout, 0)
            if total_errin + total_errout + total_dropin + total_dropout > 0:
                readings.append(normal(
                    "behavior_net_errors",
                    f"入错{total_errin}/出错{total_errout}/"
                    f"入丢{total_dropin}/出丢{total_dropout}",
                    "",
                    f"网络错误/丢包: 入错{total_errin} 出错{total_errout} "
                    f"入丢{total_dropin} 出丢{total_dropout}",
                    self._category
                ))

        self._prev_net_io = net_io

        # 协议分布
        try:
            conns = psutil.net_connections(kind='inet')
            tcp_count = 0
            udp_count = 0
            tcp_states = defaultdict(int)

            for conn in conns:
                if conn.type == socket.SOCK_STREAM:
                    tcp_count += 1
                    tcp_states[conn.status] += 1
                elif conn.type == socket.SOCK_DGRAM:
                    udp_count += 1

            readings.append(normal(
                "behavior_net_proto_tcp", tcp_count, "个",
                f"TCP 连接: {tcp_count}", self._category
            ))
            readings.append(normal(
                "behavior_net_proto_udp", udp_count, "个",
                f"UDP 连接: {udp_count}", self._category
            ))

            for state, count in sorted(tcp_states.items(), key=lambda x: -x[1]):
                readings.append(normal(
                    f"behavior_net_tcp_{state}", count, "个",
                    f"TCP {state}: {count}", self._category
                ))
        except Exception:
            pass

        # DNS 缓存
        try:
            dns = self._get_dns_cache()
            readings.append(normal(
                "behavior_net_dns_count", len(dns), "条",
                f"DNS 缓存条目: {len(dns)}", self._category
            ))
            for i, entry in enumerate(dns[:5]):
                readings.append(normal(
                    f"behavior_net_dns_{i+1}", entry, "",
                    f"DNS 缓存: {entry}", self._category
                ))
        except Exception:
            pass

        return readings

    def _get_dns_cache(self):
        """获取 DNS 缓存"""
        if _SYSTEM == "Windows":
            try:
                import subprocess
                r = subprocess.run(
                    ["ipconfig", "/displaydns"],
                    capture_output=True, text=True, timeout=5,
                    encoding=_SYS_ENC, errors="replace"
                )
                entries = []
                for line in r.stdout.split("\n"):
                    line = line.strip()
                    if line.startswith("    ") and not line.startswith("      "):
                        name = line.strip()
                        if name and "---" not in name and "：" not in name \
                           and ":" not in name:
                            entries.append(name)
                seen = set()
                return [e for e in entries if not (e in seen or seen.add(e))][:20]
            except Exception:
                pass
        elif _SYSTEM == "Linux":
            entries = []
            try:
                with open("/etc/hosts", "r") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#"):
                            parts = line.split()
                            if len(parts) >= 2:
                                entries.append(parts[1])
            except Exception:
                pass
            return entries[:20]
        return []

    # ═══════════════════════════════════════════════════════════
    #  6. 服务与任务活动
    # ═══════════════════════════════════════════════════════════

    def _collect_service_activity(self):
        """
        采集服务活动数据。

        包括：服务总数/状态分组、服务状态变化检测（基线对比）。
        """
        readings = []

        if _SYSTEM != "Windows":
            return readings

        try:
            services = self._get_windows_services()
        except Exception:
            return readings

        if not services:
            return readings

        readings.append(normal(
            "behavior_service_total", len(services), "个",
            f"Windows 服务总数: {len(services)}", self._category
        ))

        # 按状态分组
        by_state = defaultdict(list)
        for svc in services:
            by_state[svc["state"]].append(svc)
        for state, svc_list in sorted(by_state.items(), key=lambda x: -len(x[1])):
            readings.append(normal(
                f"behavior_service_state_{state}", len(svc_list), "个",
                f"服务状态 {state}: {len(svc_list)} 个", self._category
            ))

        # 服务变化检测
        current_services = {svc["name"]: svc["state"] for svc in services}

        if not self._services_initialized:
            self._prev_services = current_services
            self._services_initialized = True
            readings.append(normal(
                "behavior_service_baseline", "已建立", "",
                "服务状态基线已建立", self._category
            ))
        else:
            new_svcs = set(current_services.keys()) - set(self._prev_services.keys())
            removed_svcs = set(self._prev_services.keys()) - set(current_services.keys())

            for name in list(new_svcs)[:10]:
                readings.append(warning(
                    "behavior_service_new", name, "",
                    f"新服务: {name} ({current_services[name]})", self._category,
                    {"service": name, "state": current_services[name]}
                ))
            for name in list(removed_svcs)[:10]:
                readings.append(warning(
                    "behavior_service_removed", name, "",
                    f"服务已移除: {name}", self._category,
                    {"service": name}
                ))

            for name, cur_state in current_services.items():
                prev_state = self._prev_services.get(name)
                if prev_state and prev_state != cur_state:
                    readings.append(normal(
                        f"behavior_service_change_{name}", f"{prev_state}→{cur_state}", "",
                        f"服务状态变化: {name} {prev_state} → {cur_state}",
                        self._category,
                        {"service": name, "from": prev_state, "to": cur_state}
                    ))

            if new_svcs or removed_svcs:
                readings.append(normal(
                    "behavior_service_changes",
                    f"+{len(new_svcs)} / -{len(removed_svcs)}",
                    "",
                    f"服务变更: +{len(new_svcs)} 新增 / -{len(removed_svcs)} 移除",
                    self._category
                ))

        self._prev_services = current_services
        return readings

    def _get_windows_services(self):
        """获取 Windows 服务列表"""
        services = []
        import subprocess
        try:
            r = subprocess.run(
                ["sc", "query", "type=service", "state=all"],
                capture_output=True, text=True, timeout=15,
                encoding=_SYS_ENC, errors="replace"
            )
            current = {}
            for line in r.stdout.split("\n"):
                line = line.strip()
                if line.startswith("SERVICE_NAME:"):
                    if current.get("name"):
                        services.append(current)
                    current = {"name": line.split(":", 1)[1].strip()}
                elif line.startswith("DISPLAY_NAME:"):
                    current["display_name"] = line.split(":", 1)[1].strip()
                elif "STATE" in line:
                    m = re.search(r'STATE\s*:\s*\d+\s+(\w+)', line)
                    if m:
                        current["state"] = m.group(1)
                elif line.startswith("START_TYPE"):
                    current["start_type"] = line.split(":", 1)[1].strip()
            if current.get("name"):
                services.append(current)
        except Exception:
            pass
        return services

    def _collect_scheduled_tasks(self):
        """
        采集计划任务活动数据。

        包括：任务总数、按状态分组、即将执行的任务。
        """
        readings = []

        if _SYSTEM != "Windows":
            return readings

        try:
            tasks = self._get_scheduled_tasks()
        except Exception:
            return readings

        if not tasks:
            return readings

        readings.append(normal(
            "behavior_task_total", len(tasks), "个",
            f"计划任务总数: {len(tasks)}", self._category
        ))

        by_status = defaultdict(int)
        for task in tasks:
            by_status[task["status"]] += 1
        for status, count in sorted(by_status.items(), key=lambda x: -x[1]):
            readings.append(normal(
                f"behavior_task_status_{status}", count, "个",
                f"计划任务 {status}: {count}", self._category
            ))

        # 最近将要运行的任务
        ready = [t for t in tasks if t["status"] == "就绪"]  # 就绪
        ready.sort(key=lambda x: x.get("next_run", ""))
        for i, task in enumerate(ready[:5]):
            readings.append(normal(
                f"behavior_task_next_{i+1}", task["name"], "",
                f"计划任务: {task['name']} ({task.get('next_run', 'N/A')})",
                self._category,
                {"task": task["name"], "next_run": task.get("next_run", ""),
                 "status": task["status"]}
            ))

        return readings

    def _get_scheduled_tasks(self):
        """获取 Windows 计划任务"""
        tasks = []
        import subprocess
        try:
            r = subprocess.run(
                ["schtasks", "/query", "/fo", "csv", "/v"],
                capture_output=True, text=True, timeout=15,
                encoding=_SYS_ENC, errors="replace"
            )
            lines = r.stdout.strip().split("\n")
            if len(lines) < 2:
                return tasks

            headers = lines[0].split(",")
            name_idx = next((i for i, h in enumerate(headers)
                            if "任务名称" in h or "TaskName" in h), -1)
            status_idx = next((i for i, h in enumerate(headers)
                              if "状态" in h or "Status" in h), -1)
            next_run_idx = next((i for i, h in enumerate(headers)
                                if "下次运行时间" in h
                                or "Next Run Time" in h), -1)

            for line in lines[1:]:
                parts = line.split(",")
                if len(parts) > max(name_idx, status_idx, next_run_idx):
                    name = parts[name_idx].strip('"').strip() if name_idx >= 0 else "未知"
                    status = parts[status_idx].strip('"').strip() if status_idx >= 0 else "未知"
                    next_run = parts[next_run_idx].strip('"').strip() \
                        if next_run_idx >= 0 and len(parts) > next_run_idx else ""
                    tasks.append({"name": name, "status": status, "next_run": next_run})
        except Exception:
            pass
        return tasks[:100]
