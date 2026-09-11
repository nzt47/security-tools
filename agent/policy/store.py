"""PolicyStore — 策略库（版本化 + effective_range + 签名校验）

【职责边界】
    本模块只做「策略从哪里来、哪一版生效、有没有被人改过」。它**不判定**（判定在
    ``matcher``）、**不编排**（编排在 ``engine``）、**不执行**（执行在调用点）。

【版本化（§3.11 ``version`` + 云枢裁定 #1 SemVer）】
    - 同一 ``id`` 可以有多个版本：``add()`` 追加历史，**不覆盖**（策略变更必须留痕）。
    - ``active()`` 每个 id 只出**最高 SemVer** 的那一版；同版本重复 add ⇒ 幂等
      （内容不同则报错，避免「同版本不同文本」这种无法追责的状态）。
    - ``history(id)`` 返回该 id 的全部版本（时间序），供审计与模拟器定位。

【effective_range（云枢裁定 #2）】
    范围判定在**决策时**做（``Policy.is_active``），不在装载时过滤：装载时过滤会让
    「策略到期」这件事在长驻进程里静默失效（进程不再重新装载）。决策时判定保证了
    「到期即不生效」，也保证 ``revision`` 不必因时间流逝而变化（缓存键稳定）。

【签名校验】
    口令与降级口径见 ``signing.py``。装载策略：
      - 策略**带** ``signature`` ⇒ 一律验，失败报错（不静默接受被改过的策略）；
      - 策略**不带** ``signature`` ⇒ 默认放行（存量/手写策略），
        ``require_signature=True``（或 ``CP_POLICY_REQUIRE_SIGNATURE=1``）时拒绝。

【内置不变量（不可被策略文件遮蔽）】
    ``secret 数据出域`` 这一条是 §2.5/§3.2 的**契约级不变量**（"secret ⇒ 禁外部端点"），
    不是「某位策略作者的偏好」。因此它作为**内置策略**存在，并且在判定顺序上
    **先于**文件策略——否则文件里一条宽泛的 ``allow`` 就能把它静默关掉（那正是
    「首个命中生效」语义最危险的失效模式）。可用 ``include_builtins=False``
    （或 ``CP_POLICY_BUILTIN_INVARIANTS=0``）在测试/演示中关掉。
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from agent.policy.matcher import reject_unsupported
from agent.policy.models import (
    EFFECT_DENY,
    POLICY_SCHEMA,
    Policy,
    PolicyError,
    PolicyValidationError,
    canonical_json,
    sha256_hex,
)
from agent.policy.signing import (
    ENV_REQUIRE_SIGNATURE,
    PolicySigner,
    require_signature,
    self_sign,
    verify_policy_signature,
)

#: 环境变量：策略库文件路径
ENV_POLICY_FILE = "CP_POLICY_FILE"
#: 环境变量：是否装载内置不变量（"0" 关闭）
ENV_BUILTIN_INVARIANTS = "CP_POLICY_BUILTIN_INVARIANTS"

#: 默认策略库（入库跟踪的配置，与 ``data/permission_policies.json`` 同性质）
DEFAULT_POLICY_FILE = "data/policies/policies.json"

#: 内置不变量策略 id 前缀（便于一眼区分「契约级」与「作者级」策略）
BUILTIN_ID_PREFIX = "builtin.invariant."


class PolicyStoreError(PolicyError):
    """策略库操作错误（装载失败 / 版本冲突 / 验签不通过）"""


@dataclass(frozen=True)
class StoreProblem:
    """一条装载/校验问题（``ok=False`` 时供人读，不进决策路径）"""

    policy_id: str
    code: str
    detail: str

    def to_dict(self) -> Dict[str, str]:
        return {"policy_id": self.policy_id, "code": self.code, "detail": self.detail}


# ════════════════════════════════════════════════════════════
#  SemVer
# ════════════════════════════════════════════════════════════


def version_key(version: str) -> Tuple[int, int, int, str]:
    """SemVer → 可排序键（非法版本不抛异常：``Policy.parse`` 已拦，此处兜底最低）

    预发布段按字符串序排在正式版**之后**（``1.0.0-rc1`` > ``1.0.0``），与
    「发布即定稿」的操作直觉一致：正式版一旦发出，预发布版不该再盖过它。
    """
    core = str(version or "").split("+", 1)[0]
    pre = ""
    if "-" in core:
        core, pre = core.split("-", 1)
    parts = core.split(".")
    numbers: List[int] = []
    for part in parts[:3]:
        try:
            numbers.append(int(part))
        except (TypeError, ValueError):
            numbers.append(0)
    while len(numbers) < 3:
        numbers.append(0)
    return (numbers[0], numbers[1], numbers[2], pre)


# ════════════════════════════════════════════════════════════
#  内置不变量
# ════════════════════════════════════════════════════════════


def builtin_policy_dicts() -> List[Dict[str, Any]]:
    """内置不变量策略（原始 dict，便于序列化/文档/单测共用同一份定义）"""
    return [
        {
            "id": BUILTIN_ID_PREFIX + "secret-egress-deny",
            "version": "1.0.0",
            "owner": "system",
            "effect": EFFECT_DENY,
            "match": {
                # §2.5 Router 规则 / §3.2 不变量：data_class=secret 且目标外部 ⇒ 直接拒绝
                "all": [
                    {"field": "capability.trust.data_class", "op": "eq",
                     "value": "secret"},
                    {"field": "target.external", "op": "eq", "value": True},
                ],
            },
            "message_template": "数据分级为 secret 的能力不得经外部端点出域（§2.5 / P7.1-20）",
            "effective_range": None,
            "break_glass_ttl_min": None,
            "signature": "",
        },
    ]


def builtin_policies() -> List[Policy]:
    """内置不变量策略（已解析、已自签指纹）"""
    out: List[Policy] = []
    for raw in builtin_policy_dicts():
        body = dict(raw)
        parsed = Policy.parse(body, source_ref="builtin:agent.policy.store")
        signed = Policy.parse({**body, "signature": self_sign(parsed)},
                              source_ref=parsed.source_ref)
        out.append(signed)
    return out


def _env_flag(name: str, default: str = "1") -> bool:
    return str(os.environ.get(name, default)).strip().lower() in ("1", "true", "yes", "on")


# ════════════════════════════════════════════════════════════
#  PolicyStore
# ════════════════════════════════════════════════════════════


class PolicyStore:
    """版本化策略库（线程安全；写操作会推进 ``revision``）

    ``revision`` 是**决策缓存失效的唯一信号**：任何装载/新增/移除/重载都会 +1，
    引擎把它编进缓存键。这样「策略变更 ⇒ 缓存失效」是结构性成立的，而不是靠
    调用方记得手动清缓存。
    """

    def __init__(
        self,
        path: Optional[str] = None,
        *,
        policies: Optional[Sequence[Any]] = None,
        include_builtins: Optional[bool] = None,
        require_signed: Optional[bool] = None,
        public_key_path: Optional[str] = None,
        autoload: bool = True,
    ) -> None:
        self._path = str(path if path is not None
                         else os.environ.get(ENV_POLICY_FILE) or DEFAULT_POLICY_FILE)
        if include_builtins is None:
            include_builtins = _env_flag(ENV_BUILTIN_INVARIANTS, "1")
        self._include_builtins = bool(include_builtins)
        self._require_signed = bool(require_signature()
                                    if require_signed is None else require_signed)
        #: 验签公钥路径（``None`` ⇒ ``CP_POLICY_PUBLIC_KEY`` 或默认路径）。
        #: 显式注入的意义：让「用哪把公钥验签」成为**组合根的决定**，而不是环境的
        #: 一个巧合——用例与多环境部署都不该靠改环境变量来对齐密钥。
        self._public_key_path = public_key_path

        self._lock = threading.RLock()
        #: id → 版本升序的历史（同版本最多一条）
        self._versions: Dict[str, List[Policy]] = {}
        #: 判定顺序（插入序；内置在前）
        self._order: List[str] = []
        self._revision = 0
        self._problems: List[StoreProblem] = []
        self._loaded_paths: List[str] = []

        if self._include_builtins:
            for policy in builtin_policies():
                self._install(policy, prepend=True)
        if policies:
            for item in policies:
                self.add(item)
        if autoload:
            self.load()

    # ── 装载 ────────────────────────────────────────────────

    @property
    def path(self) -> str:
        return self._path

    @property
    def loaded_paths(self) -> List[str]:
        return list(self._loaded_paths)

    def load(self, path: Optional[str] = None) -> int:
        """从文件装载策略（**追加**语义：不动已装载的，重载前请先 ``clear``）

        Returns:
            实际装载的策略条数（文件不存在 ⇒ 0，不报错——「无策略文件」是合法部署，
            此时引擎恒返回 ``matched=False``，执行点回落既有判定）。
        """
        target = str(path if path is not None else self._path)
        items = self._read_file(target)
        if items is None:
            return 0
        count = 0
        for raw, ref in items:
            try:
                self.add(raw, source_ref=ref)
                count += 1
            except (PolicyValidationError, PolicyStoreError) as exc:
                self._problems.append(StoreProblem(
                    policy_id=str((raw or {}).get("id") or ""),
                    code=getattr(exc, "code", "STORE_REJECT"),
                    detail=str(exc)))
        with self._lock:
            if target not in self._loaded_paths:
                self._loaded_paths.append(target)
        return count

    def reload(self) -> int:
        """清空（保留内置不变量）后重新装载 —— 策略热更新入口"""
        with self._lock:
            self._versions.clear()
            self._order.clear()
            self._problems.clear()
            self._loaded_paths.clear()
            if self._include_builtins:
                for policy in builtin_policies():
                    self._install(policy, prepend=True)
        count = self.load()
        with self._lock:
            self._revision += 1  # 即使文件没变也推进：调用方显式要求重载即视为变更
        return count

    @staticmethod
    def _read_file(path: str) -> Optional[List[Tuple[Dict[str, Any], str]]]:
        """读取策略文件/目录 → ``[(策略 dict, 来源引用)]``；不存在 → ``None``"""
        if not path:
            return None
        if os.path.isdir(path):
            merged: List[Tuple[Dict[str, Any], str]] = []
            for name in sorted(os.listdir(path)):
                if not name.endswith(".json"):
                    continue
                full = os.path.join(path, name)
                try:
                    with open(full, "r", encoding="utf-8") as handle:
                        payload = json.load(handle)
                except json.JSONDecodeError as exc:
                    raise PolicyStoreError(
                        f"策略文件非法 JSON: {full}: {exc}") from exc
                except OSError:
                    continue
                merged.extend(PolicyStore._extract_policies(payload, full))
            return merged
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except json.JSONDecodeError as exc:
            raise PolicyStoreError(f"策略文件非法 JSON: {path}: {exc}") from exc
        except OSError as exc:
            raise PolicyStoreError(f"策略文件不可读: {path}: {exc}") from exc
        return PolicyStore._extract_policies(payload, path)

    @staticmethod
    def _extract_policies(data: Any, ref: str) -> List[Tuple[Dict[str, Any], str]]:
        """从一个 JSON 文档里取出策略列表（兼容三种形态）

        1. ``{"schema": "policy.v1", "policies": [...]}``（规范形态）
        2. ``[...]``（裸数组，便于脚本生成）
        3. 单条策略 dict（``id`` 直接作为顶层键）
        """
        if isinstance(data, list):
            return [(item, f"{ref}") for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            if isinstance(data.get("policies"), list):
                schema = str(data.get("schema") or "")
                if schema and schema != POLICY_SCHEMA:
                    raise PolicyStoreError(
                        f"策略文件 schema 不匹配: {schema!r}（期望 {POLICY_SCHEMA!r}）")
                return [(item, ref) for item in data["policies"]
                        if isinstance(item, dict)]
            if "id" in data:
                return [(data, ref)]
        raise PolicyStoreError(f"策略文件结构无法识别: {ref}")

    # ── 写操作 ──────────────────────────────────────────────

    def add(self, policy: Any, *, source_ref: str = "") -> Policy:
        """新增/追加一个策略版本（同 id 保留历史）"""
        parsed = Policy.parse(policy, source_ref=source_ref)
        self._verify(parsed)
        reject_unsupported(parsed.match, policy_id=parsed.id)
        with self._lock:
            versions = self._versions.setdefault(parsed.id, [])
            for existing in versions:
                if existing.version == parsed.version:
                    if canonical_json(existing.to_dict()) == canonical_json(parsed.to_dict()):
                        return existing  # 幂等：同 id 同版本同内容
                    raise PolicyStoreError(
                        f"{parsed.key()} 已存在且内容不同（同版本不同文本不可追责）；"
                        "请提升 version")
            versions.append(parsed)
            versions.sort(key=lambda p: version_key(p.version))
            if parsed.id not in self._order:
                self._order.append(parsed.id)
            self._revision += 1
        return parsed

    def remove(self, policy_id: str, version: Optional[str] = None) -> int:
        """移除策略（给 ``version`` 只移除该版本；否则移除该 id 全部版本）

        Returns:
            移除的版本数。
        """
        with self._lock:
            versions = self._versions.get(str(policy_id))
            if not versions:
                return 0
            if version is None:
                removed = len(versions)
                self._versions.pop(str(policy_id), None)
                if str(policy_id) in self._order:
                    self._order.remove(str(policy_id))
            else:
                keep = [p for p in versions if p.version != str(version)]
                removed = len(versions) - len(keep)
                if keep:
                    self._versions[str(policy_id)] = keep
                else:
                    self._versions.pop(str(policy_id), None)
                    if str(policy_id) in self._order:
                        self._order.remove(str(policy_id))
            if removed:
                self._revision += 1
            return removed

    def clear(self, *, keep_builtins: bool = True) -> None:
        """清空（默认保留内置不变量）"""
        with self._lock:
            self._versions.clear()
            self._order.clear()
            self._problems.clear()
            if keep_builtins and self._include_builtins:
                self._order = []
                for policy in builtin_policies():
                    self._install(policy, prepend=True)
            self._revision += 1

    def _install(self, policy: Policy, *, prepend: bool = False) -> None:
        """内部装载（不做验签——内置策略已自签；调用方负责校验）"""
        with self._lock:
            versions = self._versions.setdefault(policy.id, [])
            if not any(v.version == policy.version for v in versions):
                versions.append(policy)
                versions.sort(key=lambda p: version_key(p.version))
            if policy.id not in self._order:
                if prepend:
                    self._order.insert(0, policy.id)
                else:
                    self._order.append(policy.id)
            self._revision += 1

    # ── 验签 ────────────────────────────────────────────────

    def _verify(self, policy: Policy) -> None:
        raw = str(policy.signature or "").strip()
        if not raw:
            if self._require_signed:
                raise PolicyStoreError(
                    f"{policy.key()} 未签名，而当前环境要求签名"
                    f"（{ENV_REQUIRE_SIGNATURE}=1）；用 "
                    "`python -m agent.policy.signing_cli --sign <file>` 签名后合入")
            return
        result = verify_policy_signature(policy,
                                         public_key_path=self._public_key_path)
        if not result.ok:
            raise PolicyStoreError(f"{policy.key()} 验签失败: {result.reason}")
        if result.degraded and self._require_signed:
            raise PolicyStoreError(
                f"{policy.key()} 仅 sha256-self 占位签名，而当前环境要求真签名")

    # ── 读操作 ──────────────────────────────────────────────

    @property
    def revision(self) -> int:
        """策略库修订号（决策缓存失效信号）"""
        with self._lock:
            return self._revision

    @property
    def builtins_enabled(self) -> bool:
        """是否装载内置不变量策略（模拟器克隆策略库时需要沿用同一开关）"""
        return self._include_builtins

    def adopt(self, policy: Policy) -> Policy:
        """搬运一条**已生效**的策略到本库（不做验签、不做版本冲突检查）

        用途只有一个：模拟器克隆基线策略库（``build_candidate_engine``）。策略
        来源是另一个已校验的库，重复验签没有意义；但**不要**在正常装载路径用它
        ——那会绕过 ``add()`` 的版本冲突与验签纪律。
        """
        self._install(policy)
        return policy

    @property
    def require_signed(self) -> bool:
        return self._require_signed

    @property
    def problems(self) -> List[StoreProblem]:
        return list(self._problems)

    def ids(self) -> List[str]:
        with self._lock:
            return list(self._order)

    def active(self) -> List[Policy]:
        """当前生效版本的策略，按**判定顺序**（内置在前，其后为文件/插入序）"""
        with self._lock:
            out: List[Policy] = []
            for policy_id in self._order:
                versions = self._versions.get(policy_id) or []
                if versions:
                    out.append(versions[-1])  # 已按 SemVer 升序 ⇒ 末位为最高版本
            return out

    def history(self, policy_id: str) -> List[Policy]:
        with self._lock:
            return list(self._versions.get(str(policy_id)) or [])

    def get(self, policy_id: str, version: Optional[str] = None) -> Optional[Policy]:
        versions = self.history(policy_id)
        if not versions:
            return None
        if version is None:
            return versions[-1]
        for item in versions:
            if item.version == str(version):
                return item
        return None

    def count(self) -> int:
        with self._lock:
            return sum(len(v) for v in self._versions.values())

    def fingerprint(self) -> str:
        """全部生效策略的稳定指纹（供缓存键/报告引用；不含 revision 以免抖动）"""
        material = canonical_json([p.to_dict() for p in self.active()])
        return sha256_hex(material)[:16]

    # ── 诊断 ────────────────────────────────────────────────

    def validate_all(self) -> List[StoreProblem]:
        """全量自检：匹配语法 + 签名状态（**只读**，不改状态）"""
        problems: List[StoreProblem] = []
        for policy in self.active():
            for err in _validate_policy(policy):
                problems.append(StoreProblem(policy.id, "INVALID_MATCH", err))
            raw = str(policy.signature or "").strip()
            if raw:
                result = verify_policy_signature(
                    policy, public_key_path=self._public_key_path)
                if not result.ok:
                    problems.append(StoreProblem(policy.id, "BAD_SIGNATURE",
                                                 result.reason))
            elif self._require_signed:
                problems.append(StoreProblem(policy.id, "MISSING_SIGNATURE",
                                             "未签名（当前环境要求签名）"))
        return problems

    def shadow_report(self) -> List[Dict[str, str]]:
        """遮蔽诊断：**前面的 allow 可能遮蔽后面的 deny**

        「首个命中生效」是任务书指定语义，但它的已知代价是「宽泛 allow 写在前面会
        让后面的 deny 永不生效」。本方法把这种可疑顺序列出来，让策略评审（和模拟器
        报告）能把它当作一条要人确认的发现——它**不**改变判定结果。
        """
        out: List[Dict[str, str]] = []
        active = self.active()
        for index, earlier in enumerate(active):
            if earlier.effect.value != "allow":
                continue
            for later in active[index + 1:]:
                if later.effect.value != "deny":
                    continue
                out.append({
                    "shadowing": earlier.key(),
                    "shadowed": later.key(),
                    "detail": f"allow {earlier.key()} 排在 deny {later.key()} 之前；"
                              "两者 match 若重叠，后者的 deny 不会生效",
                })
        return out

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": POLICY_SCHEMA,
            "revision": self.revision,
            "fingerprint": self.fingerprint(),
            "policies": [p.to_dict() for p in self.active()],
        }

    def sign_all(self, signer: Optional[PolicySigner] = None) -> Dict[str, Any]:
        """返回**已签名**的策略库文档（写盘由调用方/CLI 决定）

        签名顺序即文件顺序；``signature`` 会被重算覆盖（签名是对内容的函数，
        不保留旧签名）。
        """
        signer = signer or PolicySigner()
        signed: List[Dict[str, Any]] = []
        for policy in self.active():
            body = policy.to_dict()
            body.pop("signature", None)
            body["signature"] = signer.sign(Policy.parse(body))
            signed.append(body)
        return {"schema": POLICY_SCHEMA, "policies": signed,
                "signing_scheme": signer.scheme,
                "signing_degraded": signer.degraded}


def _validate_policy(policy: Policy) -> List[str]:
    """匹配语法自检（错误前缀化到策略 id）"""
    from agent.policy.matcher import validate_match
    return validate_match(policy.match, policy_id=policy.id)


# ════════════════════════════════════════════════════════════
#  进程级默认库
# ════════════════════════════════════════════════════════════

_DEFAULT_STORE: Optional[PolicyStore] = None
_DEFAULT_STORE_LOCK = threading.Lock()


def get_policy_store(reload: bool = False) -> PolicyStore:
    """进程级默认策略库（懒加载）；``reload=True`` 丢弃重建（策略热更新入口）"""
    global _DEFAULT_STORE
    if _DEFAULT_STORE is None or reload:
        with _DEFAULT_STORE_LOCK:
            if _DEFAULT_STORE is None or reload:
                _DEFAULT_STORE = PolicyStore()
    return _DEFAULT_STORE


def reset_policy_store() -> None:
    """丢弃进程级默认库（测试隔离用；下一次 get 会重新装载）"""
    global _DEFAULT_STORE
    with _DEFAULT_STORE_LOCK:
        _DEFAULT_STORE = None


__all__ = [
    "ENV_POLICY_FILE", "ENV_BUILTIN_INVARIANTS", "DEFAULT_POLICY_FILE",
    "BUILTIN_ID_PREFIX", "PolicyStoreError", "StoreProblem", "PolicyStore",
    "builtin_policy_dicts", "builtin_policies", "version_key",
    "get_policy_store", "reset_policy_store",
]
