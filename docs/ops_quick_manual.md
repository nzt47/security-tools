
# SafeFileReader 运维快速操作手册

**文档编号**: OPS-MANUAL-2026-001  
**版本**: v1.0  
**更新日期**: 2026-06-11  

---

## 📋 目录

1. [快速诊断表](#-快速诊断表)
2. [常用命令速查](#-常用命令速查)
3. [高风险故障处理](#-高风险故障处理)
4. [中风险故障处理](#-中风险故障处理)
5. [回滚命令汇总](#-回滚命令汇总)
6. [服务状态检查](#-服务状态检查)

---

## 🔍 快速诊断表

| 故障现象 | 诊断步骤 | 快速命令 |
|----------|----------|----------|
| 服务无法启动 | 检查进程、端口、日志、语法 | `ps aux\|grep app_server; netstat -tlnp\|grep 5678; tail -50 logs/app_server.log` |
| 历史对话丢失 | 检查文件状态、完整性、备份 | `ls -la data/messages.jsonl; cat data/messages.jsonl\|head -20` |
| 告警不触发 | 检查规则配置、YAML语法 | `python -c "import yaml; yaml.safe_load(open('monitoring/alerts.yml'))"` |
| 指标无数据 | 检查指标端点、服务日志 | `curl http://localhost:5678/metrics\|grep safe_file_reader` |
| 中文乱码 | 检查文件编码 | `file -i data/messages.jsonl` |

---

## ⚡ 常用命令速查

### 服务管理
```bash
# 检查服务进程
ps aux | grep app_server.py

# 检查端口占用
netstat -tlnp | grep 5678

# 启动服务（后台）
nohup python app_server.py > logs/app_server.log 2>&1 &

# 停止服务
kill -9 $(ps aux | grep app_server.py | grep -v grep | awk '{print $2}')

# 强制停止
pkill -9 -f app_server.py

# 健康检查
curl http://localhost:5678/health
```

### 文件检查
```bash
# 检查历史文件
ls -la data/messages.jsonl

# 查看文件内容
cat data/messages.jsonl | head -20

# 检查文件编码
file -i data/messages.jsonl

# 查找备份文件
ls -t *.bak_* data/*.bak_* | head -5
```

### 日志检查
```bash
# 查看服务日志（最近100行）
tail -100 logs/app_server.log

# 搜索错误日志
tail -100 logs/app_server.log | grep -i "error\|exception\|traceback"

# 搜索历史加载日志
tail -100 logs/app_server.log | grep -i "历史加载\|SafeFileReader"
```

### 指标检查
```bash
# 检查指标端点
curl http://localhost:5678/metrics | grep safe_file_reader

# 检查特定指标
curl http://localhost:5678/metrics | grep loaded_history_count
curl http://localhost:5678/metrics | grep invalid_ratio
```

### 语法验证
```bash
# Python 语法检查
python -m py_compile app_server.py
python -m py_compile utils/file_reader.py

# YAML 语法检查
python -c "import yaml; yaml.safe_load(open('monitoring/alerts.yml'))"
```

---

## 🔴 高风险故障处理

### 故障A: 服务启动失败

**现象**: 服务启动后立即退出、端口无法访问、日志显示语法错误

**诊断命令**:
```bash
# 1. 检查进程
ps aux | grep app_server.py

# 2. 检查端口
netstat -tlnp | grep 5678

# 3. 检查日志
tail -100 logs/app_server.log | grep -i "error\|exception\|traceback"

# 4. 语法检查
python -m py_compile app_server.py
python -m py_compile utils/file_reader.py
```

**处理命令**:
```bash
# 方案1: 执行回滚
./scripts/rollback.sh -t code

# 方案2: 手动恢复
cp app_server.py.bak_* app_server.py
cp utils/file_reader.py.bak_* utils/file_reader.py

# 重启服务
python app_server.py

# 验证
curl http://localhost:5678/health
```

---

### 故障B: 历史数据丢失

**现象**: 用户历史对话为空、文件损坏、告警 `SafeFileReaderHistoryLoadFailed` 触发

**诊断命令**:
```bash
# 1. 检查文件状态
ls -la data/messages.jsonl
cat data/messages.jsonl | head -20

# 2. 检查完整性（查找损坏行）
python -c "
import json
with open('data/messages.jsonl', 'r') as f:
    for i, line in enumerate(f):
        try:
            json.loads(line)
        except:
            print(f'损坏行: {i+1}')
"

# 3. 检查备份
ls -la data/messages.jsonl.bak_*
```

**处理命令**:
```bash
# 方案1: 回滚恢复
./scripts/rollback.sh -t data

# 方案2: 手动恢复
BACKUP_FILE=$(ls -t data/messages.jsonl.bak_* | head -1)
cp $BACKUP_FILE data/messages.jsonl

# 重启服务
python app_server.py

# 验证
curl http://localhost:5678/metrics | grep loaded_history_count
```

---

### 故障C: 回滚失败

**现象**: 回滚脚本报错、备份文件不存在、服务无法停止

**诊断命令**:
```bash
# 1. 检查备份文件
ls -la *.bak_* data/*.bak_* utils/*.bak_* monitoring/*.bak_*

# 2. 检查脚本权限
ls -la scripts/rollback.sh scripts/rollback.ps1

# 3. 检查进程
ps aux | grep python | grep app_server

# 4. 检查磁盘空间
df -h
```

**处理命令**:
```bash
# 1. 强制停止服务
pkill -9 -f app_server.py

# 2. 手动恢复所有文件
cp app_server.py.bak_* app_server.py
cp utils/file_reader.py.bak_* utils/file_reader.py
cp data/messages.jsonl.bak_* data/messages.jsonl
cp monitoring/alerts.yml.bak_* monitoring/alerts.yml

# 3. 启动服务
nohup python app_server.py > logs/app_server.log 2>&1 &

# 4. 验证
sleep 5
curl http://localhost:5678/health
```

---

## 🟡 中风险故障处理

### 故障D: 告警规则配置错误

**现象**: Prometheus 无法加载规则、告警不触发或误触发

**处理命令**:
```bash
# 1. 验证 YAML 语法
python -c "import yaml; yaml.safe_load(open('monitoring/alerts.yml'))"

# 2. 检查规则名称
grep -E "alert: SafeFileReader" monitoring/alerts.yml

# 3. 恢复告警规则
./scripts/rollback.sh -t monitoring

# 4. 重启 Prometheus
systemctl restart prometheus
```

---

### 故障E: 监控指标异常

**现象**: `/metrics` 端点无数据、指标值异常

**处理命令**:
```bash
# 1. 检查指标端点
curl http://localhost:5678/metrics | grep safe_file_reader

# 2. 检查服务日志
tail -50 logs/app_server.log | grep -i "prometheus\|metric"

# 3. 检查指标注册
python -c "
from prometheus_client import REGISTRY
for metric in REGISTRY._metrics_to_collect:
    print(metric)
"
```

---

### 故障F: 编码兼容性问题

**现象**: 历史文件无法解析、编码降级告警频繁、中文乱码

**处理命令**:
```bash
# 1. 检查文件编码
file -i data/messages.jsonl

# 2. 转换编码（GBK → UTF-8）
iconv -f GBK -t UTF-8 data/messages.jsonl > data/messages_utf8.jsonl
mv data/messages_utf8.jsonl data/messages.jsonl

# 3. 重启服务
python app_server.py
```

---

## 📌 回滚命令汇总

### Linux/macOS/WSL
| 操作 | 命令 |
|------|------|
| 全部回滚 | `./scripts/rollback.sh -t all` |
| 仅代码回滚 | `./scripts/rollback.sh -t code` |
| 仅数据回滚 | `./scripts/rollback.sh -t data` |
| 仅监控回滚 | `./scripts/rollback.sh -t monitoring` |
| 回滚不重启 | `./scripts/rollback.sh -t all -n` |
| 列出备份 | `./scripts/rollback.sh -l` |

### Windows PowerShell
| 操作 | 命令 |
|------|------|
| 全部回滚 | `.\scripts\rollback.ps1 -Target all` |
| 仅代码回滚 | `.\scripts\rollback.ps1 -Target code` |
| 仅数据回滚 | `.\scripts\rollback.ps1 -Target data` |
| 仅监控回滚 | `.\scripts\rollback.ps1 -Target monitoring` |
| 回滚不重启 | `.\scripts\rollback.ps1 -Target all -NoRestart` |
| 列出备份 | `.\scripts\rollback.ps1 -List` |

---

## 📊 服务状态检查脚本

```bash
#!/bin/bash
# 服务状态快速检查脚本

echo "=== SafeFileReader 服务状态检查 ==="
echo ""

echo "1. 服务进程:"
ps aux | grep app_server.py | grep -v grep || echo "❌ 服务未运行"

echo ""
echo "2. 端口监听:"
netstat -tlnp | grep 5678 || echo "❌ 端口未监听"

echo ""
echo "3. 健康检查:"
curl -s http://localhost:5678/health || echo "❌ 健康检查失败"

echo ""
echo "4. 历史加载指标:"
curl -s http://localhost:5678/metrics | grep loaded_history_count

echo ""
echo "5. 无效行比例:"
curl -s http://localhost:5678/metrics | grep invalid_ratio

echo ""
echo "=== 检查完成 ==="
```

---

## 📞 应急响应联系人

| 角色 | 响应时间 |
|------|----------|
| 开发负责人 | 5分钟 |
| 运维负责人 | 5分钟 |
| 测试负责人 | 10分钟 |

---

**文档位置**: `docs/ops_quick_manual.md`  
**相关文档**: [应急预案](file:///c:/Users/Administrator/agent/docs/emergency_plan.md) | [部署确认书](file:///c:/Users/Administrator/agent/docs/deployment_confirmation.md)

---

*手册生成时间: 2026-06-11*
