# light_loader 月度汇总（2026-08）

- 归档时间：2026-08-11
- 生成方式：scripts/dev/archive_light_loader_reports.py（幂等，按文件名去重）

## 本月报告清单

| 报告 | 标题 |
|---|---|
| light_loader_workers_scan_hw24_20260811.md | light_loader 线程数拐点扫描报告 |
| light_loader_bench_threshold_guide_20260811.md | light_loader 性能门禁阈值配置指南 |
| light_loader_package_install_20260811.md | light_loader 独立 pip 包安装说明 |
| light_loader_serial_parallel_bench_20260811.md | light_loader 串行 vs 并行性能基准报告（规模-耗时曲线） |
| light_loader_workers_scan_20260811.md | light_loader 线程数拐点扫描报告 |

## 关键指标趋势（静态，随基准重跑更新）

- 10000 卡串行耗时（最新实测）：1870.83ms（2026-08-11 硬件升级 24 核模拟扫描）
- 最佳并行线程数：8（= 默认 min(8, 卡片数)，无更优拐点）
- 并行收益：1.03~1.11x（页缓存命中 + GIL 约束，随规模趋缓）
- 硬件升级模拟（CPU 12→24）：24 线程 1741.77ms（1.07x），核心翻倍无额外收益（GIL 约束），默认线程数结论不变
- 环境兼容：Python 3.12.0 / PyYAML 6.0.3 / libyaml C 扩展可用
- 性能门禁阈值：基线 1944ms × 容差 1.5 = 2916ms（见 docs/reports/light_loader_bench_threshold_guide_20260811.md）

## 关联文档

- [安装说明](light_loader_package_install_20260811.md)
- [规模基准](light_loader_serial_parallel_bench_20260811.md)
- [线程数拐点](light_loader_workers_scan_20260811.md)
- [硬件升级模拟（24 核）](light_loader_workers_scan_hw24_20260811.md)
- [阈值配置指南](light_loader_bench_threshold_guide_20260811.md)

