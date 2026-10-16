# -*- coding: utf-8 -*-
"""路由 ↔ UI 覆盖盘点（2026-10-07）。

【解决什么】本仓后端路由（静态口径实测 450 条）与前端 UI 之间没有一张「谁呈现到界面」的
总账：改一个端点、加一个插件，没人能一眼答出「它现在有没有 UI 入口」。
本脚本按静态可得的两侧数据做**机械分类**，产出一份可提交、可 diff、可上屏的清单：

  · ui           ：被前端源码引用（yunshu-ui/src 的常量层 + 页面字面量，先剥注释）；
  · cli_script   ：只在 CLI / 脚本 / CI 工作流里出现（scripts/tools/.github/main.py/*.ps1）；
  · runtime_only ：只在 app_server / agent 的非路由模块里出现（进程内 provider、探针、看门狗）；
  · unreferenced ：以上都没找到引用（可能是纯内部、已废弃，或由动态拼接调用）。

  另注「活体 / 死副本」（两个**正交**的事实）：
    · 活体 = 该路径至少有一个定义文件已接线，线上会被服务；
    · 死副本 = 该路径**还存在**未接线的 agent/server_routes/*.py 定义
      （见 tests/unit/test_server_routes_registration_inventory.py 的 KNOWN_UNREGISTERED），
      改那一条不生效、也不报错 —— 在 dead_defs 里记下未接线的定义位点。
  判据与 docs/closeout/契约治理重构_第五轮_P1front推进_20261005.md §4.3 的
  「路径 → 活体 → 死副本」表同源。

  另附每条的 description：处理函数 docstring 首个非空行（"这个路由做什么"）。**不进 content_hash**：
  说明文字随注释编辑漂移，算进门禁会让 --check 为改一句文档假红（与 refs 同理）。

【口径与边界（如实标注，不假装穷尽）】
  1. 路由与前端字面量分别复用 scripts.audit.contract_diff 的 collect_routes_static 与
     collect_frontend_literals（后者已先剥注释、排除构建产物与 mock）；
  2. 分类判据是**路径归一化后的精确匹配**：Flask 的 <int:x>/<x> 与 JS 的 ${x} 都归一成 *；
     不做「前缀包含」式匹配 —— 那会把 /api/skills 与 /api/skills-mgmt 混为一谈（假绿）；
  3. runtime_only / cli_script 的扫描会让**注释里提到端点的行**也计入 refs；清单里带 refs
     供人工复核。这是「宁可多留不可少留」口径，**不用于门禁**：门禁只钉 content_hash 是否
     与提交产物一致（排除 generated_at）。
  4. 【数字口径：勿与 url_map 规则数混淆】routes / live 数的是**归一化路径**去重
     （<id>、<int:id>、${x} 都折成 *），不是运行期 url_map 的规则条数 —— 同一路径的
     GET/POST/PUT 是 3 条规则、1 条路径；扫描范围是 agent/plugins/sensor/cognitive/memory
     的静态源码，因此**包含其它 Flask app**（如 sensor_server 的 cognitive 端点）的装饰器，
     也**不含**动态注册（如 /api/open/*）与 Flask 静态路由。判活/死副本仍只看「定义所在
     模块是否已接线」。2026-10-16 实测：本脚本 461 条 vs workbench url_map 459 个归一化
     路径（506 条规则）。此说明是**文案**，不进 content_hash。
"""
from __future__ import annotations

import argparse
import ast
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
#: 人工意图标注（该进 UI / 不该进）；未标注 = unclassified
INTENT_FILE = ROOT / "config" / "route_ui_intent.json"
INTENT_VALUES = {"should_be_in_ui", "should_not_be_in_ui"}
_UNCLASSIFIED = "unclassified"

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


def _is_live_where(where: str, registered_mods: set) -> bool:
    """一个 where 定义位点（形如 "agent/server_routes/routes_x.py:123"）是否已接线。

    【必须先剥 ":行号" —— 历史缺陷】collect_routes_static 往 where 里拼了 ":行号"
    （contract_diff.py:245）。旧实现直接拿带行号的串取模块名，得到 "routes_x.py:123"，
    永远命不中 registered_mods ⇒ 所有 agent/server_routes 的定义一律被判死，
    dead_copy 虚高（实测 457 条路由里 223 条假死）。回归见
    tests/unit/test_route_ui_coverage.py::Test活体判定。
    """
    rel = str(where).replace("\\", "/")
    rel = rel.rsplit(":", 1)[0]  # where 一律是仓库相对路径 + ":行号"
    if rel.startswith("plugins/") or rel in ("app_server.py", "main.py"):
        return True
    if rel.startswith("agent/server_routes/"):
        mod = Path(rel).name[:-3] if rel.endswith(".py") else Path(rel).name
        return mod in registered_mods
    return True  # sensor/ / memory/ / cognitive/ / agent 其它：非 server_routes，默认按活体


#: 每个路由「做什么」的**简短说明**来源：处理函数 docstring 的首个非空行。
#  【为什么不进 content_hash】说明文字会随注释编辑漂移（重写一句话、翻译、纠正错别字），
#  若算进门禁，--check 会为「改了一句文档」天天假红 —— 与 refs 的取舍同理（见 _content_hash）。
#  【为什么不改 contract_diff.collect_routes_static】那里是路由**分类**的单一来源，
#  被 contract-gate 等 6 处消费；说明是呈现层附加物，就地按 where 的 file:line 反查更安全。
_ROUTE_METHODS = {"route", "get", "post", "put", "delete", "patch"}
_DOC_CACHE: dict = {}


def _first_doc_line(doc) -> str:
    """取 docstring 的首个非空行（"简短说明"），压平空白并截断。"""
    if not doc:
        return ""
    for raw in str(doc).splitlines():
        line = " ".join(raw.split())
        if line:
            return line[:120]
    return ""


def _doc_index(rel: str) -> dict:
    """文件 -> {路由装饰器行号: 处理函数首行说明}（按需解析并缓存）。"""
    cached = _DOC_CACHE.get(rel)
    if cached is not None:
        return cached
    idx: dict = {}
    try:
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError):
        _DOC_CACHE[rel] = idx
        return idx
    # 模块级兜底：处理函数没写 docstring 时，至少给出「这个模块管什么」
    idx["__module__"] = _first_doc_line(ast.get_docstring(tree))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        first = _first_doc_line(ast.get_docstring(node))
        if not first:
            continue
        for dec in node.decorator_list:
            if (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                    and dec.func.attr.lower() in _ROUTE_METHODS):
                idx[int(dec.lineno)] = first
    _DOC_CACHE[rel] = idx
    return idx


def _route_doc(where):
    """按 where 的 file:line 反查说明，返回 (文本, 来源)。

    来源取 "handler"（处理函数 docstring）优先；否则退到 "module"（模块 docstring 首行，
    如实标注为模块级、非该路由专述）；都没有则 ("", "")。
    """
    pairs = []
    for w in where:
        rel, _, line = str(w).rpartition(":")
        if rel and line.isdigit():
            pairs.append((rel.replace("\\", "/"), int(line)))
    for rel, line in pairs:
        doc = _doc_index(rel).get(line, "")
        if doc:
            return doc, "handler"
    for rel, _line in pairs:
        doc = _doc_index(rel).get("__module__", "")
        if doc:
            return doc, "module"
    return "", ""


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


#: 客户端 baseURL=/api 的相对字面量所在目录（collect_frontend_literals 只认 "/api/" 字面量）
_API_CLIENT_DIR = "yunshu-ui/src/api"
_RELATIVE_API_LITERAL = re.compile(r"""['"](/[A-Za-z0-9_./{}<>:$%-]+)['"]""")


def _refs_from_api_client_dir() -> dict:
    """src/api/ 下的相对 URL 字面量（客户端 baseURL=/api）⇒ 登记 /api 前缀变体。

    【修一个真实盲区】collect_frontend_literals 的正则只认**已带 /api 前缀**的字面量，
    于是 request({url: '/dashboard/summary'}) 这类经 baseURL 拼接的调用被算成
    unreferenced（实测 /api/dashboard/summary 被误判）。只扫 src/api/ 目录，避免把
    普通相对路径（如前端路由 /login）误当 API。
    """
    extra: dict = {}
    for f in _iter_files({".ts", ".tsx"}, [_API_CLIENT_DIR]):
        try:
            txt = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = str(f.relative_to(ROOT)).replace("\\", "/")
        for m in _RELATIVE_API_LITERAL.finditer(txt):
            token = norm(m.group(1))
            if not token or token == "/" or token.startswith("/api/"):
                continue
            extra.setdefault("/api" + token, set()).add(rel)
    return extra


def _refs_from_frontend(fe):
    refs: dict = {}
    for label in ("react", "legacy"):
        for lit, files in (fe.get(label) or {}).items():
            refs.setdefault(norm(lit), set()).update(files)
    for lit, files in _refs_from_api_client_dir().items():
        refs.setdefault(lit, set()).update(files)
    return refs


def _load_intents() -> dict:
    """读取人工意图标注 config/route_ui_intent.json。

    【fail-closed】文件缺失 / 解析失败 / 结构不符 ⇒ 返回空表（全部按 unclassified 处理），
    不抛错、也**不假装**有标注：本文件是增量的人工判断，缺了它脚本仍按原机械分类工作。
    """
    if not INTENT_FILE.exists():
        return {}
    try:
        data = json.loads(INTENT_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print("[WARN] 意图标注文件无法解析：" + str(INTENT_FILE) + " -> " + str(exc), file=sys.stderr)
        return {}
    raw = data.get("intents") if isinstance(data, dict) else None
    if not isinstance(raw, dict):
        return {}
    out: dict = {}
    for key, value in raw.items():
        if isinstance(value, str):
            intent, area, note = value, "", ""
        elif isinstance(value, dict):
            intent = value.get("intent", "")
            area = value.get("area", "")
            note = value.get("note", "")
            area = area if isinstance(area, str) else ""
            note = note if isinstance(note, str) else ""
        else:
            continue
        if intent in INTENT_VALUES:
            out[norm(str(key))] = {"intent": intent, "area": area, "note": note}
    return out


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
    dead_def_total = 0
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
        # 活体（线上会被服务）与死副本（有未接线定义）是**两个正交事实**：
        # 同一路径可能「活体在 plugins/x.py、死副本在未接线的 agent/server_routes/y.py」，
        # 此时它既 live、又 dead_copy —— 早年判活只认 where 里有没有已接线文件，
        # 却把死副本完全藏掉；本报告要的正是把这条同时标出来。判据见
        # docs/closeout/契约治理重构_第五轮_P1front推进_20261005.md §4.3 的
        # 「路径 → 活体 → 死副本」表；未接线模块清单见
        # tests/unit/test_server_routes_registration_inventory.py::KNOWN_UNREGISTERED。
        wired = [_is_live_where(x, registered_mods) for x in where]
        live = any(wired)                       # 有任一已接线定义 ⇒ 线上会被服务
        dead_defs = [x for x, ok in zip(where, wired) if not ok]  # 未接线定义 = 死副本
        if live:
            live_totals[cat] += 1
        dead_def_total += len(dead_defs)
        desc, desc_src = _route_doc(where)
        entries.append({
            "path": path,
            "norm": np,
            "methods": sorted(meta.get("methods") or []),
            "category": cat,
            "live": live,
            # 该路径是否存在未接线定义（死副本）；与 live 正交，见上
            "dead_copy": bool(dead_defs),
            # 未接线的定义位点（死副本）。只放前 4 条、**不进 content_hash**（与 where/refs 同理）
            "dead_defs": dead_defs[:4],
            "ui_refs": ui,
            "non_ui_refs": sorted(set(cli) | set(rt))[:12],
            "where": where[:4],
            # 一句话说明；**不进 content_hash**，见 _first_doc_line 说明
            "description": desc,
            "description_source": desc_src,
        })

    # 人工意图标注：只**增**字段，不改机械分类；ui_pending = 该进 UI 却没进
    intents = _load_intents()
    for e in entries:
        meta = intents.get(e["norm"])
        e["intent"] = meta["intent"] if meta else _UNCLASSIFIED
        e["intent_area"] = meta["area"] if meta else ""
        e["intent_note"] = meta["note"] if meta else ""
        e["ui_pending"] = bool(e["intent"] == "should_be_in_ui" and e["live"] and e["category"] != "ui")

    live_n = sum(1 for e in entries if e["live"])
    dead_copy_n = sum(1 for e in entries if e["dead_copy"])
    totals = {"routes": len(entries), "live": live_n, "dead_copy": dead_copy_n,
              "dead_definitions": dead_def_total,
              "ui": 0, "cli_script": 0, "runtime_only": 0, "unreferenced": 0}
    for e in entries:
        totals[e["category"]] += 1
    totals["live_by_category"] = live_totals
    by_intent = {"should_be_in_ui": 0, "should_not_be_in_ui": 0, _UNCLASSIFIED: 0}
    for e in entries:
        if e["live"]:
            by_intent[e["intent"]] += 1
    totals["by_intent"] = by_intent

    route_norms = {e["norm"] for e in entries}
    unmatched = sorted(p for p in ui_refs if p not in route_norms)
    payload = {
        "generated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "totals": totals,
        "frontend_endpoints": len(ui_refs),
        "frontend_unmatched": unmatched[:50],
        "intent_file": str(INTENT_FILE.relative_to(ROOT)).replace("\\", "/"),
        "pending_ui": [
            {"path": e["path"], "norm": e["norm"], "methods": e["methods"],
             "category": e["category"], "area": e["intent_area"], "where": e["where"]}
            for e in entries if e["ui_pending"]
        ],
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
             "category": e["category"], "live": e["live"],
             "dead_copy": e["dead_copy"],
             "intent": e["intent"], "ui_pending": e["ui_pending"]}
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
        "  含死副本        : " + str(t["dead_copy"])
        + "（未接线定义位点 " + str(t.get("dead_definitions", 0)) + "）",
        "  ui（前端引用）  : " + str(t["ui"]),
        "  cli_script      : " + str(t["cli_script"]),
        "  runtime_only    : " + str(t["runtime_only"]),
        "  unreferenced    : " + str(t["unreferenced"]),
        "活体按分类        : " + json.dumps(t.get("live_by_category", {}), ensure_ascii=False),
        "意图（活体）      : 该进 " + str(t.get("by_intent", {}).get("should_be_in_ui", 0))
        + " / 不该进 " + str(t.get("by_intent", {}).get("should_not_be_in_ui", 0))
        + " / 未标注 " + str(t.get("by_intent", {}).get("unclassified", 0)),
        "该进却未进 UI     : " + str(len(payload.get("pending_ui", []))),
        "前端端点（去重）  : " + str(payload["frontend_endpoints"]),
        "前端引用但无路由  : " + str(len(payload["frontend_unmatched"])),
        "口径              : 归一化路径去重（非 url_map 规则数）；含其它 Flask app 的装饰器，不含动态注册/静态路由",
        "content_hash      : " + payload["content_hash"],
    ]
    return "\n".join(lines)


def _force_utf8_stdio() -> None:
    """把 stdout/stderr 切到 UTF-8。

    【解决什么】本脚本会打印 `↔`（U+2194）等**不在 GBK/cp936 里**的字符；在中文
    Windows 上以管道/重定向方式运行（如 CI 工具/harness 捕获输出）时，Python 默认按
    locale（cp936）编码 stdout ⇒ `UnicodeEncodeError: 'gbk' codec can't encode
    character '\u2194'`，`--check` 在**打印摘要**时就崩，看起来像门禁红
    （2026-10-16 实测：交接稿命令 `python scripts/audit/route_ui_coverage.py --check`
    原样执行必崩，必须显式 `PYTHONUTF8=1` 才过）。
    reconfigure 不可用（非 TextIOWrapper / 老 Python）时静默跳过，不阻断功能。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def main(argv=None) -> int:
    _force_utf8_stdio()
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
