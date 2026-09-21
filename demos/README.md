# demos/ —— 演示与验证脚本（受跟踪目录）

> 建立于 2026-09-21（根目录卫生 TASK-02 复核后的返工，任务 A）。
> 关联台账：`docs/closeout/REPO_HYGIENE_W1_TASK02_20260921.md` §8。

## 为什么有这个目录

这 7 个脚本原先散落在**仓库根目录**。TASK-02 一度把它们移进 `_scratch/`，
但 `_scratch/` 被 `.gitignore` 忽略，于是同时造成两件事：

1. 文件**脱离版本跟踪**（新克隆拿不到）；
2. 文档/注释/夹具说明里的**引用失效**。

⇒ 正确做法是移入**受跟踪的、未被忽略的**目录（即本目录），并同步引用。
根目录 `.py` 因此稳定在 10 个（`scripts/check_root_hygiene.py` 默认阈值 exit 0）。

## 怎么用

**一律在仓库根执行**（脚本内已把仓库根加入 `sys.path`，不依赖 CWD）：

```bash
python demos/run_evolution_demo.py             # offline_evolver 批量进化链路演示
python demos/gen_mock_data.py                  # 生成 100 条 Git 分支状态模拟数据（写到 **CWD**）
python demos/generate_guard_json_example.py    # 生成 docs/guard_result_example.json（路径 **CWD 相对** ⇒ 须在仓库根跑）
python demos/demo_prometheus_export.py         # 业务指标 Prometheus 格式导出示例
python demos/demo_full_stack.py                # 全链路回溯与版本对比演示
python demos/demo_production_deployment.py     # prompt 版本管理生产部署演示
```

## 文件清单与迁移对照（根目录 → demos/）

| 文件 | 用途 | 迁移方式 |
|---|---|---|
| `demo_full_stack.py` | 全链路回溯 / 版本对比演示 | `git mv`（保留历史） |
| `demo_production_deployment.py` | prompt 版本管理生产部署演示 | `git mv` |
| `demo_prometheus_export.py` | 业务指标 Prometheus 导出示例 | `git mv` |
| `gen_mock_data.py` | 生成 `mock_git_states.json`（100 条边界数据） | `git mv` |
| `generate_guard_json_example.py` | 生成 `docs/guard_result_example.json` 夹具 | `git mv` |
| `run_evolution_demo.py` | OfflineEvolver 批量进化演示；**被 `verify_budget_break.py` 同目录 import** | `git mv` |
| `verify_budget_break.py` | 预算熔断（EVO-T3）验证脚本；import 上者 | 普通移动（见下） |

### 关于 `verify_budget_break.py`

它在根目录时是**未跟踪 + 被忽略**的（`.gitignore` 的 `/verify_*.py`，仅匹配根目录），
因此 `git mv` 会报 `fatal: not under version control`。移入本目录后**不再命中该规则**，
但其是否纳入版本控制**尚未决定**（现为 `??` 未跟踪状态，未 `git add`）。

## 已知问题（既有缺陷，非本次迁移引入）

> ✅ **【L30 已修复·2026-09-21】** 下述 TypeError 已修：根因是 `run_evolution_demo.py` 的
> `MockEnhancer` 丢失了 `OfflineEvolver` 要求的鸭子类型成员（`set_lineage_hook` /
> `lineage_archive`+ `_get_lineage_archive` / `bump_version(eval_result=…)` / 钩子触发），
> 已按 `tests/unit/test_evolution_loop.py::_StubEnhancer` 对齐补回（**非**迁移引入，见下文）。
> 修复后实测：`python demos/verify_budget_break.py` **exit 0**（验证全绿）；
> `python demos/run_evolution_demo.py` 在同一次修复后于 **UTF-8 控制台** exit 0，
> 但在 **GBK(cp936) 控制台**仍会因 `print_report` 打印 "✓" 报
> `UnicodeEncodeError: 'gbk' codec can't encode character '\u2713'` —— 这是**另一项**
> 既存缺陷（该文件缺少 `verify_budget_break.py:124-128` 已有的
> `sys.stdout.reconfigure(encoding="utf-8")` 兜底），本次未改动。下文保留原文记录。

`python demos/verify_budget_break.py` 曾会以
`TypeError: MockEnhancer.__init__() got an unexpected keyword argument 'lineage_archive'` 退出：
`verify_budget_break.py` 传 `lineage_archive=`，而 `run_evolution_demo.py` 的 `MockEnhancer`
签名是 `def __init__(self):`。`git show HEAD:run_evolution_demo.py` 签名相同，且已在
"迁移前布局"下复现同一错误 ⇒ 与目录迁移无关。成因见
`scripts/generate_lineage_demo_data.py:4`（`run_evolution_demo.py` 曾被并行会话覆盖回旧版，
`MockEnhancer` 丢失 `set_lineage_hook`）。**import 本身是通的**（`from run_evolution_demo import ...` 成功）。
