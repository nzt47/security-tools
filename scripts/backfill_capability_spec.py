#!/usr/bin/env python3
"""把 `location` 事实判定结果**回填**进 `data/tool_definitions/*.yaml`（TASK-04）

【为什么需要这个脚本（而不是手改 91 个 YAML）】
    `location`（执行位置：local / remote）是 v1.4 的核心新增维度，仓库此前完全不存在。
    它的**判定依据必须是事实**（执行器调用链是否跨进程/协议边界），否则"新增一条能力就漏一条"。
    本脚本把 `agent/lines/location.py` 的**事实判定结果**写进 L1 声明文件，使其成为可审计的
    **钉住值**：此后 `scripts/sync_capability_manifest.py --check` 会把"声明 vs 事实"
    逐条对拍 —— 有人手改错了，CI 立刻非零退出（D1 单一真相源仍然成立：
    权威声明是 YAML，判定器只提供事实，不写清单）。

【不易·三档运行模式】
    --dry-run   只报告将要改什么（**先跑它**）
    --check     只校验：YAML 里的 location 与事实判定不一致 ⇒ 非零退出（CI 用）
    （默认）     备份 + 回填。备份到 `data/backups/tool_definitions_<时间戳>/`，
                 回滚 = 把该目录拷回 `data/tool_definitions/`（TASK-04 §6 的要求）。

【不易·为什么用文本插入而不是 yaml.safe_dump】
    91 个 YAML 里有大量中文注释与手写顺序；`safe_dump` 会**抹掉全部注释**，
    把一次"加一个键"的改动变成无法评审的全文件重写（那是比不改更糟的结果）。
    故本脚本只做**行级插入/替换**，未改动的行逐字节保持原样。

【简易】
    python scripts/backfill_capability_spec.py --dry-run
    python scripts/backfill_capability_spec.py
    python scripts/backfill_capability_spec.py --check
"""
from __future__ import annotations

import argparse
import datetime
import os
import re
import shutil
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

TOOL_DEFS_DIR = os.path.join(_ROOT, "data", "tool_definitions")
BACKUP_ROOT = os.path.join(_ROOT, "data", "backups")

_LOCATION_LINE = re.compile(r"^location:[ \t]*(.*)$", re.MULTILINE)
_REASON_LINE = re.compile(r"^location_reason:.*$", re.MULTILINE)


def _facts() -> dict:
    """`{工具名: 判定结果}`（事实判定，与清单生成同源）"""
    from agent.lines.callability import load_tool_docs, parse_declaration, static_executors
    from agent.lines.location import judge_executor_location

    docs = load_tool_docs(TOOL_DEFS_DIR)
    executors = static_executors()
    out = {}
    for name, doc in docs.items():
        declared = parse_declaration(doc)
        executor = declared["host_executor"] or executors.get(name, "")
        # 注意：**不传 declared** —— 本脚本要的是纯事实值，再拿去和声明对拍
        out[name] = judge_executor_location(executor)
    return out


def _render_block(loc: str, reason: str) -> str:
    """生成要插入的两行（含转义：理由里可能有冒号/引号）"""
    safe = reason.replace('"', "'").replace("\n", " ")
    return f'location: {loc}\nlocation_reason: "{safe}"\n'


def _apply(path: str, loc: str, reason: str) -> tuple[bool, str]:
    """把 location/location_reason 写进一个 YAML（行级插入/替换）

    【不易·写回时必须统一成 LF】本仓库 `core.autocrlf=true`：**blob 里是 LF**。
    若原样写回 CRLF，`git diff` 会把整个文件报成"全文件重写"
    （实测：`write_file.yaml` 报 32 增 30 删，而真实改动只有 2 行；
    只有加 `--ignore-cr-at-eol` 才看得出真实 diff）——
    那会让这次治理改动**无法逐条评审**。故写回前把行尾统一成 LF。

    Returns:
        (是否改动, 变更说明)
    """
    with open(path, "r", encoding="utf-8", newline="") as f:
        text = f.read()
    orig = text
    # 统一成 LF：与 git blob 的行尾一致（见上方说明）
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    nl = "\n"
    block = _render_block(loc, reason).replace("\n", nl)

    if _LOCATION_LINE.search(text):
        text = _LOCATION_LINE.sub(f"location: {loc}", text, count=1)
        if _REASON_LINE.search(text):
            text = _REASON_LINE.sub(
                f'location_reason: "{reason.replace(chr(34), chr(39))}"', text, count=1)
        else:
            text = _LOCATION_LINE.sub(
                f"location: {loc}{nl}"
                f'location_reason: "{reason.replace(chr(34), chr(39))}"', text, count=1)
    else:
        # 插在 `version:` 之后（那里是工具元数据的自然位置）；没有 version 就插在文件末尾。
        m = re.search(r"^version:.*$", text, re.MULTILINE)
        if m:
            text = text[:m.end()] + nl + block.rstrip(nl) + text[m.end():]
        else:
            if not text.endswith(nl):
                text += nl
            text += block
    changed = text != orig
    how = "改写" if _LOCATION_LINE.search(orig) else "插入"
    if changed:
        # 【不易·必须显式指定 newline="\n"】默认的 `newline=None` 会在 Windows 上
        # 把 `\n` 翻译成 `\r\n`（os.linesep 语义）⇒ 又回到 CRLF，等于没修。
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
    return changed, how


def main() -> int:
    ap = argparse.ArgumentParser(description="把 location 事实判定回填进工具 YAML")
    ap.add_argument("--dry-run", action="store_true", help="只报告将要改什么")
    ap.add_argument("--check", action="store_true",
                    help="只校验：YAML 的 location 与事实判定不一致 ⇒ 非零退出")
    ap.add_argument("--only", default="", help="只处理某个工具（排障用）")
    args = ap.parse_args()

    facts = _facts()
    names = sorted(facts)
    if args.only:
        names = [n for n in names if n == args.only]

    mismatches = []
    missing = []
    todo = []
    for name in names:
        path = os.path.join(TOOL_DEFS_DIR, f"{name}.yaml")
        if not os.path.exists(path):
            continue
        with open(path, "r", encoding="utf-8") as f:
            head = f.read()
        m = _LOCATION_LINE.search(head)
        want = facts[name]["location"]
        if not m:
            missing.append(name)
            todo.append((path, name, want, facts[name]["evidence"]))
        elif m.group(1).strip().strip('"').strip("'") != want:
            mismatches.append((name, m.group(1).strip(), want))
            todo.append((path, name, want, facts[name]["evidence"]))

    if args.check:
        bad = 0
        for name, got, want in mismatches:
            print(f"[FAIL] {name}: YAML location={got!r} 与事实判定 {want!r} 不一致")
            bad += 1
        for name in missing:
            print(f"[FAIL] {name}: YAML 缺 location（事实判定为 "
                  f"{facts[name]['location']!r}）")
            bad += 1
        if bad:
            print(f"\n[FAIL] 共 {bad} 条不一致（跑 scripts/backfill_capability_spec.py 回填，"
                  f"或修 agent/lines/location.py 的判定规则）")
            return 1
        print(f"[OK] 全部 {len(names)} 个工具 YAML 的 location 与事实判定一致")
        return 0

    if not todo:
        print(f"[OK] 无需回填：{len(names)} 个工具 YAML 的 location 已与事实判定一致")
        return 0

    print(f"[INFO] 待回填 {len(todo)} 个（缺 location {len(missing)} / 值不一致 "
          f"{len(mismatches)}）")
    for path, name, want, evidence in todo[:10]:
        print(f"   - {name}: → {want}　（依据：{evidence[0][:70]}）")
    if len(todo) > 10:
        print(f"   … 其余 {len(todo) - 10} 条同理")

    if args.dry_run:
        print("[DRY-RUN] 未写任何文件")
        return 0

    # ── 备份（TASK-04 §6 的回滚要求）──
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = os.path.join(BACKUP_ROOT, f"tool_definitions_{stamp}")
    os.makedirs(BACKUP_ROOT, exist_ok=True)
    shutil.copytree(TOOL_DEFS_DIR, backup_dir)
    print(f"[OK] 已备份 {TOOL_DEFS_DIR} → {backup_dir}")

    written = 0
    hows: dict = {}
    for path, name, want, evidence in todo:
        reason = evidence[0] if evidence else "事实判定（见 agent/lines/location.py）"
        changed, how = _apply(path, want, reason)
        if changed:
            written += 1
            hows[how] = hows.get(how, 0) + 1
    print(f"[OK] 已回填 {written} 个 YAML（{hows}）")
    print("      下一步：python scripts/sync_capability_manifest.py && "
          "python scripts/sync_capability_manifest.py --check")
    return 0


if __name__ == "__main__":
    sys.exit(main())
