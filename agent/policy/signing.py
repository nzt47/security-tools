"""策略签名（§3.11 ``signature: "ed25519:..."``）

【口径来源】
    §3.11 只给了 ``signature`` 的**字面形态**（``"ed25519:..."``），没说密钥从哪来。
    云枢裁定：**沿用 S2-02 审计链 ``RootsSigner`` 的既有口径**（``agent/audit/chain.py``
    §"签名优先 ed25519（cryptography 可用且有/可生成密钥）；无密钥或库缺失时降级为
    sha256 自签占位"），理由有三：

      1. 同一套降级语义，运维只需要理解一次「什么情况下签名字段是占位」；
      2. 审计链的 Merkle 根与策略库的签名在**同一条信任链**上（策略变更要能追溯到
         人，审计根要能证明记录未改），两套方案会让验签口径分裂；
      3. 单机部署下强制 ed25519 会让「没配密钥环境」的策略库完全无法装载，而策略
         装载失败会退化成「无策略」——那是**安全降级**，不是安全增强。因此：
         **密钥缺失 ⇒ 降级为 sha256-self 占位并显式标注 ``degraded=True``**，由
         ``PolicyStore(require_signature=True)`` 决定是否接受降级签名。

【签名材料】
    ``Policy.signing_payload()``：除 ``signature`` 外的全部 §3.11 字段的 canonical
    JSON。因此「改一个字符 ⇒ 验签失败」；而 ``source_ref`` 这类装载期派生字段不参与，
    同一策略文件换个路径不会被判为篡改。

【这是不是安全边界】
    不是。它防的是「策略文件在合入流程之外被静默改写」与「抄错/漏改版本号」，
    属于**完整性**控制。真正的执行边界在 egress guard / 权限网关（P7.1-20 的
    「决策与执行分离」）。不要把它当作防恶意者的一方——能改文件的人通常也能改密钥，
    所以 ``require_signature`` 只对**受管环境的密钥不落盘**场景有意义。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, List, Optional

from agent.policy.models import (
    SIGN_SCHEME_ED25519,
    SIGN_SCHEME_SHA256_SELF,
    Policy,
    PolicyValidationError,
    sha256_hex,
)

#: 环境变量：私钥路径（PKCS8 PEM）
ENV_SIGNING_KEY = "CP_POLICY_SIGNING_KEY"
#: 环境变量：公钥路径（SubjectPublicKeyInfo PEM）
ENV_PUBLIC_KEY = "CP_POLICY_PUBLIC_KEY"
#: 环境变量：是否强制要求签名（"1" 才强制）
ENV_REQUIRE_SIGNATURE = "CP_POLICY_REQUIRE_SIGNATURE"

#: 默认密钥目录（运行时产物，已入 .gitignore）
DEFAULT_KEY_DIR = "data/policies"
DEFAULT_PRIVATE_KEY_PATH = "data/policies/policy_signing_key.pem"
DEFAULT_PUBLIC_KEY_PATH = "data/policies/policy_signing_key.pub.pem"

#: sha256-self 降级占位的域分隔盐（防止与其它模块的 sha256 自签互串）
_SELF_SALT = "cloudpivot.policy.v1.sha256-self"


class PolicySignatureError(PolicyValidationError):
    """签名相关错误（验签失败 / 强制签名但缺失 / 密钥不可用）"""


@dataclass(frozen=True)
class SignatureResult:
    """一次验签的结果

    Attributes:
        ok: 是否通过。
        scheme: ``ed25519`` / ``sha256-self`` / ``""``（无签名）。
        degraded: 是否走了 sha256-self 降级占位（**非真签名**）。
        reason: 人类可读说明。
    """

    ok: bool
    scheme: str = ""
    degraded: bool = False
    reason: str = ""

    def to_dict(self) -> dict:
        return {"ok": self.ok, "scheme": self.scheme,
                "degraded": self.degraded, "reason": self.reason}


def _env_flag(name: str, default: str = "0") -> bool:
    return str(os.environ.get(name, default)).strip().lower() in ("1", "true", "yes", "on")


def require_signature() -> bool:
    """当前环境是否强制要求策略签名（``CP_POLICY_REQUIRE_SIGNATURE=1``）"""
    return _env_flag(ENV_REQUIRE_SIGNATURE, "0")


def _self_signature(payload: str) -> str:
    return f"{SIGN_SCHEME_SHA256_SELF}:{sha256_hex(_SELF_SALT + '|' + payload)}"


def self_sign(policy: Any) -> str:
    """给「代码内置不变量策略」计算自签占位签名

    内置策略没有文件、没有合入流程，但仍需要一个**内容指纹**：它让
    ``PolicyDecision.policy_version`` 之外还能校验「运行期装载的策略文本与代码
    里写的一致」（防止热改 ``match`` 造成静默放宽）。
    """
    target = Policy.parse(policy) if isinstance(policy, dict) else policy
    return _self_signature(target.signing_payload())


# ════════════════════════════════════════════════════════════
#  签名方
# ════════════════════════════════════════════════════════════


class PolicySigner:
    """策略签名方（ed25519 优先，缺失降级 sha256-self）

    与 ``agent.audit.chain.RootsSigner`` 同款降级策略，但**独立实现**：两者密钥
    生命周期不同（审计根按日签、策略签名按合入签），共用会引入不必要的耦合。
    """

    def __init__(
        self,
        private_key_path: Optional[str] = None,
        public_key_path: Optional[str] = None,
        *,
        auto_generate: bool = True,
    ) -> None:
        self._private_key_path = str(
            private_key_path or os.environ.get(ENV_SIGNING_KEY)
            or DEFAULT_PRIVATE_KEY_PATH)
        self._public_key_path = str(
            public_key_path or os.environ.get(ENV_PUBLIC_KEY)
            or DEFAULT_PUBLIC_KEY_PATH)
        self._private: Any = None
        self._public_pem: str = ""
        self._degraded_reason: str = ""
        self._scheme: str = SIGN_SCHEME_SHA256_SELF
        self._load(auto_generate=auto_generate)

    # ── 密钥装载 ──

    def _load(self, *, auto_generate: bool) -> None:
        private_pem = self._read_key(self._private_key_path)
        if private_pem is None and auto_generate:
            private_pem = self._generate_and_store()
        if private_pem is None:
            self._degraded_reason = f"无私钥（{self._private_key_path} 不存在且未生成）"
            return
        try:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric import ed25519
        except Exception as exc:  # noqa: BLE001 无 cryptography → 降级（非致命）
            self._degraded_reason = f"cryptography 不可用: {exc}"
            return
        try:
            self._private = serialization.load_pem_private_key(
                private_pem.encode("utf-8"), password=None)
        except Exception as exc:  # noqa: BLE001 密钥格式错 → 降级
            self._degraded_reason = f"私钥不可解析: {type(exc).__name__}"
            return
        if not isinstance(self._private, ed25519.Ed25519PrivateKey):
            self._degraded_reason = f"私钥类型非 ed25519: {type(self._private).__name__}"
            self._private = None
            return
        try:
            self._public_pem = self._private.public_key().public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            ).decode("utf-8")
        except Exception as exc:  # noqa: BLE001
            self._degraded_reason = f"公钥导出失败: {type(exc).__name__}"
            self._private = None
            return
        self._scheme = SIGN_SCHEME_ED25519
        self._write_public_key()

    @staticmethod
    def _read_key(path: str) -> Optional[str]:
        try:
            if not path or not os.path.exists(path):
                return None
            with open(path, "r", encoding="utf-8") as handle:
                return handle.read()
        except OSError:
            return None

    def _generate_and_store(self) -> Optional[str]:
        """首次使用生成 ed25519 密钥对（与审计链 ``_ensure_key`` 同款行为）"""
        try:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric import ed25519
        except Exception:  # noqa: BLE001
            return None
        try:
            key = ed25519.Ed25519PrivateKey.generate()
            pem = key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            ).decode("utf-8")
            directory = os.path.dirname(self._private_key_path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            with open(self._private_key_path, "w", encoding="utf-8") as handle:
                handle.write(pem)
            try:  # 尽力收紧权限（Windows 上不生效，忽略）
                os.chmod(self._private_key_path, 0o600)
            except OSError:
                pass
            return pem
        except Exception:  # noqa: BLE001 生成/落盘失败 → 降级，不阻断
            return None

    def _write_public_key(self) -> None:
        if not self._public_pem:
            return
        try:
            directory = os.path.dirname(self._public_key_path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            with open(self._public_key_path, "w", encoding="utf-8") as handle:
                handle.write(self._public_pem)
        except OSError:
            pass

    # ── 属性 ──

    @property
    def scheme(self) -> str:
        return self._scheme

    @property
    def degraded(self) -> bool:
        return self._scheme != SIGN_SCHEME_ED25519

    @property
    def degraded_reason(self) -> str:
        return self._degraded_reason

    @property
    def public_key_pem(self) -> str:
        return self._public_pem

    # ── 签名 / 验签 ──

    def sign(self, policy: Any) -> str:
        """为政策签名；返回 ``"<scheme>:<hex>"``"""
        target = Policy.parse(policy) if isinstance(policy, dict) else policy
        payload = target.signing_payload()
        if self._private is None:
            return _self_signature(payload)
        try:
            from cryptography.hazmat.primitives import serialization  # noqa: F401
            signature = self._private.sign(payload.encode("utf-8"))
        except Exception:  # noqa: BLE001 签名失败 → 降级为自签占位（不阻断合入）
            return _self_signature(payload)
        return f"{SIGN_SCHEME_ED25519}:{signature.hex()}"

    def sign_dict(self, policy_dict: dict) -> dict:
        """返回**填好 signature 的副本**（不修改入参）"""
        candidate = dict(policy_dict or {})
        candidate.pop("signature", None)
        parsed = Policy.parse(candidate)
        candidate["signature"] = self.sign(parsed)
        return candidate


def verify_policy_signature(
    policy: Any,
    signature: Optional[str] = None,
    *,
    public_key_pem: Optional[str] = None,
    public_key_path: Optional[str] = None,
) -> SignatureResult:
    """校验策略签名

    Args:
        policy: :class:`~agent.policy.models.Policy` 或策略 dict。
        signature: 覆盖策略自带的 ``signature``（回填场景用）。
        public_key_pem: 直接给公钥 PEM（优先）。
        public_key_path: 公钥文件路径；缺省读 ``CP_POLICY_PUBLIC_KEY`` 或默认路径。

    Returns:
        :class:`SignatureResult`。**不抛异常**——验签失败是数据事实，由 store 决定处置。
    """
    try:
        target = Policy.parse(policy) if isinstance(policy, dict) else policy
        payload = target.signing_payload()
    except PolicyValidationError as exc:
        return SignatureResult(ok=False, reason=f"策略非法，无法验签: {exc.errors}")

    raw = str(signature if signature is not None else target.signature or "").strip()
    if not raw:
        return SignatureResult(ok=False, reason="策略未签名（signature 为空）")

    scheme, _, digest = raw.partition(":")
    scheme = scheme.strip().lower()
    digest = digest.strip()
    if not digest:
        return SignatureResult(ok=False, scheme=scheme, reason="signature 形态非法（缺摘要）")

    if scheme == SIGN_SCHEME_SHA256_SELF:
        expected = _self_signature(payload).partition(":")[2]
        ok = digest == expected
        return SignatureResult(
            ok=ok, scheme=scheme, degraded=True,
            reason="sha256-self 一致性校验通过（占位，非真签名）" if ok
            else "sha256-self 校验失败（策略内容与签名不一致）")

    if scheme != SIGN_SCHEME_ED25519:
        return SignatureResult(ok=False, scheme=scheme,
                               reason=f"未知签名方案: {scheme!r}")

    pem = public_key_pem or ""
    if not pem:
        path = public_key_path or os.environ.get(ENV_PUBLIC_KEY) or DEFAULT_PUBLIC_KEY_PATH
        try:
            if path and os.path.exists(path):
                with open(path, "r", encoding="utf-8") as handle:
                    pem = handle.read()
        except OSError:
            pem = ""
    if not pem:
        return SignatureResult(ok=False, scheme=scheme,
                               reason="无公钥可用（设置 CP_POLICY_PUBLIC_KEY 或提供 public_key_pem）")
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ed25519
        from cryptography.exceptions import InvalidSignature
    except Exception as exc:  # noqa: BLE001
        return SignatureResult(ok=False, scheme=scheme,
                               reason=f"cryptography 不可用，无法验 ed25519: {exc}")
    try:
        public_key = serialization.load_pem_public_key(pem.encode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        return SignatureResult(ok=False, scheme=scheme,
                               reason=f"公钥不可解析: {type(exc).__name__}")
    if not isinstance(public_key, ed25519.Ed25519PublicKey):
        return SignatureResult(ok=False, scheme=scheme,
                               reason=f"公钥类型非 ed25519: {type(public_key).__name__}")
    try:
        public_key.verify(bytes.fromhex(digest), payload.encode("utf-8"))
    except InvalidSignature:
        return SignatureResult(ok=False, scheme=scheme, reason="ed25519 验签失败")
    except ValueError as exc:
        return SignatureResult(ok=False, scheme=scheme,
                               reason=f"签名摘要非 hex: {exc}")
    return SignatureResult(ok=True, scheme=scheme, reason="ed25519 验签通过")


def audit_signature_state(policies: Any) -> List[str]:
    """批量验签；返回**失败原因清单**（空＝全部通过或无需验签）

    供 ``PolicyStore`` 装载期与合入门禁共用，保证「装载」与「门禁」同一口径。
    """
    problems: List[str] = []
    for policy in policies or ():
        result = verify_policy_signature(policy)
        if result.ok:
            continue
        if not str(policy.signature or "").strip() and not require_signature():
            continue  # 未签名且不强制 ⇒ 不作为问题
        problems.append(f"{policy.key()}: {result.reason}")
    return problems


__all__ = [
    "ENV_SIGNING_KEY", "ENV_PUBLIC_KEY", "ENV_REQUIRE_SIGNATURE",
    "DEFAULT_KEY_DIR", "DEFAULT_PRIVATE_KEY_PATH", "DEFAULT_PUBLIC_KEY_PATH",
    "PolicySignatureError", "SignatureResult", "PolicySigner",
    "verify_policy_signature", "require_signature", "audit_signature_state",
    "self_sign",
]
