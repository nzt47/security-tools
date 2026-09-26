"""DYNGATE1 守护测试: detect_dynamic_loads.py 的窄口径审计豁免

被豁免的是 agent/tools/persistence.py:380/383 这一对
(importlib.util.spec_from_file_location / module_from_spec)。

本文件同时证明两件事:
  (i)  豁免对**唯一已知合法调用点**生效 => 该处 HIGH=0
       (而且是降级为 MEDIUM, 发现并未从报告里消失);
  (ii) 豁免**没有被打宽**: 换文件 / 换函数 / 同函数里多写一次,
       一律仍然 HIGH。

【改前红 / 改后绿】
  改前 (HEAD 版扫描器): 本文件的 test_exemption_* 失败 (那两处仍是 HIGH)
  改后 (当前扫描器):     全绿
  可用环境变量 DYNGATE1_SCANNER_PATH 指向另一份扫描器副本来复现"改前红",
  例如把 git show HEAD:scripts/detect_dynamic_loads.py 落到临时目录后:
    $env:DYNGATE1_SCANNER_PATH="<tmp>\detect_dynamic_loads.py"
    python -m pytest tests/unit/test_dynamic_loads_high_exemption.py -q -p no:randomly --timeout=60
  命令与原始输出见 docs/audit_skill_governance/DYNGATE1.md。
"""
from __future__ import annotations

import ast
import importlib.util
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCANNER = REPO_ROOT / "scripts" / "detect_dynamic_loads.py"
SCANNER = Path(os.environ.get("DYNGATE1_SCANNER_PATH") or DEFAULT_SCANNER)

EXEMPT_FILE_REL = "agent/tools/persistence.py"
EXEMPT_FUNC = "_import_module_from_path"

#: 被豁免调用点的最小复刻 (路径实参是变量 path, 与生产代码同形)
EXEMPT_SHAPED = '''import importlib.util


def _import_module_from_path(path):
    spec = importlib.util.spec_from_file_location("m", path)
    module = importlib.util.module_from_spec(spec)
    return module
'''


@pytest.fixture(scope="module")
def scanner():
    """以模块身份加载被测扫描器 (dataclass 要求模块已注册进 sys.modules)"""
    spec = importlib.util.spec_from_file_location("dyn_scan_under_test", SCANNER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["dyn_scan_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


def _write(root: Path, rel: str, body: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def _scan(scanner_mod, root: Path, rel: str):
    return scanner_mod.scan_file(root / rel, root)


def _high(findings):
    return [f for f in findings if f.risk_level == "HIGH"]


def _run_cli(root: Path, *extra: str):
    """真跑 CLI (两种模式共用同一退出码逻辑), 返回 (returncode, stdout)"""
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    proc = subprocess.run(
        [sys.executable, str(SCANNER), "--root", str(root), *extra],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(REPO_ROOT), env=env, timeout=300,
    )
    return proc.returncode, proc.stdout


# ════════════════════════════════════════════════════════════
#  (i) 豁免生效: 那两处 HIGH 归零, 但发现仍在 (降级而非删除)
# ════════════════════════════════════════════════════════════

def test_exemption_downgrades_the_two_preexisting_high_findings(scanner):
    findings = _scan(scanner, REPO_ROOT, EXEMPT_FILE_REL)
    assert _high(findings) == [], (
        "persistence.py 仍有 HIGH: " + str([(f.line, f.function) for f in _high(findings)]))


def test_exempted_findings_are_still_reported_as_medium(scanner):
    """[不易] 豁免是"降级为 MEDIUM", 不是"删除" —— 证据必须留在报告里"""
    findings = _scan(scanner, REPO_ROOT, EXEMPT_FILE_REL)
    med = [f for f in findings if f.risk_level == "MEDIUM"]
    assert len(med) == 2, f"应有 2 条 MEDIUM(降级后), 实际 {len(med)}"
    assert {f.function.split(".")[-1] for f in med} == {
        "spec_from_file_location", "module_from_spec"}
    assert all(f.exempted_by == f"{EXEMPT_FILE_REL}:{EXEMPT_FUNC}" for f in med)
    assert all(f.exempted_by for f in med), "降级条目必须带 exempted_by 以便审计追溯"


def test_agent_scope_reports_zero_high(scanner):
    report = scanner.scan_directory(REPO_ROOT / "agent")
    assert report.high_risk == [], (
        "agent/ 仍有 HIGH: " + str([(f.file, f.line) for f in report.high_risk]))
    assert len(report.medium_risk) >= 2


def test_guard_is_sensitive_to_the_exemption(scanner, monkeypatch):
    """自证测试有效: 清空豁免表, 同样输入立刻回到 HIGH=2

    这条同时就是"改前"的等价复现 —— 没有豁免机制的扫描器 (HEAD 版) 正是在这里红。
    """
    monkeypatch.setattr(scanner, "AUDITED_DYNAMIC_LOAD_EXEMPTIONS", ())
    findings = _scan(scanner, REPO_ROOT, EXEMPT_FILE_REL)
    assert len(_high(findings)) == 2, "豁免表清空后应恢复 2 处 HIGH"


# ════════════════════════════════════════════════════════════
#  (ii) 豁免没被打宽: 换文件 / 换函数 / 超配额 → 仍然 HIGH
# ════════════════════════════════════════════════════════════

def test_exempt_match_is_exactly_the_registered_call_site(scanner, tmp_path, monkeypatch):
    """同一相对路径 + 同一函数名 → 命中豁免 (证明匹配键是 (文件, 函数))"""
    monkeypatch.setattr(scanner, "ROOT", tmp_path)
    _write(tmp_path, EXEMPT_FILE_REL, EXEMPT_SHAPED)
    findings = _scan(scanner, tmp_path, EXEMPT_FILE_REL)
    assert _high(findings) == []
    assert [f.risk_level for f in findings] == ["MEDIUM", "MEDIUM"]


def test_same_file_different_function_still_high(scanner, tmp_path, monkeypatch):
    """**不是文件级豁免**: 同文件同路径, 换个函数名 → 仍报 HIGH"""
    monkeypatch.setattr(scanner, "ROOT", tmp_path)
    _write(tmp_path, EXEMPT_FILE_REL, EXEMPT_SHAPED.replace(EXEMPT_FUNC, "_some_other_helper"))
    findings = _scan(scanner, tmp_path, EXEMPT_FILE_REL)
    assert len(_high(findings)) == 2, "同文件的其它函数不得被豁免"


def test_different_file_still_high(scanner, tmp_path, monkeypatch):
    """**不是目录级豁免**: 同函数名放到别的文件 → 仍报 HIGH"""
    monkeypatch.setattr(scanner, "ROOT", tmp_path)
    other = "agent/tools/another_module.py"
    _write(tmp_path, other, EXEMPT_SHAPED)
    findings = _scan(scanner, tmp_path, other)
    assert len(_high(findings)) == 2, "同目录的其它文件不得被豁免"


def test_extra_occurrence_in_same_function_stays_high(scanner, tmp_path, monkeypatch):
    """**配额约束**: 同文件同函数再写一个 spec_from_file_location → 多的那个仍 HIGH"""
    monkeypatch.setattr(scanner, "ROOT", tmp_path)
    body = '''import importlib.util


def _import_module_from_path(path):
    spec = importlib.util.spec_from_file_location("m", path)
    module = importlib.util.module_from_spec(spec)
    spec2 = importlib.util.spec_from_file_location("m2", path)
    return module
'''
    _write(tmp_path, EXEMPT_FILE_REL, body)
    findings = _scan(scanner, tmp_path, EXEMPT_FILE_REL)
    high = _high(findings)
    assert len(high) == 1, f"超配额的调用点必须保持 HIGH, 实际 {[(f.line, f.function) for f in high]}"
    assert high[0].function.split(".")[-1] == "spec_from_file_location"
    assert high[0].line == 7, "HIGH 的应当是多出来的第 2 个调用点"


def test_literal_missing_path_in_unregistered_file_still_high(scanner, tmp_path, monkeypatch):
    """规则本身没被削弱: 未登记文件里的常量路径(指向不存在的文件) 照旧 HIGH"""
    monkeypatch.setattr(scanner, "ROOT", tmp_path)
    body = '''import importlib.util


def _load_it():
    spec = importlib.util.spec_from_file_location("m", "does/not/exist.py")
    return spec
'''
    _write(tmp_path, "agent/tools/unregistered.py", body)
    findings = _scan(scanner, tmp_path, "agent/tools/unregistered.py")
    assert len(_high(findings)) == 1


def test_stale_exemption_is_warned(scanner, tmp_path, monkeypatch, caplog):
    """防腐: 登记了却没命中的豁免要告警 (重构把调用点挪走后不能静默失效)"""
    monkeypatch.setattr(scanner, "ROOT", tmp_path)
    _write(tmp_path, EXEMPT_FILE_REL, EXEMPT_SHAPED.replace(EXEMPT_FUNC, "_moved_elsewhere"))
    with caplog.at_level(logging.WARNING, logger="detect_dynamic_loads"):
        scanner.scan_directory(tmp_path)
    assert any("stale exemption" in r.getMessage() for r in caplog.records), \
        "未命中的豁免条目应产生 stale exemption 告警"


# ════════════════════════════════════════════════════════════
#  (ii-b) 把豁免依赖的调用链钉死 (残留风险: 若将来有人给被豁免函数
#         加第二个调用方, 豁免会顺带覆盖那个新入口 ⇒ 本测试必须红)
# ════════════════════════════════════════════════════════════

PERSISTENCE = REPO_ROOT / EXEMPT_FILE_REL


def _target_names(node):
    """取赋值/循环目标的标识符列表 (For.target 是单个节点, Assign.targets 是列表)"""
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, (ast.Tuple, ast.List)):
        return [e.id for e in node.elts if isinstance(e, ast.Name)]
    return []


def _owner_of(tree):
    """返回 lineno -> 最内层所在函数名 的映射函数"""
    funcs = [(n.lineno, n.end_lineno, n.name) for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    def owner(lineno):
        cands = [f for f in funcs if f[0] <= lineno <= f[1]]
        return min(cands, key=lambda f: f[1] - f[0])[2] if cands else "<module>"
    return owner


def test_exemption_anchor_call_chain_is_pinned(scanner):
    tree = ast.parse(PERSISTENCE.read_text(encoding="utf-8"))
    owner = _owner_of(tree)

    defs = [n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == EXEMPT_FUNC]
    assert len(defs) == 1, "被豁免函数应只有一个定义"
    assert [a.arg for a in defs[0].args.args] == ["path"], "豁免锚定的形参必须仍是 path"

    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and getattr(n.func, "id", "") == EXEMPT_FUNC]
    assert len(calls) == 1, (
        f"{EXEMPT_FUNC} 应恰好只有 1 个调用方; 新增调用方可能传入外部路径而"
        f"被同一豁免覆盖, 实际 {len(calls)}")
    call = calls[0]
    assert owner(call.lineno) == "load_dynamic_tools"
    assert isinstance(call.args[0], ast.Name) and call.args[0].id == "path"

    load_fn = next(n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == "load_dynamic_tools")
    loop_vars = [nm for n in ast.walk(load_fn) if isinstance(n, ast.For)
                 for nm in _target_names(n.target)]
    assert "path" in loop_vars, "实参 path 应来自 load_dynamic_tools 的 for 循环变量"
    assigned = {nm: n.value for n in ast.walk(load_fn) if isinstance(n, ast.Assign)
                for tgt in n.targets for nm in _target_names(tgt)}
    files_val = assigned.get("files")
    assert isinstance(files_val, ast.Call) and \
        getattr(files_val.func, "id", "") == "_iter_custom_modules"

    iter_fn = next(n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == "_iter_custom_modules")
    walks = [n for n in ast.walk(iter_fn) if isinstance(n, ast.Call)
             and getattr(n.func, "attr", "") == "walk"]
    assert len(walks) == 1
    assert getattr(walks[0].args[0], "id", "") == "CUSTOM_TOOLS_DIR", \
        "遍历目标必须仍是固定常量目录, 不能变成外部输入"

    consts = {t.id: n.value for n in tree.body if isinstance(n, ast.Assign)
              for t in n.targets if isinstance(t, ast.Name)}
    lits = [e.value for e in ast.walk(consts["CUSTOM_TOOLS_DIR"]) if isinstance(e, ast.Constant)]
    assert lits == ["agent", "tools", "custom"], f"受控目录常量被改动: {lits}"


# ════════════════════════════════════════════════════════════
#  (iii) 退出码: 文本模式与 --json 模式口径必须一致
# ════════════════════════════════════════════════════════════

def test_cli_agent_scope_exits_zero_in_both_modes(scanner):
    rc_text, _ = _run_cli(REPO_ROOT / "agent")
    rc_json, out_json = _run_cli(REPO_ROOT / "agent", "--json")
    assert rc_text == 0, "文本模式应退出 0 (无 HIGH)"
    assert rc_json == 0, "JSON 模式应退出 0 (无 HIGH)"
    assert json.loads(out_json)["high_risk_count"] == 0


def test_medium_does_not_affect_exit_code_in_either_mode(tmp_path):
    """MEDIUM 不参与退出码 (两种模式都一样) —— 证伪『JSON 把 MEDIUM 算进退出码』"""
    _write(tmp_path, "app/med.py", '__import__("os")\n')
    rc_text, out_text = _run_cli(tmp_path)
    rc_json, out_json = _run_cli(tmp_path, "--json")
    data = json.loads(out_json)
    assert data["medium_risk_count"] >= 1, "样例应至少产生 1 条 MEDIUM"
    assert data["high_risk_count"] == 0
    assert (rc_text, rc_json) == (0, 0), f"MEDIUM 不应改变退出码, 实际 {(rc_text, rc_json)}"


def test_high_makes_both_modes_exit_one(tmp_path):
    """有 HIGH 时两种模式必须同为 1 (退出码不因输出格式而异)"""
    _write(tmp_path, "app/high.py", EXEMPT_SHAPED)
    rc_text, _ = _run_cli(tmp_path)
    rc_json, out_json = _run_cli(tmp_path, "--json")
    assert json.loads(out_json)["high_risk_count"] == 2
    assert (rc_text, rc_json) == (1, 1), f"两种模式退出码应一致, 实际 {(rc_text, rc_json)}"
