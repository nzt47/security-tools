# PARALLEL S10 批次：S9 审核遗留待修项（6 件，可全部并行）

> 来源：[`../S9收尾审核与下一步_20260913.md`](../S9收尾审核与下一步_20260913.md) §二 的 P1/P2/P3。
> 基线：`master` = `bbc5d821`（双远端同点）。**P0 已由主线修完**（测试不再覆盖真实 `.env`）。
> 用法：每个任务从本文件取 worktree id 与关键词，然后复制它对应的提示词块到新会话。

| 任务 | 名称 | worktree id | 关键词（跑邻接回归用） | 预估 |
|---|---|---|---|---|
| **S10-01** | 工作流学习层准入门槛 + 存量脏工作流退役 | `s1001` | `workflow_learning` | 3–5 人日 |
| **S10-02** | judge runtime 接入生产灰度链路 | `s1002` | `judge shadow digestion` | 2–4 人日 |
| **S10-03** | 上下文预算口径 + 检索层归一化 | `s1003` | `context prompt loader retrieval` | 4–6 人日 |
| **S10-04** | judge `verdict` 口径对齐（规格级） | `s1004` | `judge verdict` | 2–3 人日 |
| **S10-05** | `tests/integration` 原生冲突定位（pyarrow） | `s1005` | 直接跑 `tests/integration` | 2–4 人日 |
| **S10-06** | worktree `.env` 自动链接（工具链，最小） | `s1006` | `worktree` | 0.5 人日 |

**依赖**：六者落点互不重叠，**可全部并行**。唯一弱关联：S10-02 与 S10-04 都动 judge 域，
但前者动"调用/注入"、后者动"verdict 口径"，**不碰同一函数**；若同期并行，请各自在报告中标注
"未与 S10-0X 联合验证"。

---

## 通用约定（六个任务都适用，不再在每个提示词里重复）

> **⚠️ 事后同步注记（2026-09-13）**：下面这段已**同步为最新正本**
> （[`通用约定_任务派发.md`](通用约定_任务派发.md)）。
> **本批次实际派发时用的是旧版**，两者差异（旧版缺 4 处、均为派发后实战教训）：
> 1. **没有**「新增 env 必须登记注册表」独立成节（旧版只在一行里提了一句）；
> 2. **没有**「测试隔离红线」（`CP_ENV_FILE` 重定向 + conftest 护栏）；
> 3. **没有**「报告中必须写清是否需要重启才生效」（S10 四个任务都记了这条遗留 ⇒ 共性缺口）；
> 4. 抖动清单**没有**"冷启动敏感"（实测：`test_killed_process_...` 第 1 次 35s 失败、后两次 3s 通过）。
> 保留此注记是为了**不篡改历史**：S10 各会话当年收到的确实是旧版。

```
【通用约定（任务派发通用）】

一、工作区与隔离（强制）
    cd C:\Users\Administrator\agent
    python scripts/dev/new_session_worktree.py create --id <见上表> --base master
  · --base 默认是 develop，**必须显式写 --base master**；id 必须 s<数字>。
  · 脚本会自动把主工作区 .env 供给到新 worktree（S10-06 已交付，实测输出
    「开箱即用: .env 已由本脚本自动提供」）；若你看到 .env 为空，手工从主工作区复制。
  · 此后所有 git 操作都在 worktree 内。主工作区禁令：✗ git checkout　✗ git reset --hard
    ✗ git add -A（只 add 具体文件）—— **主工作区可能同时有其它会话在活动**。
  · 提交流程：worktree 内 add/commit → 主工作区 `git merge <id>/main --no-edit` → 双远端 push
    （git push origin master && git push gitee master）。
  · 提交信息含中文/反引号时，**写进临时文件用 `git commit -F <文件> -- <具体路径>`**
    （PowerShell 会把多行中文提交信息拆坏，实测踩过多次）。

二、门禁四条（缺一不可）
  · 相关套件 + 邻接回归
  · python scripts/scan_kwarg_conflicts.py --path agent --min-risk HIGH  （0 处）
    python scripts/scan_kwarg_conflicts.py --path tests --min-risk HIGH  （0 处）
  · python -m mypy <改动模块>
  · lint-imports --config .importlinter   （2 kept / 0 broken）
  · 跑完 git status 检查产物漂移（pre-commit 会提示 clean-runtime-noise）。

三、新增 env 必须登记注册表
  任何新增的 env 读取点都要登记 agent/settings/registry.py，否则
  tests/unit/test_settings_registry.py::TestMechanicalZeroGap 零缺口硬守卫变红：
    _a = 可直接切（纯数值上限/超时/非安全开关）
    _b = 二次认证 + 双人确认（关掉完整性保护、放宽成本护栏、高危动作开关）
    _c = 只读脱敏（密钥/凭据/端点/**绝对路径**）
  owner 必须指向**真实文件**；默认值与类型必须取自真实读取点；风险级拿不准就**取更严**并把依据写进注释。
  该守卫曾有一个静默漏洞会吞掉读取点（2026-09-13 已修），**必须保持常绿** ——
  红着的守卫会掩盖新违规（实测：S9-01 新增的 2 个 env 就是藏在既有红灯里没被发现）。

四、测试隔离红线
  · 测试**不得**写仓库根 .env：EnvConfigManager 现支持 CP_ENV_FILE 覆盖目标文件，
    tests/conftest.py 已有 autouse 重定向 + session 级护栏（会校验 .env 的 LLM_API_KEY 未被改写）。
    新增涉及 .env 的测试请沿用该隔离，不要自行 mock 掉真实文件 I/O。
    （背景：曾有测试把真实 .env 的 LLM_API_KEY 覆盖成 sk-test-key，静默打坏在跑的服务。）
  · 需要落盘的用例必须显式传路径或 autouse 隔离，避免污染真实 data/。

五、纪律（均为实战踩过的）
  · **先查清再改**：若某个守卫/检查变绿了，必须验证"被检查的对象**没有从报告里消失**"——
    宁可留一条真实红灯，也不要一条假绿灯（曾出现两次：一次是真漏洞、一次是误改）。
  · **不得为了让用例通过而放宽断言**。若断言比意图更严，按**意图**改并**补正向断言**收紧。
  · **先怀疑自己的测量**：性能/时序类用例（<200ms / <1s）、依赖真实 HEAD 的用例、
    依赖进程 kill 时序的用例，在并发或**冷启动**下会假红 ⇒
    **先隔离复跑（含冷/热对比）再判断**，抖动不算缺陷。
  · **不得编造数字**：做不到就写"未验证"；样本 <20 只披露不考核；数据源缺位记 None 不以 0 冒充。
  · **不得用脚本造任务/造数据来刷指标**（会污染运营指标）。

六、环境事实
  · PowerShell 跑 Python 前设 $env:PYTHONIOENCODING='utf-8'; $env:PYTHONUTF8='1'。
  · 云枢服务在 127.0.0.1:5678 跑着（Get-NetTCPConnection -LocalPort 5678 取 PID）；
    改了 agent/** 要**重启服务**才生效，且重启属运维动作 ——
    请在报告里**明确写清"是否需要重启才生效"**（S10 四个任务都记了这条遗留，属共性缺口）。
  · 模型凭证可用（DeepSeek，.env 的 LLM_API_KEY，35 位）；真调模型会产生**费用**，
    除最小探针外请用注入桩并**标明是桩**。

七、回报格式
  交付物 / 验收逐条（含证据命令与原始输出）/ 根因（**代码行级**）/ 质量证据（套件与门禁结果）/
  遗留（带归属）/ 文档更新 / 双远端 SHA。
  口径纪律：**每个数字都能溯源**；区分"数字变化"与"口径变化"；口径变更必须显式声明变更点与影响面。
```

---

## S10-01 工作流学习层准入门槛 + 存量脏工作流退役

```
【任务】S10-01 — 工作流学习层准入与存量脏工作流退役
【背景】master = bbc5d821。真用期已开始，"工作流学习层"会**持续**把单轮交互学成工作流。
【现象（有实证）】
  data/learned_workflows.json 里的 `wf-f19dc52c` 由**单轮输入**自动学习而成，
  其 `trigger_patterns` 是**单字**：["列","出","当","前","工"]，并且已经被
  `convert_to_skill` 成 `wf-f19dc52c-skill`。
  ⇒ 单字触发词会**大面积误匹配**（任何含"出/当/前"的输入都可能命中），
     很可能就是早期"1+1 等于几"返回一整篇技能文档的诱因之一。
【目标】
  1. **准入门槛**：学成工作流/转成 skill 需满足可判定条件（如最少样本数、触发词长度与区分度、
     步骤数下限）；不达标者**不得**自动 convert_to_skill，或只留草稿态。
  2. **存量清理**：把已存在的脏工作流（单字触发词、单轮来源）**退役/隔离**，
     并保留可追溯记录（不改删台账、不伪造历史）。
  3. **可观测**：给出的数字必须可复算（存量条数、命中条件、退役前后对比）。
【要做的事】
  1. 先**固化现象**（不改代码）：写用例/脚本断言"单字触发词的工作流不得进入匹配候选"
     （当前应**失败**，作为回归锚）；并统计存量里有多少条命中该条件。
  2. 定位学习与转换链路：`agent/workflow_learning/`（matcher / repository / service /
     `convert_to_skill` 相关）+ `data/learned_workflows.json` 的写入方。
  3. 实现准入门槛（阈值要有依据；拿不准就**取更严**并把依据写进注释）。
  4. 存量退役（**只标记不删除**，并留审计；参考既有"归档不删证据"口径）。
  5. 复验：脏工作流不再进候选；正常工作流不受影响（附回归）。
【验收】
  · 准入门槛有机器可读用例；单字/单轮来源的工作流不再能自动转 skill；
  · 存量退役有清单与前后对比数字（可复算）；
  · 邻接回归 `pytest tests/unit -q -k "workflow_learning"` 全过；
  · 未动 `agent/digestion/`、`agent/orchestrator/orchestrator.py`（他人地盘）。
【陷阱】
  · `data/learned_workflows.json` 是**运行期数据**，pre-commit 有 `clean-runtime-noise` 钩子会
    还原"统计漂移"；真实改动要能解释清楚，别靠钩子放行。
  · **不得用脚本造任务来刷数据**（会污染运营指标）。
  · 学习阈值属**策略**：改动要写清"为什么是这些值"，不要只改数字。
【回报】交付物 / 验收逐条（含证据命令与原始输出）/ 根因（代码行级）/ 质量证据 / 遗留（带归属）/ 双远端 SHA
```

---

## S10-02 judge runtime 接入生产灰度链路

```
【任务】S10-02 — 把已就绪的 LLM-judge runtime 接入生产灰度链路
【背景】master = bbc5d821。S8-04 交付了完整 judge（三态可用性 / 凭证三级解析 / 每日预算 /
        UTC 计费 / 回落标注），**并已在真实凭证下验证可用**：
        `scripts/verify_judge_real_credentials.py --confirm-real-calls --samples 3`
        → `judge_kind = llm:deepseek:deepseek-v4-flash`、真实模型判断、tokens 真实计费；
        预算为 0 时如实回落 `deterministic_local(budget_exceeded)`。
【**核心缺口**】`build_judge_runtime` 在**生产代码中无调用点** —— 全仓仅"定义 + 导出"，
        `ShadowRunner(judge_runtime=)` **没有生产注入者**。
        ⇒ 灰度评测**仍在走确定性打分器**，"具备即用"≠"已接入"。
        （S9-02 遗留 W1-L3，明确标为"S8-04 遗留、本批未动"。）
【目标】把 judge runtime 注入灰度链路（`agent/digestion/shadow.py` 的 `ShadowRunner` 及
        其生产调用方），使灰度报告里的 `judge_kind` **如实反映实际所用判定器**：
  · 有凭证且未超预算 → `llm:<provider>:<model>`，且 judge 调用**计入 UTC**；
  · 无凭证 / 超预算 / 调用失败 → 回落确定性打分器并标注**具体原因**（沿用既有词表）。
【要做的事】
  1. 找到 `ShadowRunner` 的生产调用方（从 `agent/digestion/internalize.py` /
     `agent/digestion/service.py` / 灰度调度处往上找），确认注入点与生命周期。
  2. 在**不改变默认行为**的前提下接入：**默认仍是确定性打分器**（安全底线），
     只有 `CP_DIGESTION_JUDGE_ENABLED=true` 时才启用真实 judge（该 env 已登记为 B 级）。
  3. 端到端验证：跑一次小样本灰度，报告 `judge_kind` = `llm:...`，UTC 有增量；
     再关掉开关跑一次，`judge_kind` = `deterministic_local(...)` 且原因正确。
  4. 若发现"接入会改变既有灰度统计口径"，**必须先报告再动**（口径变更要显式声明，
     参考 `90d70fad` 那次"按意图收紧断言"的处理方式）。
【验收】
  · `build_judge_runtime` 有明确生产调用链（可用 AST/调用图给出证据）；
  · 开关关时行为与接入前**逐字节一致**（附对比）；
  · 开时 `judge_kind=llm:...` + UTC 增量；回落路径有证据；
  · 邻接回归 `pytest tests/unit -q -k "judge shadow digestion"` 全过。
【陷阱】
  · judge 的 base_url 走部署级 `LLM_BASE_URL`（S9-02 已实现），**不要另造端点概念**；
  · `deepseek-v4-flash` 是**推理模型**：`max_tokens` 过小会被 `reasoning_content` 吃光导致
    `content` 为空（实测 `max_tokens=8` 时 content 为空）——接入时若设 `max_tokens`，必须留足；
  · `agent/digestion/**` 与 S10-04 相邻（后者动 verdict 口径）——**不要顺手动对方的函数**。
【回报】同上
```

---

## S10-03 上下文预算口径 + 检索层归一化

```
【任务】S10-03 — 上下文预算告警口径 + 检索层融合分归一化
【背景】master = bbc5d821。两件都在"答案质量/读数可信"这条线上，故同一任务；落点不同文件。
【现象 A（上下文预算）】真机复验时 `response` **尾部被追加**一句告警，形如
        「当前会话上下文即将耗尽（已使用 27%/37%）」，且会话累计 token 仍超限。
        ⚠️ 注意别与已查明的另一件事混淆：`plugins/chat.py:298-327` 的 `context.percentage`
        是"**全局会话累计 ÷ 未配置的硬编码 4096**"的**显示公式**（见
        `docs/zh/真用前置_模型凭证核查_20260913.md` §八 D4），那**不是**真实窗口占用。
        本条要处理的是**真的往回复里追加了告警文本**这一行为。
  要做：查清该告警的产生条件与阈值来源（谁算的、用的哪个上限），
        让它**基于真实上限**、措辞准确，且**不得把告警文本混进正式回答**（或明确标注为系统提示）。
【现象 B（检索层）】
  · `agent/skills_mgmt/loader.py:1231`：RRF 融合分按 **rank-1 归一化** ⇒ top1 恒 ≈1.0；
  · `_RRF_QUALITY_MIN` 质量门（`loader.py:1512-1524`）把**无界 BM25 原始分**计入
    `max_raw_score` ⇒ **噪声级候选也能过闸**；实测 `query="2 加 3 等于多少？只回答数字"`
    时 `tfidf_score=0.1` 仍进入候选。
  要做：让质量门只认**有界**相似度（或对无界分做明确归一化），使低质候选被挡住。
【要做的事】
  1. **先固化现象**（不改代码）：加用例/脚本记录"低分候选进闸"与"告警文本出现在 response"，
     当前应**失败**（回归锚）。
  2. 分别定位：告警文本的产生点；RRF/质量门的计算与阈值来源。
  3. 修，并给出**改前/改后同输入对比**（同一批 query 的候选列表与分数）。
  4. 邻接回归：`pytest tests/unit -q -k "context prompt loader retrieval skills_mgmt"`。
【验收】
  · 告警基于真实上限、不污染正式回答（或显式标注）；给出口径说明；
  · 质量门能挡住实测的低分候选（附同输入前后对比）；
  · **检索类既有断言不得被放宽**：若某条断言按字面失效但意图仍成立，按意图改**并补正向断言**；
  · 门禁四条齐全。
【陷阱】
  · 改检索分会牵动多个既有检索评测用例（含已知 `xfail`：TF-IDF 基线 Precision@3=0.4444）；
    **不要为了让它们通过而调整分数**——先判断哪些是"真变好/真变坏"。
  · `loader.py` 是技能检索核心，改动要**保守**：优先"挡低质"而不是"重排高分"。
  · 本任务与 S10-02 无关；**不要动 judge**。
【回报】同上
```

---

## S10-04 judge `verdict` 口径对齐（规格级）

```
【任务】S10-04 — judge `verdict` 口径与 v7.2 §4.5 对齐
【背景】master = bbc5d821。S9-02 在 W1 发现并如实登记为遗留 W1-L1：
        **`verdict` 口径反向，属规格级问题**，需与设计文档 §4.5 对齐后统一改。
        （S9-02 已确认 `conflict` / `model_verdict` **确实被透出、未被吞掉**，
         但下游若只看 `verdict` 会读反。）
【要做的事】
  1. 读 `docs/.../CloudPivot_v7.2...md` §4.5 与 `agent/digestion/judge_runtime.py` 的
     `judge_consistency` / `JudgeVerdictStore` / `_manual_verdict_to_judge`，
     **逐字段**列出"规格要求 vs 当前实现"的对照表（含反向项的证据）。
  2. 明确影响面：全仓 `grep` 所有消费 `verdict` 的地方（报告/面板/统计/CLI/测试），
     列出"改口径会影响谁"。
  3. **先报告再改**：口径变更是"改变既有语义"，须在报告中显式声明变更点与影响面；
     若确认属于"实现写反了"，则修实现并同步所有消费方 + 更新用例。
  4. 若两处都有理（规格模糊），则给出**两种口径的定义 + 推荐项 + 理由**，
     并把决定权留给 Owner —— **不要自己拍板改规格**。
【验收】
  · 规格↔实现逐字段对照表（含证据行号）；
  · 消费方清单；
  · 若改了：改前/改后同输入对比 + 全部消费方同步 + 用例更新；
  · 邻接回归 `pytest tests/unit -q -k "judge verdict"` 全过。
【陷阱】
  · **不要**为了"让某条断言通过"而改口径 —— 口径是对外契约。
  · 与 S10-02 相邻（都动 judge 域）：S10-02 动"注入/调用"，本任务动"verdict 语义"；
    若同期并行，各自在报告里标注"未与对方联合验证"。
【回报】同上
```

---

## S10-05 `tests/integration` 原生冲突定位（pyarrow）

```
【任务】S10-05 — 定位 `tests/integration` 单进程无法跑完的原生冲突
【背景】master = bbc5d821。S9-02 如实登记 W2-L1：`tests/integration` 约 **80% 用例未验证**，
        因为 `pyarrow` **原生访问冲突**（疑似 C 扩展 DLL / 加载顺序）使**单进程跑不完**。
        此前另一次运行 exit=1 但**输出被管道过滤吃掉**，没拿到结论。
【要做的事】
  1. **把结论落到文件**（不要只靠管道）：
     python -m pytest tests/integration -q --tb=short -rf -p no:randomly --timeout=900 > <tmp>\s1005.txt 2>&1
     然后读文件尾部的 FAILED 列表与统计行。
  2. 复现并定位冲突：`pyarrow` 的导入与使用点、是否有并发/重复导入、
     DLL 搜索路径与加载顺序、是否与其它 C 扩展（如 numpy/torch/sentence_transformers）冲突。
     可尝试：`-p no:cacheprovider`、`--forked`（若可用）、分片运行、`-x` 首次崩溃即停。
  3. 给出**可复现的最小复现**（哪两个测试/哪个导入顺序必崩）。
  4. **分清"能修的"与"环境限制"**：
     · 若是测试隔离/加载顺序问题 → 修（如把冲突用例分片、或统一导入顺序）；
     · 若是环境/平台限制 → **如实标注**，并给出"分片运行方案"让 80% 未验证部分能跑上。
  5. 对每个 FAILED 做**逐条隔离归因**（真失败 / 负载抖动 / 环境受限），抖动要给
     "同批连跑 3 次的耗时对比"作定量证据（此前实测 8.94s vs 3.24s = 2.7×）。
【验收】
  · 有最小复现；有归类表（每条 FAILED 都有结论）；未跑到的**明确写"未验证"**；
  · 给出"现在能跑多少 / 还差多少 / 怎么把它们跑上"的可执行方案。
【陷阱】
  · **不要把超时/抖动当功能缺陷**，也不要把"没跑"写成"通过"；
  · 该任务**只动测试基础设施**，不要改产品语义；
  · 单进程全量可能耗时较长（此前 `tests/unit` 全量为 58 分钟），请用后台任务并落文件。
【回报】同上
```

---

## S10-06 worktree `.env` 自动链接（工具链，最小）

```
【任务】S10-06 — 新建 worktree 时自动提供主工作区的 `.env`
【背景】master = bbc5d821。S9-01 实测登记（遗留 #6）：
        `.worktrees/<id>/.env` 是 **0 字节**，而主工作区 `.env` 是 144741 字节
        ⇒ worktree 内启动的进程**没有 LLM 配置**，只能走离线响应，
          极易把"环境没配"误判成"功能不达标"。
【要做的事】
  1. 改 `scripts/dev/new_session_worktree.py`：`create` 成功后在 worktree 根
     **提供 `.env`**（优先尝试符号链接；Windows 无权限时**回退为复制**，
     并在输出里**明确告知是链接还是复制**、以及"复制不会自动跟随主工作区更新"）。
  2. **不得覆盖**已存在的 worktree `.env`；**不得**把 `.env` 带入 git
     （`.env` 已被 `.gitignore` 忽略，仍需在实现与用例中确认）。
  3. 加一条用例/自检：新建 worktree 后其 `.env` 非空，且 `git -C <worktree> status --short`
     不出现 `.env`。
  4. 在脚本输出里补一行提示（新会话开箱即用，不必手工复制）。
【验收】新建一个测试 worktree → `.env` 非空且不被 git 跟踪 → 用例通过 → 干净清理该测试 worktree。
【陷阱】不要改 `--base` 的默认值（默认 `develop` 是既有约定，各任务均显式传 `master`）；
        清理测试 worktree 时若遇 `Permission denied`（进程占用目录），
        先确保无进程占用再 `git worktree remove`，并把分支删除、worktree 注销。
【回报】同上
```
