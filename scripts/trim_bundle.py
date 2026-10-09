#!/usr/bin/env python3
"""bundle 裁剪脱敏入口 —— 把导出包改成可对外共享的形态（S5 / portability）

【它解决什么（不这样会怎样）】
    导出 bundle 里有三类"不该无条件跟着包走"的信息：
      ① secrets.refs —— 引用式密钥的**槽位**（虽无值，但暴露用到了哪些环境变量名）；
      ② environment.items —— 导出机的**精确依赖版本**（可指纹化环境）；
      ③ 顶层 metadata（若存在）—— 外部附加的自由字段，可能含内部标识。
    本脚本把它们裁掉后重新过密钥闸 + validate_bundle，产出可安全共享的包。

【不假装】
    · 只动上面三类叶子，其余键逐字保留（不重排、不重算 bundle_id）；
    · 清空 environment.items 会**同时把 offline_ready 置 false**：没有依赖清单就无法
      复核"wheelhouse 覆盖全部声明依赖"，留着 true 等于让无法验证的声明继续生效；
    · 裁剪后的包**仍可导入**（validate_bundle 通过即合法），但 secrets.refs 清空意味着
      到达端要自己配凭据引用 —— 这是脱敏的代价，脚本会如实打印。

用法：
    python scripts/trim_bundle.py --in bundle.json --out trimmed.json
    python scripts/trim_bundle.py --in bundle.json --out trimmed.json --keep-secrets

退出码：0 成功；2 读入错误；4 裁剪结果不合法。
"""
from __future__ import annotations

import argparse
import copy
import os
import sys


if os.path.dirname(os.path.dirname(os.path.abspath(__file__))) not in sys.path:  # pragma: no cover
    # 直接 `python scripts/xxx.py` 跑时 sys.path[0] 是 scripts/，仓库根不在路径上 ⇒ 补上
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="bundle 裁剪脱敏（默认丢密钥引用/依赖版本/metadata）")
    parser.add_argument("--in", dest="source", required=True, help="输入 bundle.json")
    parser.add_argument("--out", dest="target", required=True, help="输出路径")
    parser.add_argument("--keep-secrets", action="store_true", help="保留 secrets.refs")
    parser.add_argument("--keep-env-items", action="store_true", help="保留 environment.items")
    parser.add_argument("--keep-metadata", action="store_true", help="保留顶层 metadata")
    return parser


def trim_bundle(bundle, *, keep_secrets=False, keep_env_items=False,
                keep_metadata=False):
    """裁剪脱敏（纯函数；返回 `(trimmed, report)`，不改传入对象）

    【为什么清 items 就要把 offline_ready 置 false】没有依赖清单，"覆盖全部声明依赖"
    就无法复核；保留一个无法验证的 true，正是本仓最忌讳的"字段在、没人读"的反面 ——
    字段在、没人**能**读。
    """
    out = copy.deepcopy(dict(bundle))
    report = {"refs_dropped": 0, "env_items_dropped": 0, "metadata_dropped": False,
              "offline_ready_forced_false": False, "notes": []}

    secrets = out.get("secrets")
    if not keep_secrets and isinstance(secrets, dict):
        refs = secrets.get("refs")
        if isinstance(refs, list):
            report["refs_dropped"] = len(refs)
        secrets["refs"] = []
        if report["refs_dropped"]:
            report["notes"].append("secrets.refs 已清空：到达端需自行配置凭据引用")

    env = out.get("environment")
    if not keep_env_items and isinstance(env, dict):
        items = env.get("items")
        if isinstance(items, list):
            report["env_items_dropped"] = len(items)
        env["items"] = []
        if env.get("offline_ready") is True:
            env["offline_ready"] = False
            report["offline_ready_forced_false"] = True
        if report["env_items_dropped"]:
            report["notes"].append("environment.items 已清空：不暴露导出机精确版本，offline_ready 无法复核故置 false")

    if not keep_metadata and "metadata" in out:
        out.pop("metadata", None)
        report["metadata_dropped"] = True
        report["notes"].append("顶层 metadata 已移除")
    return out, report


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    from agent.subagent.bundle import (bundle_from_json, bundle_to_json, validate_bundle)
    from agent.subagent.credentials import assert_manifest_secret_free

    try:
        with open(args.source, "r", encoding="utf-8") as fh:
            bundle = bundle_from_json(fh.read())
    except (OSError, ValueError) as exc:
        print("bundle 不可读/不合法: %s" % exc, file=sys.stderr)
        return 2

    trimmed, report = trim_bundle(
        bundle, keep_secrets=args.keep_secrets,
        keep_env_items=args.keep_env_items, keep_metadata=args.keep_metadata)
    problems = validate_bundle(trimmed)
    if problems:
        print("裁剪结果不合法：\n" + "\n".join(problems[:12]), file=sys.stderr)
        return 4
    assert_manifest_secret_free(trimmed)

    parent = os.path.dirname(os.path.abspath(args.target))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(args.target, "w", encoding="utf-8") as fh:
        fh.write(bundle_to_json(trimmed))
    print("[trim-bundle] refs_dropped=%d env_items_dropped=%d metadata_dropped=%s "
          "offline_ready_forced_false=%s"
          % (report["refs_dropped"], report["env_items_dropped"],
             report["metadata_dropped"], report["offline_ready_forced_false"]))
    for note in report["notes"]:
        print("[trim-bundle] " + note)
    print("[trim-bundle] 已写出: %s" % args.target)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
