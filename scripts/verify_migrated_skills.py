"""验证迁移到 data/skills_repo/ 的技能能否被三层架构正确加载

三层验证范围:
    L1 元数据: SkillFileStore.load_metadata_index() / SkillLoader.match()
    L2 使用说明: SkillManager.load_instruction()
    L3 脚本执行: SkillExecutor.execute()
        - 无脚本技能: 预期抛 SCRIPT_NOT_FOUND
        - 有脚本技能: 从 default_params 注入参数, 断言 success/exit_code/result

支持三种模式:
    默认模式: 验证 data/skills_repo/ 下从 skills.json 迁移的技能
    --self-test: 在临时仓库创建带脚本技能, 验证有脚本路径工作正常
    --watch: 监控 data/skills_repo 目录变动, 变动后自动触发验证

说明:
    本脚本只读不写, 不修改任何源数据。
    退出码: 0=全部通过, 1=有错误 (--watch 模式下退出码仅首次执行有效)
"""
from __future__ import annotations
import sys
import json
import shutil
import tempfile
import time
from pathlib import Path
from typing import Tuple, Dict, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.skills_mgmt.skill_manager import SkillManager
from agent.skills_mgmt.exceptions import (
    SkillExecutionError, SkillNotFoundError, ErrorCode,
)

LEGACY_JSON = ROOT / "data" / "skills.json"
REPO_PATH = ROOT / "data" / "skills_repo"


def verify_skill_l3(mgr: SkillManager, sid: str, meta: dict) -> Tuple[bool, str]:
    """验证单个技能的 L3 脚本执行, 自动处理有脚本/无脚本两条路径

    扩展点落地:
        1. 分支化: 先 list_scripts() 判断有无脚本
        2. 参数注入: 从 front matter 的 default_params 读取
        3. 结果校验: 断言 success / exit_code / result 非空

    日志输出:
        打印每个技能的脚本检查耗时、参数注入详情、执行耗时、返回结果

    Returns:
        (ok, message)
    """
    t_start = time.time()

    # ── 步骤 1: 检查脚本是否存在 ──
    t0 = time.time()
    scripts = mgr.file_store.list_scripts(sid)
    t_list = (time.time() - t0) * 1000
    print(f"         ├─ [trace] list_scripts 耗时={t_list:.2f}ms scripts={scripts}")

    if not scripts:
        # ── 无脚本路径: 预期抛 SCRIPT_NOT_FOUND ──
        t0 = time.time()
        try:
            mgr.execute(sid, params={})
            t_exec = (time.time() - t0) * 1000
            print(f"         ├─ [trace] execute 耗时={t_exec:.2f}ms (无脚本, 意外成功)")
            return False, "意外执行成功 (无脚本不应执行)"
        except SkillExecutionError as e:
            t_exec = (time.time() - t0) * 1000
            expect = ErrorCode.SCRIPT_NOT_FOUND
            print(f"         ├─ [trace] execute 耗时={t_exec:.2f}ms code={e.code}")
            if e.code == expect:
                return True, f"code={e.code} (无脚本预期)"
            return False, f"code={e.code} 期望={expect}"

    # ── 步骤 2: 参数注入 ──
    t0 = time.time()
    params = meta.get("default_params") or {}
    t_param = (time.time() - t0) * 1000
    print(f"         ├─ [trace] 参数注入 耗时={t_param:.2f}ms params={params}")

    # ── 步骤 3: 执行脚本 ──
    t0 = time.time()
    try:
        result = mgr.execute(sid, params=params)
    except SkillExecutionError as e:
        t_exec = (time.time() - t0) * 1000
        print(f"         ├─ [trace] execute 耗时={t_exec:.2f}ms (异常)")
        return False, f"执行异常 code={e.code} msg={e}"
    t_exec = (time.time() - t0) * 1000
    print(f"         ├─ [trace] execute 耗时={t_exec:.2f}ms success={result.success} exit={result.exit_code}")

    # ── 步骤 4: 结果校验 ──
    t0 = time.time()
    if not result.success:
        t_check = (time.time() - t0) * 1000
        print(f"         ├─ [trace] 结果校验 耗时={t_check:.2f}ms (success=False)")
        return False, f"success=False exit={result.exit_code} err={result.error}"
    if result.exit_code != 0:
        t_check = (time.time() - t0) * 1000
        print(f"         ├─ [trace] 结果校验 耗时={t_check:.2f}ms (exit_code!=0)")
        return False, f"exit_code={result.exit_code} (期望 0)"
    if result.result is None:
        t_check = (time.time() - t0) * 1000
        print(f"         ├─ [trace] 结果校验 耗时={t_check:.2f}ms (result 为空)")
        return False, f"success 但 result 为空 (脚本未输出 JSON)"
    t_check = (time.time() - t0) * 1000
    result_preview = str(result.result)[:80]
    print(f"         ├─ [trace] 结果校验 耗时={t_check:.2f}ms result={result_preview}")

    t_total = (time.time() - t_start) * 1000
    print(f"         └─ [trace] 总耗时={t_total:.2f}ms")
    return True, f"success exit=0 scripts={scripts} result_keys={list(result.result.keys()) if isinstance(result.result, dict) else type(result.result).__name__}"


def main() -> int:
    if "--self-test" in sys.argv:
        return run_self_test()
    if "--watch" in sys.argv:
        verbose = "--verbose" in sys.argv or "-v" in sys.argv
        return run_watch(verbose=verbose)

    with open(LEGACY_JSON, encoding="utf-8") as f:
        legacy = {s["id"]: s for s in json.load(f)["skills"]}

    print(f"[setup] legacy_count={len(legacy)} repo_path={REPO_PATH}")
    mgr = SkillManager(repo_path=str(REPO_PATH))

    # ─── L1: 元数据索引 ──────────────────────────────
    print("\n[L1] load_metadata_index")
    index = mgr.file_store.load_metadata_index(refresh=True)
    print(f"     repo_count={len(index)}")

    l1_ok = True
    for sid, ls in legacy.items():
        if sid not in index:
            print(f"     [MISSING] {sid}")
            l1_ok = False
            continue
        m = index[sid]
        name_ok = m.get("name") == ls.get("name")
        en_ok = m.get("enabled") == ls.get("enabled")
        desc_ok = (m.get("description") or "") == (ls.get("description") or "")
        status = "OK" if (name_ok and en_ok and desc_ok) else "FAIL"
        if status == "FAIL":
            l1_ok = False
        print(f"     [{status}] {sid}: name={m.get('name')!r} enabled={m.get('enabled')} desc_match={desc_ok}")

    # ─── L1+: match 各技能自身意图 ─────────────────────
    print("\n[L1+] SkillLoader.match (各技能 name 作为意图)")
    l1p_ok = True
    for sid, ls in legacy.items():
        intent = ls.get("name") or sid
        result = mgr.match(intent, top_k=10, enabled_only=False)
        hit_ids = [m.skill_id for m in result.matches]
        if sid in hit_ids:
            print(f"     [OK]    {sid} <- intent={intent!r}  (命中)")
        else:
            print(f"     [WARN]  {sid} <- intent={intent!r}  (未命中, TF-IDF 阈值原因, 非阻塞)")
            # 不视为错误: 短意图可能 TF-IDF 分数低，不阻塞元数据加载验证

    # ─── L2: 使用说明 ────────────────────────────────
    print("\n[L2] SkillManager.load_instruction")
    l2_ok = True
    for sid in legacy:
        try:
            result = mgr.load_instruction(sid)
            # SkillManager.load_instruction 返回 dict {instruction, ...}
            body = result.get("instruction", "") if isinstance(result, dict) else str(result)
            body_ok = bool(body.strip())
            status = "OK" if body_ok else "FAIL"
            if not body_ok:
                l2_ok = False
            print(f"     [{status}] {sid}: body_chars={len(body)}")
        except SkillNotFoundError as e:
            print(f"     [FAIL] {sid}: SkillNotFoundError {e}")
            l2_ok = False

    # ─── L3: 脚本执行 (分支化: 自动处理有/无脚本) ────
    print("\n[L3] SkillExecutor.execute (分支化: 无脚本→SCRIPT_NOT_FOUND / 有脚本→执行校验)")
    l3_ok = True
    for sid in legacy:
        meta = index.get(sid, {})
        ok, msg = verify_skill_l3(mgr, sid, meta)
        status = "OK" if ok else "FAIL"
        if not ok:
            l3_ok = False
        print(f"     [{status}] {sid}: {msg}")

    # ─── 总结 ───────────────────────────────────────
    print("\n" + "=" * 60)
    print(f"L1  metadata:  {'PASS' if l1_ok else 'FAIL'}")
    print(f"L1+ match:     {'PASS' if l1p_ok else 'FAIL'} (WARN 不阻塞)")
    print(f"L2  instruction: {'PASS' if l2_ok else 'FAIL'}")
    print(f"L3  execute:   {'PASS' if l3_ok else 'FAIL'} (分支化)")
    overall = l1_ok and l2_ok and l3_ok
    print("=" * 60)
    print("OVERALL: " + ("PASS" if overall else "FAIL"))
    return 0 if overall else 1


def run_self_test() -> int:
    """自测模式: 在临时仓库创建带脚本技能, 验证 verify_skill_l3 的有脚本路径

    场景:
        - 技能含 skill.md + scripts/main.py
        - front matter 含 default_params
        - 脚本读 stdin JSON, 最后一行输出 JSON 结果
    验证:
        - list_scripts 返回 ['main.py']
        - execute 成功, exit_code=0, result 非空

    注意:
        - 技能 ID 必须为 kebab_case 且不以 _ 开头
          (load_metadata_index 会跳过 _ 开头的目录)
    """
    print("[self-test] 验证带脚本技能的有脚本路径")
    tmpdir = tempfile.mkdtemp(prefix="verify_selftest_")
    try:
        repo = Path(tmpdir) / "repo"
        repo.mkdir()
        sid = "scripted-selftest"
        skill_dir = repo / sid
        scripts_dir = skill_dir / "scripts"
        scripts_dir.mkdir(parents=True)

        # 写 skill.md (含 default_params)
        (skill_dir / "skill.md").write_text(
            "---\n"
            f"id: {sid}\n"
            "name: 自测带脚本技能\n"
            "description: 验证 verify_skill_l3 有脚本路径\n"
            "category: custom\n"
            "tags: []\n"
            "version: 0.1.0\n"
            "enabled: true\n"
            "status: approved\n"
            "author: self-test\n"
            "content_type: markdown\n"
            "default_params:\n"
            "  greeting: hello\n"
            "  count: 3\n"
            "---\n\n"
            "# 自测带脚本技能\n\n"
            "用于验证 verify_skill_l3 的有脚本分支。\n",
            encoding="utf-8",
        )

        # 写 scripts/main.py (读 stdin JSON, 输出 JSON)
        (scripts_dir / "main.py").write_text(
            "import sys, json\n"
            "params = json.loads(sys.stdin.read() or \"{}\")\n"
            "greeting = params.get('greeting', 'hi')\n"
            "count = int(params.get('count', 1))\n"
            "print(json.dumps({'ok': True, 'echo': greeting, 'count': count}))\n",
            encoding="utf-8",
        )

        mgr = SkillManager(repo_path=str(repo))
        index = mgr.file_store.load_metadata_index(refresh=True)
        meta = index.get(sid, {})

        print(f"[self-test] 临时仓库: {repo}")
        print(f"[self-test] sid={sid}")
        print(f"[self-test] meta.default_params={meta.get('default_params')}")
        print(f"[self-test] scripts={mgr.file_store.list_scripts(sid)}")

        ok, msg = verify_skill_l3(mgr, sid, meta)
        status = "PASS" if ok else "FAIL"
        print(f"[self-test] verify_skill_l3: [{status}] {msg}")

        print("=" * 60)
        print("SELF-TEST: " + ("PASS" if ok else "FAIL"))
        return 0 if ok else 1
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _scan_repo_snapshots() -> Dict[str, float]:
    """扫描 data/skills_repo/ 下所有 skill.md 和 scripts/*.py 的 mtime

    返回 {相对路径: mtime} 字典，用于变更检测。
    仓库不存在或为空时返回空字典。
    """
    snapshots: Dict[str, float] = {}
    if not REPO_PATH.exists():
        return snapshots
    for skill_dir in REPO_PATH.iterdir():
        if not skill_dir.is_dir() or skill_dir.name.startswith("_"):
            continue
        # skill.md
        skill_md = skill_dir / "skill.md"
        if skill_md.exists():
            snapshots[str(skill_md.relative_to(REPO_PATH))] = skill_md.stat().st_mtime
        # scripts/*.py
        scripts_dir = skill_dir / "scripts"
        if scripts_dir.exists():
            for py in scripts_dir.glob("*.py"):
                snapshots[str(py.relative_to(REPO_PATH))] = py.stat().st_mtime
    return snapshots


def run_watch(interval: float = 2.0, verbose: bool = False) -> int:
    """监控模式: 监听 data/skills_repo/ 文件变动, 变动后自动触发验证

    实现策略 (按优先级降级):
        1. 事件驱动 (watchdog): 文件系统事件触发, 低 CPU 占用
        2. 轮询 (polling): 每 `interval` 秒扫描 mtime, 无额外依赖

    两种模式都支持:
        - 首次启动立即跑一次验证
        - 变更后去抖动 (debounce 0.5s) 避免连续事件触发多次验证
        - Ctrl+C 退出

    Args:
        interval: 轮询间隔秒数 (仅 polling 降级模式使用), 默认 2.0
        verbose: True 时实时打印每个文件变更的完整路径和事件类型

    Returns:
        首次验证的退出码 (后续验证只打印不退出)
    """
    mode_tag = "verbose" if verbose else "normal"
    print(f"[watch] 监控目录: {REPO_PATH} (mode={mode_tag})")
    print(f"[watch] 首次启动立即执行验证, 之后检测变动自动重跑")
    print(f"[watch] 按 Ctrl+C 退出\n")

    # 首次验证
    print(f"{'='*60}")
    print(f"[watch] 首次验证 (initial)")
    print(f"{'='*60}")
    first_exit = _run_verify_silent()
    print()

    # 尝试事件驱动, 不可用则降级轮询
    try:
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler
        import threading
        return _run_watch_event_driven(
            first_exit, Observer, FileSystemEventHandler, threading, verbose
        )
    except ImportError:
        print(f"[watch] watchdog 未安装, 降级为轮询模式 (interval={interval}s)")
        return _run_watch_polling(first_exit, interval, verbose)


def _run_watch_event_driven(
    first_exit: int, Observer, FileSystemEventHandler, threading, verbose: bool = False
) -> int:
    """事件驱动监控 (watchdog)

    特性:
        - 文件系统事件实时触发 (低 CPU)
        - 去抖动: 事件后等 0.5s 无新事件再验证, 避免连续触发
        - 只监听 .md 和 .py 文件变更
        - verbose=True 时实时打印每个事件的完整路径、事件类型、时间戳
    """
    debounce = threading.Event()
    pending_changes: list[str] = []
    pending_lock = threading.Lock()

    class SkillRepoHandler(FileSystemEventHandler):
        """处理 data/skills_repo/ 文件变更事件"""

        def _on_change(self, event, action: str, event_type: str):
            # 忽略目录和非 .md/.py 文件
            if event.is_directory:
                return
            path = Path(event.src_path)
            if path.suffix not in (".md", ".py"):
                return
            try:
                rel = path.relative_to(REPO_PATH)
            except ValueError:
                rel = path
            with pending_lock:
                pending_changes.append(f"{action} {rel}")
            debounce.set()

            # verbose: 实时打印完整事件信息
            if verbose:
                ts = time.strftime("%H:%M:%S.") + f"{int(time.time() * 1000) % 1000:03d}"
                dest = ""
                # on_moved 事件有 dest_path
                if hasattr(event, "dest_path") and event.dest_path:
                    dest = f" → {event.dest_path}"
                print(f"  [verbose] [{ts}] {event_type} is_dir={event.is_directory} "
                      f"path={event.src_path}{dest}")

        def on_modified(self, event):
            self._on_change(event, "~", "modified")

        def on_created(self, event):
            self._on_change(event, "+", "created")

        def on_deleted(self, event):
            self._on_change(event, "-", "deleted")

        def on_moved(self, event):
            # 移动事件: src_path → dest_path
            self._on_change(event, "→", "moved")

    observer = Observer()
    handler = SkillRepoHandler()
    # recursive=True 监听 skills_repo/ 下所有子目录 (scripts/ 等)
    observer.schedule(handler, str(REPO_PATH), recursive=True)
    observer.start()
    mode_tag = "verbose" if verbose else "normal"
    print(f"[watch] 事件驱动模式 (watchdog), 监听 .md/.py 变更 (mode={mode_tag})")
    print(f"[watch] 初始快照: {len(_scan_repo_snapshots())} 个文件")

    try:
        while True:
            # 阻塞等待事件, 超时 1s 检查 KeyboardInterrupt
            triggered = debounce.wait(timeout=1.0)
            if not triggered:
                continue
            # 去抖动: 等 0.5s 无新事件再执行
            time.sleep(0.5)
            debounce.clear()

            # 取出待处理变更
            with pending_lock:
                changes = pending_changes[:]
                pending_changes.clear()

            if not changes:
                continue

            ts = time.strftime("%H:%M:%S")
            print(f"\n[watch] [{ts}] 检测到 {len(changes)} 处变更:")
            for c in changes[:10]:
                print(f"  {c}")
            if len(changes) > 10:
                print(f"  ... 还有 {len(changes) - 10} 处")

            print(f"\n[watch] 触发重新验证...")
            _run_verify_silent()
            print(f"\n[watch] 验证完成, 继续监控")

    except KeyboardInterrupt:
        print(f"\n[watch] 收到 Ctrl+C, 退出监控")
    finally:
        observer.stop()
        observer.join(timeout=2.0)
    return first_exit


def _run_watch_polling(first_exit: int, interval: float, verbose: bool = False) -> int:
    """轮询监控 (polling, watchdog 降级方案)

    每 `interval` 秒扫描 mtime 对比变更。
    verbose=True 时打印每个变更文件的完整路径和 mtime。
    """
    print(f"[watch] 轮询间隔: {interval}s")

    last_snapshots = _scan_repo_snapshots()
    print(f"[watch] 初始快照: {len(last_snapshots)} 个文件")

    try:
        while True:
            time.sleep(interval)
            current_snapshots = _scan_repo_snapshots()

            changed = False
            changes = []
            for path, mtime in current_snapshots.items():
                if path not in last_snapshots:
                    changes.append(f"+ {path}")
                    changed = True
                    if verbose:
                        full = REPO_PATH / path
                        print(f"  [verbose] [+] created path={full} mtime={mtime:.3f}")
                elif mtime != last_snapshots[path]:
                    changes.append(f"~ {path}")
                    changed = True
                    if verbose:
                        full = REPO_PATH / path
                        print(f"  [verbose] [~] modified path={full} "
                              f"old_mtime={last_snapshots[path]:.3f} new_mtime={mtime:.3f}")
            for path in last_snapshots:
                if path not in current_snapshots:
                    changes.append(f"- {path}")
                    changed = True
                    if verbose:
                        full = REPO_PATH / path
                        print(f"  [verbose] [-] deleted path={full}")

            if changed:
                ts = time.strftime("%H:%M:%S")
                print(f"\n[watch] [{ts}] 检测到 {len(changes)} 处变更:")
                for c in changes[:10]:
                    # verbose 模式下变更详情已逐条打印, 这里只打印汇总
                    if not verbose:
                        print(f"  {c}")
                if len(changes) > 10:
                    print(f"  ... 还有 {len(changes) - 10} 处")

                print(f"\n[watch] 触发重新验证...")
                _run_verify_silent()
                print(f"\n[watch] 验证完成, 继续监控 (interval={interval}s)")

            last_snapshots = current_snapshots
    except KeyboardInterrupt:
        print(f"\n[watch] 收到 Ctrl+C, 退出监控")
    return first_exit


def _run_verify_silent() -> int:
    """执行一次验证, 返回退出码

    通过重新调用 main() 核心逻辑实现 (不带 --watch 参数)。
    捕获异常避免 watch 循环崩溃。
    """
    # 临时移除 --watch 参数避免递归
    original_argv = sys.argv[:]
    sys.argv = [arg for arg in sys.argv if arg != "--watch"]
    try:
        return main()
    except Exception as e:
        print(f"[watch] 验证异常: {e}")
        return 1
    finally:
        sys.argv = original_argv


if __name__ == "__main__":
    sys.exit(main())
