"""工作区管理工具——从 system_tools.py 拆出

包含：工作区初始化、列表、写入、删除等操作。
"""
import os
import shutil
import logging

logger = logging.getLogger(__name__)

WORKSPACE_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "workspace")


def init_workspace():
    """初始化受保护的工作区目录"""
    os.makedirs(WORKSPACE_DIR, exist_ok=True)
    # 创建 .gitkeep
    gitkeep = os.path.join(WORKSPACE_DIR, ".gitkeep")
    if not os.path.exists(gitkeep):
        with open(gitkeep, "w") as f:
            f.write("# 云枢受保护工作区\n")
    # 创建 readme
    readme = os.path.join(WORKSPACE_DIR, "README.txt")
    if not os.path.exists(readme):
        with open(readme, "w", encoding="utf-8") as f:
            f.write("云枢受保护工作区\n此目录内的文件操作受安全策略约束。\n")
    logger.info(f"工作区已初始化: {WORKSPACE_DIR}")
    return WORKSPACE_DIR


def list_workspace(path=""):
    """列出工作区内容"""
    full_path = os.path.normpath(os.path.join(WORKSPACE_DIR, path))
    if not full_path.startswith(os.path.normpath(WORKSPACE_DIR)):
        raise ValueError("路径超出工作区范围")
    if not os.path.exists(full_path):
        return {"path": path, "items": [], "error": "路径不存在"}
    if os.path.isfile(full_path):
        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read(5000)
        return {"path": path, "type": "file", "size": os.path.getsize(full_path), "content": content}
    items = []
    for name in os.listdir(full_path):
        item_path = os.path.join(full_path, name)
        items.append({
            "name": name,
            "type": "dir" if os.path.isdir(item_path) else "file",
            "size": os.path.getsize(item_path) if os.path.isfile(item_path) else 0,
        })
    return {"path": path, "type": "dir", "items": sorted(items, key=lambda x: (x["type"], x["name"]))}


def write_workspace(path, content):
    """写入工作区文件"""
    full_path = os.path.normpath(os.path.join(WORKSPACE_DIR, path))
    if not full_path.startswith(os.path.normpath(WORKSPACE_DIR)):
        raise ValueError("路径超出工作区范围")
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    with open(full_path, "w", encoding="utf-8") as f:
        f.write(content)
    return {"ok": True, "path": path, "size": len(content)}


def delete_workspace(path):
    """删除工作区文件/目录"""
    full_path = os.path.normpath(os.path.join(WORKSPACE_DIR, path))
    if not full_path.startswith(os.path.normpath(WORKSPACE_DIR)):
        raise ValueError("路径超出工作区范围")
    if path in ("", ".", "/"):
        raise ValueError("不能删除工作区根目录")
    if os.path.isdir(full_path):
        shutil.rmtree(full_path)
    else:
        os.remove(full_path)
    return {"ok": True, "path": path}


__all__ = [
    "WORKSPACE_DIR", "init_workspace", "list_workspace",
    "write_workspace", "delete_workspace",
]
