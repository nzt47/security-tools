"""TASK-S3-01 测试共用工具：合成统一台账 / 隔离的 registry / 事件目录

与 `tests/unit/descriptors_util.py` 同风格——只做构造，不做断言。
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

BASE_TS = 1_760_000_000.0


def build_ledger(
    facade: Any,
    *,
    capability_id: str,
    tasks: int = 24,
    fail_every: int = 7,
    workspace_id: str = "ws_digest",
    prefix_step: Optional[str] = "list_dir",
    duplicate_first: bool = True,
    trailing: Sequence[str] = ("shell_execute", "write_file"),
    args_key: str = "path",
    extra_param: bool = True,
) -> Dict[str, Any]:
    """合成一条"同类轨迹"台账：每个任务 = 任务级 Trace + 若干能力级子 Trace

    刻意注入两类噪声（任务书步骤 2 的清洗对象）：
    - ``prefix_step``（探索前缀，默认 ``list_dir``）；
    - ``duplicate_first``（首个能力的**相邻重复**调用，模拟重试）。

    所有 ``started_at`` 显式递增 —— 依赖时钟会让等值时间戳下的步骤顺序不确定
    （实现期实测到的真实缺陷）。
    """
    fail_ids: List[str] = []
    for i in range(tasks):
        t0 = BASE_TS + i * 10.0
        task_id = f"task{i:04d}"
        facade.start(task_id=task_id, workspace_id=workspace_id)
        cursor = t0
        if prefix_step:
            facade.record(prefix_step, args={args_key: f"C:/repo/proj{i}"},
                          output={"ok": True}, started_at=cursor, duration_ms=5.0)
            cursor += 1.0
        first_args = {args_key: f"C:/repo/proj{i}/src/mod{i}.py"}
        if extra_param:
            first_args["encoding"] = "utf-8"
        facade.record(capability_id, args=dict(first_args),
                      output={"ok": True, "size": 100 + i},
                      started_at=cursor, duration_ms=3.0)
        cursor += 1.0
        if duplicate_first:
            facade.record(capability_id, args=dict(first_args),
                          output={"ok": True, "size": 100 + i},
                          started_at=cursor, duration_ms=3.0)
            cursor += 1.0
        failing = fail_every and (i % fail_every == fail_every - 1)
        for j, label in enumerate(trailing):
            # 失败轨迹缺少末步（成功/失败骨架差异 ⇒ 决策树分支可提取）
            if failing and j == len(trailing) - 1:
                continue
            facade.record(label, args={"cmd": f"run{i}_{j}"},
                          output={"ok": True}, started_at=cursor, duration_ms=1.0)
            cursor += 1.0
        facade.finish(status="error" if failing else "success")
        if failing:
            fail_ids.append(task_id)
    return {"tasks": tasks, "fail_task_ids": fail_ids,
            "success_tasks": tasks - len(fail_ids)}


def touch_all(facade: Any) -> None:
    """等待/触发唯一台账 writer 落盘"""
    facade.flush()


def descriptor_registry(tmp_path: Any, names: Sequence[str]):
    """隔离的 descriptor 台账 + 已登记的内置工具"""
    from agent.descriptors.bridge import register_bridge_view
    from agent.descriptors.registry import DescriptorRegistry

    reg = DescriptorRegistry(path=str(tmp_path / "descriptors.json"),
                            autosave=False)
    register_bridge_view(reg, "builtin",
                         [{"name": n, "description": f"{n} 描述"} for n in names])
    return reg


def event_dir(tmp_path: Any) -> str:
    return str(tmp_path / "events")


__all__ = ["BASE_TS", "build_ledger", "descriptor_registry", "event_dir"]
