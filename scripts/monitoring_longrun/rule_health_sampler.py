
# -*- coding: utf-8 -*-
"""C-2 侧采样器：每 5 分钟记录规则组求值健康度与自监控指标（与主 harness 并行跑）

为什么单独一个：主 harness 只采 up/TSDB/告警状态；而 C-2 还要回答
"规则求值会不会随时间漂移/卡住" —— 那要看 prometheus_rule_group_last_duration_seconds、
last_evaluation_timestamp_seconds（判断是否还在按 interval 求值）与 /api/v1/rules 的 health。
"""
import json, time, urllib.parse, urllib.request
from datetime import datetime, timezone
from pathlib import Path

OUT = Path(r"C:\Users\Administrator\agent\_scratch\c2\rule_health.jsonl")
PROM = "http://127.0.0.1:9090"
DURATION = 4 * 3600
EVERY = 300


def get(path):
    with urllib.request.urlopen(PROM + path, timeout=15) as r:
        return json.loads(r.read().decode())


def q(expr):
    try:
        d = get("/api/v1/query?query=" + urllib.parse.quote(expr))
    except Exception:
        return None
    return {(s["metric"].get("group") or s["metric"].get("__name__")): s["value"][1] for s in d["data"]["result"]}


def groups_health():
    try:
        d = get("/api/v1/rules?type=alert")
    except Exception:
        return None
    out = []
    for g in d["data"]["groups"]:
        bad = [r["name"] for r in g["rules"] if r.get("health") != "ok"]
        out.append({"file": g.get("file"), "group": g.get("name"), "rules": len(g["rules"]),
                    "evaluationTime": g.get("evaluationTime"), "lastEvaluation": g.get("lastEvaluation"),
                    "not_ok": bad})
    return out


t0 = time.time()
n = 0
while time.time() - t0 < DURATION:
    n += 1
    snap = {
        "t": datetime.now(timezone.utc).isoformat(),
        "elapsed_s": round(time.time() - t0, 1),
        "group_last_duration": q("prometheus_rule_group_last_duration_seconds"),
        "group_last_eval_ts": q("prometheus_rule_group_last_evaluation_timestamp_seconds"),
        "group_interval": q("prometheus_rule_group_interval_seconds"),
        "groups_health": groups_health(),
    }
    with open(OUT, "a", encoding="utf-8") as f:
        f.write(json.dumps(snap, ensure_ascii=False) + "\n")
    time.sleep(EVERY)
print("side sampler done")
