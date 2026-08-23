"""元规则版本化存储与变更门控（任务4 Step 2/3）— 护栏 G1 落地载体

【背景（Why）】
    TASK-08 护栏 G1 要求："更新规则本身（变异策略、评估阈值、进化权重、触发条件）
    必须进入版本管理，任何变更走 bump_version + 门控；禁止运行时无版本修改"。
    审计发现进化参数散落 .env / config.yaml / 代码默认值三层且无版本化
    （元审计 T5：G1 无实现锚点）。本模块提供：
        1. 元规则登记表（data/learning/meta_policy/schema.json）：逐项 schema
           （名称/类型/默认值/合法范围/生效读取点/所属护栏）；
        2. 版本化存储：current.json（当前生效）+ versions/vN.json（不可变快照）
           + pending.json（待审批）+ audit.jsonl（G3 审计）；
        3. 变更门控：bump → 审批队列（复用 approval.py）→ 批准后生效；
           拒绝保持当前版本；pending 版本零生效；
        4. schema 校验：非法值回退默认（沿用 EVO 总览 §六.2 约定）；
        5. CLI：list / show / bump / rollback / diff / validate / migrate / approve /
           reject / status。

【不易边界（禁止触碰）】
    - 不改变任何既有模块的参数读取语义（环境变量 > config.yaml > 硬编码默认值
      优先级保持不变）；本模块只做"登记 + 快照 + 门控 + 查询"；
    - 不修改 approval.py / rollback.py / value_guard.py / lineage.py 既有接口
      （仅复用 ApprovalFlow 公开 API）；
    - 元规则存储默认只读：任何变更必须过审批链，无审批零生效；
    - 变更失败绝不阻断主流程：所有写操作异常 → 告警 + 返回错误结果。

【配置（.env / config.yaml learning.meta_policy，环境变量优先）】
    META_POLICY_ENABLED                  存储/变更总开关，默认 true（false 时
                                         变更操作全部拒绝，只读可用）
    META_POLICY_STORE_DIR                存储根目录，默认 data/learning/meta_policy
    META_POLICY_APPROVAL_LEVEL           变更审批级别（L0/L1/L2），默认 L1
                                         （L1：人工批准后系统自动生效）
    META_POLICY_AUDIT_FILE               审计 JSONL 路径（默认 store_dir/audit.jsonl）
    META_POLICY_MIGRATE_DRY_RUN_ONLY     migrate 只做 dry-run，默认 true（安全底线）
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# ════════════════════════════════════════════════════════════
#  常量与默认路径
# ════════════════════════════════════════════════════════════

_LOGGER_NAME = "MetaPolicy"
SCHEMA_VERSION = 1

_SCHEMA_FILE = "schema.json"
_CURRENT_FILE = "current.json"
_PENDING_FILE = "pending.json"
_VERSIONS_DIR = "versions"
_AUDIT_FILE = "audit.jsonl"

_DEFAULT_STORE_DIR = (
    Path(__file__).resolve().parent.parent.parent
    / "data" / "learning" / "meta_policy"
)

_ENV_ENABLED = "META_POLICY_ENABLED"
_ENV_STORE_DIR = "META_POLICY_STORE_DIR"
_ENV_APPROVAL_LEVEL = "META_POLICY_APPROVAL_LEVEL"
_ENV_AUDIT_FILE = "META_POLICY_AUDIT_FILE"
_ENV_MIGRATE_DRY_RUN_ONLY = "META_POLICY_MIGRATE_DRY_RUN_ONLY"

# 审批级别（与 approval.py APPROVAL_LEVELS 对齐）
_APPROVAL_LEVELS = ("L0", "L1", "L2")

# 触发来源 / 执行者（与 lineage TRIGGERS/ACTORS 语义对齐）
_TRIGGERS = ("manual", "scheduler", "feedback", "api")
_ACTORS = ("system", "user", "reviewer")


def _log(level: str, msg: str, *args: Any) -> None:
    """结构化日志（缺省 logging 配置时也可见）"""
    try:
        import logging
        getattr(logging.getLogger(_LOGGER_NAME), level)(msg, *args)
    except Exception:  # noqa: BLE001 日志失败零影响
        pass


# ════════════════════════════════════════════════════════════
#  配置读取（环境变量 > config.yaml learning.meta_policy > 默认值）
# ════════════════════════════════════════════════════════════

_CONFIG_YAML_CACHE: Optional[Dict[str, Any]] = None


def _config_yaml() -> Optional[Dict[str, Any]]:
    """读取仓库根 config.yaml（带缓存；失败返回 None，零影响）"""
    global _CONFIG_YAML_CACHE
    if _CONFIG_YAML_CACHE is not None:
        return _CONFIG_YAML_CACHE or None
    try:
        path = Path(__file__).resolve().parent.parent.parent / "config.yaml"
        if path.exists():
            import yaml as _yaml
            with open(path, "r", encoding="utf-8") as f:
                _CONFIG_YAML_CACHE = _yaml.safe_load(f) or {}
                return _CONFIG_YAML_CACHE
    except Exception:  # noqa: BLE001 配置解析失败回退默认
        pass
    _CONFIG_YAML_CACHE = {}
    return None


def _meta_policy_cfg() -> Dict[str, Any]:
    cfg = _config_yaml()
    if cfg is not None:
        section = ((cfg.get("learning", {}) or {}).get("meta_policy", {}) or {})
        return section
    return {}


def _env_str(key: str, default: str) -> str:
    v = os.getenv(key)
    if v is not None and str(v).strip():
        return str(v).strip()
    return default


def _env_bool(key: str, default: bool) -> bool:
    v = os.getenv(key)
    if v is None or not str(v).strip():
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def _store_dir() -> Path:
    env = os.getenv(_ENV_STORE_DIR)
    if env and str(env).strip():
        return Path(str(env).strip())
    cfg = _meta_policy_cfg().get("store_dir")
    if cfg:
        return Path(str(cfg))
    return _DEFAULT_STORE_DIR


def _enabled() -> bool:
    if os.getenv(_ENV_ENABLED) is not None and str(os.getenv(_ENV_ENABLED)).strip():
        return _env_bool(_ENV_ENABLED, True)
    cfg = _meta_policy_cfg().get("enabled")
    if cfg is not None:
        return str(cfg).strip().lower() in ("1", "true", "yes", "on")
    return True


def _approval_level() -> str:
    level = _env_str(_ENV_APPROVAL_LEVEL, "L1").upper()
    cfg = _meta_policy_cfg().get("approval_level")
    if cfg is not None:
        level = str(cfg).strip().upper()
    if level not in _APPROVAL_LEVELS:
        _log("warning", "[MetaPolicy] 非法 approval_level=%r，回退 L1", level)
        return "L1"
    return level


def _migrate_dry_run_only() -> bool:
    if os.getenv(_ENV_MIGRATE_DRY_RUN_ONLY) is not None \
            and str(os.getenv(_ENV_MIGRATE_DRY_RUN_ONLY)).strip():
        return _env_bool(_ENV_MIGRATE_DRY_RUN_ONLY, True)
    cfg = _meta_policy_cfg().get("migrate_dry_run_only")
    if cfg is not None:
        return str(cfg).strip().lower() in ("1", "true", "yes", "on")
    return True


# ════════════════════════════════════════════════════════════
#  底层 IO（原子写入 / 损坏容错，与 skills_mgmt 同策略）
# ════════════════════════════════════════════════════════════

def _atomic_write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", delete=False,
        dir=str(path.parent), suffix=".tmp",
    ) as tmp:
        json.dump(data, tmp, ensure_ascii=False, indent=2)
        tmp_path = tmp.name
    for attempt in range(3):
        try:
            os.replace(tmp_path, path)
            return
        except OSError:
            if attempt == 2:
                _log("error", "[MetaPolicy] 写入失败 path=%s（重试 3 次后放弃）", path)
                raise
            time.sleep(0.05)


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError) as e:
        _log("warning", "[MetaPolicy] %s 读取失败（按空处理）: %s", path, e)
        return None


def _append_jsonl(path: Path, record: Dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as e:
        # 审计写入失败绝不阻断主流程（守不易）
        _log("warning", "[MetaPolicy] 审计写入失败 %s: %s", path, e)


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    records: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except (json.JSONDecodeError, ValueError):
                    continue
    except (OSError, UnicodeDecodeError):
        pass
    return records


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _change_id() -> str:
    return f"mpc-{datetime.now().strftime('%Y%m%d%H%M%S%f')}-{uuid.uuid4().hex[:6]}"


# ════════════════════════════════════════════════════════════
#  Schema：登记表加载与值校验（非法值回退默认）
# ════════════════════════════════════════════════════════════

_BOOL_TRUE = ("1", "true", "yes", "on")
_BOOL_FALSE = ("0", "false", "no", "off")


class MetaPolicyError(Exception):
    """元规则存储异常基类（CLI 层转退出码；业务层捕获后零影响）"""


class SchemaError(MetaPolicyError):
    """schema 缺失 / 非法参数名"""


def _parse_bool(raw: Any) -> Optional[bool]:
    if isinstance(raw, bool):
        return raw
    s = str(raw).strip().lower()
    if s in _BOOL_TRUE:
        return True
    if s in _BOOL_FALSE:
        return False
    return None


def validate_value(entry: Dict[str, Any], raw: Any) -> Dict[str, Any]:
    """按 schema 校验单个值；非法值回退默认（沿用 .env 非法值回退约定）

    Returns:
        {valid, value, default, reason}
        valid=False 时 value 为 schema 默认值（回退），reason 说明原因。
    """
    name = str(entry.get("name", ""))
    default = entry.get("default")
    type_name = str(entry.get("type", "str")).lower()
    enum = entry.get("enum")
    lo = entry.get("min")
    hi = entry.get("max")
    result: Dict[str, Any] = {"name": name, "valid": True,
                              "value": raw, "default": default, "reason": ""}
    try:
        if type_name == "int":
            value = int(raw) if not isinstance(raw, bool) else int(raw)
        elif type_name == "float":
            value = float(raw) if not isinstance(raw, bool) else float(raw)
        elif type_name == "bool":
            parsed = _parse_bool(raw)
            if parsed is None:
                raise ValueError(f"非法布尔值: {raw!r}")
            value = parsed
        elif type_name == "enum":
            if str(raw).strip() not in enum:
                raise ValueError(f"非法枚举值: {raw!r}（允许: {enum}）")
            value = str(raw).strip()
        else:  # str / 其他
            value = str(raw)
        if lo is not None and value < lo:
            raise ValueError(f"低于下限 {lo}")
        if hi is not None and value > hi:
            raise ValueError(f"高于上限 {hi}")
    except (TypeError, ValueError) as e:
        result["valid"] = False
        result["value"] = default
        result["reason"] = f"{name}={raw!r} 非法（{e}），回退默认 {default!r}"
        _log("warning", "[MetaPolicy] %s", result["reason"])
        return result
    result["value"] = value
    return result


class MetaPolicySchema:
    """元规则登记表（schema.json）只读视图

    加载顺序: store_dir/schema.json > 默认 schema.json（data/learning/meta_policy/）。
    默认 schema 为规范登记表；store_dir 下无 schema 时（如测试用临时目录、
    自定义 META_POLICY_STORE_DIR）回退默认登记表，保证校验语义一致。
    """

    def __init__(self, store_dir: Optional[Path] = None):
        self._store_dir = Path(store_dir) if store_dir else _store_dir()
        self._entries: Dict[str, Dict[str, Any]] = {}
        self._path = self._store_dir / _SCHEMA_FILE
        self._load()

    def _load(self) -> None:
        path = self._store_dir / _SCHEMA_FILE
        data = _read_json(path)
        if not data:
            default_path = _DEFAULT_STORE_DIR / _SCHEMA_FILE
            if default_path != path:
                _log("info",
                     "[MetaPolicy] %s 无 schema.json，回退默认登记表 %s",
                     self._store_dir, default_path)
                data = _read_json(default_path)
                if data:
                    self._path = default_path
        if not data:
            _log("warning",
                 "[MetaPolicy] schema.json 缺失或损坏（store 与默认均不可用）"
                 "→ 空登记表（校验退化为宽松）")
            return
        for entry in (data.get("entries") or []):
            if isinstance(entry, dict) and entry.get("name"):
                self._entries[str(entry["name"])] = entry

    @property
    def path(self) -> Path:
        return self._path

    def __len__(self) -> int:
        return len(self._entries)

    def names(self) -> List[str]:
        return list(self._entries.keys())

    def get(self, name: str) -> Optional[Dict[str, Any]]:
        return self._entries.get(name)

    def defaults(self) -> Dict[str, Any]:
        return {name: e.get("default") for name, e in self._entries.items()}

    def validate(self, values: Dict[str, Any]) -> Dict[str, Any]:
        """校验一组变更值（全部须为已登记参数）

        Returns:
            {valid, results: [{name, valid, value, default, reason}],
             normalized: {name: 最终值}}
        """
        results: List[Dict[str, Any]] = []
        normalized: Dict[str, Any] = {}
        unknown = [k for k in values if k not in self._entries]
        if unknown:
            raise SchemaError(f"未登记参数（schema 无此条目）: {sorted(unknown)}")
        for name, raw in values.items():
            entry = self._entries[name]
            res = validate_value(entry, raw)
            results.append(res)
            normalized[name] = res["value"]
        return {"valid": all(r["valid"] for r in results),
                "results": results, "normalized": normalized}


# ════════════════════════════════════════════════════════════
#  数据模型
# ════════════════════════════════════════════════════════════

@dataclass
class MetaPolicyChange:
    """一次元规则变更（bump/rollback 的统一产物）"""
    change_id: str = ""
    version: str = ""                 # 新版本号（vN）
    parent_version: str = ""          # 变更前版本号
    values: Dict[str, Any] = field(default_factory=dict)   # 新版本完整值快照
    changes: Dict[str, Any] = field(default_factory=dict)  # 变更参数 {name: new}
    old_values: Dict[str, Any] = field(default_factory=dict)  # 变更参数旧值
    description: str = ""
    status: str = "pending"           # pending / approved / rejected / applied
    approval_record_id: str = ""
    approval_level: str = "L1"
    actor: str = "system"
    trigger: str = "manual"
    created_at: str = ""
    applied_at: str = ""
    rollback_command: str = ""

    def __post_init__(self) -> None:
        if not self.change_id:
            self.change_id = _change_id()
        if not self.created_at:
            self.created_at = _now()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class BumpResult:
    """bump/rollback 结果"""
    ok: bool = False
    change_id: str = ""
    version: str = ""
    status: str = ""                  # pending / effective / rejected / error
    effective: bool = False           # 是否已生效（仅审批通过并应用）
    approval_record_id: str = ""
    message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ════════════════════════════════════════════════════════════
#  MetaPolicyStore
# ════════════════════════════════════════════════════════════

class MetaPolicyStore:
    """元规则版本化存储 — JSON + 版本快照 + 审批门控（线程安全）

    布局（store_dir/）:
        schema.json        登记表（只读，交付物）
        current.json       当前生效版本指针与值快照
        pending.json       待审批变更（bump 产物；未批准零生效）
        versions/vN.json   不可变版本快照（v1=引导默认值）
        audit.jsonl        G3 审计（change_id/param/old/new/approver/rollback_command）

    用法:
        store = MetaPolicyStore()
        store.effective_values()                      # 只读查询（不受 pending 影响）
        res = store.bump({"evolver.interval_days": 14}, description="...")
        flow.approve(res.approval_record_id, actor="reviewer")
        flow.merge(res.approval_record_id, actor="reviewer")   # → 生效
    """

    def __init__(self, store_dir: Optional[str] = None, *,
                 approval_flow: Optional[Any] = None,
                 approval_level: Optional[str] = None,
                 enabled: Optional[bool] = None):
        """Args:
            store_dir: 存储根目录（None=按 .env/config/默认）
            approval_flow: 注入 ApprovalFlow 实例（None=懒加载默认实例，
                           便于测试隔离；跨进程 CLI 各自构造）
            approval_level: 变更审批级别（None=按 .env/config/默认 L1）
            enabled: 变更总开关（None=按 .env/config/默认 true）
        """
        self._store_dir = Path(store_dir) if store_dir else _store_dir()
        self._enabled = enabled if enabled is not None else _enabled()
        self._approval_level = (
            approval_level if approval_level is not None else _approval_level())
        self._flow = approval_flow
        # 审计路径解析：显式 env 覆盖 > 本实例 store_dir/audit.jsonl
        # （临时/隔离 store_dir 的审计随实例隔离，绝不泄漏到默认仓库路径）
        explicit_audit = os.getenv(_ENV_AUDIT_FILE)
        self._audit_path = (Path(str(explicit_audit).strip())
                            if explicit_audit else self._store_dir / _AUDIT_FILE)
        self._lock = threading.RLock()
        self._schema = MetaPolicySchema(self._store_dir)
        self._bootstrap()
        _log("info",
             "[MetaPolicy] 初始化完成 enabled=%s store_dir=%s schema=%d approval_level=%s",
             self._enabled, self._store_dir, len(self._schema),
             self._approval_level)

    # ─── 属性 ───

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def store_dir(self) -> Path:
        return self._store_dir

    @property
    def schema(self) -> MetaPolicySchema:
        return self._schema

    @property
    def audit_path(self) -> Path:
        return self._audit_path

    # ─── 内部文件路径 ───

    def _current_path(self) -> Path:
        return self._store_dir / _CURRENT_FILE

    def _pending_path(self) -> Path:
        return self._store_dir / _PENDING_FILE

    def _versions_dir(self) -> Path:
        return self._store_dir / _VERSIONS_DIR

    def _version_path(self, version: str) -> Path:
        return self._versions_dir() / f"{version}.json"

    # ─── 引导（首用初始化：登记默认值为 v1，只读快照）───

    def _bootstrap(self) -> None:
        """首次使用时以 schema 默认值建立 v1（仅登记，非变更，无审批）"""
        with self._lock:
            if self._current_path().exists():
                return
            defaults = self._schema.defaults()
            current = {
                "schema_version": SCHEMA_VERSION,
                "version": "v1",
                "values": defaults,
                "change_id": "bootstrap",
                "description": "初始登记：schema 默认值（非变更）",
                "effective_at": _now(),
                "created_at": _now(),
            }
            try:
                _atomic_write_json(self._current_path(), current)
                _atomic_write_json(self._version_path("v1"), current)
            except OSError as e:
                # 存储不可写 → 只读降级（effective_values 返回空并告警）
                _log("warning", "[MetaPolicy] 引导快照写入失败（只读降级）: %s", e)

    # ─── 只读查询 ───

    def effective_values(self) -> Dict[str, Any]:
        """当前生效值快照（pending 版本绝不进入此视图——未审批零生效）"""
        current = _read_json(self._current_path())
        if not current:
            _log("warning", "[MetaPolicy] current.json 缺失，返回空生效值")
            return {}
        return dict((current.get("values") or {}))

    def get_effective_value(self, name: str) -> Optional[Any]:
        """按名称读取当前生效值；未登记/缺失返回 None"""
        if name not in self._schema.names():
            _log("warning", "[MetaPolicy] 查询未登记参数 %s（返回 None）", name)
            return None
        return self.effective_values().get(name)

    def current_version(self) -> str:
        current = _read_json(self._current_path()) or {}
        return str(current.get("version") or "v1")

    def current_info(self) -> Dict[str, Any]:
        current = _read_json(self._current_path()) or {}
        return {
            "schema_version": current.get("schema_version", SCHEMA_VERSION),
            "version": current.get("version", "v1"),
            "change_id": current.get("change_id", "bootstrap"),
            "description": current.get("description", ""),
            "effective_at": current.get("effective_at", ""),
            "created_at": current.get("created_at", ""),
        }

    def list_versions(self) -> List[Dict[str, Any]]:
        """列出全部版本快照（按版本号升序）"""
        out: List[Dict[str, Any]] = []
        vdir = self._versions_dir()
        if vdir.exists():
            for p in sorted(vdir.glob("v*.json")):
                data = _read_json(p) or {}
                out.append({
                    "version": data.get("version") or p.stem,
                    "change_id": data.get("change_id", ""),
                    "description": data.get("description", ""),
                    "parent_version": data.get("parent_version", ""),
                    "effective_at": data.get("effective_at", ""),
                    "created_at": data.get("created_at", ""),
                })
        current = self.current_version()
        for item in out:
            item["status"] = "effective" if item["version"] == current else "superseded"
        return out

    def show(self, version: Optional[str] = None) -> Dict[str, Any]:
        """展示某版本（None=当前生效）的完整值快照"""
        if version is None or version == self.current_version():
            current = _read_json(self._current_path()) or {}
            return {
                "version": current.get("version", "v1"),
                "change_id": current.get("change_id", ""),
                "effective_at": current.get("effective_at", ""),
                "values": dict((current.get("values") or {})),
            }
        data = _read_json(self._version_path(str(version)))
        if not data:
            raise MetaPolicyError(f"版本不存在: {version}")
        return {
            "version": data.get("version", version),
            "change_id": data.get("change_id", ""),
            "effective_at": data.get("effective_at", ""),
            "values": dict((data.get("values") or {})),
        }

    def pending(self) -> Optional[Dict[str, Any]]:
        """当前待审批变更（无则 None）"""
        return _read_json(self._pending_path())

    def diff(self, from_version: Optional[str] = None,
             to_version: Optional[str] = None) -> Dict[str, Any]:
        """两个版本间差异（默认：当前 vs 上一版本）"""
        versions = self.list_versions()
        if not versions:
            return {"changed": [], "added": [], "removed": [],
                    "from": None, "to": None, "count": 0}
        to = to_version or self.current_version()
        if from_version is None:
            idx = next((i for i, v in enumerate(versions)
                        if v["version"] == to), None)
            if idx is None or idx == 0:
                raise MetaPolicyError(
                    f"版本 {to} 无更早版本可比较（diff 需指定 --from）")
            from_version = versions[idx - 1]["version"]
        from_data = self.show(from_version)
        to_data = self.show(to)
        fv = from_data["values"]
        tv = to_data["values"]
        changed, added, removed = {}, {}, {}
        for k in sorted(set(fv) | set(tv)):
            if k not in fv:
                added[k] = tv[k]
            elif k not in tv:
                removed[k] = fv[k]
            elif fv[k] != tv[k]:
                changed[k] = {"from": fv[k], "to": tv[k]}
        return {"from": from_version, "to": to,
                "changed": changed, "added": added, "removed": removed,
                "count": len(changed) + len(added) + len(removed)}

    def validate(self, values: Dict[str, Any]) -> Dict[str, Any]:
        """校验一组变更值（CLI validate / bump 前置校验共用）"""
        return self._schema.validate(values)

    # ─── 变更门控：bump ───

    def bump(self, changes: Dict[str, Any], *, description: str = "",
             actor: str = "system", trigger: str = "manual",
             auto_approve: bool = False) -> BumpResult:
        """提出一次元规则变更（进入审批队列；批准后生效）

        流程: schema 校验（非法值回退默认）→ 快照当前 → 写新版本(pending)
              → 审批队列（approval.py）→ 批准并 merge 后生效。

        Args:
            changes: {param_name: new_value}（必须全部为 schema 已登记参数）
            description: 变更说明（写入审批记录与审计）
            actor / trigger: 提交者 / 触发来源
            auto_approve: 仅测试/工具使用——直接走审批流 approve+merge
                          （生产路径应由人工在 UI/CLI 审批）
        """
        if not self._enabled:
            return BumpResult(ok=False, status="error",
                              message="META_POLICY_ENABLED=false，元规则变更被拒绝（只读）")
        if not changes:
            return BumpResult(ok=False, status="error", message="变更集为空")
        try:
            report = self._schema.validate(changes)
        except SchemaError as e:
            return BumpResult(ok=False, status="error", message=str(e))
        if not report["valid"]:
            reasons = "; ".join(r["reason"] for r in report["results"] if not r["valid"])
            return BumpResult(ok=False, status="error",
                              message=f"schema 校验失败（已回退默认）: {reasons}")
        try:
            return self._bump_locked(changes, report, description=description,
                                     actor=actor, trigger=trigger,
                                     auto_approve=auto_approve)
        except Exception as e:  # noqa: BLE001 兜底——变更失败绝不阻断主流程
            _log("error", "[MetaPolicy] bump 异常: %s", e)
            return BumpResult(ok=False, status="error", message=f"bump 异常: {e}")

    def _bump_locked(self, changes: Dict[str, Any], report: Dict[str, Any], *,
                     description: str, actor: str, trigger: str,
                     auto_approve: bool) -> BumpResult:
        """bump 主流程（持锁执行）"""
        with self._lock:
            # 单槽 pending：已有待审批变更时拒绝新变更（串行治理，防覆盖）
            existing = _read_json(self._pending_path())
            if existing and str(existing.get("status", "")) == "pending":
                return BumpResult(
                    ok=False, status="error",
                    message=(f"已有待审批变更 {existing.get('version')} "
                             f"（change={existing.get('change_id')}），"
                             "请先审批/驳回后再提出新变更"))
            current = self.effective_values()
            new_values = dict(current)
            old_values: Dict[str, Any] = {}
            for name, value in report["normalized"].items():
                old_values[name] = current.get(name)
                new_values[name] = value
            parent_version = self.current_version()
            version = self._next_version(parent_version)
            change = MetaPolicyChange(
                version=version, parent_version=parent_version,
                values=new_values, changes=report["normalized"],
                old_values=old_values, description=description,
                actor=actor, trigger=trigger,
                approval_level=self._approval_level,
                rollback_command=self._rollback_command(version),
            )
            try:
                # 不可变版本快照（pending 态也留档，供审批比对）
                _atomic_write_json(self._version_path(version), change.to_dict())
                _atomic_write_json(self._pending_path(), change.to_dict())
            except OSError as e:
                return BumpResult(ok=False, status="error",
                                  message=f"版本快照写入失败: {e}")
            # 进入审批队列（复用 approval.py ApprovalFlow）
            try:
                rec = self._submit_approval(change)
                change.approval_record_id = rec.record_id
                # 重新落 pending（补齐 approval_record_id）
                _atomic_write_json(self._pending_path(), change.to_dict())
            except Exception as e:  # noqa: BLE001 审批不可用 → 变更不生效，仅告警
                _log("error", "[MetaPolicy] 审批队列提交失败（变更不生效）: %s", e)
                return BumpResult(
                    ok=False, status="error",
                    message=f"审批队列提交失败（变更未生效）: {e}",
                    change_id=change.change_id, version=version)
            self._audit("bump", change, approver="",
                        status=change.status)
            if auto_approve:
                return self._auto_approve(change)
            _log("info",
                 "[MetaPolicy] 变更待审批 change=%s version=%s→%s record=%s level=%s",
                 change.change_id, parent_version, version,
                 change.approval_record_id, self._approval_level)
            return BumpResult(
                ok=True, change_id=change.change_id, version=version,
                status="pending", effective=False,
                approval_record_id=change.approval_record_id,
                message=f"已进入审批队列（{self._approval_level}），批准后生效")

    def _next_version(self, parent_version: str) -> str:
        try:
            n = int(str(parent_version).lstrip("v")) + 1
        except ValueError:
            n = len(self.list_versions()) + 1
        return f"v{n}"

    def _submit_approval(self, change: MetaPolicyChange) -> Any:
        """复用 approval.py：object_type=meta_policy / action=bump_version"""
        flow = self._get_flow()
        payload = {
            "version": change.version,
            "parent_version": change.parent_version,
            "change_id": change.change_id,
            "changes": change.changes,
            "values": change.values,
        }
        return flow.submit(
            "meta_policy", "meta_policy",
            action="bump_version",
            description=change.description or f"元规则变更 → {change.version}",
            payload=payload,
            actor=change.actor,
            trigger=change.trigger,
            level=self._approval_level,
            applier=self._make_applier(change.change_id),
        )

    def _make_applier(self, change_id: str):
        """审批 merge 时执行：把 pending 版本激活为当前生效（幂等）"""
        def _apply() -> None:
            self._activate_pending(change_id)
        return _apply

    def _get_flow(self) -> Any:
        if self._flow is None:
            from agent.skills_mgmt.approval import ApprovalFlow
            self._flow = ApprovalFlow()
        return self._flow

    # ─── 审批动作（CLI / API 调用方）───

    def approve_and_apply(self, record_id: str, *, actor: str = "reviewer",
                          note: str = "") -> BumpResult:
        """审批通过并应用（pending_review → approved → merged/archived）

        进程内 applier 存在时走 flow.merge（approved → merged）；
        跨进程（无 applier）时降级为 store 直接激活 + 归档（approved → archived），
        均保持 ApprovalFlow 合法状态迁移（只用公开 API）。
        """
        flow = self._get_flow()
        try:
            rec = flow.approve(record_id, actor=actor, note=note)
        except Exception as e:  # noqa: BLE001
            return BumpResult(ok=False, status="error",
                              message=f"审批通过失败: {e}")
        change = self._pending_change_for(record_id)
        if change is None:
            return BumpResult(ok=False, status="error",
                              message=f"审批记录 {record_id} 无对应 pending 变更")
        try:
            flow.merge(record_id, actor=actor)
            merged = True
        except Exception:  # noqa: BLE001 无 applier（跨进程）→ store 直接激活
            merged = False
        if not merged:
            try:
                self._activate_pending(change["change_id"])
                flow.mark_manual_executed(record_id, actor=actor,
                                          note="meta-policy CLI 应用（无进程内 applier）")
            except Exception as e:  # noqa: BLE001
                return BumpResult(ok=False, status="error",
                                  message=f"应用失败: {e}")
        return BumpResult(
            ok=True, change_id=change["change_id"], version=change["version"],
            status="effective", effective=True,
            approval_record_id=record_id,
            message=f"已批准并生效（version={change['version']}）")

    def reject_change(self, record_id: str, *, actor: str = "reviewer",
                      reason: str = "") -> BumpResult:
        """驳回变更（pending_review → rejected；当前版本保持不变）"""
        if not reason.strip():
            return BumpResult(ok=False, status="error",
                              message="reject 必须提供 reason（审计要求）")
        flow = self._get_flow()
        try:
            rec = flow.reject(record_id, actor=actor, reason=reason)
        except Exception as e:  # noqa: BLE001
            return BumpResult(ok=False, status="error",
                              message=f"驳回失败: {e}")
        change = self._pending_change_for(record_id)
        if change is not None:
            change["status"] = "rejected"
            change["decision_reason"] = reason
            _atomic_write_json(self._pending_path(), change)
            self._audit("reject", change, approver=actor,
                        status="rejected")
        _log("warning", "[MetaPolicy] 变更被驳回 record=%s reason=%s",
             record_id, reason)
        return BumpResult(
            ok=True, change_id=(change or {}).get("change_id", ""),
            version=(change or {}).get("version", ""),
            status="rejected", effective=False,
            approval_record_id=record_id,
            message="已驳回，当前版本保持不变")

    def _pending_change_for(self, record_id: str) -> Optional[Dict[str, Any]]:
        pending = _read_json(self._pending_path())
        if pending and pending.get("approval_record_id") == record_id:
            return pending
        # 跨进程重启后 pending.json 的 approval_record_id 已落盘 → 直接命中
        return pending if pending and str(pending.get("approval_record_id", "")) == record_id else None

    def _auto_approve(self, change: MetaPolicyChange) -> BumpResult:
        """测试/工具路径：一次完成 approve+merge（等价于人工立即批准）"""
        return self.approve_and_apply(change.approval_record_id, actor="system")

    def _activate_pending(self, change_id: str) -> None:
        """把 pending 变更激活为当前生效（幂等；仅审批链可调用）"""
        with self._lock:
            pending = _read_json(self._pending_path())
            if not pending:
                raise MetaPolicyError("无待审批变更（pending.json 缺失）")
            if pending.get("change_id") != change_id:
                raise MetaPolicyError(
                    f"change_id 不匹配: {change_id} != {pending.get('change_id')}")
            version = str(pending["version"])
            current = {
                "schema_version": SCHEMA_VERSION,
                "version": version,
                "values": dict((pending.get("values") or {})),
                "change_id": pending.get("change_id", ""),
                "description": pending.get("description", ""),
                "parent_version": pending.get("parent_version", ""),
                "effective_at": _now(),
                "created_at": pending.get("created_at", _now()),
            }
            _atomic_write_json(self._current_path(), current)
            pending["status"] = "applied"
            pending["applied_at"] = _now()
            _atomic_write_json(self._pending_path(), pending)
            self._audit("apply", pending, approver="approval-flow",
                        status="applied")
            _log("info", "[MetaPolicy] 版本 %s 已生效（change=%s）",
                 version, change_id)

    # ─── 回滚 ───

    def rollback(self, *, target_version: Optional[str] = None,
                 description: str = "", actor: str = "system",
                 trigger: str = "manual",
                 auto_approve: bool = False) -> BumpResult:
        """回滚到指定版本（默认上一版本）：以目标版本值提出一次受控变更

        回滚本身也是元规则变更：走 bump 同款审批链 + 版本快照 + 审计
        （G1：更新规则本身也是被管制的对象）。
        """
        if not self._enabled:
            return BumpResult(ok=False, status="error",
                              message="META_POLICY_ENABLED=false，回滚被拒绝（只读）")
        versions = self.list_versions()
        if not versions:
            return BumpResult(ok=False, status="error", message="无版本可回滚")
        current = self.current_version()
        if target_version is None:
            idx = next((i for i, v in enumerate(versions)
                        if v["version"] == current), None)
            if idx is None or idx == 0:
                return BumpResult(
                    ok=False, status="error",
                    message=f"当前版本 {current} 无可回滚的上一版本")
            target_version = versions[idx - 1]["version"]
        if target_version == current:
            return BumpResult(ok=False, status="error",
                              message=f"目标版本 {target_version} 即当前版本")
        target = self.show(target_version)
        desc = description or f"回滚到 {target_version}（当前 {current}）"
        # 计算相对当前的实际变更（仅回滚有差异的参数）
        current_values = self.effective_values()
        target_values = target["values"]
        changes = {k: v for k, v in target_values.items()
                   if current_values.get(k) != v}
        result = self.bump(changes, description=desc, actor=actor,
                           trigger=trigger, auto_approve=auto_approve)
        if result.ok:
            result.message = (f"{result.message}（回滚目标 {target_version}）")
        return result

    def _rollback_command(self, version: str) -> str:
        """审计字段：回滚到该版本的可执行命令"""
        return (f"python -m agent.learning.meta_policy rollback "
                f"--target {version}")

    # ─── 迁移（灰度可选；默认仅 dry-run 报告差异）───

    def migrate(self, params: Optional[List[str]] = None, *,
                apply: bool = False) -> Dict[str, Any]:
        """预演/执行把参数从 .env/config 迁移到 meta_policy 存储

        默认（META_POLICY_MIGRATE_DRY_RUN_ONLY=true）只报告差异不写任何内容；
        apply=True 且开关关闭时：对存在差异的参数发起一次受控 bump
        （仍走审批链，绝不绕过门控）。

        Returns:
            {dry_run, drifted: [{name, store, runtime}], aligned: [...],
             apply: bool, applied_change_id: str|None}
        """
        dry_run_only = _migrate_dry_run_only()
        if apply and dry_run_only:
            return {"dry_run": True, "error":
                    "META_POLICY_MIGRATE_DRY_RUN_ONLY=true，migrate 仅 dry-run（安全底线）",
                    "drifted": [], "aligned": [], "applied_change_id": None}
        names = [n for n in self._schema.names()
                 if params is None or n in params]
        store_values = self.effective_values()
        drifted: List[Dict[str, Any]] = []
        aligned: List[str] = []
        for name in names:
            runtime = read_runtime_value(name)
            store = store_values.get(name)
            if runtime != store:
                drifted.append({"name": name, "store": store, "runtime": runtime})
            else:
                aligned.append(name)
        result: Dict[str, Any] = {
            "dry_run": not apply,
            "drifted_count": len(drifted),
            "drifted": drifted,
            "aligned_count": len(aligned),
            "aligned": aligned,
            "applied_change_id": None,
        }
        if apply and drifted:
            changes = {d["name"]: d["runtime"] for d in drifted}
            res = self.bump(changes, description="migrate: 同步运行时值到元规则存储")
            result["applied_change_id"] = res.change_id or None
            result["apply_result"] = res.to_dict()
        return result

    # ─── G3 审计 ───

    def _audit(self, event: str, change: Any, *,
               approver: str, status: str) -> None:
        """每次 bump/rollback/approve/reject/apply 写审计（G3 字段齐备）

        change 兼容 MetaPolicyChange（dataclass）与 dict（pending 快照）。
        """
        if hasattr(change, "to_dict"):
            change = change.to_dict()
        for name, old in (change.get("old_values") or {}).items():
            _append_jsonl(self._audit_path, {
                "ts": _now(),
                "event": event,
                "change_id": change.get("change_id", ""),
                "version": change.get("version", ""),
                "param": name,
                "old": old,
                "new": (change.get("changes") or {}).get(name),
                "approver": approver,
                "actor": change.get("actor", "system"),
                "status": status,
                "approval_record_id": change.get("approval_record_id", ""),
                "rollback_command": change.get("rollback_command", ""),
            })

    def list_audit(self, limit: int = 50) -> List[Dict[str, Any]]:
        """审计记录（倒序）"""
        rows = _read_jsonl(self._audit_path)
        rows.reverse()
        return rows[: max(1, min(1000, int(limit)))]

    # ─── 状态聚合（护栏 G1 数据源）───

    def status(self) -> Dict[str, Any]:
        """护栏 G1 状态快照（供 guard_status 聚合）"""
        pending = self.pending()
        info = self.current_info()
        versions = self.list_versions()
        return {
            "enabled": self._enabled,
            "store_dir": str(self._store_dir),
            "schema_entries": len(self._schema),
            "schema_path": str(self._schema.path),
            "current_version": info["version"],
            "effective_at": info.get("effective_at", ""),
            "last_change_id": info.get("change_id", "bootstrap"),
            "last_change_description": info.get("description", ""),
            "versions_count": len(versions),
            "pending": {
                "version": (pending or {}).get("version"),
                "change_id": (pending or {}).get("change_id"),
                "status": (pending or {}).get("status", "none"),
                "approval_record_id": (pending or {}).get("approval_record_id"),
                "created_at": (pending or {}).get("created_at"),
            } if pending else None,
            "approval_level": self._approval_level,
            "rollback_command": (f"python -m agent.learning.meta_policy "
                                 f"rollback --target {info['version']}"),
            "migrate_dry_run_only": _migrate_dry_run_only(),
        }


# ════════════════════════════════════════════════════════════
#  运行时值读取（仅登记用途；不改变任何模块读取语义）
# ════════════════════════════════════════════════════════════

def read_runtime_value(name: str) -> Any:
    """按 schema 读取某参数的【实际生效值】：环境变量 > config.yaml > 代码默认

    与各模块读取语义一致（env > config > 硬编码默认），仅用于 migrate 差异报告
    与 guard_status 展示；绝不把结果写回任何生产读取路径。
    """
    entry = MetaPolicySchema().get(name)
    if entry is None:
        return None
    env_key = entry.get("env")
    if env_key:
        raw = os.getenv(str(env_key))
        if raw is not None and str(raw).strip():
            return _coerce_runtime(entry, raw)
    cfg_path = entry.get("config_path")
    if cfg_path:
        cfg = _config_yaml()
        if cfg is not None:
            node: Any = cfg
            ok = True
            for part in str(cfg_path).split("."):
                if isinstance(node, dict) and part in node:
                    node = node[part]
                else:
                    ok = False
                    break
            if ok and node is not None:
                return _coerce_runtime(entry, node)
    return entry.get("default")


def _coerce_runtime(entry: Dict[str, Any], raw: Any) -> Any:
    """把原始值按 schema 类型归一（非法 → 默认，与 validate_value 同语义）"""
    return validate_value(entry, raw)["value"]


# ════════════════════════════════════════════════════════════
#  单例（只读查询高频路径）
# ════════════════════════════════════════════════════════════

_global_store: Optional[MetaPolicyStore] = None
_global_store_lock = threading.Lock()


def get_meta_policy_store() -> MetaPolicyStore:
    """获取全局元规则存储单例（懒加载）"""
    global _global_store
    if _global_store is None:
        with _global_store_lock:
            if _global_store is None:
                _global_store = MetaPolicyStore()
    return _global_store


def reset_meta_policy_store() -> None:
    """重置单例（仅测试使用）"""
    global _global_store
    with _global_store_lock:
        _global_store = None


# ════════════════════════════════════════════════════════════
#  CLI
# ════════════════════════════════════════════════════════════

def _parse_changes(items: List[str]) -> Dict[str, Any]:
    """解析 'name=value' 列表 → {name: value}（值先按字符串解析，校验时转类型）"""
    changes: Dict[str, Any] = {}
    for item in items:
        if "=" not in item:
            raise MetaPolicyError(f"变更项须为 name=value: {item!r}")
        name, _, raw = item.partition("=")
        name = name.strip()
        raw = raw.strip()
        if not name:
            raise MetaPolicyError(f"参数名为空: {item!r}")
        changes[name] = _raw_parse(raw)
    return changes


def _raw_parse(raw: str) -> Any:
    """字符串 → 字面量（int/float/bool/JSON；失败按字符串保留）"""
    t = raw.strip()
    low = t.lower()
    if low in ("true", "false"):
        return low == "true"
    try:
        return int(t)
    except ValueError:
        pass
    try:
        return float(t)
    except ValueError:
        pass
    return t


def _print(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        description="元规则版本化存储 CLI（任务4：G1 元规则版本化与变更门控）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="列出全部版本快照")
    sub.add_parser("status", help="护栏 G1 状态快照")

    p_show = sub.add_parser("show", help="展示某版本值快照（默认当前生效）")
    p_show.add_argument("--version", default=None, help="版本号（如 v1）")

    p_bump = sub.add_parser("bump", help="提出元规则变更（进入审批队列）")
    p_bump.add_argument("--param", action="append", default=[],
                        help="name=value 变更项（可多次）")
    p_bump.add_argument("--description", default="", help="变更说明")
    p_bump.add_argument("--actor", default="system")
    p_bump.add_argument("--trigger", default="manual",
                        choices=_TRIGGERS)
    p_bump.add_argument("--auto-approve", action="store_true",
                        help="测试/工具路径：立即批准生效（生产请人工审批）")

    p_roll = sub.add_parser("rollback", help="回滚到目标版本（默认上一版本，走审批链）")
    p_roll.add_argument("--target", default=None, help="目标版本（如 v1）")
    p_roll.add_argument("--description", default="")
    p_roll.add_argument("--actor", default="system")
    p_roll.add_argument("--trigger", default="manual", choices=_TRIGGERS)

    p_diff = sub.add_parser("diff", help="两个版本间差异（默认当前 vs 上一版本）")
    p_diff.add_argument("--from", dest="from_version", default=None)
    p_diff.add_argument("--to", dest="to_version", default=None)

    p_val = sub.add_parser("validate", help="校验一组值（非法值回退默认）")
    p_val.add_argument("--param", action="append", default=[],
                       help="name=value（可多次）")

    p_appr = sub.add_parser("approve", help="审批通过并应用变更（record_id）")
    p_appr.add_argument("--record", required=True, help="审批记录 ID")
    p_appr.add_argument("--actor", default="reviewer")

    p_rej = sub.add_parser("reject", help="驳回变更（当前版本保持不变）")
    p_rej.add_argument("--record", required=True, help="审批记录 ID")
    p_rej.add_argument("--reason", required=True, help="驳回原因（审计要求）")
    p_rej.add_argument("--actor", default="reviewer")

    p_mig = sub.add_parser("migrate", help="迁移预演（默认 dry-run，仅报告差异）")
    p_mig.add_argument("--param", action="append", default=[],
                       help="参数名过滤（可多次，默认全部）")
    p_mig.add_argument("--apply", action="store_true",
                       help="实际发起受控 bump（需 META_POLICY_MIGRATE_DRY_RUN_ONLY=false）")

    p_audit = sub.add_parser("audit", help="审计记录（G3）")
    p_audit.add_argument("--limit", type=int, default=50)

    args = parser.parse_args(argv)
    try:
        store = MetaPolicyStore()
        cmd = args.command
        if cmd == "list":
            _print({"current_version": store.current_version(),
                    "versions": store.list_versions()})
        elif cmd == "status":
            _print(store.status())
        elif cmd == "show":
            _print(store.show(args.version))
        elif cmd == "bump":
            changes = _parse_changes(args.param)
            result = store.bump(changes, description=args.description,
                                actor=args.actor, trigger=args.trigger,
                                auto_approve=args.auto_approve)
            _print(result.to_dict())
            return 0 if result.ok else 2
        elif cmd == "rollback":
            result = store.rollback(target_version=args.target,
                                    description=args.description,
                                    actor=args.actor, trigger=args.trigger)
            _print(result.to_dict())
            return 0 if result.ok else 2
        elif cmd == "diff":
            _print(store.diff(args.from_version, args.to_version))
        elif cmd == "validate":
            changes = _parse_changes(args.param)
            _print(store.validate(changes))
        elif cmd == "approve":
            result = store.approve_and_apply(args.record, actor=args.actor)
            _print(result.to_dict())
            return 0 if result.ok else 2
        elif cmd == "reject":
            result = store.reject_change(args.record, actor=args.actor,
                                         reason=args.reason)
            _print(result.to_dict())
            return 0 if result.ok else 2
        elif cmd == "migrate":
            _print(store.migrate(args.param or None, apply=args.apply))
        elif cmd == "audit":
            _print({"count": len(store.list_audit(args.limit)),
                    "records": store.list_audit(args.limit)})
        return 0
    except MetaPolicyError as e:
        _print({"error": str(e)})
        return 2
    except Exception as e:  # noqa: BLE001 CLI 异常 → 退出码 3
        _print({"error": f"CLI 执行失败: {e}"})
        return 3


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "MetaPolicyStore", "MetaPolicySchema", "MetaPolicyChange", "BumpResult",
    "MetaPolicyError", "SchemaError", "validate_value",
    "read_runtime_value", "get_meta_policy_store", "reset_meta_policy_store",
    "main",
]
