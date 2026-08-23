# 云枢 API 网关 CLI 工具

网关限流/配额验证命令行工具，供各团队直接调用（无需了解云枢内部结构）。

## 安装

```powershell
# 完整安装（会携带 torch 等重依赖，适合主项目）
pip install .

# 轻量安装（仅 CLI 需要，推荐给只验证网关的团队）
pip install . --no-deps
pip install requests
```

安装后生成全局命令 `yunshu-gateway-check`（位于 venv/Scripts 或 Python Scripts 目录，需在 PATH 中）。

> **注意**：请使用 Python 3.10–3.12。若使用旧版 pip（<26），editable 开发安装也可用：
> `pip install -e . --no-deps`

## 使用

```powershell
# 全量验证（需网关服务运行在 5678）
yunshu-gateway-check

# 仅单元级验证（无需运行服务，CI 推荐）
yunshu-gateway-check --unit-only

# 附带 429 压测（短暂占用生产限流令牌约 1 秒）
yunshu-gateway-check --http-stress

# JSON 输出（便于接入监控/告警）
yunshu-gateway-check --unit-only --json

# 指定服务地址
yunshu-gateway-check --base-url http://127.0.0.1:5678
```

等价模块调用方式（无需安装）：
```powershell
python -m agent.api_gateway_cli --unit-only
```

**退出码**：`0` = 全部通过；`1` = 任一检查失败（可直接接入 CI 门禁）。

**检查项**：
| 检查 | 说明 |
|---|---|
| rate_limit(单元级) | 独立实例验证多级令牌桶限流耗尽返回 429 |
| quota(单元级) | 独立实例验证配额耗尽返回 429 |
| http_liveness(只读) | 探测 /api/open/echo、/api/open/stats、/api/docs |
| http_rate_limit_stress | 连续请求触发真实 endpoint 限流，断言 429 |

## pyproject.toml 打包修复说明（2026-08-16）

**背景**：`pip install .` 后 console script 报 `ModuleNotFoundError: No module named 'agent'`。

**根因**：原配置
```toml
[tool.setuptools.packages.find]
where = ["agent", "sensor", ...]   # ← 错误
include = ["*"]
```
`where` 指定的是**包搜索根目录**而非包本身。setuptools 会进入 `agent/` 目录，把 `agent/audit`、`agent/caching` 等子包当作**独立顶层包**（`audit`、`caching`）安装——agent 包本体缺失，且污染了顶层命名空间。

**修复**（[pyproject.toml](pyproject.toml)）：
```toml
[tool.setuptools.packages.find]
where = ["."]
include = ["agent*", "sensor*", "memory*", "planning*", "persona*",
           "core*", "cognitive*", "lifetrace*", "utils*"]
```
- `where = ["."]`：从项目根开始查找
- `include` 用顶层包名通配白名单，避免误收其他目录

**回归验证**：修复后 `pip install . --no-deps` 应能在 `site-packages/` 看到 `agent/`，且 `yunshu-gateway-check --unit-only` 正常通过（exit 0）。

**注意事项**：修改 `pyproject.toml` 的 `packages.find` 后需重新安装（`pip uninstall Yunshu` 后 `pip install .`），editable 与普通安装不要混用同一包名。
