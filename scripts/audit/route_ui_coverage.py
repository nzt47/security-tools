# -*- coding: utf-8 -*-
"""路由 ↔ UI 覆盖盘点（2026-10-07）。

【解决什么】本仓后端路由（静态口径实测 450 条）与前端 UI 之间没有一张「谁呈现到界面」的
总账：改一个端点、加一个插件，没人能一眼答出「它现在有没有 UI 入口」。
本脚本按静态可得的两侧数据做**机械分类**，产出一份可提交、可 diff、可上屏的清单：

  · ui           ：被前端源码引用（yunshu-ui/src 的常量层 + 页面字面量，先剥注释）；
  · cli_script   ：只在 CLI / 脚本 / CI 工作流里出现（scripts/tools/.github/main.py/*.ps1）；
  · runtime_only ：只在 app_server / agent 的非路由模块里出现（进程内 provider、探针、看门狗）；
  · unreferenced ：以上都没找到引用（可能是纯内部、已废弃，或由动态拼接调用）。

【口径与边界（如实标注，不假装穷尽）】
  1. 路由与前端字面量分别复用 scripts.audit.contract_diff 的 collect_routes_static 与
     collect_frontend_literals（后者已先剥注释、排除构建产物与 mock）；
  2. 分类判据是**路径归一化后的精确匹配**：Flask 的 <int:x>/<x> 与 JS 的 ${x} 都归一成 *；
     不做「前缀包含」式匹配 —— 那会把 /api/skills 与 /api/skills-mgmt 混为一谈（假绿）；
  3. runtime_only / cli_script 的扫描会让**注释里提到端点的行**也计入 refs；清单里带 refs
     供人工复核。这是「宁可多留不可少留」口径，**不用于门禁**：门禁只钉 content_hash 是否
     与提交产物一致（排除 generated_at）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from hashlib import sha256
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.audit.contract_diff import (  # noqa: E402
    collect_frontend_literals,
    collect_routes_static,
)

DEFAULT_JSON = ROOT / "reports" / "route_ui_coverage.json"

#: 归一化：Flask 变量段 <int:x>/<x> 与 JS 模板 ${x} 都折成 *
_PARAM_RE = re.compile(r"<[^>]+>")
_TMPL_RE = re.compile(r"\$\{[^}]*\}")
#: 带引号的候选路径字面量（Python / JS / PowerShell / YAML 通用）
_QUOTED = re.compile(r"""['"](/[^'"\s]{2,})['"]""")
_EXCLUDE_DIRS = {"node_modules", "dist", "__pycache__", "htmlcov", ".venv", "venv", ".git"}


def norm(path: str) -> str:
    p = path.split("?")[0]
    p = _PARAM_RE.sub("*", p)
    p = _TMPL_RE.sub("*", p)
    if len(p) > 1 and p.endswith("/"):
        p = p[:-1]
    return p


def _registered_server_route_modules() -> set:
    """从 app_server.py 解析**真正接线**的 agent.server_routes.* 模块。

    【为什么不直接把 agent/server_routes/* 当死代码】其中 routes_dashboard / routes_ui_panels
    等确已接线（见 test_server_routes_registration_inventory.py::KNOWN_UNREGISTERED 的反面）；
    「某条路由是不是死副本」只能由「它的定义文件有没有被 app_server 引到」回答。
    """
    f = ROOT / "app_server.py"
    if not f.exists():
        return set()
    src = f.read_text(encoding="utf-8", errors="replace")
    mods = set(re.findall(r"from\s+agent\.server_routes\.(\w+)\s+import", src))
    mods |= set(re.findall(r"from\s+agent\.server_routes\s+import\s+(\w+)", src))
    return mods


def _is_live_where(rel: str, registered_mods: set) -> bool:
    rel = rel.replace("\\", "/")
    if rel.startswith("plugins/") or rel in ("app_server.py", "main.py"):
        return True
    if rel.startswith("agent/server_routes/"):
        mod = Path(rel).name[:-3] if rel.endswith(".py") else Path(rel).name
        return mod in registered_mods
    return True  # sensor/ / memory/ / cognitive/ / agent 其它：非 server_routes，默认按活体


def _iter_files(suffixes, dirs):
    for d in dirs:
        base = ROOT / d
        if not base.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [n for n in dirnames
                           if n not in _EXCLUDE_DIRS and not n.startswith(".")]
            for name in filenames:
                if Path(name).suffix in suffixes:
                    yield Path(dirpath) / name


def _refs_from_files(files):
    refs: dict = {}
    for f in files:
        try:
            rel = str(f.relative_to(ROOT)).replace("\\", "/")
        except ValueError:
            continue
        try:
            txt = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in _QUOTED.finditer(txt):
            refs.setdefault(norm(m.group(1)), set()).add(rel)
    return refs


def _refs_from_frontend(fe):
    refs: dict = {}
    for label in ("react", "legacy"):
        for lit, files in (fe.get(label) or {}).items():
            refs.setdefault(norm(lit), set()).update(files)
    return refs


def classify() -> dict:
    routes = collect_routes_static()
    ui_refs = _refs_from_frontend(collect_frontend_literals())

    cli_files = list(_iter_files({".py", ".ps1", ".yml", ".yaml"}, ["scripts", "tools", ".github"]))
    main_py = ROOT / "main.py"
    if main_py.exists():
        cli_files.append(main_py)
    cli_refs = _refs_from_files(cli_files)

    runtime_files = [p for p in [ROOT / "app_server.py"] if p.exists()]
    for f in _iter_files({".py"}, ["agent"]):
        rel = str(f.relative_to(ROOT)).replace("\\", "/")
        if rel.startswith("agent/server_routes/"):
            continue  # 这些是路由定义模块，不是消费者
        runtime_files.append(f)
    rt_refs = _refs_from_files(runtime_files)

    registered_mods = _registered_server_route_modules()
    entries = []
    live_totals = {"ui": 0, "cli_script": 0, "runtime_only": 0, "unreferenced": 0}
    for path in sorted(routes):
        meta = routes[path]
        np = norm(path)
        ui = sorted(ui_refs.get(np, set()))
        cli = sorted(cli_refs.get(np, set()))
        rt = sorted(rt_refs.get(np, set()))
        if ui:
            cat = "ui"
        elif cli:
            cat = "cli_script"
        elif rt:
            cat = "runtime_only"
        else:
            cat = "unreferenced"
        where = sorted(meta.get("where") or [])
        live = any(_is_live_where(x, registered_mods) for x in where)
        if live:
            live_totals[cat] += 1
        entries.append({
            "path": path,
            "norm": np,
            "methods": sorted(meta.get("methods") or []),
            "category": cat,
            "live": live,
            "ui_refs": ui,
            "non_ui_refs": sorted(set(cli) | set(rt))[:12],
            "where": where[:4],
        })

    live_n = sum(1 for e in entries if e["live"])
    totals = {"routes": len(entries), "live": live_n, "dead_copy": len(entries) - live_n,
              "ui": 0, "cli_script": 0, "runtime_only": 0, "unreferenced": 0}
    for e in entries:
        totals[e["category"]] += 1
    totals["live_by_category"] = live_totals

    route_norms = {e["norm"] for e in entries}
    unmatched = sorted(p for p in ui_refs if p not in route_norms)
    payload = {
        "generated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "totals": totals,
        "frontend_endpoints": len(ui_refs),
        "frontend_unmatched": unmatched[:50],
        "entries": entries,
    }
    payload["content_hash"] = _content_hash(payload)
    return payload


def _content_hash(payload: dict) -> str:
    """只对**分类结论**取哈希，不含「谁引用了它」的文件清单。

    【为什么】refs 里的文件名会随任何无关编辑漂移（改注释、挪文件都会变），
    若把它们算进去，--check 会天天假红，门禁随即被无视（本仓 M-35 的教训）。
    本哈希锁的是：每条路由的 path/methods/分类/活体，加上前端端点数与未匹配项。
    """
    body = {
        "totals": {k: v for k, v in payload["totals"].items() if k != "live_by_category"},
        "frontend_endpoints": payload["frontend_endpoints"],
        "frontend_unmatched": payload["frontend_unmatched"],
        "entries": [
            {"path": e["path"], "methods": e["methods"],
             "category": e["category"], "live": e["live"]}
            for e in payload["entries"]
        ],
    }
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return sha256(raw).hexdigest()[:16]


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _summary(payload: dict) -> str:
    t = payload["totals"]
    lines = [
        "== 路由 ↔ UI 覆盖盘点 ==",
        "路由总数（静态）  : " + str(t["routes"]),
        "  其中活体        : " + str(t["live"]),
        "  其中死副本      : " + str(t["dead_copy"]),
        "  ui（前端引用）  : " + str(t["ui"]),
        "  cli_script      : " + str(t["cli_script"]),
        "  runtime_only    : " + str(t["runtime_only"]),
        "  unreferenced    : " + str(t["unreferenced"]),
        "活体按分类        : " + json.dumps(t.get("live_by_category", {}), ensure_ascii=False),
        "前端端点（去重）  : " + str(payload["frontend_endpoints"]),
        "前端引用但无路由  : " + str(len(payload["frontend_unmatched"])),
        "content_hash      : " + payload["content_hash"],
    ]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="路由 ↔ UI 覆盖盘点")
    ap.add_argument("--json-out", default=str(DEFAULT_JSON))
    ap.add_argument("--check", action="store_true",
                    help="只校验：把重算的 content_hash 与已提交产物比对，漂移则非零退出")
    args = ap.parse_args(argv)

    payload = classify()
    print(_summary(payload))

    if args.check:
        target = Path(args.json_out)
        if not target.exists():
            print("[ERR] 产物不存在：" + str(target) + "（先运行不带 --check 的生成）", file=sys.stderr)
            return 2
        old = json.loads(target.read_text(encoding="utf-8"))
        if old.get("content_hash") != payload["content_hash"]:
            print("[ERR] 路由↔UI 分类已漂移：提交产物 hash=" + str(old.get("content_hash"))
                  + " 重算 hash=" + payload["content_hash"], file=sys.stderr)
            print("      处理：运行 python scripts/audit/route_ui_coverage.py 重新派生并提交产物", file=sys.stderr)
            return 1
        print("[OK] 与提交产物一致")
        return 0

    _write(Path(args.json_out), payload)
    print("[OK] 已写入 " + args.json_out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
