# Windows 环境内存优化操作指南

**目的**: 解决 v6.5 Reranker 模型加载触发的 `os error 1455`（页面文件太小，无法完成操作）
**适用环境**: Windows 10 / Windows Server 2016+
**预期收益**: 将页面文件从默认 ~2GB 扩展到 ≥8GB，使 BGE-reranker-v2-m3（~2.3GB）可正常加载

---

## 1. 背景与根因

### 1.1 错误现象

```
OSError: [Errno 1455] 页面文件太小，无法完成操作
```

- 错误码：`ERROR_COMMITMENT_LIMIT`（1455）
- 触发位置：`sentence_transformers.CrossEncoder.__init__` 加载 ~2.3GB 模型权重时
- 退出码：1

### 1.2 根因

Windows 系统的**提交内存**（Committed Memory）= 物理 RAM + 页面文件（虚拟内存）。
当模型加载需要分配大块连续内存时，若提交内存超过物理 RAM + 页面文件总和，触发 1455 错误。

**不是 Python/sentence-transformers 的 bug，是 Windows 系统级内存配置不足。**

### 1.3 解决思路

| 路径 | 操作 | 难度 | 推荐度 |
|------|------|------|--------|
| 方案 A | 扩大页面文件到 ≥8GB | ⭐ 简单 | ⭐⭐⭐ 推荐 |
| 方案 B | 换用轻量模型 bge-reranker-base（~1.1GB）| ⭐ 简单 | ⭐⭐ 已实施 |
| 方案 C | 部署到 Linux 环境 | ⭐⭐⭐ 复杂 | ⭐ 长期方案 |

**最佳实践**：A + B 组合（已切换 base 模型 + 扩页面文件作为双保险）。

---

## 2. 方案 A：手动扩大页面文件到 8GB（详细步骤）

### 2.1 步骤 1：打开系统属性

**方法 1（GUI 推荐）**：
1. 按 `Win + R` 打开运行对话框
2. 输入 `sysdm.cpl` 回车，打开**系统属性**
3. 切换到**高级**选项卡
4. 在**性能**区域点击**设置...** 按钮
5. 切换到**高级**选项卡
6. 在**虚拟内存**区域点击**更改...** 按钮

**方法 2（PowerShell 快捷）**：
```powershell
# 以管理员身份运行 PowerShell
Start-Process "SystemPropertiesPerformance.exe" -Verb RunAs
```

### 2.2 步骤 2：取消自动管理

1. 在**虚拟内存**对话框中
2. **取消勾选**顶部的「自动管理所有驱动器的分页文件大小」复选框

### 2.3 步骤 3：选择驱动器

1. 在**驱动器**列表中，选择系统盘（通常是 `C:`）
2. 选择「自定义大小」单选按钮

### 2.4 步骤 4：设置初始大小和最大值

**关键参数**（页面文件大小计算）：

| 配置项 | 推荐值 | 说明 |
|--------|--------|------|
| 初始大小（MB） | `8192` | 8GB，避免运行时动态扩展导致性能抖动 |
| 最大值（MB） | `16384` | 16GB，预留扩展空间 |

**填入步骤**：
1. 在「初始大小（MB）」输入框填入 `8192`
2. 在「最大值（MB）」输入框填入 `16384`
3. 点击**设置**按钮（必须点击，否则不生效）
4. 点击**确定**保存

### 2.5 步骤 5：重启系统

页面文件修改**必须重启**才能生效：

```powershell
# 重启命令（管理员 PowerShell）
Restart-Computer -Force
```

或点击开始菜单 → 电源 → 重启。

### 2.6 步骤 6：验证配置生效

重启后，打开 PowerShell 验证：

```powershell
# 查看当前页面文件配置
Get-CimInstance -ClassName Win32_PageFileUsage | Select-Object Name, AllocatedBaseSize, PeakUsage

# 查看提交内存上限
Get-CimInstance -ClassName Win32_OperatingSystem | Select-Object TotalVirtualMemorySize, FreePhysicalMemory
```

**预期输出**：
- `AllocatedBaseSize` 应为 `8192` MB 或更大
- `TotalVirtualMemorySize` 应显著提升（约等于 RAM + 页面文件）

---

## 3. 方案 A 自动化脚本（可选）

若需在多台机器批量配置，使用以下 PowerShell 脚本（**需管理员权限**）：

```powershell
# set_pagefile_8gb.ps1 — 自动设置页面文件为 8-16GB
# 必须以管理员身份运行

# 检查管理员权限
if (-not ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "请以管理员身份运行此脚本" -ForegroundColor Red
    exit 1
}

# 关闭自动管理
Set-CimInstance -Query "Select * from Win32_ComputerSystem" -Property @{ AutomaticManagedPagefile = $False }

# 设置 C 盘页面文件为 8-16GB
Set-CimInstance -Query "Select * from Win32_PageFileSetting where Name='C:\\pagefile.sys'" -Property @{ InitialSize = 8192; MaximumSize = 16384 } -ErrorAction SilentlyContinue

# 若 C 盘未配置则新建
if (-not (Get-CimInstance -Query "Select * from Win32_PageFileSetting where Name='C:\\pagefile.sys'" -ErrorAction SilentlyContinue)) {
    New-CimInstance -ClassName Win32_PageFileSetting -Property @{ Name = "C:\pagefile.sys"; InitialSize = 8192; MaximumSize = 16384 }
}

# 验证
Get-CimInstance -ClassName Win32_PageFileSetting | Select-Object Name, InitialSize, MaximumSize

Write-Host "页面文件已配置，需重启生效" -ForegroundColor Green
Write-Host "重启命令: Restart-Computer -Force" -ForegroundColor Yellow
```

**使用方法**：
```powershell
# 保存为 set_pagefile_8gb.ps1 后执行
powershell -ExecutionPolicy Bypass -File .\set_pagefile_8gb.ps1
```

---

## 4. 内存优化附加建议

### 4.1 关闭不必要的后台进程

```powershell
# 查看占用内存 Top 10 进程
Get-Process | Sort-Object WorkingSet64 -Descending | Select-Object -First 10 Name, @{N='MB';E={[math]::Round($_.WorkingSet64/1MB,1)}}
```

可关闭的高内存进程示例：
- Chrome / Edge 多标签页（每个标签 ~200MB）
- Docker Desktop（~2GB）
- WSL2（~1GB）
- IDE 多开（VS Code / IntelliJ）

### 4.2 设置 Python 内存友好环境变量

在 `.env` 文件中追加：

```bash
# PyTorch 内存优化
PYTORCH_CUDA_ALLOC_CONF=max_split_size_mb:128
TOKENIZERS_PARALLELISM=false

# HF 模型加载优化
HF_HUB_OFFLINE=1
TRANSFORMERS_OFFLINE=1
```

### 4.3 使用 device=cpu 避免显存分配

`reranker.py` 已默认使用 CPU 推理（CrossEncoder 不指定 device 时默认 cpu），无需修改。

---

## 5. 验证 Reranker 模型加载

完成页面文件扩容后，执行以下验证脚本：

```powershell
# 验证脚本（需先 set SKILL_RERANKER_MODEL=BAAI/bge-reranker-base）
cd c:\Users\Administrator\agent
python -c "
import os
os.environ['HF_ENDPOINT'] = 'https://hf-mirror.com'
from sentence_transformers import CrossEncoder
import time
t0 = time.time()
model = CrossEncoder('BAAI/bge-reranker-base')
print(f'加载耗时: {time.time()-t0:.2f}s')
scores = model.predict([('语音识别', '语音交互助手'), ('语音识别', 'PDF解析器')])
print(f'排序分数: {scores}')
print('验证通过' if scores[0] > scores[1] else '排序异常')
"
```

**预期输出**：
```
加载耗时: 5-15s
排序分数: [8.5, -5.2]  # 数值仅供参考
验证通过
```

---

## 6. 常见问题排查

### Q1：修改后仍报 os error 1455？

**检查项**：
1. 是否已**重启**系统（必须）
2. 页面文件是否在 C 盘且有足够磁盘空间（需 ≥16GB 可用）
3. 物理内存是否过小（建议 ≥8GB RAM）
4. 其他进程是否占用过多提交内存（任务管理器 → 性能 → 内存 → 已提交）

### Q2：PowerShell 脚本执行被策略阻止？

```powershell
# 临时绕过执行策略
powershell -ExecutionPolicy Bypass -File .\set_pagefile_8gb.ps1

# 或永久修改策略（管理员）
Set-ExecutionPolicy RemoteSigned -Scope CurrentUser
```

### Q3：能否使用 SSD 提升页面文件性能？

可以，**强烈建议**将页面文件放在 SSD 上：
- HDD：页面文件读写 ~100MB/s，模型加载慢
- SSD：页面文件读写 ~500MB/s，模型加载快 5 倍

### Q4：扩大页面文件是否损伤 SSD 寿命？

理论上有少量写入磨损，但实际影响极小：
- 模型加载主要读取，写入量 < 1GB/次
- 现代 SSD 寿命 ≥ 100TBW，可承受 10 万次以上模型加载
- 不必担心

---

## 7. 回滚方案

若扩容后出现异常，回滚到默认配置：

```powershell
# 管理员 PowerShell
Set-CimInstance -Query "Select * from Win32_ComputerSystem" -Property @{ AutomaticManagedPagefile = $True }
Remove-CimInstance -Query "Select * from Win32_PageFileSetting"
Restart-Computer -Force
```

---

## 8. 参考资料

- Microsoft 文档：[How to change page file in Windows](https://learn.microsoft.com/en-us/windows/client-management/settings).
- 错误码 1455：[ERROR_COMMITMENT_LIMIT](https://learn.microsoft.com/en-us/windows/win32/debug/system-error-codes--1300-1699-).
- v6.5 测试报告：[RETRIEVAL_UPGRADE_V6_5_TEST_REPORT.md](file:///c:/Users/Administrator/agent/docs/RETRIEVAL_UPGRADE_V6_5_TEST_REPORT.md).
