"""开关登记表 config_path 的**逐键对拍**工具（L5 验收用）

【为什么需要它】
    `docs/closeout/开关登记表_config口径审计_20260922.md` 认定：398 个空 config_path
    的键里有 55 个**确实读 config.yaml**，登记表却没写路径 ⇒ 开关中心把
    「config.yaml 里显式写了值」谎报成「代码默认值」。补登记修的是**来源口径**，
    但补登记也会让这些键**第一次**受 config.yaml 影响 ⇒ 必须逐键证明：

        ① 配置层**不提供**该路径时，`resolve()` 的来源与取值与补登记**前完全一致**
           （即：补登记不会凭空改变任何键的生效值）；
        ② 配置层**提供**该路径时，来源变为 `config`，且取值**逐字等于**配置里的值
           （即：修好之后不再谎报）。

    本脚本把这两条对每个「声明了 config.yaml 文件路径」的键逐个跑一遍并打印成表，
    因此可以在补登记**前后各跑一次**做对拍（`--json` 落盘便于 diff）。

【不写任何真实文件】
    - config.yaml 只读一次取 mtime，随后**用内存里的合成配置**喂给 resolver
      （`resolver._CONFIG_CACHE` 是 mtime 键控缓存，喂同 mtime 即命中）；
    - 覆盖层用**临时目录**里的空 store（★ 绝不读写 `data/ui_settings.json`）。

【用法】
    python scripts/audit_settings_config_path_parity.py              # 人读表
    python scripts/audit_settings_config_path_parity.py --json p.json # 机器可读（对拍用）
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.settings import masking                          # noqa: E402
from agent.settings import resolver as RS                      # noqa: E402
from agent.settings.overrides import OverrideStore             # noqa: E402
from agent.settings.registry import RISK_C, all_specs          # noqa: E402

#: 探针文本（路径型/字符串型开关的取值；任何真实默认值都不可能是它）
PROBE_TEXT = "<<parity-probe>>"


def declared_file_path_specs():
    """登记了 **config.yaml 文件路径** 的开关（排除 ObservabilityConfig 运行态路径）"""
    obs = RS.observability_rule_paths()
    return [s for s in all_specs() if s.config_path and s.config_path not in obs]


def _probe_value(spec) -> Any:
    if spec.type == "bool":
        return (not spec.default) if isinstance(spec.default, bool) else True
    if spec.type == "int":
        return 4242
    if spec.type == "float":
        return 42.5
    return f"{PROBE_TEXT}:{spec.key}"


def _synthetic(path_value_pairs: Dict[str, Any]) -> Dict[str, Any]:
    """`{"a.b.c": v}` → 嵌套 dict"""
    out: Dict[str, Any] = {}
    for dotted, value in path_value_pairs.items():
        node = out
        parts = dotted.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    return out


def _install_config(data: Dict[str, Any]) -> None:
    """把合成配置塞进 resolver 的 mtime 缓存（**不改磁盘上的 config.yaml**）"""
    path = RS.config_yaml_path()
    try:
        mtime = path.stat().st_mtime if path.exists() else -1.0
    except OSError:                                        # pragma: no cover
        mtime = -1.0
    RS._CONFIG_CACHE["mtime"] = mtime
    RS._CONFIG_CACHE["data"] = data


def _store() -> OverrideStore:
    """隔离的空覆盖层（★ 不碰 data/ui_settings.json）"""
    tmp = Path(tempfile.mkdtemp(prefix="cp-parity-"))
    return OverrideStore(tmp / "ui_settings.json")


def run() -> List[Dict[str, Any]]:
    store = _store()
    rows: List[Dict[str, Any]] = []
    for spec in declared_file_path_specs():
        probe = _probe_value(spec)
        env_set = bool(spec.env_name) and spec.env_name in os.environ
        # ① 配置层不提供
        _install_config({})
        before = RS.resolve(spec.key, store=store)
        # ② 配置层提供该路径
        _install_config(_synthetic({spec.config_path: probe}))
        after = RS.resolve(spec.key, store=store)
        rows.append({
            "key": spec.key,
            "config_path": spec.config_path,
            "owner_module": spec.owner_module,
            "type": spec.type,
            "default": spec.default,
            "env_name": spec.env_name,
            "env_present_in_shell": env_set,
            "probe": probe,
            "no_config_source": before.source if before else None,
            "no_config_value": before.value if before else None,
            "with_config_source": after.source if after else None,
            "with_config_value": after.value if after else None,
            "source_ok": bool(after and after.source == RS.SOURCE_CONFIG),
            # C 级（只读脱敏）**按设计**不返回明文（resolver 把 value 抹成 None），
            # 故对拍用指纹比对，而不是拿不到明文就判 FAIL。
            "value_ok": bool(after and (
                after.fingerprint == masking.fingerprint(probe)
                if spec.risk == RISK_C else after.value == probe)),
            "masked_compare": spec.risk == RISK_C,
        })
    _install_config({})                    # 恢复，不给后续调用留下合成配置
    return rows


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", default="", help="把结果写成 JSON（对拍用）")
    args = parser.parse_args(argv)

    rows = run()
    if args.json:
        Path(args.json).write_text(
            json.dumps({"rows": rows}, ensure_ascii=False, indent=2),
            encoding="utf-8")

    print("=" * 118)
    print("开关登记表 config_path 逐键对拍（空配置层 vs 探针配置层）")
    print("=" * 118)
    print(f"{'key':44s} {'config_path':46s} {'空配置':10s} {'探针配置':10s} 结论")
    print("-" * 118)
    bad = []
    skipped = []
    for r in rows:
        if r["env_present_in_shell"]:
            verdict = "SKIP(env 已设)"
            skipped.append(r["key"])
        elif r["no_config_source"] != RS.SOURCE_DEFAULT:
            verdict = f"SKIP(空配置来源={r['no_config_source']})"
            skipped.append(r["key"])
        elif r["source_ok"] and r["value_ok"]:
            verdict = "OK"
        else:
            verdict = "FAIL"
            bad.append(r["key"])
        print(f"{r['key']:44s} {r['config_path']:46s} "
              f"{str(r['no_config_source']):10s} {str(r['with_config_source']):10s} {verdict}")
    print("-" * 118)
    print(f"合计 {len(rows)} 键：OK {len(rows) - len(bad) - len(skipped)}，"
          f"FAIL {len(bad)}，SKIP {len(skipped)}")
    if bad:
        print("FAIL：" + ", ".join(bad))
    if skipped:
        print("SKIP：" + ", ".join(skipped))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
