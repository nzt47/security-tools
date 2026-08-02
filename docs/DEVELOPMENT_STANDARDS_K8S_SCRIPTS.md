# K8s 脚本开发规范 — 库依赖与参数处理

> **适用范围**: 在 Kubernetes 集群内执行的内联 Python 脚本、通过 `kubectl exec` 调用的探测/巡检脚本。
> **制定依据**: 2026-08-01 HPA 巡检脚本（`hpa_scale_patrol.py`）验证过程中发现的 3 类典型错误。
> **核心原则**: 集群内执行环境不可控，必须按最简依赖 + 显式参数传递设计。

---

## 1. 问题背景

### 1.1 典型错误复盘

在 HPA 巡检脚本开发中，因库依赖和参数处理问题导致 3 次故障：

| # | 错误 | 根因 | 影响 | 修复 |
|---|------|------|------|------|
| 1 | `ModuleNotFoundError: No module named 'requests'` | 内联探测脚本依赖 `requests`，但服务镜像未安装该库 | 巡检脚本无法触发流量 | 改用 `urllib` 标准库 |
| 2 | `TypeError: PatrolConfig.__init__() got an unexpected keyword argument 'output'` | `--output` CLI 参数被误传给业务配置 dataclass | 脚本启动即崩溃 | `parse_args` 返回 `tuple[Config, output_path]` |
| 3 | `TypeError: '>' not supported between instances of 'int' and 'str'` | `_snapshot(t, t, "巡检开始")` 把字符串误传给 `current: int` 参数 | 运行时崩溃 | 改用关键字参数 `note="巡检开始"` |

### 1.2 根因分析

1. **库依赖盲区**: 开发者习惯使用 `requests`（第三方库），忽略了集群内 Pod 镜像可能未安装该库
2. **参数传递隐式化**: `**kwargs` 批量传递 CLI 参数到 dataclass，非业务参数（如 `output`）混入业务配置
3. **位置参数歧义**: 函数有多个同类型参数（如 `current: int` 和 `note: str`），位置传递时容易错位

---

## 2. 规范一：库依赖选择

### 2.1 核心原则

> **集群内执行的内联脚本，必须仅使用 Python 标准库。**

| 场景 | 允许的库 | 禁止的库 |
|------|---------|---------|
| `kubectl exec` 内联脚本 | `urllib`, `json`, `time`, `concurrent.futures`, `http.server` | `requests`, `aiohttp`, `httpx` |
| 巡检/基准测试主脚本（本地运行） | 标准库 + 明确在 requirements.txt 声明的第三方库 | 未声明的第三方库 |
| Mock/测试工具（Pod 内运行） | 标准库优先 | 必须在 Dockerfile 中 `pip install` |

### 2.2 HTTP 请求：urllib 替代 requests

#### ❌ 错误：依赖 requests（镜像内可能未安装）

```python
import requests

session = requests.Session()
response = session.post(endpoint, json={"query": "test"}, timeout=2)
```

#### ✅ 正确：使用 urllib 标准库（零依赖）

```python
import json
import urllib.request

body = json.dumps({"query": "test"}).encode("utf-8")
req = urllib.request.Request(
    endpoint,
    data=body,
    method="POST",
    headers={"Content-Type": "application/json"},
)
try:
    response = urllib.request.urlopen(req, timeout=2)
    status = response.status
    content = response.read()
except urllib.error.URLError as e:
    # 超时、连接拒绝等异常处理
    pass
```

### 2.3 urllib 常用模式速查

#### GET 请求

```python
import urllib.request

response = urllib.request.urlopen(url, timeout=5)
status = response.status
body = response.read().decode("utf-8")
```

#### POST JSON

```python
import json
import urllib.request

body = json.dumps({"key": "value"}).encode("utf-8")
req = urllib.request.Request(
    url, data=body, method="POST",
    headers={"Content-Type": "application/json"},
)
response = urllib.request.urlopen(req, timeout=5)
```

#### 并发请求（ThreadPoolExecutor + urllib）

```python
import concurrent.futures
import json
import time
import urllib.request

def worker(_):
    end = time.time() + duration
    while time.time() < end:
        try:
            req = urllib.request.Request(
                endpoint, data=body, method="POST",
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=2)
        except Exception:
            pass  # 压测场景忽略个别错误

body = json.dumps({"query": "probe"}).encode("utf-8")
with concurrent.futures.ThreadPoolExecutor(max_workers=vu) as pool:
    list(pool.map(worker, range(vu)))
```

#### 简易 HTTP 服务器（Mock Webhook）

```python
from http.server import HTTPServer, BaseHTTPRequestHandler

class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")
        # 处理告警...
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"status":"ok"}')

    def log_message(self, *args):
        pass  # 抑制默认日志

HTTPServer(("0.0.0.0", 9093), Handler).serve_forever()
```

### 2.4 检查清单

在提交内联脚本前，逐项确认：

- [ ] 脚本仅 `import` Python 标准库模块
- [ ] 无 `import requests` / `import aiohttp` / `import httpx`
- [ ] HTTP 请求使用 `urllib.request`
- [ ] JSON 序列化使用 `json.dumps` + `.encode("utf-8")`
- [ ] 异常处理用 `urllib.error.URLError`（不是 `requests.exceptions`）
- [ ] 超时通过 `urlopen(req, timeout=N)` 设置（不是 `requests.get(timeout=N)`）

---

## 3. 规范二：参数传递模式

### 3.1 核心原则

> **CLI 参数与业务配置严格分离，非业务参数不得混入 dataclass。**

### 3.2 正确模式：parse_args 返回 tuple

#### ❌ 错误：所有参数批量传入 dataclass

```python
@dataclass
class PatrolConfig:
    hpa_name: str
    namespace: str
    # ... 业务字段 ...
    # 没有 output 字段！

def parse_args() -> PatrolConfig:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hpa-name", required=True)
    parser.add_argument("--output", default=None)  # 非业务参数
    args = parser.parse_args()
    # 错误：output 被误传给 PatrolConfig
    return PatrolConfig(**vars(args))
    # → TypeError: unexpected keyword argument 'output'
```

#### ✅ 正确：业务参数与非业务参数分离

```python
@dataclass
class PatrolConfig:
    """业务配置（仅含业务字段）"""
    hpa_name: str
    namespace: str
    target_replicas: int = 15
    # ... 仅业务字段 ...

def parse_args() -> tuple[PatrolConfig, Optional[str]]:
    """返回 (业务配置, 非业务参数)"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--hpa-name", required=True)
    parser.add_argument("--target-replicas", type=int, default=15)
    # ... 业务参数 ...
    parser.add_argument("--output", default=None)  # 非业务参数
    parser.add_argument("--verbose", action="store_true")  # 非业务参数
    args = parser.parse_args()

    # 非业务参数单独提取
    output_path = args.output

    # 仅传业务字段给 dataclass
    config_fields = {
        "hpa_name", "namespace", "target_replicas",
        # ... 仅业务字段名 ...
    }
    config_kwargs = {
        k: getattr(args, k) for k in config_fields
        if getattr(args, k) is not None or k in ["hpa_name", "namespace"]
    }
    return PatrolConfig(**config_kwargs), output_path

def main() -> int:
    config, output_path = parse_args()
    # config 是纯业务配置，output_path 是非业务参数
    ...
```

### 3.3 参数分类标准

| 类别 | 归属 | 示例 |
|------|------|------|
| **业务参数** | 传入 dataclass | `hpa_name`, `namespace`, `target_replicas`, `probe_vu` |
| **输出参数** | 单独返回 | `--output`, `--verbose`, `--dry-run` |
| **调试参数** | 单独返回 | `--debug`, `--log-level` |

### 3.4 检查清单

- [ ] dataclass 仅包含业务字段，无 `output` / `verbose` / `debug`
- [ ] `parse_args` 返回 `tuple[Config, 非业务参数]`
- [ ] 业务字段通过白名单（`config_fields` 集合）过滤，不使用 `**vars(args)`
- [ ] dataclass 字段有默认值（除 `required=True` 的外）

---

## 4. 规范三：函数参数传递 — 关键字参数优先

### 4.1 核心原则

> **函数有多个参数时，调用方必须使用关键字参数，禁止位置参数。**

### 4.2 错误模式

#### ❌ 错误：位置参数导致类型错位

```python
@dataclass
class ScaleTimelineEvent:
    timestamp: str
    elapsed_sec: float
    current_replicas: int    # 期望 int
    ready_replicas: int
    cpu_utilization: Optional[float]
    event_note: str = ""

def _snapshot(self, now, t_start, current=0, ready=0, cpu=None, note=""):
    return ScaleTimelineEvent(...)

# 错误：把字符串 "巡检开始" 传给了 current: int
timeline.append(self._snapshot(t_start, t_start, "巡检开始"))
# → current_replicas = "巡检开始" (str)
# → 后续 current > timeline[-1].current_replicas 时 TypeError
```

#### ✅ 正确：关键字参数

```python
# 正确：用 note= 显式指定
timeline.append(self._snapshot(t_start, t_start, note="巡检开始"))
# → current=0 (默认值), note="巡检开始"
```

### 4.3 适用场景

当函数满足以下任一条件时，**必须**使用关键字参数调用：

1. 参数超过 3 个
2. 有多个同类型参数（如两个 `int` 或两个 `str`）
3. 有默认值的参数与非默认参数混合
4. 参数名具有业务语义（如 `current` vs `note`）

### 4.4 强制关键字参数（Python 3.8+）

对于新函数，建议用 `*` 强制关键字参数：

```python
def _snapshot(
    self,
    now: float,
    t_start: float,
    *,
    current: int = 0,
    ready: int = 0,
    cpu: Optional[float] = None,
    note: str = "",
) -> ScaleTimelineEvent:
    """* 后的参数必须用关键字传递"""
    ...
```

调用方必须写 `_snapshot(now, t_start, note="巡检开始")`，写 `_snapshot(now, t_start, "巡检开始")` 会报 `TypeError`。

### 4.5 检查清单

- [ ] 多参数函数调用使用关键字参数（`name=value`）
- [ ] 新函数定义使用 `*` 强制关键字参数
- [ ] dataclass 字段类型与赋值类型一致（`int` 字段不传 `str`）

---

## 5. 规范四：K8s API 返回值类型防御

### 5.1 核心原则

> **K8s API 返回的 JSON 字段可能为字符串或缺失，必须强制类型转换。**

### 5.2 错误模式

#### ❌ 错误：直接使用 API 返回值，未做类型转换

```python
def get_hpa_replicas(hpa_name):
    hpa = kubectl_json(["get", "hpa", hpa_name])
    current = hpa.get("status", {}).get("currentReplicas", 0)
    # current 可能是 int，也可能是 str（某些 K8s 版本）
    return current  # 类型不确定

current = get_hpa_replicas("my-hpa")
if current > 5:  # 如果 current 是 str，这里 TypeError
    ...
```

#### ✅ 正确：强制类型转换 + 空值防御

```python
def get_hpa_replicas(hpa_name: str) -> tuple[int, int]:
    hpa = kubectl_json(["get", "hpa", hpa_name])
    # 【防御】int() 强制转换，or 0 处理 None
    current = int(hpa.get("status", {}).get("currentReplicas", 0) or 0)
    desired = int(hpa.get("status", {}).get("desiredReplicas", 0) or 0)
    return current, desired
```

### 5.3 常见 K8s API 字段类型陷阱

| 字段 | 期望类型 | 实际可能返回 | 防御方式 |
|------|---------|-------------|---------|
| `currentReplicas` | int | int / str / None | `int(x or 0)` |
| `readyReplicas` | int | int / None（Pod 启动中缺失） | `int(x or 0)` |
| `averageUtilization` | float | int / None | `float(x) if x is not None else None` |
| `currentMetrics` | list | list / None | `.get("currentMetrics", []) or []` |

### 5.4 检查清单

- [ ] 所有 K8s API 返回值经过 `int()` / `float()` / `str()` 类型转换
- [ ] 使用 `or 0` / `or ""` / `or []` 处理 None 值
- [ ] 可选字段用 `Optional[float]` 类型注解，调用方检查 None

---

## 6. 代码审查检查清单（合并前必填）

```markdown
## 库依赖检查
- [ ] 内联脚本（kubectl exec）仅使用 Python 标准库
- [ ] 无 import requests / aiohttp / httpx（内联脚本中）
- [ ] HTTP 请求使用 urllib.request

## 参数传递检查
- [ ] dataclass 仅含业务字段（无 output/verbose/debug）
- [ ] parse_args 返回 tuple[Config, 非业务参数]
- [ ] 业务字段通过白名单过滤，不用 **vars(args)
- [ ] 多参数函数调用使用关键字参数

## 类型安全检查
- [ ] K8s API 返回值经过 int()/float() 类型转换
- [ ] 使用 or 0 / or [] 处理 None
- [ ] dataclass 字段类型与赋值类型一致

## 异常处理检查
- [ ] urllib.error.URLError 被捕获（不是 requests.exceptions）
- [ ] kubectl 命令失败有明确错误信息
- [ ] 子进程（subprocess）有超时保护
```

---

## 7. 相关文件

- [HPA 巡检脚本](../scripts/hpa_scale_patrol.py) — 本规范的实践示例
- [扩容基准测试脚本](../scripts/hpa_scale_3to15_benchmark.py) — 遵循本规范
- [本地 Webhook 服务器](../scripts/webhook_server.py) — 测试用 HTTP 服务器示例
- [HPA 对比压测计划](HPA_COMPARISON_LOADTEST_PLAN.md) — 压测方案与数据

---

## 8. 变更记录

| 日期 | 版本 | 变更 | 作者 |
|------|------|------|------|
| 2026-08-01 | v1.0 | 初始版本，基于 HPA 巡检脚本 3 类错误复盘 | — |
