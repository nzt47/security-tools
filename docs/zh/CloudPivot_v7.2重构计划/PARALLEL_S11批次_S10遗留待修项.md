# PARALLEL S11 批次：S10 审核遗留待修项（4 件，可全部并行）

> 来源：S10 六份交付报告里的「遗留」段（S10-01 §七 / S10-02 §六 / S10-03 §八 / S10-05 §10）+ 主线审核补充。
> 基线：`master` = `e1c1ac7f`（双远端同点）。

| 任务 | 名称 | worktree id | 关联遗留 |
|---|---|---|---|
| **S11-01** | 原生栈导入顺序提升到进程入口（防 arrow.dll 崩溃） | `s1101` | S10-05 #1 + #6 |
| **S11-02** | 工作流触发词政策收紧 + seed 脚本加固 | `s1102` | S10-01 政策漏洞 + #6 |
| **S11-03** | 读数可信性收口（context 块归属 / 告警可见性 / 三套上限口径） | `s1103` | S10-03 R3+R4+R5 |
| **S11-04** | judge `auto` 模式的预算护栏 | `s1104` | S10-02 L3 → **已交付**（[`S11-04_交付结案报告_20260914.md`](S11-04_交付结案报告_20260914.md)） |

**主线已完成（勿重复）**：`CP_ENV_FILE` 已补登注册表（`4e1f5b67`，守卫转绿）；服务已重启（S10-01/03/04 运行期生效，
真机复验三问全对、response 不再追加"上下文即将耗尽"）；脏工作流 4 条已是 `archived`。

---

## 通用约定（S11 四个任务都适用；**本文件自包含，不必再去找别的文件**）

> 本节与唯一正本 [`通用约定_任务派发.md`](通用约定_任务派发.md) 的「通用约定」段**逐字一致**；
> 若日后发现不一致，**以正本为准**并同步回来。改约定请改正本，再同步各批次内联段。

> 用法：每个新会话粘 **本「通用约定」整段** + **对应任务那一段**（本文件里每个 `## S11-0X` 标题下的代码块）。

```
【通用约定（S11 批次四个任务都适用）】

一、工作区与隔离（强制）
    cd C:\Users\Administrator\agent
    python scripts/dev/new_session_worktree.py create --id <s1101..s1104> --base master
  · --base 默认是 develop，**必须显式写 --base master**；id 必须 s<数字>。
  · 脚本会自动把主工作区 .env 供给到新 worktree（S10-06 已交付，实测输出
    「开箱即用: .env 已由本脚本自动提供」）；若你看到 .env 为空，手工从主工作区复制。
  · 此后所有 git 操作都在 worktree 内。主工作区禁令：✗ git checkout　✗ git reset --hard
    ✗ git add -A（只 add 具体文件）—— **主工作区可能同时有其它会话在活动**。
  · 提交流程：worktree 内 add/commit → 主工作区 `git merge <id>/main --no-edit` → 双远端 push
    （git push origin master && git push gitee master）。
  · 提交信息含中文/反引号时，**写进临时文件用 `git commit -F <文件> -- <具体路径>`**
    （PowerShell 会把多行中文提交信息拆坏，本轮又踩过一次）。

二、门禁四条（缺一不可）
  · 相关套件 + 邻接回归
  · python scripts/scan_kwarg_conflicts.py --path agent --min-risk HIGH  （0 处）
    python scripts/scan_kwarg_conflicts.py --path tests --min-risk HIGH  （0 处）
  · python -m mypy <改动模块>
  · lint-imports --config .importlinter   （2 kept / 0 broken）
  · 跑完 git status 检查产物漂移（pre-commit 会提示 clean-runtime-noise）。

三、新增 env 必须登记注册表（**本轮主线自己踩过这条**）
  任何新增的 env 读取点都要登记 agent/settings/registry.py，否则
  tests/unit/test_settings_registry.py::TestMechanicalZeroGap 零缺口硬守卫变红：
    _a = 可直接切（纯数值上限/超时/非安全开关）
    _b = 二次认证 + 双人确认（关掉完整性保护、放宽成本护栏、高危动作开关）
    _c = 只读脱敏（密钥/凭据/端点/**绝对路径**）
  owner 必须指向**真实文件**；默认值与类型必须取自真实读取点；风险级拿不准就**取更严**并把依据写进注释。
  该守卫现已修好（此前有个静默漏洞会吞掉读取点）且**必须保持常绿** —— 红着的守卫会掩盖新违规。

四、测试隔离红线
  · 测试**不得**写仓库根 .env：EnvConfigManager 现支持 CP_ENV_FILE 覆盖目标文件，
    tests/conftest.py 已有 autouse 重定向 + session 级护栏（会校验 .env 的 LLM_API_KEY 未被改写）。
    新增涉及 .env 的测试请沿用该隔离，不要自行 mock 掉真实文件 I/O。
  · 需要落盘的用例必须显式传路径或 autouse 隔离，避免污染真实 data/。

五、纪律（都是本轮实际踩过的）
  · **先查清再改**：若某个守卫/检查变绿了，必须验证"被检查的对象**没有从报告里消失**"——
    宁可留一条真实红灯，也不要一条假绿灯（本轮出现过两次：一次真漏洞、一次我自己的误改）。
  · **不得为了让用例通过而放宽断言**。若断言比意图更严，按**意图**改并**补正向断言**收紧。
  · **先怀疑自己的测量**：性能/时序类用例（<200ms / <1s）与依赖真实 HEAD、依赖进程 kill 时序的用例
    在并发或冷启动下会假红 ⇒ **先隔离复跑（含冷/热对比）再判断**，抖动不算缺陷。
  · **不得编造数字**：做不到就写"未验证"；样本 <20 只披露不考核；数据源缺位记 None 不以 0 冒充。
  · **不得用脚本造任务/造数据来刷指标**（会污染运营指标）。

六、环境事实
  · PowerShell 跑 Python 前设 $env:PYTHONIOENCODING='utf-8'; $env:PYTHONUTF8='1'。
  · 云枢服务在 127.0.0.1:5678 跑着（Get-NetTCPConnection -LocalPort 5678 取 PID）；
    改了 agent/** 要**重启服务**才生效，且重启属运维动作 —— 请在报告里写清"是否需要重启才生效"。
  · 模型凭证可用（DeepSeek，.env 的 LLM_API_KEY，35 位）；真调模型会产生**费用**，
    除最小探针外请用注入桩并**标明是桩**。

七、回报格式
  交付物 / 验收逐条（含证据命令与原始输出）/ 根因（**代码行级**）/ 质量证据（套件与门禁结果）/
  遗留（带归属）/ 文档更新 / 双远端 SHA。
  口径纪律：**每个数字都能溯源**；区分"数字变化"与"口径变化"；口径变更必须显式声明变更点与影响面。
```

---

## S11-01 原生栈导入顺序提升到进程入口（worktree `s1101`）

```
【任务】S11-01 — 把原生扩展预导入提升到进程入口，防 arrow.dll 打崩长寿命进程
【背景】master = 4e1f5b67。S10-05 交付 `tests/integration` 全量可跑（2106 条 0 未验证），
        其修复手段是 `tests/integration/conftest.py::_pin_native_import_order()`
        —— **固化原生扩展导入顺序**，规避 `arrow.dll` 的 0xC0000005 访问冲突。
【为什么还不够（S10-05 遗留 #1，产品侧同源风险）】
        该预导入只在 `agent/orchestrator/lifecycle_manager.py:118` 附近（`DigitalLife` 构造时）
        才发生。**长寿命进程若在加载大量原生库之后才首次**
        `import sentence_transformers`（→sklearn→pandas→pyarrow），**仍可能被 arrow.dll 打崩**。
        ⇒ 生产进程（app_server）缺同一道保护。
【要做的事】
  1. 先**固化现象**：在本机复现"先加载 torch/onnxruntime 等原生库、再 import pyarrow/sentence_transformers"
     的崩溃（给出可复现命令与退出码；若本机复现不出，如实写明并给出 S10-05 的证据引用）。
  2. 把原生栈预导入**提到进程入口最前**：`app_server.py` 顶部（在任何其它 `import agent.*` 之前），
     或在启动 bootstrap 里；**与 `_pin_native_import_order()` 复用同一实现**（不要复制一份）。
  3. 保持**可关闭**（若某环境预导入反而更慢或有副作用）：用 env 开关，**并登记注册表**
     （按语义选 `_a`/`_b`/`_c`；默认值必须等于"与现状等价"）。
  4. 同时把该保护**提升到 `tests/conftest.py`**（S10-05 遗留 #6：`tests/unit` 历史上也崩过）
     —— 让 unit/integration 共用同一道保护；确认不拖慢测试启动（给前后耗时对比）。
【验收】
  · 复现证据（或如实写明本机复现不出 + 引用 S10-05 证据）；
  · `app_server.py` 启动后原生栈已按固定顺序加载（附可执行的验证命令与输出）；
  · `tests/unit` 与 `tests/integration` 共用保护，且有耗时对比；
  · 新增 env（若有）已登记注册表，零缺口守卫转绿；
  · 邻接回归：`pytest tests/unit -q -k "lifecycle or app or startup"`。
【陷阱】
  · 预导入放最前可能改变启动耗时与日志顺序 ⇒ 用**实测数字**说明，不要只说"应该更快"；
  · 不要在 `app_server.py` 里 import 会触发副作用（起服务/连库）的模块；
  · 本任务与 S11-02/03/04 无交集。
【回报】交付物 / 验收逐条（含证据命令与原始输出）/ 根因（代码行级）/ 质量证据 / 遗留（带归属）/ 双远端 SHA
```

---

## S11-02 工作流触发词政策收紧 + seed 脚本加固（worktree `s1102`）

```
【任务】S11-02 — 收紧工作流触发词政策（单字触发词）+ 加固造数据脚本
【背景】master = 4e1f5b67。S10-01 已交付准入门槛（`MIN_STEPS=2`、`MIN_TRIGGER_CHARS=2`）
        与存量退役（4 条已 archived）。
【漏洞（主线审核发现，需你确认并修）】
        `data/learned_workflows.json` 里 `json-30a189b6` 被判为 **clean 且仍 active**，
        但它的 `trigger_patterns = ['读','取','json','配','置']` —— **4 个单字** + 1 个 "json"。
        当前政策显然只要求"**存在**至少一个 ≥2 字符的触发词"即可通过；
        而匹配若按"**任一**触发词命中"（请先确认 matcher 语义），
        那么"读/取/配/置"这些单字仍会**大面积误触发** ⇒ 政策没有真正堵住风险。
【要做的事】
  1. **先查清 matcher 语义**：命中判定是"任一触发词"还是"全部触发词"？
     （`agent/workflow_learning/matcher.py`）—— 这决定政策该怎么写。
  2. 按语义收紧政策：若为"任一"，则**单字触发词一律不得进候选**（或整条判 dirty）；
     若为"全部"，则给出为什么现有政策足够的证据。
     ⚠️ 改政策属**策略变更**：必须写清"为什么是这个阈值"，并给出**改前/改后同输入的候选对比**。
  3. 复查存量：用新政策重跑 `scripts/retire_dirty_workflows.py`（先 dry-run 再 `--apply`），
     列出**新增**被判 dirty 的条目（可复算）。
  4. **加固 `scripts/seed_demo_workflows.py`**（S10-01 遗留 #6）：它直连**生产仓库**
     `WorkflowLearningService()` 并以 `demo-seed` 造 3 条演示数据（现存 3 条 demo-seed 就来自它）。
     ⇒ 要求 `--repo` **显式必填**（或默认只 dry-run），并加 `--dry-run`；
     目的是铲除"脚本刷真实数据"的风险点（违反"不用脚本造任务刷指标"的纪律）。
【验收】
  · matcher 语义有代码级证据；
  · 政策变更有依据 + 前后对比（同一批输入）；
  · dry-run → `--apply` 的存量清单（含新增 dirty 条目）；
  · `seed_demo_workflows.py` 不再能默认写生产仓库（附尝试命令与输出）；
  · 邻接回归 `pytest tests/unit -q -k "workflow_learning"` 全过。
【陷阱】
  · `data/learned_workflows.json` 是运行期数据，pre-commit 的 `clean-runtime-noise` 会还原"统计漂移"；
    真实改动要能解释；**不得用脚本造数据**。
  · 不要改 `learner._WORD_RE` 的中文按字切分（S10-01 #3：改它会把"无区分度触发词"的判定条件本身消解掉）；
    若确要提升中文分词质量，属学习质量专项，请单列。
【回报】交付物 / 验收逐条（含证据命令与原始输出）/ 根因（代码行级）/ 质量证据 / 遗留（带归属）/ 双远端 SHA
```

---

## S11-03 读数可信性收口（worktree `s1103`）

```
【任务】S11-03 — 上下文/读数口径统一（context 块归属 + 告警可见性 + 三套上限）
【背景】master = 4e1f5b67。S10-03 已让「上下文即将耗尽」告警**不再混进正式回答**，
        改为走 `metadata.context_notice`（口径统一到"真实窗口上限 + 来源披露"）。
        主线已重启服务并真机复验：response 里确实不再追加该文本 ✅。
【三件遗留（S10-03 R3/R4/R5），都在读数可信这条线上】
  · **R3** `plugins/chat.py:298-330` 的 `context` 块仍是老口径：
    `token_limit` 取 `_cfg.get("memory","token_limit", default=4096)`（**config.yaml 根本没配这个键**，
    分母恒为硬编码 4096）；`_session_total` 取 `_get_current_session_id()`（**全局会话**，
    而同一 handler 在 `:149` 是按**请求**解析 `session_id` 的）
    ⇒ A 会话聊天可能报 B 会话的数字；`percentage` 用几轮必然 >100%。
  · **R4** 三处 token 上限**三套默认值**：`memory/memory_manager.py:286` 压缩阈值 4096 /
    `lifecycle_manager.py:288` 编排窗口 131072 / `plugins/chat.py` 显示 4096。
    ⚠️ **改这个会显著减少压缩次数，属策略决定 —— 需 Owner 裁定，不要自己拍板。**
  · **R5** `plugins/chat.py` **不转发 `metadata`** ⇒ 新口径的 `context_notice` 在 Web 端**根本看不到**，
    等于"修好了但没人看得见"。已检索确认 `tests/**`、`scripts/**` 无对该文案的断言。
【要做的事】
  1. R3：`context` 块切到**请求指定的会话** + 披露**真实上限**（编排窗口）+
     回显**实际使用的 `session_id`**（让调用方能自查）；保留 `percentage` 但明确其分母语义，
     或直接标注为"累计占比（非窗口占用）"。
  2. R5：把 `metadata`（至少 `context_notice`）转发进 HTTP 响应；补一条断言锁死"告警不出现在 `response` 正文、
     但出现在结构化字段里"。
  3. R4：**只做调查与建议**，给出"若把压缩阈值对齐到编排窗口会发生什么"的量化影响
     （压缩次数、token、成本），把结论交 Owner；**不要改阈值**。
【验收】
  · 同一响应里的所有 token 读数**分母一致且可解释**，并注明各自来源；
  · `context` 块与请求会话一致（两个不同 `session_id` 交替请求可验证）；
  · 告警在结构化字段里可见、正文里不可见（用例锁死）；
  · R4 给出量化影响与建议（不改阈值）；
  · 邻接回归 `pytest tests/unit -q -k "context prompt chat or settings"`。
【陷阱】
  · 改 `context` 块字段语义属**对外接口变更** ⇒ 必须显式声明；既有消费方（前端/脚本）要在报告里列清；
  · `session_total_tokens` 现在取全局会话，改成请求会话会**改变数字大小**（不是 bug 修复的副作用，
    而是口径修正）——要在报告里区分"数字变化"与"口径变化"。
【回报】交付物 / 验收逐条（含证据命令与原始输出）/ 根因（代码行级）/ 质量证据 / 遗留（带归属）/ 双远端 SHA
```

---

## S11-04 judge `auto` 模式的预算护栏（worktree `s1104`）— ✅ 已交付（2026-09-14）

> 结案：**现象真实、非误报，但被本部署配置掩盖**（`auto` 的回落原因是"未配
> `CP_DIGESTION_JUDGE_PROVIDER/_MODEL`"，不是"开关关"；补上这两个 **`_a`** 键即
> 直选真实通道且无预算上限、无成本记账）。修复后：**关着一定不花钱**，且入选链
> 选中的真实通道受 `CP_DIGESTION_JUDGE_DAILY_BUDGET_CENTS` 约束。
> **口径变更已声明**（§四）：仅影响"本来会开跑"的配置；本部署的报告**逐字不变**。
> 证据：现象固化脚本四场景对比 + **一次**真实最小探针（0.306 cents）+ 新增 16 例 +
> `-k "judge or shadow"` **406 passed** + 邻接 **2857 passed** + 门禁四条全绿。
> 详见 [`S11-04_交付结案报告_20260914.md`](S11-04_交付结案报告_20260914.md)。

```
【任务】S11-04 — 关闭开关时 `resolve_judge("auto")` 仍可能选中**无预算护栏**的 LLM 通道
【背景】master = 4e1f5b67。S10-02 遗留 **L3**：
        既有 `resolve_judge("auto")` 在开关关闭时**仍可能选中 LLM 通道**，
        而该通道**没有预算护栏**（`JudgeBudget` 是在 `build_judge_runtime` 那条链上）。
        S10-02 **刻意未改**（"改即口径变更"），并实测确认语义未变。
【风险】"默认关闭"是安全底线；若 `auto` 仍能走到真实模型调用，则：
  · 会产生**未受每日预算约束**的成本；
  · 与"有凭证/超预算时回落并如实标注"的设计意图冲突。
【要做的事】
  1. 先**固化现象**：给出"开关关闭 + auto 模式"下真的走到 LLM 通道的**可复现证据**
     （哪条调用路径、`judge_kind` 实际取值、是否产生成本）。
     —— 若实测**证明走不到**（即 auto 在关时必然回落），那本条应作为"**已澄清的误报**"结案，
        并补一条用例锁死"开关关 ⇒ 必为 deterministic_local"。
  2. 若确实能走到：把**预算护栏与回落判定**前移到所有入选路径（不只 `build_judge_runtime` 那条），
     使任何走到 LLM-judge 的调用都受 `CP_DIGESTION_JUDGE_DAILY_BUDGET_CENTS` 约束；
     并在超预算时**如实标注回落原因**（沿用既有词表）。
  3. 口径变更**必须先声明**（这会把"关着也可能花钱"变成"关着一定不花钱"）——
     在报告里显式写清变更点与影响面。
【验收】
  · 现象证据（或"已澄清为误报"的证据 + 新的锁定用例）；
  · 若改：改前/改后同输入对比 + `judge_kind` 取值表 + 成本影响；
  · 开关关时**必不产生**真实模型调用（用例锁死）；
  · 邻接回归 `pytest tests/unit -q -k "judge or shadow"` 全过。
【陷阱】
  · 不要动 `verdict` 语义（S10-04 已定稿，属对外契约）；
  · 真实调用会产生费用：除**一次**最小探针外，其余用注入桩，并在报告里**标明是桩**；
  · 与 `data/` 台账交互时注意测试隔离（`CP_ENV_FILE` 已可覆盖 `.env` 目标；
    测试**不得**写仓库根 `.env` —— 该守卫已在 `tests/conftest.py` 生效）。
【回报】交付物 / 验收逐条（含证据命令与原始输出）/ 根因（代码行级）/ 质量证据 / 遗留（带归属）/ 双远端 SHA
```

---

## 需 Owner 裁定（不派发，等决定）

| # | 事项 | 为什么要你定 |
|---|---|---|
| 1 | **三套 token 上限口径**（S10-03 R4）：压缩阈值 4096 / 编排窗口 131072 / 显示 4096 | 把压缩阈值对齐到 131072 会**显著减少压缩次数**（也直接影响真机"压缩 5 次"的触发频率）——这是策略与成本的取舍，不是 bug |
| 2 | **BM25-only 召回面收窄**（S10-03 R1）：现改为"只认有界相似度"，纯 BM25 专有名词召回会被拒 | 若业务上需要纯 BM25 召回，**得为 BM25 单独标定归一化映射与阈值**（S10-03 明确不发明这个常量）；要不要做、阈值多少属你的判断 |
| 3 | **是否下线两个脏技能产物**（S10-01 #2）：`wf-f19dc52c-skill`（现 `status=draft`，惰性）、`zip-d2968c59-skill` | 目前是 draft、检索不加载，所以**不紧急**；但要不要走 `deprecate` 正式下线，属产品数据决策 |
| 4 | **「kill 后丢 1 条」是否可接受**（见下方主线审核补充 #2） | 若属异步刷盘固有语义 ⇒ 应改用例（先等 flush 再 kill）；若要 kill-safe ⇒ 得改产品落盘。**持久性口径**，不宜由实现者自定 |

---

## 附：主线审核补充 —— `tests/unit` 全量结果归因（2026-09-13）

**运行**：`python -m pytest tests/unit -q -p no:randomly --timeout=600`（`s1010` worktree，基点含 `CP_ENV_FILE` 补登）
⇒ **2 failed / 18060 passed / 310 skipped / 13 xfailed / 4 xpassed，57m30s**

| # | 用例 | 归类 | 证据 |
|---|---|---|---|
| 1 | `test_s6_01_ui_panels::TestPerformanceBudget::test_pipeline_aggregate_under_200ms` | **负载/预热抖动**（二次确认） | 隔离复跑 3 次全过；同批 39 例此前首跑 8.94s vs 后两次 3.24s（2.7×） |
| 2 | `test_concurrency_multi_writer::test_killed_process_does_not_deadlock_next_writer` | **首次冷跑敏感（新发现）** | 隔离复跑：第 1 次 **1 failed / 35.19s**；第 2、3 次 **4 passed / 3.95s、2.56s** |

**第 2 条的失败内容**：`assert len(rows) == 6` → 实际 **5**（"恢复+续写后应有 6 条"）。

**⚠️ 这不只是"测试抖动"，背后有个该单列的问题**：被 kill 的写者**在途记录会不会丢**？
若该记录仍在**异步 writer 队列**未落盘，SIGKILL 后丢失属**异步刷盘的固有语义** ——
那么用例应当"**先等 flush 再 kill**"（否则它测的是时序而非恢复能力）；
若认为不该丢，则是**产品持久性口径**问题（需 kill-safe 落盘）。**本次未改**，已列入上方待裁定 #4。

**另一条好消息**：S10-05 提示的"`tests/unit` 历史上也有 `arrow.dll` 崩溃（其遗留 #6）"**本轮未复现**
（57m30s 跑完、18060 passed）⇒ **S11-01 维持"加固"定位，不是阻塞性必修**。

