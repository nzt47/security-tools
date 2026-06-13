# 云枢沙盒功能开关 — 环境切换验证指南

## 一、功能概述

沙盒功能通过环境变量 `YUNSHU_FEATURE_SANDBOX` 控制开关：
- `false`（默认）：沙盒关闭，`/api/sandbox/run` 返回 503
- `true`：沙盒启用，可在受限环境中执行 Python 代码

## 二、本地环境切换

### 2.1 启动时设置（PowerShell）

```powershell
# 沙盒关闭（默认）
$env:YUNSHU_FEATURE_SANDBOX = 'false'
python app_server.py

# 沙盒启用
$env:YUNSHU_FEATURE_SANDBOX = 'true'
python app_server.py
```

### 2.2 启动时设置（Linux/macOS）

```bash
# 沙盒关闭（默认）
YUNSHU_FEATURE_SANDBOX=false python app_server.py

# 沙盒启用
YUNSHU_FEATURE_SANDBOX=true python app_server.py
```

### 2.3 验证沙盒状态

**方式1：查看启动日志**

启动时控制台输出：
```
INFO:__main__:[沙盒] 功能状态: 已关闭 (YUNSHU_FEATURE_SANDBOX=未设置(默认false))
  沙盒：已关闭
```
或：
```
INFO:__main__:[沙盒] 功能状态: 已启用 (YUNSHU_FEATURE_SANDBOX=true)
  沙盒：已启用
```

**方式2：请求沙盒接口**

```bash
# 请求
curl -X POST http://127.0.0.1:5678/api/sandbox/run \
  -H "Content-Type: application/json" \
  -d '{"code": "x = sum(range(10))"}'

# 沙盒关闭时返回 503：
# {"blocked": true, "error": "沙盒功能已关闭，设置环境变量 YUNSHU_FEATURE_SANDBOX=true 可启用", "sandbox_disabled": true}

# 沙盒启用时返回 200：
# {"error": null, "safety": {"level": "safe", ...}, "stdout": "", "stderr": "", "timed_out": false}
```

**方式3：运行健康检查脚本**

```bash
python health_check.py
# 沙盒关闭时：/api/sandbox/run -> 503 (PASS)
# 沙盒启用时需修改 health_check.py 中沙盒接口的 expected 为 200
```

## 三、Docker 环境切换

### 3.1 配置文件

环境变量已在三处配置：

| 文件 | 配置 | 说明 |
|------|------|------|
| `Dockerfile` | `ENV YUNSHU_FEATURE_SANDBOX=false` | 默认值，可被运行时覆盖 |
| `docker-compose.yml` | `YUNSHU_FEATURE_SANDBOX=${YUNSHU_FEATURE_SANDBOX:-false}` | 支持 .env 文件 |
| `.env.example` | `YUNSHU_FEATURE_SANDBOX=false` | 环境变量模板 |

### 3.2 启动容器（沙盒关闭）

```bash
# 默认配置，无需额外设置
docker compose up -d

# 验证
curl -X POST http://容器地址:5678/api/sandbox/run \
  -H "Content-Type: application/json" \
  -d '{"code": "1+1"}'
# 期望返回 503
```

### 3.3 启动容器（沙盒启用）

**方式1：命令行覆盖**
```bash
docker compose run -e YUNSHU_FEATURE_SANDBOX=true -p 5678:5678 digital-life
```

**方式2：.env 文件**
```bash
echo "YUNSHU_FEATURE_SANDBOX=true" >> .env
docker compose up -d
```

**方式3：docker run**
```bash
docker run -e YUNSHU_FEATURE_SANDBOX=true -p 5678:5678 yunshu-agent:latest
```

### 3.4 验证 Docker 环境变量

```bash
# 进入容器检查
docker compose exec digital-life env | grep YUNSHU
# 期望输出：YUNSHU_FEATURE_SANDBOX=true（或 false）

# 查看容器日志
docker compose logs digital-life | grep "沙盒"
# 期望输出：[沙盒] 功能状态: 已启用 (YUNSHU_FEATURE_SANDBOX=true)
```

## 四、沙盒运行时日志

沙盒功能在运行时会产生以下日志：

| 场景 | 日志级别 | 日志内容 |
|------|----------|----------|
| 启动时 | INFO | `[沙盒] 功能状态: 已启用/已关闭 (YUNSHU_FEATURE_SANDBOX=xxx)` |
| 访问被拒 | WARNING | `[沙盒] 访问被拒绝 - 沙盒功能已关闭` |
| 启用执行 | INFO | `[沙盒] 沙盒功能已启用，开始执行代码` |
| 安全拦截 | WARNING | `[沙盒] 代码被安全检查拦截: ...` |
| 执行出错 | WARNING | `[沙盒] 代码执行出错: ...` |
| 执行超时 | WARNING | `[沙盒] 代码执行超时 (Ns)` |
| 执行成功 | INFO | `[沙盒] 代码执行成功，耗时 Nms` |

## 五、健康检查脚本

### 5.1 常用命令

```bash
# 单次检查
python health_check.py

# 定时检查（每 60 秒）
python health_check.py --interval 60

# 指定服务器
python health_check.py --host http://192.168.1.100:5678

# JSON 格式输出
python health_check.py --json
```

### 5.2 报告文件

报告自动保存至 `logs/health_check/` 目录：
- `health_YYYYMMDD_HHMMSS.json` — JSON 格式详细报告
- `health_YYYYMMDD_HHMMSS.txt` — 文本格式可读报告
- `health_latest.json` / `health_latest.txt` — 最新一份报告的快捷副本

## 六、快速切换清单

| 操作 | 命令 |
|------|------|
| 本地关闭沙盒 | `$env:YUNSHU_FEATURE_SANDBOX = 'false'` |
| 本地启用沙盒 | `$env:YUNSHU_FEATURE_SANDBOX = 'true'` |
| Docker 关闭沙盒 | `docker compose up -d`（默认） |
| Docker 启用沙盒 | `docker compose run -e YUNSHU_FEATURE_SANDBOX=true digital-life` |
| 验证沙盒状态 | `curl -X POST http://127.0.0.1:5678/api/sandbox/run -d '{"code":"1+1"}'` |
| 查看启动日志 | 服务器控制台搜索 `[沙盒]` |
| 批量接口检查 | `python health_check.py` |

## 七、异常情况处理

### 7.1 环境变量值异常

环境变量仅识别 `"true"`（不区分大小写），其他任何值均视为关闭：

| 环境变量值 | 沙盒状态 | 说明 |
|-----------|---------|------|
| `true` / `True` / `TRUE` | 启用 | 正常 |
| `false` / `False` / 未设置 | 关闭 | 正常 |
| `yes` / `1` / `on` | **关闭** | 不识别，视为 false |
| `ture`（拼写错误） | **关闭** | 拼写错误，视为 false |
| 空字符串 | **关闭** | 视为 false |

**排查方法**：查看启动日志中的实际值：
```
INFO:__main__:[沙盒] 功能状态: 已关闭 (YUNSHU_FEATURE_SANDBOX=yes)
```
如果值不是 `true`/`false`/未设置，说明设置有误。

### 7.2 沙盒代码执行超时

沙盒默认超时 5 秒，最大 30 秒。超时后返回：
```json
{"error": null, "stdout": "", "stderr": "", "timed_out": true}
```

**处理方式**：
- 请求时指定 `timeout` 参数（最大 30）：`{"code": "...", "timeout": 10}`
- 如果代码确实需要更长时间，考虑拆分为多个短任务
- 检查代码是否存在死循环

### 7.3 安全检查拦截

沙盒预检查会拦截以下危险模式：
- 类型属性遍历：`.__class__`、`.__bases__`、`.__subclasses__` 等
- 动态执行：`eval(`、`exec(`、`compile(`
- 文件操作：`open(`、`import `
- 反射函数：`getattr(`、`hasattr(`、`type(`

被拦截时返回 403：
```json
{"blocked": true, "safety": {"level": "critical", "matches": ["open("], "safe": false}}
```

**处理方式**：
- 检查代码是否确实需要这些操作
- 如果是误判，联系管理员评估是否调整安全规则
- 沙盒设计为受限执行环境，不支持文件/网络/反射操作

### 7.4 沙盒代码执行出错

代码在受限环境中执行，部分内置函数不可用（如 `print`、`input`、异常类）：
```json
{"error": "NameError: name 'print' is not defined", "stdout": "", "stderr": "", "timed_out": false}
```

**可用的安全内置函数**：
`abs`, `all`, `any`, `bool`, `chr`, `dict`, `enumerate`, `filter`, `float`, `int`, `len`, `list`, `map`, `max`, `min`, `ord`, `range`, `reversed`, `round`, `set`, `slice`, `sorted`, `str`, `sum`, `tuple`, `zip`

**不可用**：`print`、`input`、`open`、`type`、`eval`、`exec`、异常类、`import` 等

### 7.5 Docker 构建失败

**现象**：`docker build` 报错无法拉取基础镜像

```
ERROR: failed to solve: python:3.11-slim: failed to resolve source metadata
```

**处理方式**：
- 检查 Docker Desktop 是否运行
- 检查网络连接，确认能访问 Docker Hub
- 配置 Docker 镜像加速器（国内环境）
- 使用本地已有镜像：`docker images | grep python`

### 7.6 Docker 容器启动失败

**现象**：容器启动后立即退出

**排查步骤**：
```bash
# 查看容器日志
docker compose logs digital-life

# 查看退出码
docker compose ps -a

# 常见原因：
# - 端口冲突：修改 docker-compose.yml 中的端口映射
# - 目录权限：确保 logs/data/.backups 目录可写
# - 依赖缺失：检查 requirements.txt 是否完整安装
```

### 7.7 端口冲突

**现象**：启动时报 `Address already in use`

**处理方式**：
```powershell
# 查看占用端口的进程
netstat -ano | findstr :5678

# 结束占用进程（替换 PID）
taskkill /PID <PID> /F

# 或修改 app_server.py 中的端口号
```
