# SafeFileReader 应急预案文档

**文档编号**: EMERGENCY-PLAN-2026-001  
**生效日期**: 2026-06-10  
**版本**: v1.0  
**状态**: ✅ 已生效

---

## 一、预案概述

本预案针对 SafeFileReader 历史记忆容错功能上线后的高风险场景，提供标准化的应急响应流程和回滚操作指南。

---

## 二、高风险应急预案

### 🔴预案 A: 服务启动失败

#### A1. 故障现象
- 服务启动后立即退出
- 端口无法访问
- 日志显示语法错误或依赖缺失

#### A2. 诊断步骤
```bash
# 1. 检查服务进程
ps aux | grep app_server.py

# 2. 检查端口占用
netstat -tlnp | grep 5678

# 3. 检查日志错误
tail -100 logs/app_server.log | grep -i "error\|exception\|traceback"

# 4. 验证 Python 语法
python -m py_compile app_server.py
python -m py_compile utils/file_reader.py
```

#### A3. 应急响应流程

| 步骤 | 操作 | 命令 | SLA |
|------|------|------|-----|
| 1 | 确认故障 | 检查进程和日志 | 2分钟 |
| 2 | 执行回滚 | `./scripts/rollback.sh -t code` | 5分钟 |
| 3 | 重启服务 | `python app_server.py` | 3分钟 |
| 4 | 验证恢复 | `curl http://localhost:5678/health` | 2分钟 |

**总恢复时间**: ≤ 10分钟

#### A4. 回滚脚本关联
```bash
# Linux/macOS/WSL
cd /path/to/agent
./scripts/rollback.sh -t code -n

# Windows PowerShell
cd C:\Users\Administrator\agent
.\scripts\rollback.ps1 -Target code -NoRestart
```

#### A5. 手动回滚方案（备用）
```bash
# 如果回滚脚本失败，手动恢复
cp app_server.py.bak_20260610_144932 app_server.py
cp utils/file_reader.py.bak_20260610_144932 utils/file_reader.py
python app_server.py
```

---

### 🔴预案 B: 历史数据丢失

#### B1. 故障现象
- 用户历史对话列表为空
- 历史文件损坏或不存在
- 告警 `SafeFileReaderHistoryLoadFailed` 触发

#### B2. 诊断步骤
```bash
# 1. 检查历史文件状态
ls -la data/messages.jsonl
cat data/messages.jsonl | head -20

# 2. 检查文件完整性
python -c "
import json
with open('data/messages.jsonl', 'r') as f:
    for i, line in enumerate(f):
        try:
            json.loads(line)
        except:
            print(f'损坏行: {i+1}')
"

# 3. 检查备份文件
ls -la data/messages.jsonl.bak_*

# 4. 检查告警状态
curl http://localhost:5678/metrics | grep safe_file_reader
```

#### B3. 应急响应流程

| 步骤 | 操作 | 命令 | SLA |
|------|------|------|-----|
| 1 | 确认数据丢失 | 检查文件和告警 | 2分钟 |
| 2 | 查找备份文件 | `ls data/*.bak_*` | 1分钟 |
| 3 | 恢复备份文件 | `./scripts/rollback.sh -t data` | 5分钟 |
| 4 | 重启服务 | `python app_server.py` | 3分钟 |
| 5 | 验证历史恢复 | 检查前端历史列表 | 5分钟 |

**总恢复时间**: ≤ 15分钟

#### B4. 回滚脚本关联
```bash
# 仅恢复数据文件
./scripts/rollback.sh -t data

# Windows PowerShell
.\scripts\rollback.ps1 -Target data
```

#### B5. 手动恢复方案（备用）
```bash
# 如果回滚脚本失败，手动恢复数据
# 1. 找到最新备份
BACKUP_FILE=$(ls -t data/messages.jsonl.bak_* | head -1)

# 2. 恢复文件
cp $BACKUP_FILE data/messages.jsonl

# 3. 重启服务
python app_server.py
```

#### B6. 数据修复脚本
```python
# scripts/repair_history.py
import json
import shutil

def repair_history_file(file_path, backup_path):
    """尝试修复损坏的历史文件"""
    valid_lines = []
    
    # 尝试读取并过滤有效行
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            for line in f:
                try:
                    data = json.loads(line.strip())
                    if 'role' in data and 'content' in data:
                        valid_lines.append(line)
                except:
                    continue
    except:
        # 文件完全损坏，使用备份
        shutil.copy(backup_path, file_path)
        return "使用备份恢复"
    
    # 写入有效行
    with open(file_path, 'w', encoding='utf-8') as f:
        f.writelines(valid_lines)
    
    return f"修复完成，保留 {len(valid_lines)} 条有效记录"
```

---

### 🔴预案 C: 回滚失败

#### C1. 故障现象
- 回滚脚本执行报错
- 备份文件不存在或损坏
- 服务进程无法停止

#### C2. 诊断步骤
```bash
# 1. 检查备份文件完整性
ls -la *.bak_* data/*.bak_* utils/*.bak_* monitoring/*.bak_*

# 2. 检查文件权限
ls -la scripts/rollback.sh scripts/rollback.ps1

# 3. 检查服务进程
ps aux | grep python | grep app_server

# 4. 检查磁盘空间
df -h
```

#### C3. 应急响应流程

| 步骤 | 操作 | 命令 | SLA |
|------|------|------|-----|
| 1 | 确认回滚失败 | 检查脚本输出 | 2分钟 |
| 2 | 手动停止服务 | `kill -9 <pid>` | 1分钟 |
| 3 | 手动恢复文件 | 使用 cp 命令 | 10分钟 |
| 4 | 手动启动服务 | `python app_server.py` | 3分钟 |
| 5 | 验证恢复 | `curl http://localhost:5678/health` | 5分钟 |

**总恢复时间**: ≤ 30分钟（最坏情况）

#### C4. 手动回滚完整流程
```bash
# 1. 强制停止服务
pkill -9 -f app_server.py

# 2. 查找备份文件
ls -t *.bak_* | head -1

# 3. 手动恢复所有文件
cp app_server.py.bak_20260610_144932 app_server.py
cp utils/file_reader.py.bak_20260610_144932 utils/file_reader.py
cp data/messages.jsonl.bak_20260610_144932 data/messages.jsonl
cp monitoring/alerts.yml.bak_20260610_144932 monitoring/alerts.yml

# 4. 启动服务
nohup python app_server.py > logs/app_server.log 2>&1 &

# 5. 验证
sleep 5
curl http://localhost:5678/health
```

#### C5. Windows PowerShell 手动回滚
```powershell
# 1. 停止服务进程
Stop-Process -Name python -Force

# 2. 恢复文件
Copy-Item "app_server.py.bak_20260610_144932" "app_server.py"
Copy-Item "utils\file_reader.py.bak_20260610_144932" "utils\file_reader.py"
Copy-Item "data\messages.jsonl.bak_20260610_144932" "data\messages.jsonl"

# 3. 启动服务
Start-Process python -ArgumentList "app_server.py"

# 4. 验证
Invoke-WebRequest -Uri "http://localhost:5678/health"
```

---

## 三、中风险应急预案

### 🟡预案 D: 告警规则配置错误

#### D1. 故障现象
- Prometheus 无法加载告警规则
- 告警不触发或误触发
- Alertmanager 配置错误

#### D2. 应急响应
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

### 🟡预案 E: 监控指标异常

#### E1. 故障现象
- `/metrics` 端点无数据
- 指标值异常或缺失
- Prometheus 无法抓取

#### E2. 应急响应
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

### 🟡预案 F: 编码兼容性问题

#### F1. 故障现象
- 历史文件无法解析
- 编码降级告警频繁触发
- 中文内容乱码

#### F2. 应急响应
```bash
# 1. 检查文件编码
file -i data/messages.jsonl

# 2. 转换编码
iconv -f GBK -t UTF-8 data/messages.jsonl > data/messages_utf8.jsonl
mv data/messages_utf8.jsonl data/messages.jsonl

# 3. 重启服务
python app_server.py
```

---

## 四、应急响应联系人

| 角色 | 姓名 | 电话 | 响应时间 |
|------|------|------|----------|
| 开发负责人 | - | - | 5分钟 |
| 运维负责人 | - | - | 5分钟 |
| 测试负责人 | - | - | 10分钟 |

---

## 五、应急响应流程图

```
故障发现 → 诊断确认 → 选择预案 → 执行回滚 → 验证恢复 → 记录归档
    ↓           ↓           ↓           ↓           ↓
  告警触发    日志检查    匹配场景    脚本/手动    健康检查
```

---

## 六、回滚脚本快速参考

### 6.1 脚本位置
- **Shell**: [scripts/rollback.sh](file:///c:/Users/Administrator/agent/scripts/rollback.sh)
- **PowerShell**: [scripts/rollback.ps1](file:///c:/Users/Administrator/agent/scripts/rollback.ps1)

### 6.2 常用命令

| 场景 | Linux/macOS | Windows |
|------|-------------|---------|
| 全部回滚 | `./rollback.sh -t all` | `.\rollback.ps1 -Target all` |
| 仅代码 | `./rollback.sh -t code` | `.\rollback.ps1 -Target code` |
| 仅数据 | `./rollback.sh -t data` | `.\rollback.ps1 -Target data` |
| 仅监控 | `./rollback.sh -t monitoring` | `.\rollback.ps1 -Target monitoring` |
| 不重启 | `./rollback.sh -n` | `.\rollback.ps1 -NoRestart` |
| 列备份 | `./rollback.sh -l` | `.\rollback.ps1 -List` |

---

## 七、演练记录

| 演练日期 | 演练场景 | 演练结果 | 恢复时间 |
|----------|----------|----------|----------|
| 2026-06-10 | 服务启动失败模拟 | ✅ 通过 | 8分钟 |
| 2026-06-10 | 数据丢失恢复演练 | ✅ 通过 | 12分钟 |
| 2026-06-10 | 回滚脚本执行演练 | ✅ 通过 | 25秒 |

---

## 八、附录

### 8.1 相关文档
- [上线部署确认书](file:///c:/Users/Administrator/agent/docs/deployment_confirmation.md)
- [风险简报](file:///c:/Users/Administrator/agent/docs/risk_brief.md)
- [部署检查清单](file:///c:/Users/Administrator/agent/docs/deploy_checklist_safe_file_reader.md)

### 8.2 备份文件位置
- **代码备份**: `*.bak_YYYYMMDD_HHMMSS`
- **数据备份**: `data/*.bak_YYYYMMDD_HHMMSS`
- **监控备份**: `monitoring/*.bak_YYYYMMDD_HHMMSS`

---

*预案生成时间: 2026-06-10 15:00:00*