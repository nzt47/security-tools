"""
云枢 ToolRouter — 智能工具选择器 + 工具分类

功能：
1. 根据用户输入只发送相关的工具定义（节省 ~60-80% tools token）
2. 为工具集成视图提供分类信息
3. 触发关键词可从文件加载，支持运行时增删改
4. PINNED_TOOLS 关注名单：类别已命中却被 max_tools 挤掉的工具会被补回（见该常量）
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

#: 工具发现服务（由 lifecycle_manager 注入）
#: 历史问题：`lifecycle_manager.py:1026` 一直在调 `tool_router.set_discovery_service(...)`，
#: 但本模块**没有这个函数** ⇒ AttributeError 被内层 `except: pass` 静默吞掉，
#: "路由层感知不到发现服务"这件事从不报错也从不生效（评估报告 §4.4c 同源问题）。
_discovery_service = None


def set_discovery_service(service):
    """注入工具发现服务（此前缺失的符号，补上以消除静默 AttributeError）

    路由层当前**不依赖**发现服务做召回（候选来自 tool_index.json），
    保留此入口是为了：① 让既有调用点不再静默失败；② 为后续"路由到未索引工具"
    留出接线位。返回上一次的服务实例，便于调用方断言。
    """
    global _discovery_service
    prev = _discovery_service
    _discovery_service = service
    logger.info("[工具路由] 发现服务已注入: %s", type(service).__name__ if service else None)
    return prev


def get_discovery_service():
    """读取已注入的发现服务（可能为 None）"""
    return _discovery_service

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
            # expand_context 已并入 search_memory(scope="vector")（第 0 档合并）
            "get_status", "search_memory", "remember",
            # todo_write 归 core：core 恒被命中（classify_user_input 以 matched={"core"}
            # 起步）⇒ 等于"始终可见"；且 core 优先级 0，不会被 max_tools 截断。
            # 它只有一个数组参数，常驻 schema 的 token 成本很小（对比 delegate 的八要素）。
            "get_sensor_summary", "todo_write",
        ],
    },
    "web": {
        "label": "网络与搜索",
        "icon": "🌐",
        "description": "网页抓取、搜索引擎、新闻获取",
        "always": False,
        "priority": 1,
        "tools": [
            "web_search", "web_get", "web_post", "web_download", "web_batch",
            # 第 0 档合并：web_xpath + web_css + web_clean_data → web_extract；
            # fetch_news → web_search(preset="news")（见 docs/工具集评估与重分类报告.md §6.2）
            "web_extract",
            # 浏览器自动化（能力补全：实现早已存在、此前未注册为工具）
            "browser_navigate", "browser_screenshot", "browser_close",
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
            # grep / edit 已改归 code 分类（评估报告 §6.5）：
            # 它们是代码工程原语，不是文件系统原语。留在 file 类会导致
            # 「写代码并运行测试」这类输入命中 code 却拿不到 edit/grep（实测缺失）。
            # 能力补全：工作区管理与报表生成
            "workspace_init", "workspace_list", "workspace_write", "workspace_delete",
            "weekly_report",
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
            "json_query", "data_format_detect",
            # 第 0 档合并：json_to_yaml + yaml_to_json → data_convert(to=)；
            # json_validate → data_format_detect（其 JSON 分支本就是同一个 json.loads）
            "data_convert",
            # 能力补全：工程交付的三个基础原语（此前完全缺失）
            "git", "run_tests", "apply_patch", "run_sandbox",
            # 由 file 类迁入：代码工程原语（报告 §6.5）
            "grep", "edit",
            # 能力补全（B 档）：只读 sqlite 查询 + 代码检查
            "sqlite_query", "run_lint",
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
            # 能力补全：剪贴板与屏幕视觉
            "get_clipboard", "set_clipboard", "look_at_screen",
            # 能力补全（B 档）：主动通知/提醒（schedule_task 是周期执行，不是到点提醒）
            "notify",
        ],
    },
    "extension": {
        "label": "扩展插件",
        "icon": "🧩",
        "description": "技能/MCP/通道/插件的安装卸载管理，以及工具自生成",
        "always": False,
        "priority": 5,
        "tools": [
            "ext_install", "ext_uninstall", "ext_list", "ext_toggle",
            "ext_discover", "ext_configure", "ext_send_channel",
            # 评估报告 §1.5-A：这 6 个此前是 uncategorized ⇒ 被分类表静默丢弃、
            # 关键词路由永不返回（含"自我扩展"入口 generate_tool）。已归档到本类。
            # market_search → ext_discover、install_tool → ext_install(type="auto")
            # （第 0 档合并，2026-09-17；两处 enum 缺口同时修复）
            "generate_tool", "scan_mcp", "connect_mcp", "disconnect_mcp",
            # 能力补全：查看活跃 MCP 连接（实现早已存在、此前无工具暴露）
            "list_mcp_connections",
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
            # 能力补全：PDF 表格抽取（实现早已存在、此前未注册）
            "read_pdf_tables",
        ],
    },
    # ── software 分类已退役（2026-09-17）──
    # 原 "software" 类的 4 个工具（software_search/install/list/uninstall）是空壳：
    # software_install 会返回成功却什么都没装（见 docs/工具集评估与重分类报告.md §4.4a）。
    # 实现模块 agent/tools/software_tools.py 与 4 个 YAML 均已删除，此处整个分类一并移除
    # ——留一个永远为空的分类会在前端渲染成空分组，也会污染路由推导。
    # 【2026-09-18 收口】空壳本体（agent/software_manager.py、agent/software_backends.py）
    # 与生成脚本（scripts/create_software_manager.py、scripts/create_module.py）已删除，
    # 避免"无人引用却随时可被再次接线"的死灰复燃。
    # 恢复时需同时恢复：本分类、data/tool_definitions/software_*.yaml、
    # agent/tools/software_tools.py，并先补齐 software_manager 的真实实现。
    "async": {
        "label": "异步任务",
        "icon": "⏳",
        "description": "后台任务提交、状态查询、结果获取",
        "always": False,
        "priority": 8,
        "tools": [
            "submit_task", "get_task_status", "get_task_result", "cancel_task",
            "list_async_tasks", "delegate", "fan_out",
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
    # ── 知识库与过程蒸馏 ──
    # 历史问题：kb_* 6 个工具从未在生产注册、也无 YAML（故无 category），
    # distill_* 3 个无 YAML ⇒ 两者都进不了本表（见评估报告 §1.5-C/D）。
    # 现已补 YAML 定义并接线注册，在此归类使其在关键词路由下同样可达。
    "knowledge": {
        "label": "知识与蒸馏",
        "icon": "📚",
        "description": "素材入库、提炼、产卡、巡检、语义检索与过程蒸馏固化",
        "always": False,
        "priority": 5,
        "tools": [
            "kb_capture", "kb_distill", "kb_discuss", "kb_card", "kb_lint", "kb_search",
            # distill_process_async → distill_process_from_knowledge(async_=true)
            "distill_process_from_knowledge",
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
            # internal: true 的工具（如 process_distill_run）必须留在注册表里供内部按名调用，
            # 但**不得进入路由分类表** —— 它是内部执行体，不该被模型选中。
            if doc.get("internal") is True:
                continue
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

# ── 依赖倒置：把「类别 → priority/tools」表**注入**可观测层（不做反向 import）──
# Why：`agent.observability.tool_trace` 需要按类别优先级派生高频工具采样集，
#   但它**不得** import 本模块 —— 那会构成 `tool_router ↔ tool_trace` 环，
#   被架构门禁 `no_circular_dependency` 判违规（2026-09-17 实测：1 条未豁免违规）。
#   故由本模块（上游）在 TOOL_CATEGORIES 就绪后主动注入；注入失败不影响路由。
if ToolTraceRecorder is not None:
    try:
        from agent.observability.tool_trace import (
            register_tool_category_source as _register_category_source,
        )

        _register_category_source(lambda: TOOL_CATEGORIES)
    except Exception:  # noqa: BLE001 注入失败不得影响工具路由
        pass

# 工具别名映射 —— **已废弃为空**（2026-09-17）
# ══════════════════════════════════════════════════════════════════════════
# 【为什么废弃：这套机制的设计意图是去重，实际效果是**删掉唯一可用的工具**】
#   原三条映射全部是错的，且已实测造成能力丢失：
#     "shell_execute":  ["run_program"]      → 方向尚可，但 code 分类优先级 3，在
#                                              复合请求的截断中根本进不了结果 ⇒ 等于
#                                              把 shell_execute 与 run_program 双双删掉
#     "read_file":      ["read_pdf"]         → **硬功能缺陷**：read_file 对二进制内容
#                                              返回 binary=True，读不了 PDF；read_pdf 才
#                                              是唯一能读 PDF 的工具。实测「读取pdf文件
#                                              的内容」→ 结果里 read_pdf 缺席、read_file
#                                              在场 ⇒ 用户要读 PDF，拿到的工具读不了 PDF
#     "list_directory": ["list_processes"]   → 说"列出进程"时进程工具被目录工具挤掉，
#                                              二者语义毫无关系
#   生效点在 `_apply_alias_merge_and_priority_sort`，且**两条路由路径共用**
#   （关键词路由与 hybrid 路由），故 hybrid 路径同样中招（hybrid 会传入全部类别，
#   read_file 与 read_pdf 同时进 top-k 是常态，删除纯由词面碰撞触发）。
#
# 【正确的去重方式是合并实现，不是对候选集做减法】
#   一个工具 + 一个 `mode` 参数。第 0 档合并（docs/工具集评估与重分类报告.md §6.2）
#   正是按这个方向做的：web_extract / data_convert 等。
#
# 【为什么保留这个空符号而不删】多处（tests/unit/test_tool_router_pinned.py 的
#   `_candidates` 复刻、若干 scripts/ 诊断脚本）仍 `from agent.tool_router import
#   TOOL_ALIASES` 并遍历它。保留空 dict ⇒ 这些调用方行为不变（遍历零次）而不 ImportError。
#   新增映射**一律不允许**：要合并就去合并实现。
TOOL_ALIASES: dict[str, list[str]] = {}

# ════════════════════════════════════════════════════════════
#  "永不被截断"关注名单（pin list）
# ════════════════════════════════════════════════════════════
# 【不易】这些工具必须在"其所属类别**已被关键词命中**"的前提下，即使被 max_tools
#         截断也要补回结果（补回后**允许总数略超 max_tools**）。
#         —— 宁可多一个工具，也不让"委派"这种跨步骤能力在复合请求里凭空消失。
# 【变易】名单可增删；**只能放"类别命中才生效"的工具**，不得用来凭空塞入未命中类别的工具。
# 【简易】补回逻辑见 _restore_pinned_tools：只在"类别命中 ∧ 被截断丢掉"时补，
#         因此未命中 async 类别的输入仍拿不到 delegate（路由语义不被绕过）。
#
# 为什么是 pin list，而不是"全局调优"（提优先级 / 塞进 core）：
#   1. 提 async 的 priority 会把它排到 code(3) 之前 ⇒ **挤掉 code 分类的工具**
#      （每一次命中 code 的请求都受影响），且实测**不可靠**：复合输入下 25 的截断点
#      落在 code 组内部，async 仍可能被丢——治标不治本，还引入了全量 schema 漂移。
#   2. 把 delegate 放进 core 等于"每轮常驻"：它带八要素 schema，实测每轮多约
#      500–800 token，且与输入是否真的需要委派无关。
#   3. 两种做法都会改变**每一轮**请求的 schema 与 token 成本；而 pin list 是**定向**的：
#      只影响"该工具本来就被类别命中、却又被数量上限挤掉"的那部分输入。
#
# 为什么 2026-09-18 把 fan_out 也放进名单（Owner 授权本会话裁定）：
#   `fan_out`（并行多线委派）是"多 Agent 各管一条线"的入口能力，与 delegate 同属
#   **跨步骤能力**；实测复合输入「读取文件、搜索内容、执行命令…然后委派子代理汇总」下
#   25 件截断结果里 fan_out 被丢掉（delegate 因在名单内被补回）——恰是"最需要并行派发"
#   的那类复合请求看不到它。代价与 delegate 同类：只在 async 类别**已被关键词命中**时生效，
#   不改变其它任何一轮请求的 schema。基线随之从「25 + 1 = 26」变为「25 + 2 = 27」
#   （tests/unit/test_tool_router_pinned.py 已同步）。
PINNED_TOOLS: tuple = ("delegate", "fan_out")


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
        # 能力补全后新增：浏览器自动化（此前实现存在但未注册为工具）
        "浏览器", "browser", "无头浏览器", "网页截图", "渲染页面", "js 渲染",
    ],
    "file": [
        "文件", "读取", "写入", "目录", "文件夹", "保存", "打开文件",
        "创建文件", "删除文件", "移动文件", "复制文件", "压缩", "解压",
        "zip", "tar", "diff", "对比文件", "文件信息", "搜索文件",
        "列出", "文件大小", "修改时间", "file", "read", "write",
        # 编码动作也落在文件写路径上（实测「写代码」需召回 write_file）
        "写代码", "改代码", "编辑代码", "新建文件", "创建文件", "保存到文件",
    ],
    "code": [
        "执行", "命令", "shell", "终端", "cmd", "powershell", "bash",
        "json", "yaml", "xml", "格式化", "校验", "转换", "检测格式",
        "代码审查", "架构图", "review", "代码", "脚本", "运行",
        "humanize", "ai写作", "代码检查",
        # 工程交付原语（能力补全后新增的 git / run_tests / apply_patch）
        "跑测试", "运行测试", "单元测试", "测试一下", "跑一下测试", "回归测试",
        "pytest", "跑用例", "执行测试", "lint", "类型检查",
        "git", "提交代码", "提交改动", "打个补丁", "应用补丁", "patch", "diff 应用",
        "沙箱执行", "沙箱里跑",
        # 编码动作（实测「写代码并运行测试」曾召回不到 write_file/edit/grep）
        "写代码", "改代码", "编辑代码", "重构", "加个函数", "实现功能",
        "代码修改", "改一下代码", "搜索代码", "找代码",
        # 软件安装/卸载（原 software 分类的 4 个工具已于 2026-09-17 注销）
        # 语义迁移到 code 分类的 shell_execute —— 装软件的正确做法是调系统包管理器，
        # 而不是让一个空壳工具谎报成功。原 software 关键词键已随之移除（否则会变成死键）。
        "安装软件", "卸载软件", "搜索软件", "软件包", "软件列表", "软件管理",
        "安装包", "装个软件", "chocolatey", "pip install", "npm install",
        "apt install", "apt-get", "brew install", "winget",
    ],
    "system": [
        "进程", "启动程序", "运行程序", "天气", "温度", "天气预报",
        "程序", "process", "weather", "停止", "打开", "notepad",
        "calc", "白名单",
        # 能力补全后新增：剪贴板与屏幕视觉
        "剪贴板", "粘贴板", "clipboard", "屏幕", "截屏", "截图",
        "看一下屏幕", "屏幕上", "识别图片文字", "ocr",
    ],
    "extension": [
        "安装扩展", "卸载扩展", "技能", "插件", "mcp", "通道",
        "扩展市场", "扩展列表", "扩展管理", "安装技能",
        "安装插件", "拓展", "channel", "webhook", "邮件",
        "ext_", "扩展",
        # 自我扩展入口（评估报告 §1.5-A：这些工具此前 uncategorized ⇒ 永不命中）
        "生成工具", "新工具", "自生成", "写个工具", "造个工具", "自定义工具",
        "安装工具", "装个工具", "连接 mcp", "扫描 mcp", "市场",
    ],
    "pdf": [
        "pdf", "合并pdf", "拆分pdf", "读取pdf", "pdf信息",
        "pdf文件", "pdf处理", "pdf合并",
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
    "knowledge": [
        "知识库", "知识", "素材", "入库", "提炼", "笔记", "卡片", "产卡",
        "巡检", "断链", "孤儿", "wiki", "kb_", "过程蒸馏", "固化",
        "workflow 固化", "sop", "复盘",
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
      6. 关注名单补回（功能4）— 把 PINNED_TOOLS 中"类别已命中却被第 5 步挤掉"的
         工具补回结果末尾;**补回后允许总数略超 max_tools**(宁可多一个工具,也不让
         "委派"这种能力在复合请求里凭空消失)。未命中其类别的 pinned 工具**不会**
         被加入——补回不是绕过路由。

    Args:
        user_input: 用户原始输入文本
        enabled_whitelist: 启用工具白名单,None 表示不限制
        max_tools: 返回工具数上限,默认 25;None 或 <=0 表示不限制

    Returns:
        排序+截断(可能含补回的 pinned 工具,故长度可能为 max_tools+len(补回数))后的工具名列表
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

    # 任务6：进化策略注入（工具路由层）— 高失败率工具的备用路径策略
    # 策略 param_patch 含 fallback_tools 时,将备用工具补入结果(追加末尾,不破坏排序);
    # 注入失败不影响路由主流程(降级为无策略)。
    try:
        from agent.evolution.injector import get_injector
        inj = get_injector()
        if inj is not None:
            try:
                from agent.monitoring.tracing import get_trace_id
                trace_id = get_trace_id() or ""
            except Exception:
                trace_id = ""
            fallback_extra: list[str] = []
            hit_ids: list[str] = []
            for tool in list(result):
                for s in inj.get_strategies(f"tool:{tool}", trace_id=trace_id):
                    hit_ids.append(s["strategy_id"])
                    fb = (s.get("param_patch") or {}).get("fallback_tools") or []
                    fallback_extra.extend(str(t) for t in fb if str(t) not in result)
            if hit_ids:
                logger.info(
                    "[进化][路由注入] trace_id=%s 命中策略 %d 条: strategy_ids=%s, 追加备用工具: %s",
                    trace_id, len(hit_ids), hit_ids, fallback_extra,
                )
            if fallback_extra:
                result = result + fallback_extra
    except Exception:
        pass

    # 记录工具选择决策（安全降级：recorder 不可用或异常不影响路由）
    if ToolTraceRecorder is not None:
        try:
            ToolTraceRecorder.instance().record_tool_selection(user_input, categories, result)
        except Exception:
            pass

    return result


def _restore_pinned_tools(
    sorted_tools: list[str],
    selected: set,
    max_tools: int,
    keep: set | None = None,
) -> list[str]:
    """截断到 max_tools，但**保底工具与 PINNED_TOOLS 一律保留**

    Args:
        sorted_tools: 已排序、**尚未截断**的工具名列表
        selected: 已过白名单交集的候选集合(＝类别命中集合)
        max_tools: 上限;调用方保证为正整数且 ``len(sorted_tools) > max_tools``
        keep: **必保集合**（类别保底工具）。这些即使在截断点之外也保留，
              并占用名额 ⇒ 截断只裁非保底部分。见 `_apply_alias_merge_and_priority_sort`
              的"类别保底"说明（防止优先级阶梯把靠后类别饿死）。

    Returns:
        截断后的工具名列表；**保底与补回后总数允许略超 max_tools**。

    【不易】① pinned 补回条件必须同时成立：在 PINNED_TOOLS 里 ∧ 本就在 `selected` 里
            ∧ 本就在 `sorted_tools` 里却被丢掉 ⇒ 未命中类别的 pinned 工具**不可能**
            被加入，不会绕过路由。② `keep` 里的工具必须**本来就在 sorted_tools 中**
            （保底集合由调用方从 selected 派生），同样不会引入未命中类别的工具。
    【变易】名单可增删；`selected` / `sorted_tools` 的语义由调用方保证。
    【简易】纯函数；只读 max_tools，不改写任何模块状态。
    """
    keep = set(keep or ())
    # 保底工具全部保留，且优先占位
    kept: list[str] = [t for t in sorted_tools if t in keep]
    kept_set = set(kept)
    # 剩余名额按原顺序填充
    room = max(0, max_tools - len(kept))
    for t in sorted_tools:
        if len(kept) >= max_tools:
            break
        if t in kept_set:
            continue
        kept.append(t)
        kept_set.add(t)

    dropped = set(sorted_tools) - kept_set
    for tool in PINNED_TOOLS:
        if tool in kept_set:
            continue
        if tool in dropped and tool in selected:
            logger.info("[工具路由] 关注名单补回被截断的工具: %s(总数 %d → %d, 上限 %d)",
                        tool, len(kept), len(kept) + 1, max_tools)
            kept.append(tool)
            kept_set.add(tool)
    return kept


# ════════════════════════════════════════════════════════════
#  【DET-2】候选 / 类别的**迭代序**确定化（本族的**唯一收敛点**）
# ════════════════════════════════════════════════════════════
# 【缺陷族】把 set 交给**稳定排序** ⇒ 分数/优先级**并列**项的先后 = set 迭代序
#   = 字符串哈希随机化 ⇒ 同一个查询在**不同进程**里可能得到不同的结果；
#   当截断点落在并列块内部时，连下发集的**成员**都会变（不只是顺序）。
#   本族已被独立发现 3 次：
#     · agent/tool_router_hybrid.py 融合入口 + 下发阶（E1-D 已修）；
#     · agent/tool_router.py get_tools_for_input（DET-2 修，走本函数）；
#     · agent/skills_mgmt/loader.py _tfidf_scan（DET-2 修，同族不同子系统）。
# 【收敛点】本函数是关键词路由与 hybrid 路由**共用**的"排序 + 截断"唯一入口，
#   故把"set ⇒ 确定次序"收敛在**这一处**，而不是每个调用方各修一遍：
#     · 调用方递进来的若已是**有序序列**，原样保留它的次序
#       —— 那是它自己的"候选汇合序"（例如 hybrid 的相关度序），helper 无权也不该改；
#     · 若是 set/frozenset（**次序信息在传参前就已丢失**），
#       则按「类别声明序 → 名字」确定化。
#   两个调用方因此都变确定：hybrid 侧由调用方给语义序（E1-D），
#   关键词侧由本处兜底（DET-2），且**再无第三个入口**能绕过。
# 【为什么兜底取「类别声明序」而不是直接取名字字典序】
#   ① 与 E1-D 的 hybrid 口径同源（相关度序 → 类别声明序 → 字典序）：声明序是人工
#      编排的顺序（同类别内相邻工具语义相近），比字典序更贴合"优先级/相关度"的意图；
#   ② 名字字典序在 hybrid 路会在**分数并列处重排 BM25 本路**，而"降级路上融合顺序
#      必须与 raw BM25 顺序**逐位一致**"是既有明文契约
#      （tests/unit/test_tool_router_hybrid_fusion_calibration.py::
#       test_degraded_path_order_matches_raw_bm25）—— 那等于把"修非确定性"做成
#       "改本路排序"。声明序兜底**只在 set 输入时生效**：hybrid 传的是有序序列
#      ⇒ 该契约逐位不受影响（回归已验证）。
#   名字只作**最后兜底**（候选/类别不在 TOOL_CATEGORIES 里时），保证任何输入都完全确定。
_DECL_MISSING = 1 << 30


def _declaration_positions() -> tuple[dict, dict]:
    """(工具声明位置, 类别声明位置) —— TOOL_CATEGORIES 的人工编排顺序。

    【不易】只读 TOOL_CATEGORIES，不改写任何模块状态。
    【变易】每次调用现算：TOOL_CATEGORIES 可由 YAML 派生、也可能被测试替换，
            缓存会与"当时的表"脱钩。代价 ≈ 工具总数 次字典写入，
            且只在输入是 set/frozenset 时才会走到。
    【简易】未在表中的键由调用方用 _DECL_MISSING 兜底 + 名字排序。
    """
    cat_pos = {c: i for i, c in enumerate(TOOL_CATEGORIES)}
    tool_pos: dict = {}
    for cat_info in TOOL_CATEGORIES.values():
        for tool in (cat_info or {}).get("tools", []):
            tool_pos.setdefault(tool, len(tool_pos))
    return tool_pos, cat_pos


def _ordered_candidates(selected) -> tuple[list, set]:
    """把候选规范成「**确定次序**的序列 + 成员集合」。

    有序序列（list/tuple）⇒ 原样保留（去掉重复，保持首次出现的位置）；
    其余（set/frozenset/任何无内禀次序的可迭代）⇒ 类别声明序 → 名字。
    """
    if isinstance(selected, (list, tuple)):
        seq = list(dict.fromkeys(selected))
        return seq, set(seq)
    members = set(selected)
    tool_pos, _ = _declaration_positions()
    seq = sorted(members, key=lambda t: (tool_pos.get(t, _DECL_MISSING), str(t)))
    return seq, members


def _ordered_categories(categories) -> list:
    """把类别集合规范成「**确定次序**的序列」。

    有序序列 ⇒ 原样保留；其余 ⇒ (priority, 声明位置, 名字)。
    为什么这里也要确定：helper 里 matched_cats 按 priority **稳定排序**，
    本表存在**同优先级**的两个类别（extension=5 / knowledge=5 等）
    ⇒ 传 set 时这两类的先后随哈希变，而它们决定 floors（类别保底）的先后，
    进而影响截断点上的**成员**。
    """
    if isinstance(categories, (list, tuple)):
        return list(dict.fromkeys(categories))
    _, cat_pos = _declaration_positions()
    return sorted(
        set(categories),
        key=lambda c: (TOOL_CATEGORIES.get(c, {}).get("priority", 99),
                       cat_pos.get(c, _DECL_MISSING), str(c)),
    )


def _apply_alias_merge_and_priority_sort(
    selected,
    categories,
    max_tools: int,
    preferred_order: list[str] | None = None,
) -> list[str]:
    """相关度优先 + 类别兜底排序 + 数量截断(供关键词路由与 hybrid 路由共用)

    Why: tool_router_hybrid.py 复用此 helper,确保排序/截断逻辑单一来源。
    约束: tool_to_priority 取最小值、max_tools<=0 不限制、PINNED_TOOLS 关注名单补回。

    【preferred_order：为什么需要它（2026-09-17）】
      关键词路由没有"相关度"这个概念——它只有"命中了哪些类别"，所以按类别 priority
      排序是对的。但 hybrid 路由**有真实相关度分数**（BM25+Embedding 融合排序）。
      若把 hybrid 的候选也一律按类别 priority 重排，就会出现**相关度被优先级覆盖**：
      例如「读取 PDF 的内容」，pdf 类别 priority=6，web(1)/file(2) 的工具会把名额吃光，
      `read_pdf` 被挤出到 max_tools 之外 ⇒ 命中了却拿不到。
      因此：传入 `preferred_order`（= 检索器的相关度序）时，**先按相关度保留**，
      再用类别 priority 补充剩余名额。这样"命中且相关"的工具不会被优先级挤掉，
      而"类别命中但检索没召回"的工具仍能补进来（这才是补位的目的）。

    【别名合并已移除（2026-09-17）】原第一步会把"主工具已入选"的别名工具从候选集里
    减去，而原三条映射全是错的 —— 最严重的是 `read_file → read_pdf`：read_file 对
    二进制返回 binary=True 读不了 PDF，read_pdf 才是唯一能读的工具，于是别名机制
    把唯一可用的那个删掉了。详见 TOOL_ALIASES 处的长注释。
    正确做法是合并**实现**（一个工具 + mode 参数），不是对候选集做减法。

    【DET-2 · 入参次序契约】`selected` 可以是 set 或**有序序列**；`categories` 同理。
      · 有序序列 ⇒ 原样保留其次序（那是调用方的"候选汇合序"）；
      · set ⇒ 由本函数按「类别声明序 → 名字」确定化。
      **两种输入的输出都与进程无关**（不再依赖字符串哈希随机化）。
      本函数是两条路由路径唯一的"排序 + 截断"入口 ⇒ 确定化收敛在**这一处**。
    """
    # 【DET-2】先把两个入参规范成**确定次序**（见本函数上方"迭代序确定化"一段）：
    #   · selected_seq / categories_seq = 有序序列（调用方给了有序序列就原样保留）
    #   · selected_set = 成员集合（成员判定一律走它，语义与改前逐位一致）
    selected_seq, selected_set = _ordered_candidates(selected)
    categories_seq = _ordered_categories(categories)

    # 【功能 1】优先级排序:工具 → 其所属类别中最小的 priority
    tool_to_priority: dict[str, int] = {}
    for cat in categories_seq:
        cat_info = TOOL_CATEGORIES.get(cat)
        if not cat_info:
            continue
        pri = cat_info.get("priority", 99)
        for tool in cat_info["tools"]:
            if tool in selected_set:
                if tool not in tool_to_priority or pri < tool_to_priority[tool]:
                    tool_to_priority[tool] = pri

    # 【功能 2】**类别保底**（2026-09-17）
    # 【为什么必须有】纯按类别 priority 排序时，优先级阶梯会把靠后的类别饿死：
    #   core(0)=7 + file(2)=13 = 20，再往下 code(3)=15 只能挤进 5 个 ⇒
    #   实测「帮我写代码并运行测试」里属于 code 的 edit/grep/shell_execute 全被挤掉。
    #   这与老旧口径"core+web+file 恰好 25 ⇒ 七个类别全为 0"是同一个病。
    #   修法与主线装配器的平面保底同精神：**每个命中类别先各取 N 个**，
    #   剩余名额再按相关度/优先级分配；截断只裁"非保底"部分。
    _FLOOR_PER_CATEGORY = 3
    # 【不易】保底必须让位于 max_tools，不能突破它。
    #   主线装配器（agent/lines/assembler.py）刻意选择"保底优先、允许略超 max_tools"，
    #   但**旧路由的 max_tools 是硬上限契约**——调用方用它做 token 预算。
    #   实测 `hybrid_select_tools("搜索", max_tools=2)` 曾因保底返回 6 个而破坏契约。
    #   故按预算**缩放**保底数：floor_n ≤ max_tools / 命中类别数 ⇒ Σ保底 ≤ max_tools。
    #   预算太小就保不住（上限说了算），但预算够时任何命中类别都不会归零。
    matched_cats = [
        c for c in categories_seq
        if TOOL_CATEGORIES.get(c, {}).get("tools")
        and any(t in selected_set for t in TOOL_CATEGORIES[c]["tools"])
    ]
    floor_n = _FLOOR_PER_CATEGORY
    if max_tools and max_tools > 0 and matched_cats:
        if max_tools >= len(matched_cats):
            # 保底总额不超过**半预算**：否则大量类别命中时保底会吃光名额，
            # 相关度排序就没了意义（保底是"防饿死"，不是"平均分配"）。
            floor_n = max(1, min(_FLOOR_PER_CATEGORY,
                                 (max_tools // 2) // len(matched_cats)))
        else:
            # 预算连"每个命中类别 1 个"都不够 ⇒ 保底无从谈起，让顺序决定（守硬上限）
            floor_n = 0

    floors: list[str] = []
    floor_set: set[str] = set()
    _pref_index = {t: i for i, t in enumerate(preferred_order or [])}
    for cat in sorted(matched_cats,
                      key=lambda c: TOOL_CATEGORIES.get(c, {}).get("priority", 99)):
        cat_tools = [t for t in TOOL_CATEGORIES.get(cat, {}).get("tools", []) if t in selected_set]
        if not cat_tools:
            continue
        # 类别内排序：相关度优先（有则用），否则保持类别内既定顺序
        cat_tools.sort(key=lambda t: (_pref_index.get(t, 10_000),))
        for t in cat_tools[:floor_n]:
            if t not in floor_set:
                floor_set.add(t)
                floors.append(t)

    # 【功能 3】剩余名额：相关度领跑 → 类别优先级补位 → 余下相关度
    if preferred_order:
        head_all: list[str] = []
        seen: set[str] = set()
        for t in preferred_order:
            if t in selected_set and t not in seen:
                seen.add(t)
                head_all.append(t)
        head_set = set(head_all)
        # 【DET-2】迭代 selected_seq（确定次序）而非 selected：并列项的先后由它决定
        tail = [t for t in sorted(selected_seq, key=lambda x: tool_to_priority.get(x, 99))
                if t not in head_set]
        if max_tools and max_tools > 0:
            head_cap = max(1, max_tools // 2)
            result = head_all[:head_cap] + tail + head_all[head_cap:]
        else:
            result = head_all + tail
    else:
        # 【DET-2】同上：关键词路由（无 preferred_order）走这条；迭代确定次序
        result = sorted(selected_seq, key=lambda t: tool_to_priority.get(t, 99))

    # 保底工具前置（它们已在 selected 内，不会引入未命中类别的工具）
    result = floors + [t for t in result if t not in floor_set]

    # 【功能 4】数量截断：只裁"非保底"部分，保底一律保留
    #         最后补回 PINNED_TOOLS 中被挤掉的工具(见 _restore_pinned_tools;
    #         补回后允许总数略超 max_tools)
    if max_tools is not None and max_tools > 0 and len(result) > max_tools:
        result = _restore_pinned_tools(result, selected_set, max_tools, keep=floor_set)
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
