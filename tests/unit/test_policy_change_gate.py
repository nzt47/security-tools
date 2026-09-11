"""TASK-S4-02 策略变更合入门禁单元测试（P7.2-19 落地检查）

门禁的价值全在「能不能拦住」：因此本模块的重点是**每条失败路径都要有用例**，
而不是只验证通过路径。覆盖：

  - 未改策略 ⇒ 跳过（不能对无关 PR 设闸）
  - 改了策略但没报告 ⇒ 拦
  - 报告 schema 不符 / 不存在 ⇒ 拦
  - 报告的候选策略与本次改动不对应 ⇒ 拦
  - 报告早于策略文件 ⇒ 拦
  - 高危变更没勾选 / 勾选数不足 / 未提及策略 id ⇒ 拦
  - 无样本未勾选声明 ⇒ 拦
  - 策略文件本身非法（含禁用 token）⇒ 拦
  - 签名模式写回后可通过验签
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from policy_testkit import isolate_policy, make_policy, write_policy_file

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import check_policy_change_gate as gate  # noqa: E402


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    return isolate_policy(tmp_path, monkeypatch)


def _policy_dir(tmp_path) -> Path:
    """策略文件必须落在 ``data/policies/`` 下才会被识别为「策略变更」

    （门禁的路径模式是 ``(^|/)data/policies/.*\\.json$``；用例若把文件写在
    ``tmp_path`` 根下，门禁会认为「本次未改动策略文件」而直接放行——那测不到东西。）
    """
    target = tmp_path / "data" / "policies"
    target.mkdir(parents=True, exist_ok=True)
    return target


def _policy_file(tmp_path, policies=None, name="policies.json"):
    return write_policy_file(str(_policy_dir(tmp_path) / name),
                             policies if policies is not None
                             else [make_policy(id="gate.cand")])


def _report_file(tmp_path, *, candidate_id="gate.cand", candidate_version="1.0.0",
                 high_risk=0, total=10, verdict="clean", name="sim.json",
                 shadow=None):
    body = {
        "schema": gate.REPORT_SCHEMA,
        "generated_at": "2026-09-11T10:00:00+08:00",
        "candidate": {"id": candidate_id, "version": candidate_version},
        "window": "7d", "window_days": 7.0,
        "totals": {"total": total, "unchanged": total - high_risk,
                   "deny_to_allow": high_risk, "allow_to_deny": 0,
                   "to_ask": 0, "ask_to_allow": 0, "ask_to_deny": 0,
                   "replay_drift": 0},
        "high_risk_hits": [
            {"kind": "deny_to_allow", "old_policy_id": "hist.deny",
             "new_policy_id": candidate_id, "old_effect": "deny",
             "new_effect": "allow", "capability_id": f"cp.demo.a{index}",
             "high_risk": True}
            for index in range(high_risk)],
        "changes": [], "by_capability": {}, "by_policy": {},
        "shadow": shadow or [],
        "verdict": verdict,
    }
    path = tmp_path / name
    path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    return str(path)


# ────────────────────────────────────────────────────────────
#  路径识别
# ────────────────────────────────────────────────────────────


class TestPolicyPathDetection:
    @pytest.mark.parametrize("path,expected", [
        ("data/policies/policies.json", True),
        ("data\\policies\\policies.json", True),
        ("./data/policies/candidates/x.json", True),
        ("agent/policy/store.py", False),
        ("data/permission_policies.json", False),
        ("docs/data/policies_notes.md", False),
    ])
    def test_识别策略文件(self, path, expected):
        assert gate.is_policy_path(path) is expected


# ────────────────────────────────────────────────────────────
#  未改动 ⇒ 跳过
# ────────────────────────────────────────────────────────────


class TestNoChange:
    def test_未改策略时跳过(self, tmp_path, capsys):
        assert gate.main(["--changed"]) == 0
        assert "门禁跳过" in capsys.readouterr().out

    def test_非策略路径被忽略(self, tmp_path, capsys):
        assert gate.main(["--changed", "agent/policy/store.py",
                          "tests/unit/test_policy_engine.py"]) == 0
        assert "门禁跳过" in capsys.readouterr().out


# ────────────────────────────────────────────────────────────
#  schema-only
# ────────────────────────────────────────────────────────────


class TestSchemaOnly:
    def test_合法文件通过(self, tmp_path, capsys):
        path = _policy_file(tmp_path)
        assert gate.main(["--schema-only", "--changed", path]) == 0
        assert "自检通过" in capsys.readouterr().out

    def test_非法策略被拦(self, tmp_path, capsys):
        path = _policy_file(tmp_path, [make_policy(id="bad.one", effect="permit")])
        assert gate.main(["--schema-only", "--changed", path]) == 1
        assert "FAIL" in capsys.readouterr().out

    def test_禁用_token_被拦(self, tmp_path):
        path = _policy_file(tmp_path, [make_policy(
            id="bad.token",
            match={"field": "attributes.x", "op": "eq", "value": "http.send"})])
        assert gate.main(["--schema-only", "--changed", path]) == 1

    def test_文件不存在被拦(self, tmp_path):
        assert gate.main(["--schema-only", "--changed",
                          str(_policy_dir(tmp_path) / "nope.json")]) == 1

    def test_非法_JSON_被拦(self, tmp_path):
        bad = _policy_dir(tmp_path) / "bad.json"
        bad.write_text("{oops", encoding="utf-8")
        assert gate.main(["--schema-only", "--changed", str(bad)]) == 1

    def test_空壳文件被拦(self, tmp_path):
        empty = _policy_dir(tmp_path) / "empty.json"
        empty.write_text(json.dumps({"schema": "policy.v1", "policies": []}),
                         encoding="utf-8")
        assert gate.main(["--schema-only", "--changed", str(empty)]) == 1

    def test_裸数组形态被接受(self, tmp_path):
        path = _policy_dir(tmp_path) / "arr.json"
        path.write_text(json.dumps([make_policy(id="arr.cand")]), encoding="utf-8")
        assert gate.main(["--schema-only", "--changed", str(path)]) == 0

    def test_强制签名时未签名被拦(self, tmp_path):
        path = _policy_file(tmp_path)
        assert gate.main(["--schema-only", "--require-signed",
                          "--changed", path]) == 1


# ────────────────────────────────────────────────────────────
#  报告门禁
# ────────────────────────────────────────────────────────────


class TestReportGate:
    def test_缺报告被拦(self, tmp_path, capsys):
        path = _policy_file(tmp_path)
        assert gate.main(["--changed", path]) == 1
        assert "必附模拟报告" in capsys.readouterr().out

    def test_报告不存在被拦(self, tmp_path):
        path = _policy_file(tmp_path)
        assert gate.main(["--changed", path,
                          "--report", str(_policy_dir(tmp_path) / "nope.json")]) == 1

    def test_schema_不符被拦(self, tmp_path, capsys):
        report = tmp_path / "old.json"
        report.write_text(json.dumps({"schema": "policy.simulation.v0"}),
                          encoding="utf-8")
        path = _policy_file(tmp_path)
        assert gate.main(["--changed", path, "--report", str(report)]) == 1
        assert "schema 不匹配" in capsys.readouterr().out

    def test_报告非_JSON_被拦(self, tmp_path):
        report = tmp_path / "broken.json"
        report.write_text("nope", encoding="utf-8")
        path = _policy_file(tmp_path)
        assert gate.main(["--changed", path, "--report", str(report)]) == 1

    def test_候选与改动不对应被拦(self, tmp_path, capsys):
        path = _policy_file(tmp_path, [make_policy(id="other.policy")])
        report = _report_file(tmp_path, candidate_id="gate.cand")
        assert gate.main(["--changed", path, "--report", report]) == 1
        assert "不在本次改动的策略文件中" in capsys.readouterr().out

    def test_候选版本过期被拦(self, tmp_path, capsys):
        path = _policy_file(tmp_path, [make_policy(id="gate.cand",
                                                   version="2.0.0")])
        report = _report_file(tmp_path, candidate_id="gate.cand",
                              candidate_version="1.0.0")
        assert gate.main(["--changed", path, "--report", report]) == 1
        assert "版本" in capsys.readouterr().out

    def test_报告早于策略文件被拦(self, tmp_path, capsys):
        path = _policy_file(tmp_path)
        report = _report_file(tmp_path)
        # 把报告时间戳退到策略文件之前（模拟"拿旧报告蒙混"）
        os.utime(report, (1_600_000_000, 1_600_000_000))
        assert gate.main(["--changed", path, "--report", report]) == 1
        assert "早于策略文件" in capsys.readouterr().out

    def test_无高危且无样本已声明时通过(self, tmp_path, capsys):
        path = _policy_file(tmp_path)
        report = _report_file(tmp_path, total=0, verdict="no_sample")
        body = tmp_path / "pr.md"
        body.write_text("## 无样本声明\n\n- [x] 本次变更无历史决策样本，未经模拟，"
                        "风险由人工承担\n", encoding="utf-8")
        assert gate.main(["--changed", path, "--report", report,
                          "--pr-body", str(body)]) == 0
        assert "门禁通过" in capsys.readouterr().out

    def test_有样本无高危时通过(self, tmp_path):
        path = _policy_file(tmp_path)
        report = _report_file(tmp_path, total=10, high_risk=0)
        assert gate.main(["--changed", path, "--report", report]) == 0

    def test_PR_描述文件不存在被拦(self, tmp_path):
        path = _policy_file(tmp_path)
        report = _report_file(tmp_path)
        assert gate.main(["--changed", path, "--report", report,
                          "--pr-body", str(tmp_path / "nope.md")]) == 1

    def test_裸数组策略文件也能对齐报告(self, tmp_path):
        path = _policy_dir(tmp_path) / "arr.json"
        path.write_text(json.dumps([make_policy(id="arr.cand")]), encoding="utf-8")
        report = _report_file(tmp_path, candidate_id="arr.cand")
        assert gate.main(["--changed", str(path), "--report", report]) == 0


# ────────────────────────────────────────────────────────────
#  高危确认
# ────────────────────────────────────────────────────────────


class TestHighRiskAcknowledgement:
    def _run(self, tmp_path, *, high_risk, body_text):
        path = _policy_file(tmp_path)
        report = _report_file(tmp_path, high_risk=high_risk,
                              verdict="needs_ack" if high_risk else "clean")
        body = tmp_path / "pr.md"
        body.write_text(body_text, encoding="utf-8")
        return gate.main(["--changed", path, "--report", report,
                          "--pr-body", str(body)])

    def test_高危未勾选被拦(self, tmp_path, capsys):
        code = self._run(tmp_path, high_risk=2,
                         body_text="## 高危确认\n\n- [ ] gate.cand cp.demo.a0 说明\n")
        assert code == 1
        assert "只勾选了" in capsys.readouterr().out

    def test_缺少确认段被拦(self, tmp_path, capsys):
        code = self._run(tmp_path, high_risk=1, body_text="无确认段\n")
        assert code == 1
        assert "必须包含" in capsys.readouterr().out

    def test_勾选数不足被拦(self, tmp_path):
        code = self._run(tmp_path, high_risk=3,
                         body_text="## 高危确认\n\n- [x] gate.cand cp.demo.a0 理由\n")
        assert code == 1

    def test_勾选但未提及策略_id_被拦(self, tmp_path, capsys):
        code = self._run(tmp_path, high_risk=1,
                         body_text="## 高危确认\n\n- [x] 某策略 某能力 理由\n")
        assert code == 1
        assert "未提及" in capsys.readouterr().out

    def test_逐条勾选齐全时通过(self, tmp_path):
        body_text = ("## 高危确认\n\n"
                     "- [x] gate.cand cp.demo.a0 已确认业务合理\n"
                     "- [x] gate.cand cp.demo.a1 已确认业务合理\n")
        assert self._run(tmp_path, high_risk=2, body_text=body_text) == 0

    def test_无样本未勾选声明被拦(self, tmp_path, capsys):
        path = _policy_file(tmp_path)
        report = _report_file(tmp_path, total=0, verdict="no_sample")
        body = tmp_path / "pr.md"
        body.write_text("## 无样本声明\n\n- [ ] 本次变更无历史决策样本\n",
                        encoding="utf-8")
        assert gate.main(["--changed", path, "--report", report,
                          "--pr-body", str(body)]) == 1
        assert "勾选" in capsys.readouterr().out

    def test_无样本缺声明段被拦(self, tmp_path):
        path = _policy_file(tmp_path)
        report = _report_file(tmp_path, total=0, verdict="no_sample")
        body = tmp_path / "pr.md"
        body.write_text("随便写点什么\n", encoding="utf-8")
        assert gate.main(["--changed", path, "--report", report,
                          "--pr-body", str(body)]) == 1

    def test_遮蔽诊断只告警不阻断(self, tmp_path, capsys):
        path = _policy_file(tmp_path)
        report = _report_file(tmp_path, shadow=[{"shadowing": "a", "shadowed": "b",
                                                 "detail": "d"}])
        assert gate.main(["--changed", path, "--report", report]) == 0
        assert "策略遮蔽诊断" in capsys.readouterr().out

    def test_段落解析在下一个标题处停止(self):
        body = ("## 高危确认\n\n- [x] a\n\n## 下一段\n\n- [x] b\n")
        section = gate.high_risk_section(body)
        assert "a" in section and "b" not in section


# ────────────────────────────────────────────────────────────
#  签名
# ────────────────────────────────────────────────────────────


class TestSignMode:
    def test_签名写回且可通过强制签名校验(self, tmp_path, monkeypatch):
        path = _policy_file(tmp_path)
        key = tmp_path / "k.pem"
        pub = tmp_path / "k.pub.pem"
        monkeypatch.setenv("CP_POLICY_SIGNING_KEY", str(key))
        monkeypatch.setenv("CP_POLICY_PUBLIC_KEY", str(pub))
        assert gate.main(["--sign", path]) == 0

        signed = json.loads(Path(path).read_text(encoding="utf-8"))
        assert all(item["signature"].startswith("ed25519:")
                   for item in signed["policies"])

        from agent.policy.store import PolicyStore
        store = PolicyStore(path=path, require_signed=True,
                            public_key_path=str(pub))
        assert store.problems == []
        assert store.validate_all() == []

    def test_签名非法文件时报错退出(self, tmp_path):
        bad = _policy_dir(tmp_path) / "bad.json"
        bad.write_text(json.dumps({"schema": "policy.v1",
                                   "policies": [{"id": "x"}]}), encoding="utf-8")
        assert gate.main(["--sign", str(bad)]) == 1


class TestHookInvocationForm:
    """**pre-commit 的调用形态**：框架 hook 会把暂存文件名追加到 entry 之后

    （python scripts/check_policy_change_gate.py --schema-only <file>）。
    若只提供 --changed 选项，argparse 会把这些位置参数判为无法识别而**误拦提交**
    ——本任务实测踩到过，故固化为用例。
    """

    def test_位置参数形式可用(self, tmp_path):
        path = _policy_file(tmp_path)
        assert gate.main(["--schema-only", path]) == 0

    def test_位置参数形式的非法策略被拦(self, tmp_path):
        path = _policy_file(tmp_path, [make_policy(id="bad.hook", effect="permit")])
        assert gate.main(["--schema-only", path]) == 1

    def test_位置参数与_changed_可混用(self, tmp_path):
        path = _policy_file(tmp_path)
        assert gate.main(["--schema-only", "--changed", path, path]) == 0

    def test_位置参数里的非策略路径被忽略(self, tmp_path, capsys):
        assert gate.main(["--schema-only", "agent/policy/store.py"]) == 0

    def test_全参数_解析不报错(self):
        args = gate.build_parser().parse_args(["--schema-only", "data/policies/policies.json"])
        assert args.paths == ["data/policies/policies.json"]
        assert gate._resolve_changed(args) == ["data/policies/policies.json"]


class TestGitDiscovery:
    def test_无_git_上下文时返回空(self, monkeypatch):
        import subprocess

        def boom(*args, **kwargs):
            raise subprocess.CalledProcessError(1, "git")

        monkeypatch.setattr(subprocess, "run", boom)
        assert gate._discover_changed("origin/master") == []
