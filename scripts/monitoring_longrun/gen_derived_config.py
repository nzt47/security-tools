
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]   # 仓库根（脚本位于 scripts/monitoring_longrun/）
C2 = ROOT / "_scratch" / "c2"
C2.mkdir(parents=True, exist_ok=True)

# 1) 派生 prometheus.yml：加一条探针规则 + 一个探针 scrape job（其余逐字照抄）
src = (ROOT / "monitoring" / "prometheus.yml").read_text(encoding="utf-8")
assert '  - "rules/planning_alerts.yml"' in src, "rule_files 锚点变了，检查配置"
derived = src.replace('  - "rules/planning_alerts.yml"',
                      '  - "rules/planning_alerts.yml"\n'
                      '  # 【C-2 长跑线】探针规则：把"告警生命周期"做成可控实验（仓库文件不改，仅本派生配置多这一条）\n'
                      '  - "c2_probe_rules.yml"', 1)
derived += """
  # ── 【C-2 长跑线】受控探针：宿主进程在 :9105 暴露 c2_probe_gauge ──
  #   为什么需要：C-2 要验证"告警真正的 pending→firing→resolved 全过程"，
  #   等真实故障不可控；用探针就能按分钟级时序精确触发。
  - job_name: 'c2-probe'
    static_configs:
      - targets: ['host.docker.internal:9105']
    metrics_path: '/metrics'
    scrape_interval: 5s
"""
(C2 / "prometheus.yml").write_text(derived, encoding="utf-8")
print("ok c2/prometheus.yml（%d 行，比仓库版多 2 处：1 条 rule_files + 1 个 scrape job）" % len(derived.splitlines()))

# 2) 探针规则
rules = """# C-2 长跑线专用：可控探针告警（不进仓库的监控配置，只挂在派生配置上）
#
# 为什么是 2m：要能看到 **pending → firing** 这个中间态。若 for: 0s，采样间隔 60s 很可能
# 直接看到 firing，pending 那一段就被跳过了 —— 而"pending 到底存不存在、持续多久"正是 C-2 要问的。
groups:
  - name: c2_lifecycle_probe
    interval: 5s
    rules:
      - alert: C2ProbeLifecycle
        expr: c2_probe_gauge > 0.5
        for: 2m
        labels:
          severity: warning
          probe: c2
        annotations:
          summary: "C-2 探针告警（受控）"
          description: "探针 gauge 被置 1 后持续 2 分钟即 firing；置回 0 后应转为 resolved。"
"""
(C2 / "c2_probe_rules.yml").write_text(rules, encoding="utf-8")
print("ok c2/c2_probe_rules.yml")

# 3) 派生 compose：只把 prometheus.yml 的挂载指向派生配置，并多挂一个探针规则文件
csrc = (ROOT / "docker-compose.monitoring.yml").read_text(encoding="utf-8")
old = "      - ./monitoring/prometheus.yml:/etc/prometheus/prometheus.yml"
new = ("      # 【C-2 长跑线】派生自仓库 compose，仅下面两行不同（其余逐字照抄，含钉住的镜像 tag）\n"
       "      - ./_scratch/c2/prometheus.yml:/etc/prometheus/prometheus.yml\n"
       "      - ./_scratch/c2/c2_probe_rules.yml:/etc/prometheus/c2_probe_rules.yml:ro")
assert csrc.count(old) == 1, "compose 挂载锚点变了"
cout = csrc.replace(old, new, 1)
# 长跑时不要自动拉 Grafana 里没用的插件；其余不变
(C2 / "compose.yml").write_text(cout, encoding="utf-8")
print("ok c2/compose.yml（与仓库版差异：%d 行 -> %d 行）" % (len(csrc.splitlines()), len(cout.splitlines())))
