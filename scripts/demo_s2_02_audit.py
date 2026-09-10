#!/usr/bin/env python3
"""TASK-S2-02 链式审计端到端演示 + 篡改注入演练（§11.10）+ 性能实测

演示内容（对齐任务书 §二/§三/§四）：
    1. **审计平权（P7.2-24）**：Agent 操作（审批提交/审批/驳回、Trace 收尾/脱敏、
       能力台账变更、评审豁免、评估事件、谱系写入）与 **UI 写路由**（技能删除 /
       设置写，经 Flask 真实路由 + 全局写路由包装）落到**同一张链式审计表**，
       逐条按 source 区分可查；
    2. **链式哈希**：逐条 seq/prev_hash/self_hash；验签全链；
    3. **每日 Merkle 根**：生成 + 重放校验 + 签名方案（ed25519 / 降级 sha256 自签）；
    4. **篡改注入演练**：向链中间注入一条篡改（在台账副本上），`verify_chain()` 与
       验签 CLI 报告**注入 seq**，且后续记录全部失败（哈希前向传播）；
    5. **性能实测**：单条 append 均值/P95（对齐 §11.2 预算量级，目标 <5ms）；
    6. **双写过渡**：旧 JSONL 轨与新链轨一致性校验（`LegacyTrack`）。

用法：
    python scripts/demo_s2_02_audit.py                  # 全量演示
    python scripts/demo_s2_02_audit.py --json           # 只输出 JSON 摘要
    python scripts/demo_s2_02_audit.py --db <path>      # 指定演示台账
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import statistics
import sys
import tempfile
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from agent.audit.chain import (  # noqa: E402
    AuditChain,
    get_audit_chain,
    reset_audit_chains,
)
from agent.audit.facade import audit as global_audit  # noqa: E402

_DEFAULT_DB = os.path.join(_ROOT, "data", "audit", "audit_chain_demo.db")
_DEFAULT_ROOTS = os.path.join(_ROOT, "data", "audit", "daily_roots_demo.jsonl")
_DEFAULT_KEY = os.path.join(_ROOT, "data", "audit", "demo_signing_key.pem")
_DEFAULT_STATS = os.path.join(_ROOT, "data", "audit", "audit_stats.json")

OUT: list = []


def say(text: str = "") -> None:
    OUT.append(text)
    print(text)


# ════════════════════════════════════════════════════════════
#  1. Agent 侧：治理动作入链（审批 / Trace / 能力台账 / 评审 / 谱系）
# ════════════════════════════════════════════════════════════


def emit_agent_events(workspace_id: str) -> dict:
    """产生真实 Agent 侧审计事件（走各自的既有调用路径，非直接调门面）"""
    result: dict = {}

    # 1.1 审批流：提交（L1）→ 审批通过 → 驳回（走 ApprovalFlow 真实状态机）
    from agent.skills_mgmt.approval import ApprovalFlow
    tmp_records = os.path.join(tempfile.mkdtemp(), "approval_records.jsonl")
    flow = ApprovalFlow(records_path=tmp_records)
    rec1 = flow.submit("skill", "demo-skill-a", action="params_submit",
                       description="参数调整（演示）", actor="human",
                       payload={"temperature": 0.2})
    flow.approve(rec1.record_id, actor="reviewer", note="演示审批通过")
    rec2 = flow.submit("prompt", "demo-prompt-b", action="prompt_apply",
                       description="提示词建议应用（演示）", actor="agent")
    flow.reject(rec2.record_id, actor="reviewer", reason="演示驳回：风险未评估")
    result["approval_records"] = [rec1.record_id, rec2.record_id]

    # 1.2 Trace 关键事件：任务收尾（trace.closed）+ 脱敏动作（trace.redact，S2-01 遗留 #3）
    from agent.observability.trace_v2 import TraceFacade
    tf = TraceFacade(os.path.join(tempfile.mkdtemp(), "trace.db"))
    tid = tf.start(task_id="s2-02-demo-task", workspace_id=workspace_id,
                   subject_id="operator-demo")
    tf.record("cp.builtin.read_file", args={"path": "demo.py", "api_key": "sk-test-DEMO-REDACT"},
              output={"ok": True, "content": "print(1)"})
    tf.record("cp.builtin.shell_execute", args={"cmd": "pytest -q"},
              output={"ok": False, "error": "1 failed"}, actor="auto")
    tf.finish(status="success", output={"ok": True, "note": "done"})
    result["trace_id"] = tid
    tf._store.stop(timeout=2.0)

    # 1.3 能力台账（descriptor registry）变更 → descriptor.register/unregister
    try:
        from agent.descriptors.registry import DescriptorRegistry
        reg = DescriptorRegistry(path=os.path.join(tempfile.mkdtemp(), "desc.json"))
        reg.register({"meta": {"id": "cp.demo.audit_probe"},
                      "origin": {"source_type": "builtin", "source_id": "demo",
                                 "provenance": "declared"},
                      "capability": {"name": "audit_probe",
                                     "description": "S2-02 演示用能力",
                                     "input_schema": {"type": "object"}}},
                     actor="agent", reason="S2-02 演示")
        reg.unregister("cp.demo.audit_probe", actor="human")
        result["descriptor"] = "cp.demo.audit_probe"
    except Exception as e:  # noqa: BLE001 演示环境缺依赖时跳过
        say(f"  （能力台账演示跳过: {e}）")

    # 1.4 评审豁免发布（skill.review_waiver_publish）
    try:
        from agent.skills_mgmt import review_gate
        review_gate.audit_exemption("demo-skill-waived", actor="human",
                                    reason="演示：紧急发布豁免")
    except Exception as e:  # noqa: BLE001
        say(f"  （评审豁免演示跳过: {e}）")

    # 1.5 敏感操作面（配置访问/修改、权限变更、认证尝试、加密密钥访问）
    #     —— 对应任务书「策略变更」类：绕过治理的高危面必须与 UI/Agent 同表
    try:
        from agent.logging_utils import AuditLogger as _SensitiveAuditLogger
        _sl = _SensitiveAuditLogger()
        _sl.log_config_access("llm_api_key", user="admin")
        _sl.log_config_modification("llm_model", user="admin")
        _sl.log_permission_change("grant", "shell_execute", user="admin")
        _sl.log_authentication("admin", success=False, ip_address="10.0.0.7")
        _sl.log_encryption_key_access(success=True, user="admin")
        result["sensitive_ops"] = 5
    except Exception as e:  # noqa: BLE001
        say(f"  （敏感操作面演示跳过: {e}）")

    # 1.6 设置写后端（.env 配置变更 → config.env_set）
    try:
        from agent.env_config_manager import EnvConfigManager
        _env = EnvConfigManager()
        _env._audit_log("set", "llm_model", "old-demo", "new-demo")
        result["env_change"] = "config.env_set"
    except Exception as e:  # noqa: BLE001
        say(f"  （.env 配置变更演示跳过: {e}）")
    return result


# ════════════════════════════════════════════════════════════
#  2. UI 侧：真实 Flask 写路由（技能删除 / 设置写）→ 同一张表
# ════════════════════════════════════════════════════════════


def build_ui_app(chain: AuditChain, facade) -> dict:
    """构造真实路由 + 全局写路由审计包装的 Flask app（演示/测试同源）"""
    from flask import Flask
    from types import SimpleNamespace

    from agent.audit.ui_middleware import UIAuditRecorder
    from agent.server_routes import routes_config, routes_skills_mgmt

    app = Flask("s2_02_ui_audit_demo")
    UIAuditRecorder(facade=facade, skip_prefixes=()).register(app)

    # 2.1 技能删除（真实路由 /api/skills-mgmt/<skill_id> DELETE + @audit_action）
    class _FakeSvc:
        def delete(self, skill_id):
            return True

        def set_enabled(self, skill_id, enabled):
            from types import SimpleNamespace
            return SimpleNamespace(id=skill_id, model_dump=lambda: {"id": skill_id,
                                                                   "enabled": enabled})

    routes_skills_mgmt._svc = lambda: _FakeSvc()  # type: ignore[assignment]
    routes_skills_mgmt.register_routes(app, SimpleNamespace())

    # 2.2 设置写（真实路由 /api/config POST + @audit_action）
    class _FakeYunshu:
        @staticmethod
        def get_config():
            return {"provider": "deepseek", "model": "deepseek-chat"}

        @staticmethod
        def configure_llm(**kwargs):
            return {"ok": True, "provider": kwargs.get("provider", "")}

    class _FakeNCM:
        @staticmethod
        def _save_secure(key, value):
            return True

    class _FakeSessionMgr:
        @staticmethod
        def get_current_id():
            return "demo-session"

        @staticmethod
        def create_session(name):
            return {"id": "demo-session"}

        @staticmethod
        def clear_messages(sid):
            return None

    class _FakeHistory:
        @staticmethod
        def clear():
            return None

    state = SimpleNamespace(Yunshu=_FakeYunshu, session_mgr=_FakeSessionMgr,
                            network_config_mgr=_FakeNCM, search_engine=None,
                            chat_history=_FakeHistory())
    routes_config.register_routes(app, state)
    return {"app": app, "chain": chain, "facade": facade}


def emit_ui_events(ui: dict) -> list:
    """通过真实 HTTP 路由产生 UI 审计事件（含身份头与身份降级两条路径）"""
    client = ui["app"].test_client()
    seen = []
    r1 = client.delete("/api/skills-mgmt/demo-skill-delete",
                       headers={"X-Audit-Actor": "admin@yunshu"})
    seen.append(("DELETE /api/skills-mgmt/<skill_id>", r1.status_code))
    r2 = client.post("/api/config", json={"provider": "deepseek",
                                          "model": "deepseek-chat",
                                          "api_key": "sk-test-DEMO-NOT-IN-CHAIN"})
    seen.append(("POST /api/config", r2.status_code))
    # 无身份头 → 身份降级路径（actor=ui:<remote_addr>，来源如实标注）
    r3 = client.post("/api/skills-mgmt/demo-skill-delete/toggle", json={"enabled": False})
    seen.append(("POST /api/skills-mgmt/<id>/toggle", r3.status_code))
    return seen


# ════════════════════════════════════════════════════════════
#  3. 双写过渡：旧 JSONL 轨 ↔ 新链轨一致性
# ════════════════════════════════════════════════════════════


def dual_write_demo() -> dict:
    from agent.audit.logger import AuditLogger
    d = tempfile.mkdtemp()
    lg = AuditLogger(log_dir=d, chain_db_path=os.path.join(d, "audit_chain.db"),
                     roots_path=os.path.join(d, "daily_roots.jsonl"), dual_write=True)
    for i in range(5):
        lg.log("dual_write_demo", f"in-{i}", f"out-{i}",
               metadata={"api_key": "sk-test-DUAL-REDACT", "i": i})
    lg.flush()
    report = lg.track.verify_consistency()
    lg.close()
    return {"consistency": report.summary(), "consistent": report.consistent,
            "match_rate": report.match_rate, "legacy": report.legacy_count,
            "chain": report.chain_count}


# ════════════════════════════════════════════════════════════
#  4. 篡改注入演练（§11.10）
# ════════════════════════════════════════════════════════════


def tamper_drill(db_path: str, inject_seq: int, *, json_out: bool,
                 roots_path: str = "") -> dict:
    """在台账**副本**上向链中间注入一条篡改，跑 verify_chain + 验签 CLI"""
    workdir = tempfile.mkdtemp(prefix="s2_02_tamper_")
    tampered_db = os.path.join(workdir, "audit_chain_tampered.db")
    # WAL 检查点后再复制：否则数据仍在 -wal 文件里，副本会「无表」
    try:
        _c = sqlite3.connect(db_path)
        _c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        _c.commit()
        _c.close()
    except sqlite3.Error:
        pass
    shutil.copy2(db_path, tampered_db)
    for suffix in ("-wal", "-shm"):
        if os.path.exists(db_path + suffix):
            shutil.copy2(db_path + suffix, tampered_db + suffix)

    conn = sqlite3.connect(tampered_db)
    before = conn.execute("SELECT action, actor, payload FROM audit_chain WHERE seq=?",
                          (inject_seq,)).fetchone()
    conn.execute("UPDATE audit_chain SET actor='mallory', action='skill.delete' "
                 "WHERE seq=?", (inject_seq,))
    conn.commit()
    conn.close()

    reader = AuditChain.reader(tampered_db, signing_enabled=False)
    verification = reader.verify_chain()

    cli_code = None
    cli_out = ""
    try:
        from scripts.verify_audit_chain import main as cli_main
        import io
        buf = io.StringIO()
        real_stdout = sys.stdout
        sys.stdout = buf
        try:
            cli_code = cli_main(["--db", tampered_db, "--roots", roots_path,
                                 "--roots-check", "all", "--stats"])
        finally:
            sys.stdout = real_stdout
        cli_out = buf.getvalue()
    except Exception as e:  # noqa: BLE001 CLI 调用失败不阻断演示
        cli_out = f"（CLI 调用失败: {e}）"

    if not json_out:
        say(f"  注入点：seq={inject_seq}（原 {before[0]} / actor={before[1]} → "
            f"被改为 skill.delete / mallory）")
        say(f"  台账副本：{tampered_db}")
        say(f"  验签结论：{verification.summary()}")
        say(f"  失败明细（前 5 条）：")
        for item in verification.bad_seqs[:5]:
            say(f"    - seq={item['seq']:<6} {item['reason']:<20} {item['detail'][:70]}")
        say(f"  CLI --db <篡改副本> --stats → 退出码 {cli_code}（1=检出篡改）")
        for line in cli_out.strip().splitlines()[:6]:
            say(f"    | {line}")
    return {
        "injected_seq": inject_seq,
        "tampered_db": tampered_db,
        "verify_ok": verification.ok,
        "first_bad_seq": verification.first_bad_seq,
        "reason": verification.reason,
        "bad_seq_count": len(verification.bad_seqs),
        "bad_seqs": [b["seq"] for b in verification.bad_seqs],
        "cli_exit_code": cli_code,
    }


# ════════════════════════════════════════════════════════════
#  5. 性能实测
# ════════════════════════════════════════════════════════════


def perf_probe(chain: AuditChain, n: int = 200) -> dict:
    lat = []
    for i in range(n):
        t0 = time.perf_counter()
        chain.append("perf.probe", "bench", f"probe:{i}", {"i": i})
        lat.append((time.perf_counter() - t0) * 1000.0)
    chain.flush()
    lat_sorted = sorted(lat)
    return {
        "samples": n,
        "mean_ms": round(statistics.mean(lat), 4),
        "p50_ms": round(lat_sorted[len(lat_sorted) // 2], 4),
        "p95_ms": round(lat_sorted[int(len(lat_sorted) * 0.95) - 1], 4),
        "max_ms": round(max(lat), 4),
        "target_ms": 5.0,
        "meets_target": statistics.mean(lat) < 5.0,
    }


# ════════════════════════════════════════════════════════════
#  主流程
# ════════════════════════════════════════════════════════════


def _force_remove(path: str) -> None:
    """删除文件（受保护文件先恢复写权限——每日根置只读是单机降级保护）"""
    if not os.path.exists(path):
        return
    try:
        os.chmod(path, 0o644)
    except OSError:
        pass
    os.remove(path)


def run_demo(db_path: str, roots_path: str, key_path: str, stats_path: str,
             json_out: bool = False) -> dict:
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    for p in (db_path, roots_path, db_path + "-wal", db_path + "-shm"):
        _force_remove(p)
    reset_audit_chains()

    chain = AuditChain(db_path, roots_path=roots_path, signing_key_path=key_path,
                       auto_seal=False)
    # 全局门面绑定演示台账：Agent 侧既有钩子（审批/Trace/评审/谱系）自动落到本台账
    global_audit.bind(chain)
    global_audit.enabled = True

    workspace_id = "ws_s2_02_demo"
    say("═══ TASK-S2-02 链式审计演示 ═══")
    say(f"台账：{db_path}")
    say(f"每日根：{roots_path}")
    say()

    agent_events = emit_agent_events(workspace_id)
    say(f"[1] Agent 侧治理动作已入链（审批 {len(agent_events['approval_records'])} 条 / "
        f"Trace {agent_events.get('trace_id','')}）")

    ui = build_ui_app(chain, global_audit)
    ui_seen = emit_ui_events(ui)
    for route, code in ui_seen:
        say(f"[2] UI 写路由：{route:<40} → HTTP {code}（审计已入同一张表）")

    dual = dual_write_demo()
    say(f"[3] 双写过渡：{dual['consistency']}")

    chain.flush()
    rows = chain.entries()
    if not json_out:
        say()
        say("[4] 审计平权验证（UI 与 Agent 同表 audit_chain）：")
        say(f"    {'seq':<5}{'source':<9}{'actor':<20}{'action':<34}{'subject':<34}self_hash")
        for e in rows:
            say(f"    {e.seq:<5}{e.source:<9}{e.actor[:18]:<20}{e.action[:32]:<34}"
                f"{e.subject[:32]:<34}{e.self_hash[:12]}…")
        by_source: dict = {}
        for e in rows:
            by_source[e.source] = by_source.get(e.source, 0) + 1
        ui_actions = sorted({e.action for e in rows if e.source == "ui"})
        agent_actions = sorted({e.action for e in rows if e.source == "agent"})
        say(f"    来源分布：{by_source}")
        say(f"    UI 动作：{ui_actions}")
        say(f"    Agent 动作（前 6）：{agent_actions[:6]}")
        say(f"    同表可查（同一 audit_chain 表、同一 seq 链）："
            f"{'是' if by_source.get('ui') and by_source.get('agent') else '否'}")

    verification = chain.verify_chain()
    if not json_out:
        say()
        say(f"[5] 链式验签：{verification.summary()}")

    root = chain.daily_merkle_root()
    root_check = chain.verify_daily_root(root.date)
    if not json_out:
        say(f"[6] 每日 Merkle 根：date={root.date} root={root.root_hash[:32]}… "
            f"叶子={root.leaf_count} 签名={root.signature_scheme}"
            f"{'（降级）' if root.degraded else ''} 保护={root.protected}")
        say(f"    重放校验：{root_check.summary()}")

    # 篡改注入：取链中间一条
    inject_seq = rows[len(rows) // 2].seq
    say()
    say("[7] 混沌演练 §11.10「向审计链注入一条篡改」：")
    drill = tamper_drill(db_path, inject_seq, json_out=json_out, roots_path=roots_path)

    perf = perf_probe(chain)
    if not json_out:
        say()
        say(f"[8] 性能实测：单条 append 均值 {perf['mean_ms']}ms / p50 {perf['p50_ms']}ms / "
            f"p95 {perf['p95_ms']}ms / max {perf['max_ms']}ms"
            f"（目标 <{perf['target_ms']}ms → {'达标' if perf['meets_target'] else '未达标'}）")

    chain.flush()
    final = chain.verify_chain()
    stats = {
        "task": "TASK-S2-02",
        "db_path": db_path,
        "total_entries": chain.count(),
        "by_source": {s: sum(1 for e in chain.entries() if e.source == s)
                      for s in sorted({e.source for e in chain.entries()})},
        "ui_actions": sorted({e.action for e in chain.entries() if e.source == "ui"}),
        "chain_ok_after_injection_demo": final.ok,
        "chain_verify": final.to_dict(),
        "daily_root": root.to_dict(),
        "daily_root_check": root_check.to_dict(),
        "tamper_drill": drill,
        "dual_write": dual,
        "perf": perf,
        "signing_scheme": chain.signer.scheme,
        "signing_degraded": chain.signer.degraded,
        "append_only": True,
    }
    try:
        with open(stats_path, "w", encoding="utf-8") as f:
            json.dump(stats, f, ensure_ascii=False, indent=2)
        if not json_out:
            say()
            say(f"[9] 摘要已写出：{stats_path}")
    except OSError as e:  # noqa: BLE001
        say(f"[9] 摘要写出失败: {e}")

    if json_out:
        print(json.dumps(stats, ensure_ascii=False, indent=2))
    chain.close()
    return stats


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="TASK-S2-02 链式审计演示 + 篡改演练")
    p.add_argument("--db", default=_DEFAULT_DB)
    p.add_argument("--roots", default=_DEFAULT_ROOTS)
    p.add_argument("--key", default=_DEFAULT_KEY)
    p.add_argument("--stats", default=_DEFAULT_STATS)
    p.add_argument("--json", action="store_true", help="只输出 JSON 摘要")
    args = p.parse_args(argv)
    stats = run_demo(args.db, args.roots, args.key, args.stats, json_out=args.json)
    ok = stats["chain_verify"]["ok"] and stats["tamper_drill"]["first_bad_seq"] is not None
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
