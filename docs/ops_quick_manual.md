
# SafeFileReader 运维快速操作手册

**文档编号**: OPS-MANUAL-2026-001  
**版本**: v1.0  
**更新日期**: 2026-06-11 

> ⚠ **【2026-10-03 实测更正 · 照抄部分命令会失败或无效，请先读这段】**
> 本文是 2026-06-11 的 SafeFileReader 运维手册，**保留作历史记录**（未整篇删除），
> 但其中三类"快速命令"已被后续清理推翻：
>
> ① **所有 `cp *.bak_*` 手动恢复命令都不可用** —— 实测本仓 `*.bak_*` 归档**零命中**
>    （`Get-ChildItem -Recurse -Filter '*.bak*'` 无输出）⇒ 照抄会报 `No such file or directory`。
>    受影响：故障A 方案2（`cp app_server.py.bak_*` / `cp utils/file_reader.py.bak_*`）、
>    故障B 方案2（`cp data/messages.jsonl.bak_*`）、故障C 的全部 `cp`、以及 §快速诊断表/§文件检查里的 `ls *.bak_*`。
> ② **`./scripts/rollback.sh -t <target>` 不会恢复任何文件** —— 它按
>    `find ... -name "<文件>.bak_*"` 找归档，找不到就**静默跳过**，并以**退出码 0** 结束
>    （看似成功、实则没做任何事）⇒ §回滚命令汇总整张表当前均为 **no-op**。
> ③ **查 `safe_file_reader` 指标不能证明任何事** —— 那 5 个指标名**确实仍在 `/metrics` 上**
>    （可在 `docs/closeout/监控清理_evidence_20261002/live_metric_names.txt` 逐条查到），
>    但发射方 SafeFileReader **在非测试代码里 0 个调用方**（历史读取已改走 `agent/jsonl_history.py` 的尾部窗口）
>    ⇒ **恒为 0**。同时 `monitoring/alerts_safe_file_reader.yml` 的 9 条规则**已于 2026-10-02 删除**，
>    故障D 里 `grep -E "alert: SafeFileReader" monitoring/alerts.yml` **查不到任何东西是预期结果**。
>
> ✅ **现行可用的回滚只有 git**：`git log --oneline -- <文件>` → `git revert <commit>`
>    （或 `git checkout <good-commit> -- <文件>`）。
> 证据：`docs/closeout/监控死规则与陈旧看板清理_20261002.md` §3、`docs/closeout/过期运维指引收口_第二批_20261003.md`。 

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
| 指标无数据 | 检查指标端点、服务日志 | `curl http://localhost:5678/metrics\|grep safe_file_reader` ⚠ **【2026-10-03】查到的 5 个指标恒为 0，不能当读取健康的证据** |
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
# ⛔ 【2026-10-03 实测】当前是 no-op：脚本按 find -name "app_server.py.bak_*" 找归档，
#    本仓零命中 ⇒ 静默跳过并以退出码 0 结束（看似成功，实际什么都没恢复）。
./scripts/rollback.sh -t code

# 方案2: 手动恢复
# ⛔ 【2026-10-03 实测】以下两个 .bak_* 文件在本仓不存在，照抄会报 No such file or directory：
# cp app_server.py.bak_* app_server.py
# cp utils/file_reader.py.bak_* utils/file_reader.py

# ✅ 现行做法：用 git 回退到已知正常的 commit
git log --oneline -10 -- app_server.py utils/file_reader.py
git checkout <good-commit> -- app_server.py utils/file_reader.py

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
# ⛔ 【2026-10-03 实测】当前是 no-op（无 data/messages.jsonl.bak_* 归档，脚本静默跳过并退出 0）
./scripts/rollback.sh -t data

# 方案2: 手动恢复（先判空！本仓 data/*.bak_* 零命中，直接 cp 会失败）
BACKUP_FILE=$(ls -t data/messages.jsonl.bak_* 2>/dev/null | head -1)
if [ -z "$BACKUP_FILE" ]; then
  echo "无 .bak 备份 ⇒ 改用 git：git log --oneline -- data/messages.jsonl; git checkout <good-commit> -- data/messages.jsonl"
else
  cp "$BACKUP_FILE" data/messages.jsonl
fi

# 重启服务
python app_server.py

# 验证
# ⚠ 【2026-10-03】下面这条指标名真实存在，但**恒为 0**（SafeFileReader 无生产调用方）
#    ⇒ 它证明不了历史是否恢复。真正该看的是服务日志的 [历史加载] 行与前端历史列表：
# curl http://localhost:5678/metrics | grep yunshu_safe_file_reader_loaded_history_count
tail -100 logs/app_server.log | grep "历史加载"
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
# ⛔ 【2026-10-03 实测】以下 4 类 .bak_* 文件在本仓全部不存在（Get-ChildItem -Recurse -Filter '*.bak*' 零命中）
#    ⇒ 照抄会连续报 No such file or directory。保留原样仅为对照 2026-06-10 的流程：
# cp app_server.py.bak_* app_server.py
# cp utils/file_reader.py.bak_* utils/file_reader.py
# cp data/messages.jsonl.bak_* data/messages.jsonl
# cp monitoring/alerts.yml.bak_* monitoring/alerts.yml

# ✅ 现行做法：用 git 把这四个文件回退到已知正常的 commit
git log --oneline -10 -- app_server.py utils/file_reader.py data/messages.jsonl monitoring/alerts.yml
git checkout <good-commit> -- app_server.py utils/file_reader.py data/messages.jsonl monitoring/alerts.yml

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
# ⚠ 【2026-10-03 实测】monitoring/alerts.yml 里已无任何 SafeFileReader 规则，grep 零命中是**预期结果**，
#    不能据此判断"告警配置坏了"：那 9 条原本在 monitoring/alerts_safe_file_reader.yml，已于 2026-10-02 整文件删除。
grep -E "alert: SafeFileReader" monitoring/alerts.yml

# 3. 恢复告警规则
# ⛔ 【2026-10-03 实测】当前是 no-op（无 alerts.yml.bak_* / file_reader.py.bak_* 归档，脚本静默跳过）
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
# ⚠ 【2026-10-03】下面这 5 个 safe_file_reader 指标名**确实还在 /metrics 上，但恒为 0**
#    （SafeFileReader 在非测试代码里 0 个调用方）⇒ 它们只能证明"指标端点活着"，
#    **不能**用来判断读取是否健康。判断历史读取请看服务日志的 [历史加载] 行。
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

> ⚠ **【2026-10-03 实测】下表所有命令当前均为 no-op**：脚本按
> `find ... -name "<文件>.bak_*"` 找归档，而本仓 `.bak_*` **零命中** ⇒ 找不到就静默跳过，
> 并以**退出码 0** 结束（看似成功、实际什么都没恢复）。下表保留作历史对照。
> **现行唯一可用回滚是 git**（见本节末的"Git 回滚（现行）"）。

| 操作 | 命令 |
|------|------|
| ~~全部回滚~~ ⚠ 当前 no-op | `./scripts/rollback.sh -t all` |
| ~~仅代码回滚~~ ⚠ 当前 no-op | `./scripts/rollback.sh -t code` |
| ~~仅数据回滚~~ ⚠ 当前 no-op | `./scripts/rollback.sh -t data` |
| ~~仅监控回滚~~ ⚠ 当前 no-op | `./scripts/rollback.sh -t monitoring` |
| ~~回滚不重启~~ ⚠ 当前 no-op | `./scripts/rollback.sh -t all -n` |
| 列出备份 | `./scripts/rollback.sh -l`（⚠ 本仓无 `.bak_*` ⇒ 通常列出"未找到备份"） |

### Windows PowerShell

> ⚠ **【2026-10-03 实测】同上，全部为 no-op**（本仓无 `.bak_*` 归档）。

| 操作 | 命令 |
|------|------|
| ~~全部回滚~~ ⚠ 当前 no-op | `.\scripts\rollback.ps1 -Target all` |
| ~~仅代码回滚~~ ⚠ 当前 no-op | `.\scripts\rollback.ps1 -Target code` |
| ~~仅数据回滚~~ ⚠ 当前 no-op | `.\scripts\rollback.ps1 -Target data` |
| ~~仅监控回滚~~ ⚠ 当前 no-op | `.\scripts\rollback.ps1 -Target monitoring` |
| ~~回滚不重启~~ ⚠ 当前 no-op | `.\scripts\rollback.ps1 -Target all -NoRestart` |
| 列出备份 | `.\scripts\rollback.ps1 -List`（⚠ 本仓无 `.bak_*`） |

### ✅ Git 回滚（现行唯一可用方式）

```bash
# 1) 定位变更点
git log --oneline -20 -- <文件>

# 2) 回退（二选一）
git revert <bad-commit>                 # 留痕式，推荐
git checkout <good-commit> -- <文件>     # 只回退指定文件

# 3) 重启并验证
python app_server.py
curl http://localhost:5678/health
```

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
# ⚠ 【2026-10-03 实测】下面第 4/5 项查到的指标名真实存在，但**恒为 0**
#    （SafeFileReader 在非测试代码里 0 个调用方）⇒ 它们不是"读取健康"的证据。
#    真正反映历史读取的是服务日志的 [历史加载] 行，见第 6 项。
echo "4. 历史加载指标（恒为 0，仅供参考）:"
curl -s http://localhost:5678/metrics | grep yunshu_safe_file_reader_loaded_history_count

echo ""
echo "5. 无效行比例（恒为 0，仅供参考）:"
curl -s http://localhost:5678/metrics | grep yunshu_safe_file_reader_invalid_ratio

echo ""
echo "6. 历史加载日志（现行判据）:"
tail -100 logs/app_server.log | grep "历史加载" || echo "⚠ 无历史加载日志"

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
