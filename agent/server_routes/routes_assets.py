"""资产管理 API 路由

提供8类资产的 CRUD、搜索、备份/恢复/导出功能。
文件型类别（habits/inspires/hobbies/interactions）直接读写 JSON 文件。
非文件型类别从其他子系统获取（skills/内存/提示词/工具）。
"""

import logging
import json
import os
import time
import shutil
from pathlib import Path
from flask import request, jsonify, send_file
from agent.server_auth import require_token
from agent.server_routes.tracing_decorator import trace_route

logger = logging.getLogger(__name__)

ASSETS_DIR = Path("data/assets")
BACKUPS_DIR = Path("data/backups")

# [DET-4] 迭代序必须有定义：:173（备份）与 :265（导出）直接迭代它生成 JSON 的键序
# （HTTP 响应体 + data/backups/*.json 落盘）。set 字面量的迭代序随进程（PYTHONHASHSEED）
# 变 => 同一份备份/导出文件的键序跨进程不同。
# 口径：先声明**有序枚举**（类别的自然声明序），再从它派生成员集合 —— 单一事实来源；
# 其余 6 处 in FILE_BASED_CATEGORIES 的成员判定语义逐位不变（既有测试据此写成集合比较）。
FILE_BASED_CATEGORY_ORDER = ("habits", "inspires", "hobbies", "interactions")
FILE_BASED_CATEGORIES = set(FILE_BASED_CATEGORY_ORDER)
ALL_CATEGORIES = ["memory", "prompts", "tools", "skills", "habits", "inspires", "hobbies", "interactions"]
#: [DET-4] 本地账本类别（全部 8 类）的**有序**枚举：备份/导出直接迭代它生成 JSON 键序
LEDGER_CATEGORY_ORDER = tuple(ALL_CATEGORIES)

#: 「非文件型」= 计数与清单来自**真实子系统**（memory/prompts/tools/skills）；
#: 其余 4 类是本地账本。非文件型仍保留一份**本地补录账本**
#: （data/assets/<cat>.json）用于「添加」——子系统不提供创建/删除入口的类别
#: （skills/tools）也能记账，且真实条目只读、本地条目可删。
NON_FILE_CATEGORY_ORDER = ("memory", "prompts", "tools", "skills")
NON_FILE_CATEGORIES = set(NON_FILE_CATEGORY_ORDER)
#: 记忆数据的来源 = 与「记忆管理」**同一个** MemoryManager（memory_data/messages.jsonl）。
#  经 _assets_state.memory_mgr 注入；**不读** data/memory/agent_memory.json —— 那是迁移脚本
#  （scripts/migrate_to_sqlite_vec.py）的旧源，与运行时记忆无关。2026-10-13 由
#  「资产·记忆数据 与 记忆管理 是否同源」实测暴露（原先读旧源 ⇒ 两处对不上）。
#: 子系统条目上屏上限（记忆实测 1600+ 条，全量上屏没有意义）
SYSTEM_ITEMS_LIMIT = 200


def _read_json_file(filepath: Path) -> list:
    if not filepath.exists():
        return []
    try:
        return json.loads(filepath.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def _write_json_file(filepath: Path, data: list) -> None:
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _map_skill_items(state) -> list:
    """skills 子系统条目（真源：SkillsManager）。"""
    mgr = getattr(state, "skills_mgr", None)
    if not mgr:
        return []
    return [
        {
            "id": s.get("id", ""),
            "title": s.get("name", ""),
            "name": s.get("name", ""),
            "description": s.get("description", ""),
            "enabled": s.get("enabled", False),
            "category": "skills",
        }
        for s in mgr.get_all()
    ]


def _memory_storage(state):
    """「记忆管理」同源的存储对象（MemoryManager._storage）；不可用时返回 None。"""
    mgr = getattr(state, "memory_mgr", None)
    storage = getattr(mgr, "_storage", None) if mgr is not None else None
    return storage if hasattr(storage, "load_recent_messages") else None


def _memory_total(state) -> int:
    """记忆总条数（与「记忆管理」同一份 memory_data/messages.jsonl）。"""
    storage = _memory_storage(state)
    if storage is None:
        return 0
    return len(storage.load_recent_messages(limit=1000000))


def _read_memory_items(state) -> list:
    """记忆数据：与「记忆管理」同源（MemoryManager._storage，只读）。

    条目形状 {role, content, timestamp} ⇒ 映射为资产的 {id,title,description,created_at}。
    """
    storage = _memory_storage(state)
    if storage is None:
        return []
    out = []
    for i, m in enumerate(storage.load_recent_messages(limit=SYSTEM_ITEMS_LIMIT)):
        if not isinstance(m, dict):
            continue
        content = str(m.get("content") or "").strip()
        first = content.splitlines()[0] if content else ""
        ts = str(m.get("timestamp") or "")
        out.append({
            "id": f"memory_{i}_{ts}",
            "title": first[:80] or "（空记忆）",
            "name": first[:80] or "（空记忆）",
            "description": str(m.get("role") or "message"),
            "created_at": ts,
            "category": "memory",
        })
    return out


def _read_prompt_items() -> list:
    """提示词库：PromptStorage（data/prompts/prompts.db，真源，只读）。"""
    from agent.prompt_manager.storage import get_prompt_storage
    out = []
    for rec in get_prompt_storage().list_prompts(limit=100000):
        d = rec.to_dict() if hasattr(rec, "to_dict") else dict(rec)
        out.append({
            "id": str(d.get("prompt_id") or ""),
            "title": str(d.get("name") or d.get("prompt_id") or ""),
            "name": str(d.get("name") or ""),
            "description": str(d.get("prompt_type") or ""),
            "created_at": str(d.get("created_at_iso") or ""),
            "category": "prompts",
        })
    return out


def _read_tool_items() -> list:
    """工具资源：agent.tools.list_tools()（工具定义真源，只读）。"""
    from agent.tools import list_tools
    out = []
    for t in (list_tools() or []):
        if not isinstance(t, dict):
            continue
        out.append({
            "id": str(t.get("name") or ""),
            "title": str(t.get("name") or ""),
            "name": str(t.get("name") or ""),
            "description": str(t.get("description") or ""),
            "created_at": "",
            "category": "tools",
        })
    return out


def _real_items(category: str, state) -> list:
    """非文件型的**真实子系统**条目（只读来源）。

    单项类别取数失败只影响该类别（返回空表），不把整个端点打成 500。
    """
    try:
        if category == "skills":
            items = _map_skill_items(state)
        elif category == "memory":
            items = _read_memory_items(state)
        elif category == "prompts":
            items = _read_prompt_items()
        elif category == "tools":
            items = _read_tool_items()
        else:
            items = []
    except Exception as e:  # noqa: BLE001
        logger.warning("[AssetsAPI] 获取 %s 真实数据失败: %s", category, e)
        items = []
    for it in items:
        it.setdefault("source", "system")
    return items


def _local_items(category: str) -> list:
    """本地补录账本（data/assets/<cat>.json）：非文件型类别里可增删的那部分。"""
    out = []
    for it in _read_json_file(ASSETS_DIR / f"{category}.json"):
        if isinstance(it, dict):
            out.append({**it, "source": it.get("source") or "local"})
    return out


def _get_non_file_items(category: str, state, limit: int = SYSTEM_ITEMS_LIMIT) -> list:
    """非文件型 = 真实子系统条目（只读） + 本地补录账本（可增删）。

    【为什么合并】子系统多数没有「创建」入口（skills/tools 只能改启用态），
    若「添加」一律 400，这三类在资产管理里就是死路。真实条目只读、本地条目可删，
    账本落在 data/assets/<cat>.json，与 4 个文件型类别的落点一致。
    """
    real = _real_items(category, state)
    if limit is not None:
        real = real[:limit]
    return real + _local_items(category)


def _non_file_count(category: str, state) -> int:
    """非文件型真实总数 = 真实子系统条目数 + 本地补录条数（不受上屏上限影响）。

    与 _get_non_file_items 不同，这里**不吞异常**：overview 是「一眼看全」的门面，
    统计失败应当 500 而不是静默显示 0（否则坏了也看不出来）。
    """
    if category == "skills":
        n = len(_map_skill_items(state))
    elif category == "memory":
        n = _memory_total(state)
    elif category == "prompts":
        from agent.prompt_manager.storage import get_prompt_storage
        n = len(get_prompt_storage().list_prompts(limit=100000))
    elif category == "tools":
        from agent.tools import list_tools
        n = len(list_tools() or [])
    else:
        n = 0
    return n + len(_read_json_file(ASSETS_DIR / f"{category}.json"))


def register_routes(app, state):
    """注册所有资产管理路由"""

    @app.route("/api/assets/overview")
    @trace_route("Assets")
    def api_assets_overview():
        """获取8类资产的概览（各类数量）"""
        try:
            overview = {}
            for cat in ALL_CATEGORIES:
                if cat in FILE_BASED_CATEGORIES:
                    overview[cat] = len(_read_json_file(ASSETS_DIR / f"{cat}.json"))
                else:
                    # 真实子系统总数（不受上屏上限影响）+ 本地补录条数
                    overview[cat] = _non_file_count(cat, state)
            overview["total"] = sum(overview.values())
            return jsonify({"ok": True, "overview": overview})
        except Exception as e:
            logger.error("[AssetsAPI] overview 失败: %s", e)
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/assets/<category>")
    @trace_route("Assets")
    def api_assets_list(category):
        """获取指定类别的资产列表"""
        try:
            if category in FILE_BASED_CATEGORIES:
                items = _read_json_file(ASSETS_DIR / f"{category}.json")
                total = len(items)
            else:
                items = _get_non_file_items(category, state)
                total = _non_file_count(category, state)
            for i, item in enumerate(items):
                if not item.get("id"):
                    item["id"] = f"{category}_{i}"
                if not item.get("title"):
                    item["title"] = item.get("name") or item.get("title") or item.get("target") or item["id"]
                if not item.get("type"):
                    item["type"] = category
                if not item.get("category"):
                    item["category"] = category
            # total = 真实总数（非文件型可能大于上屏条数 count）
            return jsonify({"ok": True, "items": items, "count": len(items),
                            "total": total, "category": category})
        except Exception as e:
            logger.error("[AssetsAPI] list %s 失败: %s", category, e)
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/assets/<category>/<item_id>", methods=["DELETE"])
    @trace_route("Assets")
    @require_token
    def api_assets_delete(category, item_id):
        """删除指定资产项"""
        try:
            if category not in ALL_CATEGORIES:
                return jsonify({"ok": False, "error": f"未知类别 {category}"}), 400

            filepath = ASSETS_DIR / f"{category}.json"
            items = _read_json_file(filepath)
            before = len(items)
            items = [i for i in items if i.get("id") != item_id and i.get("name") != item_id and i.get("title") != item_id]
            after = len(items)

            if before == after:
                # 真实子系统条目不在本地账本里 ⇒ 只能去对应子系统删除
                return jsonify({"ok": False, "error": "本地账本中未找到该条目（来自子系统的只读条目请到对应页面处理）"}), 404

            _write_json_file(filepath, items)
            logger.info("[AssetsAPI] 已删除 %s/%s", category, item_id)
            return jsonify({"ok": True, "message": f"已删除 {category}/{item_id}"})
        except Exception as e:
            logger.error("[AssetsAPI] delete 失败: %s", e)
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/assets/<category>", methods=["POST"])
    @trace_route("Assets")
    @require_token
    def api_assets_add(category):
        """添加资产项到指定类别"""
        try:
            if category not in ALL_CATEGORIES:
                return jsonify({"ok": False, "error": f"未知类别 {category}"}), 400

            data = request.get_json() or {}
            filepath = ASSETS_DIR / f"{category}.json"
            items = _read_json_file(filepath)

            new_item = {
                "id": f"{category}_{int(time.time() * 1000)}",
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                # 非文件型：本地补录条目如实标注来源（真实子系统条目 source=system、只读）
                "source": "local",
                **data,
            }
            items.append(new_item)
            _write_json_file(filepath, items)
            logger.info("[AssetsAPI] 已添加 %s: %s", category, new_item.get("name") or new_item.get("title"))
            return jsonify({"ok": True, "item": new_item})
        except Exception as e:
            logger.error("[AssetsAPI] add 失败: %s", e)
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/assets/backup", methods=["POST"])
    @trace_route("Assets")
    @require_token
    def api_assets_backup():
        """创建资产备份"""
        try:
            data = request.get_json() or {}
            categories = data.get("categories")

            BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            backup_id = f"assets_backup_{timestamp}"
            backup_data = {}

            # [DET-4] 兜底默认必须是**有序**枚举（见文件头的声明）
            cats = categories if categories else LEDGER_CATEGORY_ORDER
            for cat in cats:
                if cat in ALL_CATEGORIES:
                    backup_data[cat] = _read_json_file(ASSETS_DIR / f"{cat}.json")

            backup_file = BACKUPS_DIR / f"{backup_id}.json"
            backup_file.write_text(json.dumps(backup_data, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info("[AssetsAPI] 备份创建成功: %s (%d 类)", backup_id, len(backup_data))
            return jsonify({"ok": True, "backup_id": backup_id, "message": f"备份 {backup_id} 创建成功"})
        except Exception as e:
            logger.error("[AssetsAPI] backup 失败: %s", e)
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/assets/backup/list")
    @trace_route("Assets")
    def api_assets_backup_list():
        """获取备份列表"""
        try:
            BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
            backups = []
            for f in sorted(BACKUPS_DIR.glob("assets_backup_*.json"), reverse=True):
                stat = f.stat()
                backup_data = _read_json_file(f)
                cat_list = list(backup_data.keys()) if isinstance(backup_data, dict) else []
                file_count = sum(len(v) for v in backup_data.values()) if isinstance(backup_data, dict) else 0
                backups.append({
                    "backup_id": f.stem,
                    "created_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
                    "categories": cat_list,
                    "file_count": file_count,
                    "size_bytes": stat.st_size,
                })
            return jsonify({"ok": True, "backups": backups, "count": len(backups)})
        except Exception as e:
            logger.error("[AssetsAPI] backup list 失败: %s", e)
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/assets/backup/<backup_id>", methods=["DELETE"])
    @trace_route("Assets")
    @require_token
    def api_assets_backup_delete(backup_id):
        """删除指定备份"""
        try:
            backup_file = BACKUPS_DIR / f"{backup_id}.json"
            if not backup_file.exists():
                return jsonify({"ok": False, "error": "备份不存在"}), 404
            backup_file.unlink()
            logger.info("[AssetsAPI] 备份已删除: %s", backup_id)
            return jsonify({"ok": True, "message": f"备份 {backup_id} 已删除"})
        except Exception as e:
            logger.error("[AssetsAPI] backup delete 失败: %s", e)
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/assets/restore", methods=["POST"])
    @trace_route("Assets")
    @require_token
    def api_assets_restore():
        """从备份恢复资产数据"""
        try:
            data = request.get_json() or {}
            backup_id = data.get("backup_id")
            if not backup_id:
                return jsonify({"ok": False, "error": "缺少 backup_id"}), 400

            backup_file = BACKUPS_DIR / f"{backup_id}.json"
            if not backup_file.exists():
                return jsonify({"ok": False, "error": "备份不存在"}), 404

            backup_data = json.loads(backup_file.read_text(encoding="utf-8"))
            restored = []
            for cat, items in backup_data.items():
                if cat in ALL_CATEGORIES:
                    _write_json_file(ASSETS_DIR / f"{cat}.json", items)
                    restored.append(cat)

            logger.info("[AssetsAPI] 从备份 %s 恢复 %d 类资产", backup_id, len(restored))
            return jsonify({
                "ok": True,
                "result": {
                    "backup_id": backup_id,
                    "restored_files": restored,
                    "restored_count": len(restored),
                },
                "message": f"从备份 {backup_id} 恢复了 {len(restored)} 类资产",
            })
        except Exception as e:
            logger.error("[AssetsAPI] restore 失败: %s", e)
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/assets/export")
    @trace_route("Assets")
    def api_assets_export():
        """导出所有资产为 JSON 文件"""
        try:
            export_data = {}
            for cat in LEDGER_CATEGORY_ORDER:   # [DET-4] 导出键序 = 声明序
                export_data[cat] = _read_json_file(ASSETS_DIR / f"{cat}.json")

            export_file = BACKUPS_DIR / f"assets_export_{int(time.time())}.json"
            BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
            export_file.write_text(json.dumps(export_data, ensure_ascii=False, indent=2), encoding="utf-8")
            return send_file(str(export_file), as_attachment=True, download_name=export_file.name)
        except Exception as e:
            logger.error("[AssetsAPI] export 失败: %s", e)
            return jsonify({"ok": False, "error": str(e)}), 500
