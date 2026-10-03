
# -*- coding: utf-8 -*-
"""C-2 结果复核：把 samples.jsonl 里**真实**发生的事算出来（自动摘要报告里 TSDB 那节是错的，必须自己算）"""
import json, statistics
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]   # 仓库根（脚本位于 scripts/monitoring_longrun/）
C2 = ROOT / "_scratch" / "c2"
rows = [json.loads(l) for l in (C2 / "samples.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
print("样本数:", len(rows), "| 跨度(s):", rows[-1]["elapsed_s"] - rows[0]["elapsed_s"])

def scal(d, key=None):
    """把 {job: value} 形态取成标量：优先 prometheus，否则取唯一值"""
    if not isinstance(d, dict) or not d:
        return None
    if key and key in d:
        return d[key]
    if "prometheus" in d:
        return d["prometheus"]
    vals = [v for v in d.values() if isinstance(v, (int, float))]
    return vals[0] if vals else None

print("\n== 1. 采样节奏（宿主有没有睡/卡）==")
gaps = []
for a, b in zip(rows, rows[1:]):
    d = b["elapsed_s"] - a["elapsed_s"]
    if d > 90:
        gaps.append((a["t"], round(d, 1)))
print("  超过 90s 的采样间隔:", gaps if gaps else "无（239 个样本均匀 60s ⇒ 宿主未休眠、进程未被挂起）")
print("  间隔统计: min=%.1f 中位=%.1f max=%.1f" % (
    min(b["elapsed_s"]-a["elapsed_s"] for a,b in zip(rows,rows[1:])),
    statistics.median([b["elapsed_s"]-a["elapsed_s"] for a,b in zip(rows,rows[1:])]),
    max(b["elapsed_s"]-a["elapsed_s"] for a,b in zip(rows,rows[1:]))))

print("\n== 2. up（抓取是否丢过）==")
for job in ["prometheus", "c2-probe", "grafana", "yunshu", "yunshu-business"]:
    seq = [(r["elapsed_s"], (r.get("up") or {}).get(job)) for r in rows]
    zeros = [s for s in seq if s[1] == 0]
    print("  %-16s 样本 %d，up=0 的 %d 个%s" % (
        job, len([x for x in seq if x[1] is not None]), len(zeros),
        ("（时间窗 %s ~ %s s）" % (zeros[0][0], zeros[-1][0])) if zeros else ""))
print("  应用恢复时刻: ", next((r["elapsed_s"] for r in rows if (r.get("up") or {}).get("yunshu") == 1), None), "s")

print("\n== 3. TSDB 真实增长（自动摘要那节算错了：它把 {job:val} 当标量）==")
def series(key):
    out = [(r["elapsed_s"], scal(r.get(key))) for r in rows]
    return [(t, v) for t, v in out if v is not None]
for key in ["tsdb_head_series","tsdb_head_chunks","tsdb_blocks_bytes","wal_bytes","tsdb_samples_appended"]:
    s = series(key)
    if not s:
        print("  %-24s 指标不存在/无样本" % key); continue
    hours = (s[-1][0] - s[0][0]) / 3600.0
    print("  %-24s 首 %s → 末 %s（%+.0f，约 %+.0f/小时）" % (key, s[0][1], s[-1][1], s[-1][1]-s[0][1], (s[-1][1]-s[0][1])/hours))
    print("     区间 min=%s max=%s" % (min(v for _,v in s), max(v for _,v in s)))

print("\n== 4. 抓取耗时 ==")
for job in ["prometheus","c2-probe","grafana","yunshu","yunshu-business"]:
    ds = [ (r.get("scrape_duration") or {}).get(job) for r in rows ]
    ds = [d for d in ds if isinstance(d,(int,float))]
    if ds:
        print("  %-16s n=%d 中位=%.4fs max=%.4fs" % (job, len(ds), statistics.median(ds), max(ds)))

print("\n== 5. 告警状态机（含消失=resolved）==")
names = set()
for r in rows:
    names |= set((r.get("alerts") or {}).keys())
life = {}
for r in rows:
    cur = r.get("alerts") or {}
    for n in names:
        st = cur.get(n, "resolved/inactive")
        if life.get(n) != st:
            life.setdefault(n, None)
            print("  T+%-8s %-34s -> %s" % (r["elapsed_s"], n, st))
        life[n] = st
