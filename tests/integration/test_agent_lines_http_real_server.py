# -*- coding: utf-8 -*-
"""主线端点（/api/agent-lines 系列）在**真实 HTTP 服务**下的集成测试

【为什么必须单开这一层（L10）】
    这条链此前只在两种姿态下被验过：
      1. Flask test_client —— 进程内直接调 WSGI app，**不经过 socket**；
      2. 手工脚本 —— 一次性，不随 CI 回归。
    两者都盖不住「真正的 HTTP 服务把请求交给 WSGI app」这一段：请求行/头的解析、
    Content-Type 处理、状态码与响应体的实际序列化，以及路由装饰器
    （trace_route / log_request / require_token）在**真实请求上下文**里的行为。
    下面的用例用 werkzeug.serving.make_server + 真实 TCP 请求把这段补齐。

【为什么它不会成为 CI 抖动源】
    - **端口 0**：监听端口由内核分配（make_server("127.0.0.1", 0, app)），
      测试再把实际端口读回来。这是此类用例最常见的抖动源（写死端口 / TIME_WAIT
      残留），已从结构上消除；pytest-xdist 多 worker 并行也不会互相抢端口。
    - **不出网、不需要任何 key**：只绑 127.0.0.1，只读仓库内已提交的数据
      （data/agent_lines/*.yaml），不触达 LLM / Prometheus / 任何外部服务。
    - **断言全部由仓库内数据派生**：期望值现读 data/agent_lines/engineering.yaml
      现算（如 content == prompt_note.strip()），不硬编码那段文案 ⇒ 档案文案变更
      不会造成假失败；只有**契约**变了才会红。
    - **有界且自负其责**：子进程启动最多等 STARTUP_TIMEOUT，每个请求 REQUEST_TIMEOUT；
      任一环节失败立即中止并带出服务日志，最坏路径 ≈ 40s，远低于 pytest.ini 的
      --timeout=120（该项用 thread 法超时会杀掉整个 pytest 进程，故这里刻意留足余量）。
    - **实测**：连跑 3 次逐字节一致，单次 ≈ 2.5s（含 0.5s 解释器冷启动）。
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENGINEERING_YAML = PROJECT_ROOT / "data" / "agent_lines" / "engineering.yaml"

pytestmark = pytest.mark.integration

#: 子进程监听就绪的最长等待（实测 ~0.5s，留足余量以容忍慢 CI）
STARTUP_TIMEOUT = 30.0
#: 单次 HTTP 请求超时
REQUEST_TIMEOUT = 10.0

#: 探针服务源码：最小 Flask app + make_server(127.0.0.1, 0)。
#: 【为什么写成字符串落到 tmp 再起子进程】独立解释器 ⇒ 不受测试进程的
#: 全局状态/导入顺序影响（本仓有既有的顺序污染史），且「真服务」的边界更干净。
#: 主线程只等停止哨兵，收到后调 server.shutdown() 优雅退出（不强杀）。
_SERVER_SOURCE = '''# -*- coding: utf-8 -*-
import json
import os
import sys
import threading
import time

portfile, stopfile, repo = sys.argv[1], sys.argv[2], sys.argv[3]
if repo not in sys.path:
    sys.path.insert(0, repo)
os.chdir(repo)

from flask import Flask
from werkzeug.serving import make_server

from agent.server_routes.routes_agent_lines import register_routes

app = Flask("agent-lines-http-probe")
register_routes(app, None)

server = make_server("127.0.0.1", 0, app, threaded=True)
thread = threading.Thread(target=server.serve_forever, name="wsgi", daemon=True)
thread.start()

with open(portfile, "w", encoding="utf-8") as fh:
    json.dump({"port": server.socket.getsockname()[1], "pid": os.getpid()}, fh)

deadline = time.time() + 300
while time.time() < deadline:
    if os.path.exists(stopfile):
        break
    time.sleep(0.05)

server.shutdown()
thread.join(timeout=10)
'''


# ════════════════════════════════════════════════════════════
#  基础设施
# ════════════════════════════════════════════════════════════


def _accepts_connection(port: int) -> bool:
    """该端口是否仍接受 TCP 连接（用于关停后确认端口已释放）"""
    sock = socket.socket()
    sock.settimeout(0.5)
    try:
        sock.connect(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def _await_port(proc, portfile: Path, logfile: Path) -> int:
    """等子进程写出端口文件；提前退出或超时 ⇒ 带出服务日志后失败"""
    deadline = time.time() + STARTUP_TIMEOUT
    while time.time() < deadline:
        if portfile.exists():
            try:
                return int(json.loads(portfile.read_text(encoding="utf-8"))["port"])
            except (ValueError, KeyError):
                pass
        if proc.poll() is not None:
            pytest.fail(
                "探针服务进程提前退出（returncode=%s）：\n%s"
                % (proc.returncode, logfile.read_text(encoding="utf-8", errors="replace"))
            )
        time.sleep(0.05)
    pytest.fail(
        "探针服务 %.0fs 内未监听：\n%s"
        % (STARTUP_TIMEOUT, logfile.read_text(encoding="utf-8", errors="replace"))
    )


@pytest.fixture(scope="module")
def live_server(tmp_path_factory):
    """起一个真实 HTTP 服务（子进程），产出 (base_url, port)；用例结束必回收"""
    workdir = tmp_path_factory.mktemp("agent-lines-http")
    script = workdir / "probe_server.py"
    script.write_text(_SERVER_SOURCE, encoding="utf-8")
    portfile = workdir / "port.json"
    stopfile = workdir / "stop.flag"
    logfile = workdir / "server.log"

    env = dict(os.environ)
    # 子进程必须能 import agent.*：显式把仓库根放进 PYTHONPATH（不依赖调用者 cwd）
    env["PYTHONPATH"] = os.pathsep.join(
        [str(PROJECT_ROOT)] + [p for p in [env.get("PYTHONPATH", "")] if p]
    )
    env["PYTHONIOENCODING"] = "utf-8"

    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    log = open(logfile, "wb")
    proc = subprocess.Popen(
        [sys.executable, str(script), str(portfile), str(stopfile), str(PROJECT_ROOT)],
        cwd=str(PROJECT_ROOT), env=env, stdin=subprocess.DEVNULL,
        stdout=log, stderr=subprocess.STDOUT, creationflags=flags,
    )
    port = None
    try:
        port = _await_port(proc, portfile, logfile)
        yield "http://127.0.0.1:%d" % port, port
    finally:
        stopfile.write_text("stop", encoding="utf-8")
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)
        log.flush()
        log.close()
    # 关停断言放在 finally 之后：子进程退出 + 端口不再接受连接
    if port is not None:
        assert proc.poll() is not None, "探针服务子进程未回收"
        for _ in range(40):
            if not _accepts_connection(port):
                break
            time.sleep(0.25)
        assert not _accepts_connection(port), "端口 %d 仍在监听" % port


def _request(url: str, method: str = "GET", payload=None):
    """真实 HTTP 请求；4xx/5xx 也返回 (状态码, body)，不由 urllib 抛异常"""
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        return exc.code, (json.loads(raw) if raw else {})


def _engineering_profile() -> dict:
    """仓库里那份真实档案原样读出来（不做任何加工，直接回灌给端点）"""
    return yaml.safe_load(ENGINEERING_YAML.read_text(encoding="utf-8"))


# ════════════════════════════════════════════════════════════
#  用例
# ════════════════════════════════════════════════════════════


def test_list_endpoint_over_real_http(live_server):
    """GET /api/agent-lines：档案列表 + active 指针（真实 HTTP）"""
    base, _port = live_server
    status, body = _request(base + "/api/agent-lines")
    assert status == 200
    assert body["ok"] is True

    ids = [line["id"] for line in body["lines"]]
    assert "engineering" in ids
    assert body["broken"] == []

    # active 是可变状态（谁都可以激活别的线），故只断言**不变量**：
    # null 或一个确实存在于列表里的 id。
    active = body["active"]
    assert active is None or (isinstance(active, str) and active in ids)

    # 列表回吐的档案必须与磁盘上的真实档案一致（回灌闭环的前提）
    from_disk = _engineering_profile()
    from_api = next(line for line in body["lines"] if line["id"] == "engineering")
    assert from_api["prompt_note"] == from_disk["prompt_note"]
    assert from_api["skills"] == [str(s) for s in from_disk["skills"]]


def test_preview_over_real_http_skills_and_prompt_fragment(live_server):
    """POST /preview：同一响应里 skills 判定与 prompt_fragments 必须都对

    【为什么断言写在一起】这两个字段正是本轮改动新增的「可见面」，且它们分别由
    resolve_skill_pack 与 prompt_builder.line_fragment_for_profile 产出。
    在同一响应里一起断言，才能证明**一次请求**里两套口径都被投影出来。
    """
    base, _port = live_server
    profile = _engineering_profile()
    status, body = _request(base + "/api/agent-lines/preview", "POST", profile)
    assert status == 200
    assert body["ok"] is True
    # 纯计算：预览不得落盘、不得改激活指针
    assert body["saved"] is False

    declared = [str(s) for s in profile["skills"]]
    dedup = list(dict.fromkeys(declared))
    skills = body["skills"]
    assert skills["line_id"] == "engineering"
    assert skills["mode"] == "whitelist"
    # 【不易】allowed 与 unknown 各自**保序**，但拼起来不等于声明顺序
    # （未知 id 夹在中间时会被挤到 unknown 段）⇒ 只能按集合 + 保序分别断言。
    allowed, unknown = list(skills["allowed"]), list(skills["unknown"])
    assert set(allowed) & set(unknown) == set()
    assert set(allowed) | set(unknown) == set(dedup)
    assert allowed == [s for s in dedup if s in set(allowed)]
    assert unknown == [s for s in dedup if s in set(unknown)]

    note = str(profile.get("prompt_note") or "").strip()
    assert note, "engineering.yaml 的 prompt_note 不该为空（否则本用例失去意义）"
    fragments = body["prompt_fragments"]
    assert len(fragments) == 1
    frag = fragments[0]
    assert frag["role"] == "line"
    assert frag["source"] == "line:engineering"
    assert frag["content"] == note
    assert frag["chars"] == len(note)
    # role=line 属 HARD_ROLES ⇒ 不可裁剪；priority 取 DEFAULT_PRIORITY=50
    assert frag["croppable"] is False
    assert frag["priority"] == 50
    # 有片段 ⇒ 没有人读的「为什么没有片段」说明
    assert body["prompt_fragments_note"] == ""


def test_validate_over_real_http_flags_unknown_skill_id(live_server):
    """POST /validate：技能 id 写错必须被点名（真实 HTTP）"""
    base, _port = live_server
    bogus = "no-such-skill-id-2731c4"
    profile = _engineering_profile()
    profile["skills"] = [bogus] + [str(s) for s in profile["skills"]]

    status, body = _request(base + "/api/agent-lines/validate", "POST", profile)
    assert status == 200
    assert body["ok"] is True
    assert body["valid"] is False
    assert body["saved"] is False
    assert any(bogus in issue for issue in body["issues"]), body["issues"]
    assert bogus in list(body["skills"]["unknown"])
    assert bogus not in list(body["skills"]["allowed"])

    # 校验与预览同源：坏技能 id 不影响 prompt 片段判定，两个端点给同一份结论
    assert len(body["prompt_fragments"]) == 1
    assert body["prompt_fragments"][0]["source"] == "line:engineering"
    assert body["prompt_fragments_note"] == ""


@pytest.mark.parametrize(
    "label,patch,keyword",
    [
        ("停用线", {"enabled": False}, "停用"),
        ("空 prompt_note", {"prompt_note": ""}, "prompt_note"),
        ("全空白 prompt_note", {"prompt_note": "  \n\t "}, "prompt_note"),
    ],
)
def test_no_fragment_profiles_report_backend_reason(live_server, label, patch, keyword):
    """没有片段的档案：列表为空 + note 是后端给的人读原因（不是空串）"""
    base, _port = live_server
    profile = _engineering_profile()
    profile.update(patch)

    status, body = _request(base + "/api/agent-lines/preview", "POST", profile)
    assert status == 200
    assert body["prompt_fragments"] == []
    note = body["prompt_fragments_note"]
    assert isinstance(note, str) and note.strip(), "无片段时必须给出人读原因"
    assert keyword in note, "原因未点名成因（%s）：%r" % (label, note)


def test_disabled_and_empty_note_reasons_are_distinct(live_server):
    """两种成因必须给出**不同**的人读原因（否则「为什么没注入」不可诊断）"""
    base, _port = live_server
    off = _engineering_profile()
    off["enabled"] = False
    empty = _engineering_profile()
    empty["prompt_note"] = ""

    _, body_off = _request(base + "/api/agent-lines/preview", "POST", off)
    _, body_empty = _request(base + "/api/agent-lines/preview", "POST", empty)
    assert body_off["prompt_fragments"] == []
    assert body_empty["prompt_fragments"] == []
    assert body_off["prompt_fragments_note"] != body_empty["prompt_fragments_note"]


def test_error_mapping_over_real_http(live_server):
    """错误映射在真实 HTTP 下同样成立：404（不存在）/ 400（请求体非对象）"""
    base, _port = live_server
    status, body = _request(base + "/api/agent-lines/__no_such_line__")
    assert status == 404
    assert body["ok"] is False and "__no_such_line__" in body["error"]

    req = urllib.request.Request(
        base + "/api/agent-lines/preview", data=b"[1, 2, 3]",
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT)
        pytest.fail("非对象请求体本应被拒")
    except urllib.error.HTTPError as exc:
        assert exc.code == 400
        assert json.loads(exc.read().decode("utf-8"))["ok"] is False
