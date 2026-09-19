
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

## 🧹 附录 A：根目录一次性运维脚本的引用关系（TASK-03 · 2026-09-18 实测）

> **用途**：根目录有 25 个受控 `.ps1` / `.sh` 一次性脚本。TASK-03 曾建议把它们
> `git mv` 到 `scripts/legacy/` 归类。**实测发现 23 个有引用（含 2 个 CI workflow），
> 直接移动会打断 20+ 条文档链接与 CI 脚本路径** —— 因此本次**未移动**，
> 而是把引用关系记录在此，供后续任务"先改引用、再移动"。
>
> 完整判定见 `docs/closeout/REPO_HYGIENE_20260918.md` §5.1。
> 复现引用检查：`git grep -n --fixed-strings <文件名>`

### A.1 ⛔ 被 CI / 其它脚本直接调用（**移动会直接打断自动化**）

| 脚本 | 引用方 | 风险 |
|---|---|---|
| `check.ps1` | `.github/workflows/ci.yml`、`.github/workflows/skills-check.yml`、`Modules/AdminDependencyChecker/AdminDependencyChecker.psd1` | **CI 强依赖**，改动即红 |
| `recover_docker.ps1` | `complete_verification.ps1` | 脚本间调用链断裂 |
| `simple_verify.ps1` | `complete_rebuild.ps1`、`complete_rebuild_simple.ps1` | 同上 |
| `setup-kubeconfig.ps1` | `demo-kubeconfig-fix.ps1`、`docs/archive/KUBECONFIG_*.md` | 同上 |
| `test-kubeconfig.ps1` | `demo-kubeconfig-fix.ps1`、`docs/archive/KUBECONFIG_*.md` | 同上 |

### A.2 📄 仅被文档引用（**移动前须同步改文档**）

| 脚本 | 引用文档（部分） |
|---|---|
| `api_verification.ps1` | `docs/archive/VERIFICATION_REPORT.md` |
| `complete_rebuild.ps1` | `docs/archive/FINAL_EXECUTION_REPORT_V2.md` |
| `complete_rebuild_simple.ps1` | `docs/archive/FINAL_VERIFICATION_REPORT.md` |
| `complete_verification.ps1` | `docs/archive/VERIFICATION_REPORT.md` |
| `configure_alert_rules.ps1` | `docs/archive/EXECUTION_SUMMARY_REPORT.md` |
| `configure_docker_mirror.ps1` | `docs/archive/QUICK_START_GUIDE.md`、`docs/archive/offline_image_import_guide.md` |
| `copy_config_and_restart.ps1` | `docs/archive/FINAL_EXECUTION_REPORT_V2.md`、`docs/archive/FINAL_VERIFICATION_REPORT.md` |
| `demo-kubeconfig-fix.ps1` | `docs/archive/KUBECONFIG_README.md`、`docs/archive/KUBECONFIG_SUMMARY.md` |
| `deploy.ps1` | `docs/IO_TIMEOUT_TEST_HANG_ROOTCAUSE_20260802.md`、`docs/archive/DEPLOYMENT_VERIFICATION_REPORT.md`、`docs/reports/bom_fix_links_cleanup_summary_20260803.md` |
| `fix_alert_rules.ps1` | `docs/archive/ALERT_RULES_TROUBLESHOOTING.md`、`docs/archive/FINAL_EXECUTION_REPORT.md` |
| `fix_docker_crash.ps1` | `docs/archive/docker_crash_recovery.md` |
| `fix_docker_mirror_simple.ps1` | `docs/archive/DEPLOYMENT_QUICK_CARD.md`、`docs/archive/QUICK_REFERENCE.md` |
| `import_grafana_dashboard.ps1` | `docs/archive/EXECUTION_SUMMARY_REPORT.md` |
| `manual_fix_alerts.ps1` | `docs/archive/FINAL_EXECUTION_REPORT_V3.md`、`docs/archive/ONE_CLICK_RESTART_GUIDE.md` |
| `one_click_restart.ps1` | `docs/archive/ONE_CLICK_RESTART_GUIDE.md` |
| `setup_alerts.ps1` | `docs/archive/EXECUTION_SUMMARY_REPORT.md` |
| `start_monitoring.ps1` | `docs/OBSERVABILITY_OPERATION_MANUAL.md`（**现行手册**）、`docs/archive/QUICK_START_GUIDE.md`、`docs/archive/docker_startup_guide.md` |
| `update-kubeconfig.ps1` | `docs/archive/QUICK_REAL_CLUSTER_GUIDE.md`、`docs/archive/REAL_KUBECONFIG_GUIDE.md` |
| `fix_docker_mirror.ps1` | `docs/archive/`（多份） |
| `fix_alert_rules.ps1`、`import_grafana_dashboard.ps1`、`manual_fix_alerts.ps1` | 见上 |

### A.3 迁移正确顺序（后续任务用）

1. `git grep -n --fixed-strings <脚本名>` 列出全部引用；
2. 修改 CI workflow / 文档 / 脚本间调用里的路径；
3. `git mv <脚本> scripts/ops/`（**不要用 `Remove-Item` 直删**）；
4. 重跑 `python scripts/dev/check_docs_broken_links.ps1` 确认文档链接未断。

---

## 🔐 附录 B：`.env.backups/` 密钥副本减量（**移交 TASK-07**）

实测（2026-09-18）：

| 项 | 值 |
|---|---|
| 文件数 | **50** |
| 总体积 | **7,038 KB**（均值 ~141 KB/份） |
| 时间跨度 | 2026-09-11 00:04 → 2026-09-13 03:12 |
| git 隔离 | ✅ `.gitignore:285` 已忽略 `.env.backups/` ⇒ 不会进 git 历史 |
| ACL | 继承型，**未显式收紧**（`SYSTEM` / `Administrators` / `AdminWT` 均 FullControl） |

**为什么本次未减量**：删除 47 份**生产密钥副本**是不可逆动作，且需要密钥轮换窗口；
`TASK-00 D6` 要求不碰生产数据，密钥横向扩散的根治归属 **TASK-07**。

**⚠️ 处置前必须先确认"最近 3 份密钥是否仍有效、其余是否有任何回滚用途"。**
确认后的一键命令（**保留最近 3 份**，其余移入带 ACL 收紧的隔离目录）：

```powershell
cd C:\Users\Administrator\agent
# ① 保留最近 3 份，其余移到隔离区（**先移动、不要直接删**）
New-Item -ItemType Directory -Force -Path .env.backups\_quarantine | Out-Null
Get-ChildItem .env.backups -File | Sort-Object LastWriteTime -Descending |
    Select-Object -Skip 3 | Move-Item -Destination .env.backups\_quarantine
# ② 收紧隔离区 ACL（去掉继承，只留 SYSTEM + Administrators）
icacls .env.backups\_quarantine /inheritance:r /grant:r "SYSTEM:(OI)(CI)F" "Administrators:(OI)(CI)F"
# ③ 观察一个密钥轮换周期后，再决定是否删除 _quarantine
```

---

**文档位置**: `docs/ops_quick_manual.md`  
**相关文档**: [应急预案](file:///c:/Users/Administrator/agent/docs/emergency_plan.md) | [部署确认书](file:///c:/Users/Administrator/agent/docs/deployment_confirmation.md)

---

*手册生成时间: 2026-06-11*
*附录 A/B 追加时间: 2026-09-18（TASK-03 地基加固）*
