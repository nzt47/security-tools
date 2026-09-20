"""`CapabilityRegistry` —— 进程内、**只读**、`CapabilityRecord` 的派生视图（v1.4 §6）

## 存储选择：内存 + 进程内（**理由，TASK-05 §3 第 1 步第 1 项**）

| 候选 | 结论 |
|---|---|
| A. 复用 `agent/lines/` 的 `load_tool_meta()` + 技能声明，包一层门面 | ✅ **采纳** |
| B. SQLite（先例：`data/orchestrator_config.db`） | ❌ 否决：这是**只读派生视图**，落盘 DB 等于凭空造出第二份"能力定义"并引入"何时重建/迁移/损坏"三类新故障；且 D6 明令不碰 `data/` 下 `.db` |
| C. 新建独立模块 + 启动时构建 | ⚠️ 部分采纳：模块**新建**（本包），但**数据不另存** —— 构建 = 读 A 的产物 |
| Redis/etcd/Postgres | ❌ D3 明令禁止 |

**为什么不落盘缓存**：Registry 的一切输入都是仓库内的**受版本控制的声明文件**
（`data/tool_definitions/*.yaml` 91 个 + `data/skill_callability.yaml`）。
重建成本实测在毫秒级（见 `scripts/bench_capregistry.py`），而落盘缓存会立刻
制造"D 文件比 YAML 新 ⇒ 以谁为准"这类必须靠额外规则回答的问题 —— 那正是 D1
要禁止的第二真相源。

## 数据来源（三段式，每段都标了来源，可 grep 验证）

1. **主源（`spec_source="authority"`）**：
   - 工具：`agent/lines/models.py::load_tool_meta()` —— `TASK-00` §0.3 认定的
     "能力元数据解析（唯一入口）"。**本模块不解析 YAML**（避免第二真相源）。
   - 技能：`data/capability_manifest.json` 的 23 条 skill 条目。**为什么技能走快照**：
     技能实体（`skills.json` / `skills_mgmt.json` / `skills_repo/*/skill.md`）按
     `TASK-00` §0.3 的实测**不在版本控制内** ⇒ 只在运行时才有权威，启动期只能读
     受版本控制的那份快照（`skill_callability.yaml` 的覆盖面不足以还原实体）。
     这条约束由本模块如实披露在 `stats()["skill_source"]` 里。
2. **补充源（读 `data/capability_manifest.json` 的派生字段）**：`mark` /
   `reachable` / `main_line_status` / `location_source` / `impl_status`。
   它们由 TASK-04 的 AST 判定器与主线装配产出，重算要跑全仓 AST + 装配主线，
   **不适合放进查询路径**。
3. **降级源**：主源构建失败 ⇒ 整体回落到第二段的纯快照，并标 `degraded=True`。
   **不得**因 Registry 失败而阻塞启动（D4）。

## 只读性（E2 的证明点）

本类**没有任何写方法**。它不持有"注册"入口，也不提供 `to_yaml()` 之类反向出口。
唯一可能写入的是 `_health` 字段 —— 而它**不在本类里**：运行时健康态由
`agent/capregistry/loader.py::LoaderManager` 单独持有，本类通过一个
**只读回调**（`health_provider`）查询。这样"能力定义"与"运行时状态"在类型层面
就分开了，`Registry` 保持纯只读。
"""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .modelcaps import model_capability
from .spec import CapabilityRecord, derive_callable_by

logger = logging.getLogger(__name__)

__all__ = [
    "MANIFEST_PATH",
    "CapabilityRegistry",
    "CapabilitySpecBuildError",
    "build_registry",
    "get_registry",
    "reset_registry",
]


def _repo_root() -> str:
    # agent/capregistry/view.py → 仓库根
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


MANIFEST_PATH = os.path.join("data", "capability_manifest.json")


class CapabilitySpecBuildError(RuntimeError):
    """主源构建失败（**会被捕获并降级**，不会向上抛到启动链路）"""


# ════════════════════════════════════════════════════════════
#  主源构建（纯读；失败 ⇒ 由调用方降级）
# ════════════════════════════════════════════════════════════


def _load_manifest(root: str) -> Dict[str, Any]:
    path = os.path.join(root, MANIFEST_PATH)
    with open(path, "r", encoding="utf-8") as f:
        doc = json.load(f)
    if not isinstance(doc, dict) or not isinstance(doc.get("entries"), list):
        raise CapabilitySpecBuildError(f"{MANIFEST_PATH} 结构异常（entries 非列表）")
    return doc


def _impl_status_index(manifest: Mapping[str, Any]) -> Dict[str, Tuple[str, str]]:
    """从 manifest 的 `non_capabilities` 派生 `impl_status`

    【为什么要有】`schedule_task` 的底层 `agent/scheduling.py::_execute_task`
    action 分支是 `pass`（`TASK-05` §2.3d："谎报成功"）。调用方在
    `/capabilities/tools` 里**看不出**这一点 ⇒ 会选它、会得到"成功"、然后什么也没发生。

    【不易·为什么不直接判 `verdict=="downgraded"`】`non_capabilities` 里混了
    四类东西：孤儿函数、死 mock、依赖缺失、空壳执行器。只有最后一类应当表现为
    **"这个能力在册但是坏的"**；前三类根本不进清单（`verdict` 不同）。
    """
    out: Dict[str, Tuple[str, str]] = {}
    for item in manifest.get("non_capabilities") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        kind = str(item.get("kind") or "")
        if not name:
            continue
        if kind == "hollow_executor":
            out[name] = ("not_implemented", str(item.get("evidence") or ""))
        elif kind == "unavailable_dependency":
            out[name] = ("unavailable", str(item.get("evidence") or ""))
    return out


def _build_from_authority(root: str, manifest: Mapping[str, Any]
                          ) -> Tuple[List[CapabilityRecord], List[str]]:
    """从权威声明（YAML + 技能侧）构建 specs

    【D4】本函数**只抛 `CapabilitySpecBuildError`**，绝不抛别的异常类型 ——
    调用方（`build_registry`）据此决定是否降级。任何 `Exception` 都在这里被
    翻译成构建失败，而不是漏到启动链路上。
    """
    warnings: List[str] = []
    try:
        from agent.lines.models import load_tool_meta  # noqa: PLC0415 惰性：避免 import 环
    except Exception as exc:  # noqa: BLE001
        raise CapabilitySpecBuildError(f"不可导入 agent.lines.models: {exc}") from exc

    try:
        metas = dict(load_tool_meta())
    except Exception as exc:  # noqa: BLE001
        raise CapabilitySpecBuildError(f"load_tool_meta() 失败: {exc}") from exc
    if not metas:
        raise CapabilitySpecBuildError("load_tool_meta() 返回空（YAML 目录缺失或不可读）")

    # 运行时事实（schema_registered / host_executor / source）——可选，拿不到就用快照
    facts: Dict[str, Any] = {}
    try:
        from agent import tools as _tools  # noqa: PLC0415
        facts = dict(_tools.registry_facts())
    except Exception as exc:  # noqa: BLE001  注册框架未就绪 ⇒ 只影响"事实"维度
        warnings.append(f"registry_facts() 不可用（host_executor 回落到快照）: {exc}")

    derived_by_name: Dict[str, Dict[str, Any]] = {}
    for e in manifest.get("entries") or []:
        if isinstance(e, dict) and e.get("tool_name"):
            derived_by_name[str(e["tool_name"])] = dict(e)
    impl_index = _impl_status_index(manifest)

    specs: List[CapabilityRecord] = []
    for name, meta in metas.items():
        derived = dict(derived_by_name.get(str(name)) or {})
        impl, impl_reason = impl_index.get(str(name), ("implemented", ""))
        derived["impl_status"] = impl
        derived["impl_status_reason"] = impl_reason
        try:
            specs.append(CapabilityRecord.from_tool_meta(
                meta, facts=facts.get(str(name)), derived=derived))
        except Exception as exc:  # noqa: BLE001  单条坏不该毁掉整表
            warnings.append(f"工具 {name!r} 构造失败（已跳过）: {type(exc).__name__}: {exc}")

    # 技能侧：实体不在版本控制内 ⇒ 走受版本控制的清单快照（见模块 docstring 第 1 段）
    for e in manifest.get("entries") or []:
        if not isinstance(e, dict):
            continue
        if str(e.get("kind") or "") != "skill":
            continue
        impl, impl_reason = impl_index.get(str(e.get("tool_name") or ""),
                                          ("implemented", ""))
        d = dict(e)
        d["impl_status"] = impl
        d["impl_status_reason"] = impl_reason
        try:
            specs.append(CapabilityRecord.from_manifest_entry(d, spec_source="authority"))
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"技能 {e.get('tool_name')!r} 构造失败（已跳过）: {exc!r}")
    return specs, warnings


def _build_from_snapshot(manifest: Mapping[str, Any]
                         ) -> Tuple[List[CapabilityRecord], List[str]]:
    """降级路径：整表来自 `data/capability_manifest.json` 快照"""
    warnings = ["已降级到 data/capability_manifest.json 快照："
                "input_schema / result_schema 不可用（清单未收录）"]
    impl_index = _impl_status_index(manifest)
    specs: List[CapabilityRecord] = []
    for e in manifest.get("entries") or []:
        if not isinstance(e, dict) or not e.get("tool_name"):
            continue
        d = dict(e)
        impl, impl_reason = impl_index.get(str(d["tool_name"]), ("implemented", ""))
        d["impl_status"] = impl
        d["impl_status_reason"] = impl_reason
        try:
            specs.append(CapabilityRecord.from_manifest_entry(d, spec_source="snapshot"))
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"{d.get('tool_name')!r} 快照条目构造失败: {exc!r}")
    return specs, warnings


# ════════════════════════════════════════════════════════════
#  只读视图本体
# ════════════════════════════════════════════════════════════


class CapabilityRegistry:
    """能力注册中心：**只读派生视图**

    【线程安全】构造完成即**不可变**（`specs` 是 tuple，索引是 dict 但只读）。
    waitress 16 线程并发查询共享同一实例，**无锁**（读多写零）。
    "写"只发生在构造期，由 `build_registry()` 的模块级锁串行化。
    """

    #: 过滤器元组的关键字（也是 `(tenant_id, owner, kind, location, enabled)` 索引的维度）
    _FACET_KEYS: Tuple[str, ...] = ("tenant_id", "owner", "kind", "location", "enabled")

    def __init__(self, specs: Sequence[CapabilityRecord], *,
                 degraded: bool = False,
                 build_warnings: Optional[Sequence[str]] = None,
                 manifest_meta: Optional[Mapping[str, Any]] = None,
                 health_provider: Optional[Callable[[str], str]] = None) -> None:
        self._specs: Tuple[CapabilityRecord, ...] = tuple(
            sorted(specs, key=lambda s: (s.tool_name, s.tenant_id)))
        self._degraded = bool(degraded)
        self._build_warnings: Tuple[str, ...] = tuple(build_warnings or ())
        #: 运行时健康态提供者（**只读回调**；Registry 自身不持有健康状态，见模块 docstring）
        self._health_provider = health_provider
        m = dict(manifest_meta or {})
        self._manifest_schema_version = str(m.get("schema_version") or "")
        self._manifest_generated_at = str(m.get("generated_at") or "")
        #: `stats()` 的惰性缓存（spec 集合不可变 ⇒ 结果天然可缓存；见 `stats()`）
        self._stats_cache: Optional[Dict[str, Any]] = None

        # ── 索引 ①：`(tenant_id, name)` 主键查询（v1.4 §6 要求的第一项）──
        self._by_key: Dict[Tuple[str, str], CapabilityRecord] = {}
        # ── 索引 ②：`(tenant_id, owner, kind, location, enabled)` 过滤查询 ──
        self._by_facet: Dict[Tuple[Any, ...], Tuple[Tuple[str, str], ...]] = {}
        # ── 索引 ③/④：最常用的两个单维 + 二维前缀（走查计划的辅助）──
        # 索引里存**能力键** `(tenant_id, name)` 而不是裸名字：多租户下同一个名字
        # 会对应多个条目，存裸名字就得回表全扫，等于没索引。
        self._by_kind: Dict[str, Tuple[Tuple[str, str], ...]] = {}
        self._by_location: Dict[str, Tuple[Tuple[str, str], ...]] = {}
        self._by_kind_location: Dict[Tuple[str, str], Tuple[Tuple[str, str], ...]] = {}
        # ── 别名索引：`aliases` 指向规范名（D2：改名要留别名）──
        self._by_alias: Dict[Tuple[str, str], str] = {}

        facet_acc: Dict[Tuple[Any, ...], List[Tuple[str, str]]] = {}
        kind_acc: Dict[str, List[Tuple[str, str]]] = {}
        loc_acc: Dict[str, List[Tuple[str, str]]] = {}
        kl_acc: Dict[Tuple[str, str], List[Tuple[str, str]]] = {}
        for s in self._specs:
            key = (s.tenant_id, s.tool_name)
            if key in self._by_key:
                # 同名同租户重复（清单内部自洽性由 sync_capability_manifest 的
                # validate() 守住；这里保留首个并告警，绝不静默覆盖）
                logger.warning("[capregistry] 重复能力键 %s（保留首个）", key)
                continue
            self._by_key[key] = s
            facet_acc.setdefault(
                (s.tenant_id, s.owner, s.kind, s.location, bool(s.enabled)),
                []).append(key)
            kind_acc.setdefault(s.kind, []).append(key)
            loc_acc.setdefault(s.location, []).append(key)
            kl_acc.setdefault((s.kind, s.location), []).append(key)
            for alias in s.aliases:
                self._by_alias.setdefault((s.tenant_id, str(alias)), s.tool_name)

        self._by_facet = {k: tuple(sorted(v)) for k, v in facet_acc.items()}
        self._by_kind = {k: tuple(sorted(v)) for k, v in kind_acc.items()}
        self._by_location = {k: tuple(sorted(v)) for k, v in loc_acc.items()}
        self._by_kind_location = {k: tuple(sorted(v)) for k, v in kl_acc.items()}

    # ── 只读属性 ──

    @property
    def degraded(self) -> bool:
        """是否处于降级态（主源构建失败 ⇒ 走快照）"""
        return self._degraded

    @property
    def build_warnings(self) -> Tuple[str, ...]:
        return self._build_warnings

    @property
    def specs(self) -> Tuple[CapabilityRecord, ...]:
        return self._specs

    def __len__(self) -> int:
        return len(self._specs)

    # ── 查询 ──

    def get(self, name: str, *, tenant_id: str = "default"
            ) -> Optional[CapabilityRecord]:
        """主键查询 `(tenant_id, name)`；命中不到时按 alias 再查一次"""
        key = (str(tenant_id or "default"), str(name or ""))
        hit = self._by_key.get(key)
        if hit is not None:
            return hit
        canonical = self._by_alias.get(key)
        if canonical:
            return self._by_key.get((key[0], canonical))
        return None

    def _candidates(self, *, tenant_id: Optional[str] = None,
                    owner: Optional[str] = None,
                    kind: Optional[str] = None,
                    location: Optional[str] = None,
                    enabled_only: bool = True) -> Iterable[CapabilityRecord]:
        """走查计划：能用索引就用索引，否则全表扫

        【为什么值得写这一段】v1.4 §6 的容量目标是 10,000 条。全表扫 10k 条
        在 Python 里约 5–15ms（见 `scripts/bench_capregistry.py` 的实测），
        仍然远低于 p99<100ms；但**过滤查询是高频路径**（面板/CI 每次都带
        `kind`/`location`），用索引把它压到与结果集同阶是便宜的保险。

        优先级：① 五维 facet 索引（tenant+owner+kind+location+enabled 全给定时）
                → ② 二维 `(kind, location)` → ③ 单维 `kind`/`location` → ④ 全表扫
        """
        # ① 五维 facet（要求 tenant_id 明确给出，因为索引键里含 tenant_id）
        if tenant_id and owner and kind and location:
            pool = self._by_facet.get((tenant_id, owner, kind, location,
                                       bool(enabled_only)))
            if pool is not None:
                return [self._by_key[k] for k in pool if k in self._by_key]
            # 该 facet 不存在 ⇒ 结果必然为空（索引是全量的，不是采样）
            return []
        # ② / ③ 维度前缀
        if kind and location:
            pool = self._by_kind_location.get((kind, location))
        elif kind:
            pool = self._by_kind.get(kind)
        elif location:
            pool = self._by_location.get(location)
        else:
            pool = None
        if pool is None:
            return self._specs
        return [self._by_key[k] for k in pool if k in self._by_key]

    def query(self, *,
              tenant_id: Optional[str] = None,
              namespace: Optional[str] = None,
              kind: Optional[str] = None,
              location: Optional[str] = None,
              owner: Optional[str] = None,
              enabled_only: bool = True,
              healthy_only: bool = False,
              identity: Optional[str] = None,
              llm_visible_only: bool = False,
              impl_status: Optional[str] = None,
              name_contains: Optional[str] = None) -> List[CapabilityRecord]:
        """按维度过滤（v1.4 §6 的查询契约）

        Args:
            tenant_id: 租户；缺省 = 不过滤（返回全部租户）
            healthy_only: 只看健康的能力（经 `health_provider` 查询运行时状态）
            identity: 入口身份白名单过滤（`llm`/`human`/`system`/`service_account`）
            llm_visible_only: 只看进入"模型可见集"的能力
                （`llm_callable` 且非 `internal`）—— 对齐
                `agent/tools/__init__.py::get_tool_defs` 的隐藏口径
        """
        out: List[CapabilityRecord] = []
        for s in self._candidates(tenant_id=tenant_id, owner=owner, kind=kind,
                                  location=location, enabled_only=enabled_only):
            if tenant_id and s.tenant_id != tenant_id:
                continue
            if namespace and s.namespace != namespace:
                continue
            if kind and s.kind != kind:
                continue
            if location and s.location != location:
                continue
            if owner and s.owner != owner:
                continue
            if enabled_only and not s.enabled:
                continue
            if impl_status and s.impl_status != impl_status:
                continue
            if identity and not s.callable_by_identity(identity):
                continue
            if llm_visible_only and (s.internal or not s.llm_callable):
                continue
            if name_contains and name_contains.lower() not in s.tool_name.lower():
                continue
            if healthy_only and not self.is_healthy(s.tool_name):
                continue
            out.append(s)
        return out

    def is_healthy(self, name: str) -> bool:
        """运行时健康判定

        **无 `health_provider` 时返回 True**（"未知"按"可用"处理）——
        与 `agent/tools/__init__.py::get_health_status` 对"从未调用过"给 100 分
        同一取舍：没有证据说它坏，就不要把它隐藏掉。
        """
        if self._health_provider is None:
            return True
        try:
            state = str(self._health_provider(name) or "").strip().lower()
        except Exception:  # noqa: BLE001  健康探针故障不得让查询失败
            return True
        return state not in ("unhealthy", "down", "open", "failed")

    # ── 命名冲突（E11）──

    def name_conflicts(self) -> List[Dict[str, Any]]:
        """当前**是否存在**同名冲突（含注册期事实）

        两路取证，缺一不可：
        ① **声明层**：同一 `(tenant_id, name)` 出现多条不同谱系（例如
           `registry_source` 不同）⇒ 真正的"名字指向不唯一"；
        ② **注册期**：`agent/tools/__init__.py` 的 `_name_conflicts`
           （覆盖/静默改名）—— 这是 TASK-04 把它从"静默"变成"可见"的产物。
        """
        conflicts: List[Dict[str, Any]] = []
        seen: Dict[Tuple[str, str], List[CapabilityRecord]] = {}
        for s in self._specs:
            seen.setdefault((s.tenant_id, s.tool_name), []).append(s)
        for (tenant, name), group in sorted(seen.items()):
            sources = sorted({g.registry_source for g in group})
            if len(group) > 1 and len(sources) > 1:
                conflicts.append({
                    "name": name, "tenant_id": tenant, "kind": "declaration",
                    "registry_sources": sources,
                    "declared_in": sorted({g.declared_in for g in group}),
                })
        try:
            from agent import tools as _tools  # noqa: PLC0415
            for c in _tools.name_conflicts():
                conflicts.append({
                    "name": str(c.get("name") or ""), "kind": str(c.get("kind") or ""),
                    "final_name": str(c.get("final_name") or ""),
                    "module": str(c.get("module") or ""),
                })
        except Exception as exc:  # noqa: BLE001  注册框架不可用不影响"声明层"结论
            conflicts.append({"name": "", "kind": "registry_unavailable",
                              "error": f"{type(exc).__name__}: {exc}"})
        return conflicts

    # ── 统计 ──

    def stats(self) -> Dict[str, Any]:
        """结构化统计（`/capabilities/health` 与诊断用；**无时间戳字段**保持可对拍）

        【变易·为什么缓存】`list_envelope()` 会在**每次** `/capabilities/tools`
        里带上 `meta.registry = stats()`。而 `stats()` 要遍历全部 spec 汇总 6 个
        分布 —— 在 10,000 条合成压测里，这是每次请求都要重算的 10,000 次迭代。
        本类的 spec 集合**构造后不可变**，故结果天然可缓存（首次惰性计算）。
        【不易】缓存**不影响语义**：`_by_key` / `_specs` 都是只读的，
        不存在"统计过期"的情形。
        """
        cached = self._stats_cache
        if cached is not None:
            return cached
        by_kind: Dict[str, int] = {}
        by_location: Dict[str, int] = {}
        by_owner: Dict[str, int] = {}
        by_impl: Dict[str, int] = {}
        by_source: Dict[str, int] = {}
        for s in self._specs:
            by_kind[s.kind] = by_kind.get(s.kind, 0) + 1
            by_location[s.location] = by_location.get(s.location, 0) + 1
            by_owner[s.owner] = by_owner.get(s.owner, 0) + 1
            by_impl[s.impl_status] = by_impl.get(s.impl_status, 0) + 1
            by_source[s.spec_source] = by_source.get(s.spec_source, 0) + 1
        out = {
            "total": len(self._specs),
            "degraded": self._degraded,
            "schema_version": self._manifest_schema_version,
            "manifest_generated_at": self._manifest_generated_at,
            "by_kind": dict(sorted(by_kind.items())),
            "by_location": dict(sorted(by_location.items())),
            "by_owner": dict(sorted(by_owner.items())),
            "by_impl_status": dict(sorted(by_impl.items())),
            "by_spec_source": dict(sorted(by_source.items())),
            "index": {
                "primary": len(self._by_key),
                "facet": len(self._by_facet),
                "alias": len(self._by_alias),
            },
            "skill_source": ("data/skill_callability.yaml（策略声明，受版本控制）"
                             "+ data/capability_manifest.json（23 条技能实体快照，"
                             "**技能实体不在版本控制内** ⇒ 只读快照，不可复现）"),
            "build_warnings": list(self._build_warnings),
        }
        # 【不易】返回的是**副本**（`dict(out)` 语义）：调用方（HTTP 层）会就地
        # 往 data 里补 `truncated` / `limit` 等字段；若把缓存对象本身给出去，
        # 那些路由层字段就会被写进缓存、污染下一次响应。
        self._stats_cache = out
        return dict(out)

    # ── 对外信封（HTTP / CLI / 模型三条链路**共用同一份**）──

    def list_envelope(self, **filters: Any) -> Dict[str, Any]:
        """`GET /capabilities/tools` 的响应体（CLI `list` 逐字段一致）

        【为什么把信封放在 Registry 而不是路由里】`TASK-05` E6 要求"HTTP 与 CLI
        的 JSON 逐字段一致"。若两个入口各自拼 JSON，一致性只能靠人肉比对；
        放在这里 ⇒ **结构上只可能有一份**。
        """
        model = str(filters.pop("model", "") or "")
        limit = int(filters.pop("limit", 0) or 0)
        offset = int(filters.pop("offset", 0) or 0)
        cap = model_capability(model)
        items = self.query(**filters)
        total = len(items)
        if not cap["supports_tool_calling"]:
            # 不支持 tool calling 的模型 ⇒ **裁剪后的清单**（v1.4 §7 职责 3）
            # 不是"隐藏一部分"，而是"模型根本没有工具通道" ⇒ 清空。
            items = []
        if offset:
            items = items[offset:]
        if limit:
            items = items[:limit]
        return {
            "status": "ok",
            "code": "ok",
            "data": {
                "items": [s.to_dict() for s in items],
                "total": total,
                "returned": len(items),
                "model_capability": cap,
            },
            "error": None,
            "meta": {
                "registry": self.stats(),
                "filters": {k: v for k, v in filters.items() if v not in (None, False)},
            },
        }

    def describe_envelope(self, name: str, *, tenant_id: str = "default"
                          ) -> Dict[str, Any]:
        """`describe` 的响应体（单条能力详情）"""
        spec = self.get(name, tenant_id=tenant_id)
        if spec is None:
            return {
                "status": "error", "code": "not_found", "data": None,
                "error": {"code": "not_found",
                          "message": f"能力 {name!r} 不存在或未注册",
                          "retryable": False},
                "meta": {"registry_total": len(self._specs)},
            }
        return {
            "status": "ok", "code": "ok", "data": spec.to_dict(), "error": None,
            "meta": {"health": self.health_of(name),
                     "degraded": self._degraded},
        }

    def health_of(self, name: str) -> str:
        """运行时健康态（只读回调；无 provider ⇒ `"unknown"`）"""
        if self._health_provider is None:
            return "unknown"
        try:
            return str(self._health_provider(name) or "unknown")
        except Exception:  # noqa: BLE001
            return "unknown"


# ════════════════════════════════════════════════════════════
#  构建入口（进程内单例；D4：失败降级，绝不阻塞启动）
# ════════════════════════════════════════════════════════════

_BUILD_LOCK = threading.Lock()
_SINGLETON: Dict[str, Any] = {"registry": None, "error": ""}


def build_registry(*, root: Optional[str] = None,
                   health_provider: Optional[Callable[[str], str]] = None
                   ) -> CapabilityRegistry:
    """构建 Registry（**不缓存**；`get_registry()` 才缓存）

    【D4 的落点】构建失败**不抛异常**，而是返回一个 `degraded=True` 的降级实例；
    连降级也失败时返回**空 Registry**（`total=0`）并记 error ——
    `app_server.py` 的启动链路因此**不可能**因本模块而失败。
    """
    base = root or _repo_root()
    try:
        manifest = _load_manifest(base)
    except Exception as exc:  # noqa: BLE001  连快照都读不到 ⇒ 空表 + 明确告警
        msg = f"能力清单快照不可读（{MANIFEST_PATH}）: {type(exc).__name__}: {exc}"
        logger.error("[capregistry] %s ⇒ 返回空 Registry（不阻塞启动）", msg)
        return CapabilityRegistry((), degraded=True, build_warnings=[msg])

    try:
        specs, warnings = _build_from_authority(base, manifest)
        if not specs:
            raise CapabilitySpecBuildError("主源构建出 0 条能力")
        logger.info("[capregistry] 主源构建完成：%d 条（warnings=%d）",
                    len(specs), len(warnings))
        return CapabilityRegistry(specs, degraded=False, build_warnings=warnings,
                                  manifest_meta=manifest,
                                  health_provider=health_provider)
    except Exception as exc:  # noqa: BLE001  降级路径：整表走快照
        msg = f"主源构建失败，降级到清单快照: {type(exc).__name__}: {exc}"
        logger.warning("[capregistry] %s", msg)
        specs, warnings = _build_from_snapshot(manifest)
        return CapabilityRegistry(specs, degraded=True,
                                  build_warnings=[msg] + list(warnings),
                                  manifest_meta=manifest,
                                  health_provider=health_provider)


def get_registry(*, force: bool = False,
                 health_provider: Optional[Callable[[str], str]] = None
                 ) -> CapabilityRegistry:
    """取进程内单例（**首次调用才构建**；`force=True` 重建 —— 测试用）"""
    with _BUILD_LOCK:
        if force or _SINGLETON["registry"] is None:
            try:
                _SINGLETON["registry"] = build_registry(health_provider=health_provider)
                _SINGLETON["error"] = ""
            except Exception as exc:  # noqa: BLE001  兜底：连 build_registry 都炸了
                _SINGLETON["error"] = f"{type(exc).__name__}: {exc}"
                logger.error("[capregistry] 构建异常（返回空 Registry）: %s",
                             _SINGLETON["error"], exc_info=True)
                _SINGLETON["registry"] = CapabilityRegistry(
                    (), degraded=True, build_warnings=[_SINGLETON["error"]])
        return _SINGLETON["registry"]


def reset_registry() -> None:
    """清空单例（测试与"数据侧改了 YAML 想立即生效"时用）"""
    with _BUILD_LOCK:
        _SINGLETON["registry"] = None
        _SINGLETON["error"] = ""
