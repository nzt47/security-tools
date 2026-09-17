"""工具注册模块 — 工程闭环的两个缺口：跑测试、应用补丁

此前云枢要「验证自己的改动」只能靠 ``shell_execute`` 拼 ``python -m pytest ...``，
要「落地一个改动」只能整文件覆盖写。两者都缺一样东西：**结构化的结果**。
本模块补上：

    run_tests    —— 跑 pytest 并把摘要解析成 passed/failed/errors/duration
    apply_patch  —— 应用 unified diff，先全量校验再写盘（拒绝"改一半"）

【不易】
    - ``apply_patch`` 的核心不变量：**任何 hunk 上下文不匹配 ⇒ 一个字节都不写**。
      实现方式是"先在内存里把全部文件重建出来并逐 hunk 校验，全部通过后才落盘"。
      半途写盘会让工作区处于既不是旧版本也不是新版本的中间态——比直接失败更糟。
    - 路径必须落在项目根目录内（``os.path.normcase`` 比较，兼容 Windows 大小写不敏感）。
    - 子进程一律 ``subprocess.run(argv=list)``，**永不 shell=True**。
【变易】输出裁剪上限、超时上限是这里的常量；pytest 参数拼装集中在 ``register_all`` 内。
【简易】两个工具各一个 handler，失败一律结构化返回。
"""
import importlib.util
import logging
import os
import re
import subprocess
import sys
import time

from agent import tools as _tools

logger = logging.getLogger(__name__)

_TEST_TIMEOUT_DEFAULT = 300
_TEST_TIMEOUT_CAP = 900
_TEST_OUTPUT_DEFAULT = 8000
_TEST_OUTPUT_CAP = 200000

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
#: pytest 摘要里的计数项（-q 模式最后一行，如 "1 failed, 2 passed in 1.23s"）
_COUNT_RE = re.compile(
    r"(\d+)\s+(passed|failed|error|errors|skipped|xfailed|xpassed|warning|warnings)\b"
)
_DURATION_RE = re.compile(r"\bin\s+([\d.]+)s\b")
#: unified diff 文件头
_FILE_HEADER_RE = re.compile(r"^---\s+(.+)$")
#: unified diff hunk 头：@@ -l[,s] +l[,s] @@ [可选的函数上下文]
_HUNK_RE = re.compile(r"^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@")


def _repo_root() -> str:
    """项目根目录（agent/tools/test_tools.py → 上溯三级）"""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _as_int(value, default, low=None, high=None) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = default
    if low is not None:
        result = max(low, result)
    if high is not None:
        result = min(high, result)
    return result


def _permission_denied(dl, action: str, context: str):
    """权限闸门（可选依赖，缺失时放行）。"""
    check = getattr(getattr(dl, "_permission", None), "check_action", None)
    if not callable(check):
        return None
    try:
        result = check(action, context)
    except Exception as e:  # noqa: BLE001 校验故障不阻断（工具自身仍受其它闸门约束）
        logger.warning("[test_tools] 权限校验异常，按放行处理: %s — %s", action, e)
        return None
    if isinstance(result, dict):
        allowed, reason = result.get("allowed"), result.get("reason", "")
    else:
        allowed, reason = getattr(result, "allowed", None), getattr(result, "reason", "")
    if allowed is False:
        return {"ok": False, "error": f"权限系统拒绝: {reason}", "blocked": True}
    return None


# ════════════════════════════════════════════════════════════
#  unified diff：解析
# ════════════════════════════════════════════════════════════

def _clean_header_path(raw: str):
    """把 diff 头里的路径规范化；``/dev/null`` 返回 None。

    处理三种真实写法：``a/foo.py``、``b/foo.py``、``"a/foo bar.py"``（git 对特殊
    字符路径会加引号），以及 ``--- a/foo.py\\t2024-01-01`` 这种带时间戳的老式头。
    """
    path = (raw or "").strip()
    if "\t" in path:
        path = path.split("\t", 1)[0].strip()
    if len(path) >= 2 and path.startswith('"') and path.endswith('"'):
        path = path[1:-1]
    if path in ("/dev/null", "dev/null", "nul"):
        return None
    for prefix in ("a/", "b/"):
        if path.startswith(prefix):
            path = path[len(prefix):]
            break
    return path


def _parse_patch(patch: str):
    """解析 unified diff。

    Returns:
        ``(files, error)``：files 为 ``[{"old","new","hunks":[...]}]``；
        error 非空表示补丁本身格式非法。
    """
    text = patch.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    total = len(lines)
    files: list[dict] = []
    current = None
    idx = 0

    while idx < total:
        line = lines[idx]
        line_no = idx + 1

        if line.startswith("diff --git") or line.startswith("index ") or line.startswith("new file mode") \
                or line.startswith("deleted file mode") or line.startswith("old mode") \
                or line.startswith("new mode") or line.startswith("similarity index") \
                or line.startswith("rename ") or line.startswith("copy "):
            idx += 1
            continue

        if line.startswith("\\"):
            # "\ No newline at end of file"：git 的语义提示行，重建时不参与
            idx += 1
            continue

        header = _FILE_HEADER_RE.match(line)
        if header:
            if idx + 1 >= total or not lines[idx + 1].startswith("+++"):
                return None, f"第 {line_no} 行：--- 文件头之后缺少 +++ 文件头"
            old_path = _clean_header_path(header.group(1))
            new_path = _clean_header_path(lines[idx + 1][4:])
            current = {"old": old_path, "new": new_path, "hunks": []}
            files.append(current)
            idx += 2
            continue

        if line.startswith("+++"):
            return None, f"第 {line_no} 行：出现孤立的 +++ 文件头（缺少前面的 --- 头）"

        if line.startswith("@@"):
            if current is None:
                return None, f"第 {line_no} 行：hunk 出现在任何文件头之前"
            match = _HUNK_RE.match(line)
            if not match:
                return None, f"第 {line_no} 行：无法解析的 hunk 头 {line[:80]!r}"
            old_start = int(match.group(1))
            old_count = int(match.group(2)) if match.group(2) is not None else 1
            new_start = int(match.group(3))
            new_count = int(match.group(4)) if match.group(4) is not None else 1
            idx += 1

            # 按"声明行数"驱动消费，而不是遇到 --- / @@ 就停：
            # 删除行 "- -- foo" 在原文里就是 "--- foo"，与文件头同形。
            # 只有计数已满时，形如文件头的行才可能是下一个文件的头。
            body: list[list] = []  # 每项 [prefix, content]
            old_seen = 0
            new_seen = 0
            last_prefix = None
            old_no_newline = False
            new_no_newline = False
            while idx < total:
                if old_seen >= old_count and new_seen >= new_count:
                    break
                body_line = lines[idx]
                if body_line.startswith("@@") or body_line.startswith("diff --git"):
                    break
                if body_line.startswith("\\"):
                    # "\ No newline at end of file"：只对**紧接着的那一行**生效。
                    # 跟在 '+' 后 ⇒ 新内容末尾无换行；跟在 '-'/' ' 后 ⇒ 旧内容末尾无换行。
                    if last_prefix in ("+", " "):
                        new_no_newline = True
                    if last_prefix in ("-", " "):
                        old_no_newline = True
                    idx += 1
                    continue
                if body_line.startswith("--- ") and (old_seen >= old_count or new_seen >= new_count):
                    break
                if body_line == "":
                    if idx == total - 1:
                        break  # 补丁末尾换行产生的空元素，不属于 hunk
                    prefix, content = " ", ""
                elif body_line[0] in " +-":
                    prefix, content = body_line[0], body_line[1:]
                else:
                    break  # 非 hunk 行 → 交回外层（外层会报"无法解析"）
                body.append([prefix, content])
                last_prefix = prefix
                if prefix in (" ", "-"):
                    old_seen += 1
                if prefix in (" ", "+"):
                    new_seen += 1
                idx += 1

            old_lines = [c for p, c in body if p in (" ", "-")]
            new_lines = [c for p, c in body if p in (" ", "+")]
            if old_count != len(old_lines) or new_count != len(new_lines):
                return None, (
                    f"hunk 头 {line.strip()[:60]!r} 声明的行数与实际不符："
                    f"旧 {old_count}/{len(old_lines)}，新 {new_count}/{len(new_lines)}"
                )
            current["hunks"].append({
                "header": line.strip(),
                "old_start": old_start,
                "old_count": old_count,
                "new_start": new_start,
                "new_count": new_count,
                "old_lines": old_lines,
                "new_lines": new_lines,
                # 真正的 +/- 行数（不含上下文行）——用于向调用方汇报改动规模
                "added": sum(1 for p, _c in body if p == "+"),
                "removed": sum(1 for p, _c in body if p == "-"),
                "old_no_newline": old_no_newline,
                "new_no_newline": new_no_newline,
            })
            continue

        if line.strip() == "":
            idx += 1
            continue

        return None, f"第 {line_no} 行：无法解析的内容 {line[:80]!r}（既不是文件头也不是 hunk）"

    if not files:
        return None, "补丁中没有解析到任何文件（需要 --- / +++ 文件头）"
    for entry in files:
        if not entry["hunks"]:
            return None, f"文件 {entry.get('new') or entry.get('old')} 没有任何 hunk"
    return files, None


def _resolve_in_root(root: str, rel_path: str):
    """把补丁里的相对路径解析到根目录内；越界/绝对路径一律拒绝。"""
    if not rel_path:
        return None, "路径为空"
    if os.path.isabs(rel_path) or re.match(r"^[A-Za-z]:", rel_path):
        return None, f"拒绝绝对路径: {rel_path}"
    full = os.path.abspath(os.path.join(root, rel_path))
    full_c = os.path.normcase(full)
    root_c = os.path.normcase(os.path.abspath(root))
    if full_c != root_c and not full_c.startswith(root_c + os.sep):
        return None, f"路径越出项目根目录，已拒绝: {rel_path}"
    return full, None


def _read_lines(path: str):
    """读取文本文件为行列表，并返回其原本的换行符（写回时保留）。"""
    with open(path, "r", encoding="utf-8", newline="") as f:
        raw = f.read()
    if "\r\n" in raw:
        newline = "\r\n"
    elif "\r" in raw:
        newline = "\r"
    else:
        newline = "\n"
    normalized = raw.replace("\r\n", "\n").replace("\r", "\n")
    return normalized.split("\n"), newline


def _rebuild(original_lines: list[str], hunks: list[dict], label: str):
    """在内存中把 hunks 应用到 original_lines。

    Returns:
        ``(result_lines, added, removed, failed_hunk)``；failed_hunk 非空表示校验失败，
        此时不返回任何结果行（调用方不得写盘）。
    """
    result: list[str] = []
    cursor = 0  # 原文件游标（行下标）
    added = 0
    removed = 0

    for hunk in hunks:
        start = hunk["old_start"] - 1 if hunk["old_start"] > 0 else 0
        if start < cursor:
            return None, 0, 0, {
                "path": label,
                "header": hunk["header"],
                "reason": f"hunk 起点（第 {hunk['old_start']} 行）早于上一个 hunk 的结束位置——"
                          f"补丁必须按行号升序且互不重叠",
            }
        if start > len(original_lines):
            return None, 0, 0, {
                "path": label,
                "header": hunk["header"],
                "reason": f"hunk 起点（第 {hunk['old_start']} 行）超出文件总行数 {len(original_lines)}",
            }

        result.extend(original_lines[cursor:start])
        expected = hunk["old_lines"]
        actual = original_lines[start:start + len(expected)]
        if actual != expected:
            first_diff = 0
            limit = min(len(actual), len(expected))
            while first_diff < limit and actual[first_diff] == expected[first_diff]:
                first_diff += 1
            return None, 0, 0, {
                "path": label,
                "header": hunk["header"],
                "reason": "hunk 上下文与原文件不一致（为安全起见未写入任何改动）",
                "line": start + first_diff + 1,
                "expected": expected[max(0, first_diff - 2):first_diff + 3],
                "actual": actual[max(0, first_diff - 2):first_diff + 3],
            }

        result.extend(hunk["new_lines"])
        added += hunk.get("added", 0)
        removed += hunk.get("removed", 0)
        cursor = start + len(expected)

    result.extend(original_lines[cursor:])
    # 文件末尾换行：diff 文本本身表达不了"最后一行有没有换行"，
    # 只能靠 "\ No newline at end of file" 标记与"新建文件默认以换行结尾"补回。
    if any(h.get("new_no_newline") for h in hunks):
        while result and result[-1] == "":
            result.pop()
    elif not original_lines and result and result[-1] != "":
        result.append("")
    return result, added, removed, None


def register_all(dl):
    """注册测试与补丁工具

    Args:
        dl: DigitalLife 实例（用于权限闸门；缺失时按放行处理）
    """

    # ════════════════════════════════════════════════════════════
    #  run_tests
    # ════════════════════════════════════════════════════════════

    @_tools.register("run_tests", "运行项目测试（pytest）并返回结构化结果：通过数、失败数、错误数、耗时与输出摘要。可用 path 限定测试文件/目录，用 pattern 做 -k 关键字过滤。Run the project test suite, run pytest, execute unit tests", schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "测试文件或目录（相对项目根目录，如 tests/unit/test_x.py）；不传则跑全部"},
            "pattern": {"type": "string", "description": "pytest -k 过滤表达式，如 \"login or logout\"，只跑名称匹配的用例"},
            "timeout_sec": {"type": "integer", "description": "超时秒数，默认 300，最大 900"},
            "max_output_chars": {"type": "integer", "description": "输出尾部保留的字符数，默认 8000"},
        },
    })
    def _run_tests(**kwargs):
        try:
            repo_root = _repo_root()
            path = kwargs.get("path") or ""
            pattern = kwargs.get("pattern") or ""
            timeout_sec = _as_int(kwargs.get("timeout_sec", _TEST_TIMEOUT_DEFAULT),
                                  _TEST_TIMEOUT_DEFAULT, low=1, high=_TEST_TIMEOUT_CAP)
            max_output_chars = _as_int(kwargs.get("max_output_chars", _TEST_OUTPUT_DEFAULT),
                                       _TEST_OUTPUT_DEFAULT, low=200, high=_TEST_OUTPUT_CAP)

            if path:
                probe = path if os.path.isabs(path) else os.path.join(repo_root, str(path))
                if not os.path.exists(probe):
                    return {"ok": False, "error": f"测试路径不存在: {path}"}

            try:
                pytest_available = importlib.util.find_spec("pytest") is not None
            except Exception:  # noqa: BLE001
                pytest_available = False
            if not pytest_available:
                return {
                    "ok": False,
                    "error": "pytest 不可用：当前 Python 环境未安装 pytest，请先 pip install pytest",
                    "pytest_available": False,
                }

            argv = [sys.executable, "-m", "pytest"]
            if path:
                argv.append(str(path))
            argv += ["-q", "--no-header"]
            if pattern:
                argv += ["-k", str(pattern)]

            denied = _permission_denied(dl, "run_tests", f"运行测试: {' '.join(argv[2:])}")
            if denied:
                return denied

            env = dict(os.environ)
            env["PYTHONIOENCODING"] = "utf-8"
            env["NO_COLOR"] = "1"
            env["PYTHONUNBUFFERED"] = "1"

            started = time.time()
            try:
                proc = subprocess.run(
                    argv,
                    cwd=repo_root,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout_sec,
                    env=env,
                    shell=False,  # noqa: S603 argv 列表 + 无 shell
                )
            except subprocess.TimeoutExpired as e:
                partial = ""
                for chunk in (getattr(e, "stdout", None), getattr(e, "stderr", None)):
                    if chunk:
                        partial += chunk if isinstance(chunk, str) else chunk.decode("utf-8", "replace")
                partial = _ANSI_RE.sub("", partial)
                return {
                    "ok": False,
                    "error": f"测试超时（>{timeout_sec} 秒）已被终止",
                    "timeout_sec": timeout_sec,
                    "duration_sec": round(time.time() - started, 2),
                    "output_tail": partial[-max_output_chars:],
                }
            except OSError as e:
                return {"ok": False, "error": f"无法启动 pytest: {e}"}

            duration = round(time.time() - started, 2)
            raw_output = (proc.stdout or "")
            if proc.stderr:
                raw_output += ("\n" if raw_output else "") + proc.stderr
            output = _ANSI_RE.sub("", raw_output)

            passed = failed = errors = skipped = 0
            summary = ""
            for line in output.splitlines():
                stripped = line.strip()
                counts = _COUNT_RE.findall(stripped)
                if counts and (" in " in stripped or stripped.startswith(("=", "no tests"))):
                    passed = failed = errors = skipped = 0
                    for number, kind in counts:
                        value = int(number)
                        if kind == "passed":
                            passed = value
                        elif kind == "failed":
                            failed = value
                        elif kind in ("error", "errors"):
                            errors = value
                        elif kind == "skipped":
                            skipped = value
                    summary = stripped
            if not summary:
                # 兜底：取最后一行非空输出作为摘要
                tail_lines = [l.strip() for l in output.splitlines() if l.strip()]
                summary = tail_lines[-1] if tail_lines else ""

            duration_match = _DURATION_RE.search(summary)
            if duration_match:
                try:
                    duration = float(duration_match.group(1))
                except ValueError:
                    pass

            return {
                "ok": proc.returncode == 0 and failed == 0 and errors == 0,
                "returncode": proc.returncode,
                "passed": passed,
                "failed": failed,
                "errors": errors,
                "skipped": skipped,
                "duration_sec": round(float(duration), 2),
                "summary": summary,
                "output_tail": output[-max_output_chars:],
                "command": argv,
                "cwd": repo_root,
                "timeout_sec": timeout_sec,
            }
        except Exception as e:  # noqa: BLE001
            logger.warning("[test_tools] run_tests 异常: %s", e)
            return {"ok": False, "error": f"测试执行失败: {e}"}

    # ════════════════════════════════════════════════════════════
    #  apply_patch
    # ════════════════════════════════════════════════════════════

    @_tools.register("apply_patch", "把 unified diff 补丁应用到工作区（支持 --- a/路径、+++ b/路径 文件头与 @@ -l,s +l,s @@ hunk）。先逐 hunk 校验上下文，全部通过才写盘：任何一处不匹配都不会产生半截改动。dry_run=true 时只校验不写。不能删除文件，拒绝越出项目根目录的路径。Apply a unified diff patch to files", schema={
        "type": "object",
        "properties": {
            "patch": {"type": "string", "description": "unified diff 文本，需含 --- / +++ 文件头与 @@ hunk 头"},
            "dry_run": {"type": "boolean", "description": "为 true 时只做校验、不写盘，默认 false"},
        },
        "required": ["patch"],
    })
    def _apply_patch(**kwargs):
        try:
            patch = kwargs.get("patch", "")
            if not patch or not str(patch).strip():
                return {"ok": False, "error": "请提供补丁内容（patch）"}
            dry_run = bool(kwargs.get("dry_run", False))
            root = _repo_root()

            files, parse_error = _parse_patch(str(patch))
            if parse_error:
                return {"ok": False, "error": f"补丁格式错误：{parse_error}"}

            staged: list[dict] = []
            total_added = 0
            total_removed = 0

            for entry in files:
                target_rel = entry["new"] or entry["old"]
                if entry["new"] is None:
                    return {
                        "ok": False,
                        "error": f"不支持删除文件的补丁（+++ /dev/null）: {entry['old']}",
                        "failed_hunk": {"path": entry["old"], "reason": "删除文件不在本工具能力范围内"},
                    }
                if entry["old"] is None and entry["new"] is None:
                    return {"ok": False, "error": "补丁缺少有效的文件路径"}

                full, path_error = _resolve_in_root(root, target_rel)
                if path_error:
                    return {
                        "ok": False,
                        "error": path_error,
                        "failed_hunk": {"path": target_rel, "reason": path_error},
                    }

                if entry["old"] is None:
                    # 新建文件
                    if os.path.exists(full):
                        return {
                            "ok": False,
                            "error": f"补丁声明新建文件，但目标已存在: {target_rel}",
                            "failed_hunk": {"path": target_rel, "reason": "目标已存在"},
                        }
                    original_lines, newline, created = [], "\n", True
                else:
                    if not os.path.exists(full):
                        return {
                            "ok": False,
                            "error": f"目标文件不存在: {target_rel}",
                            "failed_hunk": {"path": target_rel, "reason": "文件不存在"},
                        }
                    if os.path.isdir(full):
                        return {
                            "ok": False,
                            "error": f"目标是目录而非文件: {target_rel}",
                            "failed_hunk": {"path": target_rel, "reason": "目标是目录"},
                        }
                    try:
                        original_lines, newline = _read_lines(full)
                    except UnicodeDecodeError:
                        return {
                            "ok": False,
                            "error": f"文件不是 UTF-8 文本，已跳过: {target_rel}",
                            "failed_hunk": {"path": target_rel, "reason": "非 UTF-8 文本"},
                        }
                    except OSError as e:
                        return {
                            "ok": False,
                            "error": f"读取文件失败: {target_rel} — {e}",
                            "failed_hunk": {"path": target_rel, "reason": str(e)},
                        }
                    created = False

                rebuilt, added, removed, failed = _rebuild(
                    original_lines, entry["hunks"], target_rel
                )
                if failed is not None:
                    # 关键不变量：任何一处校验失败 → 不写任何文件
                    return {
                        "ok": False,
                        "error": f"补丁校验失败（未写入任何改动）：{failed.get('reason', '')}",
                        "failed_hunk": failed,
                    }

                staged.append({
                    "path": target_rel,
                    "full": full,
                    "content": newline.join(rebuilt),
                    "encoding": "utf-8",
                    "created": created,
                    "hunks": len(entry["hunks"]),
                    "added": added,
                    "removed": removed,
                })
                total_added += added
                total_removed += removed

            # 全部校验通过后，先做完整权限校验再统一落盘（避免权限拒绝造成的半写）
            for item in staged:
                denied = _permission_denied(
                    dl,
                    f"write_file:{item['full']}",
                    f"应用补丁写入 {item['path']}",
                )
                if denied:
                    denied["error"] = f"{denied['error']}（未写入任何改动）"
                    return denied

            if dry_run:
                return {
                    "ok": True,
                    "dry_run": True,
                    "applied": False,
                    "files": [
                        {
                            "path": i["path"], "hunks": i["hunks"],
                            "added": i["added"], "removed": i["removed"],
                            "created": i["created"], "bytes": len(i["content"].encode("utf-8")),
                        }
                        for i in staged
                    ],
                    "total_added": total_added,
                    "total_removed": total_removed,
                    "message": "dry_run=true：全部 hunk 校验通过，未写入任何文件",
                }

            written = []
            for item in staged:
                parent = os.path.dirname(item["full"])
                if parent and not os.path.isdir(parent):
                    os.makedirs(parent, exist_ok=True)
                with open(item["full"], "w", encoding=item["encoding"], newline="") as f:
                    f.write(item["content"])
                written.append({
                    "path": item["path"], "hunks": item["hunks"],
                    "added": item["added"], "removed": item["removed"],
                    "created": item["created"], "bytes": len(item["content"].encode("utf-8")),
                })

            return {
                "ok": True,
                "dry_run": False,
                "applied": True,
                "files": written,
                "total_added": total_added,
                "total_removed": total_removed,
                "root": root,
            }
        except Exception as e:  # noqa: BLE001
            logger.warning("[test_tools] apply_patch 异常: %s", e)
            return {"ok": False, "error": f"应用补丁失败: {e}"}
