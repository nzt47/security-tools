# 下次要修 —— 结转台账（云枢技能治理与路由 · 审计批次结项 2026-09-27）

> 本文件是**自包含**的：每条都给了「现象 / 证据锚点 / 复现命令 / 建议修法 / 验收标准」。
> 上一批的完整审计与实施证据在 `AUDIT_AND_PLAN.md` §0–§27（同目录），本文件只放**未闭环项**。
> 结项状态：PR #980 / #985 / #988 均已合并；`master` = `0fe830ad`（合并后 CI：6 个单元分片全绿，
> 仅 Docker/ghcr 基础设施抖动导致 4 个 job 红 —— 见 P2-9）。

---

## ★ 2026-09-28 回填（本轮闭环 / 新发现 / 仍开放）

> 交付报告：`docs/closeout/遗留清理与浮红根治_20260928.md`（含逐条实测与**单变量反证**）。

| 台账条目 | 本轮结论 | 证据锚点 |
|---|---|---|
| **P1-1** `tool-retrieval-ci.yml` 无手工触发 | ✅ **已处理**：补 `workflow_dispatch`（只加触发入口，判定一字未动） | `yaml.safe_load` → `triggers` 含 4 项 |
| **P1-3** CLI 下限门没有任何 workflow 跑 | ✅ **已处理**：新增 `.github/workflows/route-conflict-gate.yml`（下限门；**不动**既有精确棘轮） | 命令 0.9 s；默认下限 exit 0；`--min-pass 999` exit 1（反证） |
| **P2-2** `tool_generator.py:218/223` 未净化拼路径 | ✅ **已处理**（并且**复现了**，不再是"静态推断"）：停用净化后 `category="../.."` 真的写到 `%TEMP%\my_tool.py` | 新增 `_is_safe_path_segment` / `_is_safe_tool_module_name` + 31 条用例 |
| **P2-5** 测试在检出目录**创建**运行期台账 | ✅ **已修**（"只防创建"口径 + 6 条自证） | 报告 §6.3；`_redirect_default_when_absent` 三分支各一条自证 |
| **P2-8** knowledge 审计台账被测试追加 | ✅ **已修** | 实测 63702 B → **65902 B**（修前）→ 修后**逐字节不变** |
| **P1-2** CI 上「永不执行」的断言 | ⚠️ **部分已修**：rank_bm25 硬前置 + 订正过时注释 + 镜像断言补 CI 可跑孪生；其余 4 条成因已钉死（产物全 gitignore）并登记 | 报告 §6.4 |
| **P2-1** 动态加载豁免锚定在函数上 | ✅ **核验后判定"早已覆盖"**：锚点测试比建议更强（恰好 1 个调用方 + 实参来自 `CUSTOM_TOOLS_DIR` 遍历），且扫描器已接进 `skills-check.yml` | 报告 §6.5；实测 `--root agent` exit 0、该测试 14 passed |
| **P0-1**（中文召回 4/8） | ✅ **已修（判据重设计）**：中文 **8/8**、英文 8/8、S10-03 两锚绿、负样本 **5/31 持平**、端到端 8/8 | 交付报告 §8；`MINSCORE1.md` 顶部已加"后续处置" |

**本轮新发现（台账里原先没有的）**：

1. **「测试浮红」的一个真根因：`sleep` 打桩是进程全局的**。`monkeypatch.setattr(<模块>.time, "sleep", ...)`
   打的是全局 `time.sleep`（实测 `tc.time is time` → True）⇒ 同进程**任何**别的代码（含泄漏的 daemon 轮询
   线程）的 sleep 都会混进记录器，把"环境噪声"判成"被测代码退化"。已新增作用域记录器夹具
   （`tests/unit/conftest.py::scoped_sleep` / `scoped_async_sleep`）+ 1 条真实泄漏线程回归锁。
2. **`test_skill_h3_migration.py` 的 `[fixture]` 参数并不密闭**：`_skill_sources(include_runtime_catalog=True)`
   读**两份**运行期文件，而夹具只隔离了 `SKILLS_MGMT_PATH`，`SKILLS_JSON_PATH`（`data/skills.json`）没隔离
   ⇒ "CI 冷启动语义"那一半在本机读的是真实运行期目录。已隔离并加 3 条非空转自证。
3. **H-3 的"主轨独有恰好 2 条"作为"对实时台账"的断言已过期**：`data/skills_mgmt.json` 在 2026-09-28T19:49
   经**生产导入通道**新增 6 条（`source=external_agent`、`tags=["external","imported","markdown"]`）
   ⇒ 主轨独有由 2 变 8。已把判据拆成「契约本体（⊇，两类来源都判）」+「快照精确性（只有 `[fixture]` 判）」，
   `[real]` 漂移时**显式 skip 并贴出实测集合**（不再恒红，也**不放宽**契约本体）。

---

## 0. 先读这段：下次接手时的环境事实（省得重新踩）

| 事实 | 值 / 说明 |
|---|---|
| 仓库 | `C:\Users\Administrator\agent`（Windows 单机，分支 `master`） |
| Python | **系统解释器 `python`（3.12.0）**。`venv/` 是**空壳、没有 python.exe**，不要用 |
| 跑测试 | `python -m pytest <路径> -q -p no:randomly --timeout=120 --no-header -p no:cacheprovider`（`pytest.ini` 要求 `--timeout` 与 `asyncio_mode=auto`） |
| 全量单测 | `python scripts/run_full_pytest.py --chunks 4 --workers 4 --mode fast`（**fast = `-m "not slow"`**，慢档要 `--mode slow`；耗时 ~40 分钟） |
| 线上 CI 的坑 | **PR 只要触碰 `.github/workflows/` 就拿不到任何 PR check**（5 次对照探针实证，见 `AUDIT_AND_PLAN.md` §23.2）；不触碰则正常。想跑某个门可用 `gh workflow run <file> --ref <branch>`（该门必须在默认分支上已存在） |
| 生产数据 | `data/audit/**`、`data/*.jsonl`、`data/skills_mgmt.json`、`data/descriptors.json` 都是**运行期真实数据**（多数 gitignore）；改动前先记 sha256，别在它们身上做实验 |
| 数据不变性自检 | 审计链：行数 72701 / seq 1..72701 无断链 / 日根封印与链上 `self_hash` 逐条相等（本次结项时的值，可直接比对） |

---

## P0 —— 唯一的功能级未完成项（需要「判据重设计」，不是补丁）

### P0-1 生产 `min_score=0.3` 让技能检索中文命中从 8/8 掉到 4/8 —— ✅ 2026-09-28 已修（判据重设计，见交付报告 §8）

- **现象**：向量腿不可用的降级模式下，8 条中文 query 只有 4 条召回（`zh01/zh02/zh06/zh08` 全失）；英文不受影响（8/8）。
- **根因（已实测钉死）**：腿级过滤比较的「有界相似度」**不是相似度**，而是 `_match_score = H / N`
  （H = 命中的 query token 数，N = query token 数）——与文档长度无关、与 query 长度严格反比；
  `0.3` ≡ 「H ≥ 0.3·N」，中文 bigram 下长 query 必被拒。**与 BM25 无关**（开/关两档都是 4/8）。
- **为什么不能只改 loader**：编排层 `Orchestrator._bounded_relevance` 用**同一个 0.3** 比**同一个 H/N**，
  被 loader 救回的候选照样被判 `low_bounded_relevance` 降级 ⇒ **只在 `agent/skills_mgmt/loader.py` 里修 = 端到端零收益**。
  真修必须同时动 `agent/orchestrator/orchestrator.py`（`_SEM_DEFAULTS` / 语义层配置读取）与 `config.yaml`。
- **为什么"再调一个阈值"无解**：`zh01` 被 S10-03 噪声 **Pareto 支配**（cov 0.0909 vs 0.1000、H 1 vs 1、
  bm25_t1 2.5441 vs 4.6614、ratio 1.3359 vs 1.5433、idf_cov 0.0594 vs 0.0869 —— 噪声**每一项都不劣**）
  ⇒ 任何对这组特征单调非降的判据要么同时收下、要么同时拒绝。**必须换判据形态**（例如：按"是否只命中停用/泛词"分类、
  用 query 侧覆盖率 + 文档侧 IDF 权重联合、或引入长度归一化后的真相似度）。
- **`0.3` 是无标定魔数**：`git log -S ORCHESTRATOR_SEMANTIC_MIN_SCORE` → 最早 `47e7f6be`(2026-07-31)，
  commit message 全文不含 `min_score`，无标定集/负样本对照；与 `loader._RRF_QUALITY_MIN=0.3`（那个**有**标定）谱系无关。
- **复现命令**（生产入口，别用内部函数自造口径）：
  ```
  python - <<PY  (或写成脚本)
  from agent.skills_mgmt.file_store import SkillFileStore
  from agent.skills_mgmt.loader import SkillLoader
  ld = SkillLoader(file_store=SkillFileStore())
  # 用 data/eval/minscore1_query_set.v1.jsonl 的 8 中 + 8 英，逐条 print(m.skill_id)
  # min_score=0.3 -> zh 4/8 ; min_score=0.01 -> zh 8/8 ; use_bm25 开/关同值
  ```
  （完整脚本与期望值见 `MINSCORE1.md`；用例锁见 `tests/unit/test_minscore2_chinese_recall.py`，**11 passed**）
- **候选修法与各自的拦路石**（详见 `MINSCORE1.md`）：
  | 候选 | 中文@0.3 | 拦路石 |
  |---|---|---|
  | C1 调用方降到 0.01 | 8/8 | **重开 S10-03 假阳**（0.01 下返回 2 条噪声） |
  | C2 腿级地板解耦 `_RRF_LEG_MIN_SCORE=0.01` | 8/8 | **S10-03 锚红**（`self_reflection` / `pd-dispatching`）+ 跨卡护栏 `test_ret1r_bm25_quality_gate.py` 1 failed |
  | C3 = C2 + 裕度阈值 1.2→2.0 | 7/8 | 跨卡护栏仍 1 failed；7/8 差最后一条 |
- **跨卡契约需要一起改**：`tests/unit/test_ret1r_bm25_quality_gate.py::TestBm25AloneIsNotEnough::test_bm25_only_evidence_is_not_enough`
  把「`min_score` 与腿级过滤的耦联」写成了**前置契约**（`assert 0.1818 is None`）。要改判据就必须**一并**改这条契约，
  并在改时说明为什么不是"放宽守卫"。
- **验收标准**：中文 8/8、英文 8/8、S10-03 锚绿、单向量路误召数**不上升**（当前 GATE-1 已把它从 22/23 降到 12/23）、
  且**编排层端到端**（不是只 loader）有数字证明。

---

## P1 —— 影响"门禁可信度"，建议下一批优先

### P1-1 `ci.yml` 之外的门在 PR 上跑不到 —— ✅ 2026-09-28 已处理（补 `workflow_dispatch`，判定未动）
- `tool-retrieval-ci.yml` **没有 `workflow_dispatch`** ⇒ 既拿不到 PR check（它按 `push`/`pull_request` + paths 过滤），
  也无法手工触发。建议：给它加 `workflow_dispatch:`（**只加触发入口，不改判定**），下次可以直接 `gh workflow run`。
- **已闭环的对照**：`Settings Registry Gap Guard`（新增）与 `Skill Description Single Source`（修改）在 #980 合并后**第一次真跑并 success**，
  见 `AUDIT_AND_PLAN.md` §27.1 —— 所以"加 workflow 必须合并后才首次验证"这条已经用证据关闭。

### P1-2 CI 上「永不执行」的断言仍有存量
- `tests/unit/test_skill_description_single_source.py` **5 条** skip；
- `tests/unit/test_s10_03_retrieval_quality_gate.py:227` 的 `pytest.importorskip("rank_bm25")`（**只在部分入口**是假绿）：
  `tool-retrieval-ci.yml:256/261/263` 已**显式安装并断言可导入**，但 `pyproject.toml:145` 其实也声明了 `rank-bm25==0.2.2`
  ⇒ 该 workflow 的注释"不在 pyproject 依赖里"是**过时口径**，建议订正注释并统一为"硬前置"写法；
- `tests/unit/test_skill_h3_migration.py` 的 8 条 `[real]`（真实台账内容类）**已逐条登记并配夹具孪生**（`CI3.md` §3.4）——
  它们不再校验真实数据，这是**有意的取舍**，不需要再修，但别再误以为是漏配。

### P1-3 `test_route_conflict_cases.py` 的棘轮口径不一致 —— ✅ 2026-09-28 已处理（给 CLI 下限门补了 `route-conflict-gate.yml`；既有精确棘轮未动）
- 进 CI 的是**精确相等**棘轮，而 CLI 的 `>=48` 下限门**没有任何 workflow 跑**。
  要么统一口径（都改成"下限 + 变化需显式更新"），要么给 CLI 门补一个 workflow。

---

## P2 —— 卫生与健壮性（不影响本次交付）

1. **动态加载豁免锚定在函数上**：`scripts/detect_dynamic_loads.py` 的豁免是 `(file, qualname, pattern)` 三元组；
   将来若给 `load_dynamic_tools()` 新增调用方并传**外部路径**，扫描器**不会**报警。已有 AST 锚点测试钉住，但依赖有人跑。
   建议：把"参数必须来自 `CUSTOM_TOOLS_DIR` 常量"写成断言式检查。
2. ~~**`agent/tools/tool_generator.py:218/223`** 用未净化的 `name/category` 拼落盘路径（静态推断，未利用、未复现）。~~ —— ✅ **2026-09-28 已处理，且已复现**：停用净化后 `category="../.."` 真的把文件写到 `%TEMP%\my_tool.py`（`agent/tools/custom/` 之外）；现已前置两级净化 + 31 条安全用例。
3. **`DescriptorRegistry` 持久化健壮性** —— 部分闭环，**剩余项已收窄到一条**。
   ~~影响面（约 30 个调用点、含 UI 读路径）**未穷举**~~ ⇒ **2026-09-29 已穷举**：`agent/` 生产侧 44 处直接调用点
   （读 30 / 写 6 / 显式 8）+ `scripts/` CLI 22 处，**所有 UI 与接口读路径都在 `except Exception` 内**；
   端到端实测最坏用户可见后果是 **HTTP 503**（`trace_diff`）或 **HTTP 200 + `load_error` 如实报错 + 空表**
   （`capability_map`），无一处 500、无一处丢轨迹 ⇒ **判定不必改 `load()`**（依据见报告 §12.2）。
   `save()` 失败分支两处真缺陷（`*.tmp` 残留 / 非瞬态 OSError 也白等 6 次退避）**已修**（`3d058974`，带改前红/改后绿用例）。
   **仍未闭环（本卡剩余、需 owner 拍板）**：写侧**持续 ≥1.55s 的占用**（杀软长扫描 / NFS 租约）仍会让单次 `save()` 失败 ——
   实测阈值 == 退避预算本身（1.4s 成功 / 1.6s 失败），**扩大预算只能把阈值后移、不能消除窗口**，故当时**没有**动预算。
   根治要么改读侧以 `FILE_SHARE_DELETE` 打开（需 ctypes `CreateFileW`，会影响 `load()` 语义），
   要么改成单写者串行 + 外部锁；两者都超出"最小改动"。**NFS/SMB 仍未实测**（本机无环境），不臆测。
   `registry.py` 自带第二份原子写实现（未复用 `agent/utils/atomic_write.py`），两份口径不一致（重试范围 / fsync / 失败清理）
   —— 是否合并，同样建议单独立卡。见 `LEDGER2.md`。
4. ~~**测试卫生两个方向都还没收敛**：
   - 泄漏侧：`--runslow` 车道的 `test_skills_classifier` / `test_tool_callability` 未修（默认车道实测 CLEAN）；
   - 破坏侧：`test_tool_callability` 六处 `T.clear()`、`test_fan_out` 无条件 `unregister`；
   - 空台账侧：`test_skill_search_description_source.py` 与 `test_skill_h3_migration.py` 会创建 **2 字节的 `data/skills_mgmt.json`（`{}`）**。
   见 `TESTHYG2.md`、`CI3.md`。~~
   —— **2026-09-28 处置**：**破坏侧已修**（两文件加"先记后还原"夹具 + 非空转守卫；确定性复现见报告 §6.7：
   canary 注册表 **1 条 → 0 条**）；**空台账侧已修**（ISO-RUNTIME，报告 §6.3）；
   **泄漏侧未能复现**（`--runslow` 三文件 **176 passed**、5 个运行期落点逐字节不变）⇒ 保留为待复现项，不臆测硬改。
5. **测试会往检出目录写运行期数据**：`data/skills_mgmt.json` / `data/audit/` / `data/learned_workflows.json`；
   后者**未入库**，而 `test_workflow_learning_admission.py` 的存量仓库判据已按「**有内容才算**」订正（与 `skills_mgmt.json` 同族）。
   建议把"运行期落点"统一走一个 autouse 重定向（参照 `tests/unit/conftest.py` 的 ISO-EVENTS 做法）。
6. **两条负载敏感用例已按 L9 机制标 `serial`**（`test_llm_error_path_recorded`、`test_handler_timeout_scanner::TestCurrentRepoInvariants`），
   **断言未改**。根治方向：把 `TestCurrentRepoInvariants` 的"全仓扫描"改成对**固定快照目录**扫描，使其与仓库规模/机器负载解耦。
7. **`data/audit/daily_roots.jsonl` 有一条 2026-09-14 重复**（与首条同 seq 区间/同哈希，是先前授权切除后重建哈希链的补链产物）。
   **无害**（封印与链上 `self_hash` 逐条对得上、`prev_entry_hash` 无断点），仅"不整齐"；要清理得同时保证补链不断。
8. **`data/audit/knowledge_audit.jsonl`（gitignore）被既有用例行为持续追加**（结项时 57105 B / 129 行）。
   属既有行为、文件级、无完整性影响；若要根治，把 knowledge CLI 的审计落点也纳入 ISO-EVENTS 重定向。
9. **CI 基础设施抖动（非代码）**：`准备扫描器镜像` / `关键字参数冲突扫描 (Docker)` / `kwarg 扫描 → SonarQube`
   偶发 `Error response from daemon: Get "https://ghcr.io/v2/": denied: denied`。
   实证：同一 job 在多个运行里成功过（如 #980 合并后那次），而 01:46 / 01:54 两次连续失败且**只失败这一类**。
   处置建议：不要改代码；是真·基础设施/配额问题，重跑即可；如反复出现应向仓库 owner 确认 ghcr 凭据/配额。
10. **`_t06_logs/` 等未跟踪目录会污染本地扫描**：`python scripts/detect_dynamic_loads.py`（默认根）在本机报 `high=46`，
    其中 `_t06_logs/` 12 处、`qwen-agent/` 等未跟踪目录占绝大多数；**干净检出上是 `high=0 → exit 0`**。
    建议本地排查统一用 `--root agent`，或以干净检出为准。
11. **既存死代码/死键**：`index_manager.py` 死代码、`auto_upgrade` 死键（均为既存，未处理）。
    —— **2026-09-28 补证**：`agent/utils/index_manager.py` 已被**一手核实为死**（全仓引用只有它自己 +
    它自己的 3 个测试文件 + `scripts/run_full_pytest.py:72` 的清单条目；生产代码零导入）。
    删除要一次动这 4 处并跑受影响守卫，本轮判为"影响面大于收益"而**只补证不删**（详见报告 §6.8）。12. **`tests/test_digital_life.py` 在 CI 上超 300s 的归因（2026-09-30 实测，否证了"探测次数"假说）**。
    现象：CI `Shard 4/6` 上 `TestDigitalLifeInitialization::test_init_with_default_config` 与
    `TestDigitalLifeLifecycle::test_start_stop` 各报 `Timeout (>300.0s)`，而本机同文件 **13 passed / 3 xfailed，85.39s，不卡**。
    实测结论：一次 `DigitalLife(config)` 构造**最多触发 3 次** `_probe_import`（本机 HF 缓存完整时 **2 次**），
    且**只在进程内首次构造**时发生（第 2、3 次构造 = 0 次）；3 × 30s = **90s < 300s**，
    要把单条用例凑到 300s 需 **≥10 次**，而整个文件 17 处 `DigitalLife(...)` 构造点的探测总数仍是 2~3 次
    ⇒ **"探测次数凑满 300s"不成立**（已用 `_scratch/q1_probe_matrix.py` / `q1_start_stop.py` 计数实测）。
    真正**无时间上界**的是**进程内**编码器加载：
    `VectorStore.__init__` → `_init_sqlite_vec()` → `_get_shared_encoder()` → `SentenceTransformer(model)`，
    这条路径**不受 `_DEPS_IMPORT_TIMEOUT`(30s) 约束**（主进程内调用，不是子进程）；本机实测冷启动
    **951 秒仍未返回**（被手工 kill）。也没有"重复构造 VectorStore"：`lifecycle_manager.py:174→:562` 每次构造恰好 1 个。
    **未确证**：CI 上 `_is_model_fully_cached()` 的返回值（`observability-ci.yml` 无任何 HF 缓存步骤，只有 `cache: pip`，
    故〔推断〕为无缓存→走探测分支）；以及本机 951s 与 CI 300s 是否同因。
    **确证条件**：CI 日志里 `[WARN] ChromaDB not installed or import timeout` / `[OK] ChromaDB loaded` 那一对行，
    以及 Shard 4 的 pytest-timeout traceback 全文（线程 id + 各次探测耗时）。
    另注：`pytest.ini:68` 已 `--ignore` 该文件，但分片用 `split_unit_tests.py` **显式传路径**、`--ignore` 拦不住
    （仓库自己在 `scripts/split_unit_tests.py:80-81` 写明）—— 要不要把它加进 `OBSERVABILITY_CI_ONLY`，
    是"减少覆盖"与"消掉偶发红"之间的取舍，**需 owner 拍板**，本轮未动。
13. **`memory/vector_store/vector_store.py:158-159` 的离线开关实测无效（新发现，2026-09-30）**。
    该处在"模型缓存完整"分支里写 `os.environ.setdefault("HF_HUB_OFFLINE", "1")`（`TRANSFORMERS_OFFLINE` 同款），
    意图是"缓存完整 → 走本地加载、不发网络请求"。但**若 `huggingface_hub` 已被导入，该变量不再生效**，实测：
    ```
    os.environ["HF_HUB_OFFLINE"] = "1"
    huggingface_hub.constants.HF_HUB_OFFLINE  ->  False   # 常量在 import 期即固定
    ```
    后果（实测）：权重已加载完仍继续对 `huggingface.co` 发 HEAD 并 `Retry 1/5 … 5/5`
    （`adapter_config.json`、`processor_config.json` 等）。
    **未修**：可修的最小做法是"设置后再显式刷新 `huggingface_hub.constants`"，
    但这是在**共享的生产模型加载路径**上动第三方模块常量，且本机 HF 不可达（`WinError 10060`）、
    **无法验证修后的离线行为**，故只登记不臆改（"提前到模块顶部"会变成**无条件**离线，改变语义，不做）。

---

## 附：本次结项时**新装上的守卫**（下次改这些面时会被它们挡住，属预期）

| 守卫 | 守什么 |
|---|---|
| `tests/unit/test_arch_stage_contract.py` | descriptors 层不得反向 import `agent.digestion.stage`；叶子契约纯度；未注册必报错；**真树级**零循环依赖（`slow`，CI `--runslow` 档） |
| `tests/unit/test_descriptor_registry_concurrent_load.py` | 并发重建 0 损坏 / 0 丢失更新；瞬态 `PermissionError` 不被当"存储损坏"；单进程序列化摘要（**行尾归一化后**）不变 |
| `tests/unit/test_dynamic_loads_high_exemption.py` | 豁免是三元组全等 + 配额、命中降级 MEDIUM 不删除；**放宽即红** |
| `tests/unit/test_gate1_single_vector_quality_gate.py` | 单向量路质量闸（复用 `SINGLE_PATH_MIN_TOP1=0.45`） |
| `tests/unit/test_minscore2_chinese_recall.py` | **特征化锁**：把"0.3 下中文 4/8、0.01 下 8/8"钉住 —— **2026-09-28 已按约定同步为 8/8**（含真实语义层端到端锁 + 腿级地板锁），判据常量与有界键集合的锁**一字未改** |
| `tests/unit/test_date_shift_blindspots_guard.py` | 盲点登记键改为「文件 + 检测器:作用域#序号@证据指纹」（不再随行号漂移） |
| `tests/unit/test_tool_count_consistency.py` | 宣告=下发；新增 autouse 隔离夹具（受害侧） |
| `.github/workflows/settings-registry-gap-guard.yml` | 开关中心唯一事实源零缺口（AST 提取 vs 注册表） |
| `tool-retrieval-ci.yml` 的 `skill-retrieval-quality-gate` | 技能检索质量闸（含"真技能库 + 真 BM25"对照） |

---

## 附：本批的两个"教训"（下次别再犯）

1. **"抽公共实现"时最容易丢的是注入缝隙**：把两处 `run(["taskkill", ...])` 抽成 `_kill_pid()` 时，第一版用了模块级名字，
   丢掉了 `runner=` 注入桩 ⇒ 受控桩收不到 kill、7 条用例变红（且 NameError 被外层 `except` 吞掉，表现为"kill 静默没发生"）。
2. **"挪进函数体"不能消除架构环**：`dependency_graph._parse_imports` 用 `ast.walk` 遍历整棵树含函数体，
   连字面量 `importlib.import_module(...)` 也计边 ⇒ 只能靠**依赖倒置 / 叶子契约**。规则文案里早就写明了（`arch_rules.py:126-137`），我此前没读它。
