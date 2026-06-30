"""
文件比较工具 -- 比较两个文件差异，返回 unified diff 格式

我是云枢的"比较器"——基于 Python difflib 标准库，提供类似 git diff 的文件差异分析能力。
"""
import os
import json
import uuid
import time
import difflib
import logging

logger = logging.getLogger(__name__)


def _trace_id():
    """生成简短 trace_id"""
    return uuid.uuid4().hex[:16]


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
    # 记录起始时间，用于计算 duration_ms（满足可观测性约束）
    _t0 = time.time()
    logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "diff_tools", "action": "diff.files.start", "path1": path1, "path2": path2, "context_lines": context_lines, "duration_ms": int((time.time() - _t0) * 1000)}, ensure_ascii=False))

    # ── 1. 路径安全校验 ──
    from agent.system_tools import safe_resolve_path

    try:
        safe_path1 = safe_resolve_path(path1)
        safe_path2 = safe_resolve_path(path2)
    except ValueError as e:
        logger.warning(json.dumps({"trace_id": _trace_id(), "module_name": "diff_tools", "action": "diff.safety_check.failed", "error": str(e), "duration_ms": int((time.time() - _t0) * 1000)}, ensure_ascii=False))
        return {"ok": False, "error": str(e)}

    # ── 2. 检查文件是否存在 ──
    if not os.path.exists(safe_path1):
        logger.warning(json.dumps({"trace_id": _trace_id(), "module_name": "diff_tools", "action": "diff.file1.not_found", "path": safe_path1, "duration_ms": int((time.time() - _t0) * 1000)}, ensure_ascii=False))
        return {"ok": False, "error": f"文件不存在: {path1}"}
    if not os.path.exists(safe_path2):
        logger.warning(json.dumps({"trace_id": _trace_id(), "module_name": "diff_tools", "action": "diff.file2.not_found", "path": safe_path2, "duration_ms": int((time.time() - _t0) * 1000)}, ensure_ascii=False))
        return {"ok": False, "error": f"文件不存在: {path2}"}

    # ── 3. 确保是文件而非目录 ──
    if not os.path.isfile(safe_path1):
        logger.warning(json.dumps({"trace_id": _trace_id(), "module_name": "diff_tools", "action": "diff.path1.not_a_file", "path": safe_path1, "duration_ms": int((time.time() - _t0) * 1000)}, ensure_ascii=False))
        return {"ok": False, "error": f"路径不是文件: {path1}"}
    if not os.path.isfile(safe_path2):
        logger.warning(json.dumps({"trace_id": _trace_id(), "module_name": "diff_tools", "action": "diff.path2.not_a_file", "path": safe_path2, "duration_ms": int((time.time() - _t0) * 1000)}, ensure_ascii=False))
        return {"ok": False, "error": f"路径不是文件: {path2}"}

    # ── 4. 文件大小检查（10MB 限制） ──
    size1 = os.path.getsize(safe_path1)
    size2 = os.path.getsize(safe_path2)
    logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "diff_tools", "action": "diff.file_sizes", "size1": size1, "size2": size2, "duration_ms": int((time.time() - _t0) * 1000)}, ensure_ascii=False))

    if size1 > MAX_DIFF_FILE_SIZE:
        logger.warning(json.dumps({"trace_id": _trace_id(), "module_name": "diff_tools", "action": "diff.file1.too_large", "size": size1, "duration_ms": int((time.time() - _t0) * 1000)}, ensure_ascii=False))
        return {
            "ok": False,
            "error": f"文件过大 ({size1 / 1024 / 1024:.1f}MB)，超过限制 10MB: {path1}",
            "path1": path1,
            "size1": size1,
        }
    if size2 > MAX_DIFF_FILE_SIZE:
        logger.warning(json.dumps({"trace_id": _trace_id(), "module_name": "diff_tools", "action": "diff.file2.too_large", "size": size2, "duration_ms": int((time.time() - _t0) * 1000)}, ensure_ascii=False))
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
        logger.warning(json.dumps({"trace_id": _trace_id(), "module_name": "diff_tools", "action": "diff.file1.permission_denied", "error": str(e), "duration_ms": int((time.time() - _t0) * 1000)}, ensure_ascii=False))
        return {"ok": False, "error": f"没有权限读取文件: {path1}"}
    except OSError as e:
        logger.warning(json.dumps({"trace_id": _trace_id(), "module_name": "diff_tools", "action": "diff.file1.read_failed", "error": str(e), "duration_ms": int((time.time() - _t0) * 1000)}, ensure_ascii=False))
        return {"ok": False, "error": f"读取文件失败: {e}"}

    try:
        with open(safe_path2, "r", encoding="utf-8", errors="replace") as f:
            lines2 = f.readlines()
    except PermissionError as e:
        logger.warning(json.dumps({"trace_id": _trace_id(), "module_name": "diff_tools", "action": "diff.file2.permission_denied", "error": str(e), "duration_ms": int((time.time() - _t0) * 1000)}, ensure_ascii=False))
        return {"ok": False, "error": f"没有权限读取文件: {path2}"}
    except OSError as e:
        logger.warning(json.dumps({"trace_id": _trace_id(), "module_name": "diff_tools", "action": "diff.file2.read_failed", "error": str(e), "duration_ms": int((time.time() - _t0) * 1000)}, ensure_ascii=False))
        return {"ok": False, "error": f"读取文件失败: {e}"}

    logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "diff_tools", "action": "diff.files.read_success", "lines1": len(lines1), "lines2": len(lines2), "duration_ms": int((time.time() - _t0) * 1000)}, ensure_ascii=False))

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

    logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "diff_tools", "action": "diff.files.complete", "additions": additions, "deletions": deletions, "changes": changes, "duration_ms": int((time.time() - _t0) * 1000)}, ensure_ascii=False))

    return {
        "ok": True,
        "diff": diff_text,
        "path1": path1,
        "path2": path2,
        "changes": changes,
        "additions": additions,
        "deletions": deletions,
    }
