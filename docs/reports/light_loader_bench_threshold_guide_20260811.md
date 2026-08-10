# light_loader 性能门禁阈值配置指南

- 生成时间：2026-08-11
- 适用：`.github/workflows/daily_regression.yml` 的 `light-loader-bench` job

## 一、当前阈值合理性分析

| 项 | 值 | 说明 |
|---|---|---|
| 基线（baseline） | 1944ms | 本机实测 10000 卡串行耗时（2026-08-11 workers 拐点扫描中位数） |
| 容差（tolerance） | 1.5x | 阈值 = 1944 × 1.5 ≈ 2916ms |
| 实际门禁阈值 | 2916ms | env `LIGHT_LOADER_SERIAL_BASELINE_MS` × `LIGHT_LOADER_BENCH_TOLERANCE` |

**合理性结论**：门禁结构合理（独立 job + env 阈值 + 退出码契约 + 分类通知），
但两点需注意：

1. **基线来源是本机，不是 runner**：nightly 实际运行在 GitHub `ubuntu-latest`
   runner 上，其 CPU 与本机（Windows 12 核）不同。若 runner 基线显著高于
   1944ms，固定该基线会误报退化；若显著更低则门禁失效。
2. **容差 1.5x 对 runner 波动偏紧**：shared runner 有邻居噪声，建议首次在
   目标 runner 上校准基线后，将容差暂设为 2.0x 观察 1~2 周，再收敛到 1.5x。

## 二、阈值动态调整（硬件升级 / 更换 runner）

设计原则：**阈值 = 基线 × 容差，不写死绝对值**。升级后只需两步：

### 步骤 1：在目标环境校准新基线

```bash
# 在目标机器 / runner 上（与 nightly 相同硬件、相同 Python 版本）
$env:PYTHONIOENCODING="utf-8"
python scripts/dev/bench_light_loader_serial_parallel.py --scale 10000
# 输出串行耗时即新基线，如：串行=1500.00ms
```

### 步骤 2：更新 daily_regression.yml 的 env

```yaml
env:
  LIGHT_LOADER_SERIAL_BASELINE_MS: '1500'   # ← 新基线
  LIGHT_LOADER_BENCH_TOLERANCE: '1.5'       # ← 容差（可保留或微调）
```

无需改动 workflow 逻辑、脚本或通知模板。若担心误报，先调大容差（如 2.0），
连续几轮稳定后再收窄。

### 容差选择建议

| 场景 | 容差 | 说明 |
|---|---|---|
| 首次接入 / runner 波动大 | 2.0x | 宁缺毋滥，避免误报 |
| 稳定环境 | 1.5x | 捕获解析器回退（SafeLoader 约 7.6x 退化，远超该阈值） |
| 严格性能门禁 | 1.25x | 仅本机自托管 runner、硬件固定时使用 |

> 主要防护目标：libyaml C 扩展回退（SafeLoader）会使解析退化约 7.6x，
> 任何合理容差（≤3x）都能捕获；容差主要权衡的是 runner 噪声误报率。

## 三、失败时如何排查

性能退化通知（⚠️）与功能回归通知（❌）已分离（见 daily_regression.yml
`test-summary`）。收到性能告警时按序排查：

1. `python scripts/dev/check_light_loader_compat.py --json` —— 确认 CSafeLoader 是否可用；
2. 对比 [规模基准](light_loader_serial_parallel_bench_20260811.md) 的每卡耗时；
3. 若为硬件/runner 变更，按上文步骤 1~2 重新校准基线。

## 四、相关文档

- [规模基准](light_loader_serial_parallel_bench_20260811.md)
- [线程数拐点](light_loader_workers_scan_20260811.md)
- [月度汇总](light_loader_monthly_summary_202608.md)
