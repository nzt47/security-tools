"""回归守护：`tests/test_network_config_integration.py` 导入/收集期不得改写真实配置文件。

背景（2026-09-20 事故，与 2026-08-16 同因复发）：
该文件是**手动演示脚本**（0 个 test 函数），却在模块顶层执行
`NetworkConfigManager().update(...)`，而 pytest 收集 `tests/` 时会 import 它，于是
1. 用例 key `sk-test-1234567890abcdef` 被写进**仓库根真实 `.env`**（`LLM_API_KEY`），
   把部署级真 key 覆盖为占位符 ⇒ 服务重启后 key 校验失败 / 请求 401；
2. `agent/data/network_config.json` 被改写为 `openai/gpt-4` + 空 endpoint
   ⇒ 运行期 `configure_llm` 拿到不支持的模型名而 400。

本用例在**独立子进程**中导入该模块（与 pytest 收集的真实路径一致），
断言**导入期没有任何针对这两个文件的写动作**。若有人把模块级副作用改回来，本用例立即变红。

────────────────────────────────────────────────────────────────────────────
【2026-10-05 修订：判据从「父进程比两次指纹」改成「子进程审计钩子看写动作」】

原实现是「父进程先取指纹 → 起子进程导入 → 父进程再取指纹」。那个窗口里**还有别的写入者**：
同分片（CI 用 `-n 2`，两个 worker 共用同一个工作树）里的其它用例只要动了这两个文件，
本用例就会报「导入演示脚本改写了真实配置文件」—— 实测就是这么红的，
而演示脚本连续 3 次子进程导入、两处指纹逐字节不变（**它被冤枉了**）。
它的红/绿取决于「同分片还有谁动过那个文件」，即**测的不是它声称的那件事**。

修法不是放宽断言，而是换一个**因果上正确**的判据：
子进程用 `sys.addaudithook` 记录 `open`（写模式）/ `os.remove|rename|replace|truncate`
/ `subprocess.Popen` 事件，只统计**指向这两个目标文件**的那些。

  · **别的进程根本不在观测范围内** —— 审计钩子只看得见本进程的 syscall 入口，
    所以「同分片别的用例写了它」再也不会污染结论（这正是原实现的病根）；
  · 原实现还有一个**盲区**：它用 `before[p] is not None` 过滤，
    于是「文件本来不存在、导入期把它创建出来」这一最危险的形态**看不见**
    （而事故正是这种形态）。写模式的 `open` 天然覆盖创建。
  · 顺带钉住「导入一个演示脚本不该起子进程」（起子进程也能绕开进程内观测，故一并断言）。

  探针本身可证伪（本文件后半段）：合成模块**真的去写**时必须报红、只读时必须报绿、
  事故的原始写法（`NetworkConfigManager(...).update(...)`）必须报红、
  以及**父进程另起写入者时结论不受影响**。判据两个方向都能失败，才不是假绿。
"""

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
_TARGETS = (
    REPO_ROOT / ".env",
    REPO_ROOT / "agent" / "data" / "network_config.json",
)

#: 子进程探针：装审计钩子 → 导入被测模块 → 把**观测到的写动作**写进 argv[3] 指定的 JSON 文件。
#: 【为什么用文件而不是 stdout】被测模块自己会往 stdout 打东西，解析 stdout 会与被测输出
#:   的格式耦合；报告走单独文件，判据就只剩「文件里那一份 JSON」。
#: 【为什么异常要 `except BaseException`】演示脚本用 `pytest.skip(allow_module_level=True)`
#:   自保，抛的 `Skipped` 继承自 `BaseException` 而非 `Exception`；本探针不判异常类型，
#:   只关心「导入期有没有写动作」这一个不变量。
_PROBE_SOURCE = '''
import importlib.util
import json
import os
import sys
from pathlib import Path


def _norm(p):
    return os.path.normcase(os.path.abspath(str(p)))


targets = {_norm(t) for t in json.loads(sys.argv[1])}
module_path = sys.argv[2]
report_path = Path(sys.argv[3])

writes = []
spawns = []
hook_errors = []
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND


def _audit(event, args):
    # 审计钩子只看得见**本进程**的文件动作：同分片别的 worker/别的进程写同一个文件
    # 根本不会进入这里 —— 这正是本判据不受「谁还动过它」影响的原因。
    try:
        if event == "open":
            path, mode, flags = args[0], args[1], args[2]
            if mode is not None:
                hit = any(c in str(mode) for c in ("w", "a", "x", "+"))
            else:
                hit = bool(isinstance(flags, int) and flags & _WRITE_FLAGS)
            if hit and _norm(path) in targets:
                writes.append({"path": str(path), "mode": str(mode), "flags": flags})
        elif event in ("os.rename", "os.replace", "os.remove", "os.unlink", "os.truncate"):
            if args and _norm(args[0]) in targets:
                writes.append({"path": str(args[0]), "op": event})
        elif event == "subprocess.Popen":
            spawns.append(str(args[0]) if args else "")
    except Exception as e:  # noqa: BLE001 钩子自己出错要能被看见，不能静默漏观测
        hook_errors.append(repr(e))


sys.addaudithook(_audit)

exc = ""
try:
    spec = importlib.util.spec_from_file_location("_netcfg_probe_target", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
except BaseException as e:  # noqa: BLE001 见上方【为什么异常要 except BaseException】
    exc = type(e).__name__

report_path.write_text(json.dumps({
    "writes": writes,
    "spawns": spawns,
    "hook_errors": hook_errors,
    "exc": exc,
}), encoding="utf-8")
'''


def _run_probe(module_path: Path, targets, report_path: Path) -> dict:
    """在子进程里导入 `module_path`，返回「导入期观察到的写动作」报告。"""
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            _PROBE_SOURCE,
            json.dumps([str(t) for t in targets]),
            str(module_path),
            str(report_path),
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert report_path.exists(), (
        "子进程探针没有报回结果（它没有写出报告文件）——"
        "这时既不能判绿也不能判红，必须让人来看。\n"
        f"rc={proc.returncode}\nstdout 尾部={proc.stdout[-500:]}\nstderr 尾部={proc.stderr[-500:]}"
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["hook_errors"] == [], (
        "审计钩子自身报错 ⇒ 这次观测不完整，不能据此判绿："
        + repr(report["hook_errors"])
    )
    return report


def _synthetic_module(tmp_path: Path, body: str, name: str = "synthetic_demo_module.py") -> Path:
    """写一个合成的「被测模块」，用于证明探针两个方向都能失败。"""
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


# ─────────────────────────────────────────────────────────────────────────────
# 1. 被测不变量：导入演示脚本期间不得出现针对真实配置文件的写动作
# ─────────────────────────────────────────────────────────────────────────────

def test_importing_demo_script_does_not_touch_real_files(tmp_path):
    """导入演示脚本的**那一刻**，不得对 `.env` / `network_config.json` 有任何写动作。"""
    demo = REPO_ROOT / "tests" / "test_network_config_integration.py"
    report = _run_probe(demo, _TARGETS, tmp_path / "probe.json")

    assert report["writes"] == [], (
        "导入 tests/test_network_config_integration.py 期间出现了针对真实配置文件的写动作："
        + repr(report["writes"])
        + " —— 该文件不得在模块级执行写操作（见文件头事故说明）。"
        + f"（子进程导入期异常={report['exc']!r}；起子进程={report['spawns']!r}）"
    )
    assert report["spawns"] == [], (
        "导入演示脚本时起了子进程："
        + repr(report["spawns"])
        + " —— 模块级的进程外动作既不该出现在演示脚本里，也会绕开本探针的观测范围"
          "（审计钩子只看本进程）。若确有此需要，应改成 `if __name__ == '__main__':` 内执行。"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 2. 守卫的守卫：判据两个方向都必须能失败（否则本文件的守护是假绿）
# ─────────────────────────────────────────────────────────────────────────────

def test_探针能检出导入期改写已有文件(tmp_path):
    """合成一个真的会改写文件的模块 ⇒ 探针必须报出它（否则是漏报）。"""
    target = tmp_path / "netcfg.json"
    target.write_text('{"stage": "before"}', encoding="utf-8")
    module = _synthetic_module(
        tmp_path,
        "from pathlib import Path\n"
        + "Path(%r).write_text('{\"stage\": \"after\"}', encoding='utf-8')\n" % str(target),
    )

    report = _run_probe(module, [target], tmp_path / "probe_write.json")

    assert report["writes"], (
        "探针没报出「合成模块在导入期改写了目标文件」——"
        "那它同样会漏掉演示脚本的真实副作用，本文件的守护等于没有。"
        f"实测报告={report}"
    )


def test_探针能检出导入期新建文件(tmp_path):
    """目标文件**本来不存在**、导入期被创建 ⇒ 也必须报出（旧实现的盲区）。"""
    target = tmp_path / "created_by_import.json"
    assert not target.exists()
    module = _synthetic_module(
        tmp_path,
        "from pathlib import Path\n"
        + "Path(%r).write_text('{}', encoding='utf-8')\n" % str(target),
    )

    report = _run_probe(module, [target], tmp_path / "probe_create.json")

    assert report["writes"], (
        "探针报不出「导入期新建了目标文件」——这正是 2026-09-20 事故的形态"
        "（文件本不存在，被模块级代码创建/覆盖），漏掉它等于把最危险的一类放行。"
        f"实测报告={report}"
    )


def test_探针能检出事故的原始写法(tmp_path):
    """把事故的写法放回去（`NetworkConfigManager(...).update(...)`）⇒ 必须报红。

    【为什么单钉这一条】上面两条用的是 `Path.write_text`；而真实事故走的是
    `NetworkConfigManager._save()` 内部的 `open(..., 'w')`。判据要钉在**真实机制**上，
    就得证明它对那条路径同样成立（否则「合成样例过了」并不等于「事故会红」）。
    """
    target = tmp_path / "incident_replay.json"
    module = _synthetic_module(
        tmp_path,
        "import sys\n"
        "sys.path.insert(0, %r)\n"
        "from agent.network_config import NetworkConfigManager\n"
        "NetworkConfigManager(config_file=%r).update({'llm': {'timeout': 60}})\n"
        % (str(REPO_ROOT), str(target)),
        name="incident_replay_module.py",
    )

    report = _run_probe(module, [target], tmp_path / "probe_incident.json")

    assert report["writes"], (
        "探针漏掉了 2026-09-20 事故的原始写法（`NetworkConfigManager(...).update(...)`）——"
        "本文件存在的全部理由就是拦住它，漏掉即等于守护失效。"
        f"实测报告={report}"
    )


def test_探针在无副作用时不误报(tmp_path):
    """反向：被测模块只读、不写 ⇒ 必须报「无写动作」（否则探针会把一切判红）。"""
    target = tmp_path / "untouched.json"
    target.write_text("{}", encoding="utf-8")
    module = _synthetic_module(
        tmp_path,
        "from pathlib import Path\n"
        + "READ_ONLY = Path(%r).read_text(encoding='utf-8')\n" % str(target),
        name="read_only_module.py",
    )

    report = _run_probe(module, [target], tmp_path / "probe_readonly.json")

    assert report["writes"] == [], (
        "被测模块只做了读操作，探针却报出写动作——"
        "它会把「没有副作用」也判红，那样的守卫只会被绕过（或被当成噪声关掉）。"
        f"实测报告={report}"
    )


def test_别的进程在写不影响本判据(tmp_path):
    """**本修订要解决的问题本身**：另一个写入者（另一进程）在写目标文件时，结论不受影响。

    做法：父进程起一个线程持续改写目标文件，同时让子进程探针导入一个只读模块。
    旧实现（父进程前后比指纹）在这种情况下必红；新判据必须仍然判绿 ——
    因为它只观测**本进程**的写动作。
    """
    target = tmp_path / "written_by_someone_else.json"
    target.write_text("{}", encoding="utf-8")
    module = _synthetic_module(
        tmp_path,
        "VALUE = 1\n",
        name="no_side_effect_module.py",
    )

    stop = threading.Event()

    def _external_writer():
        i = 0
        while not stop.is_set():
            i += 1
            target.write_text(json.dumps({"external_writes": i}), encoding="utf-8")
            time.sleep(0.005)

    writer = threading.Thread(target=_external_writer, daemon=True)
    writer.start()
    try:
        time.sleep(0.05)  # 先让外部写入者确实动过这个文件
        report = _run_probe(module, [target], tmp_path / "probe_external.json")
    finally:
        stop.set()
        writer.join(timeout=5)

    assert report["writes"] == [], (
        "别的进程在写这个文件，探针却把它算成了「被测模块写的」："
        + repr(report["writes"])
        + " —— 这正是 2026-10-05 那次误判的形态（同分片别的用例写了它，"
          "本用例却报『导入演示脚本改写了真实配置文件』）。"
    )
