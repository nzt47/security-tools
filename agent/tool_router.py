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

logger = logging.getLogger(__name__)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEYWORDS_FILE = os.path.join(_PROJECT_ROOT, "data", "tool_router_keywords.json")

# ════════════════════════════════════════════════════════════
#  工具分类表
# ════════════════════════════════════════════════════════════

TOOL_CATEGORIES = {
    "core": {
        "label": "核心工具",
        "icon": "⚙",
        "description": "始终发送的高频基础工具",
        "always": True,
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
        "tools": [
            "run_program", "list_processes", "stop_process", "get_weather",
        ],
    },
    "extension": {
        "label": "扩展插件",
        "icon": "🧩",
        "description": "技能/MCP/通道/插件的安装卸载管理",
        "always": False,
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
        "tools": [
            "read_pdf", "merge_pdf", "split_pdf", "get_pdf_info",
        ],
    },
    "software": {
        "label": "软件管理",
        "icon": "📦",
        "description": "软件搜索、安装、卸载、列表",
        "always": False,
        "tools": [
            "software_search", "software_install", "software_list", "software_uninstall",
        ],
    },
    "async": {
        "label": "异步任务",
        "icon": "⏳",
        "description": "后台任务提交、状态查询、结果获取",
        "always": False,
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
        "tools": [
            "search_lifetrace", "get_persona_info", "get_preferences",
            "trigger_distillation",
        ],
    },
}

# 平铺所有工具（用于校验完整性）
ALL_TOOLS_SET = {tool for cat in TOOL_CATEGORIES.values() for tool in cat["tools"]}


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


def get_tools_for_input(user_input: str, enabled_whitelist: list[str] | None = None) -> list[str]:
    """根据用户输入，返回应发送的工具名称列表"""
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

    result = list(selected)

    # 记录工具选择决策（安全降级：recorder 不可用或异常不影响路由）
    if ToolTraceRecorder is not None:
        try:
            ToolTraceRecorder.instance().record_tool_selection(user_input, categories, result)
        except Exception:
            pass

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
