# LLM 禁用时段（计费高峰停止 LLM 请求）

> 一句话：北京时间默认**工作日 09:00-12:00、14:00-18:00**（中国法定节假日除外）内，
> 云枢**所有** LLM 请求一律拒绝（含用户对话）；其余时段放行。
> 时段可在**工作台顶栏**的开关 / 配置弹窗里自由增删。
>
> 实现：agent/llm_idle_gate.py；API：/api/llm-gate；顶栏组件：
> yunshu-ui/src/components/workbench/LlmGateControl.tsx；
> 定时任务：LLM 禁用时段（每 10 分钟，零 token）。

## 1. 判定口径

| 时段（北京时间） | LLM 请求 |
|---|---|
| 周一至周五 09:00-12:00、14:00-18:00（默认禁用时段） | 拒绝（计费高峰） |
| 工作日 12:00-14:00、18:00-次日 09:00 | 放行（计费空闲） |
| 周六、周日全天 | 放行 |
| 中国法定节假日全天（skip_holidays=true） | 放行 |

- 时区固定 **UTC+8**（中国无夏令时），不依赖服务器时区。
- 边界左闭右开：11:59:59 仍拦截，12:00:00 起放行；17:59:59 仍拦截，18:00:00 起放行。
- "周末/节假日"是否算空闲由配置项 skip_holidays 控制（默认 true）。
- 默认时段只是初始值：用户在顶栏弹窗里改完即落盘生效（不用重启）。

## 2. 顶栏开关与配置

工作台顶栏（报警器左侧）新增：

- Ban 图标 + Switch：总开关（开 = 禁用时段生效；关 = 全部放行）。
- 齿轮按钮：打开配置弹窗，可编辑：
  - 是否启用；
  - 法定节假日是否不拦截；
  - 提前收尾分钟数（默认 5 分钟）；
  - 任意条禁用时段（每条 = 星期多选 + 起止时刻）。
- 写入 data/llm_gate_config.json（运行期状态，不提交）；GET/POST /api/llm-gate。
- 顶栏读数失败时开关禁用并显示"闸门不可用"，不假装已开启。

## 3. 提前收尾与任务顺延（Owner 第三点）

目标：任务不要在禁用时段边界被"做一半掐断"。

- 注入时间预算：agent/tool_calling.py 在任务开始时把
  「LLM 将在 HH:MM 停止（剩余 N 分钟）」写进 system prompt，让模型**一开始就规划步骤**。
- 临近收尾：距禁用时段 <= lead_seconds 时，在下一轮 LLM 调用前插入
  「立即收尾」指令，并**停止发起新的工具调用**，基于已有信息给出结论。
- 定时任务顺延：可能调用 LLM 的任务（zero_token 不为 true）在
  「已在禁用时段」或「剩余时间 < lead_seconds」时**不启动**，状态记为 skipped，
  原因写进任务历史（"距 LLM 禁用时段不足提前量，本轮顺延"）。
  零 token 任务不受影响。

## 4. 拦截点与边界

拦截装在 SDK 类方法上，一次覆盖进程内所有 OpenAI 兼容 / Anthropic 客户端：

| 入口 | 位置 |
|---|---|
| OpenAI chat.completions.create（同步/异步） | agent/llm_idle_gate.py::install |
| Anthropic messages.create（同步/异步） | 同上 |
| 用户对话（非流式） | plugins/chat.py::api_chat（403 + blocked: true） |
| 用户对话（SSE） | plugins/chat.py::api_chat_stream（type=error + type=done） |
| LLMService / ToolCallingService | memory/llm_service.py、agent/tool_calling.py（快速失败，避免重试放大） |

**明确不拦**（不会产生 LLM API 计费）：

- 本地 embedding / rerank（SentenceTransformer / Cross-Encoder）；
- LLMResponseCache 命中（没有发请求）；
- 直接 requests.post 打 LLM 端点（当前主链路无此调用；新增时需另接闸门）。

**边界：只拦"新发起"的请求**。切换点之前已经在途的请求不会被强制掐断
（避免半截回复/流式数据损坏）；切换之后不会再放行任何新请求。

## 5. 开关与配置项

| 项 | 说明 |
|---|---|
| 顶栏 Switch | 运行期总开关（写 data/llm_gate_config.json） |
| LLM_IDLE_GATE_ENABLED | 仅当配置文件不存在时的初始 enabled（默认 0；本机 .env 置 1） |
| LLM_GATE_CONFIG_FILE | 配置文件路径覆盖（默认 data/llm_gate_config.json） |
| lead_seconds | 提前量（默认 300 秒；弹窗里以分钟展示） |
| skip_holidays | 法定节假日是否不拦截（默认 true） |

> ⚠️ 停用「定时任务」页里的 LLM 禁用时段任务**不会**解除拦截：
> 它只是状态巡检/留痕，真正的闸门在每次 LLM 调用时判定。
> 要临时放行，用顶栏开关（即时生效）。

## 6. 节假日表维护

agent/llm_idle_gate.py 的 HOLIDAY_RANGES 已登记 2025 / 2026 / 2027，
数据源为国务院办公厅《关于 20XX 年部分节假日安排的通知》。

- 新一年通知发布后（通常 11 月），追加该年放假区间并更新 COVERED_YEARS；
- 未覆盖年份 fail-open：按"非节假日"处理并每年告警一次；
- 只登记放假日期；周末本来就是空闲时段。

## 7. 验证

    # 状态与配置（不触发任何网络请求）
    python -c "from agent.llm_idle_gate import state, get_config; import json; print(json.dumps({'config': get_config(), 'state': state()}, ensure_ascii=False, indent=2))"

    # 单元测试（含 API / 时段 / 顺延 / SDK 拦截）
    python -m pytest tests/unit/test_llm_idle_gate.py -q
