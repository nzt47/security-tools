#!/usr/bin/env python3
"""导出生产环境 Prometheus + Alertmanager 完整配置快照（归档备份）。

【不易】快照内容 = 容器内实际生效配置（docker exec cat），保证与生产运行
状态一致；容器不可达（如 Alertmanager 停止）时回退读挂载源文件（同一文件源，
仓库 deploy/monitoring/prometheus/），并在 manifest 中标注来源。

产物: test_reports/config_snapshot_<时间戳>/
  ├── prometheus/{prometheus.yml, alert_rules.yml, recording_rules.yml}
  ├── alertmanager/alertmanager.yml
  └── manifest.json        # 导出时间 + 每文件 sha256 + 来源 + 容器状态

用法: python scripts/export_monitoring_config_snapshot.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ── 配置 ─────────────────────────────────────────────────────────────────
PROM_CONTAINER = "yunshu-prod-prometheus"
ALERTMANAGER_CONTAINER = "yunshu-prod-alertmanager"
REPO_MONITORING = Path(__file__).resolve().parents[1] / "deploy" / "monitoring"
OUT_ROOT = Path(__file__).resolve().parents[1] / "test_reports"
TIMESTAMP = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

# 容器内路径 -> (快照子路径, 仓库回退源)
PROM_FILES = {
    "/etc/prometheus/prometheus.yml": ("prometheus/prometheus.yml", REPO_MONITORING / "prometheus" / "prometheus.yml"),
    "/etc/prometheus/alert_rules.yml": ("prometheus/alert_rules.yml", REPO_MONITORING / "prometheus" / "alert_rules.yml"),
    "/etc/prometheus/recording_rules.yml": ("prometheus/recording_rules.yml", REPO_MONITORING / "prometheus" / "recording_rules.yml"),
    "/etc/alertmanager/alertmanager.yml": ("alertmanager/alertmanager.yml", REPO_MONITORING / "prometheus" / "alertmanager.yml"),
}


def container_status(name: str) -> str:
    try:
        r = subprocess.run(
            ["docker", "inspect", name, "--format", "{{.State.Status}}"],
            capture_output=True, text=True, timeout=15,
        )
        return r.stdout.strip() or f"inspect-failed({r.stderr.strip()[:60]})"
    except Exception as exc:
        return f"error({exc})"


def docker_cat(container: str, path: str) -> bytes | None:
    """从容器读取文件（exec cat；失败时尝试 docker cp，对停止容器同样可用）。"""
    try:
        r = subprocess.run(
            ["docker", "exec", container, "cat", path],
            capture_output=True, timeout=20,
        )
        if r.returncode == 0:
            return r.stdout
    except Exception:
        pass
    # docker cp 不要求容器运行，可读取停止容器内文件
    tmp = Path(__file__).resolve().parent.parent / "test_reports" / f".cp_tmp_{os.getpid()}"
    try:
        r = subprocess.run(
            ["docker", "cp", f"{container}:{path}", str(tmp)],
            capture_output=True, timeout=30,
        )
        if r.returncode == 0 and tmp.exists():
            data = tmp.read_bytes()
            tmp.unlink(missing_ok=True)
            return data
    except Exception:
        pass
    if tmp.exists():
        tmp.unlink(missing_ok=True)
    return None


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="导出生产监控配置快照")
    parser.add_argument("--out-dir", default=None, help="快照根目录（默认 test_reports；归档可用 backups/monitoring）")
    args = parser.parse_args()
    out_root = Path(args.out_dir) if args.out_dir else OUT_ROOT
    out_dir = out_root / f"config_snapshot_{TIMESTAMP}"
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "exported_at_utc": datetime.now(timezone.utc).isoformat(),
        "containers": {
            PROM_CONTAINER: container_status(PROM_CONTAINER),
            ALERTMANAGER_CONTAINER: container_status(ALERTMANAGER_CONTAINER),
        },
        "files": {},
    }
    missing = []

    for container_path, (snap_rel, repo_fallback) in PROM_FILES.items():
        container_name = PROM_CONTAINER if container_path.startswith("/etc/prometheus") else ALERTMANAGER_CONTAINER
        data = docker_cat(container_name, container_path)
        source = f"docker:{container_name}"
        if data is None:
            # 回退：读仓库挂载源（同一文件源）；目录/缺失视为无配置（如实记录）
            if repo_fallback.is_file():
                data = repo_fallback.read_bytes()
                source = f"repo:{repo_fallback.relative_to(REPO_MONITORING.parents[1])}"
            else:
                missing.append(f"{container_path} (repo 侧是目录或缺失: {repo_fallback})")
                continue
        dest = out_dir / snap_rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        manifest["files"][container_path] = {
            "snapshot": snap_rel,
            "sha256": sha256(data),
            "bytes": len(data),
            "source": source,
        }

    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8",
    )

    print(f"快照目录: {out_dir}")
    for cp, info in manifest["files"].items():
        print(f"  [ok] {cp:50s} {info['bytes']:>7}B  {info['source']}")
    if missing:
        print(f"[WARN] 缺失文件: {missing}", file=sys.stderr)
        return 1
    print("容器状态:", manifest["containers"])
    print("快照导出完成，manifest.json 已生成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
