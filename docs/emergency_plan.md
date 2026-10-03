# SafeFileReader 应急预案文档

**文档编号**: EMERGENCY-PLAN-2026-001  
**生效日期**: 2026-06-10  
**版本**: v1.0  
**状态**: ✅ 已生效

> ⚠ **【2026-10-03 实测更正 · 照抄下面部分步骤会失败，请先读这段】**
>
> ① **本预案里所有 `cp ...bak_20260610_144932 ...` 手动回滚命令都不可用**：这些备份文件
>    **在本仓并不存在**。实测 `git grep -n 'file_reader.py.bak'` 只命中**文档与脚本里的字符串**，
>    `Get-ChildItem -Recurse -Filter '*.bak*'` **一个文件都没有**（详见
>    `docs/closeout/过期运维指引收口_第二批_20261003.md` §1）。
>    受影响的步骤：**A5（:67）、C4（:215-218）、C5（:234-236）**。
> ② **`scripts/rollback.sh` / `rollback.ps1` 同样不会恢复任何文件**：它们按
>    `find ... -name "xxx.bak_*"` 找归档，找不到就**静默跳过**（不是报错）⇒
>    `./scripts/rollback.sh -t code` 会"正常退出但什么都没恢复"，比报错更容易骗过人。
> ③ **正确的回滚是 git 层面的**（这也是本仓唯一的版本真相）：
>    `git log --oneline -- <文件>` 找到变更点 → `git revert <commit>`（或
>    `git checkout <good-commit> -- <文件>`）。清单见下方 §C2 与 §6.3。
> ④ 告警 `SafeFileReaderHistoryLoadFailed` 等 4 条**已于 2026-10-02 随
>    `monitoring/alerts_safe_file_reader.yml` 一起删除**（全仓已无该名字），B1 里"告警触发"这一条现象不再存在。
>    原因与恢复路径：`docs/closeout/监控死规则与陈旧看板清理_20261002.md` §3。
>
> 本文**保留作历史记录**（2026-06-10 上线期文档），未做整篇删除；以下只把"照着做会失败"的步骤改为事实描述。

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

#### A5. 手动回滚方案（备用）⚠ **本节命令已作废**

> **【2026-10-03 实测】** 下面两条 `cp` 引用的备份文件**在本仓不存在**
> （`Get-ChildItem -Recurse -Filter '*.bak*'` 零命中）⇒ **照抄会直接 `No such file or directory`**。
> 正确的手动回滚是 git 层面：`git log --oneline -- app_server.py` 找到最后一个正常 commit，
> 再 `git checkout <good-commit> -- app_server.py utils/file_reader.py`。

```bash
# ⛔ 已作废（引用的备份文件不存在，执行会报 "No such file or directory"）：
# cp app_server.py.bak_20260610_144932 app_server.py
# cp utils/file_reader.py.bak_20260610_144932 utils/file_reader.py

# ✅ 现行可用的手动回滚（git 是唯一的版本真相）：
git log --oneline -10 -- app_server.py utils/file_reader.py   # 先找到目标 commit
git checkout <good-commit> -- app_server.py utils/file_reader.py
python app_server.py
```

---

### 🔴预案 B: 历史数据丢失

#### B1. 故障现象
- 用户历史对话列表为空
- 历史文件损坏或不存在
- ~~告警 `SafeFileReaderHistoryLoadFailed` 触发~~ ⚠ **【2026-10-03】该告警已不存在**：
  它随 `monitoring/alerts_safe_file_reader.yml` 于 2026-10-02 一并删除（全仓 `git grep` 已无此名字，
  见 `docs/closeout/监控死规则与陈旧看板清理_20261002.md` §3）⇒ **不要再等这条告警来发现历史丢失**，
  改为看服务日志 `[历史加载]` 行与前端历史列表

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

# 4. 检查指标端点
#    ⚠【2026-10-03】下面这 5 个 safe_file_reader 指标**仍然注册在 /metrics 上，但恒为 0**：
#    发射方 SafeFileReader 在非测试代码里 0 个调用方（历史读取已改走 agent/jsonl_history.py 的尾部窗口）
#    ⇒ 这条命令**查不出"历史是否丢失"**，只能证明指标端点活着。改用下面第 5 条。
curl http://localhost:5678/metrics | grep safe_file_reader

# 5. ✅ 真正能反映"历史是否读到"的证据（现行实现：尾部窗口读取）
tail -100 logs/app_server.log | grep "历史加载"
```

#### B3. 应急响应流程

| 步骤 | 操作 | 命令 | SLA |
|------|------|------|-----|
| 1 | 确认数据丢失 | 检查文件和告警 | 2分钟 |
| 2 | 查找备份文件 | ~~`ls data/*.bak_*`~~ ⚠ **【2026-10-03】本仓内 `data/*.bak_*` 零命中**，该步通常无结果 | 1分钟 |
| 3 | 恢复备份文件 | ~~`./scripts/rollback.sh -t data`~~ ⚠ **不会恢复任何文件**（脚本 `find ... -name "messages.jsonl.bak_*"` 找不到就静默跳过）；改走 git：`git checkout <good-commit> -- data/messages.jsonl` | 5分钟 |
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

#### B5. 手动恢复方案（备用）⚠ **前提通常不成立**

> **【2026-10-03 实测】** `data/messages.jsonl.bak_*` **在本仓零命中**（`Get-ChildItem -Recurse -Filter '*.bak*'`）⇒
> 若你的环境里确实没有历史备份，本节第 1 步会得到空值、第 2 步会失败。先按下面的命令**判空**再决定走哪条路。

```bash
# 1. 找到最新备份（若输出为空 ⇒ 本节不可用，改走 git 恢复）
BACKUP_FILE=$(ls -t data/messages.jsonl.bak_* 2>/dev/null | head -1)
if [ -z "$BACKUP_FILE" ]; then
  echo "无 .bak 备份 ⇒ 改用 git 回滚：git log --oneline -- data/messages.jsonl; git checkout <good-commit> -- data/messages.jsonl"
else
  # 2. 恢复文件
  cp "$BACKUP_FILE" data/messages.jsonl
fi

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
#    ⚠【2026-10-03 实测】本仓内以上四类 `.bak_*` **全部零命中** ⇒ 输出为空是「正常现状」，
#    不是「C 类故障」。真正的版本真相在 git，改看：
git log --oneline -10 -- app_server.py utils/file_reader.py data/messages.jsonl monitoring/alerts.yml
git status --short        # 确认工作区没有被回滚脚本搅动

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
#    ⚠【2026-10-03 实测】本仓内零命中，下面这条通常无输出：
ls -t *.bak_* 2>/dev/null | head -1

# 3. 手动恢复所有文件
#    ⛔ 已作废：以下 4 个 .bak_20260610_144932 文件**在本仓不存在**，照抄会报 No such file or directory。
# cp app_server.py.bak_20260610_144932 app_server.py
# cp utils/file_reader.py.bak_20260610_144932 utils/file_reader.py
# cp data/messages.jsonl.bak_20260610_144932 data/messages.jsonl
# cp monitoring/alerts.yml.bak_20260610_144932 monitoring/alerts.yml
#
#    ✅ 现行做法：用 git 把四个文件回退到已知正常的 commit
git log --oneline -10 -- app_server.py utils/file_reader.py data/messages.jsonl monitoring/alerts.yml
git checkout <good-commit> -- app_server.py utils/file_reader.py data/messages.jsonl monitoring/alerts.yml

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
# ⛔ 已作废：以下备份文件在本仓不存在（实测 Get-ChildItem -Recurse -Filter '*.bak*' 零命中），
#    执行会报 Cannot find path。改用 git 回退：
# Copy-Item "app_server.py.bak_20260610_144932" "app_server.py"
# Copy-Item "utils\file_reader.py.bak_20260610_144932" "utils\file_reader.py"
# Copy-Item "data\messages.jsonl.bak_20260610_144932" "data\messages.jsonl"
git log --oneline -10 -- app_server.py utils/file_reader.py data/messages.jsonl
git checkout <good-commit> -- app_server.py utils/file_reader.py data/messages.jsonl

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
#    ⚠【2026-10-03 实测】monitoring/alerts.yml 里**已经没有**任何 SafeFileReader 规则
#    （grep 零命中）——那 9 条原本在 monitoring/alerts_safe_file_reader.yml，已于 2026-10-02 整文件删除。
#    所以这条命令现在查不到东西是「预期结果」，不能据此判断告警配置坏了：
grep -E "alert: SafeFileReader" monitoring/alerts.yml

# 3. 恢复告警规则
#    ⚠【2026-10-03 实测】./scripts/rollback.sh -t monitoring 不会恢复任何文件：
#    它按 find -name "alerts.yml.bak_*" / "file_reader.py.bak_*" 找归档，本仓零命中 ⇒ 静默跳过。
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

> ⚠ **【2026-10-03】** 下面第 1 条能查到 5 个 `safe_file_reader` 指标名，但它们**恒为 0**
> （发射方 SafeFileReader 在非测试代码里 0 个调用方）⇒ 它们**不是**读取健康的证据，
> 只能用来判断「指标端点是否活着」。判断历史读取是否正常请看服务日志的 `[历史加载]` 行。

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

### 6.3 ⚠ **【2026-10-03 实测】上面这套回滚命令当前不会恢复任何文件**

`scripts/rollback.sh` / `rollback.ps1` 的工作方式是 `find ... -name "<文件>.bak_*"` 找归档再覆盖回去。
实测本仓 **`.bak_*` 归档零命中**（`Get-ChildItem -Recurse -Filter '*.bak*'` 无输出）⇒
脚本会走到「找不到备份」分支并**静默跳过**，最终以退出码 0 结束，**看上去成功、实际什么都没做**。

**当前唯一可用的回滚路径是 git**：

```bash
# 1) 找到要回退到的 commit
git log --oneline -20 -- app_server.py utils/file_reader.py data/messages.jsonl monitoring/alerts.yml

# 2) 回退（二选一）
git revert <bad-commit>                       # 留痕式回滚（推荐）
git checkout <good-commit> -- <文件>...        # 只回退指定文件

# 3) 重启并验证
python app_server.py
curl http://localhost:5678/health
```

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

> ⚠ **【2026-10-03 实测】以下三类备份在本仓目前全部不存在**（`Get-ChildItem -Recurse -Filter '*.bak*'` 零命中）。
> 这些是 2026-06-10 上线演练期的产物，未随仓库保留；`.gitignore` 也未必收它们。
> **不要依赖这些路径做恢复** —— 现行的版本真相在 git（见 §6.3）。

- ~~**代码备份**: `*.bak_YYYYMMDD_HHMMSS`~~ ⚠ 本仓不存在
- ~~**数据备份**: `data/*.bak_YYYYMMDD_HHMMSS`~~ ⚠ 本仓不存在
- ~~**监控备份**: `monitoring/*.bak_YYYYMMDD_HHMMSS`~~ ⚠ 本仓不存在

---

*预案生成时间: 2026-06-10 15:00:00*