# START-S11-09 收口：三条存量红灯 CI + 四臂 CI 覆盖面 + 全量计数口径差

> **本文件不是任务书。** §二 就是**自包含的启动提示词**，整段复制给新会话即可。
> 基线：`master` = `726b121e`（双远端同点）｜预估：1–2 人日
> 上游依据：[`S11-08_交付结案报告_20260915.md`](S11-08_交付结案报告_20260915.md) **§十.3「仍未闭合」**
> 与 [`S11-08遗留收口报告_20260915.md`](S11-08遗留收口报告_20260915.md) §十（存量红灯的初次披露）

---

## 一、当前状态（已完成，勿重复）

- **S11-08 主体已交付**：`−400` 方向 5 个日期用例已修（四臂 251 passed ×4）；平移工具 7 个盲区
  检测器 + 晚替换探针固化（守卫套件 20 条）；三向全量统计行已对照。
- **S11-08 §八 的 7 项遗留已由并行会话收口**（`6abeb6da` → `9efbbd6e` → `63985f8f` → `d4afed3a`），
  并已**独立复核通过**（S11-08 报告 §十：跨进程 `--now`、慢档 259 项四臂、cleanup 用例收紧、
  integration 四臂 2001 passed、守卫登记未削弱）。
- ⇒ **本轮只做 S11-08 报告 §十.3 的三条台账**（下面 §二 的任务 1/2/3）。

### 本文件已替你做完的侦察（**以本文件为准，勿再从零调查**）

| 工作流 | 真实失败（`gh run view --log-failed`，含最新 master `726b121e`） |
|---|---|
| `hardcoded-password-scan.yml` | run `34980360547`：`RuleID: openai-api-key`｜`File: scripts/dev/s1104_judge_auto_repro.py`｜**`Fingerprint: …:37`**｜`WRN leaks found: 1` |
| `architecture-check.yml` | run `34967440178`：**`未豁免违规: 16`**（我本机同形复现 exit=1，全为 `no_circular_dependency`） |
| `knowledge-tasks.yml` | run `34967440098`：**`MISSING 1: T0`** + **`BLOCKED 7: T1..T7`**；日志另有"任务目录 `docs/zh/知识库重构计划`／解析任务数 8、文件总数 9／1 个文件缺少任务ID（已跳过）: `任务0_核心逻辑速查.md`" |

> ⚠️ **口径更正（重要）**：`S11-08遗留收口报告` §十 把 gitleaks 那条诊断为"工作流用
> `fetch-depth: 0` + `gitleaks detect` ⇒ **扫全历史**"。**该诊断有误**：该步骤实际命令是
> `gitleaks detect --config .github/gitleaks-config.toml --source . --no-git`（工作流 L137-143，
> 注释明写"不扫历史 commit, 避免历史误报"）⇒ 它扫的是**当前工作树**，命中来自**现存文件**。

---

## 二、启动提示词（整段复制给新会话）

```
【任务】S11-09 — 收口三条台账：①存量红灯 CI ×3 ②四臂 CI 定向面补齐 ③全量计数口径差 ±1
【背景】master = 726b121e（双远端同点）。S11-08 主体（−400 五用例 + 平移工具 7 检测器 + 晚替换探针）
        与 §八 七项遗留（跨进程 --now / 慢档 259 项 / cleanup 用例 / integration 四臂 / 守卫登记）
        **均已完成并复核通过**（见 docs/zh/CloudPivot_v7.2重构计划/S11-08_交付结案报告_20260915.md §十），
        本任务**只做 §十.3 的三条**，不要重做上面任何一项。

━━━ 一、工作区与隔离 ━━━
    cd C:\Users\Administrator\agent
    python scripts/dev/new_session_worktree.py create --id s1109 --base master
  · --base 默认是 develop，**必须显式写 --base master**；脚本会自动供给 .env。
  · 主工作区禁令：✗ git checkout　✗ git reset --hard　✗ git add -A（只 add 具体文件）。
  · 提交信息含中文/反引号时，写进临时文件用 `git commit -F <文件> -- <具体路径>`。
  · 提交流程：worktree 内 add/commit → 主工作区 `git merge s1109/main --no-edit` → 双远端 push
    （git push origin master && git push gitee master）。
  · 登台证据一律落 `.tmp-s1109/`（`.gitignore:313` 的 `.tmp-*/` 已覆盖，不入库）。

━━━ 二、任务 1：三条存量红灯 CI（**先查清再改**）━━━

【1a】硬编码密码扫描（gitleaks）—— 全分支，最近一次 run 34980360547（含 master 726b121e）
  真实命中（唯一 1 条）：
      RuleID:      openai-api-key
      File:        scripts/dev/s1104_judge_auto_repro.py
      Fingerprint: scripts/dev/s1104_judge_auto_repro.py:openai-api-key:37
      WRN leaks found: 1
  · **口径更正**：该步骤是 `gitleaks detect --config .github/gitleaks-config.toml --source . --no-git`
    （工作流 hardcoded-password-scan.yml L137-143，注释明写"不扫历史 commit"）⇒ **扫当前树**、
    不是"全历史误报"（S11-08遗留收口报告 §十 的诊断有误，以本提示词为准）。
  · 命中行性质（**你要独立确认，不要只信我**）：`scripts/dev/s1104_judge_auto_repro.py:37`
    = `_STUB_KEY = "sk-stub-s1104-not-a-real-key"`，其上 L35-36 注释写明"桩密钥…绝不打真实端点"。
    至少给出三条佐证：①该值是显式桩（注释 + 命名）；②`git log -S` 看它是否曾是真实值；
    ③该脚本是否会从 .env / 环境回退读取真 key（读了就不是"纯桩"）。
  · 修法（按推荐序，**逐行放行优于仓库级白名单**）：
      (a) 行内放行注释 `# gitleaks:allow`（gitleaks 官方逐行机制）；先确认工作流 env 里的
          `GITLEAKS_VERSION` 支持（≥8.19），不支持再退 (b)；
      (b) 在 `.github/gitleaks-config.toml` 的 `[allowlist] regexes` 增**窄正则**
          （如 `^sk-stub-`）+ 注释写明"桩值前缀，非凭证"；**禁止**放宽成 `sk-.*`；
      (c) 改常量形状（运行时拼接等）——**最后手段**：那是"隐瞒形状"而不是"声明事实"。
  · ⚠️ **改安全白名单属策略决定**（上一轮刻意没动）：①无论走 (a)/(b)/(c)，报告里必须写明
    "为什么它不可能是真凭证"并附证据行；②如果你判断需要 Owner 裁定，就**不改**、
    如实上报并把 CI 保持红 —— 宁可留一条真实红灯，也不要一条假绿灯。
  · 复现（本地，报告落临时目录）：
      从工作流 env 取 GITLEAKS_VERSION，装二进制，或用 docker：
        docker run --rm -v "${PWD}:/p" zricethezav/gitleaks:v<版本> detect \
          --config /p/.github/gitleaks-config.toml --source /p --no-git \
          --report-format json --report-path /p/.tmp-s1109/gitleaks.json --verbose
    验收：`leaks found: 0`（或报告 `[]`）；**不要把 scan-reports/ 提交进库**。

【1b】架构规则校验（architecture-check.yml）—— 阻断合并，最近 run 34967440178
  CI 真实结论：**未豁免违规: 16**（另一份更早的措辞是"20 处违规 / 16 处未豁免"）。
  · 本机复现（我已跑通，exit=1；**报告落 .tmp-s1109/，勿写进 docs/architecture/ 造成产物漂移**）：
      python scripts/ci_run_module.py agent.observability.arch_rules --check --root agent \
        --exemptions docs/architecture/legacy_exemptions.json --config config.yaml \
        --json-report .tmp-s1109/arch_rules_report.json --md-report .tmp-s1109/arch_rules_report.md
  · 我复现到的违规样例（全为 no_circular_dependency；行号为报告里的位置）：
      agent.audit.chain → agent.utils.cross_process_lock（agent/audit/chain.py:70）
      agent.utils.cross_process_lock → agent.observability.events（…:356）
      agent.observability.trace_v2 → agent.observability.events（…:1007）
      agent.repair / agent.repair.locate / agent.repair.pipeline / agent.repair.propose /
      agent.repair.verify（agent/repair/*.py:31/32/35/33/40）
      agent.settings.resolver / agent.settings.service → agent.settings（…:35/42）
      （另有 4 条 agent.monitoring.* → agent.monitoring.observability_config 已标注"豁免"）
  · 两条路线，**逐条**决定并写理由：
      (i) 修：依赖倒置 / 中间层解耦 / 参考 agent/lazy_loader.py 延迟加载；
      (ii) 登记豁免：写入 docs/architecture/legacy_exemptions.json（该文件已存在），
           每条必须写"为什么现在不能修 + 计划"；**禁止批量豁免**、禁止把 `--check` 放宽。
  · 注意与 `.importlinter` 的分工：本地 `lint-imports --config .importlinter` = 2 kept / 0 broken，
    与本项**判据不同**，别把 lint-imports 变绿当成这项完成。
  · 验收：本地 `--check` exit=0 且报告 `active_violations=0`；若走豁免，逐条例外给出理由与计划。

【1c】知识库任务矩阵（knowledge-tasks.yml / run_knowledge_tasks）—— 自 2026-09-12 起连续红
  CI 真实结论（run 34967440098）：任务目录 `docs/zh/知识库重构计划`，解析任务数 8、文件总数 9，
  1 个文件（`任务0_核心逻辑速查.md`）缺任务ID被跳过；汇总 `MISSING 1: T0`、`BLOCKED 7: T1..T7`
  （T0 = 知识库宪法与目录/卡片 Schema 定义；其余因前置依赖未通过而全 BLOCKED）。
  · 复现（**必须在 worktree 内跑**，产物可能落盘；跑完立刻 `git status` 检查漂移）：
      python scripts/verify_knowledge_plan_deps.py --no-warn
      python scripts/run_knowledge_tasks.py --verbose
  · 要给出**二选一**的判定（不要含糊）：
      (i) 计划/矩阵**过期**：知识库模块早已以别的路径交付，T0 的"交付物"清单与实际仓库不符
          ⇒ 更新任务文档/矩阵，并**显式声明口径变更**（变更点 + 影响面）；
      (ii) **真缺交付物** ⇒ 记录为真实缺口（归属：知识库专项），排期补齐。
  · 证据要求：逐条列出「T0..T7 要求的交付物 ↔ 仓库实际路径（有/无/在哪）」的对照表。
  · **禁止**为了让 job 变绿而改脚本跳过检查或伪造交付物（除非有 Owner 裁定，并声明影响面）。

━━━ 三、任务 2：四臂 CI 定向面补齐 ━━━
  · 文件：.github/workflows/date-shift-guard.yml（S11-08 遗留收口时新增；注释我已订正为
    "7 个 AST 检测器 / 20 用例"）。
  · 现状：四臂矩阵的"定向日期敏感面"只列了
      tests/unit/test_retention_{archive,scan,scheduler}.py + test_policy_support.py +
      test_behavior_drift.py + test_skills_digest_assessor.py +
      tests/integration/test_knowledge_audit_ci_edge.py
  · 缺两个（S11-08 的回归批次含它们，理由：共享夹具 retention_testkit 的改动会牵动整族）：
      tests/unit/test_retention_metrics.py
      tests/unit/test_retention_guard.py
  · 做法：把这两条加进**四臂矩阵的常规档步骤**（保持 `-p no:randomly ${{ matrix.arm.plugin }}` 口径）；
    顺手核对慢档步骤与静态扫描步骤是否也要同步（慢档三件套无需变）。
  · 验收：给出本地四臂实跑统计行（新集合 = 9 文件；S11-08 的十文件批次是 251 passed ×4，
    补齐后按**实跑**数字报告，不要照抄）；如你有权限，再给一次 `workflow_dispatch` 实跑结论。

━━━ 四、任务 3：全量计数口径差 ±1（可最后做，不阻塞）━━━
  · 现象（S11-08 §十.3 如实登记、未追因）：tests/unit 全量三档的 outcome 合计 = **18482**
    （`1 failed + 18152 passed + 312 skipped + 13 xfailed + 4 xpassed`），
    而**同一棵树** `--collect-only` = **18481**（基线 a16afb6b 为 18463，本任务新增守卫 18 条）。
  · 复现：
      python -m pytest tests/unit -q --collect-only -p no:randomly -p tests._date_shift_plugin
    与三档实跑同口径命令：
      python -m pytest tests/unit -q --tb=no -rf -p no:randomly -p tests._date_shift_plugin --timeout=600
  · 追因方向（按可能性排序）：①`tests/conftest.py` 的自定义"测试统计"汇总钩子
    （通过/失败/跳过 的计数来源）；②`-rf` 与 xpass/xfail 的计数口径；
    ③`--continue-on-collection-errors` 下 collection error 是否被重复计入；
    ④运行期动态 parametrize（`pytest_generate_tests`）。
  · 判据：给出"是口径差（统计钩子问题）还是真 bug"的结论 + 证据；
    若是 conftest 统计口径问题，**顺手修好并给改前/改后数字**。
  · **做不到就如实写"未追因"**（不得编造结论）。

━━━ 五、纪律（通用约定子集，逐条都要落证据）━━━
  · **分类实验必须含对照臂**：判"真炸弹 vs 工具伪影"跑 **不平移 / CONTROL（CP_DATE_SHIFT_CONTROL=1）
    / +400 / −400** 四档；只有平移档的对比无法区分二者（S11-06 误判 49 个的教训）。
  · **先把输出落文件再读**（管道会截断）；证据统一放 `.tmp-s1109/`。
  · **不得为了让检查变绿而放宽**：不放宽断言、不批量豁免架构违规、不改脚本跳过检查、
    不放宽 gitleaks 正则到 `sk-.*`。
  · **不得编造数字**：每条结论都要能复现；做不到就写"未验证/未追因"；
    "数字变化"与"口径变化"必须分开讲。
  · **新增任何 env 读取点必须登记** agent/settings/registry.py（否则 TestMechanicalZeroGap 零缺口守卫变红）。
  · **测试隔离红线**：不得写仓库根 .env（沿用 CP_ENV_FILE 隔离）；需要落盘的用例显式传路径。
  · PowerShell 跑 Python 前设 `$env:PYTHONIOENCODING='utf-8'; $env:PYTHONUTF8='1'`。
  · 改了 `agent/**` 或 `config.yaml` **需重启** 127.0.0.1:5678 才生效（重启属运维动作：
    **不要擅自重启**，在报告里明确写"是否需要重启才生效"）。
  · **安全白名单/架构豁免属策略决定**：先确保证据（"为什么它不可能是真凭证/为什么现在不能修"），
    拿不准**就不改并上报**，而不是先改后解释。
  · 门禁四条（缺一不可）：相关套件 + 邻接回归；`python scripts/scan_kwarg_conflicts.py --path agent
    --min-risk HIGH` 与 `--path tests` 各 0 处；`python -m mypy <改动模块>`；`lint-imports --config
    .importlinter`（2 kept / 0 broken）；跑完 `git status` 检查产物漂移。

━━━ 六、验收（逐条给证据命令与原始输出）━━━
  1. 1a：本地 gitleaks `leaks found: 0`（或"需 Owner 裁定、保持红"的如实上报）+ CI 结论；
  2. 1b：本地 `arch_rules --check` exit=0 且 `active_violations=0`（或逐条例外的理由与计划）；
  3. 1c：T0..T7 的「要求交付物 ↔ 实际路径」对照表 + "过期/真缺"的判定与处置；
  4. 任务 2：四臂实跑统计行（9 文件集合）+（可选）`workflow_dispatch` 结论；
  5. 任务 3：±1 的结论或"未追因"；
  6. 门禁四条结果 + `git status` 漂移检查；
  7. 报告里写明**是否需要重启服务**。

【回报】交付物 / 验收逐条（含证据命令与原始输出）/ 根因（代码行级）/ 质量证据（套件与门禁结果）/
        遗留（带归属）/ 文档更新 / 双远端 SHA / 是否需要重启。
```

---

## 三、开工自查清单

- [ ] `--base master`、id=`s1109`；worktree 内工作；证据落 `.tmp-s1109/`
- [ ] 1a：先**独立确认** `sk-stub-…` 是桩值（注释/命名/`git log -S`/无 .env 回退），再选修法；走白名单必须写明理由
- [ ] 1a：本地 gitleaks 复现命令与实际版本对齐（`GITLEAKS_VERSION`），报告落临时目录
- [ ] 1b：复现时**不要**把报告写进 `docs/architecture/`（避免产物漂移）
- [ ] 1b：16 条**逐个**决定"修 or 豁免"，禁止批量豁免；不与 `lint-imports` 混为一谈
- [ ] 1c：`run_knowledge_tasks.py` 跑完立即 `git status`；给出 T0..T7 对照表与二选一判定
- [ ] 任务 2：把两个 retention 文件加进四臂矩阵常规档；给**实跑**统计行（不照抄 251）
- [ ] 任务 3：给出 ±1 结论或"未追因"（不许编）
- [ ] 全线：对照臂、落文件、不放宽、不编数、env 登记、双远端同点 push
- [ ] 报告写明是否需要重启 127.0.0.1:5678
