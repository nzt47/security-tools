#!/usr/bin/env python3
"""契约漂移对拍工具（重构计划 阶段 0 / R1）。

【存在理由】2026-10-03 审计（快照 c78caed0）确认：云枢的「后端能力 → 前端呈现」由
四份互相独立的手写清单驱动，且相互之间没有任何机制做一致性校验：
  ① app_server + plugins + agent/server_routes 的真实 Flask 路由（3 个注册面）；
  ② agent/modules_registry.py 的 6 域 32 节点 + 24 条 ACTION_ROUTES；
  ③ ~~plugins/*.py 里 Plugin(routes=[...]) 的声明~~ ← **已消灭（阶段 4 / R4）**：
     routes 改为从 app.url_map 派生，11 个插件的手写清单（199 条）全部删除。
     本工具对这一面改为**防回归**判定（manifest_hand_written_routes）；
     派生完整性由运行期的 app_server.audit_plugin_manifest() 守（静态拿不到 url_map）。
  ④ 前端两侧硬编码的 /api 字面量（React + legacy）。
手工维护的清单必然漂移（方案第 13 节的原话），本工具把漂移变成可执行、可进 CI 的判定。
**消灭一个事实源，优于让两个事实源保持同步** —— ③ 是这条原则的第一个落地样本：
原判据（"两侧是否一致"）实测每年命中 11 处；改判据后该漂移类**在构造上不可能发生**。

【设计约束】
- 默认纯静态（正则 + AST），不导入 app_server：实测导入一次 53.5s，且会注册
  Prometheus 计数器导致同进程二次导入 ValueError。故可进 CI、可进 pre-commit。
  --live 才导入 app_server 取 url_map 真值（本机排障用）。
- 只做「声明 vs 声明」的对拍，不修改任何文件；退出码由 --fail-on-drift 控制。
- 必须排除嵌套副本 security-tools/ 与 _scratch/，否则产出双份契约（审计 M-26）。

【已知静态局限（都是实测假阳性的来源，已逐条缓解）】
  1. Blueprint url_prefix -> 已解析（BLUEPRINT_DECL）；
  2. 模块级常量前缀 f"{PREFIX}/x" -> 已解析（CONST_DECL + 行内展开）；
  3. 查询串模板 /api/x/stream${q} -> 末尾 <> 视为查询串片段（_route_known 容错）；
  4. mock 层（devMock）-> 显式排除，它本就是同形假数据（审计 M-3）；
  5. 单段前缀常量（const PREFIX = "/api/cp"）-> 段数 < 3 且未命中真实路由则跳过；
  6. 运行期动态注册（add_url_rule）-> 静态无从得知，需 --live 兜底 [未验证 是否存在]。

用法：
    python scripts/audit/contract_diff.py                  # 人读摘要，退出码恒 0
    python scripts/audit/contract_diff.py --json reports/contract_drift_baseline.json
    python scripts/audit/contract_diff.py --fail-on-drift  # CI：有漂移即非 0
    python scripts/audit/contract_diff.py --live           # 用 app_server.url_map 真值
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

EXCLUDE_DIRS = {
    "node_modules", ".git", "__pycache__", "dist", "dist-electron", "build",
    "security-tools",
    ".pytest_tmp", "_scratch", "_ci_logs", ".devtools", ".venv", "venv",
    "htmlcov", "coverage_report", "test_reports", "backups", "backup",
    ".mypy_cache", ".ruff_cache", ".tmp-merge", "restored-frontend",
}

MUTATING = {"POST", "PUT", "DELETE", "PATCH"}

# 模块级路径常量（用于展开 f-string 前缀）：PREFIX = "/api/cp"
CONST_DECL = re.compile(r'''^([A-Z_][A-Z0-9_]*)\s*=\s*["'](/[^"']*)["']''')
# Blueprint 变量声明 + 字面量 url_prefix
BLUEPRINT_DECL = re.compile(
    r'''^\s*([A-Za-z_][\w]*)\s*=\s*Blueprint\([^)]*url_prefix\s*=\s*["'](/[^"']*)'''
)
ROUTE_DECORATOR = re.compile(
    r'''^\s*@(?P<obj>[A-Za-z_][\w.]*)\.(?P<meth>route|get|post|put|delete|patch)\('''
)
PATH_LITERAL = re.compile(r'''["'](/[A-Za-z0-9_\-./{}<>:$]*)["']''')
FRONTEND_LITERAL = re.compile(r'''["'\x60](/api/[A-Za-z0-9_\-./{}<>:$]*)["'\x60]''')


def _real_route_decorator_lines(tree):
    # 返回 {行号: 方法名}，只含**真实**函数装饰器。
    #
    # 【为什么必须走 AST】纯文本扫描会把**文档字符串里的用法示例**当成真路由。实测：
    # 2026-10-03 给 plugins/plugin_api.py 的 require_auth 写了段用法示例（docstring 里含
    # @bp.route("/api/x", methods=["POST"])），文本扫描立刻把 /api/x 计成一条真实路由，
    # routes_total 从 449 虚增到 450。一个会凭空造出端点的门禁没有可信度 —— 那正是
    # 本工具反复强调的「零假阳性」红线。
    found = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for d in node.decorator_list:
            if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute):
                meth = d.func.attr.lower()
                if meth in ("route", "get", "post", "put", "delete", "patch"):
                    found[int(d.lineno)] = meth
    return found


def _is_excluded(rel) -> bool:
    # 按**仓库相对路径**判断是否排除。
    #
    # 【为什么必须是相对路径 —— 2026-10-03 由 CI 实测暴露，且后果严重】本仓 GitHub
    # Actions 的 checkout 路径是 /home/runner/work/<repo>/<repo>/，而**仓库名恰好叫
    # security-tools**。若拿**绝对路径**的 parts 去比对 EXCLUDE_DIRS（其中含
    # "security-tools"，本意是排除仓库内那份未跟踪的嵌套副本），则 CI 上**整棵仓库都
    # 被判为排除** —— 实测后果：collect_routes_static() 只剩 app_server.py/main.py
    # （它们走另一条不过滤的分支），agent/ 与 plugins/ 全被跳过，于是「Blueprint 前缀」
    # 与「常量前缀」两条用例在 CI 上红、本机却全绿。**更危险的是它静默**：门禁会带着
    # 一个残缺的路由集判「零漂移」——那正是本工具反复强调要避免的「门禁形同虚设」。
    return any(part in EXCLUDE_DIRS for part in rel.parts)


def _iter_source_files(suffixes, dirs):
    for d in dirs:
        base = ROOT / d
        if not base.exists():
            continue
        for p in base.rglob("*"):
            if not p.is_file() or p.suffix not in suffixes:
                continue
            if _is_excluded(p.relative_to(ROOT)):
                continue
            yield p


#: 便宜预筛：文件里是否**可能**存在路由装饰器。
#  【为什么必须有 —— 2026-10-03 由 CI 超时暴露】一次全量扫描要读 719 个 .py 并对每个做
#  ast.parse；本机实测 **4.35s**，而 CI 的共享 runner（xdist 双 worker + 重测试并行）
#  上超过 pytest-timeout 的 **60s** 上限，5 个调用该函数的用例全部超时失败。
#  实测这 719 个文件里绝大多数**根本没有路由装饰器**，对它们做 ast.parse 纯属浪费。
#  先用一次 C 级正则判一遍，没有就整文件跳过 —— 这一条把开销降到只解析真正相关的文件。
_ROUTE_HINT = re.compile(r"@\w+\.(route|get|post|put|delete|patch)\(")

#: collect_routes_static 的记忆化缓存。
#  【为什么】它是**工作树的纯函数**：同一次进程内结果不变，而调用方（多个用例、
#  以及 CLI 的多次对拍）会反复调用。CI 上曾因 5 次重复全量扫描而全部超时。
_ROUTE_CACHE = None


def reset_routes_cache() -> None:
    """清空记忆化缓存（测试/同一进程内工作树被改动后需要）。"""
    global _ROUTE_CACHE
    _ROUTE_CACHE = None


def _copy_routes(routes):
    """返回深一层的副本：缓存必须防调用方就地改动（否则缓存被污染）。"""
    return {k: {"path": v["path"], "methods": list(v["methods"]),
                "where": list(v["where"])} for k, v in routes.items()}


def collect_routes_static():
    """静态收集 Flask 路由：路径 -> {methods, where}（覆盖 3 个注册面）。

    结果为**记忆化**的；同一进程内重复调用几乎零成本（见 _ROUTE_CACHE 的说明）。
    """
    global _ROUTE_CACHE
    if _ROUTE_CACHE is not None:
        return _copy_routes(_ROUTE_CACHE)

    files = [p for p in [ROOT / "app_server.py", ROOT / "main.py"] if p.exists()]
    files += list(_iter_source_files(
        {".py"}, ["plugins", "agent", "sensor", "memory", "cognitive"]))

    routes = {}
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if not _ROUTE_HINT.search(text):  # 便宜预筛，见 _ROUTE_HINT 说明
            continue
        lines = text.splitlines()

        # AST 定位真实装饰器行（排除 docstring 里的示例）
        try:
            _tree = ast.parse(text)
            real_routes = _real_route_decorator_lines(_tree)
        except SyntaxError:
            real_routes = {}

        consts = {}
        prefixes = {}
        for ln in lines:
            cm = CONST_DECL.match(ln)
            if cm:
                consts[cm.group(1)] = cm.group(2)
            bm = BLUEPRINT_DECL.match(ln)
            if bm and bm.group(2):
                prefixes[bm.group(1)] = bm.group(2)

        for i, raw_line in enumerate(lines):
            if (i + 1) not in real_routes:  # ast.lineno 1-based：只在真实装饰器行上判定
                continue
            line = raw_line
            # 展开 f"{PREFIX}/x" -> "/api/cp/x"
            if consts and 'f"{' in line or (consts and "f'{" in line):
                for cname, cval in consts.items():
                    line = line.replace('f"{' + cname + '}', '"' + cval)
                    line = line.replace("f'{" + cname + "}", "'" + cval)
            m = ROUTE_DECORATOR.match(line)
            if not m:
                continue
            block = [line]
            j = i + 1
            while j < len(lines) and j < i + 8:
                block.append(lines[j])
                if re.match(r"^\s*def\s", lines[j]):
                    break
                j += 1
            blob = "\n".join(block)
            pm = PATH_LITERAL.search(line[m.end() - 1:])
            if not pm:
                pm = PATH_LITERAL.search(blob)
            if not pm:
                continue
            path = pm.group(1)
            prefix = prefixes.get(m.group("obj"), "")
            if prefix and not path.startswith(prefix + "/"):
                path = prefix.rstrip("/") + path
            meth = m.group("meth").upper()
            if meth == "ROUTE":
                mm = re.search(r"methods\s*=\s*\[([^\]]*)\]", blob)
                if mm:
                    methods = sorted(set(re.findall(r'''["']([A-Z]+)["']''', mm.group(1))))
                else:
                    methods = ["GET"]
            else:
                methods = [meth]
            rel = str(f.relative_to(ROOT)).replace("\\", "/")
            cur = routes.setdefault(path, {"path": path, "methods": set(), "where": []})
            cur["methods"].update(methods)
            cur["where"].append(rel + ":" + str(i + 1))
    for v in routes.values():
        v["methods"] = sorted(v["methods"])
    _ROUTE_CACHE = routes  # 记忆化（见 _ROUTE_CACHE 说明）
    return _copy_routes(routes)


def collect_routes_live():
    """导入 app_server 取 url_map 真值（慢，且同进程只能导入一次）。"""
    sys.path.insert(0, str(ROOT))
    import app_server  # noqa: PLC0415
    routes = {}
    for r in app_server.app.url_map.iter_rules():
        methods = sorted(set(r.methods or ()) - {"HEAD", "OPTIONS"})
        routes[str(r.rule)] = {
            "path": str(r.rule), "methods": methods,
            "where": ["endpoint=" + str(r.endpoint)],
        }
    return routes


def collect_registry():
    """导入 agent.modules_registry（其自述可被轻量测试/CI 独立导入）。"""
    sys.path.insert(0, str(ROOT))
    try:
        from agent.modules_registry import ACTION_ROUTES, DOMAINS  # noqa: PLC0415
    except Exception as e:  # noqa: BLE001
        return None, "导入 modules_registry 失败: " + type(e).__name__ + ": " + str(e)
    nodes = []
    for d in DOMAINS:
        for n in d.nodes:
            nodes.append({
                "domain": d.domain_id, "module_id": n.module_id, "name": n.name,
                "path": n.path, "status_source": n.status_source,
                "actions": list(n.actions or []), "danger": n.danger,
            })
    actions = {k: {"method": v.method, "url": v.url, "danger": v.danger}
               for k, v in ACTION_ROUTES.items()}
    return {"nodes": nodes, "actions": actions}, None


def collect_manifest_declared():
    """静态解析 plugins/*.py 中 Plugin(routes=[...]) 的字符串列表。"""
    declared = {}
    for f in sorted((ROOT / "plugins").glob("*.py")):
        try:
            tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError):
            continue
        found = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for kw in node.keywords:
                if kw.arg != "routes":
                    continue
                if isinstance(kw.value, (ast.List, ast.Tuple)):
                    for el in kw.value.elts:
                        if isinstance(el, ast.Constant) and isinstance(el.value, str):
                            found.append(el.value)
        if found:
            declared[f.name] = sorted(set(found))
    return declared


def collect_frontend_literals():
    """两套前端分别统计：React（yunshu-ui/src）与 legacy（templates + static）。"""
    out = {}
    groups = {
        "react": list(_iter_source_files({".ts", ".tsx"}, ["yunshu-ui/src"])),
        "legacy": list(_iter_source_files({".html", ".js"}, ["templates", "static"])),
    }
    for label, files in groups.items():
        hits = {}
        for f in files:
            rel = str(f.relative_to(ROOT)).replace("\\", "/")
            if ".test." in rel or ".spec." in rel or rel.endswith(".min.js"):
                continue
            if "/assets/" in rel or rel.endswith(".map"):
                continue
            # mock 层提供的是同构假数据，不代表真实调用面（审计 M-3 已记录其掩盖漂移）。
            if "/mocks/" in rel or "devMock" in rel or "exportMock" in rel:
                continue
            try:
                txt = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            # 【必须先剥注释】否则注释里描述端点的那类反引号代码跨会被当成真实调用
            # （实测 4 处），既虚高计数，又会在该路由被删时产生假警报。
            for m in FRONTEND_LITERAL.finditer(strip_comments(txt)):
                hits.setdefault(m.group(1), []).append(rel)
        out[label] = hits
    return out


#: 引号字符：单引号、双引号、反引号（模板串）。用 hex 转义写是为了本文件自身可读，
#: 语义与字面反引号完全相同。
_QUOTES = ("'", '"', "\x60")


def strip_comments(src: str) -> str:
    """剥离 TS/JS 的注释与 HTML 注释；**保留**字符串与模板串内容。

    【解决什么】frontend_literals 用"引号包裹的 /api 路径"来识别前端调用，于是
    **注释里的反引号代码跨**也被算进来了。实测（2026-10-03，先量化再决定）：
        yunshu-ui/src/lib/callability.ts:7    * · [反引号]GET /api/agent-lines/planes[反引号]
        yunshu-ui/src/lib/callability.ts:8    * · [反引号]GET /api/capability-manifest[反引号]
        yunshu-ui/src/lib/approvalChain.ts:5  * 而 [反引号]POST /api/cp/approvals/batch/link[反引号]
        yunshu-ui/src/lib/toolExemptionsApi.ts:4 * 契约来源：[反引号]/api/cp/tool-exemptions[反引号]
    它们不是调用，却：① 让"还要收口多少"的数虚高（阶段 5 的 stray 口径要到 0）；
    ② 更具破坏性的是 —— **一旦被注释描述的那条路由被删除**，就会凭空产生一条
    "前端调用了不存在的端点"的漂移项。那是假警报，而假警报会让门禁被无视
    （本仓 M-35 记录过同类后果）。

    【为什么必须用状态机而不是正则】不能简单地删掉"斜杠斜杠到行尾"——
    http://x 这类**字符串里**的斜杠斜杠会被误删，把真实的端点弄丢，
    那才是真正危险的（漏报）。故逐字符扫描，进入字符串就把整段原样吐出。

    【边界】正则字面量不单独处理：其中的斜杠后不是斜杠或星号时按普通字符处理，
    不会误判为注释起始。实测本仓不存在会被误判的写法。
    """
    out = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        nxt = src[i + 1] if i + 1 < n else ""
        if c in _QUOTES:
            q = c
            out.append(c)
            i += 1
            while i < n:
                if src[i] == "\\":
                    out.append(src[i:i + 2])
                    i += 2
                    continue
                out.append(src[i])
                if src[i] == q:
                    i += 1
                    break
                i += 1
            continue
        if c == "/" and nxt == "/":
            # 只跳到行尾、不删换行 —— 保证后续**行号不变**（本仓对"行号参与判据"已有教训）
            while i < n and src[i] != "\n":
                i += 1
            continue
        if c == "/" and nxt == "*":
            i += 2
            while i + 1 < n and not (src[i] == "*" and src[i + 1] == "/"):
                # 【块注释里的换行必须补出来】否则跨行块注释会让后续**行号整体前移**，
                # 报错信息指向错行。本仓对"行号参与判据"已有教训
                # （test_date_shift_blindspots_guard 里那批 legacy 行号键）。
                if src[i] == "\n":
                    out.append("\n")
                i += 1
            i += 2
            continue
        if c == "<" and src.startswith("<!--", i):
            i += 4
            while i < n and not src.startswith("-->", i):
                i += 1
            i += 3
            continue
        out.append(c)
        i += 1
    return "".join(out)


#: 前端**被许可的端点常量层**（阶段 5 / R5）。
#:
#: 【为什么需要这个概念】`frontend_literals` 统计的是**去重后的路径数** ——
#: 它衡量"前端引用了多少个不同的后端端点"，是个合同**面**指标；把散落的字面量
#: 收口到常量层**不会**让它变小（路径还在，只是换了地方写）。用它当"收敛进度"会得出
#: 错误的结论（"改了一堆，数字没动"）。
#: 故另设 `frontend_stray_literals`：统计**常量层之外**还剩多少处 `/api` 字面量 ——
#: 这才是"还要收口多少"的可执行度量，且它的目标是 0。
#:
#: 【常量层仍参与正确性校验】本文件只把它从**计数**里排除，不排除它的路径参与
#: "是否命中真实后端路由"的判定 —— 计数与正确性分开：计数要"还剩多少处散落"，
#: 正确性要"每一条都真实存在"。若把常量层整段排除，那才是把门禁弄瞎。
SANCTIONED_FRONTEND_LAYER = "yunshu-ui/src/api/endpoints.ts"


def count_frontend_literals(txt: str, *, strip: bool = True) -> int:
    """统计一段前端源码里的 /api 字面量个数（默认**先剥注释**）。

    【为什么单独抽出来】"注释里的端点描述不算调用"这条判据必须能被**直接测**。
    它原先只能整仓跑一遍再断言某个文件的数值 —— 而那正是"把会变的现状写死"的形态。
    本轮实测踩到：`callability.ts` 的 stray 数在**把该文件迁进常量层之后**由 1 变 0，
    于是断言 `== 1` 的用例在 CI 上红了三条作业（契约门禁 + 两个测试分片），
    而**功能本身完全正确** —— 收集器是对的，错的是把"今天的数值"当成契约。
    ⇒ 判据改为对**合成输入**做（见 test_contract_diff_matcher.py），锚在机制上。
    """
    return len(FRONTEND_LITERAL.findall(strip_comments(txt) if strip else txt))


def collect_stray_frontend_literals():
    """统计**常量层之外**的 /api 字面量**出现次数**（按文件聚合，非去重）。

    与 collect_frontend_literals 的区别：那个给的是"引用了多少个不同端点"（去重、供对拍），
    这个给的是"还有多少处需要收口"（计次、供进度与门禁）。
    """
    groups = {
        "react": list(_iter_source_files({".ts", ".tsx"}, ["yunshu-ui/src"])),
        "legacy": list(_iter_source_files({".html", ".js"}, ["templates", "static"])),
    }
    out = {}
    for label, files in groups.items():
        per_file = {}
        for f in files:
            rel = str(f.relative_to(ROOT)).replace("\\", "/")
            if rel == SANCTIONED_FRONTEND_LAYER:
                continue  # 常量层本身不计（它就是收口点）
            if ".test." in rel or ".spec." in rel or rel.endswith(".min.js"):
                continue
            if "/assets/" in rel or rel.endswith(".map"):
                continue
            if "/mocks/" in rel or "devMock" in rel or "exportMock" in rel:
                continue
            try:
                txt = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            n = count_frontend_literals(txt)
            if n:
                per_file[rel] = n
        out[label] = per_file
    return out


def _norm(path):
    """归一化动态段：<int:id> / <id> / :id / {id} / ${id} -> <>，便于跨框架比较。

    【为什么先处理 ${...}】TS 模板字面量写作 "/api/skills-mgmt/${item.id}/publish"，
    若只把 {..} 换成 <> 会残留 "$"，与后端 "/api/skills-mgmt/<>/publish" 对不上 ——
    本工具第一版因此产生 60+ 条假阳性。
    """
    p = re.sub(r"\$\{[^}]*\}", "<>", path)
    p = re.sub(r"<[^>]*>", "<>", p)
    p = re.sub(r"\{[^}]*\}", "<>", p)
    p = re.sub(r":[A-Za-z_][\w]*", "<>", p)
    p = p.replace("$", "")
    return p.rstrip("/") or "/"


def _route_known(path, routes):
    """声明路径是否命中真实路由（支持 <> 通配、/* 子树、末尾查询串片段容错）。"""
    if path.endswith("/*"):
        base = path[:-2].rstrip("/")
        if base and base != "/":
            for rp in routes:
                if _norm(rp) == base or _norm(rp).startswith(base + "/"):
                    return True
        return False
    n = _norm(path)
    cands = [n]
    # 末尾的 <> 常来自查询串模板（/api/x/stream${q} 里的 q 是 "?a=b"），不是路径段。
    # 两种写法都要容错：带斜杠（/x/${id}）与不带（/x/stream${q}）。
    if n.endswith("/<>"):
        cands.append(n[:-3])
    elif n.endswith("<>"):
        cands.append(n[:-2])
    for rp in routes:
        if _norm(rp) in cands:
            return True
    return False


def check(registry, routes, manifest, frontend):
    findings = []

    def add(kind, severity, subject, detail, where=""):
        findings.append({"kind": kind, "severity": severity, "subject": subject,
                         "detail": detail, "where": where})

    if registry:
        for n in registry["nodes"]:
            src = (n.get("status_source") or "").strip()
            if src.startswith("api:"):
                api = src[4:].strip()
                if api and not _route_known(api, routes):
                    add("registry_status_source_missing", "high", n["module_id"],
                        "status_source 指向的端点未在真实路由中找到: " + api,
                        "agent/modules_registry.py")
            fp = (n.get("path") or "").strip()
            if fp and " " not in fp and (fp.endswith(".py") or fp.endswith("/")):
                if not (ROOT / fp.rstrip("/")).exists():
                    add("registry_path_missing", "high", n["module_id"],
                        "声明 path 指向的文件/目录不存在: " + fp + "   <- 审计 H-2 的形态",
                        "agent/modules_registry.py")
        for key, a in registry["actions"].items():
            if not _route_known(a["url"], routes):
                add("registry_action_url_missing", "high", key,
                    "ACTION_ROUTES 指向的端点不存在: " + a["method"] + " " + a["url"],
                    "agent/modules_registry.py")

    # ── 插件 manifest：判据已随"事实源唯一化"改写（2026-10-03 · 阶段 4 / R4）──
    # 【为什么删掉原来那两条判据】原判据是
    #   · manifest_route_not_real      —— manifest 声明了并非真实规则的路径；
    #   · manifest_route_undeclared    —— 插件真实注册了路由但 manifest 未声明（实测命中 11 处）。
    # 二者都建立在"manifest.routes 是**手写声明**"这个前提上。R4 把 routes 改为从
    # app.url_map **派生**、并删除了 11 个插件里的手写清单后，这两条判据
    # **在数学上恒真** —— 保留它们只会让门禁看起来很绿而什么都查不出，
    # 正是"会漏报的门禁比会误报的更危险"。
    #
    # 【替代判据 = 防回归】现在唯一能让"两个事实源"复活的方式，就是有人把手写
    # routes=[...] 加回去。collect_manifest_declared() 静态解析该关键字，
    # 只要还能解析出非空结果，就说明声明回来了。
    #
    # 【完整性判据搬到运行期】"派生结果是否覆盖了插件真实注册的全部路由"需要真实
    # url_map（Flask 的 Blueprint 在 register_blueprint 之前不持有规则列表），
    # 静态工具拿不到 ⇒ 由 app_server.audit_plugin_manifest() 在启动期判定
    # （带蓝图的插件必须至少派生出 1 条路由）。两把尺子分工明确：
    #   本工具（静态·CI）：不许再出现手写清单；
    #   启动自检（运行期）：派生必须完整、不许静默为空。
    for plugin, paths in sorted((manifest or {}).items()):
        add("manifest_hand_written_routes", "high", plugin,
            "插件仍带手写 routes=[...]（%d 条）—— manifest 的 routes 现已从 app.url_map "
            "派生，留着它就等于把第二个事实源加回来（审计 H-1/H-6）。"
            "请从 plugins/*.py 的 Plugin(...) 中删除该关键字。" % len(paths),
            "plugins/")

    for label, hits in (frontend or {}).items():
        for path, files in sorted(hits.items()):
            # 单段路径（如 "/api/cp"）多为前缀常量而非端点调用：
            #   const PREFIX = "/api/cp";  request(PREFIX + "/panels")
            segs = [s for s in _norm(path).split("/") if s]
            if len(segs) < 3 and not _route_known(path, routes):
                continue
            if not _route_known(path, routes):
                add("frontend_calls_missing_endpoint", "high", label + ":" + path,
                    str(len(files)) + " 处前端调用该端点，但真实路由中不存在",
                    ", ".join(sorted(set(files))[:2]))

    sys.path.insert(0, str(ROOT))
    try:
        from agent.server_auth import find_allowed_write_endpoints  # noqa: PLC0415
        env_allow = os.environ.get("CP_API_AUTH_ALLOW", "")
        if env_allow:
            for h in find_allowed_write_endpoints(
                    env_allow.split(","), ((p, i["methods"]) for p, i in routes.items())):
                add("auth_allowlist_write_exempt", "high", h,
                    "CP_API_AUTH_ALLOW 豁免了一个变更型端点（审计 H-5 的形态）", ".env")
    except Exception:  # noqa: BLE001
        pass

    return findings


def main():
    ap = argparse.ArgumentParser(description="契约漂移对拍（阶段 0 / R1）")
    ap.add_argument("--json", metavar="PATH", help="把基线报告写入该文件（建议入库）")
    ap.add_argument("--fail-on-drift", action="store_true",
                    help="有 high/medium 漂移即退出码 1")
    ap.add_argument("--live", action="store_true",
                    help="导入 app_server 取 url_map 真值（慢）")
    args = ap.parse_args()

    routes = collect_routes_live() if args.live else collect_routes_static()
    registry, reg_err = collect_registry()
    manifest = collect_manifest_declared()
    frontend = collect_frontend_literals()
    stray = collect_stray_frontend_literals()

    findings = check(registry, routes, manifest, frontend)
    by_sev = {}
    for f in findings:
        by_sev[f["severity"]] = by_sev.get(f["severity"], 0) + 1

    report = {
        "snapshot": {
            "route_source": "live(url_map)" if args.live else "static(decorators)",
            "routes_total": len(routes),
            "registry_nodes": len(registry["nodes"]) if registry else None,
            "registry_actions": len(registry["actions"]) if registry else None,
            "registry_error": reg_err,
            "plugins_with_declared_routes": len(manifest or {}),
            "manifest_declared_total": sum(len(v) for v in (manifest or {}).values()),
            "frontend_literals": {k: len(v) for k, v in (frontend or {}).items()},
            # 【两个数必须一起看】literal 数（去重路径，合同面）与 stray 数
            # （常量层之外的**出现次数**，收敛进度）。只看前者会得出
            # "改了一堆、数字没动"的错觉；只看后者会忽略"引用了哪些端点"本身。
            "frontend_stray_literals": {k: sum(v.values()) for k, v in stray.items()},
            "frontend_stray_by_file": {k: dict(sorted(v.items(), key=lambda kv: -kv[1]))
                                       for k, v in stray.items()},
        },
        "findings_summary": by_sev,
        "findings": findings,
    }

    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print("[contract_diff] 基线已写入 " + str(out))

    print("=" * 70)
    print("  契约漂移对拍（阶段 0 / R1）")
    print("=" * 70)
    for k, v in report["snapshot"].items():
        # 【逐文件明细太长】stray_by_file 全量打印会糊满一屏、把真信号淹掉
        # （本仓对"一个会刷屏的门禁没人会看"有记录）。故此处在人读摘要里只打
        # Top 5；完整明细在 --json 的 report 里，机读方自取。
        if k == "frontend_stray_by_file":
            for label, per_file in (v or {}).items():
                top = list(per_file.items())[:5]
                rest = len(per_file) - len(top)
                print("  frontend_stray_top5[" + label + "]: " + ", ".join(
                    f + "=" + str(n) for f, n in top)
                    + (f"  (+{rest} more files)" if rest > 0 else ""))
            continue
        print("  " + str(k) + ": " + str(v))
    print("  漂移分级: " + str(by_sev if by_sev else "无"))
    print("-" * 70)
    for f in findings:
        print("  [" + f["severity"].upper() + "] " + f["kind"] + ": " + str(f["subject"]))
        print("        " + f["detail"])
        if f["where"]:
            print("        @ " + f["where"])
    print("-" * 70)
    print("  合计 " + str(len(findings)) + " 条漂移")

    if args.fail_on_drift and any(f["severity"] in ("high", "medium") for f in findings):
        print("[contract_diff] 存在 high/medium 漂移 => 退出码 1")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
