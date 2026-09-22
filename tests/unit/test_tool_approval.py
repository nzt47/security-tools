"""``agent/tool_approval.py``（工具调用审批桥）单元测试

被守护的不变量（对应任务契约的 8 条验收）：
    1. 摘要稳定：键序无关、参数变即摘要变、``None``/``{}`` 等价、不可序列化不抛异常；
    2. **测试隔离**：全部用例只读写 ``tmp_path`` 下的审批记录与消费台账——
       本仓已四次踩到"测试写生产数据"，故额外钉一条"真实 ``data/`` 文件字节不变"；
    3. 挂单：进 ``pending_review``、payload 字段齐全（含 ``risk``）、描述含工具名且
       ≤200 字符、同一次调用重复挂单只留一条（``reused=True``）；
    4. 批准判定：pending ⇒ ``None``；``approved`` ⇒ 命中；换参数 / 会话不匹配 / 超时 ⇒ ``None``；
       记录侧 ``session_key`` 为空串时**通配**；
    5. 消费：单次有效，且**换进程（``reset_cache()``）后仍不可重复消费**（台账落盘）；
    6. 驳回：``rejected`` ⇒ 带 reason 的 dict；pending ⇒ ``None``；
    7. 边界：空工具名、非法参数、记录文件损坏/不存在 ⇒ 结构化失败或 ``None``，**不抛异常**；
    8. 端到端：挂单 → 人工批准（改写记录模拟 UI）→ 命中 → 消费 → 再判定为空。

口径纪律：**不调用** ``ApprovalFlow.approve()/reject()``（那是人工专属动作，由既有路由/UI
调用）；测试里用 ``_verdict()`` 直接改写审批记录来模拟"人在 UI 里点了批准/驳回"。
"""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import agent.tool_approval as TA
from agent.audit import facade as facade_mod
from agent.audit.chain import AuditChain

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

#: 真实（生产）数据文件——本测试的隔离断言对象，**绝不允许**被测试写入
REAL_RECORDS = _PROJECT_ROOT / "data" / "approval_records.jsonl"
REAL_USES = _PROJECT_ROOT / "data" / "tool_approval_uses.jsonl"

_ARGS = {"command": "echo hi"}
_TOOL = "shell_execute"

#: 本用例隔离出来的落点（``isolated_env`` 在 setup 时写入、teardown 清空）
#:
#: Why 要**钉一次**而不是每次重读环境变量：``APPROVAL_RECORDS_PATH`` /
#: ``CP_TOOL_APPROVAL_USES_PATH`` 是**进程级**的，而本仓确有在运行期改写它们的
#: **非 monkeypatch 写者**（``agent/settings/resolver.py`` 的 ``os.environ[...] = ...``、
#: ``import app_server`` 触发的 ``.env`` 重载 —— 见 ``tests/conftest.py`` 的原文记载）。
#: 若用例"挂单写 A 库、改记录时却读 B 库"，断言就会拿**另一只库**的视角去判本库的事：
#: 实测形态是"批次跑偶发红、单跑全绿"（2026-09-22 定位到的那次即此形态；复现与机理见
#: 本文件 ``test_入口解析库路径_运行期改道不会把一次操作劈成两只库``）。
#: 故：落点在夹具里钉一次，用例全程只认它 —— "用例自带隔离"。
_ISOLATED: dict = {}


# ════════════════════════════════════════════════════════════
#  fixtures：全部落 tmp，绝不污染运行时目录
# ════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """审批记录 / 消费台账 / 事件 / 链式审计全部指向 ``tmp_path``

    ``APPROVAL_RECORDS_PATH`` 与 ``CP_TOOL_APPROVAL_USES_PATH`` 是契约要求的两个；
    另外两个（``CP_EVENTS_DIR`` / 链式审计）是**既有审批流自身的副作用**落点
    （``ApprovalFlow.submit`` 会写事件与链式审计），不隔离等于测试照样写生产数据。
    """
    records = tmp_path / "records.jsonl"
    uses = tmp_path / "uses.jsonl"
    monkeypatch.setenv("APPROVAL_RECORDS_PATH", str(records))
    monkeypatch.setenv("CP_TOOL_APPROVAL_USES_PATH", str(uses))
    monkeypatch.setenv("APPROVAL_ENABLED", "1")
    monkeypatch.delenv("CP_TOOL_APPROVAL_TTL_SEC", raising=False)
    monkeypatch.setenv("CP_EVENTS_DIR", str(tmp_path / "events"))

    chain = AuditChain(str(tmp_path / "audit.db"),
                       roots_path=str(tmp_path / "roots.jsonl"),
                       signing_key_path=str(tmp_path / "k.pem"), auto_seal=False)
    previous = facade_mod.audit.bind(chain)
    facade_mod.audit.enabled = True

    import agent.observability.events as events_mod
    events_mod.reset_event_stores()
    TA.reset_cache()
    # 落点在此钉住：用例内的 _verdict / _screen / _records_path() 只认它（不再重读环境）
    _ISOLATED["records"] = records
    _ISOLATED["uses"] = uses
    yield tmp_path
    _ISOLATED.clear()
    TA.reset_cache()
    events_mod.reset_event_stores()
    facade_mod.audit.bind(previous)
    try:
        chain.close(timeout=2.0)
    except Exception:  # noqa: BLE001 清理失败不影响用例结论
        pass


# ════════════════════════════════════════════════════════════
#  测试内小工具
# ════════════════════════════════════════════════════════════


class _Boom:
    """不可序列化到连 ``str()`` 都失败的对象（验证摘要异常收口为 ``""``）"""

    def __str__(self) -> str:  # pragma: no cover - 由用例触发
        raise RuntimeError("boom")


def _records_path() -> Path:
    """本用例的审批记录文件：**夹具钉住的那一个**（不是"此刻环境变量指向的那一个"）

    两者在正常情况下是同一个；一旦某个运行期改写者把环境改道，钉住的这个才是本用例
    真正在隔离目录里操作的那只库 —— 用环境值去判它，得到的是**另一只库的结论**
    （挂单在 A 库、结论取自 B 库 = 随机失败）。夹具未生效时退回环境值，与改动前一致。
    """
    pinned = _ISOLATED.get("records")
    return pinned if pinned is not None else Path(os.environ["APPROVAL_RECORDS_PATH"])


def _uses_path() -> Path:
    """本用例的消费台账：同 :func:`_records_path`（夹具钉住的那一个）"""
    pinned = _ISOLATED.get("uses")
    return pinned if pinned is not None else Path(os.environ["CP_TOOL_APPROVAL_USES_PATH"])


def _flow():
    """与审批路由/UI 同源同库的审批流（``ApprovalFlow()`` 读 ``APPROVAL_RECORDS_PATH``）"""
    from agent.skills_mgmt.approval import ApprovalFlow

    return ApprovalFlow()


def _screen(state: str):
    """以**新实例**读审批记录（等价于"另一个进程/UI 看到的内容"）"""
    return _flow().list({"object_type": TA.OBJECT_TYPE, "state": state})


def _verdict(record_id: str, *, state: str, reason: str = "", actor: str = "reviewer",
             created_at: str = "", session_key: str = None,
             decided_at: str = "") -> None:
    """模拟"人在 UI 里批准/驳回"：直接改写审批记录（人工专属动作不在测试里调用）

    ``decided_at`` 可显式指定**裁决时刻**（写进 ``updated_at``）——"最新裁决为准"
    是按裁决时刻比较的，某些用例需要构造"后建的单先被决定"的时序。
    """
    path = _records_path()
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    out = []
    for line in lines:
        obj = json.loads(line)
        if obj.get("record_id") == record_id:
            obj["state"] = state
            obj["actor"] = actor
            obj["updated_at"] = decided_at or datetime.now().isoformat(timespec="seconds")
            if reason:
                obj["decision_reason"] = reason
            if created_at:
                obj["created_at"] = created_at
            if session_key is not None:
                payload = obj.get("payload") or {}
                payload["session_key"] = session_key
                obj["payload"] = payload
        out.append(json.dumps(obj, ensure_ascii=False))
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    TA.reset_cache()   # 让下一次判定重新读文件（等价于外部进程改过记录）


def _fingerprint(path: Path):
    """文件字节指纹；不存在 → ``None``（用于"原本不存在则断言仍不存在"）"""
    return path.read_bytes() if path.exists() else None


# ════════════════════════════════════════════════════════════
#  1. 摘要
# ════════════════════════════════════════════════════════════


def test_摘要_键序无关_参数变则变_空参数稳定_不可序列化不抛():
    a = TA.tool_call_digest(_TOOL, {"command": "echo hi", "timeout": 30})
    b = TA.tool_call_digest(_TOOL, {"timeout": 30, "command": "echo hi"})
    c = TA.tool_call_digest(_TOOL, {"command": "echo hi"})

    assert a == b, "同参数不同键序必须同摘要"
    assert a != c, "不同参数必须不同摘要"
    assert len(a) == 16 and all(ch in "0123456789abcdef" for ch in a)
    assert a == TA.tool_call_digest(_TOOL, {"command": "echo hi", "timeout": 30})
    assert TA.tool_call_digest(_TOOL, None) == TA.tool_call_digest(_TOOL, {})
    assert TA.tool_call_digest("read_file", None) != TA.tool_call_digest(_TOOL, None)

    # 不可序列化对象：default=str 生效（不抛异常、给出稳定长度的摘要）
    got = TA.tool_call_digest(_TOOL, {"x": object()})
    assert isinstance(got, str) and len(got) == 16

    # 连 str() 都失败 ⇒ 必须收口为 ""（调用方不得据此放行），且不抛异常
    assert TA.tool_call_digest(_TOOL, {"x": _Boom()}) == ""


# ════════════════════════════════════════════════════════════
#  2. 测试隔离
# ════════════════════════════════════════════════════════════


def test_测试隔离_路径全部落在tmp(tmp_path):
    assert Path(os.environ["APPROVAL_RECORDS_PATH"]).parent == tmp_path
    assert Path(os.environ["CP_TOOL_APPROVAL_USES_PATH"]).parent == tmp_path
    assert str(_records_path()).startswith(str(tmp_path))
    assert str(_uses_path()).startswith(str(tmp_path))


def test_换审批库后立即生效_不会读到旧库(tmp_path, monkeypatch):
    """只读实例是按"路径环境值 + 文件指纹"缓存的：换库必须立刻换实例

    否则会跨库返回旧实例 ⇒ 读到**另一个库**的记录（测试隔离与多实例场景都会踩）。
    本用例**不调用** ``reset_cache()``，专门钉住"环境值变化自行发现"。
    """
    first = TA.request_approval(_TOOL, _ARGS)
    assert len(_screen("pending_review")) == 1
    assert TA.pending_snapshot()["count"] == 1

    other = tmp_path / "other_records.jsonl"
    monkeypatch.setenv("APPROVAL_RECORDS_PATH", str(other))

    assert TA.pending_snapshot()["count"] == 0
    assert TA.find_permission(_TOOL, _ARGS) is None
    second = TA.request_approval(_TOOL, _ARGS)
    assert second["approval_id"] != first["approval_id"]
    assert other.exists() and "pending_review" in other.read_text(encoding="utf-8")


def test_入口解析库路径_运行期改道不会把一次操作劈成两只库(tmp_path, monkeypatch):
    """**一次操作只认入口解析的那只库**：运行期改道不得把它劈到两只库上

    Why 要有这条：``APPROVAL_RECORDS_PATH`` 是**进程级**变量，而本仓有非 monkeypatch 的
    改写者（``agent/settings/resolver.py`` 的 ``os.environ[...] = ...``、``import app_server``
    触发的 ``.env`` 重载 —— 见 ``tests/conftest.py``）。若 ``request_approval`` 的每一步
    各自在调用点重读环境，"挂单"会落到改道后的另一只库，而本次操作的其余步骤仍按原库
    判定 ⇒ "单次有效"与"最新裁决为准"同时失效（实测形态：人的"重新挂单并批准"被静默吃掉，
    用例表现为"批次跑偶发红、单跑全绿"）。

    本用例把改道插在**入口解析之后**（``_expire_stale_pending`` 内），断言它对本次操作不可见。
    """
    other = tmp_path / "other_records.jsonl"
    original = TA._expire_stale_pending

    def _flip(store: str = "") -> None:
        original(store)                                     # 先按入口解析到的库做清理
        monkeypatch.setenv("APPROVAL_RECORDS_PATH", str(other))   # 再模拟运行期改道

    monkeypatch.setattr(TA, "_expire_stale_pending", _flip)

    res = TA.request_approval(_TOOL, _ARGS, session_key="s1")

    assert res["ok"] is True
    assert not other.exists(), "运行期改道后本次挂单被写进了另一只库（一次操作被劈开）"
    lines = [ln for ln in _records_path().read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert [json.loads(ln)["record_id"] for ln in lines] == [res["approval_id"]]


def test_真实数据文件在测试前后字节不变():
    before = {p: _fingerprint(p) for p in (REAL_RECORDS, REAL_USES)}

    res = TA.request_approval(_TOOL, _ARGS, reason="隔离验证", session_key="s1")
    assert res["ok"] is True
    _verdict(res["approval_id"], state="approved")
    hit = TA.find_permission(_TOOL, _ARGS, session_key="s1")
    assert hit is not None
    assert TA.consume(hit["approval_id"], _TOOL, _ARGS) is True

    # 证据：这一轮真的写了盘（写的是 tmp，不是生产）
    assert _records_path().exists() and _records_path().stat().st_size > 0
    assert _uses_path().exists() and _uses_path().stat().st_size > 0

    after = {p: _fingerprint(p) for p in (REAL_RECORDS, REAL_USES)}
    assert after == before, "测试改动了生产数据文件"
    for path in (REAL_RECORDS, REAL_USES):
        if before[path] is None:
            assert not path.exists(), f"{path} 原本不存在，测试后却出现了"


# ════════════════════════════════════════════════════════════
#  3. 挂单
# ════════════════════════════════════════════════════════════


def test_挂单_进入待审_payload齐全_描述含工具名且不超200():
    args = {"command": "pytest -q tests/unit"}
    res = TA.request_approval(_TOOL, args, reason="跑单测", session_key="s1",
                              source="unit-test")

    assert res["ok"] is True and res["reused"] is False
    assert res["state"] == "pending_review"
    assert isinstance(res["approval_id"], str) and res["approval_id"]

    pending = _screen("pending_review")
    assert len(pending) == 1
    rec = pending[0]
    assert rec.record_id == res["approval_id"]
    assert rec.object_type == "tool_call"
    assert rec.object_id == _TOOL
    assert rec.action == "tool_call"
    assert rec.level == "L1"
    assert rec.actor == "auto"
    assert rec.trigger == "tool_gate"

    payload = rec.payload or {}
    for key in ("tool", "args_digest", "args_preview", "session_key", "source",
                "reason", "risk", "requested_at"):
        assert key in payload, f"payload 缺字段 {key}"
    assert payload["tool"] == _TOOL
    assert payload["args_digest"] == TA.tool_call_digest(_TOOL, args)
    assert payload["args_preview"] == "pytest -q tests/unit"
    assert len(payload["args_preview"]) <= 500
    assert payload["session_key"] == "s1"
    assert payload["source"] == "unit-test"
    assert payload["reason"] == "跑单测"
    assert payload["risk"] == "critical", "risk 必须来自 data/tool_definitions/<tool>.yaml"
    assert datetime.fromisoformat(payload["requested_at"])  # ISO8601 可解析
    # 治理可回溯字段：缺 undo_hint 则 UI 收件箱不出审批气泡（§7 硬规则）
    assert payload["undo_hint"].strip()
    assert payload["compensating_action"] == "", "本层没有工具的补偿动作，如实留空"

    # 描述：人要在收件箱里看懂"在批准什么"
    assert _TOOL in rec.description
    assert "critical" in rec.description
    assert "pytest -q tests/unit" in rec.description
    assert len(rec.description) <= 200

    # 超长参数 ⇒ 描述仍 ≤200（且工具名不被截掉）
    res2 = TA.request_approval("write_file", {"command": "x" * 400})
    assert res2["ok"] is True
    long_rec = [r for r in _screen("pending_review") if r.record_id == res2["approval_id"]][0]
    assert len(long_rec.description) <= 200
    assert "write_file" in long_rec.description
    assert len((long_rec.payload or {})["args_preview"]) <= 500


def test_挂单_重复请求同一次调用_复用同一个单_收件箱只有一条():
    first = TA.request_approval(_TOOL, _ARGS, session_key="s1")
    second = TA.request_approval(_TOOL, _ARGS, session_key="s1")
    third = TA.request_approval(_TOOL, dict(_ARGS), session_key="s1")

    assert first["reused"] is False
    assert second["reused"] is True and third["reused"] is True
    assert second["approval_id"] == first["approval_id"]
    assert third["approval_id"] == first["approval_id"]
    assert len(_screen("pending_review")) == 1, "重复请求把收件箱刷爆了"

    # 参数不同 ⇒ 是另一次调用，必须另挂一单
    other = TA.request_approval(_TOOL, {"command": "echo other"}, session_key="s1")
    assert other["reused"] is False and other["approval_id"] != first["approval_id"]
    assert len(_screen("pending_review")) == 2

    # 会话不同 ⇒ 也是另一次调用
    other_session = TA.request_approval(_TOOL, _ARGS, session_key="s2")
    assert other_session["reused"] is False
    assert len(_screen("pending_review")) == 3


def test_挂单_已消费的批准不再复用_另挂新单():
    """幂等只复用"还能兑现"的单据

    已消费的 ``approved`` 若被复用，调用方拿到的是一张**永远兑现不了**的单号
    （``approved`` 在审批状态机里不能再被批准一次：只能 merged/archived），而收件箱
    只列 ``pending_review`` ⇒ 人工看不到任何待办、无从点击 = 该次调用永久卡死。
    故复用只认未决单（``pending_review``），已裁决的一律另挂新单。
    """
    first = TA.request_approval(_TOOL, _ARGS, session_key="s1")
    _verdict(first["approval_id"], state="approved")
    assert TA.consume(first["approval_id"], _TOOL, _ARGS) is True

    again = TA.request_approval(_TOOL, _ARGS, session_key="s1")
    assert again["reused"] is False
    assert again["approval_id"] != first["approval_id"]
    assert again["state"] == "pending_review"
    # 人工重新看得到一条待办（这正是"不死单"的判据）
    assert [r.record_id for r in _screen("pending_review")] == [again["approval_id"]]
    # 旧单（已批准且已消费）永远不再被当作可复用对象
    assert TA.request_approval(_TOOL, _ARGS, session_key="s1")["approval_id"] == \
        again["approval_id"]


def test_挂单_已批准但未消费也不复用_一律另挂新单():
    """**复用只看未决单**：``approved``（哪怕没被消费、没过期）也不复用

    契约口径：幂等的目标只是"别为同一次**未决**请求重复挂单"。已批准的单不是未决请求，
    复用它会返回一张收件箱里不存在的单号（收件箱只列 ``pending_review``）——人工无从
    点击，链路就只能等 TTL 才可能自愈。故一律另挂新单，让人重新裁决。
    （放行路径不受影响：闸门先问 ``find_permission``，有效批准照样直接放行、不会走到挂单。）
    """
    first = TA.request_approval(_TOOL, _ARGS, session_key="s1")
    _verdict(first["approval_id"], state="approved", actor="alice")
    # 这张单此刻是**有效**批准（未消费、未过期）
    assert TA.find_permission(_TOOL, _ARGS, session_key="s1")["approval_id"] == \
        first["approval_id"]

    again = TA.request_approval(_TOOL, _ARGS, session_key="s1")
    assert again["reused"] is False
    assert again["approval_id"] != first["approval_id"]
    assert again["state"] == "pending_review"
    assert [r.record_id for r in _screen("pending_review")] == [again["approval_id"]]
    # 旧批准仍然有效（未被新单作废），只是不再被幂等复用
    assert TA.find_permission(_TOOL, _ARGS, session_key="s1")["approval_id"] == \
        first["approval_id"]


def test_挂单_已过期的批准不再复用_另挂新单(monkeypatch):
    """已过期的 ``approved`` 同样兑现不了（``find_permission`` 不认它）⇒ 不得复用"""
    res = TA.request_approval(_TOOL, _ARGS, session_key="s1")
    _verdict(res["approval_id"], state="approved")
    assert TA.find_permission(_TOOL, _ARGS, session_key="s1") is not None

    monkeypatch.setenv("CP_TOOL_APPROVAL_TTL_SEC", "0")
    assert TA.find_permission(_TOOL, _ARGS, session_key="s1") is None

    again = TA.request_approval(_TOOL, _ARGS, session_key="s1")
    assert again["reused"] is False
    assert again["approval_id"] != res["approval_id"]
    assert again["state"] == "pending_review"
    assert [r.record_id for r in _screen("pending_review")] == [again["approval_id"]]


def _record(approval_id: str):
    """按 record_id 取审批记录（新实例读盘，等价于"UI 看到的那一条"）"""
    from agent.skills_mgmt.approval import ApprovalFlow

    return ApprovalFlow().get(approval_id)


def test_预览_可读字段取值_多字段带键名_无匹配给个数_不泄露密钥():
    one = TA.request_approval("shell_execute", {"command": "echo hi"})
    assert _record(one["approval_id"]).payload["args_preview"] == "echo hi"

    two = TA.request_approval("write_file", {"path": "/tmp/a.txt", "code": "print(1)"})
    assert _record(two["approval_id"]).payload["args_preview"] == \
        "path=/tmp/a.txt | code=print(1)"

    none = TA.request_approval("read_file", {"timeout": 30, "verbose": True})
    assert _record(none["approval_id"]).payload["args_preview"] == "共 2 个参数"

    # 非白名单字段（尤其疑似密钥）绝不进描述/预览
    secret = TA.request_approval(
        "shell_execute",
        {"command": "echo hi", "api_key": "sk-secret-123", "token": "t0ken"})
    rec = _record(secret["approval_id"])
    assert rec.payload["args_preview"] == "echo hi"
    assert "sk-secret-123" not in rec.payload["args_preview"]
    assert "sk-secret-123" not in rec.description
    assert "t0ken" not in rec.description

    # 空参数 ⇒ 不编造字段
    empty = TA.request_approval("shell_execute", {})
    assert _record(empty["approval_id"]).payload["args_preview"] == "共 0 个参数"


def test_挂单_在UI收件箱里可见且出气泡_风险非空():
    """UI 硬规则（§7）：审批气泡缺 ``undo_hint`` 不出现 ⇒ 人根本批不了这条单

    ``agent/ui_panels/data.py::approval_inbox`` 的 ``bubble.visible`` 由
    ``governance_trace_fields`` 决定，而该函数对 ``object_type="tool_call"`` 只能取
    payload 里的叶子字段。故本用例把"人在 UI 里看得到、批得动"钉死在桥接层。
    """
    from agent.ui_panels.data import approval_inbox

    res = TA.request_approval(_TOOL, _ARGS, session_key="s1")
    view = approval_inbox(flow=_flow(), limit=50)

    assert view["ok"] is True
    items = [i for i in view["items"] if i["record_id"] == res["approval_id"]]
    assert len(items) == 1, "审批单没进 UI 收件箱投影"
    item = items[0]
    assert item["object_type"] == "tool_call"
    assert item["risk"] == "critical", "风险列为空，人工无法判断该不该批"
    assert item["bubble"]["visible"] is True, "缺 undo_hint ⇒ 收件箱不出气泡，人在 UI 里批不了"
    assert item["governance"]["undo_hint_status"] == "resolved"
    assert _TOOL in item["description"] and len(item["description"]) <= 200


def test_undo_hint_非空且UI判定resolved_文案与实现一致_含实际TTL秒数():
    """UI 硬规则 + 文案真实性一并钉住（防以后被删掉 / 被写死 / 与实现脱节）

    ``agent/ui_panels/data.py::approval_inbox`` 的 ``bubble.visible =
    bool(undo_hint or compensating_action)``：``tool_call`` 不在 descriptor 覆盖范围内，
    两个字段只能来自 payload ⇒ 少了它，收件箱里"看得见数据、点不到批准"，闭环在 UI 侧断掉。
    """
    from agent.security.governance_bridge import governance_trace_fields

    res = TA.request_approval(_TOOL, _ARGS, session_key="s1")
    payload = _record(res["approval_id"]).payload

    assert payload["undo_hint"].strip(), "undo_hint 为空 ⇒ 收件箱不出气泡、人批不了"
    assert payload["compensating_action"] == ""
    # 后端硬规则的判定结果（UI 就是按这个决定气泡出不出现）
    gov = governance_trace_fields(TA.OBJECT_TYPE, _TOOL, payload)
    assert gov["undo_hint_status"] == "resolved"
    assert gov["undo_hint"] == payload["undo_hint"]

    # 文案必须如实描述实现语义（改了单次有效 / TTL 实现就要同步改这句话）
    assert "单次有效" in payload["undo_hint"]
    assert "超时自动失效" in payload["undo_hint"]
    assert "驳回" in payload["undo_hint"]
    assert TA.ENV_TTL_SEC in payload["undo_hint"]
    assert "900" in payload["undo_hint"], "默认 TTL 秒数必须填进文案，不能写死一个别的值"

    # 快照里也能看到这张单（排查路径），且 undo_hint 在 payload 里可复查
    snap = TA.pending_snapshot()
    assert snap["ok"] is True and snap["items"][0]["approval_id"] == res["approval_id"]


def test_undo_hint里的TTL秒数跟随实际生效值(monkeypatch):
    """TTL 秒数取自 ``_ttl_seconds()`` 的实际生效值，不是写死的 900"""
    monkeypatch.setenv("CP_TOOL_APPROVAL_TTL_SEC", "120")
    res = TA.request_approval(_TOOL, _ARGS)
    payload = _record(res["approval_id"]).payload
    assert "120" in payload["undo_hint"]
    assert "900" not in payload["undo_hint"]

    # 同一句话描述的语义在实现里也是真的：TTL=120 内有效，改为 0 即失效
    _verdict(res["approval_id"], state="approved")
    assert TA.find_permission(_TOOL, _ARGS) is not None
    monkeypatch.setenv("CP_TOOL_APPROVAL_TTL_SEC", "0")
    assert TA.find_permission(_TOOL, _ARGS) is None


# ════════════════════════════════════════════════════════════
#  4. 批准判定
# ════════════════════════════════════════════════════════════


def test_批准判定_pending为None_批准后命中_换参数或换会话为None():
    res = TA.request_approval(_TOOL, _ARGS, session_key="s1")
    assert TA.find_permission(_TOOL, _ARGS, session_key="s1") is None, "待审不算批准"

    _verdict(res["approval_id"], state="approved", actor="alice")
    hit = TA.find_permission(_TOOL, _ARGS, session_key="s1")
    assert hit is not None
    assert hit["approval_id"] == res["approval_id"]
    assert hit["decided_by"] == "alice"
    assert hit["digest"] == TA.tool_call_digest(_TOOL, _ARGS)
    assert hit["decided_at"]

    # 换一个参数 ⇒ 是另一次调用，不得复用同一张批准
    assert TA.find_permission(_TOOL, {"command": "echo hi!"}, session_key="s1") is None
    # 换工具 ⇒ 不匹配
    assert TA.find_permission("read_file", _ARGS, session_key="s1") is None
    # 记录侧 session_key 非空 ⇒ 必须相等
    assert TA.find_permission(_TOOL, _ARGS, session_key="s2") is None
    assert TA.find_permission(_TOOL, _ARGS, session_key="") is None


def test_批准判定_记录侧会话为空串时通配():
    res = TA.request_approval("read_file", {"path": "a.txt"})       # session_key=""
    assert res["ok"] is True
    _verdict(res["approval_id"], state="approved")

    args = {"path": "a.txt"}
    assert TA.find_permission("read_file", args, session_key="any-session") is not None
    assert TA.find_permission("read_file", args, session_key="") is not None
    assert TA.find_permission("read_file", args) is not None


def test_批准判定_TTL过期不认(monkeypatch):
    res = TA.request_approval(_TOOL, _ARGS)
    _verdict(res["approval_id"], state="approved")
    assert TA.find_permission(_TOOL, _ARGS) is not None

    monkeypatch.setenv("CP_TOOL_APPROVAL_TTL_SEC", "0")
    assert TA.find_permission(_TOOL, _ARGS) is None, "TTL=0 必须视为已过期"


def test_批准判定_裁决时刻过老不认(monkeypatch):
    """TTL 锚在**裁决时刻**（``updated_at``）——2026-09-18 由"创建时刻"改过来

    为什么改：人工可能在挂单很久之后才点批准（排队/离线/夜里），按创建时刻计时会让
    "刚批的单"在批准瞬间就被判过期 ⇒ 人点了批准却什么也没发生。这条 TTL 的真实语义是
    "批准后 N 分钟内要用掉"，故看决定时间。所以本用例构造的是"**批准发生在很久以前**"，
    而不是"单子创建于很久以前"（后者现已**不再**使批准失效，见下一条用例）。
    """
    monkeypatch.setenv("CP_TOOL_APPROVAL_TTL_SEC", "60")
    res = TA.request_approval(_TOOL, _ARGS)
    old = (datetime.now() - timedelta(seconds=600)).isoformat(timespec="seconds")
    _verdict(res["approval_id"], state="approved", decided_at=old)

    assert TA.find_permission(_TOOL, _ARGS) is None


def test_批准判定_创建时刻过老但刚批准仍然有效(monkeypatch):
    """反向锁死：单子挂得久 ≠ 批准失效（否则"夜里挂单、早上批准"永远批不准）"""
    monkeypatch.setenv("CP_TOOL_APPROVAL_TTL_SEC", "60")
    res = TA.request_approval(_TOOL, _ARGS)
    old = (datetime.now() - timedelta(seconds=600)).isoformat(timespec="seconds")
    _verdict(res["approval_id"], state="approved", created_at=old)

    assert TA.find_permission(_TOOL, _ARGS) is not None


# ════════════════════════════════════════════════════════════
#  5. 消费（单次有效 + 落盘）
# ════════════════════════════════════════════════════════════


def test_消费_第一次True第二次False_换进程后仍False():
    res = TA.request_approval(_TOOL, _ARGS)
    _verdict(res["approval_id"], state="approved")
    aid = res["approval_id"]

    assert TA.consume(aid, _TOOL, _ARGS) is True
    assert TA.consume(aid, _TOOL, _ARGS) is False

    TA.reset_cache()          # 模拟换进程（进程内集合缓存清空，只能靠台账）
    assert TA.consume(aid, _TOOL, _ARGS) is False

    lines = [ln for ln in _uses_path().read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 1, "同一次批准只能有一条消费记录"
    entry = json.loads(lines[0])
    assert entry["approval_id"] == aid
    assert entry["tool"] == _TOOL
    assert entry["args_digest"] == TA.tool_call_digest(_TOOL, _ARGS)
    assert datetime.fromisoformat(entry["consumed_at"])


def test_消费_并发下只有一个True():
    res = TA.request_approval(_TOOL, _ARGS)
    aid = res["approval_id"]

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: TA.consume(aid, _TOOL, _ARGS), range(16)))

    assert results.count(True) == 1, "同一 approval_id 只允许被消费一次"
    assert results.count(False) == 15


def test_消费_已消费的批准不再被find_permission认作有效():
    """自查点 1：消费后 ``find_permission`` 必须返回 ``None``（单次有效）"""
    res = TA.request_approval(_TOOL, _ARGS)
    _verdict(res["approval_id"], state="approved")
    hit = TA.find_permission(_TOOL, _ARGS)
    assert hit is not None

    assert TA.consume(hit["approval_id"], _TOOL, _ARGS) is True
    assert TA.find_permission(_TOOL, _ARGS) is None, "已消费的批准不得再被当有效批准"

    TA.reset_cache()   # 换进程语义：只靠台账，结论必须一致
    assert TA.find_permission(_TOOL, _ARGS) is None


def test_消费_边界_缺id或非法参数不消费():
    assert TA.consume("", _TOOL, _ARGS) is False
    assert TA.consume("appr-x", "", _ARGS) is False
    assert TA.consume("appr-x", _TOOL, "not-a-mapping") is False
    assert TA.consume("appr-x", _TOOL, {"x": _Boom()}) is False
    assert not _uses_path().exists(), "非法请求不得写台账"


# ════════════════════════════════════════════════════════════
#  6. 驳回
# ════════════════════════════════════════════════════════════


def test_驳回判定_rejected带原因_pending为None():
    res = TA.request_approval(_TOOL, _ARGS)
    assert TA.is_rejected(_TOOL, _ARGS) is None, "待审不是驳回"

    _verdict(res["approval_id"], state="rejected", reason="不合规：涉及外部网络",
             actor="alice")
    got = TA.is_rejected(_TOOL, _ARGS)
    assert got is not None
    assert got["approval_id"] == res["approval_id"]
    assert got["reason"] == "不合规：涉及外部网络"
    assert got["decided_at"]

    # 驳回 ≠ 批准
    assert TA.find_permission(_TOOL, _ARGS) is None
    # 换参数 ⇒ 不匹配
    assert TA.is_rejected(_TOOL, {"command": "echo other"}) is None


def test_驳回_新的批准不被老驳回压制_链路可恢复():
    """最新裁决优先：人工误驳后重新挂单并批准，链路必须恢复

    真实链路里闸门是"先问 ``is_rejected``、再问 ``find_permission``"，所以旧驳回若一直
    命中，就会把人工**重新挂单并批准**的新决定整个吞掉——模型被明确告知"不要再重试"，
    而人以为自己已经批了 = **审批人的操作被静默忽略**。
    """
    first = TA.request_approval(_TOOL, _ARGS, session_key="s1")
    _verdict(first["approval_id"], state="rejected", reason="先否决：涉及外部网络")
    assert TA.is_rejected(_TOOL, _ARGS, session_key="s1") is not None

    # 人工/主流程重新发起：老驳回不是未决单 ⇒ 另挂一张新的待审单
    again = TA.request_approval(_TOOL, _ARGS, session_key="s1")
    assert again["reused"] is False and again["approval_id"] != first["approval_id"]
    # 新单还没裁决：旧驳回仍然成立（没有"凭空批准"）
    assert TA.is_rejected(_TOOL, _ARGS, session_key="s1")["approval_id"] == \
        first["approval_id"]
    assert TA.find_permission(_TOOL, _ARGS, session_key="s1") is None

    # 人工批准了新的那张 ⇒ 最新裁决生效，旧驳回不再压制
    _verdict(again["approval_id"], state="approved", actor="alice")
    assert TA.is_rejected(_TOOL, _ARGS, session_key="s1") is None, \
        "旧驳回把新的批准吞掉了，链路无法恢复"
    hit = TA.find_permission(_TOOL, _ARGS, session_key="s1")
    assert hit is not None and hit["approval_id"] == again["approval_id"]

    # 该批准被消费（放行一次）后：最新裁决**仍然是"批准"**（消费不是人的一次裁决），
    # 故不报"已被驳回"，而是走"重新挂单"这条路 ⇒ 人工再看到一条待办，链路不死。
    assert TA.consume(hit["approval_id"], _TOOL, _ARGS) is True
    assert TA.find_permission(_TOOL, _ARGS, session_key="s1") is None
    assert TA.is_rejected(_TOOL, _ARGS, session_key="s1") is None
    third = TA.request_approval(_TOOL, _ARGS, session_key="s1")
    assert third["reused"] is False
    assert third["approval_id"] not in (first["approval_id"], again["approval_id"])
    assert [r.record_id for r in _screen("pending_review")] == [third["approval_id"]]


def test_驳回_更新的驳回压过老批准_反向也必须成立():
    """反向：**更新的驳回**必须压过老的批准（老批准不得让调用照旧放行）

    与上一条同源（都走 `_latest_decision`）：若两处各自比较时间，就会出现
    "`is_rejected` 说已驳回、`find_permission` 却把老批准放行"的不对称。
    """
    old = TA.request_approval(_TOOL, _ARGS, session_key="s1")
    _verdict(old["approval_id"], state="approved", actor="alice")

    new = TA.request_approval(_TOOL, _ARGS, session_key="s1")   # 另挂新单
    _verdict(new["approval_id"], state="rejected", reason="后来否决", actor="alice")

    assert TA.find_permission(_TOOL, _ARGS, session_key="s1") is None, \
        "更新的驳回之下，老批准不得再放行"
    got = TA.is_rejected(_TOOL, _ARGS, session_key="s1")
    assert got is not None and got["approval_id"] == new["approval_id"]
    assert got["reason"] == "后来否决"


def test_最新裁决以决定时刻为准_后决定的旧单胜出():
    """排序依据是**裁决时刻**（``updated_at``），不是创建时刻（``created_at``）

    审批记录里可以并存多张同 ``(tool, args_digest, session_key)`` 的单（本层不会主动造，
    但换个提交路径/人工重新发起都可能出现）。真实时序可以是：老单一直挂在待办里，
    人**先驳了后来那张新单、又回头批了老单** ⇒ 最新一次裁决是"批准"。
    若按创建时刻排序就会判反——这就是不能拿 ``created_at`` 当排序依据的理由。
    """
    from agent.skills_mgmt.approval import ApprovalFlow

    first = TA.request_approval(_TOOL, _ARGS, session_key="s1")
    digest = TA.tool_call_digest(_TOOL, _ARGS)
    second = ApprovalFlow().submit(
        TA.OBJECT_TYPE, _TOOL, action=TA.ACTION, description="同事项的第二张单",
        payload={"tool": _TOOL, "args_digest": digest, "args_preview": "echo hi",
                 "session_key": "s1", "source": "", "reason": "", "risk": "critical",
                 "requested_at": datetime.now().isoformat(timespec="seconds")},
        actor="auto", trigger=TA.TRIGGER)
    assert second.record_id != first["approval_id"]
    assert second.state == "pending_review"

    # 后建的 second 先被驳回，先建的 first 后被批准（时刻必须落在 TTL 窗口内，
    # 否则两条都被判过期，用例就测不到"排序"这件事）
    now = datetime.now()
    _verdict(second.record_id, state="rejected", reason="先驳新的",
             decided_at=(now - timedelta(seconds=5)).isoformat(timespec="seconds"))
    _verdict(first["approval_id"], state="approved", actor="alice",
             decided_at=(now - timedelta(seconds=1)).isoformat(timespec="seconds"))

    assert TA.is_rejected(_TOOL, _ARGS, session_key="s1") is None
    hit = TA.find_permission(_TOOL, _ARGS, session_key="s1")
    assert hit is not None and hit["approval_id"] == first["approval_id"]


def test_驳回判定_超时后不再算驳回(monkeypatch):
    res = TA.request_approval(_TOOL, _ARGS)
    _verdict(res["approval_id"], state="rejected", reason="越权")
    assert TA.is_rejected(_TOOL, _ARGS) is not None

    monkeypatch.setenv("CP_TOOL_APPROVAL_TTL_SEC", "0")
    assert TA.is_rejected(_TOOL, _ARGS) is None


# ════════════════════════════════════════════════════════════
#  7. 边界
# ════════════════════════════════════════════════════════════


def test_边界_空工具名与非法参数_结构化失败或None不抛异常():
    empty = TA.request_approval("", _ARGS)
    assert empty["ok"] is False and empty["error"]
    assert TA.request_approval("   ", _ARGS)["ok"] is False
    assert TA.request_approval(_TOOL, "not-a-mapping")["ok"] is False
    assert TA.request_approval(_TOOL, {"x": _Boom()})["ok"] is False

    assert TA.find_permission("", _ARGS) is None
    assert TA.find_permission(_TOOL, "not-a-mapping") is None
    assert TA.find_permission(_TOOL, {"x": _Boom()}) is None
    assert TA.is_rejected("", _ARGS) is None
    assert TA.is_rejected(_TOOL, {"x": _Boom()}) is None

    assert not _records_path().exists(), "非法请求不得挂单"


def test_边界_记录文件不存在(tmp_path, monkeypatch):
    missing = tmp_path / "nope.jsonl"
    monkeypatch.setenv("APPROVAL_RECORDS_PATH", str(missing))
    TA.reset_cache()

    assert TA.find_permission(_TOOL, _ARGS) is None
    assert TA.is_rejected(_TOOL, _ARGS) is None
    snap = TA.pending_snapshot()
    assert snap == {"ok": True, "count": 0, "items": []}

    res = TA.request_approval(_TOOL, _ARGS)
    assert res["ok"] is True and res["state"] == "pending_review"


def test_边界_记录文件损坏行_不抛异常(tmp_path, monkeypatch):
    # 非法 JSON 行：既有审批流跳过并告警 ⇒ 本层照常可用
    bad = tmp_path / "bad.jsonl"
    bad.write_text("{ 这不是 JSON\n", encoding="utf-8")
    monkeypatch.setenv("APPROVAL_RECORDS_PATH", str(bad))
    TA.reset_cache()
    res = TA.request_approval(_TOOL, _ARGS)
    assert isinstance(res, dict) and "ok" in res
    assert isinstance(TA.pending_snapshot(), dict)

    # 顶层是列表的行（非对象）：2026-09-18 之前既有 load 路径会抛 AttributeError，
    # 而且 `_loaded` 停在 False ⇒ 每次请求都再抛一次，整个审批面 500。
    # 该缺口已在上游修掉（agent/skills_mgmt/approval.py::_ensure_loaded 单条损坏只跳过
    # 并 error 留痕）⇒ 本层与审批面都必须**照常可用**，而不是"收口成 ok=False"。
    bad2 = tmp_path / "bad2.jsonl"
    bad2.write_text("[1, 2, 3]\n", encoding="utf-8")
    monkeypatch.setenv("APPROVAL_RECORDS_PATH", str(bad2))
    TA.reset_cache()

    res2 = TA.request_approval(_TOOL, _ARGS)
    assert res2["ok"] is True and res2["approval_id"], (
        f"损坏行之外仍应能正常挂单（上游跳过该行, 不拖垮审批面）: {res2}")
    assert TA.find_permission(_TOOL, _ARGS) is None
    assert TA.is_rejected(_TOOL, _ARGS) is None
    snap2 = TA.pending_snapshot()
    assert snap2["ok"] is True and snap2["count"] == 1

    # 好记录与坏行混在一起时：好记录照样读得到（坏行不该"连坐"）
    mixed = tmp_path / "mixed.jsonl"
    good = tmp_path / "good.jsonl"
    good_res = TA.request_approval(_TOOL, {"command": "echo good"}, session_key="s-mixed")
    mixed.write_text("[1, 2, 3]\n" + json.dumps(
        {"record_id": good_res["approval_id"], "object_type": TA.OBJECT_TYPE,
         "object_id": _TOOL, "action": TA.ACTION, "level": "L1", "state": "pending_review",
         "payload": {"args_digest": TA.tool_call_digest(_TOOL, {"command": "echo good"}),
                     "session_key": "s-mixed"},
         "description": "混排用例"}, ensure_ascii=False) + "\n", encoding="utf-8")
    monkeypatch.setenv("APPROVAL_RECORDS_PATH", str(mixed))
    TA.reset_cache()
    snap3 = TA.pending_snapshot()
    assert snap3["ok"] is True and snap3["count"] == 1, (
        f"坏行把它后面的好记录一起吞了: {snap3}")


def test_边界_消费台账损坏行_不抛异常且不影响后续消费(tmp_path, monkeypatch):
    uses = tmp_path / "uses.jsonl"
    uses.write_text("垃圾行\n" + json.dumps({"approval_id": "appr-old"}) + "\n",
                    encoding="utf-8")
    monkeypatch.setenv("CP_TOOL_APPROVAL_USES_PATH", str(uses))
    TA.reset_cache()

    assert TA.consume("appr-old", _TOOL, _ARGS) is False   # 老消费记录读得回来
    assert TA.consume("appr-new", _TOOL, _ARGS) is True    # 损坏行不阻断后续消费
    assert TA.consume("appr-new", _TOOL, _ARGS) is False


def test_边界_快照limit非法不抛异常():
    TA.request_approval(_TOOL, _ARGS)
    for limit in ("bad", -5, 10 ** 9, None):
        snap = TA.pending_snapshot(limit=limit)  # type: ignore[arg-type]
        assert snap["ok"] is True
        assert snap["count"] == len(snap["items"])
        assert snap["count"] <= 200


# ════════════════════════════════════════════════════════════
#  8. 端到端
# ════════════════════════════════════════════════════════════


def test_端到端_挂单_人工批准_命中_消费_再次判定为空():
    args = {"command": "pytest -q tests/unit"}
    res = TA.request_approval(_TOOL, args, reason="回归验证", session_key="sess-1")
    assert res["ok"] is True and res["state"] == "pending_review"

    snap = TA.pending_snapshot()
    assert snap["ok"] is True and snap["count"] == 1
    item = snap["items"][0]
    assert item["approval_id"] == res["approval_id"]
    assert item["tool"] == _TOOL
    assert item["state"] == "pending_review"
    assert item["session_key"] == "sess-1"
    assert item["args_preview"] == "pytest -q tests/unit"

    # 人工尚未处置：既不是批准也不是驳回
    assert TA.find_permission(_TOOL, args, session_key="sess-1") is None
    assert TA.is_rejected(_TOOL, args, session_key="sess-1") is None

    # 人工在 UI 里批准
    _verdict(res["approval_id"], state="approved", actor="alice", reason="")
    hit = TA.find_permission(_TOOL, args, session_key="sess-1")
    assert hit is not None and hit["approval_id"] == res["approval_id"]

    # 放行一次 ⇒ 消费掉
    assert TA.consume(hit["approval_id"], _TOOL, args) is True
    # 单次有效：再问就不再有批准
    assert TA.find_permission(_TOOL, args, session_key="sess-1") is None
    assert TA.consume(hit["approval_id"], _TOOL, args) is False

    snap2 = TA.pending_snapshot()
    assert snap2["ok"] is True and snap2["count"] == 1
    assert snap2["items"][0]["state"] == "approved", "审批记录本身只读，不改写为已消费"
