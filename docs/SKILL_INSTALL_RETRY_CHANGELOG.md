# Change Log：TimeoutError 捕获修复 + 安装网络重试机制 + 集成测试

> 分支建议：`fix/skill-install-timeout-retry`
> 关联文件：
> - `agent/skills_mgmt/creator.py`（`_fetch_json` 修复 + 统一 RetryPolicy 重试）
> - `tests/integration/test_skill_install_loop.py`（外来技能安装闭环集成测试）
> - `scripts/simulate_skill_install_network_flaky.py`（网络中断重试验证脚本）
> - `tests/unit/test_skill_file_store_path_traversal.py`（本地文件篡改防护测试）

---

## 一、变更概述

| 类型 | 内容 | 影响 |
|------|------|------|
| Bug 修复 | `urlopen` 超时抛 `TimeoutError`（非 `URLError`），原 `_fetch_json` 只捕获后者 → 超时穿透契约，未转 `SkillInstallError` | 超时现在正确转 `INSTALL_SOURCE_UNREACHABLE` |
| 功能增强 | `_fetch_json` 集成统一 `RetryPolicy` 指数退避重试，env 可配 | 网络瞬时中断（断流/超时/连接重置）自动重试，成功率提升 |
| 测试新增 | 集成测试补网络中断/失败边界用例；新增路径穿越防护单元测试 | 覆盖「下载失败→重试」「恶意篡改本地文件→拦截」两个攻击面 |

---

## 二、缺陷详情

### 2.1 修复前

```python
try:
    with urllib.request.urlopen(req, timeout=self._http_timeout) as resp:
        data = resp.read().decode("utf-8")
except urllib.error.URLError as e:
    raise SkillInstallError(f"网络请求失败: {e}",
        code=ErrorCode.INSTALL_SOURCE_UNREACHABLE)
```

### 2.2 根因

`urllib.request.urlopen(..., timeout=N)` 在连接建立后读取超时时抛的是
**`TimeoutError`**（Python 内建，`socket.timeout` 别名），不是 `URLError`。
原代码 `except urllib.error.URLError` 无法捕获 → 超时异常**穿透**安装契约，
调用方拿不到带业务错误码的 `SkillInstallError`。

修复前验证：
```python
>>> import urllib.request
>>> urllib.request.urlopen("http://10.255.255.1/", timeout=1)
...
TimeoutError: timed out   # 而非 URLError → 原 except 分支不命中
```

### 2.3 修复方案

`TimeoutError` 并入可重试网络异常元组，并集成统一重试策略：

```python
# 可重试网络级异常：连接失败/超时/下载中途断流(IncompleteRead)/连接重置
retryable = (urllib.error.URLError, TimeoutError,
             http.client.HTTPException, ConnectionResetError, OSError)
policy = self._build_retry_policy()
attempt = 0
while True:
    try:
        ...  # urlopen 下载
        break
    except SkillInstallError:
        raise                    # HTTP >= 400 不重试
    except retryable as e:
        if not policy.should_retry(e, attempt):
            raise SkillInstallError(f"网络请求失败（已尝试 {attempt + 1} 次）: {e}",
                code=ErrorCode.INSTALL_SOURCE_UNREACHABLE, ...) from e
        time.sleep(policy.calculate_delay(attempt))
        attempt += 1
```

**语义保持**：
- HTTP ≥ 400 与 JSON 解析失败**不重试**（服务端已应答，重试无意义）
- 只有网络级异常（未收到完整响应）才进入退避重试
- 重试耗尽后仍转 `INSTALL_SOURCE_UNREACHABLE`，契约不变

---

## 三、重试机制设计

统一复用 `agent/error_handler.py` 的 `RetryPolicy`（守 project_memory
硬约束：重试必须用统一类，禁止自造轮子）。

| 配置项 | env 变量 | 默认值 | 说明 |
|--------|----------|--------|------|
| 最大重试次数 | `SKILL_INSTALL_MAX_RETRIES` | 3 | 0 表示关闭重试 |
| 初始退避秒数 | `SKILL_INSTALL_RETRY_BACKOFF` | 0.5 | 指数退避，封顶 10s |
| 退避策略 | — | exponential | `delay = initial * 2^attempt` |

---

## 四、新增测试用例

### 4.1 集成测试（`tests/integration/test_skill_install_loop.py`）

| 用例 | 场景 | 断言 |
|------|------|------|
| `test_download_to_review_to_store` | 良性技能：真实 HTTP 下载 → 扫描 → 评分 → 落库 | 状态 pending_review → approved，security_score ≥ 70 |
| `test_store_persists_across_reload` | 重建服务实例（模拟重启） | 技能仍可读，状态/内容完整 |
| `test_cmd_injection_blocked_in_loop` | 下载含 `rm -rf /` 的恶意技能 | review→failed、security_score=0、落库 rejected |
| `test_fork_bomb_blocked_in_loop` | 下载含行首 fork bomb | 同样拦截（回归安全漏报） |
| `test_unreachable_url_raises` | 服务器不可达（端口关闭） | 抛 `INSTALL_SOURCE_UNREACHABLE`（**回归 TimeoutError 修复**；monkeypatch 关闭重试加速失败） |
| `test_invalid_json_payload_raises` | 响应非法 JSON | 抛 `INSTALL_FAILED` |
| `test_nonexistent_skill_after_failed_review` | 未安装过 | 抛 `SkillNotFoundError` |

### 4.2 网络中断重试验证脚本（`scripts/simulate_skill_install_network_flaky.py`）

本地 HTTP 服务器**谎报 Content-Length 后只发前 10 字节即强制断开连接**，
触发客户端 `IncompleteRead`（http.client.HTTPException），真实模拟下载中途断流：

- **场景 A 瞬时中断**：第 1 次断流 → 重试后第 2 次成功。断言 install 成功且服务器收到 ≥ 2 次请求
- **场景 B 持续中断**：每次均断流 → 重试耗尽。断言抛 `INSTALL_SOURCE_UNREACHABLE` 且请求数 = 1 + max_retries

运行：`python scripts/simulate_skill_install_network_flaky.py [--max-retries N] [--backoff S]`

### 4.3 本地文件篡改防护（`tests/unit/test_skill_file_store_path_traversal.py`）

模拟恶意技能**绕过下载/扫描阶段**直接调用 `SkillFileStore` 写入 API 修改本地文件：
路径穿越（`../`、`..\`、绝对路径）在 skill_id / 脚本名 / 模板名三个入口
全部被拦截（`INVALID_SKILL_ID` / `INVALID_SCRIPT_NAME` / `INVALID_FILENAME` /
`PATH_TRAVERSAL`），并验证仓库边界外零新增文件。

---

## 五、验证结果

```
# 网络中断重试脚本（max_retries=2, backoff=0.1）
| A 瞬时中断(重试后成功) | 2 | 3 | 成功✓✓ | flaky-skill      |  155.4 |
| B 持续中断(重试耗尽)   | 3 | 3 | 失败✗✓ | SKILL_INSTALL_SOURCE_UNREACHABLE | 305.5 |
[结论] 场景 A 通过：瞬时中断后自动重试 1 次并成功
[结论] 场景 B 通过：重试耗尽（2 次）后正确抛 SkillInstallError 并停止重试

# pytest
tests/integration/test_skill_install_loop.py                   7 passed
tests/unit/test_skill_file_store_path_traversal.py            34 passed
tests/unit/test_security_scanner_malicious_skill.py           25 passed
```

---

## 六、风险与回滚

- **风险**：默认 3 次重试 + 指数退避会放大安装耗时（最坏 ~0.5+1+2=3.5s
  退避 + 3 次超时窗口）。可通过 `SKILL_INSTALL_MAX_RETRIES=0` 关闭重试
  恢复旧行为；真实生产建议按链路预算调低超时/重试。
- **回滚**：删除 `_fetch_json` 中重试循环并还原单次 `except URLError` 分支即可
  （缺陷将复现，已知问题见 2.2）。
- **遗留**：`create` 写 `temp_files` 对非法文件名**静默跳过**（不抛错），
  与 `add_temp_file`（抛 `INVALID_FILENAME`）行为不一致，属已知设计差异；
  安全影响为「不落盘」，无越界风险。
