"""
文件比较工具 -- 比较两个文件差异，返回 unified diff 格式

我是云枢的"比较器"——基于 Python difflib 标准库，提供类似 git diff 的文件差异分析能力。
"""
import os
import difflib
import logging

logger = logging.getLogger(__name__)

# 文件比较大小限制（10MB）
MAX_DIFF_FILE_SIZE = 10 * 1024 * 1024


def diff_files(path1: str, path2: str, context_lines: int = 3) -> dict:
    """比较两个文件的差异

    使用 Python difflib.unified_diff 生成类似 git diff 的统一格式差异。
    路径会通过 safe_resolve_path 进行安全校验。

    Args:
        path1: 第一个文件路径
        path2: 第二个文件路径
        context_lines: 上下文行数，默认 3

    Returns:
        {"ok": True, "diff": "<unified diff>", "path1": "...", "path2": "...",
         "changes": N, "additions": N, "deletions": N}
        或 {"ok": False, "error": "..."}
    """
    logger.info("[diff_files] 开始文件比较: path1=%s, path2=%s, context_lines=%d",
                path1, path2, context_lines)

    # ── 1. 路径安全校验 ──
    from agent.system_tools import safe_resolve_path

    try:
        safe_path1 = safe_resolve_path(path1)
        safe_path2 = safe_resolve_path(path2)
    except ValueError as e:
        logger.warning("[diff_files] 路径安全校验失败: %s", e)
        return {"ok": False, "error": str(e)}

    # ── 2. 检查文件是否存在 ──
    if not os.path.exists(safe_path1):
        logger.warning("[diff_files] 文件1不存在: %s", safe_path1)
        return {"ok": False, "error": f"文件不存在: {path1}"}
    if not os.path.exists(safe_path2):
        logger.warning("[diff_files] 文件2不存在: %s", safe_path2)
        return {"ok": False, "error": f"文件不存在: {path2}"}

    # ── 3. 确保是文件而非目录 ──
    if not os.path.isfile(safe_path1):
        logger.warning("[diff_files] 路径1不是文件: %s", safe_path1)
        return {"ok": False, "error": f"路径不是文件: {path1}"}
    if not os.path.isfile(safe_path2):
        logger.warning("[diff_files] 路径2不是文件: %s", safe_path2)
        return {"ok": False, "error": f"路径不是文件: {path2}"}

    # ── 4. 文件大小检查（10MB 限制） ──
    size1 = os.path.getsize(safe_path1)
    size2 = os.path.getsize(safe_path2)
    logger.info("[diff_files] 文件大小: path1=%d bytes, path2=%d bytes", size1, size2)

    if size1 > MAX_DIFF_FILE_SIZE:
        logger.warning("[diff_files] 文件1过大: %d bytes", size1)
        return {
            "ok": False,
            "error": f"文件过大 ({size1 / 1024 / 1024:.1f}MB)，超过限制 10MB: {path1}",
            "path1": path1,
            "size1": size1,
        }
    if size2 > MAX_DIFF_FILE_SIZE:
        logger.warning("[diff_files] 文件2过大: %d bytes", size2)
        return {
            "ok": False,
            "error": f"文件过大 ({size2 / 1024 / 1024:.1f}MB)，超过限制 10MB: {path2}",
            "path2": path2,
            "size2": size2,
        }

    # ── 5. 读取文件内容 ──
    try:
        with open(safe_path1, "r", encoding="utf-8", errors="replace") as f:
            lines1 = f.readlines()
    except PermissionError as e:
        logger.warning("[diff_files] 文件1权限不足: %s", e)
        return {"ok": False, "error": f"没有权限读取文件: {path1}"}
    except OSError as e:
        logger.warning("[diff_files] 文件1读取失败: %s", e)
        return {"ok": False, "error": f"读取文件失败: {e}"}

    try:
        with open(safe_path2, "r", encoding="utf-8", errors="replace") as f:
            lines2 = f.readlines()
    except PermissionError as e:
        logger.warning("[diff_files] 文件2权限不足: %s", e)
        return {"ok": False, "error": f"没有权限读取文件: {path2}"}
    except OSError as e:
        logger.warning("[diff_files] 文件2读取失败: %s", e)
        return {"ok": False, "error": f"读取文件失败: {e}"}

    logger.info("[diff_files] 文件读取成功: lines1=%d, lines2=%d", len(lines1), len(lines2))

    # ── 6. 生成 unified diff ──
    diff_lines = list(difflib.unified_diff(
        lines1, lines2,
        fromfile=path1,
        tofile=path2,
        n=context_lines,
    ))

    # ── 7. 统计变更 ──
    additions = 0
    deletions = 0
    for line in diff_lines:
        if line.startswith("+") and not line.startswith("+++"):
            additions += 1
        elif line.startswith("-") and not line.startswith("---"):
            deletions += 1

    diff_text = "".join(diff_lines)
    changes = additions + deletions

    logger.info("[diff_files] 比较完成: additions=%d, deletions=%d, changes=%d",
                additions, deletions, changes)

    return {
        "ok": True,
        "diff": diff_text,
        "path1": path1,
        "path2": path2,
        "changes": changes,
        "additions": additions,
        "deletions": deletions,
    }
