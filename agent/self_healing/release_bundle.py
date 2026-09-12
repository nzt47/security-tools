"""整包回滚原子单位 —— ReleaseBundle（TASK-S4-03 步骤 2 / v7.2 §4.4 P7.2-15 + §11.7）

【规则原文（§4.4 〔P7.2-15〕）】
    整包回滚规则：回滚原子单位 = 整包 release（§11.7）：code + skills + weights +
    data-baseline + manifest 的整体 hash。**禁止只回技能不回代码**——四套版本空间耦合，
    部分回滚 = 状态不一致（**直接触发 L4**）。

【本模块解决什么】
    云枢此前的"回滚"是**分散且按对象**的：`agent/skills_mgmt/rollback.py::AutoRollback`
    回的是**技能版本**，`agent/p6_snapshot.py` 存的是**运行时状态快照**，代码回退靠 git。
    三者互不知情，于是"只回技能不回代码"在机制上是**可以发生**的——那正是 P7.2-15 要
    禁掉的状态不一致。本模块把回滚的**原子单位**提升到「整包 release」：
    五个组成部分（code / skills / weights / data_baseline / manifest）各自带版本与哈希，
    合起来派生一个 `bundle_hash`；回滚**只接受** `bundle_hash`，不接受组件子集。

【三条不变量（对应验收项）】
    1. **不可拆**：`rollback_bundle()` 的 `components` 参数只允许两种取值——不传（整包）
       或恰好等于全部五组件。任何**真子集**抛 `PartialRollbackError` 并触发 L4 事故卡。
    2. **一致性可验**：`verify_consistency()` 把「当前实际组件版本」与「目标 bundle」逐项
       比对；**部分匹配**（有的组件已在目标版本、有的不在）即判为部分回滚残留 → L4。
       这条覆盖"事故已经发生了"的事后检测，与第 1 条的事前拒绝互补。
    3. **绝不真动仓库**：本模块**不 import subprocess、不跑 git**。真正的落地动作由调用方
       以 `applier=` 注入；`applier=None` 时自动降级为 dry-run（只出计划，无副作用）。
       这是「用例严禁操作真实仓库数据、绝不在测试里真跑 git checkout/merge」的机制保证
       ——不是靠约定，是靠**没有那个能力**。

【不易】纯标准库 + `agent.self_healing.levels`；所有落盘路径可显式指定。
【变易】组件集 `COMPONENT_NAMES` 是数据：v7.2 若增补组件（如 eval-anchor），加一项即可，
       旧 bundle 因缺组件会在 `build_bundle` 处显式报错而非静默通过。
【简易】无全局单例；`ReleaseStore` 实例一切显式。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from agent.self_healing.levels import (
    HealLevel,
    emit_healing_triggered,
    raise_incident,
    record_healing_audit,
)

logger = logging.getLogger("agent.self_healing.release_bundle")

# ════════════════════════════════════════════════════════════
#  常量
# ════════════════════════════════════════════════════════════

#: 整包五组件（§4.4 P7.2-15 逐字：code + skills + weights + data-baseline + manifest）
#: 顺序即**规范序**——`compute_bundle_hash` 按此序拼接，保证同内容同哈希。
COMPONENT_CODE = "code"
COMPONENT_SKILLS = "skills"
COMPONENT_WEIGHTS = "weights"
COMPONENT_DATA_BASELINE = "data_baseline"
COMPONENT_MANIFEST = "manifest"

COMPONENT_NAMES: Tuple[str, ...] = (
    COMPONENT_CODE, COMPONENT_SKILLS, COMPONENT_WEIGHTS,
    COMPONENT_DATA_BASELINE, COMPONENT_MANIFEST,
)

#: 「不可拆」的说明映射（拒绝文案引用，便于定位是哪几套版本空间耦合）
COMPONENT_LABELS: Dict[str, str] = {
    COMPONENT_CODE: "代码（git 提交）",
    COMPONENT_SKILLS: "技能集版本",
    COMPONENT_WEIGHTS: "权重/embedding 基线",
    COMPONENT_DATA_BASELINE: "数据基线",
    COMPONENT_MANIFEST: "manifest",
}

#: 默认 release 台账目录（`CP_RELEASE_BUNDLES_DIR` 可覆盖；用例必须显式传路径）
DEFAULT_BUNDLES_DIR = os.path.join("data", "releases")
ENV_BUNDLES_DIR = "CP_RELEASE_BUNDLES_DIR"

#: 台账文件名
LEDGER_FILENAME = "release_bundles.json"

#: 哈希前缀（与 S2-02 审计链同款：算法可辨）
HASH_ALGO = "sha256"


class BundleError(Exception):
    """整包回滚基类异常"""


class PartialRollbackError(BundleError):
    """**部分回滚**被拒绝（只回技能不回代码）——§4.4 P7.2-15 直接触发 L4

    Attributes:
        requested: 调用方请求回滚的组件集。
        missing: 请求中**缺失**的组件（正是"只回技能不回代码"里的"代码"）。
        extra: 请求中**多出**的未知组件。
        incident_id: 同时开出的事故卡 id（L4）。
    """

    def __init__(self, message: str, *, requested: Sequence[str] = (),
                 missing: Sequence[str] = (), extra: Sequence[str] = (),
                 incident_id: str = "") -> None:
        self.requested = list(requested)
        self.missing = list(missing)
        self.extra = list(extra)
        self.incident_id = incident_id
        super().__init__(message)


class BundleNotFoundError(BundleError):
    """目标 bundle 不在台账中"""


class BundleIntegrityError(BundleError):
    """bundle 内容与自身 hash 不符（篡改/损坏）"""


class BundleValidationError(BundleError):
    """bundle 构造非法（缺组件/字段非法）"""


# ════════════════════════════════════════════════════════════
#  组件与整包
# ════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class BundleComponent:
    """单个版本空间组件（版本 + 内容哈希 + 可追溯引用）"""

    name: str
    version: str
    hash: str
    ref: str = ""       # 可追溯引用（git sha / 目录 / 文件名），仅留痕不参与哈希

    def canonical(self) -> str:
        """参与 bundle_hash 的规范串（**不含 ref**——ref 是溯源元数据，不是内容身份）"""
        return f"{self.name}@{self.version}#{self.hash}"

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "version": self.version,
                "hash": self.hash, "ref": self.ref}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "BundleComponent":
        return cls(
            name=str(data.get("name") or ""),
            version=str(data.get("version") or ""),
            hash=str(data.get("hash") or ""),
            ref=str(data.get("ref") or ""),
        )


def compute_bundle_hash(components: Mapping[str, "BundleComponent"]) -> str:
    """五组件 → 整包 hash（规范序 + 稳定拼接）

    【为什么是「整体 hash」而不是「五个 hash 的列表」】§4.4 要的是**一个**回滚单位。
    单独回滚某个组件在台账层面**无从表达**——因为台账里根本没有"组件级回滚"这个操作，
    只有 `bundle_hash`。这是"不可拆"从数据模型层就成立的原因。

    Raises:
        BundleValidationError: 缺组件或组件字段为空。
    """
    missing = [n for n in COMPONENT_NAMES if n not in components]
    if missing:
        raise BundleValidationError(
            f"整包缺组件 {missing}；五组件必须齐备（§4.4 P7.2-15）"
        )
    extra = [n for n in components if n not in COMPONENT_NAMES]
    if extra:
        raise BundleValidationError(f"未知组件 {extra}；组件集为 {list(COMPONENT_NAMES)}")
    parts: List[str] = []
    for name in COMPONENT_NAMES:
        comp = components[name]
        if not comp.version or not comp.hash:
            raise BundleValidationError(f"组件 {name} 的 version/hash 不得为空")
        parts.append(comp.canonical())
    material = "|".join(parts)
    return f"{HASH_ALGO}:{hashlib.sha256(material.encode('utf-8')).hexdigest()}"


@dataclass
class ReleaseBundle:
    """整包 release 快照（回滚的**唯一**原子单位）"""

    components: Dict[str, BundleComponent]
    bundle_hash: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    release_tag: str = ""          # §11.7 签名 tag（留痕字段，本模块不验签）
    note: str = ""
    bundle_id: str = ""            # 人类可读短 id（hash 前 12 位）

    def __post_init__(self) -> None:
        if not self.bundle_hash:
            self.bundle_hash = compute_bundle_hash(self.components)
        if not self.bundle_id:
            self.bundle_id = "rb-" + self.bundle_hash.split(":", 1)[-1][:12]

    # ── 自校验 ──

    def verify_integrity(self) -> bool:
        """内容与自身 hash 是否一致（防台账被改）"""
        return compute_bundle_hash(self.components) == self.bundle_hash

    def component_versions(self) -> Dict[str, str]:
        """{组件名: 版本}（一致性比对的紧凑形态）"""
        return {name: self.components[name].version for name in COMPONENT_NAMES}

    def component_hashes(self) -> Dict[str, str]:
        """{组件名: 内容哈希}"""
        return {name: self.components[name].hash for name in COMPONENT_NAMES}

    # ── 序列化 ──

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bundle_hash": self.bundle_hash,
            "bundle_id": self.bundle_id,
            "created_at": self.created_at,
            "release_tag": self.release_tag,
            "note": self.note,
            "components": {n: self.components[n].to_dict() for n in COMPONENT_NAMES},
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ReleaseBundle":
        raw = dict(data.get("components") or {})
        components = {n: BundleComponent.from_dict(raw[n]) for n in COMPONENT_NAMES if n in raw}
        return cls(
            components=components,
            bundle_hash=str(data.get("bundle_hash") or ""),
            created_at=str(data.get("created_at") or datetime.now().isoformat()),
            release_tag=str(data.get("release_tag") or ""),
            note=str(data.get("note") or ""),
            bundle_id=str(data.get("bundle_id") or ""),
        )


def build_bundle(
    components: Mapping[str, Any],
    *,
    release_tag: str = "",
    note: str = "",
) -> ReleaseBundle:
    """由五组件构造整包

    Args:
        components: {组件名: BundleComponent | dict | ("version", "hash") | "version"}。
            - `BundleComponent` 直接用；
            - dict → `BundleComponent.from_dict`；
            - 2 元组 → (version, hash)；
            - 字符串 → 视为 version，hash 由 `version` 派生确定性占位（**仅用于
              无内容哈希可用的降级场景**，会在 note 中标注）。

    Raises:
        BundleValidationError: 缺组件/未知组件/字段非法。
    """
    normalized: Dict[str, BundleComponent] = {}
    degraded: List[str] = []
    for name in COMPONENT_NAMES:
        if name not in components:
            raise BundleValidationError(
                f"构造整包缺组件 {name!r}；五组件必须齐备（§4.4 P7.2-15）"
            )
        raw = components[name]
        if isinstance(raw, BundleComponent):
            normalized[name] = raw if raw.name == name else BundleComponent(
                name=name, version=raw.version, hash=raw.hash, ref=raw.ref)
        elif isinstance(raw, Mapping):
            comp = BundleComponent.from_dict(raw)
            normalized[name] = BundleComponent(comp.name or name, comp.version, comp.hash, comp.ref)
        elif isinstance(raw, (tuple, list)) and len(raw) == 2:
            normalized[name] = BundleComponent(name, str(raw[0]), str(raw[1]))
        elif isinstance(raw, str):
            normalized[name] = BundleComponent(
                name, raw, f"{HASH_ALGO}:{hashlib.sha256(raw.encode()).hexdigest()}")
            degraded.append(name)
        else:
            raise BundleValidationError(f"组件 {name} 形态非法: {type(raw).__name__}")
    if degraded:
        note = (note + "｜" if note else "") + f"version-only 降级组件: {','.join(degraded)}"
    return ReleaseBundle(components=normalized, release_tag=release_tag, note=note)


# ════════════════════════════════════════════════════════════
#  内容哈希工具（显式路径；**不触网、不跑 git**）
# ════════════════════════════════════════════════════════════

_HASH_CHUNK = 1 << 20


def hash_path(path: Any) -> str:
    """对**文件或目录**求内容哈希（目录按相对路径排序后逐个累加）

    【纪律】只读、无副作用、不跟随符号链接目录环（用 `os.walk(followlinks=False)`）。
    调用方必须**显式**给出路径——本函数不会自行猜仓库位置，避免"用例误伤真实数据"。
    """
    target = Path(str(path))
    digest = hashlib.sha256()
    if target.is_file():
        digest.update(_file_chunks(target))
    elif target.is_dir():
        for root, dirs, files in os.walk(target, followlinks=False):
            dirs.sort()
            for name in sorted(files):
                file_path = Path(root) / name
                rel = file_path.relative_to(target).as_posix()
                digest.update(rel.encode("utf-8"))
                digest.update(_file_chunks(file_path))
    else:
        raise BundleValidationError(f"路径不存在: {target}")
    return f"{HASH_ALGO}:{digest.hexdigest()}"


def _file_chunks(path: Path) -> bytes:
    """文件内容（空文件返回占位，保证哈希不与其他空输入混淆）"""
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(_HASH_CHUNK)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
    if size == 0:
        return b"<empty>"
    return digest.digest()


def hash_mapping(values: Mapping[str, Any]) -> str:
    """对 {名: 值} 求稳定哈希（用于 data-baseline / manifest 这类结构化内容）"""
    material = json.dumps(values, ensure_ascii=False, sort_keys=True, default=str)
    return f"{HASH_ALGO}:{hashlib.sha256(material.encode('utf-8')).hexdigest()}"


# ════════════════════════════════════════════════════════════
#  台账（显式路径的 bundle 存储）
# ════════════════════════════════════════════════════════════


def bundles_dir(directory: Optional[str] = None) -> Path:
    """台账目录（显式 > 环境变量 > 默认）"""
    raw = directory or os.environ.get(ENV_BUNDLES_DIR) or DEFAULT_BUNDLES_DIR
    return Path(raw)


class ReleaseStore:
    """整包台账（JSON；一 bundle 一行；**路径显式**）

    【不易】台账是**只追加**的：`put()` 对已存在的 hash 幂等（内容一致时不动，
    不一致时抛 `BundleIntegrityError`）——防止同一 hash 指向两份不同内容。
    """

    def __init__(self, path: Optional[Any] = None, *, directory: Optional[str] = None) -> None:
        self._explicit_path = Path(str(path)) if path else None
        self._directory = directory
        self._bundles: Dict[str, ReleaseBundle] = {}
        self._loaded = False

    # ── 路径 ──

    @property
    def path(self) -> Path:
        if self._explicit_path is not None:
            return self._explicit_path
        return bundles_dir(self._directory) / LEDGER_FILENAME

    # ── 读写 ──

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        path = self.path
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 台账损坏不抛：降级为空 + 告警
            logger.warning("整包台账解析失败（按空台账处理）: %s: %s", path, exc)
            return
        for row in (data.get("bundles") or []):
            try:
                bundle = ReleaseBundle.from_dict(row)
                if not bundle.verify_integrity():
                    logger.warning("台账中 bundle 完整性校验失败，跳过: %s", bundle.bundle_id)
                    continue
                self._bundles[bundle.bundle_hash] = bundle
            except Exception as exc:  # noqa: BLE001 单行损坏不影响其余
                logger.warning("台账单行损坏，跳过: %s", exc)

    def _persist(self) -> None:
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"schema": "release_bundle.v1",
                   "bundles": [b.to_dict() for b in self._bundles.values()]}
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    # ── 台账 API ──

    def put(self, bundle: ReleaseBundle, *, persist: bool = True) -> ReleaseBundle:
        """登记整包（幂等：同 hash 同内容不重复写）

        【注】"同 hash 但内容不同"这一分支在正常构造下**不可达**——`bundle_hash` 由
        组件内容派生，且入库前先过 `verify_integrity()`（重算必须等于存储值）。
        保留该分支是**纵深防御**：若将来出现显式指定 `bundle_hash` 的构造路径
        （如从外部 release 清单导入），这里仍能挡住"同 hash 指向两份内容"。
        """
        self._ensure_loaded()
        if not bundle.verify_integrity():
            raise BundleIntegrityError(
                f"bundle {bundle.bundle_id} 内容与 hash 不符，拒绝入库"
            )
        existing = self._bundles.get(bundle.bundle_hash)
        if existing is not None:
            if existing.component_hashes() != bundle.component_hashes():
                raise BundleIntegrityError(
                    f"同一 hash {bundle.bundle_hash} 对应两份不同内容，拒绝覆盖"
                )
            return existing
        self._bundles[bundle.bundle_hash] = bundle
        if persist:
            self._persist()
        return bundle

    def get(self, bundle_hash: str) -> Optional[ReleaseBundle]:
        """按 hash 取整包（不存在返回 None）"""
        self._ensure_loaded()
        return self._bundles.get(str(bundle_hash or ""))

    def require(self, bundle_hash: str) -> ReleaseBundle:
        """按 hash 取整包（不存在抛 `BundleNotFoundError`）"""
        bundle = self.get(bundle_hash)
        if bundle is None:
            raise BundleNotFoundError(f"整包不在台账中: {bundle_hash!r}")
        return bundle

    def latest(self) -> Optional[ReleaseBundle]:
        """最近登记的一包（按 created_at；同刻按登记序）"""
        self._ensure_loaded()
        if not self._bundles:
            return None
        return max(self._bundles.values(), key=lambda b: (b.created_at, b.bundle_id))

    def list(self) -> List[ReleaseBundle]:
        """全部整包（按 created_at 升序）"""
        self._ensure_loaded()
        return sorted(self._bundles.values(), key=lambda b: (b.created_at, b.bundle_id))

    def count(self) -> int:
        self._ensure_loaded()
        return len(self._bundles)


def snapshot_bundle(
    components: Mapping[str, Any],
    *,
    store: Optional[ReleaseStore] = None,
    release_tag: str = "",
    note: str = "",
    persist: bool = True,
) -> ReleaseBundle:
    """创建整包快照并入台账（"插件升级前自动快照入 D5" / §11.7）

    Returns:
        入库后的 `ReleaseBundle`（含 `bundle_hash`）。
    """
    bundle = build_bundle(components, release_tag=release_tag, note=note)
    if store is not None:
        store.put(bundle, persist=persist)
    record_healing_audit(
        HealLevel.L4, action="release.snapshot",
        subject=f"bundle:{bundle.bundle_id}",
        payload={"bundle_hash": bundle.bundle_hash,
                 "component_versions": bundle.component_versions(),
                 "release_tag": bundle.release_tag},
    )
    return bundle


# ════════════════════════════════════════════════════════════
#  部分回滚防护
# ════════════════════════════════════════════════════════════


def _normalize_requested(components: Optional[Iterable[str]]) -> Optional[Tuple[str, ...]]:
    """归一请求的组件集；None/空 → None（=整包）"""
    if components is None:
        return None
    items = tuple(str(c or "").strip() for c in components)
    items = tuple(c for c in items if c)
    return items or None


def check_atomic_request(
    components: Optional[Iterable[str]],
    *,
    trigger_incident: bool = True,
    tenant_id: str = "default",
    incidents_dir: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
) -> None:
    """**原子性闸门**：拒绝任何非整包的组件子集

    这是"禁止只回技能不回代码"的**事前**守卫。任何真子集（包括只传 `skills` 这种
    最典型的错误形态）都会被拒，并在此直接触发 **L4** 事故卡 + 审计 + 事件
    ——§4.4 原文"部分回滚 = 状态不一致（直接触发 L4）"。

    Args:
        components: 请求回滚的组件集；None = 整包（通过）。
        trigger_incident: 拒绝时是否开 L4 事故卡（默认开；dry 校验可关）。

    Raises:
        PartialRollbackError: 请求为真子集或缺/多组件。
    """
    requested = _normalize_requested(components)
    if requested is None:
        return
    requested_set = set(requested)
    all_set = set(COMPONENT_NAMES)
    duplicates = sorted({c for c in requested if requested.count(c) > 1})
    missing = [n for n in COMPONENT_NAMES if n not in requested_set]
    extra = [n for n in requested if n not in all_set]
    if not missing and not extra and not duplicates and len(requested) == len(COMPONENT_NAMES):
        return
    labels = "、".join(COMPONENT_LABELS.get(n, n) for n in missing)
    message = (
        f"拒绝部分回滚：整包回滚的原子单位是整包 release（code+skills+weights+"
        f"data-baseline+manifest 的整体 hash，§4.4 P7.2-15）。请求组件={list(requested)}"
        f"，缺失={missing}（{labels or '-'}），未知={extra}，重复={duplicates}。"
        f"部分回滚 = 状态不一致，已触发 L4。"
    )
    incident_id = ""
    if trigger_incident:
        card = raise_incident(
            HealLevel.L4,
            signal="partial_rollback",
            root_cause="整包回滚请求被拆分为组件子集（P7.2-15 禁止：四套版本空间耦合）",
            fatal_change=str((context or {}).get("fatal_change") or ""),
            trace_ids=list((context or {}).get("trace_ids") or []),
            tenant_id=tenant_id,
            directory=incidents_dir,
            detail={"requested": list(requested), "missing": missing, "extra": extra,
                    "duplicates": duplicates,
                    "component_labels": {n: COMPONENT_LABELS.get(n, n) for n in missing}},
        )
        incident_id = card.incident_id
        emit_healing_triggered(
            HealLevel.L4, signal="partial_rollback",
            tenant_id=tenant_id, incident_id=incident_id,
            extra={"requested": list(requested), "missing": missing,
                   "duplicates": duplicates},
        )
    raise PartialRollbackError(message, requested=requested, missing=missing,
                              extra=sorted(set(extra)), incident_id=incident_id)


def verify_consistency(
    current: Mapping[str, Any],
    bundle: ReleaseBundle,
    *,
    trigger_incident: bool = True,
    tenant_id: str = "default",
    incidents_dir: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """**事后**一致性校验：当前实际组件 vs 目标整包

    覆盖"事故已经发生了"的场景——某个组件被人单独回退了，整包其余部分还在原地。
    判定规则：
        - 五组件**全部**等于目标 → `consistent`；
        - 五组件**全部**不等于目标 → `diverged`（是"还没开始回滚"，不是部分回滚）；
        - **部分等于、部分不等于** → `partial`（部分回滚残留）→ L4 事故卡。

    Args:
        current: {组件名: 版本 或 hash}（多给的键忽略；缺的键视为"未知"→ 不计入判定）。

    Returns:
        {status, matched, mismatched, unknown, bundle_hash, incident_id}
        status ∈ {"consistent", "diverged", "partial", "unknown"}
    """
    matched: List[str] = []
    mismatched: List[str] = []
    unknown: List[str] = []
    for name in COMPONENT_NAMES:
        if name not in current or current[name] in (None, ""):
            unknown.append(name)
            continue
        value = str(current[name])
        comp = bundle.components[name]
        if value in (comp.version, comp.hash):
            matched.append(name)
        else:
            mismatched.append(name)

    if unknown:
        status = "unknown"
    elif not mismatched:
        status = "consistent"
    elif not matched:
        status = "diverged"
    else:
        status = "partial"

    incident_id = ""
    if status == "partial" and trigger_incident:
        card = raise_incident(
            HealLevel.L4,
            signal="partial_rollback",
            root_cause="检测到部分回滚残留：部分组件已在目标版本、部分未跟随（P7.2-15 状态不一致）",
            fatal_change=str((context or {}).get("fatal_change") or ""),
            trace_ids=list((context or {}).get("trace_ids") or []),
            tenant_id=tenant_id,
            directory=incidents_dir,
            detail={"matched": matched, "mismatched": mismatched,
                    "bundle_hash": bundle.bundle_hash,
                    "target_versions": bundle.component_versions()},
        )
        incident_id = card.incident_id
        emit_healing_triggered(
            HealLevel.L4, signal="partial_rollback", tenant_id=tenant_id,
            incident_id=incident_id,
            extra={"matched": matched, "mismatched": mismatched},
        )
    return {
        "status": status,
        "matched": matched,
        "mismatched": mismatched,
        "unknown": unknown,
        "bundle_hash": bundle.bundle_hash,
        "incident_id": incident_id,
        "target_versions": bundle.component_versions(),
    }


# ════════════════════════════════════════════════════════════
#  回滚入口
# ════════════════════════════════════════════════════════════


@dataclass
class RollbackPlan:
    """整包回滚计划（**五组件同时移动**——计划的形状本身就体现了原子性）"""

    bundle_hash: str
    bundle_id: str
    moves: Dict[str, Dict[str, str]]      # 组件 → {from_version, to_version, from_hash, to_hash}
    from_bundle_hash: str = ""
    dry_run: bool = True
    applied: bool = False
    note: str = ""

    def component_count(self) -> int:
        return len(self.moves)

    def is_full_bundle(self) -> bool:
        """计划是否覆盖全部五组件（恒为 True——子集在入口就被拒了）"""
        return set(self.moves) == set(COMPONENT_NAMES)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bundle_hash": self.bundle_hash,
            "bundle_id": self.bundle_id,
            "from_bundle_hash": self.from_bundle_hash,
            "moves": {k: dict(v) for k, v in self.moves.items()},
            "component_count": self.component_count(),
            "dry_run": self.dry_run,
            "applied": self.applied,
            "note": self.note,
        }


def plan_rollback(
    bundle: ReleaseBundle,
    *,
    current: Optional[Mapping[str, Any]] = None,
    from_bundle: Optional[ReleaseBundle] = None,
) -> RollbackPlan:
    """生成整包回滚计划（纯计算，无副作用）

    Args:
        bundle: 目标整包。
        current: 当前各组件版本（缺省时用 `from_bundle`）。
        from_bundle: 起始整包（提供 `from_*` 字段）。
    """
    base = current if current is not None else (
        from_bundle.component_versions() if from_bundle is not None else {})
    moves: Dict[str, Dict[str, str]] = {}
    for name in COMPONENT_NAMES:
        target = bundle.components[name]
        from_version = str(base.get(name) or "")
        from_hash = ""
        if from_bundle is not None and name in from_bundle.components:
            from_hash = from_bundle.components[name].hash
        moves[name] = {"from_version": from_version,
                       "to_version": target.version,
                       "from_hash": from_hash,
                       "to_hash": target.hash}
    return RollbackPlan(
        bundle_hash=bundle.bundle_hash,
        bundle_id=bundle.bundle_id,
        moves=moves,
        from_bundle_hash=from_bundle.bundle_hash if from_bundle is not None else "",
    )


def rollback_bundle(
    bundle_hash: str,
    *,
    store: Optional[ReleaseStore] = None,
    components: Optional[Iterable[str]] = None,
    current: Optional[Mapping[str, Any]] = None,
    applier: Optional[Callable[[RollbackPlan], Any]] = None,
    dry_run: bool = False,
    tenant_id: str = "default",
    incidents_dir: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
) -> RollbackPlan:
    """**整包回滚**入口（唯一合法的回滚操作；组件子集一律拒绝）

    执行顺序（顺序即安全边界）：
        1. **原子性闸门**——`components` 若为真子集 → `PartialRollbackError` + L4（先拒后做）；
        2. 目标整包必须**在台账中**且**自校验通过**（防回滚到一个不存在的/被篡改的包）；
        3. 生成五组件计划（`plan_rollback`）；
        4. `applier` 存在且非 dry-run → 调用 `applier(plan)` 落地；否则只返回计划。

    【为什么 `applier=None` 时强制 dry-run】"绝不在测试里真跑 git checkout/merge"不能靠
    约定。本模块**没有**执行能力，`applier` 是唯一副作用入口；不给就是不给。

    Args:
        bundle_hash: 目标整包 hash（**唯一**回滚标识）。
        store: 台账（None → 默认目录台账）。
        components: **只允许** None 或恰好五组件全集；真子集 → 拒绝 + L4。
        current: 当前组件版本（用于生成 from_*）。
        applier: 落地回调（接收 `RollbackPlan`）；None = dry-run。
        dry_run: 显式 dry-run（即便给了 applier 也不调用）。
        tenant_id / incidents_dir / context: 事故卡与审计上下文。

    Returns:
        `RollbackPlan`（`applied=True` 表示 applier 已成功返回）。

    Raises:
        PartialRollbackError: 部分回滚请求（已触发 L4）。
        BundleNotFoundError: 目标包不在台账。
        BundleIntegrityError: 目标包自校验失败。
    """
    # 1) 原子性闸门（**最先**执行——任何副作用之前）
    check_atomic_request(components, tenant_id=tenant_id,
                         incidents_dir=incidents_dir, context=context)

    ledger = store if store is not None else ReleaseStore()
    # 2) 目标包存在且完整
    bundle = ledger.require(bundle_hash)
    if not bundle.verify_integrity():
        raise BundleIntegrityError(f"目标整包 {bundle.bundle_id} 自校验失败，拒绝回滚")

    # 3) 计划
    plan = plan_rollback(bundle, current=current)
    plan.dry_run = dry_run or applier is None
    if plan.dry_run:
        plan.note = ("dry-run（未提供 applier 或显式 dry_run）——五组件计划已生成，无副作用"
                     if applier is None else "显式 dry_run")

    # 4) 落地（唯一副作用入口）
    if applier is not None and not dry_run:
        try:
            applier(plan)
            plan.applied = True
        except Exception as exc:  # noqa: BLE001 回滚失败 → L4（补偿/快照路径）
            logger.error("整包回滚 applier 失败: %s: %s", type(exc).__name__, exc)
            raise_incident(
                HealLevel.L4, signal="snapshot_restore_failed",
                root_cause=f"整包回滚落地失败: {type(exc).__name__}: {exc}",
                tenant_id=tenant_id, directory=incidents_dir,
                detail={"bundle_hash": bundle_hash,
                        "component_versions": bundle.component_versions()},
            )
            raise

    record_healing_audit(
        HealLevel.L4,
        action="release.rollback" if plan.applied else "release.rollback.plan",
        subject=f"bundle:{bundle.bundle_id}",
        payload={"bundle_hash": bundle.bundle_hash,
                 "component_count": plan.component_count(),
                 "applied": plan.applied, "dry_run": plan.dry_run,
                 "component_versions": bundle.component_versions()},
    )
    if plan.applied:
        emit_healing_triggered(
            HealLevel.L4, signal="verified_failure", tenant_id=tenant_id,
            extra={"action": "rollback_bundle", "bundle_hash": bundle.bundle_hash},
        )
    return plan


def reset_bundle_state() -> None:
    """无模块级可变状态；保留该函数以统一用例隔离入口（幂等）"""
    return None


__all__ = [
    # 常量
    "COMPONENT_NAMES", "COMPONENT_CODE", "COMPONENT_SKILLS", "COMPONENT_WEIGHTS",
    "COMPONENT_DATA_BASELINE", "COMPONENT_MANIFEST", "COMPONENT_LABELS",
    "DEFAULT_BUNDLES_DIR", "ENV_BUNDLES_DIR", "LEDGER_FILENAME", "HASH_ALGO",
    # 异常
    "BundleError", "PartialRollbackError", "BundleNotFoundError",
    "BundleIntegrityError", "BundleValidationError",
    # 模型
    "BundleComponent", "ReleaseBundle", "RollbackPlan",
    # 构造与哈希
    "build_bundle", "compute_bundle_hash", "hash_path", "hash_mapping",
    # 台账
    "ReleaseStore", "snapshot_bundle", "bundles_dir",
    # 防护与回滚
    "check_atomic_request", "verify_consistency", "plan_rollback", "rollback_bundle",
    "reset_bundle_state",
]
