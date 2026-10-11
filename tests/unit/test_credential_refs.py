# -*- coding: utf-8 -*-
"""引用式密钥桥：bundle 的 secrets.refs → 到达端 TTL 凭据（只搬引用，不搬值）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from agent.subagent.bundle import (BundleValidationError, build_bundle,
                                   import_config, validate_bundle)
from agent.subagent.credentials import (
    TemporaryCredentialManager, credential_scope, refs_to_specs,
)
from agent.subagent.container import SubagentConfig


def test_refs_to_specs_resolves_from_arrival_env():
    refs = [{"source": "openai", "name": "API_KEY", "env_var": "OPENAI_API_KEY"}]
    specs = refs_to_specs(refs, environ={"OPENAI_API_KEY": "sk-live"})
    assert specs == [{"name": "API_KEY", "value": "sk-live", "source": "openai",
                      "env_var": "OPENAI_API_KEY"}]


def test_refs_to_specs_skips_missing_value():
    assert refs_to_specs([{"source": "s", "name": "N", "env_var": "NOPE"}], environ={}) == []


def test_refs_to_specs_derives_env_var_when_absent():
    specs = refs_to_specs([{"source": "mcp:github", "name": "TOKEN"}],
                          environ={"CP_TEMP_MCP_GITHUB_TOKEN": "v"})
    assert specs and specs[0]["env_var"] == "CP_TEMP_MCP_GITHUB_TOKEN"


def test_refs_to_specs_ignores_non_mapping():
    assert refs_to_specs(["nope", {"name": ""}], environ={"x": "1"}) == []


def test_issue_honours_ref_env_var():
    mgr = TemporaryCredentialManager()
    cred = mgr.issue("API_KEY", "v", source="openai", task_timeout_seconds=60,
                     env_var="OPENAI_API_KEY")
    assert cred.env_var == "OPENAI_API_KEY"


def test_credential_scope_keeps_ref_env_var_and_destroys():
    mgr = TemporaryCredentialManager()
    with credential_scope(mgr, [{"name": "API_KEY", "value": "v", "source": "openai",
                                 "env_var": "OPENAI_API_KEY"}],
                          task_timeout_seconds=60) as creds:
        assert creds[0].env_var == "OPENAI_API_KEY"
    assert creds[0].destroyed is True


def test_import_config_carries_refs_without_values():
    bundle = build_bundle(SubagentConfig(name="x", model_id="m"),
                          secret_refs=[{"source": "env", "name": "K",
                                        "env_var": "CP_TEMP_K"}],
                          environment={"packages": [], "source_status": "ok"})
    cfg = import_config(bundle)
    assert cfg.secret_refs == ({"source": "env", "name": "K", "env_var": "CP_TEMP_K"},)
    assert "value" not in json.dumps(list(cfg.secret_refs))


def test_bundle_rejects_fourth_key_in_refs():
    # 造包阶段就拒：refs 只允许三键，第四键（value）一律视为存值
    with pytest.raises(BundleValidationError) as ei:
        build_bundle(SubagentConfig(name="x", model_id="m"),
                     secret_refs=[{"source": "env", "name": "K",
                                   "env_var": "CP_TEMP_K", "value": "leak"}])
    assert "value" in str(ei.value)
