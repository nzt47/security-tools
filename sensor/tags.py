"""
多维度感知标签分类系统

为每条传感器读数标注多维度分类标签，形成统一的感知维谱。

标签维度 (8 大分类轴):
  1. domain  — 目标域: 硬件/软件/行为/环境
  2. locus   — 内外方位: 内部/外部/边界
  3. temporal — 动静属性: 静态配置/动态运行/增量变化
  4. method  — 采集方式: 主动探测/被动监听/系统查询/对比检测
  5. layer   — 感知层次: 物理层/系统层/应用层
  6. role    — 功能角色: 基础生存/性能监控/安全防护/社交通信/环境适应
  7. datatype — 数据特征: 数值量/状态量/事件量/配置量
  8. control — 可干预性: 仅可观测/可配置

我是灵犀的"认知分类学"——每条神经信号都被我标注了多个维度的属性。
"""
import re
from .sensor_reading import Category


# ═══════════════════════════════════════════════════════════════
#  标签维度定义（每个维度是一个元组列表）
# ═══════════════════════════════════════════════════════════════

# 维度 1: 目标域 (Domain)
DOMAIN_HARDWARE = "硬件感知"
DOMAIN_SOFTWARE = "软件感知"
DOMAIN_BEHAVIOR = "行为感知"
DOMAIN_ENVIRONMENT = "环境感知"

# 维度 2: 内外方位 (Locus)
LOCUS_INTERNAL = "内部感知"
LOCUS_EXTERNAL = "外部感知"
LOCUS_BOUNDARY = "边界感知"

# 维度 3: 动静属性 (Temporal)
TEMP_STATIC = "静态配置"
TEMP_DYNAMIC = "动态运行"
TEMP_DELTA = "增量变化"

# 维度 4: 采集方式 (Method)
METHOD_PROBE = "主动探测"
METHOD_MONITOR = "被动监听"
METHOD_QUERY = "系统查询"
METHOD_DELTA = "对比检测"

# 维度 5: 感知层次 (Layer)
LAYER_PHYSICAL = "物理层"
LAYER_SYSTEM = "系统层"
LAYER_APPLICATION = "应用层"

# 维度 6: 功能角色 (Role)
ROLE_VITAL = "基础生存"
ROLE_PERFORMANCE = "性能监控"
ROLE_SECURITY = "安全防护"
ROLE_SOCIAL = "社交通信"
ROLE_ENVIRONMENT = "环境适应"

# 维度 7: 数据特征 (Datatype)
DTYPE_NUMERIC = "数值量"
DTYPE_STATE = "状态量"
DTYPE_EVENT = "事件量"
DTYPE_CONFIG = "配置量"

# 维度 8: 可干预性 (Control)
CTRL_OBSERVE = "仅可观测"
CTRL_CONFIG = "可配置"


# ═══════════════════════════════════════════════════════════════
#  类别 → 默认标签映射
# ═══════════════════════════════════════════════════════════════

_CATEGORY_TAGS = {
    Category.CPU: [
        DOMAIN_HARDWARE, LOCUS_INTERNAL, TEMP_DYNAMIC,
        METHOD_PROBE, LAYER_PHYSICAL, ROLE_PERFORMANCE,
        DTYPE_NUMERIC, CTRL_OBSERVE,
    ],
    Category.GPU: [
        DOMAIN_HARDWARE, LOCUS_INTERNAL, TEMP_DYNAMIC,
        METHOD_PROBE, LAYER_PHYSICAL, ROLE_PERFORMANCE,
        DTYPE_NUMERIC, CTRL_OBSERVE,
    ],
    Category.MEMORY: [
        DOMAIN_HARDWARE, LOCUS_INTERNAL, TEMP_DYNAMIC,
        METHOD_PROBE, LAYER_SYSTEM, ROLE_PERFORMANCE,
        DTYPE_NUMERIC, CTRL_OBSERVE,
    ],
    Category.BATTERY: [
        DOMAIN_HARDWARE, LOCUS_INTERNAL, TEMP_DYNAMIC,
        METHOD_PROBE, LAYER_PHYSICAL, ROLE_VITAL,
        DTYPE_NUMERIC, CTRL_OBSERVE,
    ],
    Category.DISK: [
        DOMAIN_HARDWARE, LOCUS_INTERNAL, TEMP_DYNAMIC,
        METHOD_PROBE, LAYER_PHYSICAL, ROLE_VITAL,
        DTYPE_NUMERIC, CTRL_OBSERVE,
    ],
    Category.NETWORK: [
        DOMAIN_HARDWARE, LOCUS_EXTERNAL, TEMP_DYNAMIC,
        METHOD_PROBE, LAYER_SYSTEM, ROLE_SOCIAL,
        DTYPE_NUMERIC, CTRL_CONFIG,
    ],
    Category.BOARD: [
        DOMAIN_HARDWARE, LOCUS_INTERNAL, TEMP_STATIC,
        METHOD_PROBE, LAYER_PHYSICAL, ROLE_VITAL,
        DTYPE_CONFIG, CTRL_OBSERVE,
    ],
    Category.CHASSIS: [
        DOMAIN_HARDWARE, LOCUS_INTERNAL, TEMP_STATIC,
        METHOD_PROBE, LAYER_PHYSICAL, ROLE_VITAL,
        DTYPE_STATE, CTRL_OBSERVE,
    ],
    Category.CHANGE: [
        DOMAIN_HARDWARE, LOCUS_INTERNAL, TEMP_DELTA,
        METHOD_DELTA, LAYER_PHYSICAL, ROLE_VITAL,
        DTYPE_EVENT, CTRL_OBSERVE,
    ],
    Category.FILE: [
        DOMAIN_SOFTWARE, LOCUS_INTERNAL, TEMP_DELTA,
        METHOD_MONITOR, LAYER_APPLICATION, ROLE_ENVIRONMENT,
        DTYPE_EVENT, CTRL_OBSERVE,
    ],
    Category.ENVIRONMENT: [
        DOMAIN_ENVIRONMENT, LOCUS_INTERNAL, TEMP_STATIC,
        METHOD_QUERY, LAYER_SYSTEM, ROLE_ENVIRONMENT,
        DTYPE_CONFIG, CTRL_OBSERVE,
    ],
    Category.ACTIVITY: [
        DOMAIN_BEHAVIOR, LOCUS_INTERNAL, TEMP_DELTA,
        METHOD_DELTA, LAYER_SYSTEM, ROLE_PERFORMANCE,
        DTYPE_NUMERIC, CTRL_OBSERVE,
    ],
    Category.DISPLAY: [
        DOMAIN_HARDWARE, LOCUS_INTERNAL, TEMP_STATIC,
        METHOD_QUERY, LAYER_PHYSICAL, ROLE_VITAL,
        DTYPE_CONFIG, CTRL_OBSERVE,
    ],
    Category.AUDIO: [
        DOMAIN_HARDWARE, LOCUS_INTERNAL, TEMP_STATIC,
        METHOD_QUERY, LAYER_PHYSICAL, ROLE_VITAL,
        DTYPE_CONFIG, CTRL_OBSERVE,
    ],
    Category.SYSTEM: [
        DOMAIN_SOFTWARE, LOCUS_INTERNAL, TEMP_DYNAMIC,
        METHOD_QUERY, LAYER_SYSTEM, ROLE_ENVIRONMENT,
        DTYPE_STATE, CTRL_CONFIG,
    ],
}


# ═══════════════════════════════════════════════════════════════
#  传感器名前缀 → 标签覆写/补充
# ═══════════════════════════════════════════════════════════════

# 规则: (前缀模式, [追加标签])
# 这些标签会合并到类别默认标签之上
_SENSOR_TAG_OVERRIDES = [
    # ── CPU ──
    (r"^cpu_temp", [TEMP_DYNAMIC, DTYPE_NUMERIC, ROLE_VITAL]),
    (r"^cpu_fan", [TEMP_DYNAMIC, DTYPE_NUMERIC, ROLE_VITAL]),
    (r"^cpu_voltage", [TEMP_DYNAMIC, DTYPE_NUMERIC, ROLE_VITAL]),

    # ── 磁盘 ──
    (r"^disk_smart", [TEMP_STATIC, DTYPE_CONFIG, ROLE_VITAL]),
    (r"^disk_temp", [TEMP_DYNAMIC, DTYPE_NUMERIC, ROLE_VITAL]),
    (r"^disk_partition", [TEMP_STATIC, DTYPE_CONFIG]),
    (r"^disk_io_", [DOMAIN_BEHAVIOR, TEMP_DELTA, METHOD_DELTA, DTYPE_NUMERIC]),

    # ── 网络 ──
    (r"^net_adapter", [TEMP_STATIC, DTYPE_CONFIG]),
    (r"^net_wifi_", [TEMP_DYNAMIC, DTYPE_STATE, ROLE_SOCIAL]),
    (r"^net_dns", [TEMP_STATIC, DTYPE_CONFIG, ROLE_ENVIRONMENT]),
    (r"^net_bandwidth", [TEMP_DYNAMIC, DTYPE_NUMERIC, ROLE_PERFORMANCE]),

    # ── 进程 ──
    (r"^proc_new", [TEMP_DELTA, DTYPE_EVENT, ROLE_PERFORMANCE]),
    (r"^proc_terminated", [TEMP_DELTA, DTYPE_EVENT, ROLE_PERFORMANCE]),
    (r"^proc_lifecycle", [TEMP_DELTA, DTYPE_EVENT]),
    (r"^proc_top_", [TEMP_DYNAMIC, DTYPE_NUMERIC, ROLE_PERFORMANCE]),
    (r"^proc_net_", [DOMAIN_BEHAVIOR, TEMP_DYNAMIC, DTYPE_NUMERIC, ROLE_SOCIAL]),
    (r"^proc_startup", [TEMP_STATIC, DTYPE_CONFIG, ROLE_ENVIRONMENT]),

    # ── 文件 ──
    (r"^file_created", [DTYPE_EVENT]),
    (r"^file_modified", [DTYPE_EVENT]),
    (r"^file_deleted", [DTYPE_EVENT]),
    (r"^file_moved", [DTYPE_EVENT]),
    (r"^dir_created", [DTYPE_EVENT]),
    (r"^dir_deleted", [DTYPE_EVENT]),
    (r"^filewatch", [DTYPE_EVENT]),

    # ── 硬件文件 ──
    (r"^hwfile_", [DOMAIN_HARDWARE, TEMP_STATIC, DTYPE_CONFIG, LAYER_SYSTEM]),

    # ── 环境 ──
    (r"^env_module", [TEMP_STATIC, DTYPE_STATE, ROLE_VITAL]),
    (r"^env_api_", [TEMP_STATIC, DTYPE_STATE, ROLE_VITAL]),
    (r"^env_service_", [TEMP_DYNAMIC, DTYPE_STATE, ROLE_VITAL]),
    (r"^env_uptime", [TEMP_DYNAMIC, DTYPE_NUMERIC]),

    # ── 行为/活动 ──
    (r"^behavior_disk_iops_", [DOMAIN_BEHAVIOR, DTYPE_NUMERIC]),
    (r"^behavior_disk_latency_", [DOMAIN_BEHAVIOR, DTYPE_NUMERIC]),
    (r"^behavior_disk_summary", [DOMAIN_BEHAVIOR, DTYPE_NUMERIC]),
    (r"^behavior_disk_proc_", [DOMAIN_BEHAVIOR, DTYPE_NUMERIC, LAYER_APPLICATION]),
    (r"^behavior_cpu_ctx_", [DOMAIN_BEHAVIOR, DTYPE_NUMERIC]),
    (r"^behavior_cpu_time_", [DOMAIN_BEHAVIOR, DTYPE_NUMERIC]),
    (r"^behavior_cpu_load", [DOMAIN_BEHAVIOR, DTYPE_NUMERIC]),
    (r"^behavior_cpu_interrupts", [DOMAIN_BEHAVIOR, DTYPE_NUMERIC]),
    (r"^behavior_cpu_soft_interrupts", [DOMAIN_BEHAVIOR, DTYPE_NUMERIC]),
    (r"^behavior_cpu_syscalls", [DOMAIN_BEHAVIOR, DTYPE_NUMERIC]),
    (r"^behavior_cpu_freq", [DOMAIN_BEHAVIOR, DTYPE_NUMERIC]),
    (r"^behavior_cpu_per_core", [DOMAIN_BEHAVIOR, DTYPE_NUMERIC]),
    (r"^behavior_mem_page_fault_", [DOMAIN_BEHAVIOR, DTYPE_NUMERIC]),
    (r"^behavior_mem_commit", [DOMAIN_BEHAVIOR, DTYPE_NUMERIC]),
    (r"^behavior_mem_page_in", [DOMAIN_BEHAVIOR, DTYPE_NUMERIC]),
    (r"^behavior_mem_page_out", [DOMAIN_BEHAVIOR, DTYPE_NUMERIC]),
    (r"^behavior_net_throughput", [DOMAIN_BEHAVIOR, DTYPE_NUMERIC, ROLE_SOCIAL]),
    (r"^behavior_net_errors", [DOMAIN_BEHAVIOR, DTYPE_NUMERIC]),
    (r"^behavior_net_proto_", [DOMAIN_BEHAVIOR, DTYPE_STATE]),
    (r"^behavior_net_tcp_", [DOMAIN_BEHAVIOR, DTYPE_STATE]),
    (r"^behavior_net_dns", [DOMAIN_BEHAVIOR, DTYPE_CONFIG]),
    (r"^behavior_user_", [DOMAIN_BEHAVIOR, LAYER_APPLICATION]),
    (r"^behavior_service_", [DOMAIN_BEHAVIOR, DTYPE_EVENT]),
    (r"^behavior_task_", [DOMAIN_BEHAVIOR, DTYPE_CONFIG]),
    (r"^behavior_mem_swap", [DOMAIN_BEHAVIOR, DTYPE_NUMERIC]),

    # ── 系统状态 ──
    (r"^system_display_", [DOMAIN_HARDWARE, TEMP_STATIC, DTYPE_CONFIG, LAYER_PHYSICAL]),
    (r"^system_audio_", [DOMAIN_HARDWARE, TEMP_STATIC, DTYPE_CONFIG, LAYER_PHYSICAL]),
    (r"^system_printer_", [DOMAIN_HARDWARE, LOCUS_BOUNDARY, DTYPE_STATE]),
    (r"^system_scanner_", [DOMAIN_HARDWARE, LOCUS_BOUNDARY, DTYPE_STATE]),
    (r"^system_security_defender_", [DOMAIN_SOFTWARE, ROLE_SECURITY, DTYPE_STATE]),
    (r"^system_security_firewall_", [DOMAIN_SOFTWARE, ROLE_SECURITY, DTYPE_STATE]),
    (r"^system_security_uac", [DOMAIN_SOFTWARE, ROLE_SECURITY, DTYPE_STATE]),
    (r"^system_security_bitlocker_", [DOMAIN_SOFTWARE, ROLE_SECURITY, DTYPE_STATE]),
    (r"^system_update_", [DOMAIN_SOFTWARE, TEMP_DYNAMIC, DTYPE_STATE, ROLE_ENVIRONMENT]),
    (r"^system_event_whea_", [DOMAIN_HARDWARE, TEMP_DELTA, DTYPE_EVENT, ROLE_VITAL]),
    (r"^system_event_crash_", [DOMAIN_SOFTWARE, TEMP_DELTA, DTYPE_EVENT, ROLE_VITAL]),
    (r"^system_event_", [DOMAIN_SOFTWARE, TEMP_DELTA, DTYPE_EVENT, ROLE_ENVIRONMENT]),
    (r"^system_power_", [DOMAIN_SOFTWARE, TEMP_STATIC, DTYPE_CONFIG, ROLE_VITAL]),
    (r"^system_time_", [DOMAIN_ENVIRONMENT, TEMP_STATIC, DTYPE_CONFIG, ROLE_ENVIRONMENT]),
    (r"^system_cert_", [DOMAIN_SOFTWARE, TEMP_STATIC, DTYPE_CONFIG, ROLE_SECURITY]),
    (r"^system_ime_", [DOMAIN_SOFTWARE, LAYER_APPLICATION, DTYPE_STATE, ROLE_ENVIRONMENT]),
    (r"^system_clipboard_", [DOMAIN_SOFTWARE, LAYER_APPLICATION, DTYPE_STATE, ROLE_ENVIRONMENT]),

    # ── 主板 ──
    (r"^board_", [DOMAIN_HARDWARE, TEMP_STATIC, DTYPE_CONFIG, LAYER_PHYSICAL, ROLE_VITAL]),

    # ── 机箱 ──
    (r"^chassis_", [DOMAIN_HARDWARE, TEMP_STATIC, DTYPE_STATE, LAYER_PHYSICAL]),

    # ── 电池 ──
    (r"^battery_", [DOMAIN_HARDWARE, TEMP_DYNAMIC, DTYPE_NUMERIC, LAYER_PHYSICAL, ROLE_VITAL]),

    # ── 外设 ──
    (r"^peripheral_", [DOMAIN_HARDWARE, LOCUS_BOUNDARY, TEMP_STATIC, DTYPE_CONFIG, LAYER_PHYSICAL]),
    (r"^port_", [DOMAIN_HARDWARE, LOCUS_BOUNDARY, TEMP_STATIC, DTYPE_CONFIG, LAYER_PHYSICAL]),

    # ── GPU ──
    (r"^gpu_temp", [TEMP_DYNAMIC, DTYPE_NUMERIC, ROLE_VITAL]),
    (r"^gpu_fan", [TEMP_DYNAMIC, DTYPE_NUMERIC, ROLE_VITAL]),
    (r"^gpu_clock", [TEMP_DYNAMIC, DTYPE_NUMERIC, ROLE_PERFORMANCE]),
    (r"^gpu_memory", [TEMP_DYNAMIC, DTYPE_NUMERIC, ROLE_PERFORMANCE]),
    (r"^gpu_load", [TEMP_DYNAMIC, DTYPE_NUMERIC, ROLE_PERFORMANCE]),
]


# ═══════════════════════════════════════════════════════════════
#  标签分配函数
# ═══════════════════════════════════════════════════════════════

def get_tags(category, sensor_name):
    """
    根据类别和传感器名计算标签集合。

    :param category: Category 枚举值或字符串
    :param sensor_name: 传感器名称字符串
    :returns: 去重后的标签列表
    """
    tags = []

    # 1. 类别默认标签
    if isinstance(category, Category):
        cat_key = category
    else:
        try:
            cat_key = Category(category)
        except (ValueError, TypeError):
            cat_key = None

    if cat_key and cat_key in _CATEGORY_TAGS:
        tags.extend(_CATEGORY_TAGS[cat_key])

    # 2. 传感器名前缀匹配覆写/补充
    for pattern, extra_tags in _SENSOR_TAG_OVERRIDES:
        if re.match(pattern, sensor_name):
            tags.extend(extra_tags)

    # 去重并保持顺序
    seen = set()
    result = []
    for t in tags:
        if t not in seen:
            seen.add(t)
            result.append(t)

    return result
