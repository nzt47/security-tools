# START-S9-02 收尾批次（S8-04 真跑 judge + 跨目录套件归因 + 未合并分支复核）

> **本文件不是任务书。** 本任务不需要先读别的文档 —— §二 就是**自包含的启动提示词**，整段复制给新会话即可。
> 生成：2026-09-13｜基线：`master` = `163d1281`（双远端同点）｜预估：1–2 人日

---

## 二、启动提示词（整段复制给新会话）

```
【任务】S9-02 收尾批次 —— 三件互不重叠的收尾工作（可按 W1→W2→W3 顺序做，也可只做其中一件）
【背景】master = 163d1281（github/gitee 同点）。S8-01~05 已全部合入并已通过一轮全量审核；
        S9-01（对话编排答非所问）由另一会话在做，**不要碰 orchestrator / plugins/chat.py / routes_chat.py**。

━━━ 一、工作区与隔离（强制）━━━
    cd C:\Users\Administrator\agent
    python scripts/dev/new_session_worktree.py create --id s912 --base master
此后所有 git 操作在 .worktrees/s912/ 内。主工作区禁令：✗ git checkout ✗ git reset --hard ✗ git add -A
（只 `git add <具体文件>`；主工作区可能有**其它会话**在活动，绝不 add -A）
提交流程：worktree 内 add/commit → 主工作区 `git merge s912/main --no-edit` → 双远端 `git push origin master && git push gitee master`
提交信息若含中文/反引号，**写进临时文件用 `git commit -F <文件> -- <具体路径>`**（PowerShell 引号会把多行中文提交信息拆坏，实测踩过）。

━━━ 二、W1：S8-04 在**真实凭证**下跑一次 judge 小样本（体积最小、收益最高，建议先做）━━━
【为什么】S8 批次出口条件第 6 条（唯一未闭环）："LLM-judge 有凭证时真跑并计入 UTC；
        无凭证/超预算时回落并如实标注 judge_kind"。目前只交付了"具备即用"，
        从未在真凭证下跑过一次。

【已查清的事实（别重新推导）】
  · 可用凭证：`.env` 的 `LLM_API_KEY`（DeepSeek，35 位，已验证可用）；
    `LLM_PROVIDER=DeepSeek` `LLM_MODEL=deepseek-v4-flash` `LLM_BASE_URL=https://api.deepseek.com/v1`。
  · 已装 SDK：只有 `openai` 与 `anthropic`（`google.generativeai`/`zhipuai`/`dashscope` 都缺失）。
  · `agent/model_router/adapters.py::ModelAdapterFactory.create(provider, model, **kw)`：
    只认 `openai|claude|gemini|zhipu|qwen`；**不认 `deepseek`**。
    但 `openai` 分支是 `OpenAIAdapter(model, kwargs.get("api_key"), kwargs.get("base_url"))`
    ⇒ **工厂本来就支持 base_url**（OpenAI 兼容端点可用）。
  · `agent/digestion/shadow.py::LLMJudge._adapter_kwargs()` 目前**只传 api_key**
    （`{"api_key": ...}`，L846-853），**没传 base_url** ⇒ 这是唯一的缺口。
  · `agent/digestion/judge_runtime.py` 已有：`resolve_judge_credential`
    （secret store → 进程 env → .env 三级）、`credential_names_for(provider)`、
    `judge_availability`（三态）、`build_judge_runtime`、`judge_self_check`、
    `JudgeBudget`（按日预算 + UTC 计费）、`emit_judge_fallback`。

【要做的事】
  1. 让 judge 侧支持 **base_url**（走 OpenAI 兼容端点用 DeepSeek）：
     · `LLMJudge` 增加 base_url（构造参数 + `_adapter_kwargs` 透传）；
     · 端点来源**优先复用**部署级 `LLM_BASE_URL`（不要新造同名概念）；若确实需要独立开关，
       新 env **必须**登记进 `agent/settings/registry.py`（见 §五 陷阱 1）。
  2. 凭证接线：让 judge 拿到 DeepSeek 的 key（`CP_DIGESTION_JUDGE_SECRET_FILE`
     指向一个密钥文件，或在 `credential_names_for` 支持的名单里提供）。
     ⚠️ 用 `provider=openai` 时凭证名可能是 `OPENAI_API_KEY`（`.env` 里是**空**的）
     ⇒ 要么用密钥文件，要么明确文档化"DeepSeek 兼容端点用哪个变量"。
  3. 跑**小样本**（3–5 条即可）真实 judge，并留证：
     · `judge_kind` 必须是 `llm:<provider>:<model>`（**不是** `deterministic_local(llm_unavailable)`）；
     · judge 调用**计入 UTC**（`cp.utc` / `utc_snapshot()` 能看到成本增量）；
     · 预算超限/无凭证的回落路径也要各留一条证据（可注入桩，但**必须标明是桩**）。
  4. 把证据写进 `S8-04_交付结案报告_20260913.md` 的追加段（或新建 S9-02 报告）：
     凭证来自哪里、`judge_kind` 实际值、UTC 增量、样本量。

【验收】有凭证时 `judge_kind` 为 `llm:...` 且 UTC 有增量；无凭证/超预算时如实回落并标注原因。
【诚实纪律】若最终确认"当前环境无法真跑"（例如 SDK 不支持该端点），
        **必须在报告里写明"未在真实凭证下验证"**，不得用桩冒充真跑。

━━━ 三、W2：integration / e2e / boundary / contract 套件的结果归因 ━━━
【背景】`tests/unit` 已全量跑完并逐条归因（9 failed → 2 个真失败已修 → 现全绿）。
        但这四个目录**尚未拿到可信结论**：一次运行 exit=1，输出被管道过滤吃掉。
        已知线索：`tests/integration/test_digital_life_integration.py::`
        `test_digital_life_initializes_with_missing_optional_modules` 出现过
        **pytest-timeout 的线程栈转储**，栈里有 `sentence_transformers` 导入与
        `agent/orchestrator/lifecycle_manager.py:115` ⇒ **疑似导入超时**，而非功能失败。

【要做的事】
  1. 重跑并**把结论落到文件**（不要只靠管道）：
     python -m pytest tests/integration tests/e2e tests/boundary tests/contract -q --tb=short -rf -p no:randomly --timeout=900 > <tmp>\s9_02_run.txt 2>&1
     然后读文件尾部的 FAILED 列表与统计行。
  2. **逐条隔离归因**（这是硬纪律，别把抖动当缺陷）：
     · 对每个 FAILED 用例在**无并发负载**下单独复跑；
     · 区分三类：①真失败 ②负载/超时抖动 ③依赖外部环境（网络/真实 HEAD/模型）。
     · 抖动的定量证据：同批用例连跑 3 次并给耗时（此前实测首跑 8.94s vs 后两次 3.24s = 2.7×）。
  3. 真失败就修（或按纪律立项）；抖动/环境类要**如实标注**为"未验证/环境受限"，不算通过。
  4. 结论补进 `docs/zh/S8批次全量审核报告_20260913.md`（新增一节）。

【验收】每个 FAILED 都有归类与证据；统计数字可复现；未跑到的目录明确写"未验证"。

━━━ 四、W3：三个未合并分支提交后的复核再合 ━━━
【对象】`s702` / `s802` / `s901` 三个 worktree（当前均"领先 master 0 个提交"，即只有工作区改动）。
        `s901` 是 S9-01（对话编排）**不要动它的文件**，等它自己提交。

【复核清单（每条都要有证据）】
  1. `git -C .worktrees/<id> status --short` 与 `git -C .worktrees/<id> log --oneline master..<id>/main`
     —— 先看清它改了什么、有没有夹带无关文件（尤其**运行期数据** `data/**`）。
  2. 零缺口硬守卫：`python -m pytest tests/unit/test_settings_registry.py -q`
     （**注意**：该守卫已于 163d1281 被修好——它此前有个静默漏洞会吞掉读取点；
      现在新增 env 而没登记进注册表会**真的**变红。）
  3. kwarg 双路径：`python scripts/scan_kwarg_conflicts.py --path agent --min-risk HIGH`
     与 `--path tests --min-risk HIGH`（各 0 处）。
  4. `lint-imports --config .importlinter`（2 kept / 0 broken）。
  5. 邻接回归：`pytest tests/unit -q -k "<该任务相关关键词>"`。
  6. 双远端同点：`git rev-parse HEAD origin/master gitee/master` 三个一致。
  7. 文档一致性：结案报告里的终态 SHA / 状态行与代码实际一致（本地报告不得回填未核实的 SHA）。
  合入方式与 §一 相同；**合并后再跑一遍 2/3/4**（合并后联合验收）。

━━━ 五、通用纪律与陷阱（照做，别省）━━━
  1. **新增任何 env 读取点，必须同时登记 `agent/settings/registry.py`**
     （`_a` 可直接切 / `_b` 二次认证+双人确认 / `_c` 只读脱敏；路径与密钥位置用 `_c`；
      关掉完整性保护或放宽成本护栏的开关用 `_b`）。不登记 → 守卫变红。
     风险级拿不准时**取更严的一档**，并把依据写进注释。
  2. **不得为了让用例通过而放宽断言**。若断言比意图更严，按**意图**改并**补一条正向断言**收紧
     （实例见 commit 90d70fad：`v2|` 版本前缀 vs "键里不许有数字"）。
  3. **不得接受假绿灯**。若某次修复让一个红的守卫变绿，必须验证"被检查的对象**没有从报告里消失**"
     —— 此前两次踩坑（一次是真漏洞、一次是我自己的误改，均已留痕）。
  4. 性能/时序类用例（<200ms / <1s）与依赖真实 HEAD 的用例在并发负载下会假红：**先隔离复跑再判断**。
  5. 门禁四条缺一不可：相关套件 + kwarg 双路径 + `mypy` 改动模块 + `lint-imports`；
     跑完 `git status` 检查产物漂移（pre-commit 会提示 `clean-runtime-noise`）。
  6. PowerShell 里跑 Python 前设 `$env:PYTHONIOENCODING='utf-8'; $env:PYTHONUTF8='1'`
     （否则中文输出会 UnicodeEncodeError）。
  7. 服务当前在跑（127.0.0.1:5678）；改了 `agent/**` 要重启才生效
     （`Get-NetTCPConnection -LocalPort 5678` 取 PID）。
  8. `python scripts/dev/new_session_worktree.py create` 的 `--base` **默认是 develop**，
     必须显式写 `--base master`；id 必须是 `s<数字>`。

━━━ 六、回报格式 ━━━
每件工作：交付物 / 验收逐条（含证据命令与原始输出）/ 根因（代码行级）/ 质量证据（套件、门禁）/
遗留（带归属）/ 文档更新 / 双远端 SHA。
**不得编造数字**；做不到就写"未验证"。样本 <20 只披露不考核；数据源缺位记 `None` 不以 0 冒充。
```
