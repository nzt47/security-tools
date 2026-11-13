# -*- coding: utf-8 -*-
"""覆盖缺口自检（2026-11-12）：自动找出「新增了但没人感知 / 监控 / 告警」的能力。

【为什么需要（Owner 2026-11-12 18:01 指定的新功能）】
§11.95–§11.97 把感知、监控、告警三条线都接上了，但**"新增了什么能力、它有没有被覆盖"
仍然靠人肉对拍**：新包只写了 ``MODULE_NAME`` 没写状态源、声明的单位 probe 拼错、新工具
没进索引、新传感器写了却不符合自发现契约、周期任务的注册函数写好了却没人调用 ——
这些都不会让任何东西变红，只会让「全域监控」慢慢变成一句口号。

本模块把「新功能上线检查清单」（docs/handoff/覆盖矩阵与上线检查清单.md §4）里
**可以机械判定**的那几条做成一条自检：只读、声明式优先、判据可解释（每条 gap 都带
``why`` 与"建议补哪一处"），**绝不自动注册**单位/规则/探针（那会改健康分分母、也属
另一件事，须 Owner 点头）。

【判定口径（一条能力只要有任何一条感知/监控/告警路径覆盖 ⇒ **不算** gap）】

| kind | 能力从哪来（声明面） | 什么算 gap | 建议补哪一处 |
|---|---|---|---|
| ``package`` | ``agent/<pkg>/__init__.py`` 的 ``MODULE_*`` 字面量 | 声明了模块元数据，但**既无**状态源**也无**单位；或状态源路径不在 ``config/route_domains.json`` 的任何前缀里（节点会永远离线） | 补 ``MODULE_STATUS_SOURCE`` / ``MODULE_UNITS`` |
| ``unit`` | ``MODULE_UNIT`` / ``MODULE_UNITS`` | probe 的 ``模块:函数`` 静态解析不到（文件或函数不存在）⇒ 单位永远 error | 在目标模块补 ``def f() -> dict``，或改 probe 字符串 |
| ``sensor`` | ``sensor/*.py`` 里以 ``Sensor`` 结尾的类 | 该模块里**没有一个** ``*Sensor`` 类带 ``collect()`` ⇒ 自发现静默跳过 = 这个感知能力不存在 | 补 ``collect()``，或把非传感器文件挪出 ``sensor/`` |
| ``scheduler`` | ``agent/**`` 的 ``def register_*scheduler`` + 调度器活体任务 | 注册函数**没有任何调用点**（写好了不会跑）；或任务被停用（能力当前不运行） | 在 app_server 启动注册块调用；或明确记录"有意停用" |
| ``route`` | ``config/route_domains.json`` 的前缀 | 前缀在活体路由报告里**一条都命中不到** ⇒ 表陈旧/前缀写错 | 改前缀或干净 worktree 重派生报告 |
| ``tool`` | ``data/tool_index.json`` 的条目 | 索引里有、``data/tool_definitions`` 下没有同名 YAML ⇒ 路由会选到"调不通的工具" | 补回 YAML 或删条目后重派生索引 |

【明确不做的四件事（都写在这里，避免下一个人重复建设）】
1. **不自动注册**单位 / 规则 / 探针：会改健康分分母，而且"该不该监控"是 Owner 的决定。
2. **不重复报「规则算不了」**：那 10 条 blocked 已在 ``/api/alarms/coverage.rules.blocked``
   逐条披露（缺哪个指标、为什么缺），再算一遍只会变成第二张报表。
3. **不报「工具 YAML 有、索引里没有」**：``scripts/sync_tool_index.py --check`` 在 CI 守门；
   本机未跟踪的运行时 YAML（local-env-noise）会让它每轮误报。
4. **不报「路由没标注该不该进 UI」**：那是 ``route_ui_coverage`` / ``route_unreferenced_triage``
   的守卫（ceiling 恒 0），在干净 worktree 重派生才判绿。

【零副作用纪律（与 unit_probes / modules_discovery 同族）】
- 全部**只读**：不写盘（除 ``refresh`` 落状态文件）、不建目录、不发网络、不建单例；
- 包/传感器/工具面一律 **AST 或文本扫描 + 文件存在性**，不 import 被检查的包；
- 调度器活体任务只在**已经初始化**时读（``is_initialized`` 模式，§11.93/§11.95 两次实证：
  读路径建单例会把失败基线闸打红）；
- 单条检查失败 ⇒ 该输入面记 ``ok:false`` 并如实披露（``degraded``），**不**让整体看起来
  "0 缺口"（L5：没测到 ≠ 没问题）。

【外发与去重】
``refresh()`` 每小时跑一次；**同一缺口只在首次出现时**经 ``agent/alarms/notify.dispatch_gaps``
推一次（warning 级；不受 ``CP_ALARM_NOTIFY_WARNING`` 过滤 —— 它是一次性提示，不是每轮都响的
告警）。指纹 = ``kind:id``；推送成功才记入 ``pushed``（没通道就下一轮重试，配好通道当轮就能收到）；
缺口消失后指纹被修剪，**再次出现会再推一次**。状态落 ``data/monitoring/coverage_gaps.json``
（运行期状态，gitignored）。

开关（均已进 ``agent/settings/registry.py``）：
    CP_COVERAGE_GAP_ENABLED           自检总开关（默认 true）
    CP_COVERAGE_GAP_INTERVAL_SECONDS  周期（默认 3600，最小 300）
"""
from __future__ import annotations

import ast
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

__version__ = "1.0.0"

#: 仓库根（本文件位于 <repo>/agent/monitoring/ 下）
_ROOT: Path = Path(__file__).resolve().parents[2]

ENV_ENABLED = "CP_COVERAGE_GAP_ENABLED"
ENV_INTERVAL_SECONDS = "CP_COVERAGE_GAP_INTERVAL_SECONDS"

#: 调度任务名（幂等注册按名字定位）
TASK_NAME = "覆盖缺口自检"

#: 默认周期（秒）与下限：一小时一次足够（能力集合不会分钟级变化），且别和 60s 采集抢节拍
DEFAULT_INTERVAL_SECONDS = 3600
MIN_INTERVAL_SECONDS = 300

#: 状态文件（运行期状态；放 data/monitoring/ 下，已 gitignore）
STATE_REL_PATH = "data/monitoring/coverage_gaps.json"

#: 单次最多列出多少条（防止一个坏输入面把响应体撑爆；超了如实标 truncated）
MAX_GAPS = 200

#: 每轮最多推几条外发（通道可能限流；未推的下一轮继续，指纹只在推送成功后才记）
MAX_PUSH_PER_ROUND = 5

#: 多久没跑算「自检停摆」（周期 × 该倍数；单位看板据此 warn）
STALE_ROUNDS = 3

#: 单位看板 issue 里最多列几条缺口（其余用"还有 N 条"收口，避免 issue 列表刷屏）
ISSUE_LIST_LIMIT = 5

#: 缺口种类（与 docstring 的表一一对应；前端/测试按此枚举）
KIND_PACKAGE = "package"
KIND_UNIT = "unit"
KIND_SENSOR = "sensor"
KIND_SCHEDULER = "scheduler"
KIND_ROUTE = "route"
KIND_TOOL = "tool"

KINDS: Tuple[str, ...] = (
    KIND_PACKAGE, KIND_UNIT, KIND_SENSOR, KIND_SCHEDULER, KIND_ROUTE, KIND_TOOL,
)

#: 传感器自发现契约（sensor/registry.py）：类名以 Sensor 结尾 + 有 collect()
SENSOR_CLASS_SUFFIX = "Sensor"
SENSOR_COLLECT_METHOD = "collect"

#: 传感器**声明式口径**（2026-11-12）：模块级 SENSOR_CONTRACT 常量
#:   periodic（缺省）= 必须 collect()，否则自发现跳过 = 缺口；
#:   on_demand      = 感知由显式调用方驱动（如屏幕 OCR），**不要求** collect()。
#: 为什么需要它：给 OcrSensor 补一个 collect() 会让自发现每 60s 截一次屏（隐私/开销），
#: 但"看着像传感器却没 collect()"确实需要有人给出一句解释 —— 让模块自己声明，比豁免清单好。
SENSOR_CONTRACT_KEY = "SENSOR_CONTRACT"
SENSOR_CONTRACT_PERIODIC = "periodic"
SENSOR_CONTRACT_ON_DEMAND = "on_demand"
SENSOR_CONTRACTS: Tuple[str, ...] = (SENSOR_CONTRACT_PERIODIC, SENSOR_CONTRACT_ON_DEMAND)

#: 扫描时跳过的文件（聚合器本身不是"一种感觉"；测试文件不参与生产注册）
SENSOR_SKIP_FILES = frozenset({"body_sensor.py", "__init__.py"})
_TEST_FILE_PREFIX = "test_"

#: 周期注册函数的命名约定（agent/** 里 def register_*scheduler 即"声明了一个周期能力"）
PERIODIC_FUNC_PREFIX = "register_"
PERIODIC_FUNC_KEYWORD = "scheduler"

#: 单位 probe 的"模块:函数"分隔符
PROBE_SEPARATOR = ":"

_TRUTHY = frozenset({"1", "true", "yes", "on", "enable", "enabled"})

#: 输入面清单（顺序即 surfaces 的键顺序；前端/文档引用同一份）
SURFACES: Tuple[str, ...] = (
    "packages", "units", "sensors", "scheduler", "routes", "tools", "rules", "event_sources",
)

#: 未参与判定的输入面（如实披露原因，别让读者以为"没列就是没问题"）
EXCLUDED: Tuple[Dict[str, str], ...] = (
    {"surface": "rules.blocked",
     "reason": "规则算不了的逐条原因已在 /api/alarms/coverage.rules.blocked 披露，"
               "这里不重复统计（同一条缺口两处报警 = 噪音）"},
    {"surface": "tools.yaml_to_index",
     "reason": "工具 YAML 有、索引没有：scripts/sync_tool_index.py --check 在 CI 守门；"
               "本机未跟踪的运行时 YAML 会每轮误报（local-env-noise）"},
    {"surface": "routes.ui_intent",
     "reason": "路由该不该进 UI 由 route_ui_coverage / route_unreferenced_triage 守卫"
               "（ceiling 恒 0，须在干净 worktree 重派生）"},
)


# ════════════════════════════════════════════════════════════
#  开关
# ════════════════════════════════════════════════════════════

def enabled() -> bool:
    """``CP_COVERAGE_GAP_ENABLED``（默认开；关掉后周期任务不再注册）。"""
    raw = (os.environ.get(ENV_ENABLED) or "").strip().lower()
    if not raw:
        return True
    return raw in _TRUTHY


def interval_seconds() -> int:
    """周期（秒）：非数字/太小都回落到默认或下限。"""
    raw = (os.environ.get(ENV_INTERVAL_SECONDS) or "").strip()
    try:
        value = int(float(raw))
    except (TypeError, ValueError):
        return DEFAULT_INTERVAL_SECONDS
    return max(MIN_INTERVAL_SECONDS, value)


def _iso(ts: Optional[float] = None) -> str:
    moment = datetime.fromtimestamp(ts if ts is not None else time.time()).astimezone()
    return moment.isoformat(timespec="seconds")


def _parse_iso(text: object) -> Optional[float]:
    raw = str(text or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw).timestamp()
    except ValueError:
        return None


# ════════════════════════════════════════════════════════════
#  缺口构造 / 指纹
# ════════════════════════════════════════════════════════════

def make_gap(kind: str, gap_id: str, why: str, fix: str) -> Dict[str, str]:
    """一条缺口的**唯一形状**（kind/id/why/fix 四段；前端与测试都按这个读）。"""
    return {"kind": str(kind), "id": str(gap_id), "why": str(why), "fix": str(fix)}


def fingerprint(gap: Dict[str, Any]) -> str:
    """去重指纹 = ``kind:id``（why 变化不重推：同一处缺口修法变了不该再推一次）。"""
    return "%s:%s" % (gap.get("kind") or "", gap.get("id") or "")


# ════════════════════════════════════════════════════════════
#  通用只读工具
# ════════════════════════════════════════════════════════════

def _read_text(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _read_json(path: Path) -> Tuple[Optional[Any], str]:
    """读 JSON：返回 (内容, 失败原因)。失败原因只写短标记（不把原文带进响应/日志）。"""
    try:
        return json.loads(path.read_text(encoding="utf-8")), ""
    except FileNotFoundError:
        return None, "missing"
    except Exception as exc:  # noqa: BLE001 坏文件按"不可用"处理，不抛给采集线程
        logger.warning("[CoverageGaps] %s 解析失败（%s）", path.name, type(exc).__name__)
        return None, "invalid"


def _iter_py_files(base: Path) -> List[Path]:
    """递归列出 .py（跳过 __pycache__ 与隐藏目录；只读，不改任何东西）。"""
    out: List[Path] = []
    if not base.is_dir():
        return out
    for path in base.rglob("*.py"):
        parts = path.parts
        if "__pycache__" in parts or any(p.startswith(".") for p in parts):
            continue
        out.append(path)
    return out


def _module_file(root: Path, dotted: str) -> Optional[Path]:
    """把 ``agent.a.b`` 映射成文件（模块或包的 __init__）；不存在返回 None（**不 import**）。"""
    rel = Path(*str(dotted).split("."))
    for candidate in (root / rel.with_suffix(".py"), root / rel / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


def _defines_top_level(path: Path, name: str) -> Optional[bool]:
    """该文件顶层是否定义了 ``name``（函数或类）；解析失败返回 None（无法判定）。"""
    source = _read_text(path)
    if source is None:
        return None
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == name:
            return True
    return False


def _module_str_constant(tree: ast.Module, name: str) -> str:
    """取模块顶层某个字符串字面量常量（取不到/不是字面量 ⇒ 空串；**不 import**）。"""
    for node in tree.body:
        target: Optional[ast.AST] = None
        value: Optional[ast.AST] = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            target, value = node.target, node.value
        if target is None or value is None:
            continue
        if isinstance(target, ast.Name) and target.id == name \
                and isinstance(value, ast.Constant) and isinstance(value.value, str):
            return value.value.strip()
    return ""


def _prefix_matches(prefix: str, path: str) -> bool:
    """前缀匹配：普通前缀按**段边界**（``/api/skills`` 不该匹配 ``/api/skills-mgmt``），
    含 ``*`` 的前缀按通配（``config/route_domains.json`` 的 ``/*`` = 页面入口，匹配一切）。

    【为什么要认通配】功能区表里确有一条 ``/*``（页面入口）；把它当字面前缀会得到一条
    "前缀命中不到任何路由"的假缺口 —— 判据必须与被检查的表同语义。
    """
    pre = str(prefix or "").strip()
    target = str(path or "")
    if not pre:
        return False
    if "*" in pre:
        import fnmatch  # 只在本函数用：通配前缀是少数

        return fnmatch.fnmatchcase(target, pre) or fnmatch.fnmatchcase(target, pre.rstrip("/"))
    if pre in ("/", ""):
        return True
    pre = pre.rstrip("/")
    return target == pre or target.startswith(pre + "/")


# ════════════════════════════════════════════════════════════
#  各输入面检查（每个返回 (gaps, surface_info)；**绝不抛**给调用方）
# ════════════════════════════════════════════════════════════

def check_packages(root: Path) -> Tuple[List[Dict[str, str]], Dict[str, Any]]:
    """包声明面：``MODULE_*`` 声明了能力，却没有任何监控路径。

    【判据】``unregistered``（写了名字/描述但没写状态源）且**也没有** ``MODULE_UNITS``
    ⇒ 拓扑不上图、单位看板不探测 = 真缺口。同时校验状态源路径是否落在路由表前缀内
    （写错路径的节点会永远显示"离线"，而那不是"没数据"，是"地址错了"）。
    """
    surfaces: Dict[str, Any] = {"ok": False, "available": False}
    gaps: List[Dict[str, str]] = []
    try:
        from agent import modules_discovery  # 延迟 import：仅自检路径需要

        result = modules_discovery.scan(root / "agent", use_cache=False)
    except Exception as exc:  # noqa: BLE001 扫描失败如实披露，不伪造 0 缺口
        logger.warning("[CoverageGaps] 包声明扫描失败（%s）", type(exc).__name__)
        surfaces["reason"] = "scan_failed"
        return gaps, surfaces

    unit_packages = {u.package for u in result.units}
    prefixes = _route_prefixes(root)
    surfaces.update({
        "ok": True,
        "available": True,
        "scanned": result.scanned_packages,
        "declared": len(result.declared),
        "unregistered": len(result.unregistered),
        "units": len(result.units),
        "invalid_units": len(result.invalid_units),
    })
    for row in result.unregistered:
        package = str(row.get("package") or "")
        name = str(row.get("name") or package)
        bare = package.rstrip("/").rsplit("/", 1)[-1]
        if bare in unit_packages:
            continue  # 只声明了单位 = 有监控路径（单位看板），不算缺口
        gaps.append(make_gap(
            KIND_PACKAGE, package or name,
            "声明了模块元数据但没有状态源、也没有单位声明（%s）⇒ 拓扑不上图、看板不探测"
            % str(row.get("reason") or "未声明 MODULE_STATUS_SOURCE"),
            "在 agent/%s/__init__.py 补 MODULE_STATUS_SOURCE（api:/api/... 或 config:...），"
            "或补 MODULE_UNITS 声明一个只读 probe" % (bare or "<pkg>"),
        ))
    for module in result.declared:
        source = str(module.status_source or "")
        if not source.startswith("api:"):
            continue  # config: 一类不经过 HTTP 路由表，跳过路径校验
        api_path = source[len("api:"):]
        if not prefixes or any(_prefix_matches(p, api_path) for p in prefixes):
            continue
        gaps.append(make_gap(
            KIND_PACKAGE, module.display_path,
            "状态源 %s 不在 config/route_domains.json 的任何前缀里 ⇒ 该拓扑节点会一直显示离线"
            % api_path,
            "确认路由真实路径（或该路由未注册），再把 MODULE_STATUS_SOURCE 改成已登记前缀",
        ))
    for bad in result.invalid_units:
        gaps.append(make_gap(
            KIND_UNIT, str(bad.get("package") or ""),
            "单位声明被忽略：%s" % str(bad.get("reason") or "格式不合法"),
            '按 {"id", "label", "family", "probe": "模块路径:函数名"} 重写该条声明',
        ))
    return gaps, surfaces


def check_units(root: Path) -> Tuple[List[Dict[str, str]], Dict[str, Any]]:
    """单位声明面：probe 的 ``模块:函数`` 静态解析不到 ⇒ 单位永远 error。

    【为什么静态查、不 import】import 会执行包初始化（启动期副作用 + 循环依赖风险），
    而这里要回答的只是"那个函数存在吗" —— AST 足够，且**零副作用**。
    """
    gaps: List[Dict[str, str]] = []
    surfaces: Dict[str, Any] = {"ok": False, "available": False}
    try:
        from agent import modules_discovery

        result = modules_discovery.scan(root / "agent", use_cache=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[CoverageGaps] 单位声明扫描失败（%s）", type(exc).__name__)
        surfaces["reason"] = "scan_failed"
        return gaps, surfaces

    resolved = 0
    for unit in result.units:
        target = str(unit.probe or "")
        module_name, _, attr = target.partition(PROBE_SEPARATOR)
        module_path = _module_file(root, module_name.strip())
        if module_path is None:
            gaps.append(make_gap(
                KIND_UNIT, unit.unit_id,
                "probe 的模块 %s 在仓库里找不到 ⇒ 单位每次探测都会 error" % module_name.strip(),
                "改 probe 指向真实模块，或补上缺失的模块文件",
            ))
            continue
        verdict = _defines_top_level(module_path, attr.strip())
        if verdict is None:
            continue  # 解析失败 = 无法判定（不猜）
        if not verdict:
            gaps.append(make_gap(
                KIND_UNIT, unit.unit_id,
                "probe 的函数 %s 不在 %s 顶层 ⇒ 单位每次探测都会 error"
                % (attr.strip(), module_name.strip()),
                "在该模块补 def %s() -> dict（返回单位形状），或改 probe 字符串"
                % (attr.strip() or "<fn>"),
            ))
            continue
        resolved += 1
    surfaces.update({"ok": True, "available": True,
                     "declared": len(result.units), "probes_resolved": resolved})
    return gaps, surfaces


def check_sensors(root: Path) -> Tuple[List[Dict[str, str]], Dict[str, Any]]:
    """传感器面：写了 ``*Sensor`` 类但没有 ``collect()`` ⇒ 自发现静默跳过。

    【现场依据】``sensor/registry.py`` 的发现规则是"类名以 Sensor 结尾 + 有 collect()"；
    不满足就**只是不出现**（没有 warning、没有红点）。实测 ``sensor/ocr_sensor.py`` 的
    ``OcrSensor`` 只有 ``capture_screen``/``recognize``，因此 OCR 这一路感知在本部署里
    从未进入注册表 —— 这正是"看起来有、其实没有"的典型缺口。
    """
    gaps: List[Dict[str, str]] = []
    surfaces: Dict[str, Any] = {"ok": True, "available": False}
    sensor_dir = root / "sensor"
    try:
        files = sorted(p for p in sensor_dir.glob("*.py")
                       if p.name not in SENSOR_SKIP_FILES and not p.name.startswith(_TEST_FILE_PREFIX))
    except OSError:
        files = []
    if not files:
        surfaces["reason"] = "missing"
        return gaps, surfaces
    found = 0
    exempt = 0
    for path in files:
        source = _read_text(path)
        if source is None:
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            gaps.append(make_gap(
                KIND_SENSOR, "sensor/" + path.name,
                "该模块有语法错误 ⇒ 自发现 import 时直接失败，这一路感知不存在",
                "修语法；或确认这个文件不该留在 sensor/（自发现目录）里",
            ))
            continue
        classes = [node for node in tree.body
                   if isinstance(node, ast.ClassDef) and node.name.endswith(SENSOR_CLASS_SUFFIX)]
        if not classes:
            continue  # 不是传感器文件（TTS 引擎之类），不归本检查管
        found += len(classes)
        with_collect = []
        for node in classes:
            methods = {child.name for child in node.body
                       if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))}
            if SENSOR_COLLECT_METHOD in methods:
                with_collect.append(node.name)
        if with_collect:
            continue
        names = "、".join(node.name for node in classes)
        contract = _module_str_constant(tree, SENSOR_CONTRACT_KEY)
        if contract == SENSOR_CONTRACT_ON_DEMAND:
            exempt += 1  # 模块自己声明"按需驱动"，不是漏了 collect()（见常量注释）
            continue
        if contract and contract not in SENSOR_CONTRACTS:
            gaps.append(make_gap(
                KIND_SENSOR, "sensor/" + path.name,
                "SENSOR_CONTRACT=%s 不是已知取值（%s）⇒ 无法判定它是周期传感器还是按需引擎"
                % (contract, " / ".join(SENSOR_CONTRACTS)),
                "把该常量改成 on_demand（有显式调用方、不需要周期采集）或删掉它并补 collect()",
            ))
            continue
        gaps.append(make_gap(
            KIND_SENSOR, "sensor/" + path.name,
            "定义了 %s 但没有 collect() ⇒ SensorRegistry 自发现会跳过它，该感知能力等于不存在"
            % names,
            "给 %s 补 collect()（返回 SensorReading），或声明 SENSOR_CONTRACT=\"on_demand\""
            "（按需驱动），或把非传感器文件挪出 sensor/" % names,
        ))
    surfaces.update({"ok": True, "available": True, "files": len(files),
                     "sensor_classes": found, "on_demand": exempt})
    return gaps, surfaces


def check_scheduler(root: Path) -> Tuple[List[Dict[str, str]], Dict[str, Any]]:
    """调度面：注册函数没人调用（写好了不会跑）+ 已停用的任务（能力当前不运行）。

    【为什么值得查】2026-11-12 之前 ``perception_cycle`` 的周期任务就属于"代码在、没人接线"
    这一类：文件监听配了 watch_dirs 却永远没有调用方（§11.95）。静态检查调用点能在
    "上线忘了注册"的当天就说话，而不是靠人发现"读数怎么一直不变"。
    """
    gaps: List[Dict[str, str]] = []
    surfaces: Dict[str, Any] = {"ok": True, "available": False}

    # ① 静态：def register_*scheduler 的定义点 vs 调用点
    definitions: Dict[str, str] = {}
    for path in _iter_py_files(root / "agent"):
        source = _read_text(path)
        if source is None or PERIODIC_FUNC_PREFIX not in source:
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                    and node.name.startswith(PERIODIC_FUNC_PREFIX) \
                    and PERIODIC_FUNC_KEYWORD in node.name:
                definitions.setdefault(node.name, str(path))
    callers: Dict[str, int] = {}
    candidates = _iter_py_files(root / "agent")
    app_server = root / "app_server.py"
    if app_server.is_file():
        candidates.append(app_server)
    for path in candidates:
        source = _read_text(path)
        if source is None:
            continue
        for name in definitions:
            if name not in source:
                continue
            if str(path) == definitions[name] and source.count(name + "(") <= 1:
                continue  # 只有定义本身的文件不算接线
            callers[name] = callers.get(name, 0) + 1
    for name, defined_in in sorted(definitions.items()):
        if callers.get(name):
            continue
        gaps.append(make_gap(
            KIND_SCHEDULER, "%s:%s" % (_display_path(root, defined_in), name),
            "周期注册函数没有任何调用点 ⇒ 它声明的周期能力永远不会被调度",
            "在 app_server.py 的启动注册块里调用它（参考 register_ci_sync_scheduler 的写法）",
        ))
    surfaces.update({"available": True, "registration_functions": len(definitions),
                     "wired": sum(1 for n in definitions if callers.get(n)),
                     "live_available": False})

    # ② 活体：调度器已初始化时才读（**不建单例**）
    try:
        from agent.utils.singleton_manager import get_singleton, is_initialized

        if is_initialized("task_scheduler"):
            scheduler = get_singleton("task_scheduler")
            tasks = scheduler.list_tasks() if scheduler is not None else []
            disabled = [t for t in tasks if not t.get("enabled", True)]
            surfaces["live_available"] = True
            surfaces["live_tasks"] = len(tasks)
            surfaces["live_disabled"] = len(disabled)
            for task in disabled[:MAX_GAPS]:
                gaps.append(make_gap(
                    KIND_SCHEDULER, "task:" + str(task.get("name") or task.get("task_id") or "?"),
                    "调度任务已停用 ⇒ 对应周期能力当前不运行（停用可能是故意的，但没有记录）",
                    "确认是否有意停用：有意则记进覆盖矩阵「缺口」列，否则在调度器里启用",
                ))
    except Exception as exc:  # noqa: BLE001 读不到活体任务 ⇒ 如实标不可用（不猜）
        logger.warning("[CoverageGaps] 调度器活体任务读取失败（%s）", type(exc).__name__)
    return gaps, surfaces


def _display_path(root: Path, path: str) -> str:
    """仓库内路径显示成相对路径（缺口 id 要能跨机器稳定、能直接贴进 issue）。"""
    try:
        return str(Path(path).resolve().relative_to(Path(root).resolve())).replace("\\", "/")
    except (ValueError, OSError):
        return str(path).replace("\\", "/")


def check_routes(root: Path) -> Tuple[List[Dict[str, str]], Dict[str, Any]]:
    """路由面：``config/route_domains.json`` 声明的前缀在活体路由报告里一条都没命中。

    【边界】路由**该不该进 UI** 由 route_ui_coverage / triage 守卫，这里只看"功能区表
    有没有指向不存在的路径"（表陈旧或前缀写错都会让覆盖率对拍失去意义）。
    """
    gaps: List[Dict[str, str]] = []
    surfaces: Dict[str, Any] = {"ok": True, "available": False}
    doc, reason = _read_json(root / "config" / "route_domains.json")
    if not isinstance(doc, dict) or not isinstance(doc.get("domains"), list):
        surfaces["reason"] = reason or "invalid"
        return gaps, surfaces
    report, report_reason = _read_json(root / "reports" / "route_ui_coverage.json")
    entries = report.get("entries") if isinstance(report, dict) else None
    if not isinstance(entries, list):
        surfaces["reason"] = report_reason or "missing_report"
        return gaps, surfaces
    paths = [str(e.get("path") or "") for e in entries if isinstance(e, dict)]
    prefixes = [str(p) for d in doc.get("domains") or [] for p in (d.get("prefixes") or [])]
    surfaces.update({"ok": True, "available": True,
                     "report_routes": len(paths), "declared_prefixes": len(prefixes)})
    for prefix in prefixes:
        if any(_prefix_matches(prefix, path) for path in paths):
            continue
        gaps.append(make_gap(
            KIND_ROUTE, prefix,
            "功能区声明了这个前缀，但覆盖率报告里没有任何活体路由命中 ⇒ 表陈旧或前缀写错",
            "改 config/route_domains.json 的前缀；若路由真的删了，在干净 worktree 重派生 "
            "reports/route_ui_coverage.json",
        ))
    return gaps, surfaces


def check_tools(root: Path) -> Tuple[List[Dict[str, str]], Dict[str, Any]]:
    """工具面：索引里的工具在 ``data/tool_definitions`` 下没有同名 YAML。

    【为什么只查这一个方向】反方向（YAML 有、索引没有）由 ``scripts/sync_tool_index.py``
    在 CI 守门，且本机未跟踪的运行时 YAML 会让它每轮误报（local-env-noise，见 EXCLUDED）。
    """
    gaps: List[Dict[str, str]] = []
    surfaces: Dict[str, Any] = {"ok": True, "available": False}
    doc, reason = _read_json(root / "data" / "tool_index.json")
    tools = doc.get("tools") if isinstance(doc, dict) else None
    if not isinstance(tools, list):
        surfaces["reason"] = reason or "invalid"
        return gaps, surfaces
    names = {str(t.get("name") or "") for t in tools if isinstance(t, dict)}
    yaml_dir = root / "data" / "tool_definitions"
    try:
        existing = {p.stem for p in yaml_dir.glob("*.yaml")}
    except OSError:
        existing = set()
    surfaces.update({"ok": True, "available": True,
                     "index": len(names), "yaml": len(existing)})
    for name in sorted(n for n in names if n and n not in existing):
        gaps.append(make_gap(
            KIND_TOOL, name,
            "索引里有这个工具，但 data/tool_definitions 下没有同名 YAML ⇒ 路由会选到调不通的工具",
            "补回该工具的 YAML（含 description/schema），或从索引删除后重派生 tool_index.json",
        ))
    return gaps, surfaces


def check_rules_surface(root: Path) -> Tuple[List[Dict[str, str]], Dict[str, Any]]:
    """规则面（**只报可读性，不重复报 blocked**）：规则文件读得到吗、有多少条。

    【为什么不把 blocked 算成 gap】那 10 条"算不了"的原因（缺哪个指标）已经在
    ``/api/alarms/coverage.rules.blocked`` 逐条披露；同一件事在两个地方各报一次，
    单位看板会一直 warn，最后被静音 ⇒ 等于没有（§11.91 的教训）。
    """
    surfaces: Dict[str, Any] = {"ok": True, "available": False}
    try:
        from agent.alarms import rules as rule_engine

        loaded = rule_engine.load_rules(root / "monitoring" / "alerts.yml")
        evaluated = rule_engine.snapshot()
    except Exception as exc:  # noqa: BLE001 规则读不到不影响其它面
        logger.warning("[CoverageGaps] 规则面读取失败（%s）", type(exc).__name__)
        surfaces["ok"] = False
        surfaces["reason"] = "read_failed"
        return [], surfaces
    surfaces.update({"available": bool(loaded), "total": len(loaded),
                     "evaluated": len(evaluated)})
    return [], surfaces


def check_event_sources() -> Tuple[List[Dict[str, str]], Dict[str, Any]]:
    """告警事件源面（只读上一轮采集统计）：本轮到底读到了几个源。

    【为什么这是"输入面"而不是 gap】某个源本轮不可用有它自己的 keep 语义（既不报警也不判
    恢复），由 ``/api/alarms/coverage.last_collect`` 负责披露；这里只是让缺口报告能回答
    "告警面自己还是活的吗"。
    """
    surfaces: Dict[str, Any] = {"ok": True, "available": False}
    try:
        from agent.alarms.collector import last_stats

        stats = last_stats() or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("[CoverageGaps] 事件源读数失败（%s）", type(exc).__name__)
        surfaces["ok"] = False
        return [], surfaces
    checked = stats.get("checked") if isinstance(stats, dict) else None
    if not isinstance(checked, dict) or not checked:
        return [], surfaces
    surfaces.update({"available": True, "checked": dict(checked),
                     "read": sum(1 for v in checked.values() if v), "total": len(checked)})
    return [], surfaces


#: 检查函数表（顺序 = 报告里 gap 的稳定顺序；每个都只读、单个失败不影响其它）
CHECKS: Tuple[Tuple[str, Any], ...] = (
    ("packages", check_packages),
    ("units", check_units),
    ("sensors", check_sensors),
    ("scheduler", check_scheduler),
    ("routes", check_routes),
    ("tools", check_tools),
)


def _route_prefixes(root: Path) -> List[str]:
    """``config/route_domains.json`` 里声明的全部前缀（读不到 ⇒ 空 = 跳过路径校验）。"""
    doc, _ = _read_json(root / "config" / "route_domains.json")
    if not isinstance(doc, dict):
        return []
    return [str(p) for d in doc.get("domains") or [] for p in (d.get("prefixes") or [])]


# ════════════════════════════════════════════════════════════
#  一轮自检
# ════════════════════════════════════════════════════════════

def collect(*, root: Optional[Path] = None, now: Optional[float] = None) -> Dict[str, Any]:
    """跑一遍全部检查（**只读**；返回 count / by_kind / gaps / surfaces / degraded）。"""
    base = Path(root) if root is not None else _ROOT
    gaps: List[Dict[str, str]] = []
    surfaces: Dict[str, Any] = {}
    degraded = False
    for name, check in CHECKS:
        try:
            rows, info = check(base)
        except Exception as exc:  # noqa: BLE001 单条检查失败不拖垮整轮
            logger.warning("[CoverageGaps] 检查 %s 失败（%s）", name, type(exc).__name__)
            rows, info = [], {"ok": False, "available": False, "reason": "check_failed"}
        if not info.get("ok", True):
            degraded = True
        surfaces[name] = info
        gaps.extend(rows)
    for name, check in (("rules", check_rules_surface), ("event_sources", check_event_sources)):
        try:
            rows, info = check(base) if name == "rules" else check()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[CoverageGaps] 检查 %s 失败（%s）", name, type(exc).__name__)
            rows, info = [], {"ok": False, "available": False, "reason": "check_failed"}
        if not info.get("ok", True):
            degraded = True
        surfaces[name] = info
        gaps.extend(rows)

    gaps.sort(key=lambda g: (g.get("kind") or "", g.get("id") or ""))
    by_kind: Dict[str, int] = {}
    for gap in gaps:
        kind = str(gap.get("kind") or "")
        by_kind[kind] = by_kind.get(kind, 0) + 1
    return {
        "generated_at": _iso(now),
        "count": len(gaps),
        "by_kind": by_kind,
        "gaps": gaps[:MAX_GAPS],
        "truncated": len(gaps) > MAX_GAPS,
        "degraded": degraded,
        "excluded": [dict(item) for item in EXCLUDED],
        "surfaces": surfaces,
    }


# ════════════════════════════════════════════════════════════
#  状态文件（refresh 写，summary / unit_probe 只读）
# ════════════════════════════════════════════════════════════

def state_path(path: Optional[Path] = None) -> Path:
    return Path(path) if path is not None else _ROOT / STATE_REL_PATH


def load_state(path: Optional[Path] = None) -> Dict[str, Any]:
    """读上一次自检结果（缺失/坏文件 ⇒ 空 dict，由调用方报 available=false）。"""
    doc, _ = _read_json(state_path(path))
    return doc if isinstance(doc, dict) else {}


def _write_state(payload: Dict[str, Any], path: Optional[Path] = None) -> bool:
    """原子写状态（失败只记日志：自检是旁路，不该拖垮调度线程）。"""
    target = state_path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(str(tmp), str(target))
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("[CoverageGaps] 状态写入失败（%s）", type(exc).__name__)
        return False


def refresh(*, root: Optional[Path] = None, now: Optional[float] = None,
            push: bool = True, state: Optional[Path] = None,
            dispatcher: Any = None) -> Dict[str, Any]:
    """跑一轮并把结果落盘；**首次出现**的缺口经出站通道推一次（warning）。

    Returns:
        collect() 的结果 + ``push`` 段（attempted / pushed / failed / deferred）
    """
    payload = collect(root=root, now=now)
    previous = load_state(state)
    previously_pushed = {str(x) for x in (previous.get("pushed") or []) if x}
    current = {fingerprint(g) for g in payload["gaps"]}
    new_gaps = [g for g in payload["gaps"] if fingerprint(g) not in previously_pushed]

    attempted = new_gaps[:MAX_PUSH_PER_ROUND]
    result: Dict[str, Any] = {"attempted": len(attempted), "pushed": [], "failed": [],
                              "deferred": max(0, len(new_gaps) - len(attempted))}
    if push and attempted:
        try:
            from agent.alarms import notify as alarm_notify

            outcome = alarm_notify.dispatch_gaps(attempted, dispatcher=dispatcher)
            result["pushed"] = [str(x) for x in outcome.get("sent") or []]
            result["failed"] = [str(x) for x in outcome.get("failed") or []]
            result["reason"] = str(outcome.get("reason") or "")
        except Exception as exc:  # noqa: BLE001 外发是旁路（notify 自己也不抛）
            logger.warning("[CoverageGaps] 缺口外发失败（%s）", type(exc).__name__)
            result["failed"] = [fingerprint(g) for g in attempted]

    # 指纹修剪：缺口消失就忘掉它（再次出现要能再推一次）
    kept = {fp for fp in previously_pushed if fp in current}
    kept.update(result["pushed"])
    payload["pushed"] = sorted(kept)
    payload["push"] = result
    payload["last_push"] = {"at": _iso(now), "pushed": len(result["pushed"]),
                            "failed": len(result["failed"]), "deferred": result["deferred"]}
    _write_state(payload, state)
    logger.info("[CoverageGaps] 自检完成：未覆盖 %d 条（package %d / unit %d / sensor %d / "
                "scheduler %d / route %d / tool %d）；本轮新推 %d 条",
                payload["count"], payload["by_kind"].get(KIND_PACKAGE, 0),
                payload["by_kind"].get(KIND_UNIT, 0), payload["by_kind"].get(KIND_SENSOR, 0),
                payload["by_kind"].get(KIND_SCHEDULER, 0), payload["by_kind"].get(KIND_ROUTE, 0),
                payload["by_kind"].get(KIND_TOOL, 0), len(result["pushed"]))
    return payload


def summary(*, state: Optional[Path] = None, now: Optional[float] = None) -> Dict[str, Any]:
    """只读读数（``/api/alarms/coverage.gaps`` 与单位探针共用；**不写盘、不外发**）。"""
    payload = load_state(state)
    interval = interval_seconds()
    if not payload:
        return {
            "available": False, "count": None, "by_kind": {}, "gaps": [],
            "interval_seconds": interval,
            "detail": "自检还没跑过（%s 不存在）：等周期任务首轮，或手动 POST /api/alarms/collect"
                      % STATE_REL_PATH,
        }
    generated_ts = _parse_iso(payload.get("generated_at"))
    age = None if generated_ts is None else max(
        0.0, float(now if now is not None else time.time()) - generated_ts)
    stale = age is not None and age > interval * STALE_ROUNDS
    by_kind = payload.get("by_kind") if isinstance(payload.get("by_kind"), dict) else {}
    count = payload.get("count")
    if isinstance(count, int):
        detail = "未覆盖 %d 条（%s）" % (
            count, " / ".join("%s %s" % (k, v) for k, v in sorted(by_kind.items())) or "无")
    else:
        detail = "状态文件里没有可用的 count ⇒ 视为未完成一轮自检"
        count = None
    if payload.get("degraded"):
        detail += "；⚠ 有输入面读不到（本轮结果不完整）"
    if count:
        detail += "；最近自检 %s" % ("刚刚" if not age else "%.0f 分钟前" % (age / 60.0))
    return {
        "available": count is not None,
        "count": count,
        "by_kind": dict(by_kind),
        "gaps": list(payload.get("gaps") or [])[:MAX_GAPS],
        "truncated": bool(payload.get("truncated")),
        "degraded": bool(payload.get("degraded")),
        "generated_at": payload.get("generated_at"),
        "age_seconds": None if age is None else round(age, 1),
        "stale": stale,
        "interval_seconds": interval,
        "pushed": len(payload.get("pushed") or []),
        "last_push": payload.get("last_push"),
        "excluded": list(payload.get("excluded") or []),
        "surfaces": payload.get("surfaces") or {},
        "detail": detail,
    }


# ════════════════════════════════════════════════════════════
#  单位看板探针 / 周期任务
# ════════════════════════════════════════════════════════════

def unit_probe(*, state: Optional[Path] = None, now: Optional[float] = None) -> Dict[str, Any]:
    """单位 ``ops.coverage_gaps``：gap=0 ⇒ ok；>0 ⇒ warn（逐条 issue）；自检停摆 ⇒ warn。

    【为什么 >0 就 warn】缺口本身就是"该补的活"，不是故障；但也不能是 ok —— 那会让
    "全域监控"看起来已经完成（L5：没覆盖 ≠ 一切都好）。
    """
    data = summary(state=state, now=now)
    if not data.get("available"):
        return {"status": "never", "summary": str(data.get("detail") or ""),
                "metrics": {"count": None}, "issues": [], "last_activity": None}
    issues = ["%s: %s — %s" % (g.get("kind"), g.get("id"), g.get("why"))
              for g in data["gaps"][:ISSUE_LIST_LIMIT]]
    if data["count"] and len(data["gaps"]) > ISSUE_LIST_LIMIT:
        issues.append("…还有 %d 条（GET /api/alarms/coverage.gaps）"
                      % (data["count"] - ISSUE_LIST_LIMIT))
    if data.get("degraded"):
        issues.append("有输入面读不到：本轮缺口清单不完整（详见 coverage.gaps.surfaces）")
    if data.get("stale"):
        issues.append("自检已停摆：最近一次在 %.0f 分钟前（阈值 %d 个周期）"
                      % ((data.get("age_seconds") or 0) / 60.0, STALE_ROUNDS))
    status = "warn" if (data["count"] or data.get("degraded") or data.get("stale")) else "ok"
    return {
        "status": status,
        "summary": str(data.get("detail") or ""),
        "metrics": {"count": data["count"], "by_kind": data.get("by_kind") or {},
                    "pushed": data.get("pushed") or 0,
                    "age_seconds": data.get("age_seconds")},
        "issues": issues,
        "last_activity": data.get("generated_at"),
    }


def refresh_once() -> Dict[str, Any]:
    """调度任务入口：**永不抛**（调度器只看异常判 failed，抛一次就制造"计划任务失败"）。"""
    try:
        return refresh()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[CoverageGaps] 本轮自检失败（%s）", type(exc).__name__)
        return {"count": None, "error": type(exc).__name__}


def _remove_same_name(scheduler: Any) -> bool:
    for task in list(getattr(scheduler, "tasks", []) or []):
        if task.get("name") == TASK_NAME:
            scheduler.remove_task(task["task_id"])
            return True
    return False


def register_gap_scheduler(scheduler: Any = None) -> Dict[str, Any]:
    """注册「覆盖缺口自检」间隔任务（幂等）。开关关闭时零副作用。"""
    if not enabled():
        logger.info("[CoverageGaps] 周期自检已关闭（%s=false）", ENV_ENABLED)
        return {"status": "disabled", "reason": "%s=false" % ENV_ENABLED}
    try:
        if scheduler is None:
            from agent.task_scheduler import get_scheduler

            scheduler = get_scheduler()
    except Exception as exc:  # noqa: BLE001
        logger.error("[CoverageGaps] 调度器不可用（%s）", type(exc).__name__)
        return {"status": "error", "reason": "scheduler_unavailable"}
    _remove_same_name(scheduler)
    interval = interval_seconds()
    scheduler.add_interval_task(
        TASK_NAME, func=refresh_once, interval_seconds=interval,
        zero_token=True, owner="agent/monitoring/coverage_gaps.py",
        zero_token_note="只读扫描声明面并落状态文件，不调 LLM")
    task_id = scheduler.tasks[-1]["task_id"] if getattr(scheduler, "tasks", None) else ""
    logger.info("[CoverageGaps] 周期自检已注册 interval=%ds task_id=%s", interval, task_id)
    return {"status": "scheduled", "task_id": task_id, "interval_seconds": interval}


def unschedule_gap_scheduler(scheduler: Any = None) -> bool:
    """注销周期自检（按固定任务名定位）。"""
    try:
        if scheduler is None:
            from agent.task_scheduler import get_scheduler

            scheduler = get_scheduler()
    except Exception as exc:  # noqa: BLE001
        logger.error("[CoverageGaps] 调度注销失败（%s）", type(exc).__name__)
        return False
    return _remove_same_name(scheduler)


__all__: List[str] = [
    "ENV_ENABLED", "ENV_INTERVAL_SECONDS", "TASK_NAME", "DEFAULT_INTERVAL_SECONDS",
    "MIN_INTERVAL_SECONDS", "STATE_REL_PATH", "MAX_GAPS", "MAX_PUSH_PER_ROUND", "KINDS",
    "enabled", "interval_seconds", "make_gap", "fingerprint",
    "check_packages", "check_units", "check_sensors", "check_scheduler", "check_routes",
    "check_tools", "collect", "refresh", "summary", "unit_probe", "refresh_once",
    "load_state", "state_path", "register_gap_scheduler", "unschedule_gap_scheduler",
]
