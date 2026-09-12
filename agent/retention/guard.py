"""删除前校验（TASK-S8-01 步骤 2 的闸门）。

【为什么删除必须有一道独立闸门】
    "归档"是可逆的，"删除"不可逆。S8 批次三条不可越界原则里，**审计链永久保留**
    与**默认保守**都只能靠一处机械闸门保证：任何删除动作在真正 `os.remove` 之前，
    必须先过 `PurgeGuard.check()`。护栏不是注释里的约定，而是调用路径上的必经点。

【四条拒绝理由（每条都有独立退出码，便于测试与排障）】
    1. `redline`         —— 红线类（审计链 + 每日 Merkle 根 / 纯审计轨 / 只读归档镜像）
                            **一律拒**：包含"误把审计类当普通类传进来"的情形。
    2. `forgetting_path` —— 记忆类：本模块没有删除权，必须转 S5-01
                            `ForgettingEngine`（先快照 → 再删除 → 盐销毁 → 链校验）。
    3. `not_deletable`   —— 策略表未标"可删"（默认全表只有 `digestion_drafts`）。
    4. `metric_dependency` / `out_of_scope` / `unknown_class` / `no_paths`
                          —— 删了会破坏既有指标口径 / 越界到类之外 / 未知类 / 空清单。
"""

from __future__ import annotations

import logging
import os
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from agent.retention.policy import (
    DELETE_GUARDED,
    DELETE_S5_01_FORGETTING,
    RetentionClass,
    RetentionPolicy,
)
from agent.retention.scan import expand

logger = logging.getLogger("agent.retention.guard")

# ── 拒绝码（稳定字符串：测试与审计都以此为准）──
CODE_OK = "ok"
CODE_UNKNOWN_CLASS = "unknown_class"
CODE_REDLINE = "redline"
CODE_FORGETTING_PATH = "forgetting_path"
CODE_NOT_DELETABLE = "not_deletable"
CODE_METRIC_DEPENDENCY = "metric_dependency"
CODE_OUT_OF_SCOPE = "out_of_scope"
CODE_NO_PATHS = "no_paths"
CODE_NOT_FROZEN = "not_frozen"          # 未归档即删：拒绝（归档可还原是删除的前提）

#: 所有拒绝码 → 人读说明
REJECT_REASONS: Dict[str, str] = {
    CODE_REDLINE: ("红线类：**禁止删除** —— 审计链与每日 Merkle 根（含纯审计轨、"
                   "只读归档镜像）永久保留、只归档不删"),
    CODE_FORGETTING_PATH: ("记忆类：本模块无删除权，必须走 S5-01 "
                           "「删记忆不删证据」（forgetting.ForgettingEngine）"),
    CODE_NOT_DELETABLE: "策略表未标『可删』：默认策略为只归档不删除",
    CODE_METRIC_DEPENDENCY: "有既有指标依赖本类明细才能复算：删除会改变统计口径",
    CODE_OUT_OF_SCOPE: "待删路径不在本类 globs 范围内（越界删除防护）",
    CODE_NO_PATHS: "待删清单为空",
    CODE_UNKNOWN_CLASS: "未知数据类",
    CODE_NOT_FROZEN: "该路径尚无对应归档件（先归档、验签、再删除）",
}

#: S5-01 删除路径的落点（`redirect` 字段直接给调用方）
FORGETTING_ENTRY = "agent.memory.forgetting.ForgettingEngine"


@dataclass
class GuardDecision:
    """护栏判定结果。"""

    allowed: bool = False
    code: str = CODE_OK
    class_id: str = ""
    reasons: List[str] = field(default_factory=list)
    redirect: str = ""
    checked_paths: int = 0
    total_bytes: int = 0
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def summary(self) -> str:
        head = "允许删除" if self.allowed else f"拒绝删除（{self.code}）"
        detail = "；".join(self.reasons) if self.reasons else "—"
        return f"[{self.class_id}] {head}：{detail}"


class PurgeGuard:
    """删除前校验：红线 / 记忆路径 / 可删标记 / 指标依赖 / 越界 / 已归档。"""

    def __init__(self, policy: RetentionPolicy, *, root: str = "",
                 archived_paths: Optional[Sequence[str]] = None) -> None:
        """
        Args:
            policy: 策略表。
            root: 仓库根（外部类用其 `external_root`）。
            archived_paths: 本轮**已成功归档并校验通过**的源文件路径集合；
                非空时启用"未归档即删"检查（`CODE_NOT_FROZEN`）。
        """
        self.policy = policy
        self.root = root
        self.archived = {os.path.normcase(os.path.abspath(p))
                         for p in (archived_paths or [])}

    # ── 主判定 ───────────────────────────────────────────
    def check(self, class_id: str, paths: Optional[Sequence[str]] = None, *,
              require_archived: Optional[bool] = None) -> GuardDecision:
        """判定某类（的某批路径）是否允许被本模块删除。

        Args:
            class_id: 数据类 id。
            paths: 待删的绝对路径清单；None → 取本类**已冷**的全部文件（不自动判定，
                调用方通常显式传入，避免"忘了传就全删"）。
            require_archived: 是否强制"必须先有归档件"。None → 仅当本护栏构造时
                提供了 `archived_paths` 才强制。
        """
        if not self.policy.has(class_id):
            return GuardDecision(False, CODE_UNKNOWN_CLASS, class_id,
                                 [f"未知数据类：{class_id}"])
        cls = self.policy.get(class_id)

        # ① 红线类：无条件拒（含"误把审计类当普通类传进来"）
        if cls.redline:
            return GuardDecision(False, CODE_REDLINE, class_id,
                                 [REJECT_REASONS[CODE_REDLINE],
                                  f"红线原因：{cls.basis or '审计链/每日根永久保留'}"],
                                 note=cls.note)

        # ② 记忆类：本模块无删除权，转 S5-01
        if cls.delete_mode == DELETE_S5_01_FORGETTING:
            return GuardDecision(False, CODE_FORGETTING_PATH, class_id,
                                 [REJECT_REASONS[CODE_FORGETTING_PATH]],
                                 redirect=FORGETTING_ENTRY, note=cls.note)

        # ③ 未标可删 / 删除方式非 guarded
        if not cls.deletable or cls.delete_mode != DELETE_GUARDED:
            return GuardDecision(False, CODE_NOT_DELETABLE, class_id,
                                 [REJECT_REASONS[CODE_NOT_DELETABLE],
                                  f"deletable={cls.deletable} "
                                  f"delete_mode={cls.delete_mode}"])

        # ④ 指标口径依赖：删了历史就复算不出来
        if cls.metric_dependencies:
            return GuardDecision(False, CODE_METRIC_DEPENDENCY, class_id,
                                 [REJECT_REASONS[CODE_METRIC_DEPENDENCY],
                                  "依赖指标：" + "、".join(cls.metric_dependencies)])

        # ⑤ 清单与越界（越界判定用**策略 globs 的展开结果**做白名单，
        #    不用"目录前缀"——仓库根前缀会放过任何文件，等于没有防护）
        targets = [os.path.abspath(p) for p in (paths or [])]
        if not targets:
            if paths is not None:
                return GuardDecision(False, CODE_NO_PATHS, class_id,
                                     [REJECT_REASONS[CODE_NO_PATHS]])
            targets = [os.path.abspath(p) for p in expand(cls, self.root)]
            if not targets:
                return GuardDecision(False, CODE_NO_PATHS, class_id,
                                     ["本类当前无可删文件"])
        allowed_set = {os.path.normcase(os.path.abspath(p))
                       for p in expand(cls, self.root)}
        outside: List[str] = [p for p in targets
                              if os.path.normcase(p) not in allowed_set]
        if outside:
            return GuardDecision(False, CODE_OUT_OF_SCOPE, class_id,
                                 [REJECT_REASONS[CODE_OUT_OF_SCOPE]],
                                 redirect=f"类 {cls.class_id} 的允许范围为 "
                                          f"{list(cls.globs)}",
                                 note="越界示例：" + "、".join(outside[:3]))

        # ⑥ 未归档即删（归档可还原是删除的前提）
        if require_archived or (require_archived is None and self.archived):
            missing = [p for p in targets
                       if os.path.normcase(p) not in self.archived]
            if missing:
                return GuardDecision(False, CODE_NOT_FROZEN, class_id,
                                     [REJECT_REASONS[CODE_NOT_FROZEN]],
                                     note="缺归档件示例：" + "、".join(missing[:3]))

        total = 0
        for path in targets:
            try:
                total += os.path.getsize(path)
            except OSError:
                pass
        return GuardDecision(True, CODE_OK, class_id, [], checked_paths=len(targets),
                             total_bytes=total, note=cls.note)

    # ── 便捷断言 ─────────────────────────────────────────
    def assert_deletable(self, class_id: str,
                         paths: Optional[Sequence[str]] = None) -> GuardDecision:
        """不允许即抛 `PermissionError`（供执行路径使用；绝不静默降级为删除）。"""
        decision = self.check(class_id, paths)
        if not decision.allowed:
            raise PermissionError(f"PurgeGuard 拒绝删除：{decision.summary()}")
        return decision

    def redline_scan(self) -> List[Dict[str, Any]]:
        """对**全部**类跑一遍红线判定（自证：红线类一个都过不去）。"""
        return [self.check(c.class_id).to_dict() for c in self.policy.classes]

    def describe(self) -> Dict[str, Any]:
        """护栏口径自述（供验收报告与 UI 只读展示）。"""
        return {
            "reject_codes": dict(REJECT_REASONS),
            "forgetting_entry": FORGETTING_ENTRY,
            "redline_classes": [c.class_id for c in self.policy.classes if c.redline],
            "deletable_classes": [c.class_id for c in self.policy.classes if c.can_purge],
            "policy": self.policy.summary(),
        }


def assert_no_redline_deletion(policy: RetentionPolicy, class_ids: Sequence[str]) -> None:
    """批量断言：给定类集合里不能出现红线类（调用方入口处的最后一道自检）。"""
    red = [c for c in class_ids
           if policy.has(c) and policy.get(c).redline]
    if red:
        raise PermissionError(f"拒绝：清单含红线审计类 {red}（审计链永久保留）")


__all__ = [
    "CODE_OK", "CODE_UNKNOWN_CLASS", "CODE_REDLINE", "CODE_FORGETTING_PATH",
    "CODE_NOT_DELETABLE", "CODE_METRIC_DEPENDENCY", "CODE_OUT_OF_SCOPE",
    "CODE_NO_PATHS", "CODE_NOT_FROZEN", "REJECT_REASONS", "FORGETTING_ENTRY",
    "GuardDecision", "PurgeGuard", "assert_no_redline_deletion",
]
