#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
file_monitor.py — 文件系统监控服务后端 (Flask)

【不易】API 契约 (端点/字段) 不可变; 线程安全边界不可变。
【变易】MONITOR_ROOT / LOG_LEVEL / SCAN_INTERVAL 可经环境变量演进; 模块表可扩展。
【简易】后台线程扫描 → DashboardCache; API 即时返回缓存, 不阻塞扫描。

启动: python file_monitor.py  (监听 5679)
"""
import datetime
import logging
import os
import re
import subprocess
import threading
import time
import xml.etree.ElementTree as ET

from flask import Flask, jsonify, render_template

# ============================== 日志配置 ==============================
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s | %(message)s",
    encoding="utf-8",
    force=True,
)
logger = logging.getLogger("file_monitor")

# ============================== 基础配置 ==============================
# 监控根目录: 默认本文件所在目录, 支持 MONITOR_ROOT 覆盖 (Docker 部署用)
MONITOR_ROOT = os.environ.get("MONITOR_ROOT") or os.path.dirname(os.path.abspath(__file__))

# 扫描周期 (秒)
SCAN_INTERVAL = 8

# 服务端口
PORT = 5679

# 覆盖率文件 (coverage.py 输出的 Cobertura XML)
COVERAGE_XML = os.environ.get("COVERAGE_XML") or os.path.join(MONITOR_ROOT, "coverage.xml")

# 覆盖率文件异常大小阈值 (50MB), 超过视为异常, 不解析
COVERAGE_MAX_BYTES = 50 * 1024 * 1024

# Git 分支配置 (可自定义): main 用 set 精确匹配, dev/release 用前缀元组
GIT_BRANCH_CONFIG = {
    "main_branches": {"master", "main", "prod", "production"},
    "dev_branch_prefixes": ("feature/", "develop", "dev/", "topic/"),
    "release_branch_prefixes": ("release/", "hotfix/", "bugfix/"),
}

# 排除目录名 (os.walk 就地剪枝)
EXCLUDE_DIR_NAMES = {
    "__pycache__", ".git", "_edge_profile", "node_modules", ".venv", "venv",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", "htmlcov", "coverage_report",
    ".claude", ".superpowers", ".file_backups", "yunshu-ui", "_ci_logs",
    "test_reports", "workspace",
}

# 排除相对路径 (目录或文件, 相对 MONITOR_ROOT, 正斜杠)
EXCLUDE_REL_PATHS = {
    "data/lifetrace", "data/state", "data/stress_test_lifetrace",
    "data/test_integration_lifetrace", "data/test_lifetrace",
    "data/test_enhanced_recorder", "data/test_memory_tree",
    "data/test_reflection", "data/sessions", "data/logs",
    "data/benchmark", "data/replays",
}

# 文件扩展名 → 类型映射
TYPE_MAP = {
    ".py": "Python", ".js": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript",
    ".jsx": "JavaScript", ".ts": "TypeScript", ".tsx": "TypeScript",
    ".html": "HTML", ".htm": "HTML", ".css": "CSS", ".scss": "CSS", ".less": "CSS",
    ".json": "JSON", ".yaml": "YAML", ".yml": "YAML", ".toml": "TOML",
    ".md": "Markdown", ".rst": "reStructuredText", ".txt": "Text",
    ".sh": "Shell", ".bash": "Shell", ".bat": "Batch", ".cmd": "Batch", ".ps1": "PowerShell",
    ".xml": "XML", ".sql": "SQL", ".go": "Go", ".rs": "Rust", ".java": "Java",
    ".c": "C", ".h": "C", ".cpp": "C++", ".cc": "C++", ".hpp": "C++",
    ".cs": "C#", ".rb": "Ruby", ".php": "PHP", ".swift": "Swift", ".kt": "Kotlin",
    ".vue": "Vue", ".svelte": "Svelte",
    ".cfg": "INI", ".ini": "INI", ".conf": "INI", ".env": "INI",
    ".svg": "SVG", ".png": "Image", ".jpg": "Image", ".jpeg": "Image",
    ".gif": "Image", ".ico": "Image", ".webp": "Image",
    ".woff": "Font", ".woff2": "Font", ".ttf": "Font", ".eot": "Font",
    ".csv": "CSV", ".log": "Log",
}

# Git 流程阶段 → 中文标签
STAGE_LABELS = {
    "untracked": "未跟踪",
    "staged_new": "已暂存(新增)",
    "modified": "已修改",
    "committed": "已提交",
    "dev_branch": "开发分支",
    "release": "发布分支",
    "merged_main": "已合并主线",
}

# ============================== 模块归属表 ==============================
# (前缀, 模块名, 功能说明) — 顺序敏感: 更具体的前缀须在前
MODULE_INFO = [
    ("agent/orchestrator", "编排器", "Agent 编排入口"),
    ("agent/memory", "记忆模块", "记忆存储/路由/过滤/审查"),
    ("agent/skills_mgmt", "技能管理", "技能加载/创建/审查/搜索/同步"),
    ("agent/cognitive", "认知引擎", "Actor-Critic/辩论/反思/知识"),
    ("agent/task_planner", "任务规划", "DAG/规划/执行"),
    ("agent/tools", "工具集", "浏览器/代码/文件/PDF/Shell 工具"),
    ("agent/utils", "通用工具", "兼容性/序列化/性能监控"),
    ("agent/web", "Web 工具", "爬虫/抓取/搜索/HTTP 客户端"),
    ("agent/network", "网络配置", "网络配置管理"),
    ("agent/monitoring", "监控", "指标/Prometheus/自愈/追踪"),
    ("agent/observability", "可观测性", "链路追踪"),
    ("agent/health", "健康评估", "健康评分/仪表盘"),
    ("agent/audit", "审计", "审计日志/可观测性"),
    ("agent/caching", "缓存", "缓存可观测性"),
    ("agent/extensions", "扩展管理", "扩展安装/市场/沙箱"),
    ("agent/guardrails", "安全护栏", "输入防护"),
    ("agent/handoff", "交接", "任务交接"),
    ("agent/human_in_the_loop", "人机协同", "HITL 交互"),
    ("agent/lazy_loader", "懒加载", "延迟加载核心"),
    ("agent/log_system", "日志系统", "日志采集/分析/格式化"),
    ("agent/model_router", "模型路由", "模型适配/路由"),
    ("agent/p6", "P6 快照", "频率/性能/快照"),
    ("agent/prompt_manager", "提示词管理", "提示词注册/存储"),
    ("agent/quality", "质量管理", "缺陷追踪"),
    ("agent/server_routes", "服务路由", "Flask 路由"),
    ("agent/subagent", "子代理", "子代理生命周期/沙箱"),
    ("agent/tests", "Agent 测试", "Agent 内部测试"),
    ("agent/workflow_engine", "工作流引擎", "工作流引擎/匹配"),
    ("agent/data", "Agent 数据", "Agent 数据/技能配置"),
    ("agent/", "Agent 核心", "Agent 顶层模块"),
    ("tests", "测试套件", "单元/集成/E2E/性能/混沌测试"),
    ("templates", "页面模板", "Flask HTML 模板"),
    ("static", "静态资源", "前端 CSS/JS 资源"),
    ("scripts", "运维脚本", "部署/诊断/校验脚本"),
    ("docs", "文档", "架构/运维/发布文档"),
    ("configs", "配置", "应用/模型/路径配置"),
    ("sensor", "传感器", "硬件/行为/环境传感器"),
    ("lifetrace", "生命轨迹", "轨迹记录/检索/记忆树"),
    ("memory", "外部记忆", "记忆管理/存储/摘要/向量"),
    ("mcp_services", "MCP 服务", "MCP 客户端/模拟服务"),
    ("planning", "规划引擎", "规划/分解/执行/反思"),
    ("persona", "人格注入", "人格注入器"),
    ("cognitive", "认知配置", "认知配置/模板/翻译"),
    ("core", "核心基础", "本地 LLM/注册表"),
    ("packages", "扩展包", "扩展包(kwarg 扫描等)"),
    ("patches", "补丁", "安全补丁"),
    ("monitoring", "监控部署", "Prometheus/告警部署"),
    ("deploy", "部署", "部署配置"),
    ("docker", "容器化", "Docker 配置"),
    (".github", "CI/CD", "GitHub Actions 工作流"),
]


# ============================== 工具函数 ==============================
def _norm(path):
    """路径归一化为正斜杠 (相对路径契约)。"""
    return path.replace("\\", "/")


def _match_prefix(rel, prefix):
    """前缀边界匹配: prefix 以 / 结尾用 startswith; 否则精确或 path/ 边界。"""
    if prefix.endswith("/"):
        return rel.startswith(prefix)
    return rel == prefix or rel.startswith(prefix + "/")


def classify_module(rel):
    """返回 (模块名, 功能说明)。未命中返回顶层文件。"""
    for prefix, name, desc in MODULE_INFO:
        if _match_prefix(rel, prefix):
            return name, desc
    return "顶层文件", "项目根目录文件"


def classify_branch(branch):
    """分支分类: main/dev/release/other。【不易】分类规则锁定。"""
    if branch in GIT_BRANCH_CONFIG["main_branches"]:
        return "main"
    for p in GIT_BRANCH_CONFIG["dev_branch_prefixes"]:
        if branch.startswith(p):
            return "dev"
    for p in GIT_BRANCH_CONFIG["release_branch_prefixes"]:
        if branch.startswith(p):
            return "release"
    return "other"


def file_type(name):
    ext = os.path.splitext(name)[1].lower()
    if not ext:
        return "Other"
    return TYPE_MAP.get(ext, ext.lstrip(".").upper())


def iso_ts(ts):
    try:
        return datetime.datetime.fromtimestamp(ts).isoformat()
    except (OSError, ValueError, OverflowError):
        return ""


# ============================== 分支切换日志 ==============================
# 模块级状态: 首次检测 / 切换 / 未切换
_LAST_BRANCH = {"value": "", "ts": 0.0}


def log_branch_change(branch):
    now = time.time()
    btype = classify_branch(branch)
    # 首次检测: ts 仍为初始 0
    if _LAST_BRANCH["ts"] == 0.0:
        logger.info("[分支] 首次检测: %s (type=%s)", branch, btype)
        _LAST_BRANCH["value"] = branch
        _LAST_BRANCH["ts"] = now
        return
    if branch != _LAST_BRANCH["value"]:
        gap = int(now - _LAST_BRANCH["ts"])
        logger.warning(
            "[分支切换] %s → %s (距上次扫描 %ds) | 新分支类型=%s | "
            "注意: 工作区/暂存区/未跟踪文件可能已变化",
            _LAST_BRANCH["value"], branch, gap, btype,
        )
        _LAST_BRANCH["value"] = branch
        _LAST_BRANCH["ts"] = now
    else:
        logger.debug("[分支] 未切换: %s", branch)
        _LAST_BRANCH["ts"] = now


# ============================== Git 操作 (全部容错) ==============================
def _run_git(args, timeout=10):
    """运行 git 子进程, 失败返回 None。cwd 锁定 MONITOR_ROOT。"""
    try:
        r = subprocess.run(
            ["git"] + args,
            cwd=MONITOR_ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        if r.returncode != 0:
            logger.debug("[git] %s 失败 rc=%d: %s", " ".join(args), r.returncode,
                         (r.stderr or "").strip()[:200])
            return None
        return r.stdout
    except (OSError, subprocess.SubprocessError) as e:
        logger.debug("[git] %s 异常: %s", " ".join(args), e)
        return None


def get_current_branch():
    out = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], timeout=5)
    if not out:
        return ""
    return out.strip()


def get_git_porcelain():
    """git status --porcelain -z → {rel_path: code}。rename/copy 跳过旧路径。"""
    out = _run_git(["status", "--porcelain", "-z", "--untracked-files=all"])
    if out is None:
        return {}
    result = {}
    parts = out.split("\0")
    i = 0
    while i < len(parts):
        rec = parts[i]
        i += 1
        if not rec:
            continue
        code = rec[:2]
        path = rec[3:]
        if not path:
            continue
        result[_norm(path)] = code
        # rename/copy: 下一段是旧路径, 跳过
        if code and code[0] in ("R", "C") and i < len(parts):
            i += 1
    logger.debug("[git] porcelain 条目数=%d", len(result))
    return result


def get_git_tracked():
    out = _run_git(["ls-files"])
    if out is None:
        return set()
    return {_norm(line) for line in out.splitlines() if line.strip()}


def load_last_commits(limit=1000):
    """单次 git log --name-only, 取每文件最近一次提交。limit 限制遍历提交数。"""
    out = _run_git(
        ["log", "--name-only", "--no-renames",
         "--format=@@@%H%x1f%an%x1f%aI", "-n", str(limit)],
        timeout=15,
    )
    if out is None:
        return {}
    result = {}
    cur = None
    for line in out.splitlines():
        if line.startswith("@@@"):
            parts = line[3:].split("\x1f")
            if len(parts) >= 3:
                cur = {"hash": parts[0], "author": parts[1], "date": parts[2]}
            else:
                cur = None
        elif line.strip():
            if cur is None:
                continue
            p = _norm(line.strip())
            if p and p not in result:
                result[p] = cur
    logger.debug("[git] last_commit 条目数=%d", len(result))
    return result


# Git 缓存: tracked / commits 按 branch 缓存 (同分支不重复拉取)
_git_tracked_cache = {"branch": None, "data": set()}
_git_commits_cache = {"branch": None, "data": {}}


def get_git_tracked_cached(branch):
    if _git_tracked_cache["branch"] != branch:
        _git_tracked_cache["data"] = get_git_tracked()
        _git_tracked_cache["branch"] = branch
    return _git_tracked_cache["data"]


def load_last_commits_cached(branch):
    if _git_commits_cache["branch"] != branch:
        _git_commits_cache["data"] = load_last_commits()
        _git_commits_cache["branch"] = branch
    return _git_commits_cache["data"]


def map_git_state(code):
    """porcelain XY → git_state。【不易】状态枚举锁定。"""
    code = code.strip()
    if code.startswith("??"):
        return "untracked"
    if code.startswith("A"):
        return "added"
    if code.startswith("R") or code.startswith("C"):
        return "renamed"
    if code.startswith("D") or code.endswith("D"):
        return "deleted"
    if "M" in code:
        return "modified"
    return "modified"


def compute_git_stage(git_state, branch_type):
    """git_state + branch_type → git_stage。【不易】阶段枚举锁定。"""
    if git_state == "untracked":
        return "untracked"
    if git_state == "added":
        return "staged_new"
    if git_state in ("modified", "deleted", "renamed"):
        return "modified"
    # clean tracked: 按分支类型细分
    if branch_type == "main":
        return "merged_main"
    if branch_type == "dev":
        return "dev_branch"
    if branch_type == "release":
        return "release"
    return "committed"


# ============================== 覆盖率缓存 ==============================
class CoverageCache:
    """coverage.xml 解析, 分层容错防崩溃。【不易】损坏文件必须返回空覆盖率。"""

    def __init__(self, path):
        self.path = path
        self.timestamp = 0.0
        self._loaded = False
        self._by_path = {}
        self._by_basename = {}

    def _mark_loaded_empty(self, mtime=0.0):
        # 必须更新 timestamp, 避免下次相同 mtime 重复解析损坏文件
        self.timestamp = mtime
        self._loaded = True
        self._by_path = {}
        self._by_basename = {}

    def load(self):
        if self._loaded:
            return
        # 1. 文件不存在
        if not os.path.exists(self.path):
            logger.debug("[coverage] 文件不存在: %s", self.path)
            self._mark_loaded_empty()
            return
        # 取 mtime
        try:
            mtime = os.path.getmtime(self.path)
        except OSError:
            mtime = 0.0
        # 2. 读取状态失败
        try:
            st = os.stat(self.path)
        except OSError as e:
            logger.warning("[coverage] 读取状态失败: %s (%s)", self.path, e)
            self._mark_loaded_empty(mtime)
            return
        # 3. 空文件
        if st.st_size == 0:
            logger.warning("[coverage] 空文件(0字节): %s", self.path)
            self._mark_loaded_empty(mtime)
            return
        # 4. 异常过大
        if st.st_size > COVERAGE_MAX_BYTES:
            logger.warning("[coverage] 异常过大(>%dMB): %s (%d字节)",
                           COVERAGE_MAX_BYTES // (1024 * 1024), self.path, st.st_size)
            self._mark_loaded_empty(mtime)
            return
        # 5. XML 损坏
        try:
            tree = ET.parse(self.path)
        except ET.ParseError as e:
            logger.error("[coverage] XML损坏(ParseError): %s (%s)", self.path, e)
            self._mark_loaded_empty(mtime)
            return
        root = tree.getroot()
        for cls in root.iter("class"):
            fname = cls.get("filename")
            if not fname:
                continue
            lr = cls.get("line-rate")
            if lr is None:
                continue
            # 6. line-rate 非数值
            try:
                rate = float(lr)
            except (TypeError, ValueError):
                logger.warning("[coverage] line-rate 非数值, 跳过: %s (%r)", fname, lr)
                continue
            # 7. 超出 [0,1] 钳制
            if rate < 0.0 or rate > 1.0:
                logger.debug("[coverage] line-rate 超出[0,1], 钳制: %s (%r)", fname, lr)
                rate = max(0.0, min(1.0, rate))
            norm = _norm(fname)
            self._by_path[norm] = rate
            self._by_basename[os.path.basename(norm)] = rate
        self.timestamp = mtime
        self._loaded = True
        logger.debug("[coverage] 加载完成: %d 条", len(self._by_path))

    def maybe_reload(self):
        """mtime 变化时重置并重新加载。"""
        try:
            m = os.path.getmtime(self.path)
        except OSError:
            m = 0.0
        if m != self.timestamp:
            self._loaded = False
            self.load()

    def get(self, rel):
        norm = _norm(rel)
        if norm in self._by_path:
            return self._by_path[norm]
        return self._by_basename.get(os.path.basename(norm))


coverage_cache = CoverageCache(COVERAGE_XML)


# ============================== 文档字符串缓存 ==============================
_DOC_RE = re.compile(r'\s*(?:#[^\n]*\n\s*)*("""|\'\'\')(.*?)\1', re.DOTALL)
_docstring_cache = {}


def _extract_docstring(head):
    m = _DOC_RE.match(head)
    if not m:
        return ""
    for ln in m.group(2).splitlines():
        ln = ln.strip()
        if ln:
            return ln[:120]
    return ""


def get_docstring_first_line(full_path, rel, mtime):
    """以 (rel, mtime) 为键缓存, 文件未变则不重复读。"""
    key = (rel, mtime)
    cached = _docstring_cache.get(key)
    if cached is not None:
        return cached
    line = ""
    try:
        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            head = f.read(4000)
        line = _extract_docstring(head)
    except OSError:
        line = ""
    _docstring_cache[key] = line
    if len(_docstring_cache) > 5000:
        _docstring_cache.clear()
    return line


# ============================== DashboardCache (线程安全) ==============================
class DashboardCache:
    """后台线程写, API 读。Lock 保护数据, Event 标记就绪。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self.files = []
        self.summary = {}
        self.refresh_count = 0
        self._last_update = 0.0

    def update(self, files, summary):
        with self._lock:
            self.files = files
            self.summary = summary
            self.refresh_count += 1
            self._last_update = time.time()
            self._ready.set()

    def snapshot(self):
        """返回 (files, summary, refresh_count, last_update)。"""
        with self._lock:
            return (self.files, dict(self.summary), self.refresh_count, self._last_update)


cache = DashboardCache()


# ============================== 扫描 ==============================
def _excluded_rel(rel):
    """是否命中排除相对路径 (精确或前缀边界)。"""
    for p in EXCLUDE_REL_PATHS:
        if rel == p or rel.startswith(p + "/"):
            return True
    return False


def scan_once():
    """单次扫描, 返回 (files, summary)。"""
    t0 = time.time()

    coverage_cache.maybe_reload()

    branch = get_current_branch()
    log_branch_change(branch)
    branch_type = classify_branch(branch)

    porcelain = get_git_porcelain()
    tracked = get_git_tracked_cached(branch)
    last_commits = load_last_commits_cached(branch)

    files = []
    total_dirs = 0
    tracked_count = 0
    type_counter = {}
    cov_sum = 0.0
    cov_count = 0

    for root, dirnames, filenames in os.walk(MONITOR_ROOT):
        # 就地剪枝: 排除目录名
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIR_NAMES]

        rel_root = _norm(os.path.relpath(root, MONITOR_ROOT))
        if rel_root == ".":
            rel_root = ""

        # 排除相对路径 (整棵子树剪枝)
        if rel_root and _excluded_rel(rel_root):
            dirnames[:] = []
            continue

        total_dirs += 1

        for fn in filenames:
            full = os.path.join(root, fn)
            rel = _norm(os.path.relpath(full, MONITOR_ROOT))
            if _excluded_rel(rel):
                continue
            try:
                st = os.stat(full)
            except OSError:
                continue

            # Git 状态
            if rel in porcelain:
                git_state = map_git_state(porcelain[rel])
            elif rel in tracked:
                git_state = "clean"
            else:
                git_state = "untracked"

            is_tracked = rel in tracked
            if is_tracked:
                tracked_count += 1

            git_stage = compute_git_stage(git_state, branch_type)

            # 模块归属
            module_name, module_desc = classify_module(rel)

            # 功能说明 = 模块 + 文档字符串首行 (仅 .py 提取 docstring)
            doc = ""
            if fn.endswith(".py"):
                doc = get_docstring_first_line(full, rel, st.st_mtime)
            func_str = ("%s · %s" % (module_name, doc)) if doc else ("%s · %s" % (module_name, module_desc))

            # 覆盖率
            cov = coverage_cache.get(rel)
            if cov is not None:
                cov_sum += cov
                cov_count += 1

            ftype = file_type(fn)
            type_counter[ftype] = type_counter.get(ftype, 0) + 1

            files.append({
                "name": fn,
                "path": rel,
                "type": ftype,
                "size": st.st_size,
                "module": module_name,
                "function": func_str,
                "coverage": cov,
                "git_state": git_state,
                "git_stage": git_stage,
                "git_stage_label": STAGE_LABELS.get(git_stage, git_stage),
                "mtime": iso_ts(st.st_mtime),
                "atime": iso_ts(st.st_atime),
                "ctime": iso_ts(st.st_ctime),
                "last_commit": last_commits.get(rel),
            })

    scan_ms = int((time.time() - t0) * 1000)

    # type_dist: [type, count] pairs, 按计数降序
    type_dist = [[k, v] for k, v in sorted(type_counter.items(), key=lambda kv: (-kv[1], kv[0]))]

    avg_coverage = round(cov_sum / cov_count, 4) if cov_count else 0.0

    summary = {
        "total_files": len(files),
        "total_dirs": total_dirs,
        "tracked": tracked_count,
        "has_coverage": cov_count > 0,
        "avg_coverage": avg_coverage,
        "type_dist": type_dist,
        "branch": branch,
        "branch_type": branch_type,
        "scan_ms": scan_ms,
    }
    return files, summary


# ============================== 后台守护线程 ==============================
def background_scanner():
    cycle = 0
    while True:
        cycle += 1
        t0 = time.time()
        logger.info("[刷新#%d] 开始扫描...", cache.refresh_count + 1)
        try:
            files, summary = scan_once()
            cache.update(files, summary)
            n = cache.refresh_count
            logger.info("[刷新#%d] 完成: %d 文件, 耗时 %dms, 缓存龄=0",
                        n, len(files), summary.get("scan_ms", 0))
        except Exception as e:  # noqa: BLE001 守护线程必须吞异常继续运行
            logger.error("[刷新#%d] 扫描异常: %s", cycle, e, exc_info=True)

        elapsed_scan = time.time() - t0
        sleep_s = max(0.0, SCAN_INTERVAL - elapsed_scan)
        time.sleep(sleep_s)
        total = time.time() - t0
        logger.debug("[刷新#周期%d] 实际耗时 %.2fs (sleep+scan)", cycle, total)


# ============================== Flask 应用 ==============================
app = Flask(__name__, template_folder=os.path.join(MONITOR_ROOT, "templates"))


@app.route("/")
def index():
    return render_template("file_monitor.html")


@app.route("/api/dashboard")
def api_dashboard():
    t0 = time.time()
    # 等待首次扫描就绪 (最长 30s)
    cache._ready.wait(timeout=30)
    files, summary, refresh_count, last_update = cache.snapshot()
    api_ms = int((time.time() - t0) * 1000)
    cache_age = int(time.time() - last_update) if last_update else 0
    summary["cache_age"] = cache_age
    summary["refresh_count"] = refresh_count
    summary["api_ms"] = api_ms
    logger.debug("[API] 响应 %d 文件, api=%dms, 缓存龄=%ds, 刷新次数=%d",
                 len(files), api_ms, cache_age, refresh_count)
    return jsonify({"files": files, "summary": summary})


@app.route("/api/config")
def api_config():
    # set 必须转 list 才能 JSON 序列化
    return jsonify({
        "git_branch_config": {
            "main_branches": sorted(list(GIT_BRANCH_CONFIG["main_branches"])),
            "dev_branch_prefixes": list(GIT_BRANCH_CONFIG["dev_branch_prefixes"]),
            "release_branch_prefixes": list(GIT_BRANCH_CONFIG["release_branch_prefixes"]),
        },
        "current_branch": get_current_branch(),
        "log_level": LOG_LEVEL,
    })


@app.route("/api/health")
def api_health():
    return jsonify({"status": "ok", "cache_ready": cache._ready.is_set()})


@app.route("/api/mock-dashboard")
def api_mock_dashboard():
    """返回 mock_git_states.json 模拟数据，用于测试 Git 流程状态显示的边界情况

    数据覆盖: 0/null/负数覆盖率、0/超大文件、远古/未来时间、中文/空格/超长文件名、
    XSS 注入测试、空功能说明、10 种 Git 阶段
    """
    import json as _json
    mock_path = os.path.join(MONITOR_ROOT, "mock_git_states.json")
    if not os.path.exists(mock_path):
        return jsonify({"error": "mock_git_states.json 不存在", "files": [], "summary": {}}), 404
    try:
        with open(mock_path, "r", encoding="utf-8") as f:  # type: ignore[arg-type]
            data = _json.load(f)
        files = data.get("files", [])
        # 构造 summary 与真实 dashboard 结构一致
        summary = {
            "total_files": len(files),
            "branch": "mock-branch",
            "branch_type": "other",
            "refresh_count": data.get("total", 0),
            "cache_age": 0,
            "api_ms": 0,
            "avg_coverage": 0.0,
            "type_dist": [],
            "stage_distribution": data.get("stage_distribution", {}),
            "state_distribution": data.get("state_distribution", {}),
            "is_mock": True,
        }
        logger.info("[MOCK] 返回 %d 条模拟数据 (边界情况测试)", len(files))
        return jsonify({"files": files, "summary": summary})
    except Exception as e:
        logger.error("[MOCK] 加载 mock_git_states.json 失败: %s", e)
        return jsonify({"error": str(e), "files": [], "summary": {}}), 500


# ============================== 入口 ==============================
def main():
    t = threading.Thread(target=background_scanner, daemon=True, name="file-scanner")
    t.start()
    logger.info("文件监控服务启动: root=%s port=%d log=%s", MONITOR_ROOT, PORT, LOG_LEVEL)
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
