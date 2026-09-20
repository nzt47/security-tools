# _t08_logs/ —— TASK-08 基准脚本的落盘目录

scripts/bench_router_complexity.py 等脚本的默认输出目录（见其 docstring 的用法示例）。

【2026-09-21 说明】本目录由主会话在清理临时文件时**误删**，导致随后一次全量回归在
收集期报 FileNotFoundError: 'C:\Users\Administrator\agent\_t08_logs'（瞬时报错，
重跑不复现——删除动作与 pytest 收集期的目录扫描发生竞态）。已重建以消除该竞态。

_walk_chain 的规模退化观测数据在 TASK-08 交付时曾落在此目录；
正式结论已归档到 docs/perf/可观测性实测.md 与 docs/perf/baseline.json（含
walk_chain_all_executors 指标），故此处的原始 JSON 属可再生的中间产物。
