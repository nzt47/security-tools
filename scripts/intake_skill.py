#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""技能入轨（skill intake）—— 把 `data/skills_repo/<id>/` 从「只在本机」变成「在 git 里」。

【为什么需要】§11.32 C · N5（2026-10-31）：`autoresearch` / `pdf` 由界面 backfill
登记后一直只在本机。手工入轨实测要跑 6 步，**每一步漏掉都会在 CI 才红**：

    1. 审 skill.md 形状（`scripts/validate_skill_md.py`）+ 清掉 `name` 里的
       「（自动整理）」这类**回填残留后缀**（不清就会被永久冻进仓库，§11.21 清过一轮）；
    2. `git add` 该技能目录（**不能** `git add -A`：本机 `data/_*.py` 草稿会被一起带上）；
    3. 在**干净 worktree** 里重派生 `data/capability_manifest.json` +
       `docs/rfc/云枢能力清单盘点表.md`（本机工作区跑会因 untracked 草稿与 CI 不一致，§11.31 C-2）；
    4. 刷新能力面的**精确基线**（`== 133` / `by_kind` / `envelope 132` …，6 处，
       分布在不同测试文件里，靠人肉 grep 很容易漏）；
    5. 校验三件套（validate / dual-track / 相关 pytest）；
    6. 提交 + PR（UI 做不到：materialize/backfill 只写运行期数据，不会 `git add`）。

本脚本把 1–4 收成一条命令，并把 5/6 该跑什么打出来。

【口径】
- **默认干跑**（同 `backfill_skill_usage_sections.py` / `deploy_workbench.py`）：
  只校验 + 列出将做什么，不写任何文件、不动 index；
- 只 add **该技能目录**；已入轨的技能跳过 add（幂等）；
- 派生物在 `<repo>/.worktrees/intake-<ts>`（`--detach`）里生成后**拷回**主工作区，
  worktree 用完即删；不需要把改动先提交；
- 基线只在 `--fix-baselines` 时改，且逐处打印 `old → new`，**必须 review git diff**；
- 技能 id 只用于**目录名等值匹配**（不拼路径、不进 argv），故非法 id 既不能穿越也不能注入。

用法：
    python scripts/intake_skill.py <skill_id> [<skill_id> ...]                 # 干跑
    python scripts/intake_skill.py <skill_id> --execute                        # 校验 + git add + 重派生
    python scripts/intake_skill.py <skill_id> --execute --fix-baselines        # 再刷新能力面基线
    python scripts/intake_skill.py <skill_id> --json                           # 机器可读计划

退出码：0 成功（干跑也算）/ 1 校验失败或写失败 / 2 参数或路径错误。
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.skills_mgmt.skill_md_schema import validate_file  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SKILLS_REPO = ROOT / "data" / "skills_repo"
MANIFEST_REL = Path("data") / "capability_manifest.json"
INVENTORY_REL = Path("docs") / "rfc" / "云枢能力清单盘点表.md"
SYNC_SCRIPT_REL = Path("scripts") / "sync_capability_manifest.py"

#: 技能 id 契约（与 `file_store._validate_skill_id` / `skill_md_schema._ID_RE` 同口径）。
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]*$")

#: 回填/批量整理留下的 name 后缀（§11.21 / §11.32 C·N5 两次实测都踩过）。
_AUTO_SUFFIX_RE = re.compile(r"[（(](?:自动整理|自动|整理)[）)]\s*$")

#: 能力面的**精确基线**落点。每项：文件名 → (正则, 取值键)。
#: 【为什么是硬编码表而不是全仓 grep「== 133」】这些用例钉的是"提交基线"，
#: 口径各不相同（entries / stats.total / by_kind / by_location / enabled 子集 /
#: 索引 / envelope），用一张显式表比正则乱扫安全得多：改不到=红色提示，不会误改别处。
_BASELINE_SPECS: Sequence[Tuple[str, "re.Pattern[str]", str]] = (
    ("tests/unit/test_capability_spec.py",
     re.compile(r'(assert len\(manifest\["entries"\]\) == )(\d+)'), "entries"),
    ("tests/unit/test_capregistry_core.py",
     re.compile(r'(assert st\["total"\] == )(\d+)(, f"能力总数应为 )(\d+)'), "stats_total"),
    ("tests/unit/test_capregistry_core.py",
     re.compile(r'(assert st\["by_kind"\] == \{"skill": )(\d+)(, "tool": )(\d+)(\})'), "by_kind"),
    ("tests/unit/test_capregistry_core.py",
     re.compile(r'(assert st\["by_location"\] == \{"local": )(\d+)(, "remote": )(\d+)(\})'), "by_location"),
    ("tests/unit/test_capregistry_core.py",
     re.compile(r'(assert len\(tools\) == )(\d+)( and len\(skills\) == )(\d+)'), "query_counts"),
    ("tests/unit/test_capregistry_core.py",
     re.compile(r'(assert idx\["primary"\] == )(\d+)'), "stats_total"),
    ("tests/unit/test_capregistry_core.py",
     re.compile(r'(assert env\["data"\]\["returned"\] == env\["data"\]\["total"\] == )(\d+)'),
     "envelope_total"),
    ("tests/unit/test_capregistry_core.py",
     re.compile(r'(assert env\["data"\]\["total"\] == )(\d+)'), "envelope_total"),
    ("tests/unit/test_confirm_level.py",
     re.compile(r'(assert len\(skills\) == )(\d+)(, len\(skills\))'), "skill_total"),
)


def _run(cmd: Sequence[str], cwd: Path, *, timeout: int = 600) -> subprocess.CompletedProcess:
    """跑一条**固定 argv** 的外部命令（动态部分只经 cwd 传入，不进 argv）。"""
    return subprocess.run(
        list(cmd), cwd=str(cwd), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout, shell=False,
    )


def resolve_skill_dir(skill_id: str) -> Optional[Path]:
    """按**目录名等值匹配**取技能目录（不拼路径 ⇒ 非法 id 无法穿越目录）。"""
    if not SKILLS_REPO.is_dir():
        return None
    for child in SKILLS_REPO.iterdir():
        if child.is_dir() and child.name == skill_id:
            return child
    return None


def check_skill(skill_dir: Path) -> Dict[str, object]:
    """形状校验 + 残留后缀检查（error 阻断，warning 只提示）。"""
    md = skill_dir / "skill.md"
    if not md.is_file():
        return {"skill_id": skill_dir.name, "errors": ["缺少 skill.md"], "warnings": []}
    result = validate_file(md, dir_name=skill_dir.name)
    errors: List[str] = list(result["errors"])
    warnings: List[str] = list(result["warnings"])
    text = md.read_text(encoding="utf-8", errors="replace")
    name = ""
    for line in text.splitlines():
        if line.startswith("name:"):
            name = line[len("name:"):].strip().strip("'\"")
            break
    if name and _AUTO_SUFFIX_RE.search(name):
        errors.append(
            "name 残留回填后缀 %r —— 先走生产路径改名再入轨：\n"
            "    POST /api/skills-mgmt/%s/redraft/apply {\"name\": \"%s\"}"
            % (name, skill_dir.name, _AUTO_SUFFIX_RE.sub("", name)))
    return {"skill_id": skill_dir.name, "name": name, "errors": errors, "warnings": warnings}


def is_tracked(skill_dir: Path) -> bool:
    """该技能目录是否已在 git index 里（用 cwd 传目录，argv 保持固定）。"""
    if not (skill_dir / "skill.md").is_file():
        return False
    proc = _run(["git", "ls-files", "--error-unmatch", "--", "skill.md"], skill_dir)
    return proc.returncode == 0


def stage_skill(skill_dir: Path) -> Tuple[bool, str]:
    """只 add 该技能目录（`git add -- .`，cwd 锁在技能目录）。"""
    proc = _run(["git", "add", "--", "."], skill_dir)
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout).strip()[:400]
    return True, ""


#: 生成戳（JSON 的 generated_at / 盘点表的「生成时间」）**不算内容差异** ——
#: 否则每跑一次入轨都会留一条"只改时间"的假 diff，人会习惯性忽略整份派生物。
_TS_RE = re.compile(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?")


def _same_ignoring_stamp(name: str, old: bytes, new: bytes) -> bool:
    """两份派生物在**忽略生成戳**后是否等价（等价就不覆盖，避免时间戳噪音）。"""
    if not old:
        return False
    if old == new:
        return True
    if name.endswith(".json"):
        try:
            a = json.loads(old.decode("utf-8"))
            b = json.loads(new.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return False
        a.pop("generated_at", None)
        b.pop("generated_at", None)
        return a == b
    try:
        old_text = _TS_RE.sub("<TS>", old.decode("utf-8"))
        new_text = _TS_RE.sub("<TS>", new.decode("utf-8"))
    except UnicodeDecodeError:
        return False
    return old_text == new_text


def derive_manifest(skill_dirs: Iterable[Path]) -> Dict[str, object]:
    """在干净 worktree 里重派生清单/盘点表，并拷回主工作区。

    【为什么必须另起 worktree】§11.31 C-2：本机 `data/_*.py` 这些 untracked 草稿
    会被静态扫描计入 unresolved ⇒ 产物与 CI 不一致。worktree 里只补进本次要入轨的
    技能目录（其余保持 HEAD 原样），派生口径即 CI 口径。
    """
    worktree = ROOT / ".worktrees" / ("intake-%d" % int(time.time()))
    added = [d for d in skill_dirs]
    try:
        proc = _run(["git", "worktree", "add", str(worktree), "HEAD", "--detach"], ROOT)
        if proc.returncode != 0:
            return {"ok": False, "error": (proc.stderr or proc.stdout).strip()[:400]}
        for src in added:
            dst = worktree / "data" / "skills_repo" / src.name
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
        proc = _run([sys.executable, str(SYNC_SCRIPT_REL)], worktree)
        if proc.returncode != 0:
            return {"ok": False, "error": (proc.stderr or proc.stdout).strip()[-800:]}
        copied = []
        for rel in (MANIFEST_REL, INVENTORY_REL):
            src_file = worktree / rel
            dst_file = ROOT / rel
            if not src_file.is_file():
                continue
            new_bytes = src_file.read_bytes()
            old_bytes = dst_file.read_bytes() if dst_file.is_file() else b""
            if not _same_ignoring_stamp(dst_file.name, old_bytes, new_bytes):
                dst_file.parent.mkdir(parents=True, exist_ok=True)
                dst_file.write_bytes(new_bytes)
                copied.append(str(rel))
        return {"ok": True, "changed": copied, "stdout": proc.stdout.strip()[-800:]}
    finally:
        _run(["git", "worktree", "remove", str(worktree), "--force"], ROOT)


def compute_baselines(manifest: Dict[str, object]) -> Dict[str, int]:
    """从清单算出 6 个基线数（与 `CapabilityRegistry.stats()` 的口径对齐）。"""
    entries = manifest.get("entries") or []
    skills = [e for e in entries if e.get("kind") == "skill"]
    tools = [e for e in entries if e.get("kind") == "tool"]
    enabled = [e for e in entries if e.get("enabled")]
    return {
        "entries": len(entries),
        "stats_total": len(entries),
        "skill_total": len(skills),
        "tool_total": len(tools),
        "local": sum(1 for e in entries if e.get("location") == "local"),
        "remote": sum(1 for e in entries if e.get("location") == "remote"),
        "enabled_skills": sum(1 for e in enabled if e.get("kind") == "skill"),
        #: envelope 走 query(enabled_only=True) ⇒ 比 stats 少掉 disabled 的那几条（gstack）
        "envelope_total": len(enabled),
    }


#: 一条正则里要替换多位数时，键 → 取值顺序（与正则里奇数分组的顺序一致）。
_MULTI_KEYS: Dict[str, Sequence[str]] = {
    "by_kind": ("skill_total", "tool_total"),
    "by_location": ("local", "remote"),
    "query_counts": ("tool_total", "enabled_skills"),
}


def rewrite_baselines(numbers: Dict[str, int], *, execute: bool,
                     root: Path = ROOT) -> List[Dict[str, object]]:
    """把 `_BASELINE_SPECS` 里的数字刷成新值，返回逐处 old → new。

    正则分组里，奇数号（1/3/5…，0 基）是数字位，本函数只替换这些位，保留原注释与格式；
    `root` 可注入（单测用 tmp 目录，不碰真实用例文件）。
    """
    changes: List[Dict[str, object]] = []
    for rel, pattern, key in _BASELINE_SPECS:
        path = root / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        wants = [str(numbers[k]) for k in _MULTI_KEYS.get(key, (key,))]

        def _sub(match: "re.Match[str]") -> str:
            # 分组约定：偶数位是标签（"assert … == "），**奇数位才是数字**
            parts = list(match.groups())
            slot = 0
            for i in range(1, len(parts), 2):
                if parts[i] is not None:
                    parts[i] = wants[min(slot, len(wants) - 1)]
                    slot += 1
            return "".join(p for p in parts if p is not None)

        new_text, n = pattern.subn(_sub, text, count=1)
        if n and new_text != text:
            hit = pattern.search(text)
            old = [g for i, g in enumerate(hit.groups()) if i % 2 == 1 and g] if hit else []
            changes.append({"file": rel, "key": key, "old": old, "new": wants})
            if execute:
                path.write_text(new_text, encoding="utf-8")
    return changes


# ════════════════════════════════════════════════════════════
#  程序化入口（REST `/api/skills-mgmt/intake/status|intake` 用同一份实现）
# ════════════════════════════════════════════════════════════

def tracked_skill_ids() -> set:
    """已提交进 git 的技能目录名（一次 `git ls-files`，argv 固定）。"""
    proc = _run(["git", "ls-files", "--", "data/skills_repo"], ROOT)
    ids = set()
    if proc.returncode != 0:
        return ids
    for line in (proc.stdout or "").splitlines():
        parts = line.strip().replace("\\", "/").split("/")
        if len(parts) >= 3 and parts[0] == "data" and parts[1] == "skills_repo":
            ids.add(parts[2])
    return ids


def pending_report() -> List[Dict[str, object]]:
    """文件轨里的每条技能 + 是否已入轨 + 形状告警（给界面「入轨助手」列表用）。"""
    if not SKILLS_REPO.is_dir():
        return []
    tracked = tracked_skill_ids()
    out: List[Dict[str, object]] = []
    for child in sorted(SKILLS_REPO.iterdir(), key=lambda p: p.name):
        if not child.is_dir() or child.name.startswith("."):
            continue
        if not (child / "skill.md").is_file():
            continue
        info = check_skill(child)
        # 干跑算一遍「能自动补什么 / 要人补什么」，界面据此显示「可自动补全 N 项」
        meta = fix_front_matter(child, execute=False)
        out.append({
            "skill_id": child.name,
            "tracked": child.name in tracked,
            "errors": list(info.get("errors") or []),
            "warnings": list(info.get("warnings") or []),
            "fixable": list(meta.get("fixed") or []) if meta.get("ok") else [],
            "needs_human": list(meta.get("needs_human") or []) if meta.get("ok") else [],
        })
    return out


def intake_one(skill_id: str, *, execute: bool = False,
               fix_baselines: bool = False, fix_meta: bool = False) -> Dict[str, object]:
    """单技能入轨（界面按钮与 CLI 共用）：自动补全 → 校验 → git add → 派生 → 基线。

    返回结构化报告（不 print）：`ok` / `stage` / `warnings` / `meta_fixed` /
    `needs_human` / `derived_changed` / `baseline_changes` / `numbers`。
    `commit/push/PR` 走单独的 `open_pr()`，本函数**不提交**（保持"可干跑"的性质）。
    """
    if not _ID_RE.match(skill_id or ""):
        return {"ok": False, "stage": "validate", "error": "非法技能 id"}
    skill_dir = resolve_skill_dir(skill_id)
    if skill_dir is None:
        return {"ok": False, "stage": "validate", "error": "文件轨目录不存在"}

    # 自动补全（只在 fix_meta 时跑；干跑同样只算不写）
    meta: Dict[str, object] = {}
    if fix_meta:
        meta = fix_front_matter(skill_dir, execute=execute)
        if not meta.get("ok"):
            return {"ok": False, "stage": "fix-meta", "skill_id": skill_id,
                    "error": meta.get("error")}

    info = check_skill(skill_dir)
    if info.get("errors"):
        return {"ok": False, "stage": "validate", "skill_id": skill_id,
                "errors": list(info["errors"]), "warnings": list(info.get("warnings") or [])}

    tracked = is_tracked(skill_dir)
    report: Dict[str, object] = {
        "ok": True, "skill_id": skill_id, "tracked_before": tracked,
        "warnings": list(info.get("warnings") or []), "mode": "execute" if execute else "dry-run",
        "meta_fixed": list(meta.get("fixed") or []),
        "needs_human": list(meta.get("needs_human") or []),
    }
    if not execute:
        return report

    if not tracked:
        ok, err = stage_skill(skill_dir)
        if not ok:
            return {"ok": False, "stage": "git-add", "skill_id": skill_id, "error": err}
    derived = derive_manifest([skill_dir])
    if not derived.get("ok"):
        return {"ok": False, "stage": "derive", "skill_id": skill_id,
                "error": str(derived.get("error"))}
    numbers = compute_baselines(collect_manifest())
    report["derived_changed"] = list(derived.get("changed") or [])
    report["numbers"] = numbers
    if fix_baselines:
        report["baseline_changes"] = rewrite_baselines(numbers, execute=True)
    report["next_steps"] = [
        "python scripts/validate_skill_md.py && python scripts/check_skills_dual_track.py",
        "python scripts/intake_skill.py %s --execute --fix-meta --fix-baselines --pr" % skill_id,
    ]
    return report





# ════════════════════════════════════════════════════════════
#  front matter 自动补全（界面「一键入轨」前的自动修/自动补）
# ════════════════════════════════════════════════════════════
#  【不易】自动补全的**边界**：只补"能确定性推导"的字段，绝不翻译/编造。
#  - 机械默认值（version/status/category/author/tags/content_type/enabled/id）
#    有唯一安全取值 ⇒ 直接补，diff 一行行看得见；
#  - description_zh：只有当 description 本身已是中文时才**照抄**，英文描述不翻；
#  - name 的「（自动整理）」后缀：直接清（§11.21 / §11.32 C·N5 两次踩过）；
#  - 「何时使用」段落：**不生成**。§11.33 N4 的口径是"只从正文提炼、别编造"，
#    自动补会把没写过的适用场景冻进仓库 ⇒ 只提示"需要人补"。

#: 缺省值：key → 默认值。都是"补了不会歪曲事实"的机械字段。
_FM_DEFAULTS: Sequence[Tuple[str, str]] = (
    ("version", "0.1.0"),
    ("status", "draft"),
    ("category", "custom"),
    ("author", "yunshu-intake"),
    ("tags", "[]"),
    ("content_type", "markdown"),
    ("enabled", "true"),
)

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _split_front_matter(text: str) -> Tuple[List[str], List[str]]:
    """切成 (front matter 行, 其余行)；没有 front matter 时返回 ([], 全部行)。"""
    lines = text.splitlines()
    # 没有 front matter ⇒ 返回空 tail（调用方据 "not tail" 报错；不能把正文当 front matter 补）
    if not lines or lines[0].strip() != "---":
        return [], []
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            return lines[1:idx], lines[idx:]
    return [], lines


def _fm_keys(fm_lines: Sequence[str]) -> Dict[str, str]:
    """front matter 里的顶层 key → 值（不解析嵌套，够用且不误判）。"""
    out: Dict[str, str] = {}
    for line in fm_lines:
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_\-]*):[ \t]*(.*)$", line)
        if m:
            out[m.group(1)] = m.group(2).strip()
    return out


def fix_front_matter(skill_dir: Path, *, execute: bool = False) -> Dict[str, object]:
    """补齐可确定性推导的 front matter 字段（默认只算不写）。

    Returns:
        {"ok": bool, "fixed": [key...], "needs_human": [说明...], "changed": bool, "error"?: str}
    """
    md = skill_dir / "skill.md"
    if not md.is_file():
        return {"ok": False, "error": "缺少 skill.md", "fixed": [], "needs_human": []}
    text = md.read_text(encoding="utf-8", errors="replace")
    fm, tail = _split_front_matter(text)
    if not tail:
        return {"ok": False, "error": "缺少 front matter（首行须为 ---）",
                "fixed": [], "needs_human": []}

    keys = _fm_keys(fm)
    fixed: List[str] = []
    needs_human: List[str] = []
    out_fm = list(fm)

    def _set_or_append(key: str, value: str) -> bool:
        """有该键就改值，没该键就追加（都只碰这一行）。返回是否真的改了。"""
        pat = re.compile(r"^%s:[ \t]*(.*)$" % re.escape(key))
        for i, line in enumerate(out_fm):
            m = pat.match(line)
            if m:
                if m.group(1).strip() == str(value):
                    return False
                out_fm[i] = "%s: %s" % (key, value)
                return True
        out_fm.append("%s: %s" % (key, value))
        return True

    # id 必须是目录名（schema 硬约束）—— 等值写入，不做"猜"
    if _set_or_append("id", skill_dir.name):
        fixed.append("id")

    # name 的自动整理后缀：就地清掉（不是阻断项）
    new_fm: List[str] = []
    for line in out_fm:
        m = re.match(r"^(name:[ \t]*)(.*)$", line)
        if m and _AUTO_SUFFIX_RE.search(m.group(2).strip().strip("'\"")):
            raw = m.group(2).strip().strip("'\"")
            new_fm.append("%s%s" % (m.group(1), _AUTO_SUFFIX_RE.sub("", raw)))
            fixed.append("name(去后缀)")
        else:
            new_fm.append(line)
    out_fm = new_fm
    keys = _fm_keys(out_fm)

    # 机械默认值（缺键就补；有键但值为空也补 —— tags 这类块序列有子项，值本来就为空）
    for key, default in _FM_DEFAULTS:
        if key in keys and keys[key]:
            continue
        if key == "tags" and any(l.startswith("- ") for l in out_fm):
            continue
        if _set_or_append(key, default):
            fixed.append(key)

    # description_zh：只在 description 已是中文时照抄（不翻译）
    if not (keys.get("description_zh") or "").strip():
        desc = keys.get("description", "").strip().strip("'\"")
        if desc and _CJK_RE.search(desc):
            if _set_or_append("description_zh", desc):
                fixed.append("description_zh")
        else:
            needs_human.append("description_zh：description 非中文或为空，需人写中文描述（不自动翻译）")

    if not re.search(r"^##\s*(何时使用|When to Use)\s*$", text, re.MULTILINE):
        needs_human.append("「何时使用 / When to Use」段落缺失：按 §11.33 N4 只能从正文提炼，需人补")

    changed = out_fm != list(fm)
    if changed and execute:
        md.write_text("---\n" + "\n".join(out_fm) + "\n" + "\n".join(tail), encoding="utf-8")
    return {"ok": True, "fixed": fixed, "needs_human": needs_human, "changed": changed}


# ════════════════════════════════════════════════════════════
#  commit / push / PR（入轨的最后一步，原先只能人肉跑）
# ════════════════════════════════════════════════════════════

def allowed_commit_paths(skill_id: str) -> List[str]:
    """允许被入轨 PR 带上的文件：**只这些**，其余一律拒（防止把 data/_*.py 草稿带上）。"""
    paths = [
        "data/skills_repo/%s/" % skill_id,
        MANIFEST_REL.as_posix(),
        INVENTORY_REL.as_posix(),
    ]
    for rel, _pat, _key in _BASELINE_SPECS:
        if rel not in paths:
            paths.append(rel)
    return paths


def _is_allowed(rel_path: str, skill_id: str) -> bool:
    p = rel_path.replace("\\", "/")
    if p.startswith("data/skills_repo/%s/" % skill_id):
        return True
    return p in allowed_commit_paths(skill_id)


def changed_paths(root: Path = ROOT) -> List[str]:
    """读 git status --porcelain -z（-z 不做路径转义，中文名/空格都安全）。"""
    proc = _run(["git", "status", "--porcelain", "-z", "--untracked-files=all"], root)
    if proc.returncode != 0:
        return []
    out: List[str] = []
    for item in (proc.stdout or "").split("\x00"):
        if len(item) < 4:
            continue
        out.append(item[3:])
    return out


def open_pr(skill_id: str, *, base: str = "master", root: Path = ROOT) -> Dict[str, object]:
    """把当前工作区里**属于该技能入轨**的改动提交、推分支、开 PR。

    前置（任一不满足就拒绝，不半途而废）：
      1. 当前分支必须是 base（默认 master）—— 不在 master 上说明有别的改动混着；
      2. 所有改动都必须落在白名单内（技能目录 + 清单 + 盘点表 + 基线用例）。
    """
    if not _ID_RE.match(skill_id or ""):
        return {"ok": False, "error": "非法技能 id"}
    proc = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], root)
    branch_now = (proc.stdout or "").strip()
    if proc.returncode != 0:
        return {"ok": False, "error": "读不到当前分支: %s" % (proc.stderr or "")[:200]}
    if branch_now != base:
        return {"ok": False,
                "error": "当前分支是 %r，不是 %r；入轨 PR 必须从干净的主线开（先 checkout）"
                         % (branch_now, base)}

    paths = changed_paths(root)
    outside = [p for p in paths if not _is_allowed(p, skill_id)]
    if outside:
        return {"ok": False, "error": "工作区有与本次入轨无关的改动，拒绝提交: %s"
                                      % ", ".join(outside[:8])}
    if not paths:
        return {"ok": False, "error": "没有可提交的改动（先跑 --execute）"}

    numbers = compute_baselines(collect_manifest())
    branch = "intake/%s-%s" % (skill_id, time.strftime("%Y%m%d%H%M"))
    msg = (
        "feat(skills): 入轨 %s（能力面 %d 项）\n\n"
        "由 scripts/intake_skill.py 生成：校验 + git add（仅该技能目录）+ 干净 worktree "
        "重派生能力清单/盘点表 + 刷新精确基线。\n"
        "能力面：entries=%d skill=%d tool=%d local=%d remote=%d envelope=%d"
        % (skill_id, numbers["entries"], numbers["entries"], numbers["skill_total"],
           numbers["tool_total"], numbers["local"], numbers["remote"], numbers["envelope_total"])
    )
    steps = []
    for cmd in (["git", "checkout", "-b", branch],
                ["git", "add", "--"] + paths,
                ["git", "commit", "-m", msg]):
        p = _run(cmd, root)
        steps.append({"cmd": " ".join(cmd[:3]), "rc": p.returncode})
        if p.returncode != 0:
            return {"ok": False, "stage": cmd[1],
                    "error": (p.stderr or p.stdout).strip()[:600], "steps": steps}

    p = _run(["git", "push", "-u", "origin", branch], root)
    steps.append({"cmd": "git push", "rc": p.returncode})
    if p.returncode != 0:
        _run(["git", "checkout", base], root)
        return {"ok": False, "stage": "push", "error": (p.stderr or p.stdout).strip()[:600],
                "steps": steps, "branch": branch}

    body = ("## %s 入轨\n\n- 技能目录：`data/skills_repo/%s/`\n"
            "- 派生物：`data/capability_manifest.json`、`docs/rfc/云枢能力清单盘点表.md`\n"
            "- 基线：能力面 6 处精确基线（entries=%d）\n"
            "- 生成方式：`python scripts/intake_skill.py %s --execute --fix-meta --fix-baselines`\n"
            % (skill_id, skill_id, numbers["entries"], skill_id))
    title = "feat(skills): 入轨 %s" % skill_id
    p = _run(["gh", "pr", "create", "--base", base, "--title", title, "--body", body], root)
    _run(["git", "checkout", base], root)  # PR 建完回主线，避免部署/deploy 脚本踩到 feature 分支
    steps.append({"cmd": "gh pr create", "rc": p.returncode})
    if p.returncode != 0:
        return {"ok": False, "stage": "pr", "error": (p.stderr or p.stdout).strip()[:600],
                "steps": steps, "branch": branch}
    url = ""
    for line in (p.stdout or "").splitlines():
        if line.startswith("http"):
            url = line.strip()
    return {"ok": True, "branch": branch, "pr_url": url, "committed": paths, "steps": steps}


def collect_manifest() -> Dict[str, object]:
    return json.loads((ROOT / MANIFEST_REL).read_text(encoding="utf-8"))


def plan(args) -> Dict[str, object]:
    ids = list(args.skill_ids)
    bad_ids = [i for i in ids if not _ID_RE.match(i)]
    if bad_ids:
        return {"ok": False, "error": "非法技能 id: %s（应匹配 ^[a-z0-9][a-z0-9_-]*$）" % bad_ids}
    if not SKILLS_REPO.is_dir():
        return {"ok": False, "error": "文件轨目录不存在: %s" % SKILLS_REPO}

    items: List[Dict[str, object]] = []
    for sid in ids:
        skill_dir = resolve_skill_dir(sid)
        if skill_dir is None:
            items.append({"skill_id": sid, "errors": ["文件轨目录不存在"], "warnings": []})
            continue
        info = check_skill(skill_dir)
        info["tracked"] = is_tracked(skill_dir)
        items.append(info)

    blocked = [i for i in items if i.get("errors")]
    to_add = [i for i in items if not i.get("errors") and not i.get("tracked")]
    return {
        "ok": not blocked,
        "mode": "execute" if args.execute else "dry-run",
        "items": items,
        "to_add": [i["skill_id"] for i in to_add],
        "blocked": [i["skill_id"] for i in blocked],
    }


def _apply_fix_meta(args) -> Dict[str, Dict[str, object]]:
    """先补 front matter（干跑只算），返回 {id: 报告}。"""
    metas: Dict[str, Dict[str, object]] = {}
    for sid in args.skill_ids:
        if not _ID_RE.match(sid):
            continue
        d = resolve_skill_dir(sid)
        if d is None:
            continue
        metas[sid] = fix_front_matter(d, execute=bool(args.execute))
    return metas


def run(args) -> int:
    metas: Dict[str, Dict[str, object]] = {}
    if getattr(args, "fix_meta", False):
        metas = _apply_fix_meta(args)
        for sid, rep in metas.items():
            if not rep.get("ok"):
                print("[FAIL] %s 自动补全: %s" % (sid, rep.get("error")))
                return 1
            print("[META] %s 已补: %s%s" % (
                sid, rep.get("fixed") or "（无可补字段）",
                "" if args.execute else "（干跑，未写盘）"))
            for hint in rep.get("needs_human") or []:
                print("       ! %s" % hint)

    result = plan(args)
    if result.get("ok") is False and result.get("error"):
        print(result["error"])
        return 2

    items = result["items"]
    for item in items:
        sid = item["skill_id"]
        state = "已入轨" if item.get("tracked") else "待入轨"
        print("[%s] %s（%s）" % ("OK" if not item.get("errors") else "FAIL", sid, state))
        for err in item.get("errors") or []:
            print("  ✗ %s" % err)
        for warn in item.get("warnings") or []:
            print("  ! %s" % warn)

    if not result["ok"]:
        print("\n校验未通过：先修 skill.md（error 必须为 0）再入轨。")
        return 1

    print("\n计划：add %s；在干净 worktree 重派生 %s + %s%s" % (
        result["to_add"] or "（无，均已入轨）", MANIFEST_REL, INVENTORY_REL,
        "；刷新能力面基线" if args.fix_baselines else ""))

    if not args.execute:
        print("（干跑：没有写任何文件、没动 index。加 --execute 才真做。）")
        return 0

    changed_dirs: List[Path] = []
    for sid in result["to_add"]:
        skill_dir = resolve_skill_dir(sid)
        ok, err = stage_skill(skill_dir)
        if not ok:
            print("git add 失败: %s: %s" % (sid, err))
            return 1
        changed_dirs.append(skill_dir)

    derive_inputs = [resolve_skill_dir(i["skill_id"]) for i in items]
    derived = derive_manifest(d for d in derive_inputs if d is not None)
    if not derived.get("ok"):
        print("重派生失败: %s" % derived.get("error"))
        return 1
    print("重派生完成；变更文件: %s" % (derived.get("changed") or "（无变化）"))

    if args.fix_baselines:
        numbers = compute_baselines(collect_manifest())
        changes = rewrite_baselines(numbers, execute=True)
        if not changes:
            print("基线已是新值，无需改动。")
        for ch in changes:
            print("  基线 %s %s: %s → %s" % (ch["file"], ch["key"], ch["old"], ch["new"]))
    else:
        numbers = compute_baselines(collect_manifest())
        print("当前能力面：entries=%d skill=%d tool=%d local=%d remote=%d "
              "enabled_skill=%d envelope=%d（如与测试基线不符，加 --fix-baselines）"
              % (numbers["entries"], numbers["skill_total"], numbers["tool_total"],
                 numbers["local"], numbers["remote"], numbers["enabled_skills"],
                 numbers["envelope_total"]))

    if getattr(args, "pr", False):
        if len(items) != 1:
            print("--pr 一次只能入轨一个技能（当前 %d 个）" % len(items))
            return 2
        rep = open_pr(items[0]["skill_id"])
        if not rep.get("ok"):
            print("建 PR 失败（stage=%s）: %s" % (rep.get("stage", "-"), rep.get("error")))
            return 1
        print("已提交 %d 个文件并开 PR: %s" % (len(rep["committed"]), rep.get("pr_url") or rep.get("branch")))
    else:
        print("\n下一步（不代跑，或加 --pr 一条龙）：")
        print("  python scripts/validate_skill_md.py && python scripts/check_skills_dual_track.py")
        print("  python -m pytest tests/unit/test_capability_spec.py tests/unit/test_capregistry_core.py "
              "tests/unit/test_confirm_level.py -q")
        print("  python scripts/intake_skill.py %s --execute --fix-meta --fix-baselines --pr"
              % items[0]["skill_id"] if items else "  （无技能）")
    if args.json:
        print(json.dumps({"plan": result, "derived": derived}, ensure_ascii=False, indent=1))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="技能入轨：校验 + git add + 干净 worktree 重派生 + 基线")
    parser.add_argument("skill_ids", nargs="+", help="技能 id（= data/skills_repo 下的目录名）")
    parser.add_argument("--execute", action="store_true", help="真正写入（缺省干跑）")
    parser.add_argument("--fix-baselines", action="store_true",
                        help="同时刷新能力面的精确基线（tests/unit 里的 6 处）")
    parser.add_argument("--fix-meta", action="store_true",
                        help="先自动补全 front matter（只补可确定性推导的字段，不翻译不编造）")
    parser.add_argument("--pr", action="store_true",
                        help="入轨后接着 commit → push → 建 PR（要求当前分支是 master）")
    parser.add_argument("--json", action="store_true", help="输出 JSON 摘要")
    args = parser.parse_args(argv)
    if args.fix_baselines and not args.execute:
        parser.error("--fix-baselines 需要与 --execute 一起用")
    if args.pr and not args.execute:
        parser.error("--pr 需要与 --execute 一起用")
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
