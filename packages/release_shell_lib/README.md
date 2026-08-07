# release-shell-lib

发布流程 Shell 函数库（[scripts/release_shell_lib.sh](../../scripts/release_shell_lib.sh)）的
**Python 等价实现**，供非 Shell 语言项目直接引用。行为契约与 Shell 版完全对齐
（逻辑源自 `release-auto.yml` / `release-precheck.yml` 实战验证，详见
[docs/release_workflow_manual.md](../../docs/release_workflow_manual.md) §6.1 / §10 Q3）。

## 解决的问题

| 问题 | Shell 版修复 | 本包等价实现 |
|---|---|---|
| curl 网络层失败（超时/拒连）被 `set -e` 终止、跳过重试循环 | `\|\| CODE=500` 映射进重试 | `curl_http_code()` 网络失败返回 500 |
| `gh api` 失败时错误 JSON 混入 stdout，数值变成垃圾、`-gt` 报错 | `[[ $v =~ ^[0-9]+$ ]]` 正则兜底 | `safe_num_or_zero()` 正则兜底 |
| 网络失败时响应体文件缺失/为空，直接读报错 | `[ -s file ]` 容错 | `read_resp_file()` 容错读取 |

## 安装

```bash
# 本地安装（含依赖树）
pip install -e ./packages/release_shell_lib

# 或构建发行包后安装
cd packages/release_shell_lib
python -m build          # 需 pip install build
pip install dist/release_shell_lib-0.1.0-py3-none-any.whl
```

纯标准库实现，零第三方依赖（`gh_api_len` 依赖系统 `gh` CLI，缺失时安全返回 0）。

## 使用

```python
from release_shell_lib import curl_http_code, safe_num_or_zero, gh_api_len, read_resp_file

# 1. 发请求拿状态码（网络失败统一 500 进重试），响应体落盘 gh_resp.json
code = curl_http_code(
    "https://api.github.com/repos/x/y/releases",
    method="POST",
    headers={"Authorization": "Bearer $TOKEN", "Accept": "application/vnd.github+json"},
    data='{"tag_name":"v1.1.0"}',
    timeout=30,          # 默认 30s，对齐 CURL_MAX_TIME
)
print(code)              # 200 / 404 / 500(网络失败)

# 2. 数值正则兜底（防 gh/curl 出错时混入 JSON 垃圾导致比较报错）
n = safe_num_or_zero(raw)          # "12"->12, "ab12"->0, 空->0

# 3. gh api 数值安全获取（gh 缺失/失败/输出污染一律 0）
bad = gh_api_len("repos/x/y/commits/z/check-runs",
                 '[.check_runs[] | select(.conclusion != "success")] | length')

# 4. 响应体容错读取（文件缺失/空给提示，正常返回前 300 字符）
print(read_resp_file("gh_resp.json"))
```

## 函数速查

| 函数 | 说明 |
|---|---|
| `curl_http_code(url, *, method, headers, data, timeout, resp_file)` | 返回纯数字 HTTP 状态码；网络层失败（超时/拒连/DNS）返回 500；响应体写入 `resp_file`（默认 `gh_resp.json`） |
| `safe_num_or_zero(value)` | 非纯数字一律返回 0，防止数值比较报错 |
| `gh_api_len(path, jq_expr, timeout)` | 调用系统 `gh api <path> --jq <jq_expr>` 并正则兜底；失败返回 0 |
| `read_resp_file(path, max_chars=300)` | 容错读取响应体，缺失/空文件返回提示语 |
| `selfcheck()` | 快速自检（`python -m release_shell_lib` 直接运行） |

## 测试

```bash
cd packages/release_shell_lib
python -m unittest discover -s tests -v
```

网络用例全部走本地 HTTP server（不依赖外网）。
