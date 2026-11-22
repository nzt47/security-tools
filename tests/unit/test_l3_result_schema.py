"""L3 高风险工具的输出契约（P2 result_schema 扩面）。"""
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
L3_TOOLS = [
    "connect_mcp", "disconnect_mcp", "ext_configure", "ext_install",
    "ext_toggle", "ext_uninstall", "generate_tool", "run_sandbox",
    "scan_mcp", "shell_execute",
]


def test_l3_tools_declare_result_schema():
    for name in L3_TOOLS:
        doc = yaml.safe_load((ROOT / "data" / "tool_definitions" / f"{name}.yaml")
                             .read_text(encoding="utf-8"))
        schema = doc.get("result_schema")
        assert isinstance(schema, dict), f"{name} 缺少 result_schema"
        assert schema.get("type") == "object", name
        assert "ok" in (schema.get("required") or []), name
        assert schema.get("additionalProperties") is True, name
