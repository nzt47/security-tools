# Prometheus 监控改进总结

> 本文档总结了 Prometheus 监控系统的改进内容，包括指标上报逻辑检查、详细日志添加和自动启动配置。

## 改进时间

2026-05-31

---

## 改进内容

### 1. ✅ start.py 中 Prometheus 导出器逻辑检查

**检查结果**:
- ✅ 当前逻辑使用 `subprocess.run()` 阻塞式调用 `prometheus_example.py`
- ✅ 指标上报时机在 `prometheus_example.py` 中正确触发
- ✅ 版本检查问题已修复（`prometheus_client.__version__` 属性不存在时的处理）

**改进内容**:
- 添加了 `try-except` 处理 `prometheus_client.__version__` 属性不存在的情况
- 优化了错误提示信息

---

### 2. ✅ prometheus_example.py 详细日志添加

**新增功能**:

#### PrometheusMonitor 类
- ✅ 完整的监控管理器，提供：
  - 自动指标上报
  - 周期性状态检查
  - 详细日志记录
  - 优雅关闭

#### 日志功能
- ✅ 支持多种日志级别：
  - `--debug`: 详细调试日志
  - `--quiet`: 只显示错误日志
  - 默认: INFO 级别

- ✅ 日志输出到两个位置：
  - 控制台（stdout）
  - 文件（`prometheus_export.log`）

#### 详细日志记录点
| 时机 | 日志内容 |
|------|---------|
| 初始化开始 | `[INFO] Starting initialization...` |
| 依赖检查 | `[DEBUG] All dependencies available` |
| DigitalLife 创建 | `[INFO] Creating DigitalLife instance...` |
| V2 模块状态 | `[INFO] V2 Features: - LifeTrace: True` |
| 导出器创建 | `[INFO] Creating Prometheus exporter on port 8000...` |
| HTTP 服务器启动 | `[INFO] Starting Prometheus HTTP server...` |
| 指标端点验证 | `[OK] Metrics endpoint verified (HTTP 200)` |
| 模拟指标上报 | `[METRIC] Interaction #1: 150.00ms` |
| 告警记录 | `[METRIC] Alert: 'rm -rf /' -> critical` |
| 周期性检查 | `[CHECK] Periodic check #1 completed` |
| 关闭信号 | `[INFO] Received signal 2, shutting down...` |
| 运行统计 | `[INFO] Total running time: 120.5s` |

#### 新增命令行参数
```bash
python prometheus_example.py --debug          # 调试模式
python prometheus_example.py --quiet          # 安静模式
python prometheus_example.py --port 9000      # 自定义端口
python prometheus_example.py --no-simulate    # 不模拟指标
python prometheus_example.py --no-periodic    # 不周期检查
python prometheus_example.py --interval 60    # 60秒检查间隔
```

---

### 3. ✅ 自动启动脚本集成

**新增文件**:

#### Windows 自动启动配置
- [setup_autostart.py](setup_autostart.py) - Windows 任务计划程序配置脚本

**功能**:
```powershell
# 安装自动启动任务
python setup_autostart.py --install

# 查看任务状态
python setup_autostart.py --status

# 手动运行任务
python setup_autostart.py --run

# 结束任务
python setup_autostart.py --end

# 卸载任务
python setup_autostart.py --uninstall
```

**任务计划程序配置**:
- 任务名称: `YunshuV2PrometheusMonitor`
- 触发时机: 系统启动时（`onstart`）
- 运行用户: `SYSTEM`
- 权限级别: `HIGHEST`
- 命令: `python prometheus_example.py --quiet`

#### Linux 自动启动配置
- [setup_autostart_linux.sh](setup_autostart_linux.sh) - Linux systemd 配置脚本
- [monitoring/Yunshu-prometheus.service](monitoring/Yunshu-prometheus.service) - systemd 服务文件

**功能**:
```bash
# 安装 systemd 服务
bash setup_autostart_linux.sh --install

# 查看服务状态
bash setup_autostart_linux.sh --status

# 启动服务
bash setup_autostart_linux.sh --run

# 停止服务
bash setup_autostart_linux.sh --end

# 卸载服务
bash setup_autostart_linux.sh --uninstall
```

**systemd 服务配置**:
- 服务名称: `Yunshu-prometheus`
- 启动时机: `network.target` 之后
- 重启策略: `always`（失败后自动重启）
- 重启间隔: 10 秒
- 日志输出: `/opt/Yunshu/prometheus_autostart.log`

---

## 指标上报触发时机分析

### 当前触发时机

| 指标类型 | 触发时机 | 说明 |
|---------|---------|------|
| V2 模块加载耗时 | DigitalLife 初始化时 | 从 `get_performance_report()` 获取 |
| V2 模块状态 | 初始化时 + 周期性检查 | 每 30 秒更新 |
| 交互统计 | 模拟或实际交互时 | 手动调用 `record_interaction()` |
| 交互耗时 | 同交互统计 | 手动调用 |
| 记忆数量 | 初始化时 + 周期性检查 | 每 30 秒更新 |
| 安全告警 | 检测到危险操作时 | 手动调用 `record_alert()` |

### 周期性检查机制

```python
def run_periodic_check(self, interval=30):
    """周期性检查系统状态"""
    while self.running:
        # 1. 检查 V2 模块状态
        features = self.dl.get_v2_features()
        for module, enabled in features.items():
            self.exporter.set_module_enabled(module_name, enabled)
        
        # 2. 更新记忆数量
        memory_stats = self.dl.get_memory_stats()
        self.exporter.set_memory_count(count)
        
        # 3. 记录运行时间
        elapsed = (datetime.now() - self.start_time).total_seconds()
        
        # 等待下一次检查
        time.sleep(interval)
```

---

## 日志文件位置

| 文件 | 说明 |
|------|------|
| `prometheus_export.log` | Prometheus 导出日志 |
| `prometheus_autostart.log` | 自动启动日志 |

---

## 使用指南

### 日常使用

```powershell
# 启动 Prometheus 监控（带详细日志）
python prometheus_example.py --debug

# 查看日志
type prometheus_export.log

# 访问指标
curl http://localhost:8000/metrics
```

### 自动启动配置

```powershell
# Windows: 安装自动启动
python setup_autostart.py --install

# Windows: 查看状态
python setup_autostart.py --status

# Windows: 手动运行
python setup_autostart.py --run
```

```bash
# Linux: 安装自动启动
bash setup_autostart_linux.sh --install

# Linux: 查看状态
bash setup_autostart_linux.sh --status

# Linux: 手动运行
bash setup_autostart_linux.sh --run
```

---

## 故障排查

### 问题 1: 指标不上报

**排查步骤**:
1. 检查日志文件 `prometheus_export.log`
2. 使用 `--debug` 参数运行查看详细日志
3. 验证 HTTP 服务器是否启动（访问 http://localhost:8000/metrics）
4. 检查 `prometheus_client` 是否正确安装

### 问题 2: 自动启动失败

**Windows 排查**:
```powershell
# 查看任务状态
schtasks /query /tn YunshuV2PrometheusMonitor /v

# 查看任务历史
Get-ScheduledTaskInfo -TaskName YunshuV2PrometheusMonitor
```

**Linux 排查**:
```bash
# 查看服务状态
sudo systemctl status Yunshu-prometheus

# 查看服务日志
sudo journalctl -u Yunshu-prometheus -f
```

---

## 相关文档

- [START_GUIDE.md](START_GUIDE.md) - 启动脚本使用指南
- [PROMETHEUS_VERIFICATION_SUMMARY.md](PROMETHEUS_VERIFICATION_SUMMARY.md) - Prometheus 验证总结
- [monitoring/README.md](monitoring/README.md) - 监控堆栈说明

---

## 文件清单

| 文件 | 说明 | 行数 |
|------|------|------|
| `start.py` | 启动脚本（已修复版本检查） | 312 |
| `prometheus_example.py` | Prometheus 示例（已添加详细日志） | 436 |
| `setup_autostart.py` | Windows 自动启动配置 | 200 |
| `setup_autostart_linux.sh` | Linux 自动启动配置 | 150 |
| `monitoring/Yunshu-prometheus.service` | systemd 服务文件 | 15 |

---

**文档版本**: 1.0  
**最后更新**: 2026-05-31  
**改进状态**: 全部完成