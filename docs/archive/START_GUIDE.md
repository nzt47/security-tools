# 云枢 V2 启动脚本说明

> 本文档介绍项目的统一启动脚本及其各种功能选项。

## 快速开始

### Windows 系统

```powershell
# 显示帮助信息
python start.py --help

# Prometheus 指标导出（推荐用于日常监控）
python start.py -p
python start.py --prometheus

# 诊断模式
python start.py -d
python start.py --diagnose

# 运行测试
python start.py -t
python start.py --test

# 完整监控堆栈（Prometheus + Grafana，需要 Docker）
python start.py -s
python start.py --stack

# 普通模式（无监控）
python start.py -n
python start.py --normal

# 完整流程：诊断 -> 测试 -> Prometheus 指标导出
python start.py -a
python start.py --all
```

### Linux/Mac 系统

```bash
# 显示帮助信息
python3 start.py --help

# Prometheus 指标导出（推荐用于日常监控）
python3 start.py -p

# 诊断模式
python3 start.py -d

# 运行测试
python3 start.py -t

# 完整监控堆栈
python3 start.py -s

# 普通模式
python3 start.py -n

# 完整流程
python3 start.py -a
```

---

## 功能选项详解

### 1. Prometheus 指标导出模式 (-p, --prometheus)

**用途**: 启动云枢 V2 并导出 Prometheus 指标，便于日常监控和性能分析。

**特点**:
- ✅ 自动检查 prometheus_client 依赖
- ✅ 在 http://localhost:8000/metrics 导出指标
- ✅ 包含 V2 模块状态、性能数据、安全告警等
- ✅ 无需 Docker，直接运行

**访问地址**:
- 指标导出: http://localhost:8000/metrics
- 监控页面: http://localhost:8000/

**输出的指标示例**:
```
Yunshu_v2_module_enabled{module="lifetrace"} 1
Yunshu_v2_module_enabled{module="persona"} 1
Yunshu_interaction_total 15
Yunshu_alert_total{level="critical"} 0
Yunshu_memory_count 42
```

---

### 2. 完整监控堆栈模式 (-s, --stack)

**用途**: 启动完整的监控堆栈，包括 Prometheus 和 Grafana，提供可视化仪表盘。

**依赖**:
- Docker Desktop（Windows）或 Docker Engine（Linux/Mac）
- prometheus_client 库

**特点**:
- ✅ 自动检测操作系统（Windows/Linux/Mac）
- ✅ 自动选择合适的启动脚本
- ✅ 包含预配置的 Grafana 仪表盘
- ✅ 包含 Prometheus 告警规则

**服务访问地址**:
- Grafana: http://localhost:3000 (默认用户 admin / admin)
- Prometheus: http://localhost:9090
- 云枢指标: http://localhost:8000/metrics

---

### 3. 诊断模式 (-d, --diagnose)

**用途**: 快速诊断 V2 模块的状态和性能，无需启动完整服务。

**特点**:
- ✅ 检查 V2 模块依赖是否完整
- ✅ 验证模块加载状态
- ✅ 显示性能数据
- ✅ 生成诊断报告

---

### 4. 测试模式 (-t, --test)

**用途**: 运行完整的测试套件，确保系统功能正常。

**特点**:
- ✅ Memory 模块单元测试
- ✅ PermissionSystem 安全测试
- ✅ V2 功能开关测试
- ✅ LifeTrace & Persona 集成测试

---

### 5. 普通模式 (-n, --normal)

**用途**: 启动云枢 V2 但不启动任何监控功能，适合日常使用。

**特点**:
- ✅ 轻量级启动，无额外开销
- ✅ 所有 V2 功能正常可用
- ✅ 可通过代码获取性能报告

---

### 6. 完整流程模式 (-a, --all)

**用途**: 依次执行诊断、测试和 Prometheus 指标导出，适合部署前验证。

**执行顺序**:
1. 诊断模式
2. 测试模式
3. Prometheus 指标导出模式

---

## 指标说明

### 云枢 V2 导出的 Prometheus 指标

| 指标名称 | 类型 | 说明 |
|---------|------|------|
| Yunshu_v2_module_load_duration_seconds | Histogram | V2 模块加载耗时分布 |
| Yunshu_v2_module_load_total | Counter | V2 模块加载次数（含状态） |
| Yunshu_v2_module_enabled | Gauge | V2 模块启用状态（1=启用，0=禁用） |
| Yunshu_interaction_total | Counter | 交互总次数 |
| Yunshu_interaction_duration_seconds | Histogram | 交互处理耗时分布 |
| Yunshu_memory_count | Gauge | 当前记忆数量 |
| Yunshu_alert_total | Counter | 安全告警总数（按级别） |

---

## 依赖安装

### 基础依赖（必需）

```bash
# 安装项目基础依赖
pip install -r requirements.txt
```

### Prometheus 监控依赖（可选）

```bash
# 安装 Prometheus Client 库
pip install prometheus_client
```

### 监控堆栈依赖（可选）

- Docker Desktop（Windows）
- Docker Engine + Docker Compose（Linux/Mac）

---

## 推荐使用场景

### 场景 1: 日常开发和调试

```bash
# 使用 Prometheus 指标导出模式，便于性能监控
python start.py -p
```

### 场景 2: 部署前验证

```bash
# 使用完整流程模式，确保系统正常
python start.py -a
```

### 场景 3: 性能分析和问题排查

```bash
# 启动完整监控堆栈，使用 Grafana 仪表盘分析
python start.py -s
```

### 场景 4: 快速测试系统状态

```bash
# 使用诊断模式，快速检查 V2 模块
python start.py -d
```

---

## 故障排查

### 问题: Prometheus Client 未安装

**症状**:
```
提示: 请先安装 prometheus_client 库
  pip install prometheus_client
```

**解决**:
```bash
pip install prometheus_client
```

### 问题: Docker 未安装

**症状**:
```
Docker 未安装
提示: 请先安装 Docker Desktop
```

**解决**:
- Windows: 下载并安装 Docker Desktop
- Linux: 安装 Docker Engine 和 Docker Compose
- Mac: 下载并安装 Docker Desktop for Mac

### 问题: 端口被占用

**症状**:
```
OSError: [Errno 48] Address already in use
```

**解决**:
1. 检查是否有其他进程占用 8000 端口
2. 关闭占用端口的进程
3. 或者修改 prometheus_example.py 中的端口号

---

## 相关文档

- [V2_VERIFICATION_SUMMARY.md](V2_VERIFICATION_SUMMARY.md) - V2 功能验证总结
- [monitoring/README.md](monitoring/README.md) - 监控堆栈详细说明
- [monitoring/GRAFANA_SETUP_GUIDE.md](monitoring/GRAFANA_SETUP_GUIDE.md) - Grafana 配置指南
- [monitoring/GRAFANA_EMAIL_ALERT_GUIDE.md](monitoring/GRAFANA_EMAIL_ALERT_GUIDE.md) - Email 告警配置

---

## 示例

### 示例 1: 日常监控

```powershell
# 终端 1: 启动 Prometheus 指标导出
python start.py -p

# 浏览器访问: http://localhost:8000/metrics 查看指标
```

### 示例 2: 完整部署检查

```powershell
# 运行完整流程验证
python start.py -a

# 检查诊断和测试是否全部通过
```

### 示例 3: 生产环境监控

```bash
# 1. 安装依赖
pip install prometheus_client

# 2. 启动监控堆栈（可选）
python start.py -s

# 3. 启动指标导出（必须）
python start.py -p
```

---

**文档版本**: 1.0  
**最后更新**: 2026-05-31  
**维护者**: 云枢开发团队
