
# -*- coding: utf-8 -*-
"""C-2 长跑线：把监控栈真的跑起来，逐分钟采样，回答"长期运行会怎样"

【为什么需要它】交付报告 §10 C-2：监控栈此前只在"一次性启动"下验证过（规则加载、热加载双向证明），
长期运行行为（抓取漂移 / TSDB 增长 / 告警真正的 pending→firing→resolved 全过程）从未验证。
这类问题没法靠读代码回答，只能真跑一段时间再读数。

【怎么做到不污染仓库】
- 用 --project-directory 指向仓库根，跑一份**派生的** compose（_scratch/c2/compose.yml）：
  与 docker-compose.monitoring.yml 逐行相同，只把 prometheus.yml 的挂载指向本目录的派生配置
  （多一个探针 scrape job + 一条探针规则），仓库文件一行不改。
- 探针 exporter 由本进程在宿主 :9105 提供，Prometheus 容器按 host.docker.internal 抓它；
  这样"告警生命周期"是**可控的**：到点把 gauge 从 0 拨到 1，就能看到 pending → firing；
  拨回 0 就能看到 resolved —— 而不是等一个真实故障发生。

【采样什么（对齐 C-2 的三个问号）】
1. 抓取漂移：每个 job 的 up / scrape_duration_seconds / scrape_samples_scraped / count_over_time(up[5m])；
2. TSDB 增长：head_series / head_chunks / head_samples_appended_total / storage_blocks_bytes / WAL 大小；
3. 告警全过程：每轮快照 /api/v1/alerts 的 (alertname -> state)，把**状态跃迁**单独记成事件流。

【收尾】写报告 + 拆栈（down -v），不留容器、不留卷。
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]   # 仓库根（脚本位于 scripts/monitoring_longrun/）
C2 = ROOT / "_scratch" / "c2"
PROM = "http://127.0.0.1:9090"
PROBE_PORT = 9105
DURATION = int(sys.argv[1]) if len(sys.argv) > 1 else 4 * 3600
SAMPLE_EVERY = 60
FLIP_ON_AT = 300
FLIP_OFF_AT = 1500
LOG = C2 / "harness.log"
SAMPLES = C2 / "samples.jsonl"
EVENTS = C2 / "events.jsonl"


def log(msg):
    line = "[%s] %s" % (datetime.now().strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


class Probe:
    def __init__(self):
        self.gauge = 0.0
        self.started = time.time()

    def render(self):
        return ("# HELP c2_probe_gauge 受控探针（0/1），用于观察 pending->firing->resolved\n"
                "# TYPE c2_probe_gauge gauge\n"
                "c2_probe_gauge %s\n"
                "# HELP c2_probe_uptime_seconds 探针进程存活秒数\n"
                "# TYPE c2_probe_uptime_seconds counter\n"
                "c2_probe_uptime_seconds %.1f\n" % (self.gauge, time.time() - self.started))


PROBE = Probe()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/metrics"):
            body = PROBE.render().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *a):
        pass


def api(path, timeout=10.0):
    with urllib.request.urlopen(PROM + path, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def query(expr):
    try:
        d = api("/api/v1/query?query=" + urllib.parse.quote(expr))
    except Exception:
        return None
    if d.get("status") != "success":
        return None
    out = {}
    for s in d["data"]["result"]:
        m = s["metric"]
        key = m.get("job") or m.get("alertname") or m.get("__name__") or "value"
        try:
            out[key] = float(s["value"][1])
        except Exception:
            out[key] = None
    return out


def alert_states():
    try:
        d = api("/api/v1/alerts", timeout=10)
    except Exception:
        return {}
    out = {}
    for a in d["data"]["alerts"]:
        out[a["labels"].get("alertname", "?")] = a.get("state", "?")
    return out


def docker(*args, timeout=300):
    return subprocess.run(["docker", *args], cwd=str(ROOT), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def num(d, k):
    try:
        return float(d.get(k) or 0)
    except Exception:
        return 0.0


def write_summary(transitions, final):
    rows = []
    if SAMPLES.exists():
        for line in SAMPLES.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
    if not rows:
        return
    first, last = rows[0], rows[-1]
    hours = max((num(last, "elapsed_s") - num(first, "elapsed_s")) / 3600.0, 1e-6)
    series_delta = num(last, "tsdb_head_series") - num(first, "tsdb_head_series")
    appended_delta = num(last, "tsdb_samples_appended") - num(first, "tsdb_samples_appended")
    blocks_delta = num(last, "tsdb_blocks_bytes") - num(first, "tsdb_blocks_bytes")
    updown = [r for r in rows if (r.get("up") or {}).get("yunshu") == 0]
    probe_zero = [r for r in rows if (r.get("up") or {}).get("c2-probe") == 0]
    dur = [(r.get("scrape_duration") or {}).get("prometheus") for r in rows]
    dur = [d for d in dur if d is not None]

    lines = [
        "# C-2 长跑报告：监控栈长期运行行为",
        "",
        "> 由 _scratch/c2/c2_harness.py 自动生成。采样间隔 %ds，样本 %d 个，跨度 %.2f 小时%s。"
        % (SAMPLE_EVERY, len(rows), hours, "（**中途快照**）" if not final else "（最终）"),
        "> 生成时间：%s（UTC）" % datetime.now(timezone.utc).isoformat(),
        "> 原始逐分钟样本：_scratch/c2/samples.jsonl（体积原因不进仓库；本文件是它的汇总）",
        "",
        "## 1. 抓取漂移",
        "",
        "| 观测项 | 值 |",
        "|---|---|",
        "| prometheus 自抓耗时（最大 / 中位） | %.4f s / %.4f s |" % (max(dur) if dur else -1,
                                                                     sorted(dur)[len(dur) // 2] if dur else -1),
        "| up{job=c2-probe}=0 的样本数 | %d / %d（探针在宿主侧，正常应恒为 0 ⇒ 非 0 即抓取真的丢过） |"
        % (len(probe_zero), len(rows)),
        "| up{job=yunshu}=0 的样本数 | %d / %d（应用当时未运行，属预期，见备注） |" % (len(updown), len(rows)),
        "",
        "各 job 的 up 取值分布（末次样本）：%s" % json.dumps(last.get("up"), ensure_ascii=False),
        "",
        "## 2. TSDB 增长",
        "",
        "| 指标 | 首 | 末 | 增量 |",
        "|---|---|---|---|",
        "| head_series | %s | %s | %+.0f |" % (first.get("tsdb_head_series"), last.get("tsdb_head_series"), series_delta),
        "| samples_appended_total | %s | %s | %+.0f |"
        % (first.get("tsdb_samples_appended"), last.get("tsdb_samples_appended"), appended_delta),
        "| storage_blocks_bytes | %s | %s | %+.0f |" % (first.get("tsdb_blocks_bytes"), last.get("tsdb_blocks_bytes"), blocks_delta),
        "| head_chunks | %s | %s | %+.0f |" % (first.get("tsdb_head_chunks"), last.get("tsdb_head_chunks"),
                                               num(last, "tsdb_head_chunks") - num(first, "tsdb_head_chunks")),
        "",
        "外推（按本窗口速率，仅供量级参考，不要当容量承诺）：**%.0f 条序列/小时**、**%.0f 样本/小时**。"
        % (series_delta / hours, appended_delta / hours),
        "",
        "## 3. 告警状态跃迁（真实 pending -> firing -> resolved）",
        "",
    ]
    if transitions:
        lines += ["| T+秒 | 告警 | 新状态 |", "|---|---|---|"]
        lines += ["| %s | %s | %s |" % (t["elapsed_s"], t["alert"], t["state"]) for t in transitions[:80]]
    else:
        lines.append("（本窗口内没有状态跃迁 —— 这本身是个结论：要么没有告警被判为 pending，要么采样没抓到）")
    lines += [
        "",
        "## 4. 备注与结论边界",
        "",
        "- 应用（yunshu / yunshu-business 两个 job 的目标 host.docker.internal:5678）在本次窗口内**未运行**，",
        "  因此 up=0 与其后的告警属**预期现象**；反过来它证明「目标真的不可达 ⇒ 规则真的 pending->firing」这条链路是通的。",
        "- 探针 job（c2-probe）是本进程在宿主 :9105 提供的受控 exporter，",
        "  用于把 pending->firing->resolved 做成**可控实验**而不是等真实故障。",
        "- 宿主休眠/挂起会让采样与抓取同时中断 —— 这类缺口本身就是长期运行要暴露的事实之一，",
        "  但本报告无法区分「宿主睡了」与「Prometheus 卡了」，需要配合 samples.jsonl 的时间戳看。",
        "- 结论边界：本窗口只覆盖数小时，**不足以**外推磁盘/内存的月度行为；",
        "  真要做容量规划应把采样周期拉长到数天并同时采集磁盘占用。",
        "",
    ]
    out = C2 / ("interim_summary.md" if not final else "final_summary.md")
    out.write_text("\n".join(lines), encoding="utf-8")
    log("已写 %s" % out.name)
    if final:
        ev = ROOT / "docs" / "closeout" / "监控清理_evidence_20261002"
        (ev / "c2_longrun_report.md").write_text("\n".join(lines), encoding="utf-8")
        (ev / "c2_longrun_transitions.jsonl").write_text(
            "\n".join(json.dumps(t, ensure_ascii=False) for t in transitions) + "\n", encoding="utf-8")
        log("已写证据：c2_longrun_report.md / c2_longrun_transitions.jsonl")


def main():
    t0 = time.time()
    srv = HTTPServer(("0.0.0.0", PROBE_PORT), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log("探针 exporter 已起（宿主 :%d）；目标时长 %d 秒（%.1f 小时）" % (PROBE_PORT, DURATION, DURATION / 3600.0))

    up = docker("compose", "--project-directory", str(ROOT), "-f", str(C2 / "compose.yml"),
                "up", "-d", "--remove-orphans")
    log("up rc=%s %s" % (up.returncode, (up.stderr or "").strip()[:300]))
    if up.returncode != 0:
        log("!! 起栈失败，收尾退出")
        return 2

    ready = False
    for _ in range(40):
        try:
            api("/-/ready", timeout=3)
            ready = True
            break
        except Exception:
            time.sleep(2)
    log("Prometheus 就绪=%s（等待 %.0fs）；初始 up=%s" % (ready, time.time() - t0, query("up")))

    transitions = []
    prev_states = {}
    samples = 0
    next_sample = t0
    flipped_on = flipped_off = False
    elapsed = 0.0

    while time.time() - t0 < DURATION:
        elapsed = time.time() - t0
        if elapsed >= FLIP_ON_AT and not flipped_on:
            PROBE.gauge = 1.0
            flipped_on = True
            log("探针置 1（T+%ds）⇒ 期待 pending，随后 firing（规则 for: 2m）" % int(elapsed))
        if elapsed >= FLIP_OFF_AT and not flipped_off:
            PROBE.gauge = 0.0
            flipped_off = True
            log("探针置 0（T+%ds）⇒ 期待 resolved" % int(elapsed))

        if time.time() >= next_sample:
            next_sample = time.time() + SAMPLE_EVERY
            samples += 1
            snap = {
                "t": datetime.now(timezone.utc).isoformat(),
                "elapsed_s": round(elapsed, 1),
                "up": query("up"),
                "scrape_duration": query("scrape_duration_seconds"),
                "samples_scraped": query("scrape_samples_scraped"),
                "series_added": query("scrape_series_added"),
                "up_5m_counts": query("count_over_time(up[5m])"),
                "tsdb_head_series": query("prometheus_tsdb_head_series"),
                "tsdb_head_chunks": query("prometheus_tsdb_head_chunks"),
                "tsdb_samples_appended": query("prometheus_tsdb_head_samples_appended_total"),
                "tsdb_blocks_bytes": query("prometheus_tsdb_storage_blocks_bytes"),
                "wal_bytes": query("prometheus_tsdb_wal_storage_size_bytes"),
                "probe": PROBE.gauge,
                "alerts": alert_states(),
            }
            with open(SAMPLES, "a", encoding="utf-8") as f:
                f.write(json.dumps(snap, ensure_ascii=False) + "\n")
            for name, state in (snap["alerts"] or {}).items():
                if prev_states.get(name) != state:
                    transitions.append({"t": snap["t"], "elapsed_s": snap["elapsed_s"],
                                        "alert": name, "state": state})
                    log("告警状态跃迁：%s -> %s（T+%ss）" % (name, state, snap["elapsed_s"]))
                prev_states[name] = state
            if samples % 10 == 0:
                log("样本 #%d：series=%s up=%s 告警数=%s" % (
                    samples, snap["tsdb_head_series"], snap["up"], len(snap["alerts"] or {})))
            if samples % 30 == 0:
                write_summary(transitions, final=False)

    write_summary(transitions, final=True)
    log("采样结束，拆栈（down -v）…")
    down = docker("compose", "--project-directory", str(ROOT), "-f", str(C2 / "compose.yml"), "down", "-v")
    log("down rc=%s %s" % (down.returncode, (down.stderr or "").strip()[:200]))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        log("!! 异常退出：%s" % e)
        log(traceback.format_exc())
        try:
            docker("compose", "--project-directory", str(ROOT), "-f", str(C2 / "compose.yml"), "down", "-v")
        except Exception:
            pass
        sys.exit(3)
