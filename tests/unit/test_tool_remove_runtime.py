"""运行时登记的工具：**移除**（台账 + YAML + 实现 + 单测 + UI 开关 + 注册表）。

【锁死的口径】
  1. 只有 data/dynamic_tools.json 台账里的工具可移除；内建工具一律 builtin 拒绝
     （删了下次启动又回来，真删必须走 PR 改代码 + 重派生五处）。
  2. 默认 dry_run：只报告"将删什么"，不动文件。真删必须 confirm=true（双保险）。
  3. 删前先快照到 data/backups/；任一步失败按快照回滚并重新加载。
"""

from __future__ import annotations

import json

import pytest

from agent import tools as tools_mod
from agent.tools import persistence

pytestmark = [pytest.mark.unit, pytest.mark.p3]

_FAKE = "demo_runtime_tool"


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """把台账 / 工具目录 / UI 开关都指到 tmp_path（不碰真实运行期文件）"""
    custom = tmp_path / "custom"
    defs = tmp_path / "tool_definitions"
    tests_dir = tmp_path / "tests"
    for d in (custom, defs, tests_dir):
        d.mkdir(parents=True)
    ledger = tmp_path / "dynamic_tools.json"
    ledger.write_text(json.dumps({
        "version": 1,
        "tools": {_FAKE: {"name": _FAKE, "description": "演示", "source": "ingestion:ing_demo",
                          "module_path": str(custom / (_FAKE + ".py"))}},
    }, ensure_ascii=False), encoding="utf-8")
    (custom / (_FAKE + ".py")).write_text("# demo\ndef register_all():\n    pass\n", encoding="utf-8")
    (defs / (_FAKE + ".yaml")).write_text("name: " + _FAKE + "\n", encoding="utf-8")
    (tests_dir / ("test_" + _FAKE + ".py")).write_text("# demo\n", encoding="utf-8")
    cfg = tmp_path / "tools_config.json"
    cfg.write_text(json.dumps({"tool_states": {_FAKE: False}}), encoding="utf-8")
    manifest = tmp_path / "capability_manifest.json"
    manifest.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(persistence, "_index_path", str(ledger))
    monkeypatch.setattr(persistence, "CUSTOM_TOOLS_DIR", str(custom))
    monkeypatch.setattr(persistence, "TOOL_DEFS_DIR", str(defs))
    monkeypatch.setattr(persistence, "_tool_config_path", lambda: str(cfg))
    monkeypatch.setattr(persistence, "_manifest_path", lambda: str(manifest))
    # 单测文件路径按 _ROOT 拼，这里单独放一份让它也能被"列出并删除"
    monkeypatch.setattr(persistence, "runtime_tool_files",
                        lambda name: [str(p) for p in (custom / (name + ".py"),
                                                       defs / (name + ".yaml"),
                                                       tests_dir / ("test_" + name + ".py"))
                                      if p.is_file()])
    return {"tmp": tmp_path, "custom": custom, "defs": defs, "tests": tests_dir,
            "ledger": ledger, "cfg": cfg}


def test_builtin_tool_is_rejected(sandbox):
    report = persistence.remove_runtime_tool("read_file", dry_run=True)
    assert report["ok"] is False and report["reason"] == "builtin"
    assert "内建" in report["error"]


def test_dry_run_lists_files_without_touching_them(sandbox):
    report = persistence.remove_runtime_tool(_FAKE, dry_run=True)
    assert report["ok"] is True and report["dry_run"] is True
    assert report["in_manifest"] is False
    assert any(p.endswith(_FAKE + ".py") for p in report["files"])
    # 一个文件都没动
    assert (sandbox["custom"] / (_FAKE + ".py")).is_file()
    ledger = json.loads(sandbox["ledger"].read_text(encoding="utf-8"))
    assert _FAKE in ledger["tools"]


def test_confirm_flag_is_required(sandbox):
    report = persistence.remove_runtime_tool(_FAKE, dry_run=False, confirm=False)
    assert report["ok"] is False and report["reason"] == "need_confirm"
    assert (sandbox["custom"] / (_FAKE + ".py")).is_file()


def test_confirm_removes_files_ledger_and_state(sandbox):
    report = persistence.remove_runtime_tool(_FAKE, dry_run=False, confirm=True)
    assert report["ok"] is True and report["removed"] is True, report
    assert not (sandbox["custom"] / (_FAKE + ".py")).exists()
    assert not (sandbox["defs"] / (_FAKE + ".yaml")).exists()
    assert not (sandbox["tests"] / ("test_" + _FAKE + ".py")).exists()
    ledger = json.loads(sandbox["ledger"].read_text(encoding="utf-8"))
    assert _FAKE not in ledger["tools"]
    cfg = json.loads(sandbox["cfg"].read_text(encoding="utf-8"))
    assert _FAKE not in (cfg.get("tool_states") or {})
    # 快照还在（可人工回滚）
    assert report["backup"] and sandbox["ledger"].is_file()


def test_failure_rolls_back_to_original(sandbox, monkeypatch):
    def _boom(name):
        raise RuntimeError("registry down")
    monkeypatch.setattr(tools_mod, "unregister", _boom)
    report = persistence.remove_runtime_tool(_FAKE, dry_run=False, confirm=True)
    assert report["ok"] is False and report["reason"] == "remove_failed"
    # 回滚：文件与台账都回到原样
    assert (sandbox["custom"] / (_FAKE + ".py")).is_file()
    ledger = json.loads(sandbox["ledger"].read_text(encoding="utf-8"))
    assert _FAKE in ledger["tools"]
