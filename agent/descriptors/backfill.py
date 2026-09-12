"""TASK-S1-02 — 存量技能资产 provenance/trust/undo_hint 回填引擎

对云枢**存量技能资产**执行 v7.2 契约字段（provenance 四级 / data_class 四级 /
risk_level 四级 / undo_hint+compensating_action）的摸底、确定性回填与迁移，
消费 TASK-S1-01 交付的 DescriptorRegistry 写入 API
（``update_trust`` / ``mark_provenance`` / ``set_governance``），
**只写 Descriptor 层、绝不反向写 Skill 主轨**（与 bridge 单向只读守则一致），
不触碰存量状态机与发布门禁。

设计口径（确定性规则优先，禁"一刀切全高/全低"）：
- provenance：按来源记录确定性映射（builtin=verified / 内部声明=declared /
  外来无 manifest 签名=unknown），保留人工提升通道（reviewer + 审计留痕）；
- data_class：public/internal 为自动判定带；confidential/secret 只出
  **candidate** 并进 NEEDS_REVIEW（人工复核确认后才写入，见任务 §二步骤 2）；
- risk：low/medium/high 按"执行面/代码风险/破坏性指令/审批旁路"特征自动判定；
  destructive 只出 **candidate**（任务口径：destructive 类必须人工复核），
  校验器三不变量（destructive 三件套 / secret 禁外部端点 / borrowed 轨迹）
  由 registry 写前强制，本引擎在计划期预校验，绝不静默放行；
- undo_hint/compensating_action：risk≥high 时给出引用技能自身
  ``set_enabled``/``rollback_version`` 机制的真实可执行补偿描述；无法给出时打
  NEEDS_UNDO_HINT 进待补队列（不阻断既有发布，发布门禁提示）。

保守合并（防覆盖人工复核结果，对齐 S1-01 D6）：回填只**升级**或**补空**，
绝不降低人工已置的 provenance/data_class/risk/requires_approval/undo 文本。

审计与回滚：逐条经写 API 落 registry 审计轨（action=descriptor.provenance/
descriptor.patch…）；分批复填（默认 200 条/批），批内任一条失败 → 整批回滚
（reg.load() 恢复至上一已提交批次状态），绝不残留半批。

模块级**零依赖** skills_mgmt（对齐 bridge 守则）：读取主轨/文件轨采用函数内
懒加载，纯存储层直读（data/skills_mgmt.json + data/skills_repo/<id>/skill.md），
不实例化服务、不触发调度器副作用，保证摸底/回填确定性可复现。
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .bridge import sanitize_id_part, skill_to_descriptor
from .models import (
    DataClass,
    ProvenanceLevel,
    RiskLevel,
)
from .validator import validate_descriptor

logger = logging.getLogger(__name__)


def _resolution_helpers() -> Any:
    """裁定留痕接入（`agent.digestion.resolutions`；**函数内懒加载**）

    架构纪律：descriptors 是纯依赖叶子，不得在导入期反向依赖 digestion 包
    （否则 importlinter 的层次规则会被打破）。故本模块只在本函数内取用，
    且**任何异常都退化为"未接入"**（needs 报告照旧产出，不因台账故障而中断）。
    """
    try:
        from agent.digestion import resolutions as mod
        return mod
    except Exception as e:  # noqa: BLE001
        logger.debug("裁定台账模块不可用（D4 过滤停用）: %s", e)
        return None


def enrich_needs(needs: Dict[str, Any], resolutions: Any) -> Dict[str, Any]:
    """needs 报告 → 附带"已裁定项及其依据"（D4）

    thin wrapper over `resolutions.enrich_needs`；``resolutions`` 为 None 或模块
    不可用时**原样返回**（零行为变化）。
    """
    mod = _resolution_helpers() if resolutions is not None else None
    if mod is None or resolutions is None:
        out = dict(needs)
        out.setdefault("needs_review_resolved", [])
        out.setdefault("needs_undo_hint_resolved", [])
        out.setdefault("resolution_summary", {"active": 0, "note": "未接入裁定台账"})
        out.setdefault("resolution_skipped", 0)
        out.setdefault("resolution_note", "")
        return out
    try:
        return mod.enrich_needs(needs, resolutions)
    except Exception as e:  # noqa: BLE001  台账故障不得让清单消失
        logger.warning("裁定台账过滤失败（按未接入处理，清单照旧）: %s", e)
        out = dict(needs)
        out["resolution_summary"] = {"active": 0, "error": str(e)}
        return out

# ─────────────────────────────────────────────────────────────
# 常量与规则表（确定性、可复现；规则 ID 供审计 reason 引用）
# ─────────────────────────────────────────────────────────────

# 默认数据位置（与 store.py / file_store.py 同源约定）
_DEFAULT_MAIN_PATH = Path(__file__).parent.parent.parent / "data" / "skills_mgmt.json"
_DEFAULT_REPO_PATH = Path(__file__).parent.parent.parent / "data" / "skills_repo"
_DEFAULT_REGISTRY_PATH = Path(__file__).parent.parent.parent / "data" / "descriptors.json"
# 回填报告/摸底表落点（运行时台账，gitignore）
_DEFAULT_REPORT_DIR = Path(__file__).parent.parent.parent / "data" / "descriptors_s1_02"

BACKFILL_ACTOR_PREFIX = "backfill:s1-02"
BACKFILL_REASON = "s1-02 存量资产字段回填"
DEFAULT_BATCH_SIZE = 200

# 外部通道来源（无 manifest/签名时只能 unknown）
_EXTERNAL_CHANNEL_SOURCES = {"external_agent", "install", "install_from_zip",
                             "market", "zip"}
_EXTERNAL_SOURCE_PREFIXES = ("github:", "url:", "registry:", "market:",
                             "local:", "mcp:", "http://", "https://")
_EXTERNAL_CATEGORIES = {"claude", "community", "mcp", "ai_generated"}

# manifest 声明键（§2.3 manifest/provenance + license；外来安装 payload 上查找）
_MANIFEST_KEYS = ("manifest", "license", "provenance", "source_manifest",
                  "manifest_ref")
_SIGNATURE_KEYS = ("signature", "manifest_signature", "detached_signature")

# 代码类内容类型（可执行面判定）
_CODE_TYPES = {"python", "javascript", "js", "typescript", "ts",
               "shell", "bash", "sh", "java", "go", "rust", "ruby"}

# ── 风险信号表 ──

# 破坏性操作短语（明确、不可逆的操作，避免"未覆盖/覆盖防御"等歧义命中）
_DESTRUCTIVE_PHRASES = [
    r"删除(?:大量)?(?:文件|数据|仓库|分支)",
    r"rm\s+-rf",
    r"清空(?:目录|数据|表|缓存)?",
    r"格式化(?:硬盘|磁盘|分区)?",
    r"force\s+push",
    r"git\s+reset\s+--hard",
    r"drop\s+table",
    r"truncate\s+table",
    r"覆盖(?:生产|线上|正式|全部).{0,6}(?:数据|文件|配置)",
]
# 强保护语义（硬性护栏：命中即视为"提及但被拦截/禁止"——护栏技能不升风险）
_GUARD_HARD_TERMS = (
    r"禁止|严禁|不得|不要|避免|切勿|阻止|拦截|拒绝|不直接执行|"
    r"二次确认|强制.{0,4}(?:确认|审批)|(?<!无)需.{0,6}(?:确认|批准|审批)|"
    r"审批后|批准后"
)
# 可回滚/可恢复语义：仅正向（"可回滚/可恢复"）算护栏；"无法恢复/不能回滚"反而警示
_GUARD_ROLLBACK_RE = re.compile(r"(?:回滚|恢复)")
_GUARD_ROLLBACK_NEG_RE = re.compile(
    r"(?:无法|不能|不可|无法自动|未能|未成功).{0,6}(?:回滚|恢复)")
# 审批旁路语义（破坏性操作 + 免确认 → 与 v7.2 审批门冲突，需人工复核）
_APPROVAL_BYPASS = (
    r"(?:无需|不需|不用|免|跳过).{0,10}(?:确认|批准|审批|弹窗|门禁)"
)

# 代码内容风险（脚本/代码执行面；对齐 assessor SEC_* 语义：权限/攻击面/数据合规）
_CODE_RISK_PATTERNS: List[Dict[str, Any]] = [
    {"name": "shell_exec",
     "re": re.compile(r"os\.system\s*\(|os\.popen|subprocess\.(?:run|call|Popen)|"
                      r"shutil\.rmtree|os\.remove|os\.unlink")},
    {"name": "net_client",
     "re": re.compile(r"requests\.(?:get|post|put|delete|request)|urllib|urlopen|"
                      r"socket\.|http\.client")},
    {"name": "env_access",
     "re": re.compile(r"os\.environ|os\.getenv")},
    {"name": "dynamic_exec",
     "re": re.compile(r"\beval\s*\(|\bexec\s*\(")},
    {"name": "obfuscation",
     "re": re.compile(r"base64|b64decode")},
    {"name": "write_op",
     "re": re.compile(r"open\s*\([^)]*[\"']w[\"']")},
]

# 数据合规信号（对齐 assessor DATA_COLLECT_SENSITIVE / DATA_SECRET_IN_PARAMS 语义）
_SECRET_KEY_RE = re.compile(r"(api[_-]?key|secret|token|password|passwd|credential|私钥)", re.I)
_SECRET_VALUE_RE = re.compile(r"^(sk-|ghp_|AKIA|-----BEGIN|eyJ[A-Za-z0-9_-]{10,})")
_COLLECT_PERSONAL_RE = re.compile(
    r"(收集|采集|抓取|处理).{0,12}(个人信息|个人数据|手机号|身份证|邮箱|隐私)|"
    r"(手机号|身份证号|邮箱地址).{0,12}(收集|采集|上传|存储)", re.I)

# 保守等级序（同 kinds 比较用；None=未评估视为最低）
_RISK_ORDER = ["low", "medium", "high", "destructive"]
_DATA_ORDER = ["public", "internal", "confidential", "secret"]
_PROV_ORDER = ["unknown", "declared", "verified", "signed"]


def _level_index(level: Optional[str], order: List[str]) -> int:
    if level is None:
        return -1
    return order.index(level) if level in order else -1


# ═════════════════════════════════════════════════════════════
# 外来安装 provenance 分类（§2.3 manifest 合并预检；install_precheck 集成）
# ═════════════════════════════════════════════════════════════

def classify_install_provenance(
    scheme: str,
    source: str = "",
    payload: Optional[Dict[str, Any]] = None,
    *,
    category: Optional[str] = None,
) -> Dict[str, Any]:
    """按安装来源 scheme + payload manifest 声明分类 provenance（确定性）。

    Returns:
        {level, scheme, source, manifest_present, evidence, upgrade_path,
         rationale, rule}——供 install_precheck / import_queue 合并进既有安全预检，
        不另起炉灶。
    """
    scheme = str(scheme or "").lower()
    payload = payload or {}
    source = str(source or "")

    # 1) 签名证据 → signed（§2.3 证据纪律）
    if any(k in payload for k in _SIGNATURE_KEYS):
        return _prov_result("signed", scheme, source, payload,
                            evidence=["payload.signature 存在"],
                            upgrade_path="signed 无需提升；需周期性复核签名链有效性",
                            rationale="安装 payload 携带签名（manifest.signature）",
                            rule="PRV-0")

    # 2) manifest/license/provenance 声明 → declared（§2.3：外来技能安装应带声明）
    manifest_hits = [k for k in _MANIFEST_KEYS if k in payload]
    has_license = bool(payload.get("license") or
                       (isinstance(payload.get("manifest"), dict)
                        and payload["manifest"].get("license")))
    if manifest_hits or has_license:
        evidence = [f"payload manifest 声明字段: {', '.join(manifest_hits) or 'license'}"]
        return _prov_result("declared", scheme, source, payload,
                            evidence=evidence,
                            upgrade_path=("升级 verified：六步探针（清单/哈希/行为一致性核验）后 "
                                          "mark_provenance(verified, evidence=<probe-run-id>)"),
                            rationale="安装 payload 带 manifest/license/provenance 声明（§2.3）",
                            rule="PRV-7")

    # 3) local 受控文件 → declared（来源为本机受控路径，install_path 为证据）
    if scheme == "local":
        return _prov_result("declared", scheme, source, payload,
                            evidence=["scheme=local（受控本地文件，install_path 可溯源）"],
                            upgrade_path=("若内容来自不可信外部，应先补 manifest/license 声明"
                                          "或人工复核后 mark_provenance(verified,…)"),
                            rationale="local 安装：本机受控来源（管理员放行）",
                            rule="PRV-8")

    # 4) 其余外部 scheme（github/url/registry/market）或外来类别 → unknown
    cat = str(category or "").lower()
    reason = (f"外来安装 scheme={scheme} 无 manifest/签名证据"
              if cat not in _EXTERNAL_CATEGORIES
              else f"外来类别 category={cat} 无 manifest/签名证据")
    return _prov_result("unknown", scheme, source, payload,
                        evidence=[f"安装来源: {source or scheme}",
                                  "无 manifest/签名证据（§2.3）"],
                        upgrade_path=("升级 declared：补上游 manifest/license 声明；"
                                      "升级 verified：六步探针核验；signed：签名"),
                        rationale=reason,
                        rule="PRV-5")


def _prov_result(level: str, scheme: str, source: str, payload: Dict[str, Any],
                 *, evidence: List[str], upgrade_path: str, rationale: str,
                 rule: str) -> Dict[str, Any]:
    return {
        "level": level,
        "scheme": scheme,
        "source": source,
        "manifest_present": bool(payload),
        "evidence": sorted(set(evidence)),
        "upgrade_path": upgrade_path,
        "rationale": rationale,
        "rule": rule,
    }


# ═════════════════════════════════════════════════════════════
# 存量资产装载（主轨 data/skills_mgmt.json ∪ 文件轨 data/skills_repo/<id>/）
# ═════════════════════════════════════════════════════════════

def _read_main_store(main_path: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    path = Path(main_path) if main_path else _DEFAULT_MAIN_PATH
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:  # pragma: no cover - 数据损坏防御
        logger.warning("[backfill] 主轨读取失败 %s: %s", path, e)
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for k, v in raw.items():
        if isinstance(v, dict) and v.get("id"):
            out[str(k)] = v
    return out


def _parse_front_matter(text: str) -> Dict[str, Any]:
    """解析 skill.md front matter（'---' 包裹的 YAML），失败返回空 dict。"""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    fm_text = text[3:end]
    try:
        import yaml  # 局部导入：仅文件轨读取路径使用
        meta = yaml.safe_load(fm_text) or {}
        return meta if isinstance(meta, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _read_file_track(repo_path: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    """直读文件轨：<repo>/<id>/skill.md front matter + scripts 清单 + 正文。"""
    path = Path(repo_path) if repo_path else _DEFAULT_REPO_PATH
    out: Dict[str, Dict[str, Any]] = {}
    if not path.exists():
        return {}
    for d in sorted(p for p in path.iterdir() if p.is_dir() and not p.name.startswith(".")):
        md = d / "skill.md"
        if not md.exists():
            continue
        try:
            text = md.read_text(encoding="utf-8")
        except OSError:  # pragma: no cover
            continue
        meta = _parse_front_matter(text)
        sid = str(meta.get("id") or d.name)
        body = text
        cut = text.find("\n---", 3)
        if cut != -1:
            body = text[cut + 4:]
        meta.setdefault("id", sid)
        meta.setdefault("_track", "file_track")
        meta["_content"] = body.strip()
        scripts_dir = d / "scripts"
        meta["_scripts"] = sorted(
            p.name for p in scripts_dir.iterdir() if p.is_file()) \
            if scripts_dir.exists() else []
        out[sid] = meta
    return out


def load_skill_assets(*, main_path: Optional[Path] = None,
                      repo_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """装载全部存量技能资产（主轨权威 ∪ 文件轨独有），返回归一化资产列表。

    排序确定性：按 id 升序。文件轨目录与主轨同 id 时以主轨记录为准
    （与 skills_mgmt/registry.py 双源合并口径一致）。
    """
    main_store = _read_main_store(main_path)
    file_track = _read_file_track(repo_path)
    assets: List[Dict[str, Any]] = []
    seen: set = set()

    for sid in sorted(main_store):
        assets.append(_normalize_asset(dict(main_store[sid]), track="main"))
        seen.add(sid)
    for sid in sorted(file_track):
        if sid in seen:
            continue
        fm = file_track[sid]
        payload = {k: v for k, v in fm.items() if not k.startswith("_")}
        payload["content"] = fm.get("_content", "")
        payload["_scripts"] = fm.get("_scripts", [])
        assets.append(_normalize_asset(payload, track="file_track"))
    return assets


def _normalize_asset(payload: Dict[str, Any], *, track: str) -> Dict[str, Any]:
    """Skill 存储 dict/front matter → 引擎归一化资产（JSON-safe，无活对象）。"""
    sid = str(payload.get("id") or "")
    metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
    scripts = payload.get("_scripts")
    if scripts is None:
        scripts = []
    content = str(payload.get("content") or "")
    ct = str(payload.get("content_type") or "markdown").lower()

    # 代码内容（脚本文件正文 / code 类 content）——供代码风险扫描
    script_code: List[str] = []
    if scripts and track == "file_track":
        d = _DEFAULT_REPO_PATH / sid
        for sname in scripts:
            p = d / "scripts" / sname
            try:
                if p.exists() and p.stat().st_size < 200_000:
                    script_code.append(p.read_text(encoding="utf-8", errors="replace"))
            except OSError:  # pragma: no cover
                pass

    review = payload.get("review") if isinstance(payload.get("review"), dict) else {}
    rv = review.get("review_verdict") or review.get("digest_verdict") or ""
    if hasattr(rv, "value"):
        rv = rv.value  # pragma: no cover - 防御枚举值
    cs = payload.get("config_schema")
    os_ = payload.get("output_schema")
    versions = payload.get("versions")
    default_params = payload.get("default_params")
    if default_params is None and "params" in payload:
        default_params = payload["params"]

    return {
        "id": sid,
        "name": str(payload.get("name") or sid),
        "description": str(payload.get("description") or ""),
        "category": str(payload.get("category") or "custom"),
        "source": str(payload.get("source") or ""),
        "status": str(payload.get("status") or "draft"),
        "author": str(payload.get("author") or "unknown"),
        "enabled": bool(payload.get("enabled", True)),
        "is_sensitive": bool(payload.get("is_sensitive", False)),
        "content_type": ct,
        "tags": [str(t) for t in (payload.get("tags") or [])],
        "version": str(payload.get("version") or "0.1.0"),
        "created_at": str(payload.get("created_at") or ""),
        "installed_at": str(payload.get("installed_at") or ""),
        "track": track,
        "content": content,
        "has_config_schema": bool(cs and (cs.get("properties") or cs.get("type"))),
        "has_output_schema": bool(os_),
        "config_schema": cs if isinstance(cs, dict) else {},
        "output_schema": os_ if isinstance(os_, dict) else {},
        "default_params": default_params if isinstance(default_params, dict) else {},
        "dependencies": list(payload.get("dependencies") or []),
        "scripts": list(scripts),
        "script_code": script_code,
        "review_verdict": str(rv),
        "auto_assessed": bool(review.get("auto_assessed")),
        "security_score": float(review.get("security_score") or 100.0),
        "usage_count": int(metrics.get("usage_count") or 0),
        "success_rate": float(metrics.get("success_rate") or 0.0),
        "versions_count": len(versions) if isinstance(versions, list) else 0,
    }


def _asset_to_skill_payload(asset: Dict[str, Any]) -> Dict[str, Any]:
    """归一化资产 → skills_mgmt 存储同构 dict（供 bridge.skill_to_descriptor 映射）。"""
    return {
        "id": asset["id"],
        "name": asset["name"],
        "description": asset["description"],
        "category": asset["category"],
        "source": asset["source"],
        "status": asset["status"],
        "author": asset["author"],
        "config_schema": asset["config_schema"] or {"type": "object", "properties": {}},
        "output_schema": asset["output_schema"] or {},
        "metrics": {"usage_count": asset["usage_count"],
                    "success_rate": asset["success_rate"]},
    }


def asset_capability_id(asset: Dict[str, Any]) -> str:
    return f"cp.skill.{sanitize_id_part(asset['id']) or 'unknown'}"


# ═════════════════════════════════════════════════════════════
# 摸底（步骤 1：存量资产字段现状摸底表）
# ═════════════════════════════════════════════════════════════

def survey_skill_assets(*, main_path: Optional[Path] = None,
                        repo_path: Optional[Path] = None) -> Dict[str, Any]:
    """摸底：资产 × 来源分布 × 已有标记覆盖率。"""
    assets = load_skill_assets(main_path=main_path, repo_path=repo_path)
    rows = []
    for a in assets:
        rows.append({
            "id": a["id"],
            "track": a["track"],
            "category": a["category"],
            "source": a["source"],
            "status": a["status"],
            "author": a["author"],
            "is_sensitive": a["is_sensitive"],
            "has_config_schema": a["has_config_schema"],
            "has_output_schema": a["has_output_schema"],
            "has_source_record": bool(a["source"]),
            "has_review": bool(a["review_verdict"]),
            "auto_assessed": a["auto_assessed"],
            "content_type": a["content_type"],
            "scripts": len(a["scripts"]),
            "versions_count": a["versions_count"],
            "enabled": a["enabled"],
        })
    return {"assets": assets, "rows": rows, "summary": summarize_assets(assets)}


def summarize_assets(assets: List[Dict[str, Any]]) -> Dict[str, Any]:
    """资产规模 × 来源分布 × 覆盖率的确定性聚合（供摸底表/验收复核）。"""
    src: Dict[str, int] = {}
    cat: Dict[str, int] = {}
    status: Dict[str, int] = {}
    track: Dict[str, int] = {}
    for a in assets:
        src[str(a["source"])] = src.get(str(a["source"]), 0) + 1
        cat[str(a["category"])] = cat.get(str(a["category"]), 0) + 1
        status[str(a["status"])] = status.get(str(a["status"]), 0) + 1
        track[a["track"]] = track.get(a["track"], 0) + 1

    n = len(assets)
    return {
        "asset_count": n,
        "by_track": track,
        "by_source": dict(sorted(src.items(), key=lambda x: (-x[1], x[0]))),
        "by_category": dict(sorted(cat.items(), key=lambda x: (-x[1], x[0]))),
        "by_status": dict(sorted(status.items(), key=lambda x: (-x[1], x[0]))),
        "coverage": {
            "is_sensitive": {"yes": sum(1 for a in assets if a["is_sensitive"]),
                             "total": n},
            "has_config_schema": {"yes": sum(1 for a in assets if a["has_config_schema"]),
                                  "total": n},
            "has_output_schema": {"yes": sum(1 for a in assets if a["has_output_schema"]),
                                  "total": n},
            "has_source_record": {"yes": sum(1 for a in assets if a["source"]),
                                  "total": n},
            "has_review": {"yes": sum(1 for a in assets if a["review_verdict"]),
                           "total": n},
            "has_scripts": {"yes": sum(1 for a in assets if a["scripts"]),
                            "total": n},
        },
    }


def survey_markdown(survey: Dict[str, Any]) -> str:
    """摸底表 Markdown（验收可离线复核）。"""
    s = survey["summary"]
    lines = [
        "# S1-02 存量资产字段摸底表", "",
        f"- 资产规模：**{s['asset_count']}**（主轨 {s['by_track'].get('main', 0)} / "
        f"文件轨独有 {s['by_track'].get('file_track', 0)}）",
        f"- 来源分布：{json.dumps(s['by_source'], ensure_ascii=False)}",
        f"- 分类分布：{json.dumps(s['by_category'], ensure_ascii=False)}",
        f"- 状态分布：{json.dumps(s['by_status'], ensure_ascii=False)}",
        "- 已有标记覆盖率：",
    ]
    for k, v in s["coverage"].items():
        lines.append(f"  - {k}: {v['yes']}/{v['total']}")
    lines += [
        "",
        "| id | track | category | source | status | is_sensitive | config_schema | "
        "output_schema | 来源记录 | review | scripts | content_type |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in sorted(survey["rows"], key=lambda x: x["id"]):
        lines.append(
            f"| {r['id']} | {r['track']} | {r['category']} | {r['source']} | "
            f"{r['status']} | {r['is_sensitive']} | {r['has_config_schema']} | "
            f"{r['has_output_schema']} | {r['has_source_record']} | "
            f"{r['has_review']} | {r['scripts']} | {r['content_type']} |")
    return "\n".join(lines)


# ═════════════════════════════════════════════════════════════
# 确定性回填规则（步骤 2）
# ═════════════════════════════════════════════════════════════

def _derive_provenance(asset: Dict[str, Any]) -> Dict[str, Any]:
    """provenance：按来源记录确定性映射（PRV-1..6），evidence 可审计。"""
    cat = asset["category"].lower()
    src = asset["source"].lower()
    ev_base = [f"skill.id={asset['id']}", f"track={asset['track']}",
               f"source={asset['source']}"]
    if cat == "builtin":
        return {"level": "verified", "evidence": ev_base
                + ["category=builtin（本地代码即证据，S1-01 D8）"],
                "rule": "PRV-1", "applied": True,
                "rationale": "builtin 类别：本地代码即证据（D8）",
                "upgrade_path": "verified 已可进自动化；如需 signed 补签名"}
    if src in {"manual", "ai_assisted", "workflow"}:
        ev = ev_base + [f"author={asset['author']}",
                        "来源=内部手工/辅助创建（custom=declared）"]
        return {"level": "declared", "evidence": ev, "rule": "PRV-2",
                "applied": True,
                "rationale": "内部声明来源（in-house authored）",
                "upgrade_path": ("人工复核通道：reviewer 核验后 "
                                 "mark_provenance(verified, evidence=<review-id>)")}
    if src in {"knowledge_distill", "process_distill"}:
        ev = ev_base + ["author=process_distill（蒸馏管线留痕，素材哈希含于 id）"]
        if "from_knowledge" in asset["tags"] or "distilled" in asset["tags"]:
            ev.append("tags=distilled/from_knowledge")
        return {"level": "declared", "evidence": ev, "rule": "PRV-3",
                "applied": True,
                "rationale": "内部蒸馏来源：素材来源经蒸馏管线声明（上游素材未独立核验）",
                "upgrade_path": ("升级 verified：对上游素材做来源核验/六步探针后 "
                                 "mark_provenance(verified, evidence=<probe-run-id>)")}
    if src == "legacy_migration":
        ev = ev_base + ["author=unknown（legacy 迁移记录）",
                        "产物=产品内置 persona 行为技能（skills_repo 文件轨）"]
        return {"level": "declared", "evidence": ev, "rule": "PRV-4",
                "applied": True,
                "rationale": "legacy 内部迁移来源（可声明的内部来源记录）",
                "upgrade_path": ("人工复核通道：以产品内置资产身份确认后 "
                                 "mark_provenance(verified, evidence=<review-id>)")}
    # 外来通道：external_agent / install scheme / 外来类别 → 无签名证据 unknown
    external = (src in _EXTERNAL_CHANNEL_SOURCES
                or src.startswith(_EXTERNAL_SOURCE_PREFIXES)
                or cat in _EXTERNAL_CATEGORIES)
    if external or src == "":
        ev = ev_base + ["无上游 manifest/签名证据（§2.3）"]
        return {"level": "unknown", "evidence": ev, "rule": "PRV-5",
                "applied": True,
                "rationale": (f"外来资产（source={asset['source'] or 'unknown'}）"
                              "无签名证据 → unknown；仅可人工单步执行"),
                "upgrade_path": ("升级 declared：补上游 manifest/license 声明（install_precheck "
                                 "合并预检）；升级 verified：六步探针核验；signed：签名")}
    return {"level": "unknown", "evidence": ev_base + ["无来源记录"],
            "rule": "PRV-6", "applied": True,
            "rationale": "无任何来源记录 → unknown（仅人工单步执行）",
            "upgrade_path": "补来源声明 → declared；核验 → verified"}


def _derive_data_class(asset: Dict[str, Any]) -> Dict[str, Any]:
    """data_class：is_sensitive + 内容数据特征；confidential/secret 只出 candidate。"""
    blob = "\n".join([asset["content"], asset["description"],
                      " ".join(asset["tags"]),
                      " ".join(str(v) for v in asset["default_params"].values())])

    # 参数含疑似真实密钥值（对齐 assessor DATA_SECRET_IN_PARAMS）→ secret candidate
    for k, v in (asset["default_params"] or {}).items():
        sv = str(v)
        key_hit = bool(_SECRET_KEY_RE.search(str(k)) and sv)
        val_hit = bool(_SECRET_VALUE_RE.match(sv))
        if key_hit or val_hit:
            return {"value": None, "applied": False, "candidate": "secret",
                    "rule": "DC-4",
                    "rationale": "default_params 含疑似密钥类参数 → secret 候选（人工复核）",
                    "needs_review": ("secret 需人工复核（v7.2 §3.2）；确认后 update_trust 写入，"
                                     "且禁止配置外部端点（校验器强制）")}
    # 收集个人数据（对齐 assessor DATA_COLLECT_SENSITIVE）→ confidential candidate
    if _COLLECT_PERSONAL_RE.search(blob):
        return {"value": None, "applied": False, "candidate": "confidential",
                "rule": "DC-3",
                "rationale": "内容/描述涉及个人数据收集 → confidential 候选（人工复核）",
                "needs_review": "confidential 需人工复核确认后再写入"}
    # is_sensitive（云枢合规隔离标记）→ confidential candidate
    if asset["is_sensitive"]:
        return {"value": None, "applied": False, "candidate": "confidential",
                "rule": "DC-2",
                "rationale": ("is_sensitive=True（隔离/合规处理标记）→ 数据分级保守建议 "
                              "confidential（人工复核确认后写入）"),
                "needs_review": "confidential 需人工复核确认后再写入"}
    # 默认 internal：无数据收集/外发、非对外分发资产
    return {"value": "internal", "applied": True, "candidate": None, "rule": "DC-1",
            "rationale": "无个人数据收集/外发、未声明对外公开 → internal（默认内部分级）",
            "needs_review": ""}


def _destructive_mentions(blob: str) -> List[Dict[str, int]]:
    """返回破坏性短语命中位置（用于护栏语境排除）。"""
    hits: List[Dict[str, int]] = []
    for pat in _DESTRUCTIVE_PHRASES:
        rx = re.compile(pat, re.I)
        for m in rx.finditer(blob):
            hits.append({"phrase": pat, "start": m.start()})
    return hits


# 破坏性短语命中所在"句子"（句号/感叹/换行分句）
_SENT_BOUNDARY = re.compile(r"[\n。！？!?；;]")


def _sentence_of(blob: str, start: int) -> str:
    """取包含 start 的句子（分句边界：换行/句号/分号），护栏判定用。"""
    bounds = [m.start() for m in _SENT_BOUNDARY.finditer(blob)]
    prev = [b + 1 for b in bounds if b < start]
    nxt = [b for b in bounds if b >= start]
    seg_start = max([0] + prev)
    seg_end = min([len(blob)] + nxt)
    return blob[seg_start:seg_end + 1]


_GUARD_RE = re.compile(_GUARD_HARD_TERMS)
_APPROVAL_BYPASS_RE = re.compile(_APPROVAL_BYPASS)


def _sentence_has_guard(sentence: str) -> bool:
    """句内护栏判定：硬性护栏命中，或正向"可回滚/可恢复"（否定式不算护栏）。"""
    if _GUARD_RE.search(sentence):
        return True
    if _GUARD_ROLLBACK_RE.search(sentence):
        # "无法恢复/不能回滚" 属于警示（不可逆），不构成护栏
        return not _GUARD_ROLLBACK_NEG_RE.search(sentence)
    return False


def _derive_risk(asset: Dict[str, Any]) -> Dict[str, Any]:
    """risk：执行面/代码风险/破坏性指令/审批旁路 → low/medium/high；destructive 只出候选。"""
    blob = "\n".join([asset["content"], asset["description"],
                      " ".join(asset["tags"])])
    code_scan = "\n".join(asset["script_code"])
    if asset["content_type"] in _CODE_TYPES:
        code_scan = code_scan + "\n" + asset["content"]

    # 1) 代码内容风险（shell/网络/环境/动态执行等，对齐 assessor SEC_*）
    code_risks: List[str] = []
    if code_scan.strip():
        for p in _CODE_RISK_PATTERNS:
            if p["re"].search(code_scan):
                code_risks.append(p["name"])
    # 2) 破坏性指令：同句无强护栏语义（禁止/拦截/二次确认/可回滚等）才计为指令
    mentions = _destructive_mentions(blob)
    destructive_instr = [
        m for m in mentions
        if not _sentence_has_guard(_sentence_of(blob, m["start"]))]
    # 3) 审批旁路：破坏性指令所在句同时"免确认/免审批"（与 v7.2 审批门冲突）
    bypass = bool(destructive_instr) and any(
        _APPROVAL_BYPASS_RE.search(_sentence_of(blob, m["start"]))
        for m in destructive_instr)

    has_exec_surface = bool(asset["scripts"] or asset["content_type"] in _CODE_TYPES)
    has_deps = bool(asset["dependencies"])

    if code_risks and {"shell_exec", "net_client", "dynamic_exec", "obfuscation"} \
            & set(code_risks):
        return {"level": "high", "applied": True, "candidate": None,
                "requires_approval": False, "rule": "RK-3",
                "rationale": f"代码执行面存在高危模式: {', '.join(code_risks)}（assessor SEC 语义）",
                "needs_review": ""}
    if destructive_instr and bypass:
        return {"level": "high", "applied": True, "candidate": None,
                "requires_approval": False, "rule": "RK-5",
                "rationale": ("内容指示破坏性操作免确认执行，与 v7.2 destructive 审批门冲突；"
                              "需人工复核（可下调或按 destructive 处置补齐三件套）"),
                "needs_review": ("RK-5 破坏性操作免确认指令：人工复核——若确属破坏性能力，"
                                 "按 v7.2 补 requires_approval+undo_hint+compensating_action "
                                 "三件套后经 update_trust 升级 destructive；否则下调并记录原因")}
    if destructive_instr:
        return {"level": None, "applied": False, "candidate": "destructive",
                "requires_approval": True, "rule": "RK-4",
                "rationale": "内容含破坏性指令（危险写/删除类操作）→ destructive 候选（必须人工复核）",
                "needs_review": ("destructive 候选：人工复核确认后补齐三件套"
                                 "（requires_approval=True + undo_hint + compensating_action）"
                                 "再经 update_trust 写入，校验器强制")}
    if has_exec_surface or has_deps:
        return {"level": "medium", "applied": True, "candidate": None,
                "requires_approval": False, "rule": "RK-2",
                "rationale": (f"可执行面/依赖面：scripts={len(asset['scripts'])} "
                              f"code_type={asset['content_type'] in _CODE_TYPES} "
                              f"deps={len(asset['dependencies'])}（沙箱内执行，人工可下调）"),
                "needs_review": ""}
    if code_risks:
        return {"level": "medium", "applied": True, "candidate": None,
                "requires_approval": False, "rule": "RK-2b",
                "rationale": f"代码面存在风险模式: {', '.join(code_risks)}",
                "needs_review": ""}
    return {"level": "low", "applied": True, "candidate": None,
            "requires_approval": False, "rule": "RK-1",
            "rationale": "指令型技能：无执行面/无数据外发/无破坏性指令 → low",
            "needs_review": ""}


def _derive_governance(asset: Dict[str, Any], risk: Dict[str, Any],
                       data_class: Dict[str, Any]) -> Dict[str, Any]:
    """undo_hint/compensating_action：risk≥high 必须提供真实可执行补偿描述。

    可引用技能自身回滚/停用机制（SkillRegistry.set_enabled / rollback_version）。
    """
    level = risk.get("level")
    if risk.get("candidate") == "destructive":
        # destructive 候选：三件套内容由人工复核补齐（本引擎不静默生成破坏性补偿）
        return {"undo_hint": "", "compensating_action": "", "applied": False,
                "rule": "GV-0", "needs_undo_hint": True,
                "rationale": "destructive 候选待人工复核补齐三件套（禁用静默放行）"}
    if level not in ("high", "destructive"):
        return {"undo_hint": "", "compensating_action": "", "applied": False,
                "rule": "GV-N", "needs_undo_hint": False,
                "rationale": "risk<high 无需 undo_hint（任务 §二步骤 2 口径）"}
    # risk≥high：引用真实机制（set_enabled 停用 / rollback_version 回退）
    kind = "指令型" if asset["content_type"] == "markdown" else "代码型"
    undo = (f"撤销指引：{asset['name']} 为{kind}技能，执行影响限于技能自身注入/输出；"
            "如需撤销：先停用技能（SkillRegistry.set_enabled=false，消除后续注入/调用），"
            "再评估具体副作用；技能版本可经 rollback_version 回退。")
    comp = ("补偿动作：本技能无外部持久副作用时停用即可；若其指令/脚本已产生外部副作用，"
            "按具体操作人工恢复（git 回退/备份恢复/快照），无通用自动补偿。")
    return {"undo_hint": undo, "compensating_action": comp, "applied": True,
            "rule": "GV-1", "needs_undo_hint": False,
            "rationale": "risk≥high：引用技能自身 set_enabled/rollback_version 机制"}


# ═════════════════════════════════════════════════════════════
# 计划生成（确定性、可复现：同输入两次结果一致）
# ═════════════════════════════════════════════════════════════

def derive_plan_item(asset: Dict[str, Any]) -> Dict[str, Any]:
    """单资产回填计划（纯函数，无时钟/无随机——dry-run 可复现）。"""
    prov = _derive_provenance(asset)
    dc = _derive_data_class(asset)
    risk = _derive_risk(asset)
    gov = _derive_governance(asset, risk, dc)

    flags_needs_review: List[Dict[str, str]] = []
    if risk.get("needs_review"):
        flags_needs_review.append({"kind": "NEEDS_REVIEW", "scope": "risk",
                                   "rule": risk.get("rule"), "reason": risk["needs_review"]})
    if dc.get("needs_review"):
        flags_needs_review.append({"kind": "NEEDS_REVIEW", "scope": "data_class",
                                   "rule": dc.get("rule"), "reason": dc["needs_review"]})
    if prov.get("level") == "unknown":
        flags_needs_review.append({"kind": "NEEDS_REVIEW", "scope": "provenance",
                                   "rule": prov.get("rule"),
                                   "reason": ("provenance=unknown（无签名证据），仅可人工单步；"
                                              "升级路径见 provenance.upgrade_path")})

    automation_eligible = prov.get("level") in ("verified", "signed")

    return {
        "asset_id": asset["id"],
        "capability_id": asset_capability_id(asset),
        "name": asset["name"],
        "track": asset["track"],
        "provenance": prov,
        "data_class": dc,
        "risk": risk,
        "governance": gov,
        "automation_eligible": automation_eligible,
        "flags": {"needs_review": flags_needs_review,
                  "needs_undo_hint": bool(gov.get("needs_undo_hint"))},
    }


def plan_backfill(*, main_path: Optional[Path] = None,
                  repo_path: Optional[Path] = None,
                  resolutions: Any = None) -> Dict[str, Any]:
    """摸底 + 全量回填计划（dry-run 数据源；两次调用结果一致）。

    ``resolutions``：裁定留痕台账（`agent.digestion.resolutions.ResolutionStore`）。
    给出时，``needs`` 中的已裁定项改列 ``needs_review_resolved``（D4：已裁定不再
    重复提醒），并在 ``needs.resolution_summary`` 给出台账总览。**缺省不启用** ⇒
    输出与从前逐字一致（零行为变化）。
    """
    survey = survey_skill_assets(main_path=main_path, repo_path=repo_path)
    plan = [derive_plan_item(a) for a in survey["assets"]]
    return {
        "survey": survey,
        "assets": survey["assets"],
        "plan": plan,
        "summary": survey["summary"],
        "coverage": plan_coverage(plan),
        "needs": collect_needs(plan, resolutions=resolutions),
    }


def plan_coverage(plan: List[Dict[str, Any]]) -> Dict[str, Any]:
    """计划级覆盖率（回填后预期分布）。"""
    prov: Dict[str, int] = {}
    dc: Dict[str, int] = {}
    risk: Dict[str, int] = {}
    dc_candidates: Dict[str, int] = {}
    risk_candidates: Dict[str, int] = {}
    for p in plan:
        prov[p["provenance"]["level"]] = prov.get(p["provenance"]["level"], 0) + 1
        if p["data_class"]["applied"]:
            dc[p["data_class"]["value"]] = dc.get(p["data_class"]["value"], 0) + 1
        elif p["data_class"].get("candidate"):
            c = p["data_class"]["candidate"]
            dc_candidates[c] = dc_candidates.get(c, 0) + 1
        if p["risk"]["applied"]:
            risk[p["risk"]["level"]] = risk.get(p["risk"]["level"], 0) + 1
        elif p["risk"].get("candidate"):
            c = p["risk"]["candidate"]
            risk_candidates[c] = risk_candidates.get(c, 0) + 1
    return {
        "assets": len(plan),
        "provenance": prov,
        "data_class_applied": dc,
        "data_class_candidates": dc_candidates,
        "risk_applied": risk,
        "risk_candidates": risk_candidates,
        "automation_eligible": sum(1 for p in plan if p["automation_eligible"]),
        "needs_review_assets": len({p["asset_id"] for p in plan
                                    if p["flags"]["needs_review"]}),
        "needs_undo_hint_assets": len({p["asset_id"] for p in plan
                                       if p["flags"]["needs_undo_hint"]}),
    }


def collect_needs(plan: List[Dict[str, Any]],
                  *, resolutions: Any = None) -> Dict[str, Any]:
    """NEEDS_REVIEW / NEEDS_UNDO_HINT 待补清单（资产级 + 处置路径）

    **TASK-S8-05 / D4**：``resolutions`` 给出时，**已有人工裁定**的项不再列入
    ``needs_review``，改列 ``needs_review_resolved``（附 ``basis``：谁/何时/依据
    什么规则/理由/证据）—— 已裁定的不再重复提醒，**未裁定的仍逐条照报**。
    ``resolutions=None`` 时输出与从前逐字一致（零行为变化）。
    """
    review: List[Dict[str, Any]] = []
    undo: List[Dict[str, Any]] = []
    for p in plan:
        for f in p["flags"]["needs_review"]:
            review.append({
                "asset_id": p["asset_id"],
                "capability_id": p["capability_id"],
                "scope": f["scope"],
                "rule": f["rule"],
                "reason": f["reason"],
                "disposal": _disposal_path(f["scope"]),
            })
        if p["flags"]["needs_undo_hint"]:
            undo.append({
                "asset_id": p["asset_id"],
                "capability_id": p["capability_id"],
                "reason": p["governance"].get("rationale", ""),
                "disposal": ("人工复核补齐真实可执行补偿动作：registry.set_governance("
                             "capability_id, undo_hint=…, compensating_action=…)；"
                             "补齐前不写入 destructive 风险（校验器三件套强制，不静默放行）"),
            })
    return enrich_needs({"needs_review": review, "needs_undo_hint": undo},
                        resolutions)


def needs_markdown(needs: Dict[str, Any]) -> str:
    """待办清单 Markdown（含**已裁定项及其依据** —— D4"依据可见"的落地载体）"""
    review = needs.get("needs_review") or []
    undo = needs.get("needs_undo_hint") or []
    done = needs.get("needs_review_resolved") or []
    lines = ["# S1-02 NEEDS_REVIEW 清单", "",
             f"- 待人工复核：**{len(review)}** 条"
             f"（已裁定 {len(done)} 条不再重复提醒）",
             f"- 待补 undo_hint：**{len(undo)}** 条", ""]
    if review:
        lines += ["| 资产 | 域 | 规则 | 原因 |", "|---|---|---|---|"]
        for row in review:
            lines.append(f"| {row['asset_id']} | {row['scope']} | {row['rule']} | "
                         f"{row['reason']} |")
    else:
        lines.append("（无待人工复核项）")
    if done:
        lines += ["", "## 已裁定项及其依据（不重复提醒；已入审计 resolution.record）", ""]
        for row in done:
            lines.append(f"- `{row['asset_id']}` · {row['scope']}/{row['rule']}："
                         f"{row.get('basis', '')}")
    return "\n".join(lines) + "\n"


def _disposal_path(scope: str) -> str:
    return {
        "risk": ("人工复核风险等级：确认破坏性则补三件套后 update_trust 写入；"
                 "否则下调并记录原因（reviewer 审计留痕）"),
        "data_class": ("人工复核数据分级：确认后 update_trust(data_class=…) 写入；"
                       "secret 必须同时满足禁外部端点（校验器强制）"),
        "provenance": ("补上游 manifest/license 声明或六步探针证据后 "
                       "mark_provenance(verified, evidence=…) 提升"),
    }.get(scope, "人工复核")


# ═════════════════════════════════════════════════════════════
# 分批回填实施（步骤 3：幂等 + 逐条审计 + 批级回滚 + 保守合并）
# ═════════════════════════════════════════════════════════════

def _current_fields(reg: Any, cid: str) -> Dict[str, Any]:
    d = reg.get(cid)
    if d is None:
        return {}
    return {
        "provenance": d.origin.provenance.value if d.origin.provenance else None,
        "evidence": sorted(d.origin.evidence or []),
        "risk_level": d.trust.risk_level.value if d.trust.risk_level else None,
        "data_class": d.trust.data_class.value if d.trust.data_class else None,
        "requires_approval": d.trust.requires_approval,
        "undo_hint": d.governance.undo_hint,
        "compensating_action": d.governance.compensating_action,
    }


def _planned_patch(item: Dict[str, Any], current: Dict[str, Any]) -> List[str]:
    """计划 vs 现状 → 需执行的写入动作（**保守合并**：只升级/补空，不降级）。

    - provenance：仅更高 or 同级补证据（mark_provenance 单调语义）；
    - risk/data_class：现状为空(None)或计划更严格才写，绝不覆盖人工已置值；
    - requires_approval：只 False→True（审批更严），不撤销；
    - undo/compensating：只在现状为空时补（不覆盖人工文本）。
    """
    actions: List[str] = []
    prov = item["provenance"]
    cur_prov = current.get("provenance")
    if (cur_prov is None
            or _level_index(prov["level"], _PROV_ORDER)
               > _level_index(cur_prov, _PROV_ORDER)
            or (prov["level"] == cur_prov
                and not set(prov["evidence"]) <= set(current.get("evidence", [])))):
        actions.append("provenance")

    dc = item["data_class"]
    if dc["applied"] and (
            current.get("data_class") is None
            or _level_index(dc["value"], _DATA_ORDER)
               >= _level_index(current.get("data_class"), _DATA_ORDER)):
        if current.get("data_class") != dc["value"]:
            actions.append("data_class")

    risk = item["risk"]
    if risk["applied"] and (
            current.get("risk_level") is None
            or _level_index(risk["level"], _RISK_ORDER)
               > _level_index(current.get("risk_level"), _RISK_ORDER)):
        actions.append("risk_level")
    if risk.get("requires_approval") and not current.get("requires_approval"):
        actions.append("requires_approval")

    gov = item["governance"]
    if gov["applied"]:
        if gov["undo_hint"] and not current.get("undo_hint"):
            actions.append("undo_hint")
        if gov["compensating_action"] and not current.get("compensating_action"):
            actions.append("compensating_action")
    return actions


def _apply_item(reg: Any, asset: Dict[str, Any], item: Dict[str, Any], *,
                actor: str, dry_run: bool,
                counters: Dict[str, int]) -> List[str]:
    """单资产回填：新资产先 register 桥接视图；随后经写 API 保守补丁（逐条审计）。

    dry_run=True：只计算计划动作，**绝不调用 registry 变更方法**（防污染真实
    实例内存态）；register 仅当资产无既有登记时列入计划。
    """
    cid = item["capability_id"]
    existing = reg.get(cid)
    current = _current_fields(reg, cid) if existing is not None else {}

    planned: List[str] = []
    if existing is None:
        planned.append("register")
    patch = _planned_patch(item, current)
    if existing is not None and not patch:
        counters["no_op"] = counters.get("no_op", 0) + 1
        return ["no-op"]
    if dry_run:
        return planned + patch

    actions: List[str] = []
    # 1) 新资产 → register 桥接视图（created）；既有登记不整条覆盖（防冲掉人工升级）
    if "register" in planned:
        desc, notes = skill_to_descriptor(_asset_to_skill_payload(asset))
        if desc is None:
            raise RuntimeError(f"资产 {item['asset_id']} 桥接失败: {notes.get('error')}")
        res = reg.register(desc, actor=actor,
                           reason=f"{BACKFILL_REASON}（wire: {item['asset_id']}）")
        if res.action in ("created", "updated", "merged"):
            counters["register"] = counters.get("register", 0) + 1
        actions.append("register")
        # register 后现状变化 → 重算补丁（避免重复写回桥接已含的值）
        patch = _planned_patch(item, _current_fields(reg, cid))
        if not patch:
            return actions

    reason = f"{BACKFILL_REASON}（rules: {_item_rules(item)}）"

    # 2) provenance（写 API mark_provenance；证据累积；单调提升由 _planned_patch 保证）
    if "provenance" in patch:
        reg.mark_provenance(cid, item["provenance"]["level"],
                            evidence=item["provenance"]["evidence"],
                            actor=actor, reason=reason)
        counters["provenance"] = counters.get("provenance", 0) + 1

    # 3) trust 组（写 API update_trust）
    trust_patch: Dict[str, Any] = {}
    if "data_class" in patch:
        trust_patch["data_class"] = item["data_class"]["value"]
    if "risk_level" in patch:
        trust_patch["risk_level"] = item["risk"]["level"]
    if "requires_approval" in patch:
        trust_patch["requires_approval"] = True
    if trust_patch:
        reg.update_trust(cid, trust_patch, actor=actor, reason=reason)
        for k in trust_patch:
            counters[k] = counters.get(k, 0) + 1

    # 4) governance（写 API set_governance；仅 risk≥high 的 applied 项）
    gov_patch: Dict[str, Any] = {}
    if "undo_hint" in patch:
        gov_patch["undo_hint"] = item["governance"]["undo_hint"]
    if "compensating_action" in patch:
        gov_patch["compensating_action"] = item["governance"]["compensating_action"]
    if gov_patch:
        reg.set_governance(cid, **gov_patch, actor=actor, reason=reason)
        for k in gov_patch:
            counters[k] = counters.get(k, 0) + 1

    actions.extend(patch)
    return actions


def _item_rules(item: Dict[str, Any]) -> str:
    return ",".join([item["provenance"]["rule"],
                     item["data_class"].get("rule") or "-",
                     item["risk"].get("rule") or "-",
                     item["governance"].get("rule") or "-"])


def run_backfill(
    planned: Dict[str, Any],
    registry: Optional[Any] = None,
    *,
    dry_run: bool = False,
    actor: str = BACKFILL_ACTOR_PREFIX,
    batch_size: int = DEFAULT_BATCH_SIZE,
    registry_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """分批回填：经写 API 逐条落库 + 审计；批失败整批回滚（reg.load()）。

    dry_run=True 时在一次性内存 registry 上模拟，不触碰真实台账，
    计划结果与实跑一致（审计/计数除外）。
    """
    from .registry import DescriptorRegistry  # 延迟（避免模块级加载开销）

    plan = planned["plan"]
    assets = planned.get("assets") or []
    assets_by_id = {a["id"]: a for a in assets}
    bs = max(1, int(batch_size))
    run_id = datetime.now().strftime("%Y%m%dT%H%M%S") + (".dryrun" if dry_run else "")

    if registry is None:
        # 批级原子性：autosave=False，仅 ok 批显式 save() 提交；
        # 批失败 reg.load() 恢复至上一已提交批次（半批残留防护）
        reg = DescriptorRegistry(path=registry_path or _DEFAULT_REGISTRY_PATH,
                                 autosave=False)
        reg.load()
    else:
        reg = registry
        if dry_run:
            reg._autosave = False  # noqa: SLF001 dry-run 内部

    result: Dict[str, Any] = {
        "run_id": run_id,
        "dry_run": bool(dry_run),
        "actor": actor,
        "assets_total": len(plan),
        "batches": [],
        "applied": {"register": 0, "provenance": 0, "data_class": 0,
                    "risk_level": 0, "requires_approval": 0,
                    "undo_hint": 0, "compensating_action": 0, "no_op": 0},
        "rollback_events": [],
        "stopped": False,
    }

    for bi in range(0, len(plan), bs):
        batch = plan[bi:bi + bs]
        batch_no = bi // bs + 1
        batch_rec = {"index": batch_no, "size": len(batch), "ok": False,
                     "errors": [], "item_actions": []}
        try:
            for item in batch:
                asset = assets_by_id.get(item["asset_id"])
                if asset is None:
                    raise RuntimeError(f"资产 {item['asset_id']} 未在计划 assets 中")
                acts = _apply_item(reg, asset, item, actor=actor,
                                   dry_run=dry_run, counters=result["applied"])
                batch_rec["item_actions"].append({
                    "asset_id": item["asset_id"],
                    "capability_id": item["capability_id"],
                    "actions": acts,
                })
            # 批级校验（destructive 三件套 / secret 禁外部端点 / 自动化资格 / 校验器）
            if dry_run:
                # dry-run 不落库：计划动作本身由规则保证合法，跳过批级存在性校验
                batch_rec["ok"] = True
                result["batches"].append(batch_rec)
                continue
            verify_errors = _verify_batch(reg, batch)
            if verify_errors:
                raise RuntimeError("批校验失败: " + "; ".join(verify_errors))
            reg.save()  # 提交本批（原子）
            batch_rec["ok"] = True
        except Exception as e:  # noqa: BLE001
            # 整批回滚：恢复至上一已提交批次状态（半批残留防护）
            result["rollback_events"].append({
                "batch": batch_no,
                "error": str(e),
                "rolled_back_items": [it["asset_id"] for it in batch],
            })
            batch_rec["ok"] = False
            batch_rec["errors"].append(str(e))
            result["stopped"] = True
            if not dry_run and registry is None:
                try:
                    reg.load()  # 丢弃本批内存变更
                except Exception:  # noqa: BLE001
                    pass
            result["batches"].append(batch_rec)
            break
        result["batches"].append(batch_rec)

    result["pending"] = planned["needs"]
    result["coverage"] = planned["coverage"]
    result["validation"] = validate_registry(reg)  # 回填后全量重校验（步骤 4）
    return result


def _verify_batch(reg: Any, batch: List[Dict[str, Any]]) -> List[str]:
    """批级校验：destructive 三件套 / secret 禁外部端点 / 自动化资格 / 校验器。

    三不变量由 registry 写前校验器强制，此处全量复核（双保险）。
    """
    errors: List[str] = []
    for item in batch:
        cid = item["capability_id"]
        d = reg.get(cid)
        if d is None:
            errors.append(f"{cid}: 回填后 descriptor 不存在")
            continue
        if d.trust.risk_level == RiskLevel.DESTRUCTIVE:
            if not d.trust.requires_approval:
                errors.append(f"{cid}: destructive 缺 requires_approval")
            if not d.governance.undo_hint.strip():
                errors.append(f"{cid}: destructive 缺 undo_hint")
            if not d.governance.compensating_action.strip():
                errors.append(f"{cid}: destructive 缺 compensating_action")
        if d.trust.data_class == DataClass.SECRET and d.origin.external_endpoint:
            errors.append(f"{cid}: secret 配置了外部端点（校验器拒绝）")
        # 自动化资格：仅 provenance≥verified 可标记自动化（其余人工单步）
        if item.get("automation_eligible") and \
                d.origin.provenance not in (ProvenanceLevel.VERIFIED,
                                            ProvenanceLevel.SIGNED):
            errors.append(f"{cid}: 自动化资格与 provenance 不一致")
        vres = validate_descriptor(d)
        if not vres.valid:
            errors.append(f"{cid}: 校验失败 {vres.errors}")
    return errors


def validate_registry(reg: Any) -> Dict[str, Any]:
    """对 registry 全量 descriptor 重校验（步骤 4；warnings 不阻断）。"""
    rows: List[Dict[str, Any]] = []
    n_warn = 0
    for d in sorted(reg.list(), key=lambda x: x.capability_id):
        vres = validate_descriptor(d)
        rows.append({
            "capability_id": d.capability_id,
            "valid": vres.valid,
            "warnings": list(vres.warnings),
            "errors": list(vres.errors),
        })
        n_warn += len(vres.warnings)
    return {
        "total": len(rows),
        "valid": sum(1 for r in rows if r["valid"]),
        "with_warnings": sum(1 for r in rows if r["warnings"]),
        "with_errors": sum(1 for r in rows if r["errors"]),
        "warning_entries": n_warn,
        "rows": rows,
    }


# ═════════════════════════════════════════════════════════════
# 报告落盘 + 便捷入口（步骤 3/4 产出）
# ═════════════════════════════════════════════════════════════

def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1, default=str),
                    encoding="utf-8")


def full_backfill(
    *,
    dry_run: bool = False,
    out_dir: Optional[Path] = None,
    registry_path: Optional[Path] = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> Dict[str, Any]:
    """摸底 → dry-run（可选）→ 分批回填 → 全量重校验 → 报告落盘。"""
    planned = plan_backfill()
    out = Path(out_dir) if out_dir else _DEFAULT_REPORT_DIR
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")

    # 摸底表（步骤 1 产出）
    _write_json(out / f"survey_{stamp}.json", {
        "summary": planned["summary"],
        "rows": planned["survey"]["rows"],
    })
    (out / f"摸底表_{stamp}.md").write_text(survey_markdown(planned["survey"]),
                                            encoding="utf-8")
    # dry-run 计划（步骤 2 产出；两次结果一致性校验见 CLI/测试）
    _write_json(out / f"plan_{stamp}.json", planned)

    run = run_backfill(planned, dry_run=dry_run,
                       registry_path=registry_path, batch_size=batch_size)
    if not dry_run:
        _write_json(out / f"run_{stamp}.json", run)
        _write_json(out / "needs_review.json", run["pending"]["needs_review"])
        _write_json(out / "needs_undo_hint.json", run["pending"]["needs_undo_hint"])
        _write_json(out / "validation.json", run["validation"])
    return {"planned": planned, "run": run, "out_dir": str(out)}


def sync_skill_descriptor(skill_id: str, *, source: str = "",
                          registry_path: Optional[Path] = None,
                          actor: str = "skill_sync",
                          payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """运行时接线（S1-01 遗留 #4 / S1-02 输出 3）：单技能 → descriptor 注册 + 回填。

    install / install_from_zip 成功后调用（advisory：任何失败不阻断主流程）。

    【架构纪律】payload 必须由调用方传入（调用方读取主轨/文件轨并持有 Skill 对象
    或 meta dict）——本模块不 import skills_mgmt（保持 descriptors 为纯依赖叶子，
    避免 skills_mgmt → descriptors → skills_mgmt 循环依赖；架构规则校验强制）。
    payload 缺失时返回错误（advisory），不自动回退读库。
    """
    from .registry import DescriptorRegistry  # 延迟

    if payload is None:
        return {"ok": False, "skill_id": skill_id,
                "error": "payload 必传（调用方负责读取主轨/文件轨；"
                         "descriptors 包不反向依赖 skills_mgmt）"}
    payload.setdefault("id", skill_id)

    asset = _normalize_asset(payload,
                             track="main" if "source" in payload else "file_track")
    if source:
        asset["source"] = source  # 安装来源覆盖（install 记录 source=原始 source 串）
    item = derive_plan_item(asset)
    reg = DescriptorRegistry(path=registry_path or _DEFAULT_REGISTRY_PATH,
                             autosave=True)
    reg.load()
    counters: Dict[str, int] = {}
    try:
        actions = _apply_item(reg, asset, item, actor=actor,
                              dry_run=False, counters=counters)
        reg.save()
    except Exception as e:  # noqa: BLE001
        logger.warning("[backfill] sync(%s) 回填失败(advisory): %s", skill_id, e)
        return {"ok": False, "skill_id": skill_id, "error": str(e)}
    return {"ok": True, "skill_id": skill_id,
            "capability_id": item["capability_id"],
            "actions": actions,
            "provenance": item["provenance"]["level"]}
