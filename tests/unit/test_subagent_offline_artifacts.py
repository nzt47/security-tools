"""离线打包物（wheelhouse）与裁剪脱敏守卫（dependencies.py + scripts/ 两个入口）

【为什么有这份守卫（不这样会怎样）】
    portability 此前的 `offline_ready` 恒 false（未 vendoring wheel）——"可带走"只是
    一句口号。本增量加了 wheelhouse 扫描 / 覆盖判定 / 离线包构建入口；这类"就绪判定"
    最容易退化成**数量在说话**（artifacts.count>0 就 true）。守卫把口径钉死：

      A1 扫描：目录空/不存在 ⇒ mode=none（不谎报有打包物）；有 .whl ⇒ 逐文件 sha256 +
         规范分发名 + 整包 digest（换/少一个 wheel 即变）；清单优先（不重算大 wheel）。
      A2 覆盖：offline_ready 必须"wheelhouse 覆盖**全部**声明依赖"才真；少一个即 false；
         items 被裁掉（无清单）时不得凭空判 true。
      A3 入口：build_offline_pack 的 --skip-download 路径能产出自包含包并把 offline_ready
         翻真；trim_bundle 丢掉密钥引用/依赖版本/metadata，且清 items 时把 offline_ready
         同步置 false。
      A4 契约：validate_environment 认识 artifacts；check_against_bundle 的 offline_ready
         是"包自己的声明"，不再恒 false。

不 import app_server；不联网（--skip-download / 纯函数）。
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from agent.subagent import dependencies as dep
from agent.subagent.bundle import build_bundle, bundle_to_json, validate_bundle
from agent.subagent.container import SubagentConfig

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_script(filename):
    path = _REPO_ROOT / "scripts" / filename
    spec = importlib.util.spec_from_file_location(filename[:-3], path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


trim_script = _load_script("trim_bundle.py")


def _env(declared, installed, *, running="3.12.0", expected=">=3.11", artifacts=None):
    return dep.build_dependency_manifest(
        declared=declared, installed=installed, expected_python=expected,
        running_python=running, captured_at="2026-10-10T00:00:00+00:00",
        artifacts=artifacts)


def _wheelhouse(tmp_path, names):
    root = tmp_path / "wheelhouse"
    root.mkdir(parents=True, exist_ok=True)
    for name in names:
        (root / (name + "-1.0.0-py3-none-any.whl")).write_bytes(b"dummy-wheel")
    return root


def _bundle(tmp_path, environment):
    cfg = SubagentConfig(name="sa-offline", model_id="m")
    return build_bundle(cfg, backend="inproc", generated_at="2026-10-10T00:00:00+00:00",
                        bundle_id="bnd-offline", environment=environment)


class TestA1Scan:
    def test_目录缺失或空即_none(self, tmp_path):
        assert dep.scan_wheelhouse("")["mode"] == dep.ARTIFACTS_MODE_NONE
        assert dep.scan_wheelhouse(str(tmp_path / "nope"))["mode"] == dep.ARTIFACTS_MODE_NONE
        empty = tmp_path / "empty"
        empty.mkdir()
        assert dep.scan_wheelhouse(str(empty))["mode"] == dep.ARTIFACTS_MODE_NONE

    def test_有_wheel_即逐文件摘要(self, tmp_path):
        root = _wheelhouse(tmp_path, ["numpy", "packaging"])
        art = dep.scan_wheelhouse(str(root))
        assert art["mode"] == dep.ARTIFACTS_MODE_WHEELHOUSE
        assert art["count"] == 2 and art["total_bytes"] > 0
        assert len(art["digest"]) == 64
        dists = {f["distribution"] for f in art["files"]}
        assert dists == {"numpy", "packaging"}
        assert all(len(f["sha256"]) == 64 for f in art["files"])

    def test_digest_随内容变化(self, tmp_path):
        root = _wheelhouse(tmp_path, ["a", "b"])
        first = dep.scan_wheelhouse(str(root))["digest"]
        (root / "a-1.0.0-py3-none-any.whl").write_bytes(b"changed")
        assert dep.scan_wheelhouse(str(root))["digest"] != first

    def test_清单优先且可写出(self, tmp_path):
        root = _wheelhouse(tmp_path, ["numpy"])
        art = dep.scan_wheelhouse(str(root), write_manifest=True)
        assert dep.wheelhouse_manifest_path(str(root)).endswith(
            dep.WHEELHOUSE_MANIFEST_NAME)
        assert Path(dep.wheelhouse_manifest_path(str(root))).is_file()
        doc = json.loads(Path(dep.wheelhouse_manifest_path(str(root))).read_text(
            encoding="utf-8"))
        assert doc["files"][0]["name"] == "numpy-1.0.0-py3-none-any.whl"
        assert dep.scan_wheelhouse(str(root))["digest"] == art["digest"]

    def test_分发名归一化(self):
        assert dep.wheel_distribution("numpy-1.0.0-py3-none-any.whl") == "numpy"
        assert dep.wheel_distribution("Memory_Optimized-2.0-py3-none-any.whl") == (
            "memory-optimized")
        assert dep.wheel_distribution("not-a-wheel.txt") == ""


class TestA2Coverage:
    def _manifest(self, tmp_path, covered, declared):
        art = dep.scan_wheelhouse(str(_wheelhouse(tmp_path, covered)))
        installed = {dep.canonical_name(n): "1.0.0" for n in declared}
        return _env([{"name": n, "specifier": ""} for n in declared], installed,
                    artifacts=art)

    def test_全覆盖才为真(self, tmp_path):
        m = self._manifest(tmp_path, ["numpy", "packaging"], ["numpy", "packaging"])
        assert dep.offline_ready(m) is True

    def test_少一个即假(self, tmp_path):
        m = self._manifest(tmp_path, ["numpy"], ["numpy", "packaging"])
        assert dep.offline_ready(m) is False, "带一个 wheel 不等于能离线装齐"

    def test_mode_none_恒假(self, tmp_path):
        m = self._manifest(tmp_path, [], ["numpy"])
        assert dep.offline_ready(m) is False

    def test_items_被裁掉不得蒙混(self, tmp_path):
        m = self._manifest(tmp_path, ["numpy"], ["numpy"])
        m["items"] = []
        assert dep.offline_ready(m) is False, "无依赖清单无法复核覆盖，不能判 true"

    def test_python_不匹配即假(self, tmp_path):
        art = dep.scan_wheelhouse(str(_wheelhouse(tmp_path, ["numpy"])))
        m = _env([{"name": "numpy", "specifier": ""}], {"numpy": "1.0.0"},
                 running="3.9.0", expected=">=3.11", artifacts=art)
        assert dep.offline_ready(m) is False


class TestA4Contract:
    def test_校验认识_artifacts(self):
        bad = {"source_status": "ok", "offline_ready": False,
               "artifacts": {"mode": "tarball"}}
        problems = dep.validate_environment(bad)
        assert any("artifacts.mode" in p for p in problems)
        good = {"source_status": "ok", "offline_ready": False,
                "artifacts": {"mode": "wheelhouse", "files": []}}
        assert dep.validate_environment(good) == []

    def test_check_against_bundle_回显声明(self):
        env = {"items": [], "python": {}, "offline_ready": True}
        check = dep.check_against_bundle(env)
        assert check["offline_ready"] is True
        assert check["satisfied"] is True

    def test_wheel_specs_构造与过滤(self):
        env = {"items": [{"name": "numpy", "required": ">=1.0"},
                          {"name": "packaging", "required": ""},
                          {"name": "", "required": ""},
                          "bad"]}
        assert dep.wheel_specs_from_environment(env) == ["numpy>=1.0", "packaging"]
        assert dep.wheel_specs_from_environment(env, only=["numpy"]) == ["numpy>=1.0"]


class TestA3Entries:
    def test_apply_artifacts_不改入参且翻真(self, tmp_path):
        art = dep.scan_wheelhouse(str(_wheelhouse(tmp_path, ["numpy"])))
        bundle = _bundle(tmp_path, _env([{"name": "numpy", "specifier": ""}],
                                        {"numpy": "1.0.0"}))
        packed = dep.apply_artifacts_to_bundle(bundle, art)
        assert bundle["environment"]["offline_ready"] is False, "不得改传入对象"
        assert packed["environment"]["offline_ready"] is True
        assert packed["environment"]["artifacts"]["mode"] == dep.ARTIFACTS_MODE_WHEELHOUSE
        assert validate_bundle(packed) == []

    def test_离线包_skip_download_端到端(self, tmp_path):
        env = _env([{"name": "numpy", "specifier": ""}], {"numpy": "1.0.0"})
        bundle = _bundle(tmp_path, env)
        bundle_path = tmp_path / "bundle.json"
        bundle_path.write_text(bundle_to_json(bundle), encoding="utf-8")
        dest = tmp_path / "out_pack"
        # 预置 wheelhouse（模拟已下载），脚本只扫描不打网络
        wh = dest / "wheelhouse"
        wh.mkdir(parents=True, exist_ok=True)
        (wh / "numpy-1.0.0-py3-none-any.whl").write_bytes(b"dummy")
        proc = subprocess.run(
            [sys.executable, str(_REPO_ROOT / "scripts" / "build_offline_pack.py"),
             "--bundle", str(bundle_path), "--dest", str(dest), "--skip-download"],
            capture_output=True, text=True, timeout=180)
        assert proc.returncode == 0, proc.stderr[-1500:]
        out = json.loads((dest / "bundle.json").read_text(encoding="utf-8"))
        assert out["environment"]["offline_ready"] is True
        assert out["environment"]["artifacts"]["mode"] == dep.ARTIFACTS_MODE_WHEELHOUSE
        assert (tmp_path / "out_pack.tar.gz").is_file()

    def test_trim_丢三类且同步置假(self, tmp_path):
        art = dep.scan_wheelhouse(str(_wheelhouse(tmp_path, ["numpy"])))
        env = _env([{"name": "numpy", "specifier": ""}], {"numpy": "1.0.0"},
                   artifacts=art)
        bundle = _bundle(tmp_path, env)
        bundle["secrets"] = {"refs": [{"source": "env", "name": "K", "env_var": "X"}]}
        bundle["metadata"] = {"internal": "yes"}
        trimmed, report = trim_script.trim_bundle(bundle)
        assert trimmed["secrets"]["refs"] == []
        assert trimmed["environment"]["items"] == []
        assert "metadata" not in trimmed
        assert trimmed["environment"]["offline_ready"] is False
        assert report["refs_dropped"] == 1
        assert report["metadata_dropped"] is True
        assert validate_bundle(trimmed) == []

    def test_trim_可保留(self):
        bundle = {"secrets": {"refs": [{"source": "env", "name": "K", "env_var": "X"}]},
                  "metadata": {"a": 1}}
        kept, report = trim_script.trim_bundle(
            bundle, keep_secrets=True, keep_metadata=True)
        assert kept["secrets"]["refs"] == bundle["secrets"]["refs"]
        assert kept["metadata"] == {"a": 1}
        assert report["refs_dropped"] == 0
        # 不改传入对象
        assert bundle["secrets"]["refs"][0]["env_var"] == "X"

