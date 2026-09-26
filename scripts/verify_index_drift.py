# -*- coding: utf-8 -*-
"""索引漂移巡检 — T1 / S1 / S2 / S3 四道门（只读，退出码 0=PASS / 1=FAIL）

【为什么需要它】审计 Q3（docs/audit_skill_governance/Q3_index_rebuild.md）实测：
    - 索引重建链路 4 处窗口期（W1..W4）**无一条自动**；
    - 夜间 skills-check.yml 完全不碰 .index/cache.json 与 data/skill_vectors/；
    - scripts/sync_tool_index.py --check 只校验 YAML 语法、**不比对索引内容**。
本脚本补齐这 4 个盲区，供 CI / 夜间巡检使用。

四道门（口径与 Q3 §6.1 一致；任一 FAIL 即退出码 1）：
  T1  工具索引内容漂移 == 0
      必须比对**内容**，不能比对 mtime：实测 91/91 个 YAML 的 mtime 都晚于
      data/tool_index.json 而字段内容 0 漂移 => mtime 判据 91/91 全假阳性。
  S1  data/skills_repo/.index/cache.json 缺失 / 多余 / hash 失效 == 0
      （相对文件轨 data/skills_repo/*/skill.md；hash = md5(原始字节)，与
      index_cache._parse_and_store 同源）
  S2  注册表并集 - **代码路径**可召回集合 == 0（两条生产路径逐条判，不是取并集）
      注册表并集 = 主轨 data/skills_mgmt.json ∪ 文件轨目录；
      可召回集合 = 生产代码真正调用的入口的返回值：
        pathA 裸 `SkillFileStore.load_metadata_index()`（`SkillLoader()` 默认形态；
              生产调用点 capregistry/skillsearch.py:53、orchestrator.py:3874）
        pathB `SkillFileStore + SkillIndexCache`（`SkillsMgmtService.__init__` service.py:77-82）
      任一缺口 > 0 ⇒ FAIL。

  【主审计修正 2026-09-25】原口径写的是「cache.json 的 skills ∪ main_track」，
  那是**磁盘分区**而不是**代码路径** ⇒ 主轨条目一被持久化进 `main_track`，
  本门就从 FAIL(7) 变 PASS(0)，而检索行为零变化（**假绿**）。
  实测两条路径并不同（裸路径 23 条 / 服务路径 30 条），只测一条或取并集都会漏判。
  报告同时打印「磁盘口径缺口」作对照，两者不一致时打 **假绿告警**。
      **不**用「注册表并集 - 文件轨」作口径：它恒等于「主轨独有技能数」，永远无法
      反映「主轨是否已被检索路径覆盖」这一待验证事实（恒红的门等于没有门）。
  S3  落盘向量库未覆盖技能数 == 0
      落盘向量库 = data/skill_vectors/native_chroma/chroma.sqlite3（BGE-m3 路无落盘，
      仅在内存）。该库是 _ensure_vector_store 的第二级降级后端：BGE-m3 初始化失败时
      会**自动落到它**并返回只覆盖一部分技能的结果 => 覆盖缺口 = 静默降级召回。
      => 覆盖缺口 = 注册表并集 - 库内条目；库内多余条目同样 FAIL。

附带观察项（只打印，不进退出码）：
  T1-w  tool_index.json 落后最新 YAML mtime（内容一致时允许陈旧，仅 WARN）
  S3-w  落盘向量库覆盖率与库年龄（WARN）；该库被投入使用时会显式告警并置降级标记
        （见 vector_adapter._warn_fallback_store / _DEGRADE_FALLBACK_STORE_LOW_COVERAGE）

【只读】本脚本不写仓库内任何文件、不启动服务、不加载任何 embedding 模型、不重建索引。
  · pathA 为纯读；pathB 会触发 `SkillIndexCache.persist`（派生缓存），故**在临时副本上运行**
    （tempfile 目录，退出即删），仓库内的 `.index/cache.json` 不会被本脚本改写。
  · S1 在 S2 之前计算，因此 S2 的任何刷新都不会"自我满足"S1 的判定。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    import yaml
except ImportError:  # pragma: no cover - 环境缺 pyyaml 时显式报错，不静默跳过得像 PASS
    yaml = None  # type: ignore[assignment]

_SKILL_MD = "skill.md"
_MAIN_TRACK_REL = os.path.join("data", "skills_mgmt.json")
_REPO_REL = os.path.join("data", "skills_repo")
_INDEX_REL = os.path.join(_REPO_REL, ".index", "cache.json")
_TOOL_INDEX_REL = os.path.join("data", "tool_index.json")
_TOOL_DEFS_REL = os.path.join("data", "tool_definitions")
_VECTOR_DB_REL = os.path.join("data", "skill_vectors", "native_chroma", "chroma.sqlite3")


# ============================================================
#  基础工具
# ============================================================

def find_root(explicit: Optional[str] = None) -> str:
    """定位仓库根（显式 --root > cwd > 脚本上级目录）"""
    candidates: List[str] = []
    if explicit:
        candidates.append(os.path.abspath(explicit))
    candidates.append(os.getcwd())
    candidates.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    for cand in candidates:
        if os.path.isdir(os.path.join(cand, "data", "tool_definitions")) and \
           os.path.isdir(os.path.join(cand, "agent", "skills_mgmt")):
            return cand
    raise SystemExit("找不到仓库根：需含 data/tool_definitions 与 agent/skills_mgmt（可用 --root 指定）")


def _read_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _md5_file(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


def _production_recall_sets(root: str) -> Dict[str, Optional[Set[str]]]:
    """用**两条真实生产入口**实测「真正可召回」的技能 id 集合。

    【为什么必须这样测（主审计修正 2026-09-25）】
    本脚本原先用 `cache.json` 的 `skills ∪ main_track` 当「可召回集合」，那是**磁盘存储分区**，
    不是**代码路径**。主轨条目被持久化进 `main_track` 后，该口径立刻报 PASS，
    而检索实际用的 `SkillFileStore.load_metadata_index()` 只返回文件轨那部分 ——
    于是产生「**假绿**」：门变绿了，生产行为零变化。

    【两条路径都必须测（实测两者不同：裸路径 23 条 / 服务路径 30 条）】
      pathA `bare`    裸 `SkillFileStore.load_metadata_index()` —— 即 `SkillLoader()` 的默认形态
                       （loader.py:279 `self.fs = file_store or SkillFileStore()`），
                       生产调用点：`agent/capregistry/skillsearch.py:53`（/capabilities/skills/search）、
                       `agent/orchestrator/orchestrator.py:3874`（_context_assembler_procedural）
      pathB `service` 服务形态 `SkillFileStore + SkillIndexCache` ——
                       `SkillsMgmtService.__init__`（service.py:77-82）装配 svc.loader / svc.injector

    任一路径的缺口 > 0 ⇒ FAIL（不是取并集；两条路径都必须覆盖注册表并集）。
    pathB 会触发 `SkillIndexCache.persist`（派生缓存），故**在临时副本上运行**，
    保证本脚本对仓库仍然只读。

    Returns: {"bare": set|None, "service": set|None}（导入/调用失败为 None ⇒ 调用方必须报 FAIL）
    """
    out: Dict[str, Optional[Set[str]]] = {"bare": None, "service": None}
    try:
        if root not in sys.path:
            sys.path.insert(0, root)
        from agent.skills_mgmt.file_store import SkillFileStore  # noqa: PLC0415

        # ── pathA：裸入口（只读）──
        try:
            store = SkillFileStore(os.path.join(root, _REPO_REL))
            out["bare"] = set(store.load_metadata_index(refresh=True).keys())
        except Exception:  # noqa: BLE001
            out["bare"] = None

        # ── pathB：服务形态（临时副本，避免写仓库内派生缓存）──
        try:
            import shutil  # noqa: PLC0415
            import tempfile  # noqa: PLC0415
            from agent.skills_mgmt.index_cache import SkillIndexCache  # noqa: PLC0415

            tmp = tempfile.mkdtemp(prefix="verify_index_drift_")
            try:
                shutil.copytree(os.path.join(root, _REPO_REL),
                                os.path.join(tmp, "data", "skills_repo"))
                main_src = os.path.join(root, _MAIN_TRACK_REL)
                if os.path.isfile(main_src):
                    shutil.copy2(main_src, os.path.join(tmp, "data", "skills_mgmt.json"))
                fs = SkillFileStore(os.path.join(tmp, "data", "skills_repo"))
                SkillIndexCache(fs)                      # = service.py:80
                out["service"] = set(fs.load_metadata_index(refresh=False).keys())
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
        except Exception:  # noqa: BLE001
            out["service"] = None
    except Exception:  # noqa: BLE001 生产入口不可用 ⇒ 交由调用方报 FAIL（不降级为磁盘口径）
        return out
    return out


def file_track_ids(root: str) -> List[str]:
    """文件轨技能 id（= 有 skill.md 的技能目录，跳过 _ / . 前缀，与 index_cache 同口径）"""
    repo = os.path.join(root, _REPO_REL)
    if not os.path.isdir(repo):
        return []
    out = []
    for name in sorted(os.listdir(repo)):
        if name.startswith((".", "_")):
            continue
        if os.path.isfile(os.path.join(repo, name, _SKILL_MD)):
            out.append(name)
    return out


def main_track_ids(root: str) -> List[str]:
    """主轨技能 id（data/skills_mgmt.json 的 id 字段或 key）"""
    path = os.path.join(root, _MAIN_TRACK_REL)
    if not os.path.isfile(path):
        return []
    data = _read_json(path)
    if not isinstance(data, dict):
        return []
    return sorted(str(v.get("id") or k) for k, v in data.items() if isinstance(v, dict))


def _days_since(stamp: Optional[str]) -> Optional[float]:
    """chroma embeddings.created_at（YYYY-MM-DD HH:MM:SS）-> 距今天数；解析失败返回 None"""
    if not stamp:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return (datetime.now() - datetime.strptime(str(stamp)[:19], fmt)).total_seconds() / 86400.0
        except ValueError:
            continue
    return None


# ============================================================
#  T1 — 工具索引内容漂移
# ============================================================

def check_tool_index(root: str, report: "Report") -> None:
    """比对 data/tool_index.json 与 data/tool_definitions/*.yaml 的集合与字段内容

    口径（Q3 §3.2）：可见 YAML = 非 internal、非 llm_callable:false、非 callable_mode:manual；
    比对字段 = description / version / category / parameter_names（= schema.properties 的键序）。
    """
    defs_dir = os.path.join(root, _TOOL_DEFS_REL)
    index_path = os.path.join(root, _TOOL_INDEX_REL)
    if yaml is None:
        report.fail("T1 缺 pyyaml，无法比对工具索引内容（拒绝把 SKIP 当成 PASS）")
        return
    if not os.path.isfile(index_path):
        report.fail("T1 data/tool_index.json 不存在")
        return
    try:
        index = _read_json(index_path)
        imap = {t["name"]: t for t in index.get("tools", []) if isinstance(t, dict) and t.get("name")}
    except Exception as e:  # noqa: BLE001
        report.fail("T1 tool_index.json 解析失败: %s" % e)
        return

    drift: List[Tuple[str, str]] = []
    visible = 0
    newest_yaml_mtime = 0.0
    for fname in sorted(os.listdir(defs_dir)):
        if not fname.endswith((".yaml", ".yml")):
            continue
        fpath = os.path.join(defs_dir, fname)
        newest_yaml_mtime = max(newest_yaml_mtime, os.path.getmtime(fpath))
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                doc = yaml.safe_load(f) or {}
        except Exception as e:  # noqa: BLE001
            drift.append((fname, "YAML_PARSE_ERROR:%s" % type(e).__name__))
            continue
        if not isinstance(doc, dict):
            drift.append((fname, "YAML_NOT_MAPPING"))
            continue
        if doc.get("internal") is True or doc.get("llm_callable") is False or \
           str(doc.get("callable_mode") or "").strip().lower() == "manual":
            continue  # 设计内排除（Q3 §3.2：91 -> 90）
        visible += 1
        name = doc.get("name") or fname
        entry = imap.get(name)
        if entry is None:
            drift.append((name, "ABSENT_IN_INDEX"))
            continue
        for field in ("description", "version", "category"):
            if entry.get(field) != doc.get(field):
                drift.append((name, field))
        props = ((doc.get("schema") or {}).get("properties") or {})
        if list(entry.get("parameter_names") or []) != list(props.keys()):
            drift.append((name, "parameter_names"))

    report.note("[T1] tool_index=%d  YAML可见=%d  内容漂移=%d" % (len(imap), visible, len(drift)))
    if len(imap) != visible:
        report.fail("T1 条目数不等（索引 %d vs YAML 可见 %d）" % (len(imap), visible))
    if drift:
        report.fail("T1 工具索引内容漂移 %d 项：%s" % (len(drift), drift[:10]))
    if newest_yaml_mtime > os.path.getmtime(index_path):
        report.warn("T1-w tool_index.json 落后最新 YAML mtime（内容一致时允许陈旧；刷新请手跑 scripts/sync_tool_index.py）")


# ============================================================
#  S1 — 元数据索引 vs 文件轨（缺失 / 多余 / hash 失效）
#  S2 — 注册表并集 vs **生产入口**可召回集合（不是磁盘分区并集）
# ============================================================

def check_meta_index(root: str, report: "Report") -> Tuple[List[str], Set[str]]:
    """S1 + S2；返回 (文件轨 id 列表, 元数据索引可召回集合)"""
    ft = file_track_ids(root)
    ft_set = set(ft)
    mt_set = set(main_track_ids(root))
    reg_union = ft_set | mt_set

    index_path = os.path.join(root, _INDEX_REL)
    if not os.path.isfile(index_path):
        report.fail("S1 %s 不存在（元数据索引缺失）" % _INDEX_REL)
        report.fail("S2 元数据索引不可读，注册表并集 %d 项全部不可召回" % len(reg_union))
        return ft, set()
    try:
        data = _read_json(index_path)
    except Exception as e:  # noqa: BLE001
        report.fail("S1 %s 解析失败: %s" % (_INDEX_REL, e))
        return ft, set()

    skills = data.get("skills") or {}
    meta = data.get("meta") or {}
    main_track_index = data.get("main_track") or {}
    if not isinstance(skills, dict) or not isinstance(meta, dict):
        report.fail("S1 cache.json 结构非法（skills/meta 必须是对象）")
        return ft, set()

    missing = sorted(ft_set - set(skills))
    extra = sorted(set(skills) - ft_set)
    stale: List[str] = []
    for sid in sorted(ft_set & set(skills)):
        md_path = os.path.join(root, _REPO_REL, sid, _SKILL_MD)
        info = meta.get(sid) or {}
        try:
            digest = _md5_file(md_path)
        except OSError:
            stale.append(sid)
            continue
        if digest != info.get("hash"):
            stale.append(sid)

    report.note("[S1] cache.json=%d  文件轨=%d  缺失=%d  多余=%d  hash失效=%d"
                % (len(skills), len(ft), len(missing), len(extra), len(stale)))
    if missing:
        report.fail("S1 元数据索引缺失 %d 项：%s" % (len(missing), missing[:10]))
    if extra:
        report.fail("S1 元数据索引多余 %d 项：%s" % (len(extra), extra[:10]))
    if stale:
        report.fail("S1 元数据索引 hash 失效 %d 项：%s" % (len(stale), stale[:10]))

    # ── 【主审计修正·2026-09-25】S2 判据必须是「代码路径可达」，不是「磁盘分区并集」──
    #
    # 原实现写 `recallable = set(skills) | set(main_track_index)`，把 cache.json 的
    # `main_track` **存储分区**当成了「可召回集合」，于是主轨条目一被持久化，S2 就从
    # FAIL(7) 变成 PASS(0) —— 但生产行为零变化：检索真正使用的入口
    # （vector_adapter.py / loader.py）读的是 `SkillFileStore.load_metadata_index()`，
    # 它**只返回文件轨那 23 条**，主轨独有技能一条都进不去。
    #
    # ⇒ 那是**假绿**：断言派生工件不等于断言行为。本函数改为调用**生产入口**实测。
    recallable_disk = set(skills) | set(main_track_index)
    prod = _production_recall_sets(root)
    bare, service = prod.get("bare"), prod.get("service")

    gap_bare = sorted(reg_union - bare) if bare is not None else None
    gap_service = sorted(reg_union - service) if service is not None else None
    gap_disk = sorted(reg_union - recallable_disk)

    report.note("[S2] 注册表并集=%d（主轨=%d 文件轨=%d 交集=%d）"
                % (len(reg_union), len(mt_set), len(ft), len(mt_set & ft_set)))
    report.note("[S2] pathA 裸入口（SkillLoader() 默认；capregistry/skillsearch.py:53、"
                "orchestrator.py:3874）可召回=%s  缺口=%s"
                % ("导入失败" if bare is None else len(bare),
                   "N/A" if gap_bare is None else len(gap_bare)))
    report.note("[S2] pathB 服务形态（SkillsMgmtService.__init__:77-82）可召回=%s  缺口=%s"
                % ("导入失败" if service is None else len(service),
                   "N/A" if gap_service is None else len(gap_service)))
    report.note("[S2] 参考（非判据）磁盘分区并集 skills∪main_track=%d  缺口=%d"
                % (len(recallable_disk), len(gap_disk)))

    if bare is None and service is None:
        # 【不易】生产入口不可用**不得**降级成磁盘口径 —— 那正是"假绿"的来源
        report.fail("S2 两条生产入口均无法调用（导入/构造失败）：本门不降级为磁盘口径，直接判 FAIL")
    else:
        # 【不易】两条路径都必须覆盖：只看并集会掩盖"某条生产路径漏召"（本次就是这个坑）
        if gap_bare:
            report.fail("S2 pathA 裸入口（SkillLoader() 默认路径）不可召回 %d 项：%s"
                        % (len(gap_bare), gap_bare[:10]))
        if gap_service:
            report.fail("S2 pathB 服务形态不可召回 %d 项：%s"
                        % (len(gap_service), gap_service[:10]))
        if gap_disk == 0 and (gap_bare or gap_service):
            report.warn("[S2-w] **假绿告警**：磁盘分区口径缺口=0，但生产代码路径仍有缺口 "
                        "⇒ cache.json 的 main_track 分区已被持久化，却没有（全部）生产路径合并它")
    recallable = (bare or set()) | (service or set())
    return ft, recallable


# ============================================================
#  S3 — 落盘向量库覆盖
# ============================================================

def check_vector_store(root: str, reg_union: Set[str], report: "Report") -> None:
    """比对 data/skill_vectors/native_chroma 落盘向量库与注册表并集

    该库是 BGE-m3 初始化失败时的**自动降级后端**（vector_adapter._ensure_vector_store
    第二级）=> 覆盖缺口 = 静默返回只覆盖一部分技能的结果。
    """
    db_path = os.path.join(root, _VECTOR_DB_REL)
    if not os.path.isfile(db_path):
        report.note("[S3] 无落盘向量库（%s 不存在）；BGE-m3 可用时属正常" % _VECTOR_DB_REL)
        return
    try:
        uri = "file:%s?mode=ro" % db_path.replace(os.sep, "/")
        con = sqlite3.connect(uri, uri=True)
        try:
            rows = con.execute("select embedding_id, created_at from embeddings").fetchall()
        finally:
            con.close()
    except Exception as e:  # noqa: BLE001
        report.fail("S3 落盘向量库不可读（只读模式）: %s" % e)
        return

    indexed: Set[str] = set()
    stamps: List[str] = []
    malformed: List[str] = []
    for embedding_id, created_at in rows:
        eid = str(embedding_id or "")
        if created_at:
            stamps.append(str(created_at))
        if eid.startswith("skill_"):
            indexed.add(eid[len("skill_"):])
        else:
            malformed.append(eid)  # 非 skill_ 前缀：无法归属技能，按异常条目报出

    uncovered = sorted(reg_union - indexed)
    orphan = sorted(indexed - reg_union)
    coverage = (100.0 * len(indexed & reg_union) / len(reg_union)) if reg_union else 100.0
    age = _days_since(max(stamps)) if stamps else None
    report.note("[S3] 落盘向量库=%d  注册表并集=%d  未覆盖=%d  库外多余=%d  覆盖率=%.1f%%  最新条目=%s%s"
                % (len(indexed), len(reg_union), len(uncovered), len(orphan), coverage,
                   max(stamps) if stamps else "N/A",
                   "" if age is None else "（%.1f 天前）" % age))
    if malformed:
        report.warn("S3 落盘向量库含 %d 个非 skill_ 前缀条目：%s" % (len(malformed), malformed[:5]))
    if age is not None and age > 30:
        report.warn("S3-w 落盘向量库已冻结 %.1f 天（仅覆盖 %.1f%%）；BGE-m3 不可用时运行时会降级到它，"
                    "vector_adapter 会记录 degraded 并告警，但仍会返回该库覆盖范围内的结果" % (age, coverage))
    if orphan:
        report.fail("S3 落盘向量库含注册表外条目 %d 项：%s" % (len(orphan), orphan[:10]))
    if uncovered:
        report.fail("S3 落盘向量库未覆盖 %d 项（覆盖率 %.1f%%）；修复动作 = 重建该降级库（本脚本只读、不重建）示例 %s"
                    % (len(uncovered), coverage, uncovered[:8]))


# ============================================================
#  报告与入口
# ============================================================

class Report:
    """收集 FAIL / WARN / NOTE，决定退出码（0=PASS / 1=FAIL）"""

    def __init__(self) -> None:
        self.fails: List[str] = []
        self.warns: List[str] = []
        self.notes: List[str] = []

    def fail(self, msg: str) -> None:
        self.fails.append(msg)

    def warn(self, msg: str) -> None:
        self.warns.append(msg)

    def note(self, msg: str) -> None:
        self.notes.append(msg)

    def emit(self, *, as_json: bool = False, root: str = "") -> int:
        for line in self.notes:
            print(line)
        for line in self.warns:
            print("WARN: " + line)
        for line in self.fails:
            print("FAIL: " + line)
        code = 1 if self.fails else 0
        print("")
        print("=== 结论 ===")
        print("%s（FAIL=%d WARN=%d）" % ("PASS" if code == 0 else "FAIL", len(self.fails), len(self.warns)))
        if as_json:
            print(json.dumps({
                "root": root, "exit_code": code,
                "fails": self.fails, "warns": self.warns, "notes": self.notes,
            }, ensure_ascii=False))
        return code


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="索引漂移巡检（只读，0=PASS / 1=FAIL）")
    parser.add_argument("--root", default=None, help="仓库根（默认 cwd / 脚本上级目录）")
    parser.add_argument("--json", action="store_true", help="额外输出一行 JSON 汇总")
    args = parser.parse_args(argv)

    root = find_root(args.root)
    rep = Report()
    rep.note("仓库根: %s" % root)
    check_tool_index(root, rep)
    _ft, _recallable = check_meta_index(root, rep)
    reg_union = set(file_track_ids(root)) | set(main_track_ids(root))
    check_vector_store(root, reg_union, rep)
    return rep.emit(as_json=args.json, root=root)


if __name__ == "__main__":
    sys.exit(main())
