"""
pytest配置文件
云枢(Yunshu)系统自动化测试框架 - conftest.py

提供：
- 测试fixtures（测试数据和依赖注入）
- pytest钩子函数（测试执行生命周期管理）
- 测试数据管理策略
- 测试环境配置
"""

import os
import sys
import json
import copy
import time
import atexit
import shutil
import tempfile
import pytest
from unittest.mock import Mock

# ── Windows GBK 编码兼容：避免 emoji 日志吐乱码 ──
# 注释：不要在这里 reconfig stdout/stderr，会导致 pytest 的 capture 模块冲突
# 改用 PYTHONIOENCODING=utf-8 环境变量或直接在调用时设置
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional
import logging

# ── 测试环境变量固化（P1 污染治理，2026-08-31）──
# 说明：此处仅 setdefault（不覆盖用户显式配置），供测试内 spawn 的子进程继承。
# - PYTHONUTF8=1 / PYTHONIOENCODING=utf-8：子进程 stdio 统一 UTF-8，规避中文
#   Windows GBK 下 subprocess 输出捕获的 UnicodeDecodeError（conftest 顶部注释
#   记载的方案在此正式落地；当前进程 stdout 保持不动，避免与 pytest capture 冲突）。
# - OMP_NUM_THREADS / MKL_NUM_THREADS：限制 torch/numpy 线程数，规避 Windows
#   上 C 扩展线程竞争导致的 0xC0000005 崩溃（与 README「Docker 部署」记载一致）。
os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("OMP_NUM_THREADS", "4")
os.environ.setdefault("MKL_NUM_THREADS", "4")

# ════════════════════════════════════════════════════════════
#  【TESTHYG-1 · 2026-09-26】`.env` 重定向的**会话级地板**（必须早于任何夹具）
# ════════════════════════════════════════════════════════════
# 现象：全量 22637 条里 5 条红、**单跑全绿** ——
#   test_settings_registry.py::TestConfigPathDrivesDisplayNotRuntime 的
#     test_no_declared_key_is_env_pinned_here / test_every_declared_path_flips_default_to_config、
#   test_error_reporting_config.py::TestErrorReportingConfig::test_get_config_default、
#   test_evolver_real_eval.py::TestRealEvalDistinguishable（×2）。
#
# 根因（**脏写钩子实测调用栈**，不是推断）：
#   module 级夹具 `import app_server`
#     → `app_server.py:50`  `get_env_config_manager().reload()`
#     → `agent/env_config_manager.py:387`  `os.environ[k] = v`   ← **整份 .env**（实测 140 键）
#
# 为什么原有的重定向挡不住：`CP_ENV_FILE` 此前**只在函数级**夹具
#   `_isolate_dotenv_target` 里设置；而 pytest 的**高 scope 夹具先于低 scope 夹具**
#   setup ⇒ 任何 `scope="module"/"session"` 的夹具里 `import app_server` 时，
#   重定向尚未生效，manager 指向**仓库真实 `.env`**。
#   实测触发点（同一形状共 5 处，全部是 module 级夹具）：
#     test_server_routes_registration_inventory.py:153（real_url_paths）
#     test_graceful_shutdown_persist.py:28（app_server_mod）
#     test_health_retrieval_endpoint.py:31（flask_app）
#     test_legacy_memory_routes.py:94（real_app）
#     test_tool_callability.py:567（real_app）
#   灌进来的键里有 `EVOLUTION_DEFAULT_EVALUATOR=real`、
#   `ERROR_REPORTING_WEBHOOK_URL=https://hooks.slack.com/test`、
#   `ORCHESTRATOR_REJECT_ENABLED=false`、`SKILLS_FUSION_WEIGHT_BM25=0.2` —— 正是上述
#   用例"断言默认值/启发式路径"的判据 ⇒ **判据被换掉，与被测代码无关**。
#
# 修法（治本，不是掩盖）：把重定向**提前到 conftest 导入期**（早于收集、早于一切夹具），
#   于是 `reload()` 读到的是**空的隔离 .env** ⇒ **污染源自己不再写 os.environ**。
#   这与本文件既有的 `_isolate_dotenv_target`（"重定向真实 I/O，而不是 mock 掉写入"）
#   是同一口径，只是把生效时机从"逐用例"提前到"会话开始前"。
#   与函数级夹具的关系：本处是**地板**（保证任何时刻都不指向仓库 `.env`）；
#   函数级仍是**逐用例隔离**（用例之间互不串味），其还原目标即本值。
#
# 【为什么是无条件覆盖而不是 setdefault】不变量是"测试进程任何时刻都不得把仓库
#   `.env` 读进 os.environ"。继承来的 `CP_ENV_FILE` 完全可能就是仓库 `.env` 本身
#   （那正是本缺陷的形态），setdefault 会把缺陷原样留下。函数级 `_isolate_dotenv_target`
#   本来就是无条件覆盖，故这里不引入新的语义。
_DOTENV_FLOOR_DIR = tempfile.mkdtemp(prefix="pytest_dotenv_floor_")
_DOTENV_FLOOR_FILE = os.path.join(_DOTENV_FLOOR_DIR, "isolated.env")
os.environ["CP_ENV_FILE"] = _DOTENV_FLOOR_FILE
atexit.register(shutil.rmtree, _DOTENV_FLOOR_DIR, ignore_errors=True)

# ── 【TESTHYG-1 · 离线基线】测试进程**不加载** .env（见上方地板）──
# import app_server 会真的实例化 sentence-transformers 编码器，而"只读本地缓存、
# 不出网"的前提是 HF_HUB_OFFLINE / TRANSFORMERS_OFFLINE（.env:153-154 提供的
# 正是这两个键）。地板生效后 .env 不再进 os.environ ⇒ 必须在这里**显式补齐**，
# 否则实测后果（2026-09-26）：去 huggingface.co 拉 paraphrase-multilingual-MiniLM-L12-v2，
# WinError 10060 五次重试 × 多个文件，import app_server 从 ~90s 拖到 >13min，
# 直接撞穿 pytest-timeout 的 120s 并对整个进程硬退出（整轮无 summary、exit 1）。
# 【为什么这不是"为了让测试变绿而放宽"】这两条**本来就在 CI 上如此**：CI 的干净
# checkout 里没有 .env（.gitignore:12），故三份 workflow 都显式设了它们
# （.github/workflows/test.yml:301-302、daily_regression.yml:57-58、
# observability-ci.yml）—— 这里只是把本地基线**对齐 CI**。
# 它们不在开关注册表（agent/settings/registry.py）里，也没有任何用例断言其默认值，
# 故不改变任何用例的判据语义。
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")


# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


#: 审批库**绝不该出现**的位置（`agent/data/` 是 tool_trace.db 等运行期库的家，
#: 但审批库正确位置是 `<repo>/data/`）——两个守卫共用的同一份判据
_STRAY_APPROVAL_TARGETS = [
    PROJECT_ROOT / "agent" / "data" / "approval_records.jsonl",
    PROJECT_ROOT / "agent" / "data" / "tool_approval_uses.jsonl",
]

# ════════════════════════════════════════════════════════════
#  审批/事件/审计落盘隔离（2026-09-17）
# ════════════════════════════════════════════════════════════
#
# 为什么需要：`agent/tool_gate.py` 命中审批边界的工具调用会**经审批流挂单**，而
# `ApprovalFlow()` 无参构造默认写运行时 `data/approval_records.jsonl`；消费台账同理。
# 单测里凡是通过 `agent.tools.call()` 调到 `shell_execute` / `generate_tool` /
# `ext_install` 一类工具的用例，都会**真的往生产审批库里写待审批单** —— 本仓已四次
# 踩到"测试写生产数据"（本次是第五处的预防）。
# 而审批动作本身还有**两处副作用落点**，同样必须隔离，否则"跑一次单测"
# 就会往生产事件流和链式审计台账里追加记录（实测：events.jsonl 41903→50835 字节、
# audit_chain.db 14221312→14237696 字节）。这里在**会话级**把四个路径全部指向临时目录，
# 用 `setdefault` 保证用例内的 `monkeypatch.setenv` 仍可覆盖（且回滚回本会话值）。

# 【2026-09-20 已移除 `_live_server_running()` 探测】
# `400b76f4` 曾加过一个 `socket.create_connection(("127.0.0.1", 5678))` 探测，
# 意图是"后端在跑时跳过 stray 归因"。**实测证明该判据过度粗糙，已废弃并删除**：
# negative probe 用例自己写出了 `tool_approval_uses.jsonl`，却因为后端在跑被归因成
# "服务写的" ⇒ 真缺陷被甩锅、且文件不删（守卫完全失效）。
# 现改用**窗口前后 (size, mtime_ns) 差集**归属（见下方两道守卫），
# 它能在后端存活的情况下仍精确抓出"本窗口内被改动"，故不再需要该探测，也不留死代码。


@pytest.fixture(autouse=True)
def _no_stray_approval_store(request):
    """**逐用例**守卫：审批库若在某个用例执行期间出现在 agent/data/ 下，就地失败并点名

    为什么要有逐用例版（会话版只能报"整轮跑完多了个文件"）：首次发现该产物时，
    会话级守卫只知道"存在"，单跑任何涉事文件又都不复现（说明与执行序/随机种子有关）
    ⇒ 只能靠逐用例快照把**具体是哪个用例**钉出来。
    检测到即**删掉该产物再失败**，避免后续用例级联报错（一次只点名一个真凶）。

    【不易·2026-09-20 判据升级为 mtime 差集 —— 取代"后端在跑就整体跳过"】
      背景：本机开发时常驻后端 `python app_server.py`（`start_yunshu.bat` 的启动链），
      而 `.env:1215` 的 `APPROVAL_RECORDS_PATH=agent/data/approval_records.jsonl` 是
      **服务的真实配置** ⇒ 后端处理真实请求时会正常往那里写审批单。
      最初的应对是"检测到后端就跑跳过归因"，但实测证明它**过度粗糙**：
          negative probe 用例**自己**写出了 `tool_approval_uses.jsonl`，
          却因为后端在跑而被归因成"服务写的"，**真缺陷被甩锅、且文件不删**。
      实测依据（本机，后端 PID 1792 存活）：
          12 秒内 `approval_records.jsonl` 的 length/mtime **完全不变**
          ⇒ 后端**只在有审批动作时写，不是持续写**，故"该窗口内有没有被改动"
            是一条可用的归属判据。

      新判据：比较**用例执行窗口前后**的 (exists, size, mtime_ns) 三元组。
        · 用例前不存在 → 用例后存在          ⇒ 本用例写的（真 stray）
        · 用例前后都存在，但 size/mtime 变了 ⇒ 本用例改写的（真 stray）
        · 前后完全一致                        ⇒ 与本用例无关（可能是后端窗口内写入后又被
                                               本夹具判为"未变化"，或纯属会话前遗留）
      这样即便后端在跑，**只要它没在这个窗口里写**，真 stray 依然会被抓到；
      而它若确实在窗口里写了，此时 mtime 变化与我们无法区分归属 —— 那种极小概率的
      窗口冲突会表现为一次假红，属于**宁可假红也不漏真缺陷**的取舍（原判据是反过来）。
    """
    def _stat(p):
        try:
            st = p.stat()
        except OSError:
            return None
        return (st.st_size, st.st_mtime_ns)

    before = {p: _stat(p) for p in _STRAY_APPROVAL_TARGETS}
    yield
    for p, prev in before.items():
        now = _stat(p)
        if now is None:
            continue                      # 不存在 ⇒ 无 stray
        if prev == now:
            continue                      # 窗口内未被改动 ⇒ 与本用例无关
        try:
            p.unlink()
        except OSError:
            pass
        pytest.fail(
            f"用例 {request.node.nodeid} 执行期间，审批库被写到了 agent/data/：{p}\n"
            f"（窗口前 {prev} → 窗口后 {now}）\n"
            "→ 正确位置是 <repo>/data/ 或会话临时目录（见本文件会话级隔离）。")


@pytest.fixture(autouse=True, scope="session")
def _isolate_llm_monitor_snapshot(tmp_path_factory):
    """把 LLM 监控的「会话最后一条通信」快照指向会话级临时目录（绝不写 data/）

    Why（2026-09-19）：llm_monitor 现在会把每次通信（节流）与进程退出时的最后一条
    落盘到 `<repo>/data/llm_monitor_last.json`，并在**新实例启动时回填**该快照。
    若不隔离：单测写出的假记录会被后续用例（乃至真实服务重启）当成"上次会话遗留"读回，
    典型症状是 `test_llm_monitor_singleton` 里 `total == 1` 变成 2。

    注意 `LLMMonitor.__init__` 在构造时读取模块级常量，故必须**在用例内构造之前**改绑
    （session 级 autouse 夹具先于用例执行，满足该时序）。
    """
    isolation_file = tmp_path_factory.mktemp("llm_monitor_isolation") / "llm_monitor_last.json"
    try:
        import agent.llm_monitor as _lm

        _saved = getattr(_lm, "PERSIST_FILE", None)
        _lm.PERSIST_FILE = str(isolation_file)
    except Exception as e:  # noqa: BLE001 模块不可用不该影响测试运行
        print(f"[conftest] LLM 监控快照隔离跳过: {type(e).__name__}: {e}")
        yield None
        return

    yield isolation_file

    if _saved is not None:
        _lm.PERSIST_FILE = _saved


@pytest.fixture(autouse=True)
def _reassert_approval_isolation(_isolate_approval_stores, monkeypatch):
    """逐用例**重新施加**审批落盘隔离（防中途 `import app_server` 把它冲掉）

    【为什么必须补这一道（2026-09-20 实测根因，TASK-06 分块全量跑时发现）】
      `app_server.py:39-51` 在**导入期**调 `EnvConfigManager().reload()`，而它的语义是
      "**覆盖**同名环境变量为 `.env` 值"。实测（同一进程内）：

          os.environ["APPROVAL_RECORDS_PATH"] = "<隔离临时目录>/approval_records.jsonl"
          import app_server
          → os.environ["APPROVAL_RECORDS_PATH"] == "agent/data/approval_records.jsonl"

      而 `.env:1215` 正是 `APPROVAL_RECORDS_PATH=agent/data/approval_records.jsonl`（**相对路径**）。
      ⇒ 任何 `import app_server` 的用例（本仓有多个：`test_background_tasks_routes.py`、
      能力面路由用例等）都会把上面 `_isolate_approval_stores` 的会话级隔离**冲掉**，
      此后每一个触发审批的用例都会写进 `agent/data/approval_records.jsonl`
      ⇒ 逐用例守卫 `_no_stray_approval_store` 在这些用例上报 teardown ERROR。

      实测代价（`python scripts/run_full_pytest.py 4 4 fast`）：chunk_1 里 **8 条**用例
      teardown 报 stray，而**单跑同一批文件全绿** —— 典型的"与执行序有关"，
      与真缺陷的表现无法区分（第 5 次踩到"测试写生产数据"的同一族）。
    【为什么只重设 `.env` 真正会覆盖的那一个】实测 `.env` 只设了
      `APPROVAL_RECORDS_PATH`（其余五个隔离项不在 `.env` 里 ⇒ 不会被 reload 覆盖）。
      只重设它会覆盖的那一个，能把影响面压到最小。
    【为什么用 monkeypatch 而不是直改 os.environ】用例内仍可用
      `monkeypatch.setenv("APPROVAL_RECORDS_PATH", ...)` 覆盖（monkeypatch 的还原栈是
      后进先出，用例内设置的值优先），且随用例自动还原，不污染其它用例。
    【为什么不改 `app_server.py` 的 .env 加载】那是**生产**语义（"配置走 .env 单一数据源"，
      见该处注释），不该为了测试让步；隔离应由测试侧保证。
    """
    isolation_dir = Path(str(_isolate_approval_stores))
    monkeypatch.setenv("APPROVAL_RECORDS_PATH",
                       str(isolation_dir / "approval_records.jsonl"))


@pytest.fixture(autouse=True)
def _reset_env_derived_approval_flow(_reassert_approval_isolation):
    """逐用例复位**按环境变量在首次访问时定型**的审批流单例

    【为什么仅重设环境变量还不够（2026-09-20 实测）】
      补上 `_reassert_approval_isolation` 后，整轮跑仍有 **2 条** teardown stray
      （`test_capregistry_core`、`test_capregistry_callpaths_routes` 各 1 条），单跑却全绿。
      根因：`agent/server_routes/routes_approval.py:65-80` 的 `get_approval_flow()`
      把 `ApprovalFlow()` 缓存在**模块级** `_flow`，而 `ApprovalFlow.__init__` 只在
      **构造那一刻**读 `APPROVAL_RECORDS_PATH`。若它的首次访问发生在"环境已被
      `import app_server` 冲掉"之后，这个单例就把**错的路径**记到整轮结束 ——
      之后无论怎么改环境变量，写盘都还是那条相对路径。
    ⇒ 必须**同时把单例置空**，让它按新路径重建。
    【与既有手法一致】这正是 `_isolate_llm_monitor_snapshot` + `_reset_llm_monitor_snapshot`
      那一对的同一手法（会话级隔离开关 + 逐用例复位单例），故此处沿用同款命名与结构。
    【为什么安全】`test_approval_routes.py` 的注入是**函数级夹具**（在用例 setup 内
      `set_approval_flow(...)`、teardown 还原），发生在本夹具之后 ⇒ 不受影响。
    """
    try:
        from agent.server_routes import routes_approval as _ra
        _ra._flow = None
    except Exception as e:  # noqa: BLE001 模块不可导入/无该属性 ⇒ 无需复位
        logging.getLogger(__name__).debug(
            "[conftest] 审批流单例复位跳过: %s: %s", type(e).__name__, e)
    try:
        from agent import tool_approval as _ta
        _ta._READ_FLOW = None
    except Exception as e:  # noqa: BLE001
        logging.getLogger(__name__).debug(
            "[conftest] tool_approval 只读流缓存复位跳过: %s: %s", type(e).__name__, e)


@pytest.fixture(autouse=True)
def _reset_llm_monitor_snapshot(_isolate_llm_monitor_snapshot):
    """每个用例前清掉隔离的 LLM 快照

    Why：监控器现在会在**构造时回填**上次会话的最后一条通信。用例之间若共用同一份
    快照文件，上一个用例 record() 出来的记录会被下一个用例新建的监控器读回，
    `get_records()` 的 total 随之 +1（test_llm_monitor_singleton 的 `total == 1` 实测失败）。
    生产语义（跨进程重启保留快照）不受影响 —— 这里清的是**测试专用**的隔离文件。
    """
    try:
        target = _isolate_llm_monitor_snapshot
        if target and not isinstance(target, str):
            target = str(target)
        if target and os.path.exists(target):
            os.remove(target)
    except Exception:  # noqa: BLE001 清理失败不该影响用例
        pass
    yield


@pytest.fixture(autouse=True, scope="session")
def _isolate_approval_stores(tmp_path_factory):
    """把审批记录 / 消费台账 / 事件流 / 链式审计指向会话级临时目录（绝不写 data/**）"""
    isolation_dir = tmp_path_factory.mktemp("approval_isolation")
    events_dir = isolation_dir / "events"
    audit_dir = isolation_dir / "audit"
    events_dir.mkdir(parents=True, exist_ok=True)
    audit_dir.mkdir(parents=True, exist_ok=True)
    keys = {
        "APPROVAL_RECORDS_PATH": isolation_dir / "approval_records.jsonl",
        "CP_TOOL_APPROVAL_USES_PATH": isolation_dir / "tool_approval_uses.jsonl",
        # 审批动作会写事件流与链式审计（agent/audit/facade.py 的三个环境变量）
        "CP_EVENTS_DIR": events_dir,
        "AUDIT_DB_PATH": audit_dir / "audit_chain.db",
        "AUDIT_ROOTS_PATH": audit_dir / "daily_roots.jsonl",
        "AUDIT_SIGNING_KEY": audit_dir / "audit_signing_key.pem",
        # 【为什么不隔离它会有真实后果（2026-09-22）】`app_server.py` 在 **import 期**就调用
        # `settings.bootstrap.apply_overrides()`（app_server.py:1031-1032），而相当多的单测会
        # import app_server（路由清单、在线 E2E 等）⇒ **操作员在开关中心/界面上改过的开关**
        # 会被灌进测试进程的 `os.environ`。实测：某台机器把 `git` 加进"确认分级豁免名单"
        # 之后，`test_tool_gate_fallback.py::TestYamlStaysAuthoritative` 开始变红，
        # 而**单跑该文件恒绿** —— 典型的跨用例污染。
        # 这不是"某条用例脆弱"，而是**测试读到了生产运行态**（D6：测试不碰生产数据/状态）。
        # 故与审批库、事件流、审计链同样处置：把覆盖层指向会话级临时目录。
        "CP_UI_SETTINGS_PATH": isolation_dir / "ui_settings.json",
        # 【测试基线：审批边界默认关闭】大量单测会注册"没有 YAML 元数据"的探针工具并
        # 直接 `agent.tools.call()` 调它来验证**工具调用机制**（返回值、健康跟踪、
        # 限流顺序…）。而 HITL 兜底网（`agent/tool_gate.py::_hitl_boundary`）对
        # "已注册但无元数据"的工具 fail-closed ⇒ 这些调用会被挂单待审批、不执行 handler，
        # 于是测到的是治理网而不是被测机制。
        # 故测试**基线**取关闭，边界/兜底行为由专门的文件显式开启（它们都自带
        # `monkeypatch.setenv(..., "1")` 的 fixture）：
        #   tests/unit/test_tool_gate.py、test_tool_gate_fallback.py、
        #   test_tool_approval_e2e.py、test_tool_approval.py
        # 与生产默认值（开启）不同是**刻意**的：单测的对象是被测机制，不是治理姿态；
        # 治理姿态本身有上述专项测试与 settings registry 守卫覆盖。
        "CP_TOOL_GATE_APPROVAL_ENFORCE": "0",
    }
    saved = {k: os.environ.get(k) for k in keys}
    for k, v in keys.items():
        os.environ.setdefault(k, str(v))

    # 覆盖层是**惰性单例**：若在本夹具之前已被构造过（那样它读的是真实
    # `data/ui_settings.json`），复位一次让它按上面的临时路径重建。
    try:
        from agent.settings.overrides import reset_override_store
        from agent.settings.service import reset_settings_service
        reset_override_store()
        reset_settings_service()
    except Exception:  # noqa: BLE001 复位失败不影响其它隔离项
        pass

    # ── 兜底守卫：审批库绝不该出现在 agent/data/ 下 ──────────────────────────
    # 实测过一次：某个（会话级组合下的）用例把审批记录写到了
    # `agent/data/approval_records.jsonl` —— 那是**相对路径**撞上被 chdir 过的 cwd 的结果
    # （`agent/data/` 是真实运行期目录，但审批库的正确位置是 `<repo>/data/`）。
    # 单跑任一个涉事文件都不复现，故在这里做**窄而准**的守卫：会话结束时若这两个文件
    # 出现在 agent/data/ 下，直接失败并点名，下一次跑就能二分定位。
    _stray_targets = _STRAY_APPROVAL_TARGETS
    # 【不易·2026-09-20 判据升级为 (size, mtime_ns) 差集】与逐用例守卫 `_no_stray_approval_store` 用同一口径。
    #
    # 为什么必须区分"会话前就存在且未变"与"会话期间被改动"：
    #   本机开发时常驻 `python app_server.py`（`start_yunshu.bat` 的启动链），
    #   而 `.env:1215` 的 `APPROVAL_RECORDS_PATH=agent/data/approval_records.jsonl`
    #   就是**服务的真实配置** ⇒ 后端处理真实请求时会正常往那里写审批单。
    #   原判据是"文件存在即失败" ⇒ 后端在跑时**必然假失败**，表现为 session teardown ERROR，
    #   与"真有 stray 产物"无法区分（实测：`400b76f4` 回归中该 assert 即被此类文件触发）。
    #
    # 实测依据（判据可行性）：后端存活时 12 秒内该文件 length/mtime **完全不变**
    #   ⇒ 它只在有审批动作时写，故"会话窗口内有没有被改动"是可用的归属判据。
    #
    # 与"检测到后端就整体跳过 assert"的区别：那种做法会在最需要守卫的场景
    #   （开发机常驻后端）**彻底丧失检测能力** —— 已实测踩到：negative probe 用例
    #   自己写出的 stray 被甩锅给后端、且文件不删。差集口径既不漏真缺陷，也不误伤服务数据。
    def _stat(p):
        try:
            st = p.stat()
        except OSError:
            return None
        return (st.st_size, st.st_mtime_ns)

    _strays_at_session_start = {p: _stat(p) for p in _stray_targets}

    # 【必须改绑，不能只设环境变量】`agent/audit/facade.py:466` 是**模块级单例**
    # `audit = AuditFacade()`，它在 **import 期**按当时的环境变量定路径。若该模块先于本夹具
    # 被导入（collection 期由别的模块链式导入），它拿到的就是**生产路径**，
    # 之后再设 AUDIT_DB_PATH 也不会回头生效 —— 实测后果：跑一次审批相关单测就写生产
    # `data/audit/audit_chain.db`（每次 +32KB）。故这里显式重置门面并改绑三个路径。
    try:
        from agent.audit import facade as _audit_facade

        _audit_facade.reset_audit_facade()          # close + bind(None) + 计数清零
        _facade = getattr(_audit_facade, "audit", None)
        if _facade is not None:
            _facade._db_path = str(isolation_dir / "audit" / "audit_chain.db")
            _facade._roots_path = str(isolation_dir / "audit" / "daily_roots.jsonl")
            _facade._key_path = str(isolation_dir / "audit" / "audit_signing_key.pem")
    except Exception as e:  # noqa: BLE001 审计门面不可用不该影响测试运行
        print(f"[conftest] 审计门面隔离跳过（仅影响生产数据保护）: {type(e).__name__}: {e}")

    # ── 【TASK-A / 遗留 L2】把**模块级审计单例**也改绑到临时目录 ──────────────
    # 为什么上一段（AUDIT_DB_PATH + 重绑 `facade.audit`）**挡不住**它：
    #   `agent/audit/logger.py:209` 的 `audit_logger = AuditLogger()` 是**模块级单例**，
    #   其 `log_dir` 在 import 期固定为默认值 `"./data/audit/"`，并用
    #   `chain_db_path = <log_dir>/audit_chain.db` **显式传参**构造自己的 `AuditFacade`；
    #   而 `AuditFacade.__init__:198` 的路径优先级是
    #       db_path（显式实参） > AUDIT_DB_PATH（环境变量） > DEFAULT_DB_PATH
    #   ⇒ **显式实参压过环境变量** ⇒ 该单例既不读 `AUDIT_DB_PATH`，也完全不经过
    #     `facade.audit`，故上两段隔离对它**恒为失效**。
    #
    # 实测代价（2026-09-21 复现，见 `_ci_logs/t10/`）：
    #   `tests/unit/test_audit_logger_comprehensive.py:200` 调
    #   `audit_logger.log("global_test_action")` ⇒ 每跑一次就
    #     ① 往**生产链** `data/audit/audit_chain.db` +1 条；
    #     ② 往**生产旧轨** `data/audit/audit_2026MMDD.jsonl` 追加一行。
    #   （实测 count 20088→20089、max_seq 20103→20104，**主库 size 不变** ⇒
    #     该库是 WAL/journal 模式，只比 size/mtime 会漏判，必须比 count(*) 与 max(seq)。）
    #
    # 分工：本段是**会话级兜底**（覆盖全仓任何文件对该单例的使用）；
    #   `tests/unit/test_audit_logger_comprehensive.py` 另有 autouse 夹具把同一单例
    #   重绑到该用例自己的 `tmp_path`（归属更精确，并能断言"写入只落 tmp_path"）。
    _logger_mod = None
    _logger_saved: dict = {}
    try:
        from agent.audit import logger as _logger_mod

        _singleton = getattr(_logger_mod, "audit_logger", None)
        if _singleton is not None:
            # `_current_file` 沿用其原有日期分片名，只换目录（保持"按日分片"契约）
            _orig_current = getattr(_singleton, "_current_file", None)
            _redirects = [
                ("_log_dir", audit_dir),
                ("_current_file", audit_dir / (Path(_orig_current).name if _orig_current
                                               else "audit_unknown.jsonl")),
                ("_chain_db_path", str(audit_dir / "audit_chain.db")),
                ("_roots_path", str(audit_dir / "daily_roots.jsonl")),
            ]
            for _attr, _value in _redirects:
                _logger_saved[_attr] = getattr(_singleton, _attr, None)
                setattr(_singleton, _attr, _value)
    except Exception as e:  # noqa: BLE001 单例改绑失败不该影响测试运行
        print(f"[conftest] 审计日志单例隔离跳过（仅影响生产数据保护）: {type(e).__name__}: {e}")

    yield isolation_dir

    # 还原模块级审计单例（并关闭它可能已在临时目录上打开的台账，释放单写者登记）
    try:
        if _logger_mod is not None:
            _singleton = getattr(_logger_mod, "audit_logger", None)
            if _singleton is not None:
                _facade_obj = getattr(_singleton, "_facade", None)
                if _facade_obj is not None:
                    _facade_obj.close()
                _singleton._facade = None
                _singleton._track = None
                for _attr, _value in _logger_saved.items():
                    setattr(_singleton, _attr, _value)
    except Exception as e:  # noqa: BLE001 还原失败不该让会话 teardown 报 ERROR
        print(f"[conftest] 审计日志单例还原跳过: {type(e).__name__}: {e}")

    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    stray = [f"{p}（{_strays_at_session_start[p]} → {_stat(p)}）"
             for p in _stray_targets
             if _stat(p) is not None and _stat(p) != _strays_at_session_start[p]]
    assert not stray, (
        "审批库在**本次会话期间**被写到了 agent/data/ 下（相对路径 + 被 chdir 的 cwd 的典型后果）："
        f"{stray}\n→ 正确位置是 <repo>/data/ 或会话临时目录；请让写它的夹具改用绝对路径"
        "（或见 tests/conftest.py 的会话级隔离）。\n"
        "注：判据是**会话窗口内 (size, mtime_ns) 发生变化**，而非「文件存在」 —— "
        "会话前就存在且未被改动的同名文件不计入（可能是本机常驻后端 `app_server.py` 写的"
        "真实审批单，`.env:1215` 即为该配置）。")

# ════════════════════════════════════════════════════════════
# 原生扩展导入顺序固化（S11-01：从 tests/integration 提升到 tests 根）
# ════════════════════════════════════════════════════════════
# 【为什么放在这里】必须早于任何测试模块的 import；本文件是 pytest 在
#   tests/** 下加载的**第一个** conftest（rootdir→子目录逐级加载），且此时
#   `sys.path` 刚补上 PROJECT_ROOT，故是"能 import 到 agent 的最早时机"。
# 【为什么 unit 也要（S10-05 遗留 #6）】`tests/unit` 全量历史上同样崩过
#   （同类 0xC0000005），此前保护只在 tests/integration 生效 —— unit 处于
#   **无保护**状态。提到 tests 根后 unit / integration 共用同一道保护。
# 【实现唯一】`agent/utils/native_preimport.py`（同一份实现，三处调用点共用；
#   现象/根因/顺序依据/失败姿态/开关语义见该模块 docstring，此处不复制）。
# 【可关闭】`CP_NATIVE_PREIMPORT_ENABLED=0` 整支关闭（已登记开关注册表）。
# 【失败姿态】任何一步失败只降级告警，绝不阻断收集（不得引入新的硬依赖）。
# 【为什么 import 出现在文件中部】它必须晚于 `sys.path` 补齐（第 43 行）才能
#   解析到 `agent.*`，故只能在此处；`# noqa: E402` 即为此而加。
try:
    from agent.utils.native_preimport import (  # noqa: E402
        pin_native_import_order,
        report_line as native_preimport_report_line,
    )

    pin_native_import_order()
except Exception as _native_preimport_e:  # pragma: no cover - 仅在环境缺件时走到
    # 守【不易】：保护装不上 ≠ 测试不能跑（缺件应表现为用例失败/跳过）
    logging.getLogger(__name__).warning(
        "[S11-01] 原生扩展导入顺序固化装载失败（降级，不阻断收集）: %s",
        _native_preimport_e,
    )

    def native_preimport_report_line() -> str:  # type: ignore[misc]
        return "[S11-01] 原生扩展导入顺序固化: 装载失败（降级）"

# 测试配置
TEST_CONFIG = {
    "env": os.getenv("TEST_ENV", "development"),
    "enable_monitoring": True,
    "enable_coverage": True,
    "test_data_dir": PROJECT_ROOT / "tests" / "fixtures",
    "report_dir": PROJECT_ROOT / "test_reports",
    "coverage_threshold": 70,
}

# ============================================================================
# pytest钩子函数 - 测试执行生命周期管理
# ============================================================================

def pytest_configure(config):
    """pytest配置初始化"""
    # 注册自定义标记
    config.addinivalue_line(
        "markers", "p0: P0优先级测试用例，必须通过"
    )
    config.addinivalue_line(
        "markers", "p1: P1优先级测试用例"
    )
    config.addinivalue_line(
        "markers", "requires_setup: 需要复杂环境设置"
    )

    # 创建测试报告目录
    TEST_CONFIG["report_dir"].mkdir(exist_ok=True, parents=True)

    # 设置测试日志
    _setup_test_logging(config)

def pytest_report_header(config, start_path=None):
    """把原生扩展预导入结果写进报告头（S11-01：unit / integration 共用一行）。

    Why（不可省）：规避逻辑若静默失败，报告里看不出「崩溃路径是否真的被规避」，
    等于假绿灯。此处把每一步的实际状态（ok/耗时、cached、failed:原因）显式打印，
    使「被检查的对象不会从报告里消失」（纪律：宁可留一条真实红灯，不要假绿灯）。

    【为什么放在 tests 根】本文件定义的 hook 对 `tests/unit` 与
    `tests/integration` 都会被调用，故只在这里定义一次：integration 的 conftest
    若同时定义，pytest 会把两处结果**拼接**，同一行会打印两遍。
    措辞统一来自 `agent/utils/native_preimport.py::report_line()`。
    """
    return [native_preimport_report_line()]


def pytest_collection_modifyitems(config, items):
    """修改测试用例集合 - 合并自动标记和跳过逻辑"""
    skip_slow = pytest.mark.skip(reason="需要 --runslow 选项才能运行慢速测试")
    skip_llm = pytest.mark.skip(reason="需要 LLM 服务才能运行")

    for item in items:
        # 自动标记快速测试
        if "quick" not in item.keywords and "slow" not in item.keywords:
            if "test_basics" in item.nodeid or "test_import" in item.nodeid:
                item.add_marker(pytest.mark.quick)

        # 自动标记P0测试
        if "test_memory" in item.nodeid or "test_permission" in item.nodeid:
            item.add_marker(pytest.mark.p0)
            item.add_marker(pytest.mark.critical)

        # 跳过慢速测试（除非 --runslow）
        if "slow" in item.keywords and not config.getoption("--runslow"):
            item.add_marker(skip_slow)

        # 跳过需要 LLM 的测试（除非有 API key）
        if "requires_llm" in item.keywords and not os.getenv("LLM_API_KEY"):
            item.add_marker(skip_llm)

def pytest_runtest_makereport(item, call):
    """生成测试报告"""
    if call.when == "call":
        # 记录测试结果用于后续分析
        outcome = getattr(call, "outcome", None)
        if outcome and hasattr(call, "excinfo"):
            if call.excinfo:
                _handle_test_failure(item, call)

def _setup_test_logging(config):
    """配置测试日志"""
    log_dir = TEST_CONFIG["report_dir"] / "logs"
    log_dir.mkdir(exist_ok=True, parents=True)

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)8s] %(name)s: %(message)s',
        handlers=[
            logging.FileHandler(log_dir / f"test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"),
            logging.StreamHandler()
        ]
    )

def _handle_test_failure(item, call):
    """处理测试失败"""
    logger = logging.getLogger("test.failures")
    logger.error(
        f"测试失败: {item.nodeid}\n"
        f"异常: {call.excinfo.typename if call.excinfo else 'None'}\n"
        f"消息: {str(call.excinfo.value) if call.excinfo else 'None'}"
    )

# ============================================================================
# 测试Fixtures - 依赖注入
# ============================================================================

@pytest.fixture(scope="session")
def project_root():
    """项目根目录"""
    return PROJECT_ROOT

@pytest.fixture(scope="session")
def test_config():
    """测试配置"""
    return TEST_CONFIG

@pytest.fixture(scope="function")
def temp_test_dir(tmp_path):
    """临时测试目录"""
    test_dir = tmp_path / "test_data"
    test_dir.mkdir()
    return test_dir

@pytest.fixture(scope="function")
def sample_sensor_data():
    """示例传感器数据"""
    return {
        "cpu_usage": 45.5,
        "memory_usage": 62.3,
        "temperature": 55.0,
        "battery_level": 85,
        "disk_usage": 50.0,
        "network_status": "connected",
        "timestamp": datetime.now().isoformat()
    }

@pytest.fixture(scope="function")
def sample_memory_data():
    """示例记忆数据"""
    return {
        "sources": [
            {
                "id": "src_001",
                "type": "conversation",
                "content": "用户询问天气",
                "timestamp": datetime.now().isoformat()
            }
        ],
        "topics": [
            {
                "id": "topic_001",
                "name": "weather",
                "count": 5
            }
        ],
        "summary": {
            "content": "用户关注天气信息",
            "confidence": 0.85
        }
    }

@pytest.fixture(scope="function")
def mock_llm_response():
    """模拟LLM响应数据"""
    return {
        "response": "今天的天气晴朗，温度25度。",
        "tokens_used": 150,
        "model": "gpt-3.5-turbo",
        "finish_reason": "stop"
    }

@pytest.fixture(scope="function")
def test_user_input():
    """测试用户输入数据"""
    return {
        "message": "今天天气怎么样？",
        "user_id": "test_user_001",
        "session_id": "test_session_001",
        "timestamp": datetime.now().isoformat(),
        "metadata": {
            "platform": "test",
            "version": "2.0.0"
        }
    }

@pytest.fixture(scope="function")
def permission_test_cases():
    """权限系统测试用例"""
    return [
        {
            "name": "危险操作_删除系统文件",
            "operation": "delete",
            "path": "C:\\Windows\\System32",
            "expected_result": "blocked",
            "severity": "critical"
        },
        {
            "name": "安全操作_读取文档",
            "operation": "read",
            "path": "C:\\Users\\Documents\\report.txt",
            "expected_result": "allowed",
            "severity": "low"
        },
        {
            "name": "警告操作_修改系统配置",
            "operation": "write",
            "path": "C:\\Program Files",
            "expected_result": "warning",
            "severity": "medium"
        }
    ]

@pytest.fixture(scope="function")
def monitoring_metrics_sample():
    """监控系统指标样本数据"""
    return {
        "request_count": 100,
        "error_count": 5,
        "avg_latency_ms": 250.5,
        "max_latency_ms": 1500,
        "min_latency_ms": 50,
        "cpu_usage": 45.0,
        "memory_usage": 60.0,
        "active_connections": 10
    }

# ============================================================================
# 测试数据管理
# ============================================================================

class TestDataManager:
    """测试数据管理器"""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self._cache = {}

    def load_json(self, filename: str) -> Dict[str, Any]:
        """加载JSON测试数据"""
        if filename in self._cache:
            return self._cache[filename]

        filepath = self.data_dir / filename
        if not filepath.exists():
            pytest.fail(f"测试数据文件不存在: {filepath}")

        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)

        self._cache[filename] = data
        return data

    def save_json(self, filename: str, data: Dict[str, Any]):
        """保存JSON测试数据"""
        filepath = self.data_dir / filename
        filepath.parent.mkdir(parents=True, exist_ok=True)

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def get_fixtures_path(self, fixture_name: str) -> Path:
        """获取测试固件路径"""
        return self.data_dir / "fixtures" / fixture_name

@pytest.fixture(scope="session")
def test_data_manager():
    """测试数据管理器fixture"""
    data_dir = TEST_CONFIG["test_data_dir"]
    return TestDataManager(data_dir)

# ============================================================================
# 测试环境管理
# ============================================================================

# ════════════════════════════════════════════════════════════
#  【S11-10 · R9-c】已删除 CI 专用 mock `_mock_env_config_in_ci`
#
#  历史：为了让 CI 不去写 `.env`，该 autouse 夹具在 `SKILLS_OFFLINE=1` 时把
#  `EnvConfigManager.set/delete` 整体替换为"只写 os.environ"的 no-op，并靠
#  `_MOCK_ENV_CONFIG_EXCLUDED_FILES`（文件名黑名单）给"契约是真实文件 I/O"的
#  测试放行（历史记录：CHG-2026-0801，CI 上曾 13+8+4=25 个失败）。
#
#  为什么现在可以删除（三条证据）：
#    1. **它的原始目的已被更强的机制取代**：`_isolate_dotenv_target`
#       （见下方 §2026-09-13）用 `CP_ENV_FILE` 把写入**重定向**到每个用例的
#       tmp 文件 —— 仓库根 `.env` 依然碰不到，而且**保留真实文件读写**。
#       即"保护仓库 .env"不再需要"把写入变成 no-op"。
#    2. **它本身会制造假绿灯**：把 `set` 变成 no-op 正是
#       `tests/unit/test_env_isolation_p0.py` 要拦截的"掩盖"形状——该护栏
#       在 CI 上因此**必然失败**（S11-10/R9 实测：CI 日志里
#       `env_config_manager` 只有 `init`/`secure_permissions`，**完全没有**
#       `write_start`/`env_config.set`，而 `_save_secure` 仍打印"已写入 .env"）。
#       黑名单只能靠人工补登，漏登一次就复现一次（本次就是）。
#    3. **真实 I/O 在 CI 上早已被验证可用**：被排除的那 4 个文件本来就在
#       CI（`SKILLS_OFFLINE=1`）里做真实写盘/chmod 600/跨进程锁，长期稳定；
#       且真实 `set()` 的副产物路径 `logs/config_audit.jsonl` 与审计链 `*.db`
#       均已被 `.gitignore` 覆盖（`.gitignore:32` 的 `logs/`、`:170` 的 `*.db`），
#       不会造成产物漂移。
#
#  不变量（删除后仍然成立）：仓库根 `.env` 不被测试改写 —— 由
#  `_isolate_dotenv_target`（重定向）+ `_guard_repo_dotenv_llm_key`
#  （会话级回归锁，比对 `LLM_API_KEY` 行）双重保证。
# ════════════════════════════════════════════════════════════


# ════════════════════════════════════════════════════════════
#  【2026-09-13 新增】把 `.env` 目标重定向到临时文件（**所有环境**，不止 CI）
#
#  背景（P0）：删掉的 `_mock_env_config_in_ci` 只在 `SKILLS_OFFLINE=1`
#  时激活，本地开发**走真实写入** ⇒ 任何调用 `NetworkConfigManager.update()`
#  的测试都会写**仓库根的真实 `.env`**。实测
#  `tests/unit/test_network_config.py::TestNetworkConfigEncryption::`
#  `test_no_secure_manager_warning` 会把 `LLM_API_KEY` 覆盖成 `sk-test-key`
#  ⇒ 正在运行的服务随即对模型 401、响应退化成兜底文案，
#  **极易把"答得对"误判为"不达成"**（S9-01 的验证就被这样误导过）。
#  `.env.backups/` 已有数百次覆盖记录（2026-08-15 事故）⇒ 属复发问题。
#
#  修法：不 mock 掉真实 I/O、不动测试断言语义，而是**重定向目标文件**
#  （`CP_ENV_FILE`）—— 真实文件读写/审计日志/权限契约仍可验证，
#  但**永不触碰仓库 `.env`**。
#  【S11-10 · R9-c】该重定向生效后，"CI 再额外 mock 掉 set/delete"已无必要，
#  故如上一节所述把那个 mock 整体删除（`SKILLS_OFFLINE` 分支随之消失）。
# ════════════════════════════════════════════════════════════

@pytest.fixture(scope="session", autouse=True)
def _assert_dotenv_redirect_floor():
    """会话级**自证**：`.env` 重定向地板已生效（TESTHYG-1）

    本夹具按 scope 规则**先于任何 module/session 级夹具** setup ⇒ 它在
    `import app_server`（module 级夹具）**之前**就把不变量钉住：
    `EnvConfigManager` 的目标**不是**仓库根 `.env`。

    这不是"断言测试自己写的东西"：`EnvConfigManager` 的目标由
    `CP_ENV_FILE`（第 83-86 行的地板）与 `EnvConfigManager.__init__` 共同决定，
    正是污染源 `app_server.py:50 → env_config_manager.py:387` 读取的那一个值。
    故本断言 = "**污染源读不到真实 .env**" 的机器可校验形式；一旦有人把地板删掉
    （或让 CP_ENV_FILE 指回仓库 .env），它会在**第一个用例**上直接失败并点名，
    而不是等到全量跑里以 5 条"单跑全绿"的红灯形式浮现。
    """
    from agent.env_config_manager import ENV_FILE_OVERRIDE_VAR, EnvConfigManager

    repo_env = (Path(__file__).resolve().parents[1] / ".env").resolve()
    override = str(os.environ.get(ENV_FILE_OVERRIDE_VAR) or "").strip()
    assert override, (
        ENV_FILE_OVERRIDE_VAR + " 未设置 —— tests/conftest.py 的 .env 重定向地板失效，"
        "此时 module 级夹具 `import app_server` 会把**整份仓库 .env** 灌进 os.environ")
    assert Path(override).resolve() != repo_env, (
        ENV_FILE_OVERRIDE_VAR + " 指向仓库根 .env（" + str(repo_env) + "）—— 地板失效")
    target = Path(EnvConfigManager()._env_file).resolve()
    assert target != repo_env, (
        "EnvConfigManager 的目标是仓库根 .env（" + str(target)
        + "）—— 污染源会读到真实 .env")
    yield
    # 收尾：摘掉地板目录（`atexit` 已注册同一动作；这里再钉一次是为了"会话正常结束必清"，
    # 不依赖解释器退出路径）。硬杀（pytest-timeout 的 os._exit）跳过二者 ⇒ 见 TESTHYG1.md 残留物一节。
    shutil.rmtree(_DOTENV_FLOOR_DIR, ignore_errors=True)


@pytest.fixture(scope="function", autouse=True)
def _isolate_dotenv_target(tmp_path):
    """每个用例把 `EnvConfigManager` 的目标 `.env` 指向本用例的 tmp 目录

    【与 TESTHYG-1 地板的关系】会话级地板（本文件第 43-86 行，conftest 导入期设置）
    保证**任何时刻**都不指向仓库 `.env`；本夹具在此之上做**逐用例**隔离
    （用例之间不共享同一个 .env），其 teardown 还原到的 `prev` 正是那个地板值。
    """
    from agent import env_config_manager as _ecm

    prev = os.environ.get(_ecm.ENV_FILE_OVERRIDE_VAR)
    os.environ[_ecm.ENV_FILE_OVERRIDE_VAR] = str(tmp_path / "isolated.env")
    _ecm.reset_env_config_manager()
    try:
        yield
    finally:
        # 先重置单例再还原变量：避免"单例仍指向本用例 tmp、变量已还原"的中间态
        _ecm.reset_env_config_manager()
        if prev is None:
            os.environ.pop(_ecm.ENV_FILE_OVERRIDE_VAR, None)
        else:
            os.environ[_ecm.ENV_FILE_OVERRIDE_VAR] = prev
        _ecm.reset_env_config_manager()


def _llm_api_key_line(env_file) -> str:
    """取 `.env` 里 `LLM_API_KEY=` 那一行（不存在则空串）"""
    try:
        for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("LLM_API_KEY="):
                return line
    except OSError:
        return ""
    return ""


@pytest.fixture(scope="session", autouse=True)
def _guard_repo_dotenv_llm_key():
    """会话级护栏：整轮测试结束后，仓库根 `.env` 的 `LLM_API_KEY` 必须**未被改写**

    这是 P0 的**回归锁**：一旦有测试（现在或将来）绕过隔离写到真实 `.env`，
    整轮测试会在收尾时报错，而不是**静默把在跑的服务打坏**。

    只比对 `LLM_API_KEY` 这一行（而非整个文件）：既精确覆盖事故形态
    （key 被覆盖成占位值），又不受并发会话对 `.env` 其它条目的合法改动干扰。
    """
    env_file = Path(__file__).resolve().parents[1] / ".env"
    before = _llm_api_key_line(env_file)
    yield
    after = _llm_api_key_line(env_file)
    if before != after:
        pytest.fail(
            "仓库根 .env 的 LLM_API_KEY 在测试期间被改写（P0：测试污染真实凭证）——"
            "请检查是否有测试绕过 CP_ENV_FILE 隔离直接写 .env；"
            f"before_len={len(before)} after_len={len(after)}")


@pytest.fixture(scope="session", autouse=True)
def setup_test_environment():
    """设置测试环境 - 会话级别自动执行"""
    print(f"\n{'='*60}")
    print(f"开始测试会话 - 环境: {TEST_CONFIG['env']}")
    print(f"测试报告目录: {TEST_CONFIG['report_dir']}")
    print(f"{'='*60}\n")

    yield

    print(f"\n{'='*60}")
    print(f"测试会话结束")
    print(f"{'='*60}\n")

@pytest.fixture(scope="function", autouse=True)
def reset_environment():
    """每个测试函数前后重置环境"""
    # 测试前
    original_cwd = os.getcwd()
    original_env = os.environ.copy()

    yield

    # 测试后清理
    os.chdir(original_cwd)
    os.environ.clear()
    os.environ.update(original_env)


# ════════════════════════════════════════════════════════════
# 测试污染强制清理 helpers（黄金快照 + 强制恢复）
# ════════════════════════════════════════════════════════════
# Why: 完整套件下（pytest-randomly 随机顺序）失败集随种子漂移（默认 31 / seed=12345 28），
# 核心机制是全局状态/类静态方法被前序测试 patch 泄漏或修改。与其逐个定位污染源，
# 采用「conftest 加载时快照真实引用 → 每测试后若发现被替换为 Mock 则强制恢复」兜底。
# 验证：9 个"看似恒定"失败（boundary 5 + message_handler 2 + singleton 2）单独运行
# 全部通过（9 passed in 2.15s）→ 均为污染受害方而非真实缺陷。

_GOLDEN_METHODS = {}


def _snapshot_golden_methods():
    """conftest 加载时（早于任何测试）快照易被 patch 泄漏的类静态方法真实引用。

    patch("agent.orchestrator.message_handler.MessageHandler.is_follow_up", ...)
    若在异常/嵌套场景下未恢复，后续测试读到 MagicMock（name='is_follow_up'），
    assert False 失败。快照取真实函数引用，恢复时 setattr 回去即可。
    """
    global _GOLDEN_METHODS
    try:
        from agent.orchestrator.message_handler import MessageHandler
        for _m in ("parse", "is_simple_query", "detect_dissatisfaction",
                   "is_follow_up", "extract_keywords"):
            _GOLDEN_METHODS[("MessageHandler", _m)] = getattr(MessageHandler, _m)
    except Exception:
        pass  # 模块暂不可导入时跳过，fixture 内再延迟快照


def _force_restore_golden_methods():
    """若类静态方法被 mock 泄漏替换为 MagicMock，强制恢复为真实引用。"""
    for (_cls, _m), _orig in list(_GOLDEN_METHODS.items()):
        try:
            import agent.orchestrator.message_handler as _mod
            _cur = getattr(_mod.MessageHandler, _m)
            if isinstance(_cur, Mock):
                setattr(_mod.MessageHandler, _m, _orig)
        except Exception:
            pass


def _force_reset_intent_rules():
    """强制重置 IntentRouter._rules 为全新深拷贝默认规则，并恢复被 mock 泄漏的 classify/for_intent。

    Why deepcopy 而非 list()：list(_DEFAULT_RULES) 是浅拷贝，若前序测试
    修改了规则对象的 patterns（append 正则），浅拷贝仍携带污染；深拷贝
    保证每个测试拿到与源码完全一致的 8 条规则（意图全识别为 unknown 的根因）。

    Why 同时恢复静态方法：子线程内 `with patch(...)` 的 start/stop 竞态会把
    IntentRouter.classify 泄漏为 MagicMock（return_value=("unknown", ...)），
    classify 恒返回 unknown（response_workflows 17 失败）；仅重置 _rules 无法恢复，
    必须把类属性回写为模块加载时的真实函数。
    """
    try:
        from agent import response_workflows as _rw
        _rw.IntentRouter._rules = copy.deepcopy(_rw._DEFAULT_RULES)
    except Exception:
        pass
    # 懒初始化 golden 静态方法引用（首次调用时快照，避免循环导入）
    _f = _force_reset_intent_rules
    if not hasattr(_f, "_golden_classify"):
        try:
            from agent.response_workflows import IntentRouter as _IR, ResponseTemplates as _RT
            _f._golden_classify = _IR.classify
            _f._golden_for_intent = _RT.for_intent
        except Exception:
            return
    try:
        from agent.response_workflows import IntentRouter as _IR, ResponseTemplates as _RT
        if isinstance(getattr(_IR, "classify", None), Mock):
            setattr(_IR, "classify", staticmethod(_f._golden_classify))
        if isinstance(getattr(_RT, "for_intent", None), Mock):
            setattr(_RT, "for_intent", staticmethod(_f._golden_for_intent))
    except Exception:
        pass


def _force_reset_scheduler_singleton():
    """若 task_scheduler._scheduler 被 patch 泄漏为 MagicMock，置 None 触发重建。

    Why: test_task_scheduler_integration.py L937 `patch("agent.task_scheduler._scheduler")`
    不带 new 参数时会替换为 MagicMock；若未恢复，get_scheduler() 读到非 None 的
    Mock → isinstance(_, TaskScheduler) 断言失败（test_get_scheduler_returns_instance）。
    """
    try:
        import agent.task_scheduler as _ts
        if isinstance(getattr(_ts, "_scheduler", None), Mock):
            _ts._scheduler = None
    except Exception:
        pass


_snapshot_golden_methods()


# ════════════════════════════════════════════════════════════
# 跨平台临时目录清理兜底（chroma sqlite 句柄占用）
# ════════════════════════════════════════════════════════════
# Why: seed=12345 下 memory_module 18 个失败，traceback 定位在 shutil.rmtree
# （TemporaryDirectory.cleanup 阶段）：
#   PermissionError [WinError 32] 文件被占用 → 链式 NotADirectoryError [WinError 267]
# 根因：chromadb PersistentClient 的 sqlite 连接在测试结束时不释放文件句柄，
# Windows 无法删除被占用文件（POSIX 允许删除打开中的文件，故仅 Windows 受影响）。

# 模块加载时保存原始类：_safe_tmp_directory 会把 tempfile.TemporaryDirectory
# 替换为 _RetryTemporaryDirectory，内部创建必须引用此原始类（防无限递归）
_ORIG_TEMPFILE_TEMPDIR_CLS = tempfile.TemporaryDirectory


class _RetryTemporaryDirectory:
    """跨平台安全的临时目录：Windows 上 cleanup 重试 + 最终忽略而非抛错。

    与 tempfile.TemporaryDirectory 接口兼容（with 模式），替换后对现有测试
    零侵入——test_memory_module 等均以 `with tempfile.TemporaryDirectory() as d:`
    方式使用，__enter__ 返回路径字符串、__exit__ 兜底清理。
    """

    def __init__(self, suffix=None, prefix=None, dir=None,
                 *, ignore_cleanup_errors=False):
        # 必须用模块加载时保存的原始类，而非 tempfile.TemporaryDirectory——
        # 后者在 _safe_tmp_directory 运行期间已被替换为本类，直接引用会无限递归
        self._inner = _ORIG_TEMPFILE_TEMPDIR_CLS(
            suffix=suffix, prefix=prefix, dir=dir,
            ignore_cleanup_errors=ignore_cleanup_errors,
        )

    @property
    def name(self) -> str:
        return self._inner.name

    def cleanup(self) -> None:
        self._try_cleanup()

    def __enter__(self) -> str:
        return self._inner.name

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        self._try_cleanup()
        return False

    def _try_cleanup(self) -> None:
        if sys.platform != "win32":
            # POSIX：删除打开中的文件合法，直接清理
            self._inner.cleanup()
            return
        # Windows：chroma sqlite/segment 句柄可能延迟释放，短暂重试等待。
        # 捕获 OSError（PermissionError 文件占用 / rmtree 重试后的半删状态
        # NotADirectoryError），避免链式抛错。
        for _ in range(5):
            try:
                self._inner.cleanup()
                return
            except OSError:
                time.sleep(0.3)
        # 最终仍失败：保留目录并告警，绝不阻断测试、
        # 绝不让 rmtree 半删状态链式抛出 NotADirectoryError
        logging.getLogger("pytest").warning(
            "[_RetryTemporaryDirectory] 临时目录清理失败，已保留: %s",
            self._inner.name,
        )


@pytest.fixture(scope="session", autouse=True)
def _safe_tmp_directory():
    """跨平台临时目录兜底（session 级，autouse）。

    1. tempfile.tempdir 重定向到项目内 `.pytest_tmp`：
       - 跨平台路径一致、可诊断（不再依赖 C:\\Windows\\TEMP）
       - 避免系统 TEMP 的外部清理器/权限竞态干扰
    2. 替换 tempfile.TemporaryDirectory 为 _RetryTemporaryDirectory：
       - Windows 上 chroma sqlite 句柄占用时重试清理，最终保留+告警
    """
    _orig_dir = tempfile.tempdir
    _orig_cls = tempfile.TemporaryDirectory
    _base = PROJECT_ROOT / ".pytest_tmp"
    _base.mkdir(exist_ok=True)
    tempfile.tempdir = str(_base)
    tempfile.TemporaryDirectory = _RetryTemporaryDirectory
    yield
    tempfile.tempdir = _orig_dir
    tempfile.TemporaryDirectory = _orig_cls


@pytest.fixture(scope="function", autouse=True)
def reset_global_singletons():
    """每个测试后清理模块级全局单例与 ContextVar，防止测试间状态污染。

    Why: error_handler/metrics/state_manager/tracing 均为模块级单例，其内部
    计数器、字典、注册表会在测试间累积；circuit_breaker/disaster_recovery/
    graceful_degrade 的 _trace_id_ctx ContextVar 也会泄漏 trace_id。
    除 tracing 为懒加载单例外，其余 getter 不重建实例，故采用「清空实例内部
    状态容器」策略以保持实例引用稳定（避免 session-scope fixture 持有的旧
    引用失效）。
    另: setup_agent_logging() 会清除 root logger 的 handler 并添加带
    EmojiFilter/SensitiveDataFilter 的 handler，若不恢复会导致后续测试的
    emoji 被替换为 [ROCKET] 等、audit 模块日志被过滤。故在 yield 前快照
    root logger 的 handlers/level，yield 后恢复。
    """
    # 0. 快照 root logger 状态（防止 setup_agent_logging 污染）
    _root_logger = logging.getLogger()
    _saved_handlers = _root_logger.handlers[:]
    _saved_level = _root_logger.level
    yield
    # 0. 恢复 root logger 状态
    _root_logger.handlers = _saved_handlers
    _root_logger.setLevel(_saved_level)
    # 0b. 重置 logging 进程级全局开关 Manager.disable（防泄漏兜底）
    # Why: logging.disable(level) 设置 Manager.disable 后，任何 logger 的
    # isEnabledFor 对低于该 level 的记录恒 False（进程级屏蔽，conftest 的
    # 快照/恢复均不覆盖此属性）。若某测试因断言失败未恢复，后续所有依赖
    # INFO 级日志 filter 链的测试静默失败（perf_monitor 7 失败根因：
    # stress_test 的 logger.info() 静默丢弃记录 → 注入 filter 永不触发、
    # errors=0 与断言矛盾）。此处强制复位 NOTSET，阻断同类泄漏。
    logging.root.manager.disable = logging.NOTSET
    # 1. ErrorHandler: 清空错误计数与熔断器注册表
    try:
        from agent.error_handler import get_error_handler
        _inst = get_error_handler()
        _inst._metrics.clear()
        _inst._circuit_breakers.clear()
    except Exception:
        pass
    # 2. MetricsCollector: 清空 histogram 与 counter
    try:
        from agent.monitoring.metrics import get_metrics_collector
        _inst = get_metrics_collector()
        _inst._histograms.clear()
        _inst._counters.clear()
    except Exception:
        pass
    # 3. ServerState: __init__ 无副作用，重新初始化实例属性（不替换实例）
    try:
        from agent.state_manager import get_server_state
        get_server_state().__init__()
    except Exception:
        pass
    # 4. TraceStorage: 懒加载单例，置 None 触发下次访问重建
    try:
        import agent.monitoring.tracing as _tr
        _tr.reset_trace_storage()
    except Exception:
        pass
    # 5. ContextVar 重置：circuit_breaker / disaster_recovery / graceful_degrade / tracing
    # Why: tracing._current_trace_id 若被前序测试 set_trace_id() 设置且未恢复，
    # TraceContext.__enter__ 会复用旧值而非生成新 ID，导致唯一性/长度断言失败
    for _mod_path, _var_name in (
        ("agent.circuit_breaker", "_trace_id_ctx"),
        ("agent.disaster_recovery", "_trace_id_ctx"),
        ("agent.graceful_degrade", "_trace_id_ctx"),
        ("agent.monitoring.tracing", "_current_trace_id"),
        ("agent.monitoring.tracing", "_current_span_id"),
    ):
        try:
            _mod = __import__(_mod_path, fromlist=[_var_name])
            _var = getattr(_mod, _var_name, None)
            if _var is not None:
                _var.set(None)
        except Exception:
            pass
    # 6. CircuitBreaker: 清空所有命名熔断器实例（如 schema_validation）
    # Why: OutputSchemaValidator.parse_and_validate 通过 get_circuit_breaker("schema_validation")
    # 获取命名熔断器，前序测试的 record_failure 累积会导致熔断器打开，后续测试进入降级路径
    try:
        from agent.circuit_breaker import reset_breakers
        reset_breakers()
    except Exception:
        pass
    # 7. GracefulDegrade: 重置降级管理器单例状态（_states/_metrics/_module_states）
    # Why: 前序测试触发错误会累积 _states 中的错误计数，导致降级级别升至 LENIENT，
    # 使 OutputSchemaValidator 返回 degraded_lenient 响应而非 ErrorMessage
    try:
        from agent.graceful_degrade import reset_degrade_manager
        reset_degrade_manager()
    except Exception:
        pass
    # 8. browser_tools: 保留原有清理（防止真实 Chrome 实例泄漏）
    try:
        import agent.tools.browser_tools as _bt
        _bt._browser_instance = None
    except Exception:
        pass
    # 9. error_reporting_config: 重置敏感字段模式列表
    # Why: set_sensitive_patterns 会覆盖默认 _sensitive_patterns，若前序测试未恢复，
    # 会导致 Authorization 等敏感 key 不被识别，后续测试脱敏行为异常
    # 注: 只重置 _sensitive_patterns，不调用 _reset_for_test() 以避免影响 _sentry_initialized
    try:
        import agent.error_reporting_config as _erc
        _erc._sensitive_patterns = list(_erc._DEFAULT_SENSITIVE_PATTERNS)
    except Exception:
        pass
    # 10. system_prompt_config: 重置 _manager 单例
    # Why: 前序测试调用 get_manager() 创建单例并缓存配置,可能导致后续测试
    # 的配置查询读到陈旧缓存。重置确保每个测试拿到干净的配置管理器。
    try:
        import agent.system_prompt_config as _spc
        _spc.reset_system_prompt_manager()
    except Exception:
        pass
    # 11. sqlite_vec: 清理 sys.modules 中所有 sqlite_vec 相关键
    # Why: test_vector_store_sqlite_vec.py 的 _enable_sqlite_vec_for_tests 用
    # patch.dict(sys.modules, ...) 覆盖 _BlockModules 封禁，其 __exit__ 会
    # _clear_dict 清空测试期间新导入的模块键（如 sqlite_vec.util）但父包属性
    # 仍引用旧模块对象，形成 sys.modules 与包属性不一致的残留。删除所有
    # sqlite_vec* 键，强制后续测试全新导入（或被 _BlockModules 封禁），
    # 避免 C 扩展重复加载/引用残留导致偶发 ERROR。
    try:
        import sys as _sys
        for _key in [k for k in list(_sys.modules) if k == "sqlite_vec" or k.startswith("sqlite_vec.")]:
            _sys.modules.pop(_key, None)
    except Exception:
        pass
    # 12. memory.vector_store: 清空共享编码器单例缓存
    # Why: VectorStore._get_shared_encoder（vector_store.py）是模块级单例缓存，
    # 前序测试若在 sentence_transformers 被 mock（MagicMock 模块）的上下文中
    # 实例化 VectorStore，会把 mock 编码器缓存进 _shared_encoder_cache。后续
    # sqlite-vec 测试即使 patch 了 SentenceTransformer，_get_shared_encoder 仍
    # 命中缓存返回 mock 编码器，get_sentence_embedding_dimension() 得到 MagicMock，
    # vec0 DDL 构造失败降级 json → "expected sqlite_vec, got json"（随机序
    # TestVectorStoreSqliteVecIntegration 8 ERROR + backend 1 FAILED 根因）。
    try:
        import memory.vector_store.vector_store as _vstore
        _vstore._shared_encoder_cache.clear()
        # 12b. 移除被 mock 污染的 sentence_transformers 模块
        # Why: test_reranker.py 模块级 `sys.modules["sentence_transformers"] =
        # MagicMock()`（真实模块未导入时设置）会永久残留；VectorStore 从 Mock
        # 模块取 SentenceTransformer 类（可调用不抛异常）→ 缓存 Mock 编码器 →
        # memory 全链路 add 失败（assert 0 == N，seed 顺序下 11 个失败根因）。
        # 移除后强制后续重新导入真实模块；真实模块不可导入时 _get_shared_encoder
        # 返回 None（不缓存），VectorStore 正确降级 JSON。
        import sys as _sys
        _st_mod = _sys.modules.get("sentence_transformers")
        if _st_mod is not None and hasattr(_st_mod, "mock_calls"):
            _sys.modules.pop("sentence_transformers", None)
        # 注意：不要清理被 mock 污染的 transformers。Why: 真实 import transformers
        # 会加载 torch C 扩展 → Windows 0xC0000005 崩溃（reranker 测试模块级 mock
        # 三件套正是防崩溃屏障）。transformers 的 MagicMock 残留阻止任何后续真实
        # import（import 链返回 Mock，不加载 torch）→ 安全失败模式。
    except Exception:
        pass
    # 13. 强制恢复被 patch 泄漏的类静态方法（MessageHandler 5 个）
    # Why: e2e 测试大量 patch("agent.orchestrator.message_handler.MessageHandler.*")，
    # 泄漏后 MagicMock 覆盖真实实现 → test_message_handler / test_orchestrator_boundary
    # 的 is_follow_up/detect_dissatisfaction/extract_keywords 断言失败。
    _force_restore_golden_methods()
    # 14. 强制重置 IntentRouter._rules（deepcopy 默认规则）
    # Why: 意图规则注册表被清空/污染后 classify 全部返回 unknown（response_workflows 17 失败）。
    _force_reset_intent_rules()
    # 15. 强制重置 task_scheduler 单例（Mock 泄漏时置 None 重建）
    # Why: patch("agent.task_scheduler._scheduler") 泄漏 → get_scheduler() 返回 Mock。
    _force_reset_scheduler_singleton()
    # 16. SingletonManager 定向重置「带 cleanup 钩子的线程持有型单例」
    # Why（污染治理 P0，2026-08-31）：async_executor / resource_monitor /
    #     self_healer / task_scheduler 注册时带 cleanup_fn（关闭线程池/释放资源）。
    #     前序测试若初始化过这些单例且未清理，其线程池非 daemon 线程不退出 →
    #     长跑累积资源耗竭 → 后续测试 setup 级联失败（全量 22072 errors 放大器）。
    #     这里仅在「已初始化」时定向 reset，cleanup 钩子负责真正释放资源。
    #     为什么不是 reset_all_singletons()：其余单例无 cleanup 钩子，直接删除
    #     实例不会释放其内部资源（见第 17 项 lazy_loader），且会让 session 级
    #     fixture 持有的旧引用失效——沿用「定向重置」策略，不动无钩子单例。
    try:
        from agent.utils.singleton_manager import is_initialized as _sm_is_initialized
        from agent.utils.singleton_manager import reset_singleton as _sm_reset_singleton
        for _sn in ("async_executor", "resource_monitor", "self_healer", "task_scheduler"):
            try:
                if _sm_is_initialized(_sn):
                    _sm_reset_singleton(_sn)
            except Exception:
                pass
        for _sn, _mod_path, _getter in (
            ("lazy_loader", "agent.lazy_loader", "get_lazy_loader"),
            ("async_lazy_loader", "agent.lazy_loader_async", "get_async_lazy_loader"),
        ):
            try:
                if not _sm_is_initialized(_sn):
                    continue
                _inst = getattr(__import__(_mod_path, fromlist=[_getter]), _getter)()
                if _inst is not None and hasattr(_inst, "shutdown"):
                    _inst.shutdown()
                _sm_reset_singleton(_sn)
            except Exception:
                pass
    except Exception:
        pass

# ============================================================================
# 测试断言辅助函数
# ============================================================================

def assert_response_success(response: Dict[str, Any], msg: str = ""):
    """断言响应成功"""
    assert response.get("success", False), f"响应失败: {msg}, 响应: {response}"

def assert_error_type(error: Exception, expected_type: type, msg: str = ""):
    """断言错误类型"""
    assert isinstance(error, expected_type), \
        f"{msg} 期望错误类型: {expected_type}, 实际: {type(error)}"

def assert_metrics_threshold(metrics: Dict[str, float], thresholds: Dict[str, float]):
    """断言指标在阈值范围内"""
    for key, threshold in thresholds.items():
        value = metrics.get(key)
        if value is not None:
            assert value <= threshold, \
                f"指标 {key} 超标: {value} > {threshold}"

# ============================================================================
# 测试跳过条件（逻辑已合并到上方的 pytest_collection_modifyitems 中）
# ============================================================================

def pytest_addoption(parser):
    """添加命令行选项"""
    parser.addoption(
        "--runslow",
        action="store_true",
        default=False,
        help="运行慢速测试"
    )
    parser.addoption(
        "--env",
        action="store",
        default="development",
        help="指定测试环境"
    )
    parser.addoption(
        "--report-format",
        action="store",
        default="html",
        choices=["html", "json", "xml"],
        help="测试报告格式"
    )

# ============================================================================
# pytest钩子 - 测试结果收集
# ============================================================================

def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """测试结束后的终端摘要"""
    if exitstatus == 0:
        terminalreporter.write_sep("=", "所有测试通过！✓", green=True, bold=True)
    else:
        terminalreporter.write_sep("=", "测试失败 - 需要修复！✗", red=True, bold=True)

    # 输出关键统计
    stats = terminalreporter.stats
    terminalreporter.write_line("\n测试统计:")
    terminalreporter.write_line(f"  通过: {len(stats.get('passed', []))}")
    terminalreporter.write_line(f"  失败: {len(stats.get('failed', []))}")
    terminalreporter.write_line(f"  跳过: {len(stats.get('skipped', []))}")

    # 【S11-09】把"收集级跳过"单独标注出来。
    # 背景：pytest 的结尾 outcome 合计取自 `stats`，而 `--collect-only` 报的
    # "N tests collected" 取自 `_numcollected`（只数 Item）。一个模块级
    # `pytest.skip(..., allow_module_level=True)` 会产生一条 **0 条目**的
    # CollectReport：`stats["skipped"] += 1` 但 `_numcollected += 0`
    # （见 `_pytest/terminal.py::pytest_collectreport`）。
    # ⇒ 合计 = collected + 收集级跳过数，历史上被误读为"少了/多了 1 个用例"。
    # 这里显式分列，使该差值**自带出处**，无需再靠人去追因。
    # 判据：用例级 skip 的 nodeid 含 `::`（如 `a/b.py::test_x`）；
    #       收集级 skip 的 nodeid 是裸文件路径（如 `a/b.py`）。
    skipped = stats.get("skipped", [])
    collection_skips = [r for r in skipped if "::" not in getattr(r, "nodeid", "")]
    if collection_skips:
        terminalreporter.write_line(
            f"    ├ 其中收集级（非用例）: {len(collection_skips)}"
            "  ← 计入上面/结尾的 outcome 合计，但**不计入** --collect-only 的条目数"
        )
        terminalreporter.write_line(
            f"    └ 用例级: {len(skipped) - len(collection_skips)}"
        )

# ============================================================================
# 导出公共API
# ============================================================================

__all__ = [
    "TestDataManager",
    "assert_response_success",
    "assert_error_type",
    "assert_metrics_threshold",
]
