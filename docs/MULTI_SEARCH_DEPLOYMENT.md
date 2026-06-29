# multi-search-engine 正式环境部署指南

## 概述

本文档详细说明如何将 multi-search-engine MCP 服务部署到云枢正式环境。

## 1. 依赖安装

### 1.1 Python 版本要求

| 版本 | 状态 |
|------|------|
| Python 3.10 | 支持 |
| Python 3.11 | 推荐 |
| Python 3.12 | 支持 |

### 1.2 安装项目依赖

**方式一：使用 pip 安装（推荐）**

```bash
# 安装核心依赖
pip install -r requirements.txt

# 安装开发依赖（如需运行测试）
pip install -e ".[dev]"
```

**方式二：使用 pip-compile 更新依赖**

```bash
# 更新 requirements.txt（需要 pip-tools）
pip install pip-tools
pip-compile --output-file=requirements.txt pyproject.toml
```

### 1.3 MCP 服务依赖说明

multi-search-engine MCP 服务仅使用 Python 标准库，无需额外安装依赖：

| 依赖 | 来源 | 说明 |
|------|------|------|
| `asyncio` | Python标准库 | 异步IO支持 |
| `json` | Python标准库 | JSON处理 |
| `logging` | Python标准库 | 日志记录 |
| `subprocess` | Python标准库 | 子进程管理 |
| `time` | Python标准库 | 时间处理 |
| `os` | Python标准库 | 操作系统接口 |
| `typing` | Python标准库 | 类型提示 |
| `dataclasses` | Python标准库 | 数据类 |
| `pathlib` | Python标准库 | 文件路径处理 |
| `functools` | Python标准库 | 函数工具 |
| `datetime` | Python标准库 | 日期时间 |

### 1.4 可选依赖（官方MCP服务）

如果需要使用官方 MCP 服务（如 filesystem、github、brave-search）：

```bash
# 安装官方 MCP 服务
npx -y @modelcontextprotocol/server-filesystem ~
npx -y @modelcontextprotocol/server-github
npx -y @modelcontextprotocol/server-brave-search
```

## 2. 环境变量配置

### 2.1 必需环境变量

| 变量名 | 说明 | 默认值 | 正式环境推荐值 |
|--------|------|--------|--------------|
| `ENV` | 环境标识 | `development` | `production` |
| `MCP_LOG_LEVEL` | MCP服务日志级别 | `DEBUG` | `ERROR` |
| `MCP_TIMEOUT` | 请求超时时间（秒） | `60` | `30` |
| `MCP_MAX_RETRIES` | 最大重试次数 | `3` | `2` |

### 2.2 可选环境变量

| 变量名 | 说明 | 示例值 |
|--------|------|--------|
| `MCP_STARTUP_WAIT` | Windows启动等待时间（秒） | `2` |
| `MCP_BACKOFF_FACTOR` | 指数退避因子 | `2.0` |
| `MCP_READ_CHUNK_SIZE` | 读取缓冲区大小 | `4096` |

### 2.3 配置方式

**Linux/macOS**:
```bash
# 设置环境变量
export ENV=production
export MCP_LOG_LEVEL=ERROR
export MCP_TIMEOUT=30
export MCP_MAX_RETRIES=2

# 或者写入 ~/.bashrc 或 ~/.profile 使其永久生效
```

**Windows (PowerShell)**:
```powershell
$env:ENV="production"
$env:MCP_LOG_LEVEL="ERROR"
$env:MCP_TIMEOUT="30"
$env:MCP_MAX_RETRIES="2"
```

**Docker 环境**:
```dockerfile
ENV ENV=production
ENV MCP_LOG_LEVEL=ERROR
ENV MCP_TIMEOUT=30
ENV MCP_MAX_RETRIES=2
```

## 3. 配置文件修改

### 3.1 扩展配置 (`agent/data/extensions.json`)

确保 multi-search-engine 已注册：

```json
{
  "multi-search-engine": {
    "ext_id": "multi-search-engine",
    "ext_type": "mcp",
    "name": "多引擎搜索 MCP",
    "description": "Hermes/OpenClaw多引擎搜索技能，支持17个搜索引擎",
    "version": "1.0.0",
    "source": "mcp-template:multi-search-engine",
    "status": "enabled",
    "enabled": true,
    "config": {
      "command": "python",
      "args": ["mcp_services/multi_search_engine.py"],
      "protocol": "stdio",
      "timeout": 30,
      "max_retries": 2,
      "auto_start": true
    }
  }
}
```

### 3.2 内置扩展列表 (`agent/extensions/base.py`)

确认 multi-search-engine 在 `BUILTIN_EXTENSIONS["mcp"]` 中：

```python
{
    "id": "multi-search-engine",
    "name": "多引擎搜索 MCP",
    "description": "Hermes/OpenClaw多引擎搜索技能，支持17个搜索引擎",
    "protocol": "stdio",
    "command": "python",
    "args": ["mcp_services/multi_search_engine.py"],
    "builtin": True,
},
```

### 3.3 网络配置 (`agent/data/network_config.json`)

如果需要配置网络代理或API密钥：

```json
{
  "mcp_services": {
    "multi-search-engine": {
      "enabled": true,
      "timeout": 30,
      "proxy": null,
      "api_keys": {}
    }
  }
}
```

## 3. 部署步骤

### 3.1 前置检查

```bash
# 1. 检查 Python 版本
python --version  # 建议 3.8+

# 2. 检查依赖
pip install -r requirements.txt

# 3. 验证服务文件存在
ls -la mcp_services/
```

### 3.2 服务安装

通过扩展管理器安装：

```python
from agent.extensions.manager import ExtensionManager

mgr = ExtensionManager()

# 安装 multi-search-engine
result = mgr.install("mcp", "multi-search-engine")
print(result)
```

### 3.3 服务验证

```bash
# 1. 运行单元测试
python -m pytest tests/test_multi_search_engine.py -v

# 2. 运行集成测试
python mcp_services/test_mcp_integration.py

# 3. 手动验证
python -c "
import asyncio
from mcp_services.yunshu_mcp_bridge import YunshuMCPBridge

async def test():
    bridge = YunshuMCPBridge()
    await bridge.install_service('multi-search-engine')
    result = await bridge.call_tool('multi-search-engine', 'search', {'query': 'test', 'engines': ['baidu']})
    print(result)
    await bridge.stop_service('multi-search-engine')

asyncio.run(test())
"
```

### 3.4 启动脚本示例

**Linux/macOS**: `start_mcp.sh`

```bash
#!/bin/bash
export ENV=production
export MCP_LOG_LEVEL=ERROR

cd /path/to/agent
python -c "
import asyncio
from mcp_services.yunshu_mcp_bridge import YunshuMCPBridge

async def main():
    bridge = YunshuMCPBridge()
    await bridge.install_service('multi-search-engine')
    print('Multi-search-engine MCP service started')

asyncio.run(main())
"
```

**Windows**: `start_mcp.ps1`

```powershell
$env:ENV="production"
$env:MCP_LOG_LEVEL="ERROR"

cd C:\path\to\agent
python -c @"
import asyncio
from mcp_services.yunshu_mcp_bridge import YunshuMCPBridge

async def main():
    bridge = YunshuMCPBridge()
    await bridge.install_service('multi-search-engine')
    print('Multi-search-engine MCP service started')

asyncio.run(main())
"@
```

## 4. 生产环境优化建议

### 4.1 资源限制

```bash
# 设置进程资源限制（Linux）
ulimit -n 1024  # 文件描述符
ulimit -u 100   # 最大进程数
```

### 4.2 日志管理

```bash
# 日志轮换配置（使用 logrotate）
# /etc/logrotate.d/mcp-services

/path/to/agent/logs/mcp*.log {
    daily
    rotate 7
    compress
    missingok
    notifempty
    create 644 root root
}
```

### 4.3 监控告警

建议配置以下监控指标：
- 服务启动状态
- 工具调用成功率
- 响应时间
- 错误率

## 5. 安全性检查

### 5.1 权限检查

```bash
# 检查文件权限
ls -la mcp_services/

# 确保配置文件权限正确
chmod 600 agent/data/extensions.json
chmod 600 agent/data/network_config.json
```

### 5.2 安全配置

1. **禁止以 root 用户运行**
2. **限制网络访问**（仅允许内网访问）
3. **配置防火墙规则**
4. **定期更新依赖**

## 6. 故障排查

### 6.1 常见问题

| 问题 | 原因 | 解决方案 |
|------|------|----------|
| 服务启动失败 | 命令路径错误 | 检查 `command` 和 `args` 配置 |
| 超时错误 | 网络问题或服务未启动 | 增加超时时间，检查服务状态 |
| 编码错误 | Windows 终端编码问题 | 确保使用 UTF-8 编码 |
| 权限错误 | 文件权限不足 | 设置正确的文件权限 |

### 6.2 日志排查

```bash
# 查看 MCP 客户端日志
grep "mcp_client" logs/*.log

# 查看桥接器日志
grep "MCP桥接" logs/*.log

# 查看错误日志
grep "ERROR" logs/*.log
```

### 6.3 服务状态检查

```python
from mcp_services.yunshu_mcp_bridge import YunshuMCPBridge

bridge = YunshuMCPBridge()

# 列出所有服务
services = bridge.list_services()
print(services)

# 健康检查
health = bridge.health_check()
print(health)
```

## 7. 回滚方案

### 7.1 回滚步骤

1. **停止服务**：
   ```python
   await bridge.stop_service("multi-search-engine")
   ```

2. **恢复配置**：
   ```bash
   cp agent/data/extensions.json.bak agent/data/extensions.json
   ```

3. **重新启动**：
   ```python
   await bridge.install_service("multi-search-engine")
   ```

### 7.2 备份建议

定期备份以下文件：
- `agent/data/extensions.json`
- `agent/data/network_config.json`
- `mcp_services/multi_search_engine.py`

## 8. 部署清单

- [ ] 设置环境变量 (`ENV=production`)
- [ ] 配置日志级别 (`MCP_LOG_LEVEL=ERROR`)
- [ ] 更新超时配置 (`MCP_TIMEOUT=30`)
- [ ] 验证扩展注册
- [ ] 运行单元测试
- [ ] 运行集成测试
- [ ] 设置日志轮换
- [ ] 配置监控告警
- [ ] 检查文件权限
- [ ] 备份配置文件

## 附录：环境变量完整列表

```bash
# 生产环境推荐配置
export ENV=production
export MCP_LOG_LEVEL=ERROR
export MCP_TIMEOUT=30
export MCP_MAX_RETRIES=2
export MCP_STARTUP_WAIT=2
export MCP_BACKOFF_FACTOR=2.0
export MCP_READ_CHUNK_SIZE=4096
```