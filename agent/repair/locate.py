"""步骤 2 · 定位器：把上下文打包成可委派的工单（TASK-S7-02）

【任务定位】
    任务书 §三 步骤 2 要求为每个失败项聚合四类证据并生成**源码切片**（失败点 ±N 行的
    最小上下文，「避免整仓塞进提示词」），再组装成 §3.9 八要素任务包。本模块负责
    证据聚合与切片；八要素的组装与准入在 ``delegate``。

【不易（缺证据要如实说，不许补白）】
    没有 Trace、没有 descriptor、git 不可用 —— 这些都**不是**错误，而是需要被
    披露的证据缺口。本模块把它们逐条写进 ``RepairTicket.evidence_gaps``：定位器
    的诚实度直接决定子代理拿到的工单是不是「编造出来的上下文」。

【不易（切片必须带行号且可核对）】
    切片文本每一行前缀真实行号（``   12| code``）。理由：子代理产出的补丁是
    **按行号定位**的（unified diff 的 hunk 头依赖行号）；若切片不带行号，子代理
    只能凭内容猜位置，补丁必然错位。

【变易】
    实现文件的反推走「测试文件 import 扫描」这一条：它对本项目（测试与被测模块
    同名同构）足够，且**不依赖任何运行时状态**。若将来需要更强的映射（如覆盖率
    数据），只需替换 ``infer_impl_files()`` 一处。
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from agent.repair import gitio
from agent.repair.diagnose import clean_text, recent_changes
from agent.repair.models import (
    DiagnosisReport,
    FailureItem,
    RepairTicket,
    SourceSlice,
)
from agent.repair.policy import RepairPolicy, is_readonly_path
from agent.repair.trace import RepairRunLogger, short_hash

logger = logging.getLogger("agent.repair.locate")

#: 单个切片的最大字符数（防超大文件把提示词撑爆）
MAX_SLICE_CHARS = 6000
#: 一次工单最多包含的切片数
MAX_SLICES = 6
#: 一次工单最多反推的实现文件数
MAX_IMPL_FILES = 4

#: `from x.y import z` / `import x.y as z` 的模块名捕获
_FROM_IMPORT_RE = re.compile(r"^\s*from\s+([A-Za-z_][\w.]*)\s+import\b", re.MULTILINE)
_PLAIN_IMPORT_RE = re.compile(r"^\s*import\s+([A-Za-z_][\w.]*)", re.MULTILINE)


def module_files(repo_root: str, module: str) -> List[str]:
    """模块名 → 仓库内文件（``a.b`` → ``a/b.py`` 或 ``a/b/__init__.py``）"""
    rel = str(module or "").replace(".", "/")
    if not rel:
        return []
    out: List[str] = []
    for candidate in (f"{rel}.py", f"{rel}/__init__.py"):
        if os.path.exists(os.path.join(repo_root, candidate)):
            out.append(candidate)
    return out


def infer_impl_files(repo_root: str, test_file: str, *,
                     limit: int = MAX_IMPL_FILES) -> List[str]:
    """由测试文件反推**被测实现文件**（仓库相对路径）

    规则（保守、可解释）：
      1. 扫描测试文件的 ``import`` / ``from ... import``，逐个映射到仓库内文件；
      2. 过滤只读区路径（即使被测也不给子代理改，避免产出必被护栏丢弃的工单）；
      3. 过滤 ``tests/`` 自身与测试辅助模块（``conftest`` 等）；
      4. 按「最短路径优先」排序（被测模块通常比工具链模块更靠上层）。
    """
    rel_test = str(test_file or "").replace("\\", "/")
    full = os.path.join(repo_root, rel_test)
    if not rel_test or not os.path.isfile(full):
        return []
    try:
        with open(full, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return []
    modules: List[str] = []
    for match in _FROM_IMPORT_RE.finditer(text):
        modules.append(match.group(1))
    for match in _PLAIN_IMPORT_RE.finditer(text):
        modules.append(match.group(1))
    out: List[str] = []
    for module in modules:
        for candidate in module_files(repo_root, module):
            if candidate in out:
                continue
            if candidate.startswith("tests/") or "/tests/" in candidate:
                continue
            if os.path.basename(candidate) in ("conftest.py", "__init__.py"):
                continue
            if is_readonly_path(candidate):
                continue
            out.append(candidate)
    out.sort(key=lambda p: (len(p), p))
    return out[: int(limit)]


def make_slice(repo_root: str, rel_path: str, center_line: int, *,
               radius: int, max_chars: int = MAX_SLICE_CHARS) -> Optional[SourceSlice]:
    """生成失败点 ± ``radius`` 行的源码切片（**带真实行号前缀**）"""
    rel = str(rel_path or "").replace("\\", "/")
    if not rel:
        return None
    full = os.path.join(repo_root, rel)
    if not os.path.isfile(full):
        return None
    try:
        with open(full, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return None
    total = len(lines)
    if total == 0:
        return SourceSlice(path=rel, start_line=1, end_line=1, content="(空文件)")
    center = int(center_line) if int(center_line) > 0 else 1
    start = max(1, center - int(radius))
    end = min(total, center + int(radius))
    body: List[str] = []
    truncated = False
    used = 0
    for idx in range(start, end + 1):
        line = f"{idx:>6}| {lines[idx - 1]}"
        used += len(line) + 1
        if used > int(max_chars):
            truncated = True
            break
        body.append(line)
    return SourceSlice(path=rel, start_line=start,
                       end_line=start + len(body) - 1 if body else start,
                       content="\n".join(body), truncated=truncated)


def build_slices(repo_root: str, failure: FailureItem, *, policy: RepairPolicy,
                 extra_files: Sequence[str] = ()) -> List[SourceSlice]:
    """为一条失败项构造切片集（失败点所在文件在前，其次为实现文件）"""
    slices: List[SourceSlice] = []
    primary = make_slice(repo_root, failure.file, failure.line,
                         radius=int(policy.slice_radius))
    if primary is not None:
        slices.append(primary)
    for rel in extra_files:
        if len(slices) >= MAX_SLICES:
            break
        if any(s.path == str(rel).replace("\\", "/") for s in slices):
            continue
        piece = make_slice(repo_root, rel, 1, radius=int(policy.slice_radius))
        if piece is not None:
            slices.append(piece)
    return slices


def trace_chain_leaf(*, logger_: Optional[RepairRunLogger] = None,
                     capability: str = "", limit: int = 20) -> Tuple[List[Dict[str, Any]],
                                                                     List[str]]:
    """读取相关 Trace 链（``UnifiedTraceStore.chain()`` / ``query()``）的**叶子视图**

    Returns:
        ``(叶子记录列表, 缺口说明列表)``。Trace 不可用时返回 ``([], ["..."])``
        —— 缺口是**事实**，不是错误。
    """
    gaps: List[str] = []
    store = logger_.store if logger_ is not None else None
    if store is None:
        gaps.append("统一 Trace 存储不可用 → 本条失败项无 Trace 证据")
        return [], gaps
    # 先把**本进程刚入队**的 Trace 落盘，再查询：``UnifiedTraceStore`` 是
    # "入队即返回、后台线程批量写"，不 flush 就查会看不到本次运行自己的记录
    # （实测：不 flush 时 query() 恒为空，看起来像"没有 Trace 证据"）。
    try:
        store.flush(timeout=2.0)
    except Exception:  # noqa: BLE001 flush 失败不阻断查询（最多少看到几条）
        pass
    try:
        if capability:
            traces = store.query(capability_id=capability, limit=int(limit))
        else:
            traces = store.query(limit=int(limit))
    except TypeError:
        # ``query()`` 签名差异（不同版本）：退化为无参查询
        try:
            traces = store.query()
        except Exception as exc:  # noqa: BLE001
            gaps.append(f"Trace 查询失败：{type(exc).__name__}")
            return [], gaps
    except Exception as exc:  # noqa: BLE001
        gaps.append(f"Trace 查询失败：{type(exc).__name__}")
        return [], gaps
    leaves: List[Dict[str, Any]] = []
    for trace in list(traces)[: int(limit)]:
        try:
            parent = str(getattr(trace, "parent_trace_id", "") or "")
            leaf = {
                "trace_id": str(getattr(trace, "trace_id", "")),
                "task_id": str(getattr(trace, "task_id", "")),
                "capability_id": str(getattr(trace, "capability_id", "")),
                "actor": str(getattr(trace, "actor", "")),
                "parent_trace_id": parent,
                "status": str(getattr(getattr(trace, "response", None), "status", "")),
                "duration_ms": getattr(getattr(trace, "timing", None), "duration_ms", None),
                "chain_len": 0,
            }
            if leaf["trace_id"]:
                try:
                    leaf["chain_len"] = len(store.chain(leaf["trace_id"]))
                except Exception:  # noqa: BLE001 链长是可选增强
                    leaf["chain_len"] = 0
            leaves.append(leaf)
        except Exception as exc:  # noqa: BLE001
            gaps.append(f"Trace 叶子提取失败：{type(exc).__name__}")
    if not leaves:
        gaps.append("Trace 库中无相关记录 → 本条失败项无 Trace 证据")
    if logger_ is not None and logger_.notes:
        gaps.extend(n for n in logger_.notes if "Trace" in n and n not in gaps)
    return leaves, gaps


def descriptors_leaf(*, keywords: Iterable[str] = (), limit: int = 8
                     ) -> Tuple[List[Dict[str, Any]], List[str]]:
    """读取涉及 capability 的 descriptor 叶子视图（按失败点关键词匹配）

    descriptor 是 S1-01/S1-02 的能力描述表（``data/descriptors.json``）。本函数
    **只读**该注册表（``DescriptorRegistry.load()`` + ``list()``），不实现第二套
    存储、也不触发任何写回（``autosave`` 只在 ``register`` 时生效）。

    Returns:
        ``(叶子记录列表, 缺口说明列表)``；注册表不可用/无匹配 → 缺口如实记录。
    """
    gaps: List[str] = []
    words = [str(w).lower() for w in keywords if str(w or "").strip()]
    if not words:
        return [], gaps
    try:
        from agent.descriptors.registry import DescriptorRegistry
    except Exception as exc:  # noqa: BLE001
        gaps.append(f"descriptor 注册表不可用（{type(exc).__name__}）→ 无 capability 证据")
        return [], gaps
    try:
        registry = DescriptorRegistry()
        registry.load()
        items = list(registry.list())
    except Exception as exc:  # noqa: BLE001
        gaps.append(f"descriptor 读取失败：{type(exc).__name__}: {exc}")
        return [], gaps
    out: List[Dict[str, Any]] = []
    for item in items:
        try:
            cid = str(getattr(item, "capability_id", "") or "")
            name = ""
            desc = ""
            meta = getattr(item, "meta", None)
            if meta is not None:
                name = str(getattr(meta, "name", "") or "")
                desc = str(getattr(meta, "description", "") or "")
            haystack = f"{cid} {name} {desc}".lower()
            if any(word in haystack for word in words):
                out.append({"capability_id": cid, "name": name,
                            "description": clean_text(desc, limit=160)})
        except Exception:  # noqa: BLE001 单条异常不拖垮整体
            continue
        if len(out) >= int(limit):
            break
    if not out:
        gaps.append("未匹配到相关 descriptor → 无 capability 证据（非错误）")
    return out, gaps


def adjacent_tests(repo_root: str, changed_files: Sequence[str], *,
                   failure_file: str = "", limit: int = 8) -> List[str]:
    """邻接回归测试候选：由改动文件映射到测试子集

    映射规则（按优先级）：
      1. 改动文件本身是测试 → 直接收；
      2. ``agent/x/y.py`` → ``tests/unit/test_y.py`` / ``tests/**/test_y.py``；
      3. 目标失败用例所在文件（保证邻接回归包含失败点所在套件）。
    """
    out: List[str] = []
    for rel in list(changed_files) + ([failure_file] if failure_file else []):
        posix = str(rel or "").replace("\\", "/")
        if not posix:
            continue
        if posix.startswith("tests/") and posix.endswith(".py"):
            if posix not in out:
                out.append(posix)
            continue
        stem = os.path.basename(posix)
        if stem.endswith(".py"):
            stem = stem[:-3]
        else:
            continue
        if stem in ("__init__", "conftest"):
            continue
        for candidate in (f"tests/unit/test_{stem}.py",
                          f"tests/unit/test_{stem}_unit.py"):
            if os.path.isfile(os.path.join(repo_root, candidate)) and candidate not in out:
                out.append(candidate)
    return out[: int(limit)]


def locate(report: DiagnosisReport, *, repo_root: str, policy: Optional[RepairPolicy] = None,
           failure: Optional[FailureItem] = None, run_logger: Optional[RepairRunLogger] = None,
           capability: str = "") -> RepairTicket:
    """为一条失败项生成派工工单（步骤 2）

    Args:
        report: 体检报告（提供 ``recent_changes`` 与失败清单）。
        repo_root: 仓库根（切片与只读 git 的基准）。
        policy: 策略（切片半径、历史条数）。
        failure: 指定失败项；缺省取报告中的第一条。
        run_logger: 留痕器（用于读 Trace 与写留痕）。
        capability: 相关 capability 名（Trace 查询过滤）。

    Returns:
        ``RepairTicket``（``evidence_gaps`` 如实列出证据缺口）。
    """
    policy = policy or RepairPolicy()
    target = failure or (report.failures[0] if report.failures else FailureItem())
    gaps: List[str] = []

    impl_files = infer_impl_files(repo_root, target.file)
    if not impl_files:
        gaps.append("未能由测试文件反推实现文件（切片仅含测试文件）")
    slices = build_slices(repo_root, target, policy=policy, extra_files=impl_files)
    if not slices:
        gaps.append(f"源码切片为空（文件不存在或不可读：{target.file}）")

    traces, trace_gaps = trace_chain_leaf(logger_=run_logger, capability=capability)
    gaps.extend(trace_gaps)

    keywords: List[str] = []
    for rel in [target.file, *impl_files]:
        stem = os.path.basename(str(rel or "")).replace(".py", "")
        if stem.startswith("test_"):
            stem = stem[5:]
        if stem:
            keywords.append(stem)
    descriptors, desc_gaps = descriptors_leaf(keywords=keywords)
    gaps.extend(desc_gaps)

    changes = report.recent_changes
    if not changes:
        changes = recent_changes(repo_root, policy=policy,
                                 interest=[target.file] if target.file else [])
    if not changes:
        gaps.append("近期改动为空（git 不可用或历史过短）——非错误，仅缺旁证")

    head = gitio.repo_head(repo_root)
    if not head:
        gaps.append("无法取得 HEAD SHA（非 git 仓库？）——补丁基线锚点缺失")
    if not gitio.is_git_repo(repo_root):
        gaps.append("给定根不是 git 工作区：仅出补丁文件，不创建产物分支")

    tests = adjacent_tests(repo_root, impl_files, failure_file=target.file)

    ticket = RepairTicket(
        ticket_id=f"tkt-{short_hash(f'{target.node_id}|{target.stack_fingerprint}')}",
        failure=target, slices=slices, impl_files=impl_files, trace_chain=traces,
        descriptors=descriptors, recent_changes=changes, repo_head=head,
        adjacent_tests=tests,
        evidence_gaps=gaps)

    if run_logger is not None:
        run_logger.record_step(
            "locate", subject=ticket.ticket_id,
            status="ok" if target.node_id else "error",
            detail={"failure": target.node_id, "slices": len(slices),
                    "impl_files": list(impl_files), "trace_records": len(traces),
                    "descriptors": len(descriptors), "evidence_gaps": len(gaps),
                    "repo_head": head[:12], "adjacent_tests": tests},
            error="" if target.node_id else "工单缺少失败项（无可定位目标）")
    return ticket


__all__ = [
    "MAX_SLICE_CHARS", "MAX_SLICES", "MAX_IMPL_FILES",
    "module_files", "infer_impl_files", "make_slice", "build_slices",
    "trace_chain_leaf", "descriptors_leaf", "adjacent_tests", "locate",
]
