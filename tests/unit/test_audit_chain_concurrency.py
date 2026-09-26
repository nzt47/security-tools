"""审计链并发写压测（CONC 卡）——回答「多进程并发 append 会不会丢记录」

【本卡补的是哪一个留白】
    `docs/audit_skill_governance/VERIFICATION_LOG.md` 的
    「D3 上报的 `lock.degraded / seq_conflict`」一节把结论钉在「**这次没丢**」，
    并显式登记为待验证面：
        「并发写审计链（≥4 进程 × 大记录数）下 seq 是否出现缺口 /
          `lock.degraded` 频率」
    本文件是那张卡**可进 CI 的轻量版**；重量版（多进程矩阵 + 延迟分位 + 吞吐）
    是 `scripts/audit_concurrency_probe.py`。

【测什么，为什么必须真多进程】
    N 个**真进程**（`multiprocessing` spawn，不是线程——线程共用同一把进程内锁与
    同一份内存链头，**根本走不到**跨进程 seq 分配路径）同时 `append()` 大量记录，
    然后核对：
      · 总数守恒：DB 行数 == N × 每进程条数；
      · **seq 连续无缺口**：库内 seq 恰好是 `1..总数`（本卡的核心断言）；
      · 无重复 seq（重复会直接撞 `UNIQUE constraint failed: audit_chain.seq`）；
      · `verify_chain()` 通过（两级哈希 + prev_hash 链接 + seq 连续性）；
      · 每个子进程上报的降级事件——插桩 `AuditChain._note_degraded` **逐次**计数，
        而不是只看被 30s 节流后的留痕条数（留痕条数会**低报**真实降级次数）。

【安全保证：绝不碰生产库（本卡铁律，代码级而非口头级）】
    1. `conc_env` 夹具在**构造路径时**就硬断言「DB 在系统临时目录之下」且
       「不在仓库 `data/audit/` 之下」；子进程打开库**之前**再断言一次
       （纵深防御：路径若被改，失败发生在压测开始前，而不是压测之后）。
    2. 把 `AUDIT_DB_PATH` / `AUDIT_ROOTS_PATH` 指向该临时目录。
       **这不是可选项**：审计链降级留痕走
       `notify_degraded` → `agent.audit.facade.record`，而门面的路径口径是
       `db_path or os.getenv("AUDIT_DB_PATH") or DEFAULT_DB_PATH`（见 facade.py:198）
       ⇒ 不设这个变量，一次锁降级就会把留痕写进**生产审计链**。
    3. 子进程用 `set_notify_hook` 的**计数钩子**替换默认留痕：既精确计数，
       又避免「留痕自己去取锁 + 写库」给**被测对象**再加一层竞争与 IO 噪声。
    4. 用例前后对生产库做**只读 action 签名查询**（`conc.write` / `conc.probe`
       是本卡独有的 action，生产库里出现即污染），并另记 `(size, mtime_ns)` 作参考。
       刻意**不**拿 `(size, mtime_ns)` 当判据：本机常驻的 `python app_server.py` 会
       持续正常写生产链，实测把文件级判据打成假红（3 连跑红 2 次）；
       也刻意不用 `git status -- data/`：`data/audit/` 被 gitignore，改了也是空。

【为什么不 flaky（稳定性处理，逐条对应）】
    · **负载完全确定**：进程数/每进程条数都是常量，不使用随机数 ⇒ 不需要固定 seed；
    · **就绪栅栏（最关键的一条）**：全部子进程先构造完链、报"就绪"，父进程才放
      发令枪 ⇒ 任何进程开始 append 时预留日志都是空的。实测理由：不这样做时，
      后构造的进程会在 `_load_state` 撞上"日志头 > DB 头"（别人的**在途**记录），
      触发启动期收敛**重复插入** ⇒ `UNIQUE constraint failed` ⇒ 单次用例从 2.4s
      涨到 43s（实测 3 连跑红 2 次）。那个"新实例在别人正在写时启动"的场景由
      `scripts/audit_concurrency_probe.py --mode join` 专门覆盖，本用例钉的是
      **稳态并发追加**契约——两者刻意分开，CI 才不会变成随机红；
    · **结果通道用独占文件**（一进程一文件），不用 `Queue`/pipe：父进程只做
      「读文件 + `join(timeout)`」，不受管道缓冲或沙箱 `stdio: pipe` 限制影响；
    · **全链路显式超时**：子进程 join 上界、`flush(timeout)` 上界、发令枪/就绪等待
      上界，且用例内部自带上界（`_CHILD_DEADLINE_S`）**远小于** pytest 的 `--timeout`——
      `pytest.ini` 用 `--timeout-method=thread`，超时会 `os._exit(1)` **杀掉整个 pytest
      进程**，所以用例必须自己兜住，不能指望 pytest 兜；
    · **重试口径克制**：只在「子进程异常 / 退出码非 0 / 未退出 / 未全部就绪」这类
      **瞬态**失败上重跑一次，且每次重跑用**独立的新库**；
      **一旦发现 seq 缺口或重复立即失败，绝不重试**——那正是本卡要抓的真相。
    · **不拿 `flush()` 的返回值当瞬态失败**：实测并发下它是**屏障假阴性**
      （冲突把它扣成"未落盘"，而记录其实已被别的进程写进 DB），拿它重试只会白等；
      真正判据是库内的 行数 / seq 连续性 / 无重号。
"""

from __future__ import annotations

import json
import multiprocessing
import os
import sqlite3
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.p2]

#: 仓库根（tests/unit/<本文件> → parents[2]）
_REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
#: 生产审计库：**只做只读前后对照**，任何用例都不得写入
PROD_DB = os.path.join(_REPO_ROOT, "data", "audit", "audit_chain.db")

#: 子进程退出上界（Windows spawn + 解释器启动远慢于 POSIX）
#:
#: 【为什么是 120 而不是 90（实测踩到的边界）】子进程收尾会调用**两次**带超时的
#: 持久化屏障：flush(timeout) + close(timeout)（close 内部还会再 flush 一次），
#: 所以单个子进程的最坏收尾耗时是 2 × _FLUSH_TIMEOUT_S。上界必须严格大于它，
#: 否则会把"flush 屏障假阴性导致收尾慢"误判成"子进程挂死"——实测：90s 截止线
#: 正好卡在 45+45 上，子进程被 terminate，退出码 -15。
_CHILD_DEADLINE_S = 120.0
#: 子进程内 `flush()` / `close()` 的等待上界（见上：最坏收尾 = 2×本值）
_FLUSH_TIMEOUT_S = 20.0
#: 发令枪等待上界（父进程若异常早退，子进程也不会永久挂住）
_GATE_TIMEOUT_S = 60.0
#: CI 轻量档（验收要求的那一档）：2 进程 × 50 条
_CI_PROCS = 2
_CI_PER_PROC = 50
#: 中并发档（覆盖「≥4 进程」口径）：4 进程 × 50 条
_MID_PROCS = 4
_MID_PER_PROC = 50


# ════════════════════════════════════════════════════════════
#  安全护栏：只在系统临时目录下的假库上压测
# ════════════════════════════════════════════════════════════


def _norm(path: str) -> str:
    """路径归一（Windows 大小写不敏感，比较前统一 `normcase`）"""
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _inside(child: str, parent: str) -> bool:
    """`child` 是否在 `parent` 之下（跨盘符返回 False，不抛异常）"""
    try:
        return os.path.commonpath([_norm(child), _norm(parent)]) == _norm(parent)
    except ValueError:
        return False


def _temp_roots(extra: Sequence[str] = ()) -> List[str]:
    r"""本环境下**可接受的临时根**清单（去重；被别的根包含的冗余项会被剔除）

    【为什么不能只用 `tempfile.gettempdir()`（实测踩到的坑）】
    `tests/conftest.py::_safe_tmp_directory`（session 级 autouse）把
    `tempfile.tempdir` 重定向到**项目内**的 `<repo>/.pytest_tmp`；而 pytest 的
    `tmp_path_factory` 在本进程启动时**已经**用真实系统临时目录算好了 basetemp
    ⇒ 两者并不相等。实测：
        `tmp_path      = C:\Windows\Temp\pytest-of-AdminWT\pytest-5298\...`
        `gettempdir()  = C:\Users\Administrator\agent\.pytest_tmp`
    只认后者会把**合法的 pytest 临时目录**误判成"不是临时目录"，三条用例直接 error
    （实测：`AssertionError: 审计库必须位于系统临时目录之下` ×3）。
    故这里把「环境变量 TEMP/TMP/TMPDIR + `tempfile.gettempdir()` + 调用方显式传入的
    `tmp_path`」**都**算作临时根。
    """
    roots: List[str] = []
    for cand in list(extra) + [tempfile.gettempdir(),
                               os.environ.get("TEMP", ""),
                               os.environ.get("TMP", ""),
                               os.environ.get("TMPDIR", "")]:
        if cand:
            roots.append(os.path.abspath(os.fspath(cand)))
    uniq: List[str] = []
    for cand in roots:                      # 只保留"最外层"的根，避免清单膨胀
        if not any(_inside(cand, kept) for kept in uniq):
            uniq.append(cand)
    return uniq


def assert_tmp_db_path(path: str, *, allowed_roots: Sequence[str] = ()) -> str:
    """硬断言：审计库必须在**临时根**之下，且**绝不在生产审计目录**里

    父进程（构造路径时）与子进程（打开库之前）**都**调用它——这是本卡
    「绝不对生产库做并发压测」的代码级保证，而不是靠人记得。

    判定分两层，顺序不可颠倒：
      ① **绝对禁止面**：生产库本体 / 生产审计目录 `<repo>/data/audit/` /
         仓库 `<repo>/data/` 之下 —— 无条件拒绝（哪怕它同时位于某个临时根下）；
      ② **必须落在某个临时根之下**（见 `_temp_roots` 的环境说明）。
    注意：`<repo>/.pytest_tmp` 虽在仓库内，但它是 conftest 显式指定的临时目录，
    故被 ② 接受；`data/` 则被 ① 无条件拒绝。
    """
    ap = os.path.abspath(os.fspath(path))
    prod_dir = os.path.dirname(PROD_DB)
    data_dir = os.path.join(_REPO_ROOT, "data")
    assert _norm(ap) != _norm(PROD_DB), f"拒绝对生产审计库压测：{ap!r}"
    assert not _inside(ap, prod_dir), f"拒在生产审计目录下压测：{ap!r}"
    assert not _inside(ap, data_dir), f"拒在仓库 data/ 下压测：{ap!r}"

    roots = _temp_roots(allowed_roots)
    assert any(_inside(ap, root) for root in roots), (
        f"审计库必须位于临时根之下：{ap!r} 不在 {roots!r} 任何一个之内"
        "（本卡禁止在非临时目录做并发压测）")
    return ap


#: 本卡写入审计链时专用的 action（` 内是本卡独有的命名空间）
#: —— 生产库里出现它们，**只能**是本卡污染的（外部服务不会用这些 action）。
CARD_ACTIONS = ("conc.write", "conc.probe")


def _prod_stat() -> Optional[Tuple[int, int]]:
    """生产库的 `(size, mtime_ns)`（不存在返回 None）——**仅作参考信息**，不作断言

    【为什么不能拿它当断言（实测假红）】本机常驻的 `python app_server.py` 会持续
    往生产审计链追加（实测：本卡测量窗口内 sha256 变了 3 次、mtime 每次都变，
    而 size 不变——那是 WAL 侧的原地写）。拿它做判据会把**外部正常写入**误判成
    "本卡污染了生产库"，实测在 3 连跑里红了 2 次（2 errors）。
    真正可判别、且不受外部写入干扰的判据是 `_prod_card_rows()`（本卡 action 签名）。
    """
    try:
        st = os.stat(PROD_DB)
        return int(st.st_size), int(st.st_mtime_ns)
    except OSError:
        return None


def _prod_card_rows() -> Tuple[Optional[int], str]:
    """只读统计生产库里**属于本卡**的记录条数（本卡铁律的直接判据）

    为什么要按签名查而不是按 (size, mtime)：见 `_prod_stat` 的说明 ——
    外部常驻服务也在写这条链，文件级判据分不清"谁写的"；
    而 `action` 是**内容级**判据，外部服务不会写 `conc.write` / `conc.probe`。
    刻意不用 `git status -- data/`：`data/audit/` 被 gitignore，改了也是空。

    Returns:
        `(count, note)`；`count=None` 表示本次查询不可用（库不存在 / 被独占），
        此时**不判红**（只读断言取不到证据 ≠ 有污染），note 里写明原因。
    """
    if not os.path.exists(PROD_DB):
        return None, "生产库不存在（本机无生产链）"
    marks = ",".join(["?"] * len(CARD_ACTIONS))
    try:
        conn = sqlite3.connect(f"file:{PROD_DB}?mode=ro", uri=True, timeout=10.0)
        try:
            row = conn.execute(
                f"SELECT COUNT(*) FROM audit_chain WHERE action IN ({marks})",
                list(CARD_ACTIONS)).fetchone()
        finally:
            conn.close()
        return int(row[0]), ""
    except Exception as exc:  # noqa: BLE001 只读查询失败不判红（附原因）
        return None, f"只读查询失败: {type(exc).__name__}: {exc}"


# ════════════════════════════════════════════════════════════
#  子进程 worker（模块级函数：spawn 要求可按名导入）
# ════════════════════════════════════════════════════════════


def concurrency_worker(job: Dict[str, Any],
                       start_gate: Any) -> None:  # pragma: no cover - 子进程内执行
    """子进程：等发令枪 → 并发追加 count 条 → flush/close → 结果写文件

    结果一律写 `job["out"]` 这个**独占文件**（一个进程一个文件），父进程只做
    「读文件 + join」。刻意不用 `Queue`/pipe：管道在受限沙箱下可能被拒，
    文件通道没有这个不确定性。
    """
    result: Dict[str, Any] = {
        "tag": job["tag"], "seqs": [], "degrade": [], "notify": [],
        "lat_ms": [], "flush_ok": False, "error": "", "pid": os.getpid(),
    }
    try:
        # 子进程侧纵深防御：临时根由父进程显式传入，不依赖环境变量继承
        assert_tmp_db_path(job["db"], allowed_roots=job.get("temp_roots") or ())
        from agent.audit.chain import AuditChain
        from agent.utils import cross_process_lock as cpl

        cpl.set_notify_hook(
            lambda action, detail: result["notify"].append(
                {"action": str(action), "reason": str(detail.get("reason", ""))}))

        chain = AuditChain(job["db"], roots_path=job["roots"],
                           signing_enabled=False, auto_seal=False,
                           enforce_single_writer=False)
        orig_note = chain._note_degraded  # noqa: SLF001 运行期插桩，不改生产代码

        def _counting_note(reason: str, *, key: str = "") -> None:
            """逐次计数降级（`"_note_degraded"` 自身的留痕有 30s 节流）"""
            result["degrade"].append({"reason": str(reason), "key": str(key)})
            return orig_note(reason, key=key)

        chain._note_degraded = _counting_note  # type: ignore[method-assign]

        # 【就绪栅栏（本卡不 flaky 的关键）】链已构造完（= 启动期 `_load_state`
        # 已跑完）才报"就绪"。父进程等**全部**子进程就绪后才放发令枪 ⇒ 任何进程
        # 开始 append 时，预留日志都还是空的。
        # 为什么必须这样（实测）：若让各进程"谁先构造完谁先写"，后构造的进程会在
        # `_load_state` 里看到"日志头 > DB 头"（别的进程**在途**的记录）⇒ 置
        # `_journal_needs_drain` ⇒ 收敛时把那批在途记录**再插一遍** ⇒ 拥有者稍后
        # 插入时撞 `UNIQUE constraint failed: audit_chain.seq` ⇒ 一整批进 ring buffer
        # ⇒ 单次用例耗时从 2.4s 涨到 43s（实测 3 连跑里 2 次）。
        # 那是"新实例在别人正在写时启动"这一**独立场景**，由
        # `scripts/audit_concurrency_probe.py --mode join` 专门覆盖；本 CI 用例钉的是
        # **稳态并发追加**契约，两者刻意分开，避免把 CI 变成随机红。
        try:
            with open(job["out"] + ".ready", "w", encoding="utf-8") as fh:
                fh.write("ready")
        except OSError:
            pass          # 报就绪失败不该让用例挂住：父进程超时后照样会放枪

        if not start_gate.wait(timeout=float(job["gate_timeout"])):
            raise TimeoutError("未等到发令枪（父进程未启动？）")

        lat: List[float] = []
        for i in range(int(job["count"])):
            t0 = time.perf_counter()
            entry = chain.append("conc.write", actor=f"actor-{job['tag']}",
                                 subject=f"subject-{job['tag']}",
                                 payload={"tag": job["tag"], "i": i})
            lat.append((time.perf_counter() - t0) * 1000.0)
            result["seqs"].append(int(entry.seq))
        result["lat_ms"] = lat
        result["flush_ok"] = bool(chain.flush(timeout=float(job["flush_timeout"])))
        chain.close(timeout=float(job["flush_timeout"]))
    except Exception as exc:  # noqa: BLE001 子进程异常必须回传，否则父进程只能看到超时
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            with open(job["out"], "w", encoding="utf-8") as fh:
                json.dump(result, fh, ensure_ascii=False)
        except Exception:  # noqa: BLE001 结果落盘失败 → 父进程按"缺文件"判为瞬态失败
            pass


# ════════════════════════════════════════════════════════════
#  夹具
# ════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True, scope="module")
def _prod_db_readonly_guard():
    """**模块级只读守卫**：本模块全部用例跑完后，生产库内**不得**出现本卡的记录

    为什么用 module 作用域夹具、而不是"写在文件末尾的用例"：
    本仓库启用了 `pytest-randomly`（实测收集期即打印 `Using --randomly-seed=...`），
    用例顺序会被打乱 —— 「靠定义顺序排在最后」是**不成立**的假设。
    模块作用域夹具的 teardown 一定在**本模块所有用例之后**执行，与顺序无关。

    判据是 `action 签名`（`_prod_card_rows`），不是 `(size, mtime_ns)`，
    也不是 `git status`：前者会被常驻 `app_server.py` 的正常写入打成假红（实测 3 连跑红 2 次），
    后者对 `data/audit/` 完全无效（被 gitignore，改了也是空）。
    """
    before, reason_before = _prod_card_rows()
    yield
    after, reason_after = _prod_card_rows()
    if after is None:
        return                      # 取不到证据 ≠ 有污染；原因见两条 reason
    assert after == 0, (
        f"生产审计库出现本卡写入的记录 {after} 条（action ∈ {CARD_ACTIONS}）——"
        f"本卡只允许打临时假库：before={before} after={after} path={PROD_DB}")
    assert after == before, (
        f"生产库本卡签名条数在用例期间变化：{before} → {after}"
        f"（排除不了的污染；note={reason_before or reason_after}）")


@pytest.fixture
def conc_env(tmp_path, monkeypatch):
    """并发压测环境：临时假库 + 环境隔离 + 生产库只读对照

    Returns:
        dict：`db` / `roots` / `run_dir` / `prod_before`
    """
    run_dir = tmp_path / "conc"
    run_dir.mkdir(parents=True, exist_ok=True)
    db = os.path.join(str(run_dir), "audit_chain.db")
    roots = os.path.join(str(run_dir), "daily_roots.jsonl")
    temp_roots = _temp_roots([str(tmp_path)])
    assert_tmp_db_path(db, allowed_roots=temp_roots)   # 构造即断言（父进程侧）

    # 门面（降级留痕的默认落点）也必须指向临时目录
    monkeypatch.setenv("AUDIT_DB_PATH", db)
    monkeypatch.setenv("AUDIT_ROOTS_PATH", roots)

    from agent.audit.chain import reset_audit_chains
    reset_audit_chains()
    prod_before, prod_note = _prod_card_rows()
    try:
        yield {"db": db, "roots": roots, "run_dir": str(run_dir),
               "temp_roots": temp_roots, "prod_before": prod_before,
               "prod_note": prod_note}
    finally:
        reset_audit_chains()
        # 生产库只读对照：并发压测**不得**往生产链写入本卡的记录（action 签名判据）。
        # 只在"用例本身没在抛异常"时断言：否则这条断言会**顶替**掉真正的失败原因
        # （finally 里抛出的异常会覆盖在途异常）；失败路径由 module 级守卫覆盖。
        if sys.exc_info()[0] is None and prod_before is not None:
            prod_after, _ = _prod_card_rows()
            assert prod_after == prod_before == 0, (
                f"生产审计库出现本卡记录：before={prod_before} after={prod_after}"
                f"（action ∈ {CARD_ACTIONS}）")


# ════════════════════════════════════════════════════════════
#  运行器 / 断言工具
# ════════════════════════════════════════════════════════════


def _run_once(env: Dict[str, Any], processes: int, per_process: int,
              attempt: int) -> Dict[str, Any]:
    """跑一轮 N 进程 × M 条，返回**原始统计**（不加工成结论）"""
    ctx = multiprocessing.get_context("spawn")
    gate = ctx.Event()
    # 【每轮一个**独立库**（实测踩到的坑）】首版让所有 attempt 共用 env["db"]：
    # 第一次 attempt 若被判为瞬态失败而重跑，第二轮会**接着已有链尾**继续分配，
    # DB 里于是同时存在两轮的记录（实测 4 进程用例读到 `实际DB行=400` 而预期 200），
    # 断言口径被自己污染。每轮独立目录后，"预期 == 实际"才是可复核的。
    run_dir = os.path.join(env["run_dir"], f"run{attempt}_p{processes}")
    os.makedirs(run_dir, exist_ok=True)
    db = assert_tmp_db_path(os.path.join(run_dir, "audit_chain.db"),
                            allowed_roots=env["temp_roots"])
    roots = os.path.join(run_dir, "daily_roots.jsonl")

    jobs: List[Dict[str, Any]] = [{
        "db": db, "roots": roots, "tag": f"p{i}",
        "count": per_process, "temp_roots": env["temp_roots"],
        "out": os.path.join(run_dir, f"result_p{i}.json"),
        "gate_timeout": _GATE_TIMEOUT_S,
        "flush_timeout": _FLUSH_TIMEOUT_S,
    } for i in range(processes)]

    procs = [ctx.Process(target=concurrency_worker, args=(job, gate))
             for job in jobs]
    for p in procs:
        p.start()

    # 【就绪栅栏】等**全部**子进程报"链已构造完"才放枪（见 worker 里的说明）：
    # 这样任何进程开始 append 时预留日志都是空的，不会撞上"启动期收敛在途记录"。
    ready_deadline = time.time() + _GATE_TIMEOUT_S
    ready_ok = False
    while time.time() < ready_deadline:
        if all(os.path.exists(job["out"] + ".ready") for job in jobs):
            ready_ok = True
            break
        if any(not p.is_alive() for p in procs):
            break
        time.sleep(0.02)
    gate.set()               # 就绪超时也照放：宁可失败在断言上，也不要挂死在等待里

    deadline = time.time() + _CHILD_DEADLINE_S
    for p in procs:
        p.join(timeout=max(1.0, deadline - time.time()))
    alive = [p for p in procs if p.is_alive()]
    for p in alive:          # 兜底：绝不留孤儿进程拖垮后续用例
        p.terminate()
        p.join(timeout=20.0)

    results: List[Dict[str, Any]] = []
    for job in jobs:
        try:
            with open(job["out"], "r", encoding="utf-8") as fh:
                results.append(json.load(fh))
        except Exception as exc:  # noqa: BLE001 缺文件 = 子进程没走到收尾
            results.append({"tag": job["tag"], "seqs": [], "degrade": [],
                            "notify": [], "lat_ms": [], "flush_ok": False,
                            "error": f"结果文件不可读: {type(exc).__name__}: {exc}"})

    return {
        "processes": processes, "per_process": per_process,
        "expected": processes * per_process,
        "exitcodes": [p.exitcode for p in procs],
        "alive": len(alive),
        "ready_ok": ready_ok,
        "results": results,
        "db": db,
        "elapsed_s": None,
    }


def _transient_failures(run: Dict[str, Any]) -> List[str]:
    """**瞬态**失败（可重试）：子进程异常 / 退出码非 0 / 未退出 / 结果文件缺失

    刻意**不包含**这三类，它们都是本卡的**结论本身**或**已实测的常态**，绝不重试：
      · 「seq 缺口」「重复 seq」——丢了就必须如实报出来；
      · `flush_ok=False`——实测在并发下它是**屏障假阴性**（见下），不是挂死。
    """
    bad: List[str] = []
    if not run.get("ready_ok"):
        bad.append(f"子进程未在 {_GATE_TIMEOUT_S}s 内全部报就绪（无法保证「稳态并发」口径）")
    if run["alive"]:
        bad.append(f"{run['alive']} 个子进程未在上界内退出")
    for code in run["exitcodes"]:
        if code != 0:
            bad.append(f"子进程退出码 {code}")
    for r in run["results"]:
        if r.get("error"):
            bad.append(f"{r.get('tag')}: {r['error']}")
    return bad


def _run_matrix(env: Dict[str, Any], processes: int, per_process: int,
                attempts: int = 2) -> Dict[str, Any]:
    """跑一轮，**仅**在瞬态失败上重试（最多 attempts 次）"""
    last: Dict[str, Any] = {}
    for attempt in range(1, attempts + 1):
        t0 = time.time()
        run = _run_once(env, processes, per_process, attempt)
        run["elapsed_s"] = time.time() - t0
        run["transient"] = _transient_failures(run)
        if not run["transient"]:
            return run
        last = run
    return last


def _db_seqs(db_path: str) -> List[int]:
    """直读临时库取**全部** seq（升序）——判据用原始行，不用被测模块的自述"""
    conn = sqlite3.connect(db_path, timeout=10.0)
    try:
        rows = conn.execute("SELECT seq FROM audit_chain ORDER BY seq").fetchall()
    finally:
        conn.close()
    return [int(r[0]) for r in rows]


def _gaps(seqs: List[int]) -> List[Tuple[int, int]]:
    """相邻缺口列表 `(前一条, 后一条)`；空列表 = 连续"""
    return [(a, b) for a, b in zip(seqs, seqs[1:]) if b != a + 1]


def _duplicates(seqs: List[int]) -> List[int]:
    """重复出现的 seq（升序）"""
    seen: set = set()
    dup: set = set()
    for s in seqs:
        if s in seen:
            dup.add(s)
        seen.add(s)
    return sorted(dup)


def _degrade_summary(run: Dict[str, Any]) -> Dict[str, int]:
    """按 reason 归类**全部**子进程的降级事件（逐次计数，非节流后留痕数）"""
    summary: Dict[str, int] = {}
    for r in run["results"]:
        for ev in r.get("degrade") or []:
            key = str(ev.get("reason", "")).split("（")[0].strip()[:60]
            summary[key] = summary.get(key, 0) + 1
    return summary


def _report(run: Dict[str, Any], label: str) -> str:
    """把一轮的原始统计拼成一行可复核的结论串（失败信息里带它，便于定位）

    刻意做**失败安全**：本函数只在断言失败时构造消息，若它也抛异常，
    真正的失败原因就会被吞掉——那会把"定位线索"变成"第二个谜题"。
    """
    try:
        seqs = _db_seqs(run["db"])
    except Exception as exc:  # noqa: BLE001
        seqs = []
        label = f"{label}; DB 不可读({type(exc).__name__})"
    alloc = sorted(s for r in run["results"] for s in r["seqs"])
    return (
        f"[{label}] 进程={run['processes']} 每进程={run['per_process']} "
        f"预期={run['expected']} 实际DB行={len(seqs)} "
        f"seq范围=({seqs[0] if seqs else 0}..{seqs[-1] if seqs else 0}) "
        f"缺口={_gaps(seqs)} 重复={_duplicates(seqs)} "
        f"分配总数={len(alloc)} 分配重复={_duplicates(alloc)} "
        f"降级={_degrade_summary(run)} 退出码={run['exitcodes']} "
        f"flush全确认={all(r.get('flush_ok') for r in run['results'])} "
        f"耗时={run['elapsed_s']:.2f}s"
    )


def _assert_chain_intact(run: Dict[str, Any], label: str) -> Dict[str, Any]:
    """核心断言：无瞬态失败、总数守恒、**seq 连续无缺口**、无重复、链可验

    Returns:
        dict：`seqs` / `degrades` / `verify`（供用例追加断言或打印）
    """
    assert not run["transient"], (
        f"[{label}] 瞬态失败: {run['transient']}；原始统计: {_report(run, label)}")

    seqs = _db_seqs(run["db"])
    expected = int(run["expected"])

    assert len(seqs) == expected, (
        f"[{label}] 总数不守恒：预期 {expected}，DB 实有 {len(seqs)}"
        f"；{_report(run, label)}")
    assert _duplicates(seqs) == [], (
        f"[{label}] DB 内出现重复 seq；{_report(run, label)}")
    assert _gaps(seqs) == [], (
        f"[{label}] DB 内 seq 出现缺口；{_report(run, label)}")
    assert seqs == list(range(1, expected + 1)), (
        f"[{label}] seq 不是 1..{expected} 的精确排列；{_report(run, label)}")

    # 分配侧（子进程自报）也不得重号：重号是 UNIQUE 冲突的上游
    alloc = sorted(s for r in run["results"] for s in r["seqs"])
    assert _duplicates(alloc) == [], (
        f"[{label}] 跨进程 seq 分配出现重号：{_duplicates(alloc)}"
        f"；{_report(run, label)}")
    assert alloc == list(range(1, expected + 1)), (
        f"[{label}] 分配序列不是 1..{expected} 的精确排列；{_report(run, label)}")

    # 链级校验：哈希链接 + seq 连续性
    from agent.audit.chain import AuditChain
    chain = AuditChain(run["db"], roots_path=os.path.join(
        os.path.dirname(run["db"]), "daily_roots.jsonl"),
        role="reader", signing_enabled=False, auto_seal=False,
        enforce_single_writer=False)
    try:
        verification = chain.verify_chain()
    finally:
        chain.close(timeout=5.0)
    assert verification.ok, (
        f"[{label}] verify_chain 未通过：{verification.summary()}"
        f"；{_report(run, label)}")

    return {"seqs": seqs, "degrades": _degrade_summary(run),
            "verify": verification}


# ════════════════════════════════════════════════════════════
#  用例 1（验收命令点名的那一条）：2 进程 × 50 条 → seq 连续无缺口
# ════════════════════════════════════════════════════════════


@pytest.mark.timeout(300)
def test_two_processes_50_each_seq_contiguous(conc_env):
    """**CI 轻量档**：2 进程各 50 条并发追加 → seq 恰好 1..100，无缺口/无重复/链可验

    断言的是**完整性**，不是"不降级"。这个分寸是**实测定的**，不是拍的：
    本卡第一次跑这一档时，`_note_degraded` 上报了 2 次
    `seq_conflict: UNIQUE constraint failed: audit_chain.seq` —— 也就是说
    「2 进程并发不会重号」这个假设**当场被证伪**（原因与最小复现见
    `docs/audit_skill_governance/CONC.md` 与 `scripts/audit_concurrency_probe.py`）。
    但同一轮里 100 条记录**一条不少**、seq 恰好 1..100、`verify_chain()` 通过 ——
    ⇒ 这才是本卡真正要钉住的性质：**降级是"安全地退回"，不是"悄悄丢"**。

    故本用例硬断言：
      · `_assert_chain_intact`：总数守恒 / seq 连续无缺口 / 无重复 / 链可验；
      · 降级计数与 seq 完整性**同时**成立（前者是观测，后者是契约）。
    刻意**不**断言"降级次数为 0"：那是把已被实测证伪的假设写成回归网，
    结果只会是一个必然 flaky 的用例。
    """
    run = _run_matrix(conc_env, _CI_PROCS, _CI_PER_PROC)
    info = _assert_chain_intact(run, "2进程×50")
    assert len(info["seqs"]) == _CI_PROCS * _CI_PER_PROC, (
        f"2 进程 × {_CI_PER_PROC} 条应得 {_CI_PROCS * _CI_PER_PROC} 行，"
        f"实得 {len(info['seqs'])}；降级={info['degrades']}")


# ════════════════════════════════════════════════════════════
#  用例 2：4 进程 × 50 条 → 覆盖验收的「≥4 进程」口径
# ════════════════════════════════════════════════════════════


@pytest.mark.timeout(300)
def test_four_processes_50_each_seq_contiguous(conc_env):
    """**中并发档**：4 进程各 50 条并发追加 → seq 恰好 1..200，无缺口/无重复

    比用例 1 多压一倍并发：锁争用显著上升，是「边界在哪」的第一个探针。
    """
    run = _run_matrix(conc_env, _MID_PROCS, _MID_PER_PROC)
    info = _assert_chain_intact(run, "4进程×50")
    assert len(info["seqs"]) == _MID_PROCS * _MID_PER_PROC


# ════════════════════════════════════════════════════════════
#  用例 3：护栏自证 —— 生产库路径会被硬拒（保证"绝不压测生产库"不是空话）
# ════════════════════════════════════════════════════════════


def test_guard_rejects_production_db_path(tmp_path):
    """护栏自证：生产库路径与仓库内路径都必须被 `assert_tmp_db_path` 拒绝

    这条用例让「只在临时目录压测」这条纪律**可被回归**：若有人把护栏删了/改松了，
    这里会红；同时正例说明 pytest 的 `tmp_path` 确实落在护栏认的临时根之下
    （否则夹具会在构造阶段就把自己锁死）。
    """
    roots = _temp_roots([str(tmp_path)])
    with pytest.raises(AssertionError):
        assert_tmp_db_path(PROD_DB, allowed_roots=roots)      # 生产库本体
    with pytest.raises(AssertionError):
        assert_tmp_db_path(os.path.join(os.path.dirname(PROD_DB), "x.db"),
                           allowed_roots=roots)               # 生产审计目录
    with pytest.raises(AssertionError):
        assert_tmp_db_path(os.path.join(_REPO_ROOT, "audit_chain.db"),
                           allowed_roots=roots)               # 仓库内非临时路径
    with pytest.raises(AssertionError):
        assert_tmp_db_path(os.path.join(_REPO_ROOT, "data", "elsewhere.db"),
                           allowed_roots=roots)               # 仓库 data/ 之下
    # 正例：pytest 给的临时目录必须放行（否则夹具会在构造阶段把自己锁死）
    assert assert_tmp_db_path(str(tmp_path / "audit_chain.db"), allowed_roots=roots)


# ════════════════════════════════════════════════════════════
#  用例 4：只读断言 —— 本文件跑完后生产库原封不动
# ════════════════════════════════════════════════════════════


def test_production_db_untouched_by_this_module():
    """**只读断言（可见证据版）**：生产库里没有本卡写过的任何一条记录

    判据是 `action` **签名**（`_prod_card_rows`：`conc.write` / `conc.probe`），
    不是 `(size, mtime_ns)`、也不是 `git status`：
      · `git status -- data/` 对 `data/audit/` **完全无效**（被 gitignore，恒为空）；
      · `(size, mtime_ns)` 会被本机常驻 `app_server.py` 的正常写入打成**假红**
        （实测：3 连跑里红了 2 次，size 根本没变、只有 mtime 变）。
    内容级签名则只认"本卡写的那些 action"——外部服务不会写它们。

    口径（诚实说明）：`pytest-randomly` 会打乱用例顺序，本条**不能**保证
    "排在本模块所有并发用例之后"；完整覆盖由 autouse 的 `_prod_db_readonly_guard`
    （module 作用域 teardown，必然在所有用例之后）承担。本条的价值是把同一事实
    变成 pytest 输出里**肉眼可见的一行 PASS**。
    """
    count, note = _prod_card_rows()
    if count is None:
        pytest.skip(f"生产库只读查询不可用，本机无法取得该证据：{note}")
    assert count == 0, (
        f"生产审计库出现本卡写入的记录 {count} 条（action ∈ {CARD_ACTIONS}）："
        f"并发压测只允许打临时假库；path={PROD_DB}，参考 (size, mtime_ns)={_prod_stat()}")
