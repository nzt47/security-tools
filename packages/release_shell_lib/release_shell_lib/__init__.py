"""release_shell_lib — 发布流程 Shell 函数库的 Python 等价实现。

来源：scripts/release_shell_lib.sh（release-auto.yml / release-precheck.yml 实战验证，
2026-08-07 提炼，行为契约完全对齐，详见 docs/release_workflow_manual.md §6.1 / §10 Q3）。

背景问题：
1. curl 网络层失败（超时/拒连）时 shell 版用 `|| CODE=500` 映射为 HTTP 500 进重试，
   避免 set -e 终止 step、跳过重试循环 —— 本包 curl_http_code 等价处理。
2. gh api 失败时错误 JSON 混入 stdout（`2>/dev/null` 挡不住），数值结果变成
   "JSON垃圾+0"，`-gt` 比较会报 "integer expression expected" —— 本包
   safe_num_or_zero / gh_api_len 正则兜底等价实现。

用法（任何 Python 项目直接 import，不依赖外部第三方库）:
    from release_shell_lib import curl_http_code, safe_num_or_zero, gh_api_len, read_resp_file
"""

from __future__ import annotations

import os
import re
import subprocess
import urllib.error
import urllib.request
from typing import Any, Optional

__all__ = [
    "curl_http_code",
    "safe_num_or_zero",
    "gh_api_len",
    "read_resp_file",
    "selfcheck",
]

# 与 shell 版 CURL_MAX_TIME 默认值对齐（防 API 挂起拖满 CI step 超时）
DEFAULT_CURL_MAX_TIME = 30
# 响应体文件默认名（与 shell 版一致，CI 中可被后续 read_resp_file 读取）
DEFAULT_RESP_FILE = "gh_resp.json"


def curl_http_code(
    url: str,
    *,
    method: str = "GET",
    headers: Optional[dict] = None,
    data: Optional[str] = None,
    timeout: float = DEFAULT_CURL_MAX_TIME,
    resp_file: str = DEFAULT_RESP_FILE,
) -> int:
    """发送 HTTP 请求并把响应体写入 resp_file，返回纯数字 HTTP 状态码。

    网络层失败（超时 / 连接拒绝 / DNS 解析失败等，即 shell 版 curl 退出码非零的场景）
    统一返回 500 —— 与 shell 版 `|| CODE=500` 映射一致，使上层重试循环能正常触发。
    HTTP 4xx/5xx 属于服务器响应，返回真实状态码（同样进重试）。

    用法: code = curl_http_code("https://api.example.com/releases", method="POST",
                                headers={"Authorization": "Bearer x"}, data='{"a":1}')
    """
    req = urllib.request.Request(url, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    body = data.encode("utf-8") if isinstance(data, str) else data
    try:
        with urllib.request.urlopen(req, data=body, timeout=timeout) as resp:
            _write_resp(resp_file, resp.read())
            return int(resp.status)
    except urllib.error.HTTPError as e:
        # HTTP 错误响应（4xx/5xx）也要落盘，供 read_resp_file 读取错误详情
        _write_resp(resp_file, e.read())
        return int(e.code)
    except (urllib.error.URLError, TimeoutError, OSError):
        # 网络层失败：无响应体，按 shell 版语义映射为 500 进重试
        return 500


def safe_num_or_zero(value: Any) -> int:
    """非纯数字一律返回 0（正则兜底），防止后续数值比较报错。

    shell 版对应:
        [[ "$v" =~ ^[0-9]+$ ]] && echo "$v" || echo 0
    兼容场景：gh/curl 出错时变量混入 JSON/HTML 垃圾文本（如 "JSON垃圾+0"）。
    """
    if isinstance(value, bool):  # Python 中 bool 是 int 子类，需先排除
        return 0
    s = str(value).strip()
    if re.fullmatch(r"[0-9]+", s):
        return int(s)
    return 0


def gh_api_len(path: str, jq_expr: str, timeout: float = 60) -> int:
    """安全获取 gh api 的数值结果（JSON 污染兜底）。

    调用系统 gh CLI: `gh api <path> --jq <jq_expr>`。
    gh 命令失败（退出码非零）或输出混入错误 JSON 时返回 0，
    不会像 shell 版 `-gt` 比较那样抛 "integer expression expected"。

    用法: n = gh_api_len("repos/x/y/commits/z/check-runs",
                          '[.check_runs[] | select(.conclusion != "success")] | length')
    """
    try:
        proc = subprocess.run(
            ["gh", "api", path, "--jq", jq_expr],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return 0
    if proc.returncode != 0:
        return 0
    return safe_num_or_zero(proc.stdout.strip())


def read_resp_file(path: str = DEFAULT_RESP_FILE, max_chars: int = 300) -> str:
    """响应体容错读取：文件缺失/为空时给出明确提示（对齐 shell 版）。

    网络层失败时 curl/urllib 可能没写文件或写空文件；直接读会报错，
    统一输出可读说明便于日志定位。
    """
    try:
        if os.path.isfile(path) and os.path.getsize(path) > 0:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                return f.read(max_chars)
    except OSError:
        pass
    return "(无——网络层失败，已按 HTTP 500 进入重试)"


def _write_resp(path: str, content: bytes) -> None:
    with open(path, "wb") as f:
        f.write(content)


def selfcheck() -> None:
    """快速自检（对齐 shell 版 _release_shell_lib_selfcheck），可直接 python -m 调用。"""
    print("safe_num_or_zero('12')   ->", safe_num_or_zero("12"))
    print("safe_num_or_zero('ab12') ->", safe_num_or_zero("ab12"))
    print("safe_num_or_zero(空)     ->", safe_num_or_zero(""))
    print("safe_num_or_zero(None)   ->", safe_num_or_zero(None))
    print("gh_api_len(gh 缺失)      ->", gh_api_len("repo/x/check-runs", "[] | length", timeout=5))
    print("read_resp_file(不存在)   ->", read_resp_file("/nonexistent/x.json"))
    print("--- selfcheck 完成（curl_http_code 需网络，未在此执行） ---")


if __name__ == "__main__":
    selfcheck()
