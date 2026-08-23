"""评估回放隔离层 + 回放管道（任务6：沙箱回放与评估隔离加固）

【背景（Why）】
    TASK-08 报告 §4.2 提出"沙箱回放 = 合成边缘案例"：在隔离环境离线回放历史
    轨迹 / 失败案例 / 评估集样本 + 候选产物，验证更新产物对长尾场景的效果。
    审计发现两个前提问题：
      1. 既有扩展/技能执行沙箱为软隔离（同进程执行、黑名单可绕过、资源限制
         不 enforcement）；回放的是**不可信**的历史轨迹与候选产物（含 LLM 生成
         的变异参数、工具调用序列），软隔离执行存在逃逸风险。
      2. 无"轨迹记录 → 隔离回放 → 结果比对"的现成管道。

    本模块建立**评估回放隔离层**：不可信代码在全新解释器进程（进程级隔离）中
    执行，受限 builtins + 受限 import（危险模块惰性代理，属性访问即逃逸判定）+
    环境变量白名单（禁带密钥/代理）+ 只读数据面（样本以脱敏后内存数据注入，
    无文件写路径）+ 墙钟超时强杀 + 输出体积上限 + 并发上限；并提供回放管道
    （复用任务1 评估集回归通道读样本、回放素材走 black_box 脱敏管道）。

【不变式（不易）】
    - 不修改 SkillExecutor 接口与 evaluator.py 评估算法；本模块在**其外部**
      新增执行边界（候选脚本协议与 SkillExecutor 一致：stdin=参数 JSON、
      stdout 最后一行为结果 JSON）。
    - 回放环境与生产零共享写路径：worker cwd 为引擎工作根（系统临时区，
      独立于生产 data/），样本数据在 per-sample scratch 目录（in/out 文件注入，
      用完即删）；不可信代码无文件访问能力（无 open/os），素材目录只读不写。
    - 隔离层失败/不可用时回放**显式失败**（fail-closed），绝不静默降级为同进程
      执行；默认关闭（EVAL_REPLAY_ENABLED=false），开启需显式配置 + 审计。

【开关/参数（优先级: 环境变量 > config.yaml(learning.replay) > 硬编码默认值）】
    EVAL_REPLAY_ENABLED                    总开关（默认 false，安全底线）
    EVAL_REPLAY_BACKEND                    后端（process | docker，默认 process）
    EVAL_REPLAY_RUNNER                     进程后端执行器（spawn | subprocess，
                                           默认 spawn；spawn 不可用时显式失败，
                                           不会静默降级）
    EVAL_REPLAY_TIMEOUT_S                  单样本墙钟超时（默认 30）
    EVAL_REPLAY_MAX_OUTPUT_BYTES           stdout 捕获上限（默认 1MB）
    EVAL_REPLAY_MAX_STDERR_BYTES           stderr 捕获上限（默认 256KB）
    EVAL_REPLAY_MAX_CONCURRENT             并发 worker 上限（默认 1，防并发放大）
    EVAL_REPLAY_MAX_SAMPLES                单 job 样本数上限（默认 200）
    EVAL_REPLAY_WORK_DIR                   scratch 根目录（默认 <系统临时>/yunshu_replay）
    EVAL_REPLAY_MATERIAL_DIR               回放素材只读目录（默认 data/evals）
    EVAL_REPLAY_AUDIT_FILE                 审计 JSONL（默认 data/learning/replay_audit.jsonl）
    EVAL_REPLAY_DOCKER_IMAGE               docker 后端镜像（默认 python:3.12-slim）
    EVAL_REPLAY_MAX_RSS_MB                 内存上限（POSIX setrlimit；Windows 仅记录）

【审计字段（data/learning/replay_audit.jsonl 逐条 JSONL）】
    replay_id / created_at / candidate_id / samples[{id, verdict, duration_ms}] /
    verdict 统计 / duration / resource_usage / evidence(截断) / rollback_command /
    backend / runner / enabled

【CLI】
    python -m agent.learning.replay --candidate <file.py> --samples <samples.json>
        [--out <report.json>] [--enabled] [--timeout 30]
    python -m agent.learning.replay --skill <skill_id> --set v1 --category search
        [--out <report.json>] [--enabled]
    python -m agent.learning.replay --benchmark --samples 5 [--runner subprocess]
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = None  # 延迟绑定（避免重导入链；见 _log()）

# ════════════════════════════════════════════════════════════
#  判定 / 配置常量
# ════════════════════════════════════════════════════════════

VERDICT_SUCCESS = "success"
VERDICT_FAILED = "failed"
VERDICT_TIMEOUT = "timeout"
VERDICT_ESCAPE = "escape"
VERDICTS = (VERDICT_SUCCESS, VERDICT_FAILED, VERDICT_TIMEOUT, VERDICT_ESCAPE)

# 默认值（安全底线：默认关闭）
DEFAULT_ENABLED = False
DEFAULT_BACKEND = "process"
DEFAULT_RUNNER = "spawn"
DEFAULT_TIMEOUT_S = 30
DEFAULT_MAX_OUTPUT_BYTES = 1024 * 1024
DEFAULT_MAX_STDERR_BYTES = 256 * 1024
DEFAULT_MAX_CONCURRENT = 1
DEFAULT_MAX_SAMPLES = 200
DEFAULT_AUDIT_FILE = "data/learning/replay_audit.jsonl"
DEFAULT_MATERIAL_DIR = "data/evals"
DEFAULT_DOCKER_IMAGE = "python:3.12-slim"
DEFAULT_MAX_RSS_MB = 0  # 0 = 不设内存上限（POSIX 默认可设）

_ENV_PREFIX = "EVAL_REPLAY"


def _log() -> Any:
    """延迟获取模块 logger（避免 import 链重依赖）。"""
    global logger
    if logger is None:
        import logging
        logger = logging.getLogger("agent.learning.replay")
    return logger


def _replay_id() -> str:
    return f"replay_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}"


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ════════════════════════════════════════════════════════════
#  配置读取（环境变量 > config.yaml(learning.replay) > 默认值）
# ════════════════════════════════════════════════════════════


def _config_yaml() -> Optional[Dict[str, Any]]:
    cfg_path = Path(__file__).resolve().parent.parent.parent / "config.yaml"
    if not cfg_path.exists():
        return None
    try:
        import yaml as _yaml  # 延迟导入，避免硬依赖
        with open(cfg_path, "r", encoding="utf-8") as f:
            return _yaml.safe_load(f) or {}
    except Exception:  # noqa: BLE001 配置解析失败回退默认
        return None


def _cfg_value(key: str, default: Any) -> Any:
    cfg = _config_yaml()
    if cfg is not None:
        val = ((cfg.get("learning", {}) or {}).get("replay", {}) or {}).get(key)
        if val is not None:
            return val
    return default


def _env_str(key: str, default: str) -> str:
    val = os.getenv(key)
    if val is not None and val.strip() != "":
        return val.strip()
    return str(_cfg_value(key, default))


def _env_int(key: str, default: int) -> int:
    raw = os.getenv(key)
    if raw is not None and raw.strip():
        try:
            return max(0, int(raw.strip()))
        except ValueError:
            pass
    try:
        return max(0, int(_cfg_value(key, default)))
    except (TypeError, ValueError):
        return default


def _env_float(key: str, default: float) -> float:
    raw = os.getenv(key)
    if raw is not None and raw.strip():
        try:
            return max(0.0, float(raw.strip()))
        except ValueError:
            pass
    try:
        return max(0.0, float(_cfg_value(key, default)))
    except (TypeError, ValueError):
        return default


def _env_bool(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is not None and raw.strip() != "":
        return raw.strip().lower() in ("1", "true", "yes", "on")
    val = _cfg_value(key, default)
    if isinstance(val, str):
        return val.strip().lower() in ("1", "true", "yes", "on")
    return bool(val)


def replay_enabled() -> bool:
    """回放总开关（默认关闭，安全底线）。"""
    return _env_bool("EVAL_REPLAY_ENABLED", DEFAULT_ENABLED)


def _timeout_s() -> float:
    return _env_float("EVAL_REPLAY_TIMEOUT_S", DEFAULT_TIMEOUT_S)


def _max_output_bytes() -> int:
    return _env_int("EVAL_REPLAY_MAX_OUTPUT_BYTES", DEFAULT_MAX_OUTPUT_BYTES)


def _max_stderr_bytes() -> int:
    return _env_int("EVAL_REPLAY_MAX_STDERR_BYTES", DEFAULT_MAX_STDERR_BYTES)


def _max_concurrent() -> int:
    return max(1, _env_int("EVAL_REPLAY_MAX_CONCURRENT", DEFAULT_MAX_CONCURRENT))


def _max_samples() -> int:
    return max(1, _env_int("EVAL_REPLAY_MAX_SAMPLES", DEFAULT_MAX_SAMPLES))


def _work_dir() -> Path:
    raw = _env_str("EVAL_REPLAY_WORK_DIR", "")
    if raw:
        return Path(raw)
    return Path(tempfile.gettempdir()) / "yunshu_replay"


def _material_dir() -> Path:
    return Path(_env_str("EVAL_REPLAY_MATERIAL_DIR", DEFAULT_MATERIAL_DIR))


def _audit_file() -> Path:
    return Path(_env_str("EVAL_REPLAY_AUDIT_FILE", DEFAULT_AUDIT_FILE))


def _docker_image() -> str:
    return _env_str("EVAL_REPLAY_DOCKER_IMAGE", DEFAULT_DOCKER_IMAGE)


def _max_rss_mb() -> int:
    return _env_int("EVAL_REPLAY_MAX_RSS_MB", DEFAULT_MAX_RSS_MB)


# ════════════════════════════════════════════════════════════
#  异常
# ════════════════════════════════════════════════════════════


class ReplayError(Exception):
    """回放错误基类"""


class ReplayDisabledError(ReplayError):
    """回放默认关闭：显式拒绝，绝不静默跳过"""


class ReplayIsolationError(ReplayError):
    """隔离层不可用/失败：fail-closed，绝不降级为同进程执行"""


class ReplayJobError(ReplayError):
    """job 校验失败"""


# ════════════════════════════════════════════════════════════
#  数据结构
# ════════════════════════════════════════════════════════════


@dataclass
class ReplaySample:
    """回放样本（task 已脱敏；明文不出隔离环境）"""
    sample_id: str
    task: str
    category: str = "general"
    expected_output: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sample_id": self.sample_id,
            "task": self.task,
            "category": self.category,
            "expected_output": self.expected_output,
            "metadata": self.metadata or {},
        }


@dataclass
class ReplayCandidate:
    """候选产物（不可信：LLM 生成的变异参数/工具调用序列/技能脚本）"""
    candidate_id: str
    code: str
    name: str = ""
    before_version: Optional[str] = None
    rollback_command: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "name": self.name,
            "before_version": self.before_version,
            "rollback_command": self.rollback_command,
        }


@dataclass
class ReplayBudget:
    """单 job 资源预算"""
    timeout_s: float = DEFAULT_TIMEOUT_S
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES
    max_stderr_bytes: int = DEFAULT_MAX_STDERR_BYTES
    max_concurrent: int = DEFAULT_MAX_CONCURRENT
    max_samples: int = DEFAULT_MAX_SAMPLES
    max_rss_mb: int = DEFAULT_MAX_RSS_MB

    def validate(self) -> None:
        if self.timeout_s <= 0:
            raise ReplayJobError(f"timeout_s 必须 > 0（got {self.timeout_s}）")
        if self.max_output_bytes <= 0 or self.max_stderr_bytes <= 0:
            raise ReplayJobError("输出上限必须 > 0")
        if self.max_concurrent < 1:
            raise ReplayJobError("max_concurrent 必须 >= 1（防并发放大）")
        if self.max_samples < 1:
            raise ReplayJobError("max_samples 必须 >= 1")


@dataclass
class ReplayJob:
    """回放任务：样本集 + 候选产物 + 预算"""
    samples: List[ReplaySample]
    candidate: ReplayCandidate
    budget: Optional[ReplayBudget] = None
    job_id: str = ""
    sampleset_version: str = ""
    category: str = ""
    material_dir: Optional[Path] = None
    audit_file: Optional[Path] = None
    notes: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.job_id:
            self.job_id = _replay_id()


@dataclass
class ReplayResult:
    """单样本回放结果"""
    sample_id: str
    verdict: str = VERDICT_FAILED
    duration_ms: float = 0.0
    resource_usage: Dict[str, Any] = field(default_factory=dict)
    evidence: str = ""          # 截断后的结果证据（stdout/error）
    result: Any = None          # 解析后的结果 JSON 快照
    error: Optional[str] = None

    def to_dict(self, evidence_cap: int = 2000) -> Dict[str, Any]:
        return {
            "sample_id": self.sample_id,
            "verdict": self.verdict,
            "duration_ms": round(self.duration_ms, 2),
            "resource_usage": self.resource_usage,
            "evidence": self.evidence[:evidence_cap],
            "result": self.result,
            "error": self.error,
        }


@dataclass
class ReplayReport:
    """一次回放的汇总报告（含审计字段）"""
    replay_id: str = ""
    job_id: str = ""
    candidate_id: str = ""
    sampleset_version: str = ""
    category: str = ""
    sample_count: int = 0
    results: List[ReplayResult] = field(default_factory=list)
    duration_ms: float = 0.0
    resource_usage: Dict[str, Any] = field(default_factory=dict)
    rollback_command: Optional[str] = None
    backend: str = DEFAULT_BACKEND
    runner: str = ""
    enabled: bool = True
    created_at: str = field(default_factory=_now_iso)
    notes: List[str] = field(default_factory=list)

    @property
    def verdict_counts(self) -> Dict[str, int]:
        counts: Dict[str, int] = {v: 0 for v in VERDICTS}
        for r in self.results:
            counts[r.verdict] = counts.get(r.verdict, 0) + 1
        return counts

    @property
    def success_rate(self) -> float:
        if not self.results:
            return 0.0
        ok = sum(1 for r in self.results if r.verdict == VERDICT_SUCCESS)
        return round(ok / len(self.results), 4)

    def to_dict(self, evidence_cap: int = 2000) -> Dict[str, Any]:
        return {
            "replay_id": self.replay_id,
            "job_id": self.job_id,
            "candidate_id": self.candidate_id,
            "sampleset_version": self.sampleset_version,
            "category": self.category,
            "sample_count": self.sample_count,
            "verdict_counts": self.verdict_counts,
            "success_rate": self.success_rate,
            "results": [r.to_dict(evidence_cap=evidence_cap) for r in self.results],
            "duration_ms": round(self.duration_ms, 2),
            "resource_usage": self.resource_usage,
            "rollback_command": self.rollback_command,
            "backend": self.backend,
            "runner": self.runner,
            "enabled": self.enabled,
            "created_at": self.created_at,
            "notes": self.notes,
        }


# ════════════════════════════════════════════════════════════
#  脱敏（black_box.py 同源管道：agent.security_utils.DataSanitizer）
# ════════════════════════════════════════════════════════════

_sanitizer_cache: Optional[Any] = None
_sanitizer_lock = threading.Lock()


def _sanitizer() -> Any:
    """惰性构造 DataSanitizer（与 memory/black_box.py 同一脱敏管道）。"""
    global _sanitizer_cache
    if _sanitizer_cache is not None:
        return _sanitizer_cache
    with _sanitizer_lock:
        if _sanitizer_cache is None:
            try:
                from agent.security_utils import DataSanitizer  # 延迟导入
                _sanitizer_cache = DataSanitizer()
            except Exception as e:  # noqa: BLE001 脱敏器不可用 → 拒绝明文进回放
                raise ReplayIsolationError(
                    f"脱敏器不可用（black_box 管道加载失败）: {e}") from e
        return _sanitizer_cache


def sanitize_text(text: str) -> str:
    """脱敏字符串（手机号/邮箱/密钥等）。"""
    try:
        return _sanitizer().sanitize_string(text)
    except ReplayIsolationError:
        raise
    except Exception as e:  # noqa: BLE001 脱敏失败 → fail-closed，不传明文
        raise ReplayIsolationError(f"脱敏失败: {e}") from e


def sanitize_dict(data: Dict[str, Any]) -> Dict[str, Any]:
    """脱敏字典（递归）。"""
    try:
        return _sanitizer().sanitize_dict(data)
    except ReplayIsolationError:
        raise
    except Exception as e:  # noqa: BLE001
        raise ReplayIsolationError(f"脱敏失败: {e}") from e


def sanitize_sample(sample: ReplaySample) -> ReplaySample:
    """样本入库前脱敏（任务不变式：明文样本不出隔离环境）。"""
    return ReplaySample(
        sample_id=sample.sample_id,
        task=sanitize_text(sample.task),
        category=sample.category,
        expected_output=sanitize_dict(sample.expected_output)
        if isinstance(sample.expected_output, dict) else sample.expected_output,
        metadata=sanitize_dict(sample.metadata)
        if isinstance(sample.metadata, dict) else sample.metadata,
    )


# ════════════════════════════════════════════════════════════
#  静态逃逸预扫描（spawn 前拦截，省资源 + 少一条攻击面）
# ════════════════════════════════════════════════════════════

_ESCAPE_IMPORT_RE = re.compile(
    r"^\s*(?:import|from)\s+(?:os|socket|subprocess|ctypes|shutil|pathlib|"
    r"requests|urllib|http|ssl|ftplib|smtplib|telnetlib|httpx|importlib|"
    r"pkgutil|runpy|multiprocessing|threading|concurrent|signal|resource|"
    r"platform|pdb|traceback|gc|dis|inspect|tempfile|pickle|marshal|mmap|"
    r"winreg|posix|nt|builtins|sysconfig|codecs|io|types|asyncio|select|"
    r"selectors|fcntl|getpass|netrc|poplib|imaplib|xmlrpc|socketserver)\b",
    re.MULTILINE,
)
_ESCAPE_DANGEROUS_ATTR_RE = re.compile(
    r"\b(?:os|socket|subprocess|ctypes|shutil|pathlib|requests|urllib|http)\.",
    re.MULTILINE,
)
_ESCAPE_CALL_RE = re.compile(
    r"(?<![\w.])(?:eval|exec|compile|open|breakpoint)\s*\(",
    re.MULTILINE,
)
_ESCAPE_META_RE = re.compile(
    r"__import__\s*\(|__builtins__\b|"
    r"\b(?:globals|locals|vars|type)\s*\(|\b(?:getattr|hasattr)\s*\(",
    re.MULTILINE,
)
_ESCAPE_DUNDER_RE = re.compile(
    r"\.__(?:class|bases|mro|subclasses|globals|code|dict|builtins|init|"
    r"getattribute|getitem|reduce)__",
    re.MULTILINE,
)


def scan_for_escape(code: str) -> Optional[str]:
    r"""静态逃逸预扫描：命中返回匹配的模式说明，未命中返回 None。

    注意：`compile(` 排除 `re.compile(` 等合法点号前缀调用（(?<![\w.])），
    `eval/exec` 同理；`import sys` 不在拦截清单（由 worker 侧 sys 代理管控）。
    """
    patterns: List[Tuple[str, "re.Pattern[str]"]] = [
        ("危险模块 import", _ESCAPE_IMPORT_RE),
        ("危险模块属性访问", _ESCAPE_DANGEROUS_ATTR_RE),
        ("受限内置调用 eval/exec/compile/open/breakpoint", _ESCAPE_CALL_RE),
        ("反射/元编程调用 __import__/globals/locals/vars/type/getattr/hasattr",
         _ESCAPE_META_RE),
        ("dunder 属性遍历", _ESCAPE_DUNDER_RE),
    ]
    for label, rx in patterns:
        m = rx.search(code)
        if m:
            snippet = code[max(0, m.start() - 20):m.end() + 20].replace("\n", " ")
            return f"静态逃逸预扫描拦截[{label}]: {snippet!r}"
    return None


# ════════════════════════════════════════════════════════════
#  worker 隔离执行逻辑（单一事实来源：spawn 与 subprocess 共用）
#  以全新解释器进程运行；受限 builtins + 受限 import + 逃逸即判
# ════════════════════════════════════════════════════════════

_WORKER_SOURCE = r'''
"""replay 隔离 worker 体（由 agent.learning.replay 内嵌，勿手工编辑）

在隔离进程中以受限环境执行不可信候选代码：
    - 受限 builtins（无 open/eval/exec/type/getattr/globals...）
    - 受限 import：白名单模块放行；危险模块（os/socket/subprocess/...）返回
      惰性代理，任何属性访问抛 _EscapeError → verdict=escape
    - sys 仅暴露 stdin/stdout/stderr（StringIO 捕获，符合 SkillExecutor 协议：
      stdin=参数 JSON，stdout 最后一行为结果 JSON）
    - 输出有界写入（超限即逃逸判定，防内存炸弹）
    - 可选 POSIX 内存上限（setrlimit）

返回 dict: {ok, verdict, stdout, stderr, error, result, duration_ms, rss_kb}
"""

import io
import json
import os
import sys
import time as _time


# 判定常量（worker 独立命名空间内定义，与父模块语义一致）
VERDICT_SUCCESS = "success"
VERDICT_FAILED = "failed"
VERDICT_ESCAPE = "escape"


class _EscapeError(Exception):
    """逃逸尝试标记（原始 traceback 不外泄给不可信代码）"""


class _InertModule:
    """危险模块惰性代理：任何属性访问/调用/写入均判逃逸"""

    def __init__(self, name):
        object.__setattr__(self, "_mname", name)

    def __getattr__(self, attr):
        raise _EscapeError("blocked module attribute: %s.%s" % (self._mname, attr))

    def __setattr__(self, attr, value):
        raise _EscapeError("blocked module write: %s.%s" % (self._mname, attr))

    def __call__(self, *args, **kwargs):
        raise _EscapeError("blocked module call: %s(...)" % self._mname)


class _SysProxy:
    """sys 代理：仅暴露 stdin/stdout/stderr，其余属性访问判逃逸"""

    _ALLOWED = ("stdin", "stdout", "stderr")

    def __init__(self, stdin, stdout, stderr):
        object.__setattr__(self, "_s", {"stdin": stdin, "stdout": stdout,
                                        "stderr": stderr})

    def __getattr__(self, attr):
        if attr in self._ALLOWED:
            return self._s[attr]
        raise _EscapeError("blocked sys attribute: sys.%s" % attr)

    def __setattr__(self, attr, value):
        raise _EscapeError("blocked sys write: sys.%s = ..." % attr)


class _CappedStringIO(io.StringIO):
    """有界输出捕获：超过上限抛 _EscapeError（防内存炸弹）"""

    def __init__(self, limit):
        super().__init__()
        self._limit = limit

    def write(self, s):
        if self.tell() + len(s) > self._limit:
            raise _EscapeError("output limit exceeded (%d bytes)" % self._limit)
        return super().write(s)


# 白名单模块（stdlib 安全子集：无文件/网络/进程能力）
_SAFE_IMPORTS = frozenset({
    "json", "math", "re", "random", "string", "datetime", "collections",
    "itertools", "functools", "textwrap", "unicodedata", "statistics",
    "decimal", "fractions", "numbers", "enum", "copy", "operator", "bisect",
    "heapq", "uuid", "hashlib", "base64", "binascii", "struct", "time",
    "calendar", "dataclasses", "difflib", "pprint", "html", "abc", "typing",
})

# 危险模块清单（惰性代理）
_INERT_IMPORTS = frozenset({
    "os", "socket", "subprocess", "ctypes", "shutil", "pathlib", "requests",
    "urllib", "http", "ssl", "ftplib", "smtplib", "telnetlib", "urllib3",
    "httpx", "importlib", "pkgutil", "runpy", "multiprocessing", "threading",
    "concurrent", "signal", "resource", "platform", "pdb", "traceback", "gc",
    "dis", "inspect", "tempfile", "pickle", "marshal", "mmap", "winreg",
    "posix", "nt", "builtins", "sysconfig", "codecs", "io", "types", "asyncio",
    "select", "selectors", "fcntl", "getpass", "netrc", "poplib", "imaplib",
    "xmlrpc", "socketserver", "cgi", "numpy", "pandas", "yaml", "requests2",
})


def _safe_builtins(guarded_import):
    """受限 builtins 字典（无 open/eval/exec/type/getattr/globals...）"""
    return {
        "abs": abs, "all": all, "any": any, "bin": bin, "bool": bool,
        "bytes": bytes, "bytearray": bytearray, "chr": chr, "complex": complex,
        "dict": dict, "divmod": divmod, "enumerate": enumerate,
        "filter": filter, "float": float, "format": format,
        "frozenset": frozenset, "hash": hash, "hex": hex, "int": int,
        "isinstance": isinstance, "iter": iter, "len": len, "list": list,
        "map": map, "max": max, "min": min, "next": next, "oct": oct,
        "ord": ord, "pow": pow, "print": print, "range": range, "repr": repr,
        "reversed": reversed, "round": round, "set": set, "slice": slice,
        "sorted": sorted, "str": str, "sum": sum, "tuple": tuple, "zip": zip,
        "object": object,
        "__build_class__": __build_class__,
        "__import__": guarded_import,
    }


def _extract_json(stdout):
    """与 SkillExecutor._extract_json 同语义：取 stdout 最后一个 JSON 行"""
    if not stdout:
        return None
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{") or line.startswith("["):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    try:
        return json.loads(stdout.strip())
    except json.JSONDecodeError:
        return None


def _apply_memory_limit(max_rss_mb):
    """POSIX 内存上限（setrlimit）；Windows 无 resource → 跳过（墙钟/输出兜底）"""
    if not max_rss_mb or max_rss_mb <= 0:
        return
    try:
        import resource
        limit = int(max_rss_mb) * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
        resource.setrlimit(resource.RLIMIT_DATA, (limit, limit))
    except Exception:
        pass


def run_sample_in_worker(payload):
    """隔离进程入口：执行单个样本回放，返回结果 dict（单一事实来源）。

    payload = {"sample": {...}, "candidate": {"code": str}, "budget": {...}}
    """
    t0 = _time.time()
    sample = payload.get("sample") or {}
    candidate = payload.get("candidate") or {}
    budget = payload.get("budget") or {}
    code = candidate.get("code") or ""
    max_out = int(budget.get("max_output_bytes") or 1024 * 1024)
    max_err = int(budget.get("max_stderr_bytes") or 256 * 1024)

    # 参数（父进程已脱敏）：以 stdin JSON 注入（SkillExecutor 协议）
    params = sample.get("params") or {}
    try:
        params_json = json.dumps(params, ensure_ascii=False)
    except (TypeError, ValueError) as e:
        return _worker_result(VERDICT_FAILED, "", "", "参数序列化失败: %r" % (e,),
                              None, t0, None)

    stdin_io = io.StringIO(params_json)
    stdout_io = _CappedStringIO(max_out)
    stderr_io = _CappedStringIO(max_err)

    _apply_memory_limit(budget.get("max_rss_mb"))

    # 替换本进程 stdin/stdout/stderr（隔离进程内，不影响父进程）
    sys.stdin = stdin_io
    sys.stdout = stdout_io
    sys.stderr = stderr_io

    def guarded_import(name, globals_=None, locals_=None, fromlist=(), level=0):
        if level != 0:
            raise _EscapeError("relative import blocked")
        top = name.split(".")[0]
        if top == "sys":
            return _SysProxy(stdin_io, stdout_io, stderr_io)
        if top in _SAFE_IMPORTS:
            return __import__(name, globals_, locals_, fromlist, level)
        if top in _INERT_IMPORTS:
            return _InertModule(top)
        raise _EscapeError("import blocked: %s" % name)

    verdict = VERDICT_SUCCESS
    error = None
    result = None
    try:
        safe_globals = {"__builtins__": _safe_builtins(guarded_import)}
        exec(compile(code, "<replay>", "exec"), safe_globals)
    except _EscapeError as e:
        verdict = VERDICT_ESCAPE
        error = str(e)
    except SystemExit as e:  # sys 代理已拦 sys.exit；SystemExit 兜底仍判逃逸
        verdict = VERDICT_ESCAPE
        error = "SystemExit %r (blocked)" % (e,)
    except BaseException as e:  # noqa: BLE001 普通异常 = 候选代码执行失败
        verdict = VERDICT_FAILED
        error = "%s: %s" % (type(e).__name__, e)

    stdout_val = stdout_io.getvalue()
    stderr_val = stderr_io.getvalue()

    if verdict == VERDICT_SUCCESS:
        result = _extract_json(stdout_val)
        if result is None:
            verdict = VERDICT_FAILED
            error = ("无结果 JSON（stdout 空或非 JSON 尾行）"
                     if not stdout_val.strip() else
                     "无法从 stdout 提取结果 JSON")

    rss_kb = None
    try:
        import psutil
        rss_kb = psutil.Process(os.getpid()).memory_info().rss // 1024
    except Exception:
        pass

    return _worker_result(verdict, stdout_val, stderr_val, error, result, t0,
                          rss_kb)


def _worker_result(verdict, stdout_val, stderr_val, error, result, t0, rss_kb):
    return {
        "ok": True,
        "verdict": verdict,
        "stdout": stdout_val,
        "stderr": stderr_val,
        "error": error,
        "result": result,
        "duration_ms": round((_time.time() - t0) * 1000, 2),
        "rss_kb": rss_kb,
    }
'''

# ════════════════════════════════════════════════════════════
#  subprocess 后端驱动脚本（独立进程运行，零管道依赖）
# ════════════════════════════════════════════════════════════

# bootstrap 版本标记：升级 worker 体后递增，引擎据此重新生成缓存文件
_BOOTSTRAP_VERSION = 1
_BOOTSTRAP_VERSION_MARKER = f"__REPLAY_BOOTSTRAP_VERSION__ = {_BOOTSTRAP_VERSION}"

_BOOTSTRAP_DRIVER = r'''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""replay 隔离 worker 驱动（由 agent.learning.replay 生成，勿手工编辑）

全新解释器进程：python -u <bootstrap.py> --params <in.json> --result <out.json>
零管道依赖：输入/输出均为文件（适配无命名管道/匿名管道的受限环境）。
"""
import argparse
import json
import sys
import time as _time


def _write_result(path, payload):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
    except Exception:
        pass


def _main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--params", required=True)
    ap.add_argument("--result", required=True)
    args = ap.parse_args(argv)
    try:
        with open(args.params, "r", encoding="utf-8") as f:
            payload = json.load(f)["payload"]
    except Exception as e:  # noqa: BLE001
        _write_result(args.result, {
            "ok": False, "verdict": "escape", "stdout": "", "stderr": "",
            "error": "params 读取失败: %r" % (e,),
            "result": None, "duration_ms": 0.0, "rss_kb": None,
        })
        return 2
    t0 = _time.time()
    ns = {}
    exec(compile(_WORKER_SOURCE, "<replay-worker>", "exec"), ns)
    try:
        out = ns["run_sample_in_worker"](payload)
    except Exception as e:  # noqa: BLE001 worker 基础设施异常 → 隔离失败（fail-closed）
        out = {
            "ok": False, "verdict": "escape", "stdout": "", "stderr": "",
            "error": "worker 基础设施异常: %r" % (e,),
            "result": None, "duration_ms": 0.0, "rss_kb": None,
        }
    out.setdefault("duration_ms", round((_time.time() - t0) * 1000, 2))
    _write_result(args.result, out)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
'''


def _bootstrap_text() -> str:
    """生成 bootstrap 脚本内容（内嵌 worker 体 + 驱动，单点维护）。

    含版本标记：bootstrap 升级后旧缓存文件会被引擎重新生成，避免版本漂移。
    """
    return (
        f"__REPLAY_BOOTSTRAP_VERSION__ = {_BOOTSTRAP_VERSION}\n\n"
        + "_WORKER_SOURCE = " + repr(_WORKER_SOURCE) + "\n\n"
        + _BOOTSTRAP_DRIVER + "\n"
    )


# ════════════════════════════════════════════════════════════
#  执行后端
# ════════════════════════════════════════════════════════════

# 环境变量白名单（不传密钥/代理/用户目录；KEY/TOKEN/SECRET/PASSWORD/PROXY 全剔除）
_ENV_WHITELIST = (
    "PATH", "PYTHONUTF8", "PYTHONIOENCODING", "SYSTEMROOT", "SYSTEMDRIVE",
    "TEMP", "TMP", "COMSPEC", "WINDIR", "PROCESSOR_ARCHITECTURE",
)
_SENSITIVE_ENV_RE = re.compile(
    r"(?i)(api[_-]?key|secret|token|password|passwd|proxy|credential|private[_-]?key)"
)


def _safe_env(payload: Dict[str, Any]) -> Dict[str, str]:
    """构建隔离 worker 环境：白名单 + 显式剔除敏感变量。"""
    env: Dict[str, str] = {}
    for key in _ENV_WHITELIST:
        val = os.environ.get(key)
        if val is not None:
            env[key] = val
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    # 防御性剔除：即使白名单漏配，任何敏感/代理变量也不得进入
    for key in list(os.environ.keys()):
        if _SENSITIVE_ENV_RE.search(key):
            env.pop(key, None)
    material = payload.get("material_dir")
    if material:
        env["REPLAY_MATERIAL_DIR"] = str(material)
    return env


class SampleExecution:
    """后端单样本执行结果（内部）"""

    def __init__(self, *, verdict: str, duration_ms: float,
                 evidence: str = "", error: Optional[str] = None,
                 result: Any = None,
                 resource_usage: Optional[Dict[str, Any]] = None):
        self.verdict = verdict
        self.duration_ms = duration_ms
        self.evidence = evidence
        self.error = error
        self.result = result
        self.resource_usage = resource_usage or {}


class _ProcessBackend:
    """进程级隔离后端：全新解释器进程 + 受限 exec + 超时强杀

    runner:
      - spawn（默认）：multiprocessing spawn 上下文（与 run_sandbox 同模式）
      - subprocess：文件交换的独立 python 进程（零管道依赖，受限环境可用）
    """

    name = "process"

    def __init__(self, budget: ReplayBudget, *, runner: Optional[str] = None,
                 python_exe: Optional[str] = None):
        self.budget = budget
        self.runner = (runner or _env_str("EVAL_REPLAY_RUNNER", DEFAULT_RUNNER))
        if self.runner not in ("spawn", "subprocess"):
            raise ReplayIsolationError(
                f"EVAL_REPLAY_RUNNER 非法: {self.runner!r}（可选 spawn|subprocess）")
        self.python_exe = python_exe or sys.executable
        if self.runner == "spawn":
            self._init_spawn()

    def _init_spawn(self) -> None:
        """spawn runner 可用性探测：失败即显式报错（fail-closed，绝不静默降级）。"""
        try:
            import multiprocessing
            self._ctx = multiprocessing.get_context("spawn")
            # 探测 Queue（命名管道）可用性；受限环境（无命名管道）→ 显式失败
            probe = self._ctx.Queue()
            probe.close()
            probe.join_thread()
        except Exception as e:  # noqa: BLE001
            raise ReplayIsolationError(
                "spawn runner 不可用（multiprocessing Queue 创建失败），"
                f"回放 fail-closed 拒绝执行；如需改用文件交换子进程执行器，"
                f"请显式设置 EVAL_REPLAY_RUNNER=subprocess。原始错误: {e}") from e

    # ── spawn 执行器 ──

    def run_sample_spawn(self, payload: Dict[str, Any],
                         scratch_dir: Path) -> SampleExecution:
        import multiprocessing  # 延迟导入
        ctx = multiprocessing.get_context("spawn")
        result_queue = ctx.Queue()
        process = ctx.Process(target=_spawn_worker, args=(payload, result_queue),
                              daemon=True)
        t0 = time.monotonic()
        process.start()
        process.join(timeout=self.budget.timeout_s)
        if process.is_alive():
            process.terminate()
            process.join(timeout=2)
            if process.is_alive():
                process.kill()
                process.join(timeout=1)
            return SampleExecution(
                verdict=VERDICT_TIMEOUT,
                duration_ms=(time.monotonic() - t0) * 1000,
                error=f"墙钟超时（{self.budget.timeout_s}s），进程已强杀",
            )
        try:
            data = result_queue.get(timeout=2)
        except Exception:  # noqa: BLE001 子进程崩溃/无结果 → 隔离失败（fail-closed）
            return SampleExecution(
                verdict=VERDICT_ESCAPE,
                duration_ms=(time.monotonic() - t0) * 1000,
                error="隔离 worker 未返回结果（进程异常终止，按逃逸处理）",
            )
        return self._to_sample_execution(data, t0)

    # ── subprocess 执行器（零管道依赖）──

    def run_sample_subprocess(self, payload: Dict[str, Any],
                              scratch_dir: Path,
                              bootstrap_file: Path) -> SampleExecution:
        in_dir = scratch_dir / "in"
        out_dir = scratch_dir / "out"
        in_dir.mkdir(parents=True, exist_ok=True)
        out_dir.mkdir(parents=True, exist_ok=True)
        params_file = in_dir / "params.json"
        result_file = out_dir / "result.json"
        stderr_file = out_dir / "bootstrap_stderr.txt"
        params_file.write_text(
            json.dumps({"payload": payload}, ensure_ascii=False),
            encoding="utf-8")

        cmd = [self.python_exe, "-u", str(bootstrap_file),
               "--params", str(params_file), "--result", str(result_file)]
        env = _safe_env(payload)
        # worker cwd = 引擎工作根（持久目录，避免子进程 CWD 句柄锁住待删 scratch）；
        # 样本数据在 scratch/in|out（绝对路径注入，用完即删）。不可信代码无文件访问
        # 能力（无 open/os），cwd 不构成额外攻击面。
        work_root = scratch_dir.parents[1]
        t0 = time.monotonic()
        proc: Optional[subprocess.Popen] = None
        try:
            with open(stderr_file, "w", encoding="utf-8") as err_f:
                proc = subprocess.Popen(
                    cmd, cwd=str(work_root), env=env,
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=err_f,
                )
                try:
                    proc.wait(timeout=self.budget.timeout_s)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
                    return SampleExecution(
                        verdict=VERDICT_TIMEOUT,
                        duration_ms=(time.monotonic() - t0) * 1000,
                        error=f"墙钟超时（{self.budget.timeout_s}s），进程已强杀",
                    )
        except OSError as e:
            raise ReplayIsolationError(
                f"隔离 worker 启动失败（fail-closed，不降级同进程执行）: {e}") from e

        duration_ms = (time.monotonic() - t0) * 1000
        if not result_file.exists():
            stderr_tail = ""
            try:
                stderr_tail = stderr_file.read_text(encoding="utf-8")[-2000:]
            except OSError:
                pass
            return SampleExecution(
                verdict=VERDICT_ESCAPE,
                duration_ms=duration_ms,
                evidence=stderr_tail,
                error=(f"隔离 worker 未产出结果文件（exit={proc.returncode}），"
                       "按隔离失败处理（fail-closed）"),
            )
        try:
            data = json.loads(result_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            return SampleExecution(
                verdict=VERDICT_ESCAPE,
                duration_ms=duration_ms,
                error=f"隔离 worker 结果文件损坏: {e}",
            )
        return self._to_sample_execution(data, t0)

    @staticmethod
    def _to_sample_execution(data: Dict[str, Any], t0: float) -> SampleExecution:
        verdict = data.get("verdict", VERDICT_ESCAPE)
        stdout_val = data.get("stdout", "")
        stderr_val = data.get("stderr", "")
        error = data.get("error")
        resource_usage: Dict[str, Any] = {
            "wall_ms": round((time.monotonic() - t0) * 1000, 2),
        }
        if data.get("rss_kb") is not None:
            resource_usage["rss_kb"] = data["rss_kb"]
        # 证据截断（stdout + stderr 尾部）
        evidence = (stdout_val[-3000:] + ("\n[stderr] " + stderr_val[-1000:] if stderr_val else ""))
        return SampleExecution(
            verdict=verdict,
            duration_ms=resource_usage["wall_ms"],
            evidence=evidence,
            error=error,
            result=data.get("result"),
            resource_usage=resource_usage,
        )

    def run_sample(self, payload: Dict[str, Any],
                   scratch_dir: Path,
                   bootstrap_file: Optional[Path] = None) -> SampleExecution:
        if self.runner == "subprocess":
            assert bootstrap_file is not None
            return self.run_sample_subprocess(payload, scratch_dir, bootstrap_file)
        return self.run_sample_spawn(payload, scratch_dir)


class _DockerBackend:
    """容器化后端（可选）：docker run --rm --read-only --network none

    默认不引入运行依赖；仅当 EVAL_REPLAY_BACKEND=docker 显式启用。
    不可用（无 docker/镜像）→ 显式失败（fail-closed）。
    降级路径：移除配置即回到 process 后端；文档见任务6 变更说明。
    """

    name = "docker"

    def __init__(self, budget: ReplayBudget, *, image: Optional[str] = None):
        self.budget = budget
        self.image = image or _docker_image()
        if shutil.which("docker") is None:
            raise ReplayIsolationError(
                "docker 后端不可用：未找到 docker 可执行文件"
                "（fail-closed；请改用 EVAL_REPLAY_BACKEND=process 或安装 docker）")

    @staticmethod
    def _container_name(replay_id: str) -> str:
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", replay_id)
        return f"yunshu_replay_{safe}"

    def run_sample(self, payload: Dict[str, Any], scratch_dir: Path,
                   bootstrap_file: Optional[Path] = None) -> SampleExecution:
        in_dir = scratch_dir / "in"
        out_dir = scratch_dir / "out"
        in_dir.mkdir(parents=True, exist_ok=True)
        out_dir.mkdir(parents=True, exist_ok=True)
        params_file = in_dir / "params.json"
        result_file = out_dir / "result.json"
        params_file.write_text(
            json.dumps({"payload": payload}, ensure_ascii=False),
            encoding="utf-8")
        # bootstrap 一并挂载（只读）
        boot_mount = str(bootstrap_file) if bootstrap_file else ""
        name = self._container_name(payload.get("job_id", "job"))
        cmd = [
            "docker", "run", "--rm", "--name", name,
            "--read-only", "--network", "none",
            "--tmpfs", "/tmp:rw,size=64m",
            "-v", f"{in_dir}:/in:ro",
            "-v", f"{out_dir}:/out",
            "--memory", f"{max(64, int(self.budget.max_rss_mb or 256))}m",
            "--cpus", "1",
        ]
        if boot_mount:
            cmd += ["-v", f"{boot_mount}:/in/bootstrap.py:ro"]
        cmd += [self.image, "python", "-u", "/in/bootstrap.py",
                "--params", "/in/params.json", "--result", "/out/result.json"]
        t0 = time.monotonic()
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
            try:
                proc.wait(timeout=self.budget.timeout_s)
            except subprocess.TimeoutExpired:
                # 强杀容器（--rm 配合 docker rm -f 兜底）
                try:
                    subprocess.Popen(
                        ["docker", "rm", "-f", name],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    ).wait(timeout=10)
                except Exception:  # noqa: BLE001
                    pass
                proc.kill()
                return SampleExecution(
                    verdict=VERDICT_TIMEOUT,
                    duration_ms=(time.monotonic() - t0) * 1000,
                    error=f"容器墙钟超时（{self.budget.timeout_s}s）",
                )
        except OSError as e:
            raise ReplayIsolationError(f"docker 后端启动失败: {e}") from e
        duration_ms = (time.monotonic() - t0) * 1000
        if not result_file.exists():
            return SampleExecution(
                verdict=VERDICT_ESCAPE,
                duration_ms=duration_ms,
                error=f"容器未产出结果（exit={proc.returncode}），按隔离失败处理",
            )
        try:
            data = json.loads(result_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            return SampleExecution(
                verdict=VERDICT_ESCAPE, duration_ms=duration_ms,
                error=f"容器结果文件损坏: {e}")
        return _ProcessBackend._to_sample_execution(data, t0)


def _spawn_worker(payload: Dict[str, Any], result_queue: Any) -> None:
    """模块级 spawn 入口（可 pickle；mock_sandbox_spawn 下线程化执行）。"""
    t0 = time.monotonic()
    ns: Dict[str, Any] = {}
    try:
        exec(compile(_WORKER_SOURCE, "<replay-worker>", "exec"), ns)
        out = ns["run_sample_in_worker"](payload)
    except Exception as e:  # noqa: BLE001 worker 基础设施异常 → 隔离失败
        out = {"ok": False, "verdict": VERDICT_ESCAPE, "stdout": "", "stderr": "",
               "error": f"worker 异常: {e}", "result": None,
               "duration_ms": (time.monotonic() - t0) * 1000, "rss_kb": None}
    try:
        result_queue.put(out)
    except Exception:  # noqa: BLE001
        pass


def _resolve_backend(budget: ReplayBudget, *,
                     backend: Optional[str] = None,
                     runner: Optional[str] = None,
                     image: Optional[str] = None) -> Any:
    """按配置构造后端；不可用 → ReplayIsolationError（fail-closed）。"""
    name = (backend or _env_str("EVAL_REPLAY_BACKEND", DEFAULT_BACKEND)).lower()
    if name == "process":
        return _ProcessBackend(budget, runner=runner)
    if name == "docker":
        return _DockerBackend(budget, image=image)
    raise ReplayIsolationError(f"EVAL_REPLAY_BACKEND 非法: {name!r}（可选 process|docker）")


# ════════════════════════════════════════════════════════════
#  回放引擎
# ════════════════════════════════════════════════════════════


class ReplayEngine:
    """回放引擎：样本集 + 候选产物 → 隔离执行 → ReplayResult + 审计

    fail-closed：
      - enabled=False（默认）→ ReplayDisabledError（显式拒绝，不静默跳过）
      - 后端不可用/worker 异常 → ReplayIsolationError（绝不降级同进程执行）
      - 输出/进程上限 → 逃逸或超时判定
    """

    def __init__(self, *, enabled: Optional[bool] = None,
                 work_dir: Optional[Path] = None,
                 audit_file: Optional[Path] = None,
                 backend_name: Optional[str] = None,
                 runner: Optional[str] = None,
                 docker_image: Optional[str] = None,
                 budget: Optional[ReplayBudget] = None):
        self.enabled = (replay_enabled() if enabled is None else bool(enabled))
        self.work_dir = (Path(work_dir) if work_dir else _work_dir()).resolve()
        self.audit_file = (Path(audit_file) if audit_file else _audit_file()).resolve()
        self.backend_name = backend_name
        self.runner = runner
        self.docker_image = docker_image
        self.budget = budget or ReplayBudget(
            timeout_s=_timeout_s(),
            max_output_bytes=_max_output_bytes(),
            max_stderr_bytes=_max_stderr_bytes(),
            max_concurrent=_max_concurrent(),
            max_samples=_max_samples(),
            max_rss_mb=_max_rss_mb(),
        )
        self.budget.validate()
        self._semaphore = threading.BoundedSemaphore(self.budget.max_concurrent)
        self._bootstrap_file: Optional[Path] = None
        self._audit_lock = threading.Lock()

    # ── 内部 ──

    def _ensure_bootstrap(self) -> Path:
        if self._bootstrap_file is None:
            self.work_dir.mkdir(parents=True, exist_ok=True)
            path = self.work_dir / "replay_bootstrap.py"
            if not path.exists():
                path.write_text(_bootstrap_text(), encoding="utf-8")
            else:
                # 版本漂移防护：worker 体升级后旧缓存文件必须重新生成
                try:
                    head = path.read_text(encoding="utf-8", errors="ignore")[:256]
                except OSError:
                    head = ""
                if _BOOTSTRAP_VERSION_MARKER not in head:
                    path.write_text(_bootstrap_text(), encoding="utf-8")
            self._bootstrap_file = path
        return self._bootstrap_file

    def _scratch_dir(self, sample_id: str) -> Path:
        """每样本独立 scratch（进程 cwd）；与生产零共享写路径。"""
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", sample_id)[:60]
        return self.work_dir / "scratch" / f"{safe}_{uuid.uuid4().hex[:8]}"

    def _run_one(self, sample: ReplaySample,
                 candidate: ReplayCandidate,
                 backend: Any,
                 job: ReplayJob) -> ReplayResult:
        scratch: Optional[Path] = None
        try:
            payload = {
                "sample": {
                    "sample_id": sample.sample_id,
                    "params": {"task": sample.task,
                               "sample_id": sample.sample_id,
                               "category": sample.category,
                               "expected_output": sample.expected_output,
                               "metadata": sample.metadata or {}},
                },
                "candidate": {"code": candidate.code,
                              "candidate_id": candidate.candidate_id},
                "budget": {
                    "timeout_s": self.budget.timeout_s,
                    "max_output_bytes": self.budget.max_output_bytes,
                    "max_stderr_bytes": self.budget.max_stderr_bytes,
                    "max_rss_mb": self.budget.max_rss_mb,
                },
                "material_dir": str(job.material_dir) if job.material_dir else None,
                "job_id": job.job_id,
            }
            # 静态预扫描：命中 → escape（不 spawn）
            hit = scan_for_escape(candidate.code)
            if hit:
                return ReplayResult(
                    sample_id=sample.sample_id,
                    verdict=VERDICT_ESCAPE,
                    evidence=hit,
                    error="静态逃逸预扫描拦截（未执行）",
                )
            scratch = self._scratch_dir(sample.sample_id)
            scratch.mkdir(parents=True, exist_ok=True)
            with self._semaphore:
                ex = backend.run_sample(payload, scratch,
                                        self._ensure_bootstrap())
            return ReplayResult(
                sample_id=sample.sample_id,
                verdict=ex.verdict,
                duration_ms=ex.duration_ms,
                resource_usage=ex.resource_usage,
                evidence=ex.evidence,
                result=ex.result,
                error=ex.error,
            )
        except ReplayIsolationError:
            raise
        except Exception as e:  # noqa: BLE001 后端异常 → 隔离失败（fail-closed）
            raise ReplayIsolationError(f"隔离执行失败（sample={sample.sample_id}）: {e}") from e
        finally:
            # 零残留：删除 per-sample scratch（Windows 目录句柄延迟释放约 1s，重试兜底）
            if scratch is not None:
                for _ in range(20):
                    if not scratch.exists():
                        break
                    try:
                        shutil.rmtree(scratch)
                        break
                    except OSError:
                        time.sleep(0.1)
                if scratch.exists():
                    _log().warning("[Replay] scratch 清理未完成: %s", scratch)
                # 顺带清理空的 scratch 容器目录（仅当为空时删除；并发场景忽略）
                try:
                    scratch.parent.rmdir()
                except OSError:
                    pass

    def _append_audit(self, report: ReplayReport) -> None:
        try:
            self.audit_file.parent.mkdir(parents=True, exist_ok=True)
            entry = {
                "replay_id": report.replay_id,
                "created_at": report.created_at,
                "candidate_id": report.candidate_id,
                "samples": [
                    {"sample_id": r.sample_id, "verdict": r.verdict,
                     "duration_ms": round(r.duration_ms, 2)}
                    for r in report.results
                ],
                "verdict_counts": report.verdict_counts,
                "duration_ms": round(report.duration_ms, 2),
                "resource_usage": report.resource_usage,
                "evidence": json.dumps(report.to_dict().get("results", []),
                                       ensure_ascii=False)[:2000],
                "rollback_command": report.rollback_command,
                "backend": report.backend,
                "runner": report.runner,
                "enabled": report.enabled,
            }
            with self._audit_lock:
                with open(self.audit_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as e:
            _log().warning("[Replay] 审计写入失败 %s: %s", self.audit_file, e)

    # ── 主入口 ──

    def run(self, job: ReplayJob) -> ReplayReport:
        """执行回放。默认关闭；开启需显式配置（EVAL_REPLAY_ENABLED）。"""
        if not self.enabled:
            raise ReplayDisabledError(
                "回放默认关闭（EVAL_REPLAY_ENABLED=false）。"
                "仅任务1 评估集回归 / 任务3 observe 态回放用途可显式开启。")
        if not job.samples:
            raise ReplayJobError("job 无样本（samples 为空）")
        if not job.candidate.code.strip():
            raise ReplayJobError("candidate.code 为空")
        if len(job.samples) > self.budget.max_samples:
            raise ReplayJobError(
                f"样本数 {len(job.samples)} 超上限 {self.budget.max_samples}")

        t0 = time.monotonic()
        report = ReplayReport(
            replay_id=_replay_id(),
            job_id=job.job_id,
            candidate_id=job.candidate.candidate_id,
            sampleset_version=job.sampleset_version,
            category=job.category,
            rollback_command=job.candidate.rollback_command
            or self._default_rollback_command(job.candidate),
            backend=self.backend_name or _env_str("EVAL_REPLAY_BACKEND",
                                                  DEFAULT_BACKEND),
            runner=(self.runner or _env_str("EVAL_REPLAY_RUNNER", DEFAULT_RUNNER)),
            enabled=True,
            notes=list(job.notes),
        )
        backend = _resolve_backend(self.budget, backend=self.backend_name,
                                   runner=self.runner,
                                   image=self.docker_image)
        report.backend = backend.name
        if hasattr(backend, "runner"):
            report.runner = backend.runner

        # 样本脱敏（防御性：即使 job 未脱敏，引擎侧再脱一次再进隔离环境）
        for sample in job.samples:
            sanitized = sanitize_sample(sample)
            r = self._run_one(sanitized, job.candidate, backend, job)
            report.results.append(r)

        report.sample_count = len(report.results)
        report.duration_ms = (time.monotonic() - t0) * 1000
        report.resource_usage = {
            "total_wall_ms": round(report.duration_ms, 2),
            "per_sample_wall_ms": [
                round(r.duration_ms, 2) for r in report.results
            ],
            "rss_kb": [
                r.resource_usage.get("rss_kb") for r in report.results
                if r.resource_usage.get("rss_kb") is not None
            ],
        }
        self._append_audit(report)
        _log().info(
            "[Replay] done replay_id=%s candidate=%s samples=%d "
            "verdicts=%s success_rate=%.3f wall_ms=%.1f",
            report.replay_id, report.candidate_id, report.sample_count,
            report.verdict_counts, report.success_rate, report.duration_ms,
        )
        return report

    @staticmethod
    def _default_rollback_command(candidate: ReplayCandidate) -> str:
        if candidate.before_version:
            return ("python -m agent.learning.rollout_controller "
                    f"--action evolution --rollback --candidate {candidate.candidate_id} "
                    f"# 回滚到 before_version={candidate.before_version}")
        return (f"# 候选 {candidate.candidate_id} 未提交（回放仅验证，无回滚语义）；"
                "如已提交请用 rollout_controller --rollback")


# ════════════════════════════════════════════════════════════
#  便捷入口 / 候选产物构建 / 评估集回归入口
# ════════════════════════════════════════════════════════════


def replay_samples(job: ReplayJob, *, engine: Optional[ReplayEngine] = None,
                   enabled: Optional[bool] = None,
                   backend: Optional[str] = None,
                   runner: Optional[str] = None) -> ReplayReport:
    """便捷入口：回放 job（默认关闭，需显式开启）。"""
    eng = engine or ReplayEngine(enabled=enabled, backend_name=backend,
                                 runner=runner)
    return eng.run(job)


def candidate_from_code(code: str, candidate_id: str, *,
                        name: str = "",
                        before_version: Optional[str] = None,
                        rollback_command: Optional[str] = None) -> ReplayCandidate:
    """从原始代码构建候选产物（危险样本测试/工具调用序列回放用）。"""
    return ReplayCandidate(
        candidate_id=candidate_id,
        code=code,
        name=name or candidate_id,
        before_version=before_version,
        rollback_command=rollback_command,
    )


def candidate_from_skill(skill_id: str, *, file_store: Any = None) -> ReplayCandidate:
    """从技能仓库构建候选产物：scripts/main.py 内容（只读，不修改 SkillExecutor）。"""
    from agent.skills_mgmt.file_store import SkillFileStore  # 延迟导入
    fs = file_store or SkillFileStore()
    path = fs.get_script_path(skill_id, "main.py")
    code = Path(path).read_text(encoding="utf-8")
    meta = fs.get_metadata(skill_id) or {}
    return ReplayCandidate(
        candidate_id=f"{skill_id}@replay",
        code=code,
        name=skill_id,
        before_version=meta.get("version"),
        rollback_command=(
            "python -m agent.learning.rollout_controller "
            f"--action evolution --rollback --candidate {skill_id}@replay"
            if meta.get("version") else None),
    )


def run_replay_regression(candidate: ReplayCandidate, *,
                          sampleset_version: str = "v1",
                          category: Optional[str] = None,
                          sample_ids: Optional[List[str]] = None,
                          samples_dir: Optional[str] = None,
                          budget: Optional[ReplayBudget] = None,
                          engine: Optional[ReplayEngine] = None,
                          enabled: Optional[bool] = None,
                          backend: Optional[str] = None,
                          runner: Optional[str] = None,
                          material_dir: Optional[Path] = None) -> ReplayReport:
    """回放入口（复用任务1 评估集回归通道，只读）：
    按 manifest (version, category) 解析样本 id → EvalSamplePool 加载样本 →
    脱敏 → 隔离回放 → 比对结论（success_rate + 判定明细）。
    只读：不写基线、不修改 manifest/样本文件。
    """
    from agent.skills_mgmt.eval_regression import SamplesetRegistry  # 延迟导入
    from agent.skills_mgmt.evaluator import EvalSamplePool  # 延迟导入

    samples_dir_path = Path(samples_dir) if samples_dir else (
        Path(__file__).resolve().parent.parent.parent / "data" / "evals")
    registry = SamplesetRegistry(samples_dir_path / "manifest.json")
    cat = category or candidate.name.split("/")[0] or "general"
    ids = sample_ids
    if ids is None:
        ids = registry.sample_ids(sampleset_version, cat)
    if not ids:
        raise ReplayJobError(
            f"样本集版本 {sampleset_version!r} 类别 {cat!r} 未登记（manifest）")

    pool = EvalSamplePool(base_dir=str(samples_dir_path))
    evals = pool.get(cat, ids)
    if not evals:
        raise ReplayJobError(f"类别 {cat!r} 无可用样本（绝不伪造指标）")

    samples = [
        ReplaySample(
            sample_id=str(s.id),
            task=str(s.task),
            category=str(s.category),
            expected_output=s.expected_output,
            metadata=s.metadata,
        )
        for s in evals
    ]
    job = ReplayJob(
        samples=samples,
        candidate=candidate,
        budget=budget,
        sampleset_version=sampleset_version,
        category=cat,
        material_dir=material_dir or samples_dir_path,
        notes=[f"回放入口=任务1 评估集回归通道（manifest v{sampleset_version}）"],
    )
    eng = engine or ReplayEngine(enabled=enabled, backend_name=backend,
                                 runner=runner, budget=budget)
    return eng.run(job)


# ════════════════════════════════════════════════════════════
#  性能基准（单样本墙钟/内存；对照 EVO-T2 报告 §3 口径）
# ════════════════════════════════════════════════════════════

_BENCHMARK_CODE = (
    "import sys, json\n"
    "params = json.loads(sys.stdin.read())\n"
    "out = {\"summary\": \"ok\", \"sample_id\": params.get(\"sample_id\"), "
    "\"echo\": len(params.get(\"task\", \"\"))}\n"
    "print(json.dumps(out, ensure_ascii=False))\n"
)


def run_benchmark(sample_count: int = 5, *, runner: Optional[str] = None,
                  backend: Optional[str] = None,
                  work_dir: Optional[Path] = None) -> Dict[str, Any]:
    """单样本回放性能基准：墙钟/内存（对照 EVO-T2 报告 §3 子进程冷启动口径）。"""
    eng = ReplayEngine(enabled=True, runner=runner or "subprocess",
                       backend_name=backend or "process",
                       work_dir=work_dir)
    samples = [
        ReplaySample(sample_id=f"bench-{i:03d}", task=f"基准样本 {i}", category="bench")
        for i in range(sample_count)
    ]
    job = ReplayJob(
        samples=samples,
        candidate=candidate_from_code(_BENCHMARK_CODE, "bench@replay",
                                      name="benchmark"),
        notes=["性能基准：EVAL_REPLAY_RUNNER=" + (runner or "subprocess")],
    )
    report = eng.run(job)
    walls = [r.duration_ms for r in report.results]
    rss = [r.resource_usage.get("rss_kb") for r in report.results
           if r.resource_usage.get("rss_kb") is not None]
    return {
        "runner": report.runner,
        "backend": report.backend,
        "sample_count": sample_count,
        "wall_ms": {
            "min": round(min(walls), 2) if walls else None,
            "max": round(max(walls), 2) if walls else None,
            "avg": round(sum(walls) / len(walls), 2) if walls else None,
            "per_sample": [round(w, 2) for w in walls],
        },
        "rss_kb": {
            "min": min(rss) if rss else None,
            "max": max(rss) if rss else None,
            "avg": round(sum(rss) / len(rss), 1) if rss else None,
        },
        "success_rate": report.success_rate,
        "created_at": report.created_at,
    }


# ════════════════════════════════════════════════════════════
#  CLI
# ════════════════════════════════════════════════════════════

_EXIT_OK = 0
_EXIT_DISABLED = 1
_EXIT_ISOLATION = 2
_EXIT_JOB_ERROR = 3


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        description="评估回放隔离管道 CLI（任务6；默认关闭）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--candidate", default=None,
                        help="候选产物代码文件路径（.py）")
    parser.add_argument("--skill", default=None,
                        help="从技能仓库读 scripts/main.py 作为候选")
    parser.add_argument("--samples", default=None,
                        help="样本 JSON 文件（[{sample_id, task, category}]）")
    parser.add_argument("--set", default="v1", help="样本集版本（回归入口）")
    parser.add_argument("--category", default=None, help="样本类别（回归入口）")
    parser.add_argument("--out", default=None, help="报告 JSON 输出路径")
    parser.add_argument("--enabled", action="store_true",
                        help="显式开启回放（默认关闭）")
    parser.add_argument("--timeout", type=float, default=None,
                        help="单样本墙钟超时（秒）")
    parser.add_argument("--runner", default=None,
                        help="进程后端执行器（spawn|subprocess）")
    parser.add_argument("--backend", default=None,
                        help="后端（process|docker）")
    parser.add_argument("--benchmark", action="store_true",
                        help="运行性能基准（单样本墙钟/内存）")
    parser.add_argument("--benchmark-samples", type=int, default=5)
    args = parser.parse_args(argv)

    def _emit(data: Dict[str, Any], code: int) -> int:
        if args.out:
            out_path = Path(args.out)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                                encoding="utf-8")
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return code

    try:
        if args.benchmark:
            data = run_benchmark(args.benchmark_samples, runner=args.runner,
                                 backend=args.backend)
            return _emit(data, _EXIT_OK)

        if args.skill:
            candidate = candidate_from_skill(args.skill)
        elif args.candidate:
            code = Path(args.candidate).read_text(encoding="utf-8")
            candidate = candidate_from_code(
                code, f"cli@{Path(args.candidate).stem}")
        else:
            print(json.dumps({"error": "需提供 --candidate 或 --skill 或 --benchmark"},
                             ensure_ascii=False))
            return _EXIT_JOB_ERROR

        if args.samples:
            raw = json.loads(Path(args.samples).read_text(encoding="utf-8"))
            samples = [ReplaySample(sample_id=str(s.get("sample_id", f"i{i}")),
                                    task=str(s.get("task", "")),
                                    category=str(s.get("category", "general")))
                       for i, s in enumerate(raw)]
            job = ReplayJob(samples=samples, candidate=candidate,
                            sampleset_version=args.set, category=args.category or "")
        else:
            report = run_replay_regression(
                candidate, sampleset_version=args.set, category=args.category,
                enabled=args.enabled or None, backend=args.backend,
                runner=args.runner)
            return _emit(report.to_dict(), _EXIT_OK)

        eng = ReplayEngine(enabled=args.enabled or None, backend_name=args.backend,
                           runner=args.runner)
        if args.timeout is not None:
            eng.budget.timeout_s = args.timeout
        report = eng.run(job)
        return _emit(report.to_dict(), _EXIT_OK)
    except ReplayDisabledError as e:
        return _emit({"error": str(e)}, _EXIT_DISABLED)
    except ReplayIsolationError as e:
        return _emit({"error": str(e)}, _EXIT_ISOLATION)
    except ReplayJobError as e:
        return _emit({"error": str(e)}, _EXIT_JOB_ERROR)
    except Exception as e:  # noqa: BLE001
        return _emit({"error": f"回放失败: {e}"}, _EXIT_JOB_ERROR)


# ════════════════════════════════════════════════════════════
#  沙箱回放覆盖率（TC-4 触发条件输入 · 任务6 遗留项接线）
# ════════════════════════════════════════════════════════════


def compute_replay_coverage(
    audit_file: Optional[Path] = None,
    manifest_path: Optional[Path] = None,
) -> Optional[float]:
    """计算"沙箱回放覆盖率"（报告 §5.2 TC-4 触发条件输入）。

    口径：审计中出现过的**评估集样本**（sample_id ∈ manifest 登记样本）去重数
          / manifest 当前版本登记样本总数。
    审计文件不存在/为空、manifest 缺失或解析失败 → 返回 None
    （TC-4 保持 unknown，绝不伪造指标——沿用评估体系不变式）。

    用途：`/api/learning/metrics/trigger` 与运维脚本自动注入 replay_coverage，
    消除"回放统计未接入 KPI"断点（任务6 遗留项）；阈值判定在查询层
    `LearningMetrics.evaluate_trigger_conditions(replay_coverage=...)`。
    """
    from pathlib import Path as _Path

    audit = _Path(audit_file) if audit_file is not None else _audit_file()
    manifest = _Path(manifest_path) if manifest_path is not None else (
        _Path(__file__).resolve().parent.parent.parent
        / "data" / "evals" / "manifest.json")
    if not audit.exists() or not manifest.exists():
        return None
    try:
        with open(manifest, "r", encoding="utf-8") as f:
            mdata = json.load(f)
        current = (mdata or {}).get("current")
        cats = (((mdata or {}).get("versions") or {}).get(current) or {}).get(
            "categories") or {}
        registered = set()
        for ids in cats.values():
            for sid in (ids or []):
                registered.add(str(sid))
        if not registered:
            return None
        replayed = set()
        with open(audit, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                for s in (entry.get("samples") or []):
                    sid = s.get("sample_id")
                    if sid is not None and str(sid) in registered:
                        replayed.add(str(sid))
        if not replayed:
            return 0.0
        return round(len(replayed) / len(registered), 4)
    except (OSError, ValueError, TypeError):
        return None


if __name__ == "__main__":
    raise SystemExit(main())
