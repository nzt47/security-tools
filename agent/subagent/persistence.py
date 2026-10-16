# -*- coding: utf-8 -*-
"""分身定义的落盘持久化（2026-10-17，P3）。

【解决什么】会话级智能体（SessionMeta.agent）绑定的是**内存态** SubagentContainer：
进程重启后 _subagent_mgr 里空无一物，plugins/chat.py 只能回退母体并提示
「绑定的分身不存在，本次回退母体回答」，用户还得去装配车间重建。而用户经
POST /api/subagent/create **显式创建**且 ttl_seconds == 0（永久档）的分身，本质就是
一份**配置** —— 落盘即可：重启时（以及聊天首次取不到容器时）按定义重建，会话绑定
不再因重启而失效。

【只存什么】只存 SubagentConfig 的字段（不含上下文/记忆/凭据值）。上下文与记忆有各自
的持久化域；secret_refs 只存引用，且创建端点本就不写它。

【边界（有意为之）】
- 只持久化**显式创建**的定义：委派内部 delegate-* / fan-out-* 不落盘（用完即弃）。
- 只持久化 ttl_seconds == 0 的定义：ttl>0 是"临时分身"，重启复活它会违背 TTL 语义。
- destroy 同步删定义，避免"销毁后重启又复活"。
- 全流程 best-effort：落盘/恢复失败只记日志，不阻断创建 / 启动 / 聊天。
- 由调用方（orchestrator 包装层 / lifecycle_manager）按配置门控；本模块只负责读写。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

logger = logging.getLogger(__name__)

#: 仓库根 = 本文件（agent/subagent/persistence.py）的上两级
REPO_ROOT = Path(__file__).resolve().parents[2]

#: 分身定义目录（运行期数据，不入库）
DEFS_DIR = REPO_ROOT / "data" / "subagents"


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _file_for(name: str) -> Path:
    """定义文件路径：安全文件名 + name 的短哈希，避免非法字符/截断碰撞。"""
    digest = hashlib.sha1(str(name).encode("utf-8")).hexdigest()[:10]
    safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in str(name))[:48] or "agent"
    return DEFS_DIR / ("%s-%s.json" % (safe, digest))


def persist_definition(config: Mapping[str, Any]) -> bool:
    """把一份分身配置落盘（原子写）；缺 name 或写失败返回 False（不抛）。"""
    name = str((config or {}).get("name") or "").strip()
    if not name:
        logger.warning("[SubagentDefs] 拒绝持久化：config 缺 name")
        return False
    try:
        DEFS_DIR.mkdir(parents=True, exist_ok=True)
        path = _file_for(name)
        tmp = path.with_name(path.name + ".tmp")
        payload = {"name": name, "saved_at": _now(), "config": dict(config)}
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                       encoding="utf-8")
        os.replace(str(tmp), str(path))
        logger.info("[SubagentDefs] 已持久化分身定义: %s -> %s", name, path.name)
        return True
    except OSError as e:
        logger.warning("[SubagentDefs] 持久化失败 %s: %s", name, e)
        return False


def remove_definition(name: str) -> bool:
    """删除一份定义（destroy 时同步）；不存在也算成功。"""
    try:
        path = _file_for(name)
        if path.exists():
            path.unlink()
            logger.info("[SubagentDefs] 已删除分身定义: %s", name)
        return True
    except OSError as e:
        logger.warning("[SubagentDefs] 删除定义失败 %s: %s", name, e)
        return False


def _read_config(path: Path) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    cfg = data.get("config")
    return cfg if isinstance(cfg, dict) else None


def load_definition(name: str) -> Optional[Dict[str, Any]]:
    """按 name 读回配置字典；没有/损坏返回 None。"""
    return _read_config(_file_for(name))


def load_definitions() -> List[Dict[str, Any]]:
    """读回全部定义（跳过损坏文件）。"""
    if not DEFS_DIR.exists():
        return []
    out: List[Dict[str, Any]] = []
    for path in sorted(DEFS_DIR.glob("*.json")):
        cfg = _read_config(path)
        if cfg:
            out.append(cfg)
    return out


def restore_into(mgr: Any, name: Optional[str] = None) -> List[Any]:
    """把落盘定义恢复成容器：给了 name 只恢复它，否则恢复全部。

    跳过已存在的同名容器与非法定义；单条失败不影响其它。返回本次新建的容器列表。
    """
    from dataclasses import fields as _fields

    from agent.subagent.container import SubagentConfig

    allowed = {f.name for f in _fields(SubagentConfig)}
    configs = [load_definition(name)] if name else load_definitions()
    restored: List[Any] = []
    for cfg in configs:
        if not cfg:
            continue
        nm = str(cfg.get("name") or "").strip()
        if not nm:
            continue
        try:
            if mgr.get(nm) is not None:
                continue
        except Exception:  # noqa: BLE001 查询失败按"不存在"继续尝试
            pass
        try:
            kwargs = {k: v for k, v in cfg.items() if k in allowed}
            restored.append(mgr.create(SubagentConfig(**kwargs)))
        except Exception as e:  # noqa: BLE001 单条恢复失败不阻断其它
            logger.warning("[SubagentDefs] 恢复失败 %s: %s", nm, e)
    if restored:
        logger.info("[SubagentDefs] 已从落盘定义恢复 %d 个分身: %s",
                    len(restored), [getattr(c.config, "name", "?") for c in restored])
    return restored
