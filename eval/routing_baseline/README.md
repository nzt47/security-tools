# eval/routing_baseline/ —— TASK-10 路由可验证化基线（数据目录）

> 本目录遵循 `eval/README.md` 的**只放数据**约定：执行代码在
> `scripts/run_routing_reliability.py`，本目录只放用例集与机器生成的报告。

## 文件

| 文件 | 性质 | 说明 |
|---|---|---|
| `cases.json` | 人工录入的**数据**（含逐条来源） | 76 条标注用例；每条的 `text/gold` 抄自仓库内既有文件，`source`+  `anchor` 记录出处 |
| `report.json` | **机器生成** | 全部中间读数（每层指标、抽样结果、可靠性表、阈值曲线、findings） |
| `REPORT.md` | **机器生成** | 人读版报告（由 `report.json` 渲染） |
| `README.md` | 本文件 | 口径与复现方式 |

> `report.json` / `REPORT.md` **不要手工编辑**：它们每次由脚本整体重写，
> 手工改动会在下一次运行时丢失，且会制造第二真相源。

## 复现

```powershell
python scripts/run_routing_reliability.py            # 重新生成报告
python scripts/run_routing_reliability.py --print-md # 顺便打印
```

回归守卫：`python -m pytest tests/unit/test_routing_reliability.py -q -p no:randomly`

## 口径（读报告前必看）

1. **本任务只增加观测，不改任何路由判定语义**。脚本只调用
   `WorkflowEngine.try_match` / `IntentRouter.classify` / `classify_user_input` /
   `HybridRetriever.query`，全部是只读调用；不 register 新规则、不改阈值、不改 alpha。
2. **不训练任何模型**。温度缩放只拟合 1 个标量 T（黄金分割搜索最小化 NLL），
   不训练分类头、不微调、不引入共形预测 / PASC。
3. **不新增第三方依赖**（仅标准库；numpy 只做可用性登记），**不新增环境变量开关**。
4. **来源可核**：`cases.json` 的每条都有 `source` + `anchor`；
   `tests/unit/test_routing_reliability.py` 会逐条断言 anchor 仍在来源文件中存在，
   防止"来源漂移"（改了上游测试后本用例集静默失真）。
5. **类目表是机器派生的**，不是臆造的：11 类来自 `agent/tool_router.py:308 TOOL_CATEGORIES`
   （YAML 存在时由 `:238` 从 `data/tool_definitions/*.yaml` 派生）。

## 已知边界（详见 `REPORT.md` 第 8 节）

- 规则层 / 模板层用例抄自这两个模块**自己的单测**（关键词 / 正则即规则定义）
  ⇒ 这两层的准确率是**同分布上界**，不是对真实流量的泛化估计。
- 仓库内**不存在**留出的路由标注集（已勘察 `eval/**`、`data/evals/**`、`tests/**`）。
- 本环境 Embedding 不可用 ⇒ 语义层读数描述 **BM25-only 降级路**。
- 标注样本量 ≤ 80 ⇒ 温度参数 T 与 ECE 只能当**占位基线**。
