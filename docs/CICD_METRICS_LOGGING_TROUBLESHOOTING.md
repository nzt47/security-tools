# CI/CD 指标推送日志排查指南

> **创建日期**：2026-07-30
> **关联文件**：`scripts/cicd_metrics_push.py`、`.github/workflows/ci-cd.yml`
> **用途**：解释 push() 函数新增的 4 个日志字段如何用于排查 pushgateway 连接问题

---

## 一、新增日志字段说明

`cicd_metrics_push.py` 的 `push()` 函数在推送全生命周期设置了 4 个日志点：

| 日志点 | 级别 | 位置 | 格式 | 关键字段 |
|---|---|---|---|---|
| ① 推送开始 | INFO | try 前 | `推送开始 → url=X, job=Y` | `url`: pushgateway 地址; `job`: 推送 job 名 |
| ② 推送参数 | INFO | pushadd 前 | `推送参数 → run_id=X, grouping_key=Y` | `run_id`: CI 运行 ID; `grouping_key`: 分组键 |
| ③ 推送成功 | INFO | pushadd 后 | `推送成功 → url=X, job=Y, run_id=Z` | 完整上下文确认 |
| ④ 推送失败 | ERROR | except 中 | `推送失败 → url=X, job=Y, error_type=Z, error=W` | `error_type`: 异常类名; `error`: 异常消息 |

### 字段含义详解

| 字段 | 来源 | 排查用途 |
|---|---|---|
| `url` | `PUSHGATEWAY_URL` 环境变量 | 确认推送目标地址是否正确 |
| `job` | `push(job)` 参数（如 `ci-cd-build`） | 定位是哪个 CI 阶段的推送 |
| `run_id` | `os.environ.get("GITHUB_RUN_ID", "local")` | 区分每次 CI 运行，排查 Counter 覆盖 |
| `grouping_key` | `{"run_id": run_id}` | 确认分组键格式正确 |
| `error_type` | `type(e).__name__` | 快速判断异常类别（无需翻堆栈） |
| `error` | `str(e)` | 异常的具体消息 |

---

## 二、排查 pushgateway 连接问题

### 场景 1：pushgateway 不可达（最常见）

**日志特征**：
```
[metrics] 推送开始 → url=http://monitoring.internal:9091, job=ci-cd-build
[metrics] 推送参数 → run_id=123456789, grouping_key={'run_id': '123456789'}
[metrics] 推送失败（不影响流水线） → url=http://monitoring.internal:9091, job=ci-cd-build, error_type=ConnectionError, error=pushgateway 不可达
```

**排查步骤**：
1. **检查 `url` 字段**：确认 `http://monitoring.internal:9091` 是正确的 pushgateway 地址
2. **检查 `error_type=ConnectionError`**：网络连接问题，pushgateway 服务未启动或不可达
3. **验证连通性**：`curl http://monitoring.internal:9091/-/healthy`
4. **检查 DNS**：`nslookup monitoring.internal`
5. **检查端口**：确认 9091 端口开放，防火墙未拦截

### 场景 2：DNS 解析失败

**日志特征**：
```
[metrics] 推送失败 → error_type=URLError, error=<urlopen error [Errno -2] Name or service not known>
```

**排查步骤**：
1. 检查 `monitoring.internal` 是否在 DNS 中注册
2. 检查 `/etc/hosts` 是否有对应条目
3. 临时方案：在 `ci-cd.yml` 中用 IP 地址替代域名

### 场景 3：prometheus_client 未安装

**日志特征**：
```
[metrics] 推送失败 → error_type=ImportError, error=No module named 'prometheus_client'
```

**排查步骤**：
1. 检查 `requirements.txt` 是否包含 `prometheus_client`
2. 检查 ci-cd.yml 中 `pip install prometheus_client -q 2>/dev/null || true` 是否静默失败
3. **注意**：`2>/dev/null || true` 会吞掉安装错误，排查时临时去掉 `2>/dev/null`

### 场景 4：连接超时

**日志特征**：
```
[metrics] 推送失败 → error_type=URLError, error=<urlopen error timed out>
```

**排查步骤**：
1. pushgateway 负载过高，响应慢
2. 网络延迟过大
3. 考虑增加超时配置（pushadd_to_gateway 默认无超时，可包装 socket.setdefaulttimeout）

### 场景 5：Counter 被覆盖（grouping_key 问题）

**日志特征**：
```
[metrics] 推送参数 → run_id=local, grouping_key={'run_id': 'local'}
```

**排查步骤**：
1. **`run_id=local` 说明 `GITHUB_RUN_ID` 环境变量未设置**
2. 确认 CI 环境是否注入 `GITHUB_RUN_ID`（GitHub Actions 自动注入）
3. 本地运行时 `run_id=local` 是预期行为（多次运行会互相覆盖，可接受）
4. **在 CI 中应看到数字 run_id**（如 `123456789`），若仍为 `local` 则环境变量配置有误

---

## 三、日志级别配置说明

### cicd_metrics_push.py 的日志级别（支持运行时动态配置）

`cicd_metrics_push.py` 使用 Python 标准 `logging` 模块，在 `main()` 中通过 `LOG_LEVEL` 环境变量动态配置：

```python
# [不易] 日志级别支持运行时动态配置
# LOG_LEVEL 有效值：DEBUG/INFO/WARNING/ERROR/CRITICAL，无效值降级为 INFO
_log_level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
_log_level = getattr(logging, _log_level_name, logging.INFO)
if not isinstance(_log_level, int):
    _log_level = logging.INFO
logging.basicConfig(level=_log_level, format="%(message)s")
```

| 配置项 | 值 | 说明 |
|---|---|---|
| `LOG_LEVEL` 环境变量 | `INFO`（默认） | 支持 DEBUG/INFO/WARNING/ERROR/CRITICAL |
| `format` | `%(message)s` | 仅输出消息体（已在消息中包含上下文） |
| 无效值降级 | `INFO` | 非法值（如 `FOO`）自动降级为 INFO，不报错 |

#### 运行时配置方式

**方式 1：ci-cd.yml 全局 env（推荐）**

在 `.github/workflows/ci-cd.yml` 顶部 `env` 中添加：

```yaml
env:
  ERROR_REPORTING_FILE_LEVEL: error
  ERROR_REPORTING_CONSOLE_LEVEL: warning
  PUSHGATEWAY_URL: http://monitoring.internal:9091
  LOG_LEVEL: INFO  # [新增] cicd_metrics_push.py 日志级别
```

**方式 2：step 级 env（按需覆盖）**

在特定埋点 step 中覆盖：

```yaml
      - name: Record CI build metrics
        if: always()
        env:
          PUSHGATEWAY_URL: ${{ env.PUSHGATEWAY_URL }}
          LOG_LEVEL: DEBUG  # [按需] 排查时临时改为 DEBUG
        run: |
          python scripts/cicd_metrics_push.py --stage build ${{ success() && '--success' || '' }}
```

**方式 3：命令行临时指定**

本地运行或 CI 中临时测试：

```bash
# Linux/macOS
LOG_LEVEL=DEBUG python scripts/cicd_metrics_push.py --stage build --success

# Windows PowerShell
$env:LOG_LEVEL="DEBUG"; python scripts/cicd_metrics_push.py --stage build --success
```

#### 各日志级别的输出范围

| LOG_LEVEL | ① 推送开始(INFO) | ② 推送参数(INFO) | ③ 推送成功(INFO) | ④ 推送失败(ERROR) | 适用场景 |
|---|---|---|---|---|---|
| `DEBUG` | ✅ | ✅ | ✅ | ✅ | 本地开发/深度排查 |
| `INFO`（默认） | ✅ | ✅ | ✅ | ✅ | 生产环境（推荐） |
| `WARNING` | ❌ | ❌ | ❌ | ✅ | 仅看错误（减少日志量） |
| `ERROR` | ❌ | ❌ | ❌ | ✅ | 仅看推送失败 |
| `CRITICAL` | ❌ | ❌ | ❌ | ❌ | 静默模式（不推荐） |

#### 防御机制

| 场景 | 行为 |
|---|---|
| `LOG_LEVEL` 未设置 | 默认 `INFO` |
| `LOG_LEVEL=foo`（无效值） | 降级为 `INFO`，不报错 |
| `LOG_LEVEL=info`（小写） | `.upper()` 转为 `INFO`，正常生效 |
| `LOG_LEVEL=Formatter`（非 int 属性） | `isinstance` 检查失败，降级为 `INFO` |

**结论**：新增的 ERROR 日志（推送失败）**会被输出**，无需额外配置。INFO 级别已覆盖所有 4 个日志点。生产环境建议保持 `LOG_LEVEL=INFO`，排查问题时临时改为 `DEBUG`。

### ci-cd.yml 的日志配置

`ci-cd.yml` 的全局 `env` 中有：

```yaml
env:
  ERROR_REPORTING_FILE_LEVEL: error      # error_reporting 模块的文件日志级别
  ERROR_REPORTING_CONSOLE_LEVEL: warning  # error_reporting 模块的控制台日志级别
  PUSHGATEWAY_URL: http://monitoring.internal:9091
```

**重要区分**：

| 配置项 | 影响范围 | 是否影响 cicd_metrics_push.py |
|---|---|---|
| `ERROR_REPORTING_FILE_LEVEL` | `error_reporting` 模块 | ❌ 不影响 |
| `ERROR_REPORTING_CONSOLE_LEVEL` | `error_reporting` 模块 | ❌ 不影响 |
| `logging.basicConfig(level=INFO)` | `cicd_metrics_push.py` 自身 | ✅ 决定其日志输出 |

### 结论：ci-cd.yml 无需同步更新

`cicd_metrics_push.py` 的日志级别由自身的 `logging.basicConfig(level=logging.INFO)` 控制，与 `ci-cd.yml` 中的 `ERROR_REPORTING_*_LEVEL` 无关。新增的 ERROR 日志已被 INFO 级别覆盖，**无需在 ci-cd.yml 中同步更新日志级别配置**。

---

## 四、日志过滤技巧

在 CI 中过滤特定级别的日志：

```bash
# 只看 ERROR 日志（推送失败）
python scripts/cicd_metrics_push.py --stage build 2>&1 | grep "推送失败"

# 只看推送参数（排查 grouping_key）
python scripts/cicd_metrics_push.py --stage build 2>&1 | grep "推送参数"

# 只看特定 job 的日志
python scripts/cicd_metrics_push.py --stage build 2>&1 | grep "ci-cd-build"

# 查看完整推送流程（开始→参数→成功/失败）
python scripts/cicd_metrics_push.py --stage build 2>&1 | grep "\[metrics\]"
```

### GitHub Actions 中查看日志

在 GitHub Actions 的 step 输出中，搜索 `[metrics]` 即可过滤所有指标推送日志：

```
Run python scripts/cicd_metrics_push.py --stage build --success
  [metrics] 推送开始 → url=http://monitoring.internal:9091, job=ci-cd-build
  [metrics] 推送参数 → run_id=123456789, grouping_key={'run_id': '123456789'}
  [metrics] 推送成功 → url=http://monitoring.internal:9091, job=ci-cd-build, run_id=123456789
```

---

## 五、error_type 速查表

| error_type | 含义 | 常见原因 | 处理方式 |
|---|---|---|---|
| `ConnectionError` | 连接被拒绝 | pushgateway 未启动 / 端口未开放 | 启动 pushgateway 服务 |
| `URLError` | URL 相关错误 | DNS 解析失败 / 超时 | 检查 DNS / 网络 |
| `ImportError` | 模块未找到 | prometheus_client 未安装 | `pip install prometheus_client` |
| `OSError` | 系统级错误 | socket 异常 | 检查系统资源 |
| `TypeError` | 类型错误 | grouping_key 格式错误 | 检查 grouping_key 是否为 dict |
| `ValueError` | 值错误 | 指标值非法（NaN/Inf） | 检查指标值合法性 |

---

## 六、相关文件

| 文件 | 说明 |
|---|---|
| `scripts/cicd_metrics_push.py` | CI/CD 指标推送脚本（含 4 个日志点） |
| `scripts/test_grouping_key_local.py` | grouping_key 本地验证测试（5 用例） |
| `.github/workflows/ci-cd.yml` | CI/CD 流水线（含埋点 step） |
| `docs/CICD_METRICS_KNOWN_ISSUES.md` | CI/CD 指标埋点已知问题与规避说明 |
