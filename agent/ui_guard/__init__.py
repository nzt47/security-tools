"""UI 规范守卫（ui_guard）—— 让「UI 封装规范」变成可执行检查的**模块**。

为什么要有它（2026-11-19）：
    UI 封装规范此前只以「文档 + 3 个 vitest 守卫 + 一个 Python 量具」存在，
    人知道、agent 不一定知道，更没法在对话/工作流里被调用。本模块把同一套
    规则收成一个**只读检查器**（fast 模式纯 Python，生产无 Node 也能跑）：
      GET  /api/ui-guard/check     检查（filter: mode/rule/limit）
      POST /api/ui-guard/fix       两类低风险修复（默认 dry_run）
      GET  /api/ui-guard/health    给「系统监控」拓扑用的状态源

口径（与 docs/ui/封装规范.md、3 个 vitest 守卫逐条对齐）：
    1. 量具（raw_button/raw_select/raw_input/raw_textarea/raw_range/
       arbitrary_text/raw_heading/css_hardcoded_color）—— 直接复用
       scripts/audit/ui_primitive_convergence.py 的 METRICS/正则，不抄第二份；
    2. Button 对齐：满宽/flex-1 必须显式 justify-start|justify-center；
       text-left 必须配 justify-start；
    3. 标题字号：每个 <h1..h6> 开标签内必须出现 text-*；
    4. Tab 顺序：渲染 <Tabs> 的页面，第一个 Tab 之前必须有标题。
"""
from agent.ui_guard.models import Finding, Report  # noqa: F401
from agent.ui_guard.engine import check, health, last_report  # noqa: F401
from agent.ui_guard.fixer import fix  # noqa: F401

# ── 自动发现声明（modules_discovery 只做 AST 扫描，不 import 本包）──────────
# 只有声明了 MODULE_STATUS_SOURCE 的包才会在「系统监控」拓扑里生成节点。
MODULE_STATUS_SOURCE = "api:/api/ui-guard/health"
MODULE_DOMAIN = "ops"
MODULE_NAME = "UI 规范守卫"
MODULE_DESCRIPTION = "检查 UI 封装规范（原语/字号/标题/Tab 顺序），给出 file:line 与修复建议"

__version__ = "1.0.0"
