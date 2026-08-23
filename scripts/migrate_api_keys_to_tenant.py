"""API Key 认证双轨迁移脚本（T8.2 前置）

背景：T8 多租户改造后，开放 API 认证从"独立 ApiKeyManager Key"升级为
"Key ↔ 租户/角色绑定"（RBAC）。为保证平滑过渡，旧 Key 保留 **3 个月兼容期**
（compat_until = 迁移日 + 90 天），兼容期内旧 Key 仍可调用，到期后强制走新格式。

本脚本把现有 agent/data/api_keys.json（旧格式：key → {user_id, scopes, ...}）
迁移为新格式：每 Key 附加 tenant_id（未归属用户挂到 legacy 租户）、role、
compat_until 字段。

用法:
    python scripts/migrate_api_keys_to_tenant.py                # dry-run 预览
    python scripts/migrate_api_keys_to_tenant.py --apply        # 实际迁移
    python scripts/migrate_api_keys_to_tenant.py --apply --compat-days 180
    python scripts/migrate_api_keys_to_tenant.py --json         # JSON 报告

退出码: 0 = 成功/无可迁移; 1 = 失败
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# 保证从任意工作目录运行都能导入 agent 包
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.multi_tenant import tenant_manager, RoleType  # noqa: E402

# 旧 Key 存储（与 agent/api_gateway.py ApiKeyManager._load_keys 一致）
API_KEYS_FILE = Path(__file__).parent.parent / "agent" / "data" / "api_keys.json"
# 未归属用户的默认租户
LEGACY_TENANT_NAME = "legacy-keys"
LEGACY_TENANT_TYPE = "organization"


def load_old_keys() -> dict:
    """读取旧格式 api_keys.json（key → info）"""
    if not API_KEYS_FILE.exists():
        return {}
    with open(API_KEYS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def ensure_legacy_tenant() -> str:
    """确保 legacy-keys 租户存在（owner 为系统用户），返回 tenant_id"""
    for t in tenant_manager.list_tenants():
        if t.name == LEGACY_TENANT_NAME:
            return t.id
    # 创建系统 owner（幂等：email 固定）
    owner = tenant_manager.create_user("legacy@local", "Legacy Keys Owner")
    org = tenant_manager.create_organization(LEGACY_TENANT_NAME, owner.id)
    tenant_manager.assign_role(owner.id, org.id, RoleType.OWNER)
    return org.id


def build_migration_plan(old_keys: dict, compat_days: int) -> dict:
    """生成迁移计划（不写盘）"""
    legacy_tenant_id = ensure_legacy_tenant() if old_keys else ""
    compat_until = (datetime.now() + timedelta(days=compat_days)).isoformat()

    migrated = []
    for api_key, info in old_keys.items():
        migrated.append({
            "key_masked": api_key[:8] + "****",
            "user_id": info.get("user_id", "anonymous"),
            "tenant_id": legacy_tenant_id,      # 全部挂 legacy 租户（T8.2 可再细分）
            "role": "member",
            "compat_until": compat_until,
            "scopes": info.get("scopes", ["read", "write"]),
        })
    return {
        "legacy_tenant_id": legacy_tenant_id,
        "compat_until": compat_until,
        "total_keys": len(migrated),
        "keys": migrated,
    }


def apply_migration(plan: dict, old_keys: dict) -> dict:
    """执行迁移：给每个旧 Key 附加 tenant_id/role/compat_until 后写回"""
    # 新格式 = 旧字段 + 租户绑定字段
    updated = {}
    key_index = {k["key_masked"].split("****")[0]: k for k in plan["keys"]}
    compat_until = plan["compat_until"]
    legacy_tenant_id = plan["legacy_tenant_id"]

    for api_key, info in old_keys.items():
        entry = dict(info)
        entry["tenant_id"] = legacy_tenant_id
        entry["role"] = "member"
        entry["compat_until"] = compat_until
        updated[api_key] = entry

    # 原子写回（先写 .tmp 再 rename，避免中途崩溃损坏）
    tmp_file = API_KEYS_FILE.with_suffix(".json.tmp")
    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(updated, f, ensure_ascii=False, indent=2)
    os.replace(tmp_file, API_KEYS_FILE)

    return {"applied": len(updated), "compat_until": compat_until,
            "legacy_tenant_id": legacy_tenant_id}


def main() -> int:
    parser = argparse.ArgumentParser(description="API Key 双轨认证迁移（旧 Key 兼容 3 个月）")
    parser.add_argument("--apply", action="store_true",
                        help="执行迁移（默认 dry-run 只预览）")
    parser.add_argument("--compat-days", type=int, default=90,
                        help="兼容期天数（默认 90）")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    args = parser.parse_args()

    t0 = time.time()
    old_keys = load_old_keys()
    if not old_keys:
        print(json.dumps({"ok": True, "message": "无旧 Key 可迁移", "total_keys": 0}) if args.json
              else "无旧 Key 可迁移")
        return 0

    try:
        plan = build_migration_plan(old_keys, args.compat_days)
    except Exception as e:
        print(f"迁移计划构建失败: {e}")
        return 1

    result = {"ok": True, "dry_run": not args.apply, "plan": plan,
              "duration_ms": round((time.time() - t0) * 1000, 1)}

    if args.apply:
        applied = apply_migration(plan, old_keys)
        result["applied"] = applied
        result["dry_run"] = False

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        mode = "DRY-RUN（未写盘）" if not args.apply else "已迁移"
        print(f"[{mode}] 旧 Key 共 {plan['total_keys']} 个")
        print(f"  legacy 租户: {plan['legacy_tenant_id'] or '(无)'}")
        print(f"  兼容截止: {plan['compat_until']}")
        for k in plan["keys"][:10]:
            print(f"  - {k['key_masked']} user={k['user_id']} role={k['role']} "
                  f"compat_until={k['compat_until']}")
        if len(plan["keys"]) > 10:
            print(f"  ... 其余 {len(plan['keys']) - 10} 个省略")
        if args.apply:
            print(f"[完成] 已迁移 {result['applied']['applied']} 个 Key")
    return 0


if __name__ == "__main__":
    sys.exit(main())
