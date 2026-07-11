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
from agent.server_routes.tracing_decorator import trace_route

logger = logging.getLogger(__name__)

ASSETS_DIR = Path("data/assets")
BACKUPS_DIR = Path("data/backups")

FILE_BASED_CATEGORIES = {"habits", "inspires", "hobbies", "interactions"}
ALL_CATEGORIES = ["memory", "prompts", "tools", "skills", "habits", "inspires", "hobbies", "interactions"]


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


def _get_non_file_items(category: str, state) -> list:
    """从其他子系统获取非文件型资产"""
    try:
        if category == "skills" and state.skills_mgr:
            skills = state.skills_mgr.get_all()
            return [
                {
                    "id": s.get("id", ""),
                    "title": s.get("name", ""),
                    "name": s.get("name", ""),
                    "description": s.get("description", ""),
                    "enabled": s.get("enabled", False),
                    "category": "skills",
                }
                for s in skills
            ]
    except Exception as e:
        logger.warning("[AssetsAPI] 获取 %s 数据失败: %s", category, e)
    return []


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
                    items = _read_json_file(ASSETS_DIR / f"{cat}.json")
                    overview[cat] = len(items)
                else:
                    items = _get_non_file_items(cat, state)
                    overview[cat] = len(items)
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
            else:
                items = _get_non_file_items(category, state)
            for i, item in enumerate(items):
                if not item.get("id"):
                    item["id"] = f"{category}_{i}"
                if not item.get("title"):
                    item["title"] = item.get("name") or item.get("title") or item.get("target") or item["id"]
                if not item.get("type"):
                    item["type"] = category
                if not item.get("category"):
                    item["category"] = category
            return jsonify({"ok": True, "items": items, "count": len(items), "category": category})
        except Exception as e:
            logger.error("[AssetsAPI] list %s 失败: %s", category, e)
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/assets/<category>/<item_id>", methods=["DELETE"])
    @trace_route("Assets")
    def api_assets_delete(category, item_id):
        """删除指定资产项"""
        try:
            if category not in FILE_BASED_CATEGORIES:
                return jsonify({"ok": False, "error": f"类别 {category} 不支持删除"}), 400

            filepath = ASSETS_DIR / f"{category}.json"
            items = _read_json_file(filepath)
            before = len(items)
            items = [i for i in items if i.get("id") != item_id and i.get("name") != item_id and i.get("title") != item_id]
            after = len(items)

            if before == after:
                return jsonify({"ok": False, "error": "未找到指定资产项"}), 404

            _write_json_file(filepath, items)
            logger.info("[AssetsAPI] 已删除 %s/%s", category, item_id)
            return jsonify({"ok": True, "message": f"已删除 {category}/{item_id}"})
        except Exception as e:
            logger.error("[AssetsAPI] delete 失败: %s", e)
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/assets/<category>", methods=["POST"])
    @trace_route("Assets")
    def api_assets_add(category):
        """添加资产项到指定类别"""
        try:
            if category not in FILE_BASED_CATEGORIES:
                return jsonify({"ok": False, "error": f"类别 {category} 不支持直接添加"}), 400

            data = request.get_json() or {}
            filepath = ASSETS_DIR / f"{category}.json"
            items = _read_json_file(filepath)

            new_item = {
                "id": f"{category}_{int(time.time() * 1000)}",
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
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
    def api_assets_backup():
        """创建资产备份"""
        try:
            data = request.get_json() or {}
            categories = data.get("categories")

            BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            backup_id = f"assets_backup_{timestamp}"
            backup_data = {}

            cats = categories if categories else FILE_BASED_CATEGORIES
            for cat in cats:
                if cat in FILE_BASED_CATEGORIES:
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
                if cat in FILE_BASED_CATEGORIES:
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
            for cat in FILE_BASED_CATEGORIES:
                export_data[cat] = _read_json_file(ASSETS_DIR / f"{cat}.json")

            export_file = BACKUPS_DIR / f"assets_export_{int(time.time())}.json"
            BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
            export_file.write_text(json.dumps(export_data, ensure_ascii=False, indent=2), encoding="utf-8")
            return send_file(str(export_file), as_attachment=True, download_name=export_file.name)
        except Exception as e:
            logger.error("[AssetsAPI] export 失败: %s", e)
            return jsonify({"ok": False, "error": str(e)}), 500
