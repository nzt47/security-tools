# -*- coding: utf-8 -*-
"""B2 · 路由决策事件落盘 sink 单测（全部 tmp_path，不写生产 data/logs）

被测对象是 **app_server.py 的生产源码本身**：本模块按
"# [B2-ROUTE-SINK-BEGIN]" / "# [B2-ROUTE-SINK-END]" 标记把该段原样抽取出来、
原样 exec，因此断言的是真实实现，不是副本。

【为什么不直接 import app_server（不易）】
app_server.py 模块级就会执行 _Yunshu = DigitalLife(_cfg.merged) 与 start()
（实测 80–100s，并启动 embedding/reranker 子进程、GB 级内存；同类说明见
tests/unit/test_graceful_shutdown_persist.py 开头）。一个 sink 单测不得付出
这个代价，也不得在 import 期真的去写生产 data/logs。生产路径由 B2 的端到端
验收覆盖（真启服务 → 真发请求 → 读真文件）。

断言清单：
1. sink 被正确装配：handler 挂在规定 logger 上、目标文件 = <log_dir>/<今天>.jsonl
2. 路由类事件按预期落到文件（route_decision / layer.* / intent_layer.* / tool_retrieval）
3. 非路由事件不落盘（action 门 + logger 门）
4. 开关 CP_ROUTE_EVENT_SINK_ENABLED=0 时：无 handler、无写入
5. 幂等：重复装配不叠加 handler（不重复写行）
6. 单条写盘失败不向业务线程抛出（守"埋点不阻断主链路"）
7. 装配点未触动 A1 的 guarded_startup / cleanup_port_listeners 调用关系（静态核对）
"""

import datetime
import json
import logging
import os

import pytest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_APP_SERVER_PATH = os.path.join(_PROJECT_ROOT, "app_server.py")
_BEGIN = "# [B2-ROUTE-SINK-BEGIN]"
_END = "# [B2-ROUTE-SINK-END]"

_ROUTE_LOGGER = "agent.orchestrator"
_TOOL_LOGGER = "agent.observability.tool_trace"
_UNRELATED_LOGGER = "agent.skills_mgmt.loader"


def _read_app_source():
    with open(_APP_SERVER_PATH, "r", encoding="utf-8") as fh:
        return fh.read()


def _extract_sink_source(src=None):
    """抽取 app_server.py 中 [B2-ROUTE-SINK-BEGIN]..[B2-ROUTE-SINK-END] 之间的源码"""
    src = _read_app_source() if src is None else src
    assert src.count(_BEGIN) == 1, "标记 [B2-ROUTE-SINK-BEGIN] 必须恰好出现一次"
    assert src.count(_END) == 1, "标记 [B2-ROUTE-SINK-END] 必须恰好出现一次"
    body = src.split(_BEGIN, 1)[1].split(_END, 1)[0]
    assert "def install_route_event_sink" in body
    assert "class RouteEventJsonlHandler" in body
    return body


@pytest.fixture()
def sink_ns():
    """把抽取出的生产代码 exec 成一个命名空间（只注入它用到的 stdlib 名）"""
    ns = {
        "__name__": "app_server_route_sink_extract",
        "logging": logging,
        "json": json,
        "os": os,
        "datetime": datetime,
    }
    exec(compile(_extract_sink_source(), _APP_SERVER_PATH, "exec"), ns)
    yield ns
    ns["_detach_route_event_sink"]()


@pytest.fixture()
def route_logger_level():
    """固定被测 logger 的级别：pytest 下 root 级别不确定，断言不得依赖它"""
    saved = {}
    for name in (_ROUTE_LOGGER, _TOOL_LOGGER, _UNRELATED_LOGGER):
        lg = logging.getLogger(name)
        saved[name] = lg.level
        lg.setLevel(logging.INFO)
    yield
    for name, lvl in saved.items():
        logging.getLogger(name).setLevel(lvl)


def _install(sink_ns, tmp_path, **kw):
    """装配 sink 并把落盘目录指向 tmp_path（生产默认目录绝不被本文件触碰）"""
    return sink_ns["install_route_event_sink"](log_dir=str(tmp_path), **kw)


def _today_file(tmp_path):
    name = datetime.datetime.now().strftime("%Y-%m-%d") + ".jsonl"
    return os.path.join(str(tmp_path), name)


def _read_lines(path):
    with open(path, "r", encoding="utf-8") as fh:
        return [json.loads(ln) for ln in fh if ln.strip()]


def _emit(logger_name, action, **extra):
    payload = {"module_name": "orchestrator", "action": action,
               "message": "单测事件", "trace_id_ctx": "ut-test"}
    payload.update(extra)
    logging.getLogger(logger_name).info(payload)


# ────────────────────────────────────────────────────────────────
# 1. 装配
# ────────────────────────────────────────────────────────────────

def test_sink_installed_on_expected_loggers(sink_ns, tmp_path):
    status = _install(sink_ns, tmp_path)
    assert status["installed"] is True, status
    assert status["reason"] == "ok"
    assert status["log_dir"] == os.path.abspath(str(tmp_path))
    assert status["target_file"] == _today_file(tmp_path)
    assert status["loggers"] == [_ROUTE_LOGGER, _TOOL_LOGGER]
    handler = status["handler"]
    assert handler is not None
    for name in (_ROUTE_LOGGER, _TOOL_LOGGER):
        assert handler in logging.getLogger(name).handlers
    # 不改既有语义：propagate 必须仍为 True（控制台 basicConfig 输出不受影响）
    assert logging.getLogger(_ROUTE_LOGGER).propagate is True
    # handler 级别固定 INFO（不打开 DEBUG 洪泛）
    assert handler.level == logging.INFO


# ────────────────────────────────────────────────────────────────
# 2. 事件落盘
# ────────────────────────────────────────────────────────────────

def test_route_events_land_in_jsonl(sink_ns, tmp_path, route_logger_level):
    _install(sink_ns, tmp_path)
    _emit(_ROUTE_LOGGER, "orchestrator.process.route_decision",
          final_layer="llm", decision="success", duration_ms=12.5,
          layer_results={"workflow": {"outcome": "miss"},
                         "template": {"outcome": "miss"},
                         "llm": {"outcome": "success"}})
    _emit(_ROUTE_LOGGER, "orchestrator.layer.rule.hit", layer="rule", decision="hit")
    _emit(_ROUTE_LOGGER, "orchestrator.intent_layer.metric_recorded", layer="llm")
    _emit(_TOOL_LOGGER, "tool_retrieval", top_k=5, fused_candidates=3)

    rows = _read_lines(_today_file(tmp_path))
    assert len(rows) == 4, rows
    events = [r["labels"]["event"] for r in rows]
    assert events == [
        "orchestrator.process.route_decision",
        "orchestrator.layer.rule.hit",
        "orchestrator.intent_layer.metric_recorded",
        "tool_retrieval",
    ]
    # 行格式与既有 data/logs/<date>.jsonl 一致：timestamp / labels / message(JSON 字符串)
    for row in rows:
        assert set(row) == {"timestamp", "labels", "message"}
        assert row["labels"]["app"] == "yunshu-route"
        assert isinstance(row["message"], str)
        body = json.loads(row["message"])
        assert body["action"] == row["labels"]["event"]
        assert body["trace_id_ctx"] == "ut-test"
    first = json.loads(rows[0]["message"])
    assert first["final_layer"] == "llm"
    assert set(first["layer_results"]) == {"workflow", "template", "llm"}
    assert rows[0]["labels"]["level"] == "INFO"
    assert rows[0]["labels"]["logger"] == _ROUTE_LOGGER


def test_non_route_events_are_not_written(sink_ns, tmp_path, route_logger_level):
    """action 门 + logger 门：同一 logger 的非路由事件、其它 logger 的事件都不落盘"""
    _install(sink_ns, tmp_path)
    _emit(_ROUTE_LOGGER, "orchestrator.llm.done", message="[LLM] 正常完成")
    _emit(_ROUTE_LOGGER, "unknown")
    _emit(_UNRELATED_LOGGER, "orchestrator.process.route_decision")  # 借名冒充也不放行
    assert not os.path.exists(_today_file(tmp_path))
    stats = sink_ns["route_event_sink_stats"]()
    assert stats["written"] == 0
    # 只有挂载该 handler 的两个 logger 的记录会被计数（skipped=2）；
    # 第三个来自 _UNRELATED_LOGGER 的事件**根本没进入 handler**（logger 门，
    # 连过滤开销都不付），因此不计入 skipped —— 这正是分双门的意图。
    assert stats["skipped"] == 2


# ────────────────────────────────────────────────────────────────
# 3. 开关
# ────────────────────────────────────────────────────────────────

def test_switch_off_writes_nothing(sink_ns, tmp_path, monkeypatch, route_logger_level):
    monkeypatch.setenv("CP_ROUTE_EVENT_SINK_ENABLED", "0")
    status = _install(sink_ns, tmp_path)
    assert status["installed"] is False
    assert status["reason"].startswith("disabled_by_switch")
    assert status["handler"] is None
    assert not any(isinstance(h, sink_ns["RouteEventJsonlHandler"])
                   for h in logging.getLogger(_ROUTE_LOGGER).handlers)

    _emit(_ROUTE_LOGGER, "orchestrator.process.route_decision", final_layer="llm")
    assert list(tmp_path.iterdir()) == [], "开关置 0 时不得产生任何文件"
    assert sink_ns["route_event_sink_stats"]() is None


@pytest.mark.parametrize("value,expected", [
    ("0", False), ("false", False), ("no", False), ("off", False), (" FALSE ", False),
    ("1", True), ("true", True), ("yes", True), ("", True),
])
def test_switch_parsing(sink_ns, monkeypatch, value, expected):
    monkeypatch.setenv("CP_ROUTE_EVENT_SINK_ENABLED", value)
    assert sink_ns["route_event_sink_enabled"]() is expected


def test_switch_off_detaches_previously_installed_handler(sink_ns, tmp_path, route_logger_level):
    """先开再关：必须摘掉已装 handler，回到"只进 stderr"的改动前状态"""
    _install(sink_ns, tmp_path)
    assert any(isinstance(h, sink_ns["RouteEventJsonlHandler"])
               for h in logging.getLogger(_ROUTE_LOGGER).handlers)
    status = sink_ns["install_route_event_sink"](enabled=False, log_dir=str(tmp_path))
    assert status["installed"] is False
    assert not any(isinstance(h, sink_ns["RouteEventJsonlHandler"])
                   for h in logging.getLogger(_ROUTE_LOGGER).handlers)
    assert not any(isinstance(h, sink_ns["RouteEventJsonlHandler"])
                   for h in logging.getLogger(_TOOL_LOGGER).handlers)


# ────────────────────────────────────────────────────────────────
# 4. 幂等 / 失败姿态
# ────────────────────────────────────────────────────────────────

def test_install_is_idempotent(sink_ns, tmp_path, route_logger_level):
    _install(sink_ns, tmp_path)
    _install(sink_ns, tmp_path)  # 重复装配
    handlers = [h for h in logging.getLogger(_ROUTE_LOGGER).handlers
                if isinstance(h, sink_ns["RouteEventJsonlHandler"])]
    assert len(handlers) == 1, "重复装配不得叠加 handler（否则同一事件写多行）"
    _emit(_ROUTE_LOGGER, "orchestrator.process.route_decision", final_layer="llm")
    assert len(_read_lines(_today_file(tmp_path))) == 1


def test_write_failure_never_raises(sink_ns):
    """写盘失败（磁盘满/文件被占）只计数，绝不向业务线程抛出"""
    class _BoomClient(object):
        def push_log(self, **kwargs):
            raise OSError("disk full")

    handler = sink_ns["RouteEventJsonlHandler"](_BoomClient())
    record = logging.LogRecord(
        name=_ROUTE_LOGGER, level=logging.INFO, pathname=__file__, lineno=1,
        msg={"module_name": "orchestrator",
             "action": "orchestrator.process.route_decision"},
        args=None, exc_info=None)
    handler.emit(record)  # 不得抛出
    stats = handler.stats()
    assert stats["failed"] == 1 and stats["written"] == 0
    assert "OSError" in stats["last_error"]


def test_unparseable_message_is_skipped(sink_ns):
    handler = sink_ns["RouteEventJsonlHandler"](type("C", (), {"push_log": lambda *a, **k: None})())
    for msg in ("纯文本日志", "{不是合法 JSON", 12345):
        record = logging.LogRecord(name=_ROUTE_LOGGER, level=logging.INFO,
                                   pathname=__file__, lineno=1, msg=msg,
                                   args=None, exc_info=None)
        handler.emit(record)
    assert handler.stats() == {"written": 0, "skipped": 3, "failed": 0, "last_error": ""}


def test_json_string_message_is_accepted(sink_ns, tmp_path):
    """历史/其它调用点传 JSON 字符串时同样按 action 门判定（形态不改变判据）"""
    status = _install(sink_ns, tmp_path)
    handler = status["handler"]
    record = logging.LogRecord(
        name=_ROUTE_LOGGER, level=logging.INFO, pathname=__file__, lineno=1,
        msg=json.dumps({"module_name": "orchestrator",
                        "action": "orchestrator.process.route_decision"}),
        args=None, exc_info=None)
    handler.emit(record)
    rows = _read_lines(_today_file(tmp_path))
    assert len(rows) == 1
    assert rows[0]["labels"]["event"] == "orchestrator.process.route_decision"


# ────────────────────────────────────────────────────────────────
# 5. 跨卡：A1 的启动机制调用关系未被本卡触动（静态核对）
# ────────────────────────────────────────────────────────────────

def test_A1_startup_call_relations_untouched_by_this_change():
    src = _read_app_source()
    main_idx = src.index('if __name__ == "__main__":')
    reaper_idx = src.index("install_child_process_reaper()")
    sink_idx = src.index("install_route_event_sink()")
    guarded_idx = src.index("from agent.server_port_guard import guarded_startup")
    preflight_def_idx = src.index("def _startup_preflight():")
    preflight_use_idx = src.index("preflight=_startup_preflight,")

    # ① guarded_startup 仍只在 __main__ 里调用一次，且仍带就绪门 preflight
    assert src.count("guarded_startup(") == 1
    assert guarded_idx > main_idx
    assert preflight_def_idx < guarded_idx < preflight_use_idx
    # ② cleanup_port_listeners 仍未被 app_server 以**代码**调用（A1 把它交给
    #    guarded_startup）；它只允许出现在注释里（app_server.py 的 A1 时序说明）
    code_lines = [ln for ln in src.splitlines() if not ln.lstrip().startswith("#")]
    assert not any("cleanup_port_listeners(" in ln for ln in code_lines)
    # ③ 本卡的 sink 装配在 Job Object 之后、guarded_startup 之前
    assert reaper_idx < sink_idx < guarded_idx
