"""本地模拟 Docker 启动流程测试 — 验证 entrypoint 迁移步骤与幂等性（无需 Docker daemon）

对照 scripts/docker_entrypoint.sh 步骤顺序：
  1) .env 生成/复用（此处模拟：不触碰真实 .env，使用临时目录）
  2) 历史审计日志租户归属补全（--apply --yes）
  3) 可选租户初始化（跳过，需服务）

验证点：
  - 无 tenant_id 的历史记录被补全为指定租户（默认 system）
  - 已有 tenant_id 的记录保持不变（幂等核心）
  - 重跑（模拟容器重启）→ 0 条待补全（幂等）
  - 备份文件生成（.bak_*）
  - 损坏 JSON 行原样保留、不崩溃

用法：
  python scripts/test_docker_entrypoint_flow.py        # 运行断言，失败退出码 1
  python scripts/test_docker_entrypoint_flow.py -v     # 详细输出
仅标准库（模拟容器环境，不依赖 docker / app_server）。
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MIGRATE = ROOT / "scripts" / "migrate_audit_logs_tenant.py"

# Windows 控制台默认 GBK：强制 UTF-8 输出
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 模拟"容器挂载 data 卷"内的一批历史审计记录（含一条损坏行、一条已归属记录）
SEED_RECORDS = [
    {"timestamp": "2026-08-01T00:00:00+00:00", "action": "legacy_1", "metadata": {}},
    {"timestamp": "2026-08-02T00:00:00+00:00", "action": "legacy_2", "metadata": {}},
    {"timestamp": "2026-08-03T00:00:00+00:00", "action": "owned",
     "metadata": {}, "tenant_id": "org_x"},
]


def run_migrate(audit_dir: Path, tenant: str) -> str:
    """以 subprocess 执行迁移脚本（等价容器内 python scripts/...），返回 stdout"""
    proc = subprocess.run(
        [sys.executable, str(MIGRATE), "--audit-dir", str(audit_dir),
         "--tenant", tenant, "--apply", "--yes"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError(f"迁移脚本失败: {proc.stderr}")
    return proc.stdout


def read_records(audit_dir: Path) -> list[dict | None]:
    """读回全部记录（损坏行返回 None）"""
    records = []
    for f in sorted(audit_dir.glob("audit_*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                records.append(None)
    return records


def count_pending(records: list[dict | None]) -> int:
    """待补全记录数（tenant_id 缺失/为空）"""
    return sum(1 for r in records if r is not None and not r.get("tenant_id"))


def simulate_entrypoint_flow(audit_dir: Path, tenant: str, verbose: bool) -> list[str]:
    """模拟 entrypoint 步骤 2（迁移），返回断言结果列表"""
    checks: list[str] = []

    def ok(name: str):
        checks.append(f"  [OK] {name}")

    # 步骤 1 准备：构造"容器挂载卷"内的历史数据
    seed_file = audit_dir / "audit_20260801.jsonl"
    seed_lines = [json.dumps(r, ensure_ascii=False) for r in SEED_RECORDS]
    seed_lines.append('{"broken json line')          # 损坏行（应原样保留）
    seed_file.write_text("\n".join(seed_lines) + "\n", encoding="utf-8")

    # 步骤 2a：首次启动 → 迁移
    out1 = run_migrate(audit_dir, tenant)
    if verbose:
        print(out1)
    records = read_records(audit_dir)

    legacy = [r for r in records if r is not None and r["action"].startswith("legacy_")]
    ok(f"首次迁移：{len(legacy)} 条历史记录补全为 tenant_id={tenant}"
       if legacy and all(r["tenant_id"] == tenant for r in legacy)
       else f"首次迁移：历史记录补全失败（{legacy}）")
    owned = [r for r in records if r is not None and r["action"] == "owned"]
    ok(f"已有归属保留：owned 仍为 org_x（实际 {owned[0]['tenant_id'] if owned else '缺失'}）"
       if owned and owned[0]["tenant_id"] == "org_x" else "已有归属保留失败")
    broken = [r for r in records if r is None]
    ok(f"损坏行原样保留：{len(broken)} 条未损坏/未崩溃"
       if len(broken) == 1 else f"损坏行处理异常（{len(broken)}）")

    # 步骤 2b：模拟容器重启 → 再迁移（幂等）
    out2 = run_migrate(audit_dir, tenant)
    records2 = read_records(audit_dir)
    pending = count_pending(records2)
    ok(f"幂等重跑：0 条待补全（实际 {pending}）" if pending == 0 else f"幂等性破坏（待补全 {pending}）")

    # 备份文件存在
    baks = list(audit_dir.glob("audit_*.jsonl.bak_*"))
    ok(f"备份文件生成：{len(baks)} 个 .bak_*（回滚能力）" if baks else "备份文件缺失")

    return checks


def main():
    ap = argparse.ArgumentParser(description="本地模拟 Docker 启动流程 + 迁移幂等测试")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    workdir = Path(tempfile.mkdtemp(prefix="entrypoint_flow_"))
    print(f"模拟容器挂载卷: {workdir}")
    print("==> entrypoint 步骤 2：历史审计日志租户归属补全（--apply --yes）")

    checks = simulate_entrypoint_flow(workdir, "system", args.verbose)

    print("---- 断言结果 ----")
    for c in checks:
        print(c)
    failed = [c for c in checks if "FAIL" in c or "失败" in c or "异常" in c or "破坏" in c]
    shutil.rmtree(workdir, ignore_errors=True)
    if failed:
        print(f"\n测试失败：{len(failed)} 项异常")
        sys.exit(1)
    print(f"\n全部 {len(checks)} 项断言通过 ✅（无需 Docker daemon，逻辑与容器 entrypoint 等价）")


if __name__ == "__main__":
    main()
