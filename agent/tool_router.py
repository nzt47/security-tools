"""
云枢 ToolRouter — 智能工具选择器 + 工具分类

功能：
1. 根据用户输入只发送相关的工具定义（节省 ~60-80% tools token）
2. 为工具集成视图提供分类信息
3. 触发关键词可从文件加载，支持运行时增删改
"""

import os
import json
import re
import logging
from typing import Optional

# 安全导入 ToolTraceRecorder（不可用时降级，不影响工具路由）
try:
    from agent.observability.tool_trace import ToolTraceRecorder
except ImportError:
    ToolTraceRecorder = None

# 安全导入 PyYAML（不可用时降级到代码内默认分类，保证模块可加载）
try:
    import yaml as _yaml
except ImportError:  # pragma: no cover - PyYAML 为项目依赖，缺失时降级
    _yaml = None

logger = logging.getLogger(__name__)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEYWORDS_FILE = os.path.join(_PROJECT_ROOT, "data", "tool_router_keywords.json")
# 工具定义 YAML 目录（source of truth）；缺失时回退到下方代码内默认值
TOOL_DEFINITIONS_DIR = os.path.join(_PROJECT_ROOT, "data", "tool_definitions")

# ════════════════════════════════════════════════════════════
#  工具分类表（兜底默认值；YAML 存在时由 YAML 派生覆盖）
# ════════════════════════════════════════════════════════════

_DEFAULT_TOOL_CATEGORIES = {
    "core": {
        "label": "核心工具",
        "icon": "⚙",
        "description": "始终发送的高频基础工具",
        "always": True,
        "priority": 0,
        "tools": [
            "get_status", "search_memory", "remember", "expand_context",
            "get_sensor_summary",
        ],
    },
    "web": {
        "label": "网络与搜索",
        "icon": "🌐",
        "description": "网页抓取、搜索引擎、新闻获取",
        "always": False,
        "priority": 1,
        "tools": [
            "web_search", "web_get", "web_post", "web_xpath", "web_css",
            "web_clean_data", "web_download", "web_batch", "fetch_news",
        ],
    },
    "file": {
        "label": "文件系统",
        "icon": "📁",
        "description": "文件读写、目录操作、搜索、压缩解压",
        "always": False,
        "priority": 2,
        "tools": [
            "read_file", "write_file", "list_directory", "get_file_info",
            "search_files", "compress", "decompress", "diff_files",
        ],
    },
    "code": {
        "label": "代码与Shell",
        "icon": "💻",
        "description": "Shell 执行、代码审查、JSON/YAML 处理、格式检测",
        "always": False,
        "priority": 3,
        "tools": [
            "shell_execute", "code_review", "arch_diagram", "humanize_zh",
            "json_query", "json_to_yaml", "yaml_to_json", "json_validate",
            "data_format_detect",
        ],
    },
    "system": {
        "label": "系统与进程",
        "icon": "🖥",
        "description": "进程管理、天气查询、程序启动",
        "always": False,
        "priority": 4,
        "tools": [
            "run_program", "list_processes", "stop_process", "get_weather",
        ],
    },
    "extension": {
        "label": "扩展插件",
        "icon": "🧩",
        "description": "技能/MCP/通道/插件的安装卸载管理",
        "always": False,
        "priority": 5,
        "tools": [
            "ext_install", "ext_uninstall", "ext_list", "ext_toggle",
            "ext_discover", "ext_configure", "ext_send_channel",
        ],
    },
    "pdf": {
        "label": "PDF 处理",
        "icon": "📄",
        "description": "PDF 读取、合并、拆分、信息提取",
        "always": False,
        "priority": 6,
        "tools": [
            "read_pdf", "merge_pdf", "split_pdf", "get_pdf_info",
        ],
    },
    "software": {
        "label": "软件管理",
        "icon": "📦",
        "description": "软件搜索、安装、卸载、列表",
        "always": False,
        "priority": 7,
        "tools": [
            "software_search", "software_install", "software_list", "software_uninstall",
        ],
    },
    "async": {
        "label": "异步任务",
        "icon": "⏳",
        "description": "后台任务提交、状态查询、结果获取",
        "always": False,
        "priority": 8,
        "tools": [
            "submit_task", "get_task_status", "get_task_result", "cancel_task",
            "list_async_tasks",
        ],
    },
    "schedule": {
        "label": "定时任务",
        "icon": "⏰",
        "description": "定时任务创建、暂停、恢复、取消",
        "always": False,
        "priority": 9,
        "tools": [
            "schedule_task", "list_scheduled_tasks", "cancel_scheduled_task",
            "pause_scheduled_task", "resume_scheduled_task",
        ],
    },
    "v2": {
        "label": "V2 特性",
        "icon": "⚡",
        "description": "LifeTrace 记忆检索、人格查询与蒸馏（需安装对应模块）",
        "always": False,
        "priority": 99,
        "tools": [
            "search_lifetrace", "get_persona_info", "get_preferences",
            "trigger_distillation",
        ],
    },
}


def _load_tool_categories_from_yaml() -> Optional[dict]:
    """从 data/tool_definitions/*.yaml 派生 TOOL_CATEGORIES。

    【不易】
      - 仅 11 个已知分类键进入 TOOL_CATEGORIES；category=uncategorized 的工具
        仅纳入检索索引，不进入路由分类（保持分类表与原代码一致）。
      - 分类元数据(label/icon/description/always)取自 _DEFAULT_TOOL_CATEGORIES，
        YAML 仅承载工具列表与 schema —— 元数据契约不变。
      - 保留默认工具顺序：默认列表中的工具按默认顺序排列，YAML 新增工具字母序追加。
    【变易】YAML 为 source of truth：可增删工具，CI 由 sync_tool_index.py 守门。
    【简易】任何加载异常或目录缺失 → 返回 None，由调用方回退到默认值。

    Returns:
        派生后的分类表 dict；YAML 不可用或加载失败时返回 None（触发兜底）。
    """
    if _yaml is None or not os.path.isdir(TOOL_DEFINITIONS_DIR):
        return None

    # tool_name -> category（仅取已知分类，uncategorized 不进入 TOOL_CATEGORIES）
    yaml_tools: dict[str, str] = {}
    default_cat_keys = set(_DEFAULT_TOOL_CATEGORIES.keys())
    try:
        for fname in sorted(os.listdir(TOOL_DEFINITIONS_DIR)):
            if not fname.endswith(".yaml"):
                continue
            path = os.path.join(TOOL_DEFINITIONS_DIR, fname)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    doc = _yaml.safe_load(f)
            except (OSError, _yaml.YAMLError) as e:
                logger.warning("工具定义 YAML 读取失败 %s: %s（跳过）", fname, e)
                continue
            if not isinstance(doc, dict):
                continue
            name = doc.get("name")
            category = doc.get("category")
            if isinstance(name, str) and isinstance(category, str):
                # 仅记录已知分类的工具；uncategorized 不进入路由分类表
                if category in default_cat_keys:
                    yaml_tools[name] = category
    except OSError as e:
        logger.warning("扫描工具定义目录失败: %s（回退到默认分类）", e)
        return None

    if not yaml_tools:
        # 目录存在但无有效 YAML —— 视为缺失，回退默认
        return None

    # 派生分类表：元数据来自默认，工具列表来自 YAML（保留默认顺序 + 新增工具字母序）
    result: dict[str, dict] = {}
    for cat_key, meta in _DEFAULT_TOOL_CATEGORIES.items():
        default_tools = meta.get("tools", [])
        # 默认列表中的工具，若 YAML 仍归此分类，则按默认顺序保留
        kept = [t for t in default_tools if yaml_tools.get(t) == cat_key]
        # YAML 中归此分类但不在默认列表中的工具（新增），字母序追加
        new_tools = sorted(
            n for n, c in yaml_tools.items()
            if c == cat_key and n not in default_tools
        )
        entry = dict(meta)  # 浅拷贝元数据
        entry["tools"] = kept + new_tools
        result[cat_key] = entry
    return result


# 工具分类表：YAML 存在时由 YAML 派生，否则回退到代码内默认值
TOOL_CATEGORIES = _load_tool_categories_from_yaml() or _DEFAULT_TOOL_CATEGORIES

# 平铺所有工具（用于校验完整性）
ALL_TOOLS_SET = {tool for cat in TOOL_CATEGORIES.values() for tool in cat["tools"]}

# 工具别名映射(main_name → [alias_names])
# 【不易】别名是工具名的等价替代,解析后必须映射到已注册工具;
#        主工具被选中时,其别名工具必须从结果中移除(避免重复语义工具)
# 【变易】运行时可扩展,按需追加新别名对
# 【简易】主工具/别名都必须存在于 ALL_TOOLS_SET(test_tool_count_consistency 守门)
TOOL_ALIASES: dict[str, list[str]] = {
    "shell_execute": ["run_program"],      # 两者都是命令执行,保留 code 分类的高优先级工具
    "read_file": ["read_pdf"],             # 读取 PDF 时优先通用 read_file
    "list_directory": ["list_processes"],  # "列出"语义歧义,目录列出优先于进程列出
}


# ════════════════════════════════════════════════════════════
#  默认关键词（当配置文件不存在时使用）
# ════════════════════════════════════════════════════════════

DEFAULT_KEYWORDS = {
    "web": [
        "搜索", "查找", "打开网页", "网站", "url", "http", "https",
        "新闻", "网络", "查询", "联网", "上网", "百度", "谷歌",
        "信息", "资料", "文章", "页面", "链接", "抓取", "爬虫",
        "translate", "翻译", "search", "web", "internet", "fetch",
        "最新", "热点", "资讯",
    ],
    "file": [
        "文件", "读取", "写入", "目录", "文件夹", "保存", "打开文件",
        "创建文件", "删除文件", "移动文件", "复制文件", "压缩", "解压",
        "zip", "tar", "diff", "对比文件", "文件信息", "搜索文件",
        "列出", "文件大小", "修改时间", "file", "read", "write",
    ],
    "code": [
        "执行", "命令", "shell", "终端", "cmd", "powershell", "bash",
        "json", "yaml", "xml", "格式化", "校验", "转换", "检测格式",
        "代码审查", "架构图", "review", "代码", "脚本", "运行",
        "humanize", "ai写作", "代码检查",
    ],
    "system": [
        "进程", "启动程序", "运行程序", "天气", "温度", "天气预报",
        "程序", "process", "weather", "停止", "打开", "notepad",
        "calc", "白名单",
    ],
    "extension": [
        "安装扩展", "卸载扩展", "技能", "插件", "mcp", "通道",
        "扩展市场", "扩展列表", "扩展管理", "安装技能",
        "安装插件", "拓展", "channel", "webhook", "邮件",
        "ext_", "扩展",
    ],
    "pdf": [
        "pdf", "合并pdf", "拆分pdf", "读取pdf", "pdf信息",
        "pdf文件", "pdf处理", "pdf合并",
    ],
    "software": [
        "安装软件", "卸载软件", "搜索软件", "软件包", "软件列表",
        "chocolatey", "pip install", "npm install", "安装包",
        "软件管理",
    ],
    "async": [
        "异步", "后台", "提交任务", "任务状态", "任务结果",
        "取消任务", "长时间", "耗时", "background", "async",
        "submit", "task",
    ],
    "schedule": [
        "定时", "计划", "调度", "cron", "周期", "每天", "每小时",
        "定时任务", "计划任务", "schedule", "定时执行",
        "重复", "每隔",
    ],
    "v2": [
        "lifetrace", "人格", "persona", "蒸馏", "distillation",
        "偏好", "preference", "记忆检索",
    ],
}


# ════════════════════════════════════════════════════════════
#  关键词加载/保存（可配置）
# ════════════════════════════════════════════════════════════

def _load_keywords() -> dict:
    """从文件加载关键词，不存在则返回默认"""
    if os.path.exists(KEYWORDS_FILE):
        try:
            with open(KEYWORDS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and "keywords" in data:
                return data["keywords"]
        except Exception as e:
            logger.warning("读取关键词文件失败: %s，使用默认", e)
    return DEFAULT_KEYWORDS


def _save_keywords(keywords: dict) -> bool:
    """保存关键词到文件"""
    try:
        os.makedirs(os.path.dirname(KEYWORDS_FILE), exist_ok=True)
        with open(KEYWORDS_FILE, "w", encoding="utf-8") as f:
            json.dump({"keywords": keywords}, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.error("保存关键词失败: %s", e)
        return False


def get_keywords() -> dict:
    """获取当前关键词配置"""
    return _load_keywords()


def add_keyword(category: str, keyword: str) -> bool:
    """为指定类别添加触发关键词"""
    keywords = _load_keywords()
    if category not in keywords:
        keywords[category] = []
    if keyword not in keywords[category]:
        keywords[category].append(keyword)
        return _save_keywords(keywords)
    return True  # 已存在，视为成功


def remove_keyword(category: str, keyword: str) -> bool:
    """删除指定类别的触发关键词"""
    keywords = _load_keywords()
    if category in keywords and keyword in keywords[category]:
        keywords[category].remove(keyword)
        return _save_keywords(keywords)
    return False


def update_keyword(category: str, old_keyword: str, new_keyword: str) -> bool:
    """修改触发关键词"""
    keywords = _load_keywords()
    if category in keywords and old_keyword in keywords[category]:
        idx = keywords[category].index(old_keyword)
        keywords[category][idx] = new_keyword
        return _save_keywords(keywords)
    return False


def reset_keywords() -> bool:
    """恢复默认关键词"""
    try:
        if os.path.exists(KEYWORDS_FILE):
            os.remove(KEYWORDS_FILE)
        return True
    except Exception:
        return False


# ════════════════════════════════════════════════════════════
#  分类与路由逻辑
# ════════════════════════════════════════════════════════════

def classify_user_input(user_input: str) -> set[str]:
    """分析用户输入，返回相关的工具类别集合"""
    if not user_input:
        return {"core"}

    text = user_input.lower()
    keywords = _load_keywords()
    matched = {"core"}  # core 始终包含

    for category, kw_list in keywords.items():
        if category not in TOOL_CATEGORIES:
            continue
        for kw in kw_list:
            if kw.lower() in text:
                matched.add(category)
                break

    logger.debug("工具路由: 输入='%s' → 匹配类别=%s", user_input[:30], matched)
    return matched


def get_tools_for_input(
    user_input: str,
    enabled_whitelist: list[str] | None = None,
    max_tools: int = 25,
) -> list[str]:
    """根据用户输入，返回应发送的工具名称列表。

    处理流程（三义约束）:
      1. 分类匹配（不易）— 关键词命中类别 → 收集该类别全部工具
      2. 白名单交集（不易）— 仅保留启用工具
      3. 别名合并（功能2）— 主工具在结果中时,移除其别名工具(避免语义重复)
      4. 优先级排序（功能1）— 按 category.priority 升序;跨类别工具取最小 priority
      5. 数量截断（功能3）— 按 max_tools 截断,保留高优先级工具

    Args:
        user_input: 用户原始输入文本
        enabled_whitelist: 启用工具白名单,None 表示不限制
        max_tools: 返回工具数上限,默认 25;None 或 <=0 表示不限制

    Returns:
        排序+截断后的工具名列表
    """
    categories = classify_user_input(user_input)
    selected = set()

    for cat in categories:
        cat_info = TOOL_CATEGORIES.get(cat)
        if cat_info:
            selected.update(cat_info["tools"])

    # 与白名单取交集
    if enabled_whitelist is not None:
        whitelist_set = set(enabled_whitelist)
        selected &= whitelist_set

    # 【功能 2/1/3】别名合并 + 优先级排序 + 数量截断(抽为 helper,供 hybrid 复用)
    result = _apply_alias_merge_and_priority_sort(selected, categories, max_tools)

    # 记录工具选择决策（安全降级：recorder 不可用或异常不影响路由）
    if ToolTraceRecorder is not None:
        try:
            ToolTraceRecorder.instance().record_tool_selection(user_input, categories, result)
        except Exception:
            pass

    return result


def _apply_alias_merge_and_priority_sort(
    selected: set,
    categories: set,
    max_tools: int,
) -> list[str]:
    """别名合并 + 优先级排序 + 数量截断(从 get_tools_for_input 抽取,行为不变)

    【不易】TOOL_ALIASES 合并 + 优先级去重 + 25 上限逻辑保留
           — 主工具存在 → 别名移除;跨类别工具取最小 priority;max_tools None/<=0 不限制
    【变易】抽为独立函数,供 tool_router_hybrid.HybridRetriever 复用,确保单一来源
    【简易】纯函数无副作用,输入 selected 集合 + categories 集合,返回排序+截断后的列表

    Args:
        selected: 已经过白名单交集的工具集合(会被原地修改:移除别名)
        categories: 命中的工具类别集合(用于查询 priority)
        max_tools: 返回工具数上限;None 或 <=0 表示不限制

    Returns:
        排序+截断后的工具名列表
    """
    # 【功能 2】别名合并:主工具被选中时,移除其别名工具
    # 【不易】别名规则不变 — 主工具存在 → 别名移除
    if TOOL_ALIASES:
        aliases_to_remove: set[str] = set()
        for main_tool, alias_list in TOOL_ALIASES.items():
            if main_tool in selected:
                aliases_to_remove.update(alias_list)
        selected -= aliases_to_remove

    # 【功能 1】优先级排序:工具 → 其所属类别中最小的 priority
    # 【简易】跨类别工具取最小 priority,确保高优先级类别工具排前
    tool_to_priority: dict[str, int] = {}
    for cat in categories:
        cat_info = TOOL_CATEGORIES.get(cat)
        if not cat_info:
            continue
        pri = cat_info.get("priority", 99)
        for tool in cat_info["tools"]:
            if tool in selected:
                if tool not in tool_to_priority or pri < tool_to_priority[tool]:
                    tool_to_priority[tool] = pri
    result = sorted(selected, key=lambda t: tool_to_priority.get(t, 99))

    # 【功能 3】数量限制:按 priority 排序后截断,保留高优先级工具
    # 【变易】max_tools 可配置;None 或 <=0 表示不限制(向后兼容)
    if max_tools is not None and max_tools > 0 and len(result) > max_tools:
        result = result[:max_tools]

    return result


def get_categorized_tools() -> list[dict]:
    """获取按类别分组的工具列表（供前端渲染）"""
    result = []
    for cat_key, cat_info in TOOL_CATEGORIES.items():
        result.append({
            "key": cat_key,
            "label": cat_info["label"],
            "icon": cat_info["icon"],
            "description": cat_info["description"],
            "always": cat_info.get("always", False),
            "tools": list(cat_info["tools"]),
        })
    return result


def estimate_tool_tokens(tool_names: list[str], total_tokens_all: int = 10000) -> int:
    """估算选定工具的 token 数（比例法）"""
    all_count = len(ALL_TOOLS_SET)
    if all_count == 0:
        return 0
    selected_count = len(tool_names)
    return int(total_tokens_all * selected_count / all_count)
