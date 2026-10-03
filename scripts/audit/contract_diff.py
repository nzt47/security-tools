#!/usr/bin/env python3
"""契约漂移对拍工具（重构计划 阶段 0 / R1）。

【存在理由】2026-10-03 审计（快照 c78caed0）确认：云枢的「后端能力 → 前端呈现」由
四份互相独立的手写清单驱动，且相互之间没有任何机制做一致性校验：
  ① app_server + plugins + agent/server_routes 的真实 Flask 路由（3 个注册面）；
  ② agent/modules_registry.py 的 6 域 32 节点 + 23 条 ACTION_ROUTES；
  ③ plugins/*.py 里 Plugin(routes=[...]) 的声明（即 /api/plugins manifest）；
  ④ 前端两侧硬编码的 /api 字面量（React + legacy）。
手工维护的清单必然漂移（方案第 13 节的原话），本工具把漂移变成可执行、可进 CI 的判定。

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


def _iter_source_files(suffixes, dirs):
    for d in dirs:
        base = ROOT / d
        if not base.exists():
            continue
        for p in base.rglob("*"):
            if not p.is_file() or p.suffix not in suffixes:
                continue
            if any(part in EXCLUDE_DIRS for part in p.parts):
                continue
            yield p


def collect_routes_static():
    """静态收集 Flask 路由：路径 -> {methods, where}（覆盖 3 个注册面）。"""
    files = [p for p in [ROOT / "app_server.py", ROOT / "main.py"] if p.exists()]
    files += list(_iter_source_files(
        {".py"}, ["plugins", "agent", "sensor", "memory", "cognitive"]))

    routes = {}
    for f in files:
        try:
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue

        # AST 定位真实装饰器行（排除 docstring 里的示例）
        try:
            _tree = ast.parse(chr(10).join(lines))
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
    return routes


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
            for m in FRONTEND_LITERAL.finditer(txt):
                hits.setdefault(m.group(1), []).append(rel)
        out[label] = hits
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

    declared_all = set()
    for plugin, paths in (manifest or {}).items():
        for p in paths:
            declared_all.add(p)
            if not _route_known(p, routes):
                add("manifest_route_not_real", "medium", plugin,
                    "manifest 声明了并非真实规则的路径: " + p, "plugins/")
    declared_norm = {_norm(p) for p in declared_all}
    for path, info in routes.items():
        if not any("plugins/" in w for w in info.get("where", [])):
            continue
        if _norm(path) not in declared_norm:
            add("manifest_route_undeclared", "medium", path,
                "插件真实注册了该路由，但 manifest.routes 未声明",
                ", ".join(info.get("where", [])[:2]))

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
