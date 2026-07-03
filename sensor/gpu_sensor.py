"""
GPU 传感器 — 我的"视觉皮层"监测器

采集 GPU 温度、显存占用、使用率、功耗、风扇转速等信息。
GPU 像是我的视觉皮层，帮助我处理图像和并行计算任务。
"""
import logging
import platform
from .sensor_reading import SensorReading, Severity, Category, normal, warning, critical

# GPU 库检测
_GPUtil = None
_pynvml = None
_GPUtil_available = False
_pynvml_available = False

try:
    import GPUtil as _GPUtil
    _GPUtil_available = True
except ImportError:
    pass

try:
    import warnings
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=FutureWarning, message=".*pynvml.*deprecated.*")
        import pynvml as _pynvml
    _pynvml_available = True
except Exception:
    _pynvml_available = False

_pynvml_initialized = False


class GPUSensor:
    """GPU 传感器，负责监测视觉皮层状态"""

    CAPABILITIES = {
        "name": "gpu",
        "description": "GPU（视觉皮层）— 显卡温度、显存、负载",
        "category": Category.GPU,
        "platforms": ["Windows", "Linux", "Darwin"],
        "dependencies": ["pynvml"],
        "enabled_by_default": True,   # GTX 1650 已可用
    }

    def __init__(self):
        self._category = Category.GPU
        self._gpu_count = 0
        if _pynvml_available:
            global _pynvml_initialized
            try:
                if not _pynvml_initialized:
                    _pynvml.nvmlInit()
                    _pynvml_initialized = True
                self._gpu_count = _pynvml.nvmlDeviceGetCount()
            except Exception:
                self._gpu_count = 0
        # 备选：通过 nvidia-smi 检测 GPU（即使 pynvml 不可用）
        if self._gpu_count == 0:
            try:
                from .counter_reader import get_nvidia_smi
                gpus = get_nvidia_smi()
                self._gpu_count = len(gpus)
            except Exception:
                pass

    def collect(self):
        """
        全面采集 GPU 状态信息。
        返回 SensorReading 列表。
        """
        results = []
        if self._gpu_count == 0:
            logging.info("未检测到独立 GPU，我是纯 CPU 思考模式。")
            return results

        try:
            results.extend(self._collect_pynvml())
        except Exception as e:
            logging.error(f"pynvml 采集 GPU 信息失败: {e}")
        try:
            results.extend(self._collect_gputil())
        except Exception as e:
            logging.warning(f"GPUtil 采集 GPU 信息失败: {e}")
        try:
            results.extend(self._collect_nvidia_smi())
        except Exception as e:
            logging.debug(f"nvidia-smi 补充采集失败: {e}")
        return results

    def _collect_pynvml(self):
        """通过 NVIDIA NVML 采集 GPU 详细信息"""
        readings = []
        for i in range(self._gpu_count):
            try:
                handle = _pynvml.nvmlDeviceGetHandleByIndex(i)
                name = _pynvml.nvmlDeviceGetName(handle)
                if isinstance(name, bytes):
                    name = name.decode("utf-8")
                prefix = f"gpu_{i}"

                # GPU 使用率
                try:
                    util = _pynvml.nvmlDeviceGetUtilizationRates(handle)
                    sev = Severity.CRITICAL if util.gpu > 90 else (
                        Severity.WARNING if util.gpu > 70 else Severity.NORMAL
                    )
                    readings.append(SensorReading(
                        f"{prefix}_load", util.gpu, "%",
                        f"GPU{i} 核心使用率 ({name})", self._category, sev
                    ))
                    readings.append(normal(
                        f"{prefix}_mem_controller_load", util.memory, "%",
                        f"GPU{i} 显存控制器使用率", self._category
                    ))
                except Exception:
                    pass

                # 显存信息
                try:
                    mem_info = _pynvml.nvmlDeviceGetMemoryInfo(handle)
                    total_mb = mem_info.total / (1024 * 1024)
                    used_mb = mem_info.used / (1024 * 1024)
                    free_mb = mem_info.free / (1024 * 1024)
                    pct = (mem_info.used / mem_info.total) * 100
                    sev = Severity.CRITICAL if pct > 90 else (
                        Severity.WARNING if pct > 70 else Severity.NORMAL
                    )
                    readings.append(SensorReading(
                        f"{prefix}_mem_usage", round(pct, 1), "%",
                        f"GPU{i} 显存使用率 ({name})", self._category, sev,
                        {"total_mb": round(total_mb, 1), "used_mb": round(used_mb, 1), "free_mb": round(free_mb, 1)}
                    ))
                    readings.append(normal(
                        f"{prefix}_mem_used", round(used_mb, 1), "MB",
                        f"GPU{i} 显存已用 ({name})", self._category
                    ))
                    readings.append(normal(
                        f"{prefix}_mem_total", round(total_mb, 1), "MB",
                        f"GPU{i} 显存总量", self._category
                    ))
                except Exception:
                    pass

                # GPU 温度
                try:
                    temp = _pynvml.nvmlDeviceGetTemperature(handle, _pynvml.NVML_TEMPERATURE_GPU)
                    sev = Severity.CRITICAL if temp > 85 else (
                        Severity.WARNING if temp > 75 else Severity.NORMAL
                    )
                    readings.append(SensorReading(
                        f"{prefix}_temp", temp, "℃",
                        f"GPU{i} 核心温度 ({name})", self._category, sev
                    ))
                except Exception:
                    pass

                # GPU 功耗
                try:
                    power_mw = _pynvml.nvmlDeviceGetPowerUsage(handle)
                    readings.append(normal(
                        f"{prefix}_power", power_mw / 1000.0, "W",
                        f"GPU{i} 功耗", self._category
                    ))
                except Exception:
                    pass

                # GPU 风扇转速
                try:
                    fan_speed = _pynvml.nvmlDeviceGetFanSpeed(handle)
                    readings.append(normal(
                        f"{prefix}_fan_speed", fan_speed, "%",
                        f"GPU{i} 风扇转速百分比", self._category
                    ))
                except Exception:
                    pass

                # GPU 时钟频率
                try:
                    graphics_clock = _pynvml.nvmlDeviceGetClockInfo(handle, _pynvml.NVML_CLOCK_GRAPHICS)
                    mem_clock = _pynvml.nvmlDeviceGetClockInfo(handle, _pynvml.NVML_CLOCK_MEM)
                    readings.append(normal(
                        f"{prefix}_clock_graphics", graphics_clock, "MHz",
                        f"GPU{i} 图形时钟频率", self._category
                    ))
                    readings.append(normal(
                        f"{prefix}_clock_mem", mem_clock, "MHz",
                        f"GPU{i} 显存时钟频率", self._category
                    ))
                except Exception:
                    pass

                # 驱动版本（仅第一个 GPU 记录）
                if i == 0:
                    try:
                        driver_ver = _pynvml.nvmlSystemGetDriverVersion()
                        if isinstance(driver_ver, bytes):
                            driver_ver = driver_ver.decode("utf-8")
                        readings.append(normal(
                            "gpu_driver_version", driver_ver, "",
                            "NVIDIA 驱动版本", self._category
                        ))
                    except Exception:
                        pass

            except Exception as e:
                logging.warning(f"采集 GPU{i} 信息失败: {e}")
        return readings

    def _collect_gputil(self):
        """通过 GPUtil 采集 GPU 信息（补充数据源）"""
        readings = []
        try:
            gpus = _GPUtil.getGPUs()
            for gpu in gpus:
                prefix = f"gpu_{gpu.id}"
                # GPUtil 给出了总显存
                if gpu.memoryTotal:
                    readings.append(normal(
                        f"{prefix}_mem_total_gputil", gpu.memoryTotal, "MB",
                        f"GPU{gpu.id} 显存总量(GPUtil)", self._category
                    ))
        except Exception as e:
            logging.debug(f"GPUtil 补充采集失败: {e}")
        # 去重：如果已有 pynvml 的 mem_total，跳过 GPUtil 的
        return [r for r in readings if not any(
            e.sensor_name == r.sensor_name.replace("_gputil", "") for e in readings
        )]

    def _collect_nvidia_smi(self):
        """
        通过 nvidia-smi 全面采集 GPU 深度信息。

        补充 pynvml 无法直接获取的信息：
        - PCIe 链路代际与宽度（当前/最大）
        - 最大加速时钟频率（Graphics/Memory/SM）
        - VBIOS 版本
        - 编码/解码器利用率
        - P-State 电源状态
        - 计算能力版本
        来源: nvidia-smi --query-gpu=...
        """
        readings = []
        try:
            from .counter_reader import get_nvidia_smi
            gpus = get_nvidia_smi()
            for gpu in gpus:
                idx = gpu.get("index", 0)
                prefix = f"gpu_{idx}"

                source_meta = {"source": "nvidia-smi"}

                # PCIe 链路代际
                pcie_gen_cur = gpu.get("pcie_gen_current")
                pcie_gen_max = gpu.get("pcie_gen_max")
                if pcie_gen_cur is not None:
                    readings.append(normal(
                        f"{prefix}_pcie_gen_current", pcie_gen_cur, "",
                        f"GPU{idx} PCIe 当前代际", self._category, source_meta
                    ))
                if pcie_gen_max is not None:
                    readings.append(normal(
                        f"{prefix}_pcie_gen_max", pcie_gen_max, "",
                        f"GPU{idx} PCIe 最大代际", self._category, source_meta
                    ))

                # PCIe 链路宽度
                pcie_width_cur = gpu.get("pcie_width_current")
                pcie_width_max = gpu.get("pcie_width_max")
                if pcie_width_cur is not None:
                    readings.append(normal(
                        f"{prefix}_pcie_width_current", pcie_width_cur, "lane",
                        f"GPU{idx} PCIe 当前宽度", self._category, source_meta
                    ))
                if pcie_width_max is not None:
                    readings.append(normal(
                        f"{prefix}_pcie_width_max", pcie_width_max, "lane",
                        f"GPU{idx} PCIe 最大宽度", self._category, source_meta
                    ))

                # GPU 温度（交叉验证）
                temp = gpu.get("temp_gpu")
                if temp is not None:
                    readings.append(normal(
                        f"{prefix}_temp_smi", temp, "℃",
                        f"GPU{idx} 温度 (nvidia-smi)", self._category, source_meta
                    ))

                # 当前时钟频率（交叉验证）
                clock_gfx = gpu.get("clock_graphics")
                if clock_gfx is not None:
                    readings.append(normal(
                        f"{prefix}_clock_graphics_smi", clock_gfx, "MHz",
                        f"GPU{idx} 图形时钟 (nvidia-smi)", self._category, source_meta
                    ))
                clock_mem = gpu.get("clock_memory")
                if clock_mem is not None:
                    readings.append(normal(
                        f"{prefix}_clock_mem_smi", clock_mem, "MHz",
                        f"GPU{idx} 显存时钟 (nvidia-smi)", self._category, source_meta
                    ))
                clock_sm = gpu.get("clock_sm")
                if clock_sm is not None:
                    readings.append(normal(
                        f"{prefix}_clock_sm_smi", clock_sm, "MHz",
                        f"GPU{idx} SM 时钟 (nvidia-smi)", self._category, source_meta
                    ))

                # 最大加速时钟（Boost 频率）
                clock_gfx_max = gpu.get("clock_graphics_max")
                if clock_gfx_max is not None:
                    readings.append(normal(
                        f"{prefix}_clock_graphics_max", clock_gfx_max, "MHz",
                        f"GPU{idx} 图形最大加速时钟", self._category, source_meta
                    ))
                clock_mem_max = gpu.get("clock_memory_max")
                if clock_mem_max is not None:
                    readings.append(normal(
                        f"{prefix}_clock_mem_max", clock_mem_max, "MHz",
                        f"GPU{idx} 显存最大加速时钟", self._category, source_meta
                    ))
                clock_sm_max = gpu.get("clock_sm_max")
                if clock_sm_max is not None:
                    readings.append(normal(
                        f"{prefix}_clock_sm_max", clock_sm_max, "MHz",
                        f"GPU{idx} SM 最大加速时钟", self._category, source_meta
                    ))

                # 功耗（交叉验证）
                power = gpu.get("power_draw_w")
                if power is not None and power > 0:
                    readings.append(normal(
                        f"{prefix}_power_smi", power, "W",
                        f"GPU{idx} 功耗 (nvidia-smi)", self._category, source_meta
                    ))
                power_limit = gpu.get("power_limit_w")
                if power_limit is not None:
                    readings.append(normal(
                        f"{prefix}_power_limit", power_limit, "W",
                        f"GPU{idx} 功耗上限", self._category, source_meta
                    ))

                # 风扇转速（交叉验证）
                fan = gpu.get("fan_speed_pct")
                if fan is not None:
                    readings.append(normal(
                        f"{prefix}_fan_speed_smi", fan, "%",
                        f"GPU{idx} 风扇转速 (nvidia-smi)", self._category, source_meta
                    ))

                # VBIOS 版本
                vbios = gpu.get("vbios_version")
                if vbios:
                    readings.append(normal(
                        f"{prefix}_vbios", vbios, "",
                        f"GPU{idx} VBIOS 版本", self._category, source_meta
                    ))

                # GPU 利用率（交叉验证）
                util = gpu.get("util_gpu")
                if util is not None:
                    readings.append(normal(
                        f"{prefix}_load_smi", util, "%",
                        f"GPU{idx} 核心使用率 (nvidia-smi)", self._category, source_meta
                    ))
                util_mem = gpu.get("util_mem")
                if util_mem is not None:
                    readings.append(normal(
                        f"{prefix}_mem_controller_load_smi", util_mem, "%",
                        f"GPU{idx} 显存控制器使用率 (nvidia-smi)", self._category, source_meta
                    ))

                # 编码/解码器利用率
                util_enc = gpu.get("util_encoder")
                if util_enc is not None:
                    readings.append(normal(
                        f"{prefix}_encoder_load", util_enc, "%",
                        f"GPU{idx} 编码器利用率", self._category, source_meta
                    ))
                util_dec = gpu.get("util_decoder")
                if util_dec is not None:
                    readings.append(normal(
                        f"{prefix}_decoder_load", util_dec, "%",
                        f"GPU{idx} 解码器利用率", self._category, source_meta
                    ))

                # P-State 电源状态
                pstate = gpu.get("pstate")
                if pstate:
                    readings.append(normal(
                        f"{prefix}_pstate", pstate, "",
                        f"GPU{idx} 电源状态", self._category, source_meta
                    ))

                # 计算能力
                compute_cap = gpu.get("compute_cap")
                if compute_cap:
                    readings.append(normal(
                        f"{prefix}_compute_capability", compute_cap, "",
                        f"GPU{idx} 计算能力", self._category, source_meta
                    ))
        except Exception as e:
            logging.debug(f"nvidia-smi 采集异常: {e}")
        return readings

    @property
    def gpu_count(self):
        """返回 GPU 数量"""
        return self._gpu_count

    @property
    def has_gpu(self):
        """是否有 GPU"""
        return self._gpu_count > 0
