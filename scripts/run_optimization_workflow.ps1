# B1 锁热点分析 + C1 灰度发布 自动化执行脚本（固化本会话步骤）
# 用法:
#   .\run_optimization_workflow.ps1 -Mode B1                  # 锁采样 + 热点报告
#   .\run_optimization_workflow.ps1 -Mode B1 -PytestTarget "tests/unit/test_skills_mgmt_safety.py tests/unit/test_lock_watchdog.py"
#   .\run_optimization_workflow.ps1 -Mode C1 -DryRun          # 灰度流程演练（只打印命令）
#   .\run_optimization_workflow.ps1 -Mode C1 -RemoteHost prod1 -RollbackDrill  # 真实执行 + 回滚演练
#   .\run_optimization_workflow.ps1 -Mode All
#
# 【权限与安全说明】
# - B1: LOCK_PROFILE=1 仅用于性能分析环境，生产勿启用；采样输出默认在系统 Temp（LOCK_PROFILE_LOG 可覆盖）
# - C1: 生产灰度/回滚属生产变更，需管理员权限与变更审批；建议先 -DryRun 演练再小流量放量
# - C1 小流量(5%) 语义 = 对目标主机子集执行（本脚本单主机执行，多机灰度需循环调用）
# - 回滚演练(-RollbackDrill) 应在首次全量前完成；任一判定指标不达标立即回滚

param(
    [ValidateSet("B1", "C1", "All")]
    [string]$Mode = "All",
    [string]$PytestTarget = "tests/unit",          # B1: 采样目标（LOCK_PROFILE=1 运行）
    [string]$ProfileEnv = "1",                     # B1: LOCK_PROFILE 开关
    [switch]$DryRun,                               # C1: 只打印命令不执行
    [string]$RemoteHost = "",                      # C1: 部署主机（SSH 前缀，空=本机）
    [string]$ServiceName = "yunshu",               # C1: systemd 服务名
    [string]$ImageTag = "yunshu:v1.2.0-rc4-final", # C1: 镜像标签
    [switch]$RollbackDrill                         # C1: 全量前执行一次回滚演练
)

$ErrorActionPreference = "Stop"

function Invoke-Remote {
    param([string]$Cmd)
    if ($DryRun) { Write-Host "  [DRY-RUN] $Cmd" }
    elseif ($RemoteHost) { & ssh $RemoteHost $Cmd 2>&1 | ForEach-Object { Write-Host "  $_" } }
    else { Invoke-Expression $Cmd 2>&1 | ForEach-Object { Write-Host "  $_" } }
}

function Invoke-B1 {
    Write-Host "`n========== [B1] 锁竞争热点采样与分析 =========="
    $prev = $env:LOCK_PROFILE
    try {
        $env:LOCK_PROFILE = $ProfileEnv
        Write-Host "[B1.1] 采样运行: pytest $PytestTarget (LOCK_PROFILE=$ProfileEnv)"
        python -m pytest $PytestTarget -q -p no:randomly --no-header
        if ($LASTEXITCODE -ne 0) { throw "采样测试失败 rc=$LASTEXITCODE" }
    }
    finally { $env:LOCK_PROFILE = $prev }

    Write-Host "[B1.2] 汇总采样并生成热点报告（Top 10）"
    python scripts/analyze_lock_hotspots.py --report --top 10
    if ($LASTEXITCODE -ne 0) { throw "热点报告生成失败 rc=$LASTEXITCODE" }

    Write-Host "[B1] 完成。报告已生成（路径见 analyze_lock_hotspots.py 输出）"
    Write-Host "[B1] 下一步: 依据 Top 锁热点实施读锁/分段锁/无锁化优化（验收: wait 时间降 >=50%）"
}

function Invoke-C1 {
    Write-Host "`n========== [C1] 灰度发布流程（阶段5 部署） =========="
    if ($DryRun) { Write-Host "[C1] DRY-RUN 模式：仅打印命令，不执行" }
    elseif (-not $RemoteHost) { Write-Host "[C1] 注意：未指定 -RemoteHost，将在本机执行（需部署权限）" }

    # 0. 前置检查
    Write-Host "`n[C1.0] 前置检查（不满足则中止）"
    $dirty = git status --porcelain 2>&1
    if ($dirty) { Write-Host "  ! 工作区有未提交改动，建议先提交：" ; $dirty | Select-Object -First 5 | ForEach-Object { Write-Host "    $_" } }
    $planning = Select-String -Path config.yaml -Pattern "planning:" -SimpleMatch -Quiet
    if ($planning) { Write-Host "  config.yaml 含 planning 配置 ✓" } else { Write-Host "  ! config.yaml 缺少 planning 配置，中止"; return }
    Invoke-Remote "ls /opt/yunshu/.env" # 部署目录可达性（DRY-RUN 时仅打印）

    # 1. 基线（全量关闭）
    Write-Host "`n[C1.1] 基线：PLANNING_ENABLED=false"
    Invoke-Remote "systemctl stop $ServiceName && systemctl set-environment PLANNING_ENABLED=false && systemctl start $ServiceName"
    Write-Host "  记录基线看板数值（planning 流量/成本/错误率）"

    # 2. 小流量 5%（目标实例开启）
    Write-Host "`n[C1.2] 小流量：5% 实例 PLANNING_ENABLED=true（观察 3-5 天）"
    Invoke-Remote "systemctl stop $ServiceName && systemctl set-environment PLANNING_ENABLED=true && systemctl start $ServiceName"
    Write-Host "  观察指标：$ImageTag 规划成功率 100% / 单计划成本 <= \$0.01 / 平均耗时 <= 30s"

    # 3. 回滚演练（首次全量前必须）
    if ($RollbackDrill) {
        Write-Host "`n[C1.3] 回滚演练（验证后恢复 true）"
        Invoke-Remote "systemctl stop $ServiceName && systemctl set-environment PLANNING_ENABLED=false && systemctl start $ServiceName"
        Write-Host "  验证：看板 yunshu_intent_layer_total{layer=\"planning\"} 增量归零（5min 窗口）+ [planning] 日志停止"
        Invoke-Remote "systemctl stop $ServiceName && systemctl set-environment PLANNING_ENABLED=true && systemctl start $ServiceName"
    }

    # 4. 全量 + 稳定观察
    Write-Host "`n[C1.4] 全量：所有实例 PLANNING_ENABLED=true，稳定观察 7 天"
    Write-Host "[C1.5] 监控检查（Prometheus 9090）:"
    Write-Host "  planning 流量    : sum(rate(yunshu_intent_layer_total{layer=\"planning\"}[5m]))"
    Write-Host "  单计划成本        : sum(rate(yunshu_planning_cost_total[5m]))"
    Write-Host "  LLM 错误率        : rate(llm_call_errors_total[5m]) / clamp_min(rate(llm_call_total[5m]),1)"
    Write-Host "  锁纪律违规        : increase(lock_hold_timeouts_total[10m]) / increase(lock_wait_timeouts_total[10m])"
    Write-Host "  全部判定指标达标 7 天 -> 更新阶段5 验收报告 §3.4/§3.5 为「通过」"

    Write-Host "[C1] 完成。任一指标不达标 -> 按 [C1.3] 回滚并复盘"
}

if ($Mode -in @("B1", "All")) { Invoke-B1 }
if ($Mode -in @("C1", "All")) { Invoke-C1 }
Write-Host "`n========== 流程结束 =========="
