# G1-B0 · 描述改造**开工前**的重新对账（只读）

> 任务卡：**G1-B0**（G1 描述三段式改造的**动工前**复核卡）
> 性质：**纯只读**。本卡**未修改任何既有文件**，只新建本报告一个文件。
> 前置：`docs/audit_skill_governance/G1A_reconciliation.md`（尤其 §0.5 与 §14）。
> 环境：Python 3.12.0；pyyaml；chromadb 1.5.9；**未启动服务、未跑 pytest、未执行任何 git 写操作**。
> 口径：一切以本次实测为准；无证据的推断一律显式标注【推测】。

---

## 0. 结论速览

| # | 结论 | 判定 |
|---|---|---|
| **R1** | **G1-A §14.4 的 4 条重新对账命令全部执行完毕**。②③ 的输出与 G1-A §11.1 / §11.5 的预期**逐字节相同**；④ 确认「主轨先占位」仍在；① 只有 `vector_adapter.py` 的行号发生漂移，其余落点全部对得上 | 完成 |
| **R2** | **10 处存储清册全部仍然存在，条数一个没变**（23/22/30/30/4/4/7/7/29/23/8）。G1-A 引用的行号中，**只有 `digital_life_persona.py` 的 `_loaded_skill_ids`（436→446）和 `vector_adapter.py` 一族漂移**；`registry.py` / `plugins/skills.py` / `spec.py` / `callability.py` / `sync_capability_manifest.py` 的行号**逐一未变** | **成立** |
| **R3** | **15/23 双描述冲突仍然成立且仍是 15/15 全分歧**。逐条重跑 `difflib.SequenceMatcher.ratio`，**15 个数值与 G1-A 表格逐个相同**（0.077 … 0.913） | **成立（数值零漂移）** |
| **R4** | **唯一源裁定仍成立**：`git ls-files --error-unmatch data/skills_repo/self_reflection/skill.md` 仍成功，**23/23 个 skill.md 全部被 git 跟踪**，`git check-ignore -v` 的 6 条命中行号（`.gitignore:224/148/506/226/250/211`）与 G1-A 完全一致；且 `git status --porcelain -- data/` **为空**（数据文件全部与 HEAD 一致） | **成立** |
| **R5** | **CapabilityRegistry 的 0/23 缺口仍成立**（114 条：91 工具全非空 / 23 技能全空）。G1-A §9.4 的落点行号（`spec.py:229`、`spec.py:344`、`callability.py:982-988 / 1000-1003 / 1164-1169`、`sync_capability_manifest.py:165`）**全部对得上，零漂移** | **成立** |
| **R6** | **「改中文会砸检索」重跑确认：17/23（73.9%）→ 13/23（56.5%）**，与 G1-A 完全一致；本轮补充了**具体是哪 5 条**会失去触发句式 | **成立（数值零漂移）** |
| **R7** | **F1b 已就位：`update_meta` 现在确实是「除目标键所在行外逐字节不变」**。23/23 实测通过；同值重写 23/23 零字节改动（幂等） | **成立** |
| **R8** | **15 个缺末尾换行的文件全是 `pd-*`**（清单见 §3.2）。实测：一次把 description 改写后，**每个文件恰好在 EOF 补 1 个 CRLF，且只补一次**（第二次改写不再追加） | **成立** |
| **R9** | **新增硬前置**：`description_zh` **不在** `file_store._META_FIELDS`（`file_store.py:75-82`，全仓库 0 处出现该键）⇒ 现在用 `update_meta` 写 `description_zh` 会被**静默忽略**；即使手工写进 skill.md，`parse()` 也**看不见**它，`load_metadata_index()` 更不会带出来。**M2 必须先补这 1 行白名单** | **G1-B 的阻塞项（1 行代码）** |
| **R10** | **F1b 恰好把 M2 变成「可存活」**：修复前 `update_meta` 走 parse→serialize 整文件重排，**会把 `description_zh` 连同任何白名单外字段一起静默删除**（本卡已实测复现）；修复后 `patch_front_matter` 原样保留。⇒ **没有 F1b，M2 写完 `description_zh` 会被下一次技能启停悄悄抹掉** | **重大正面影响** |
| **R11** | **G1-B 现在可以开工，但必须按 §4.2 的顺序，且第一步是那 1 行白名单**。仍有 5 项需要人类决策（§4.5） | **YES（有前置）** |
| **R12** | 本卡新发现 **3 处 G1-A 的勘误**（§2.7）：① M4 的前端落点 `skill-center.tsx` **不显示技能描述**（grep 零命中）；② M0 漏了第二条 overlay 写路径 `POST /api/skills/describe`（`plugins/skills.py:443-456`）与 `service.py:1315` 的自动补描述；③ 「callability.py 两处」（§0 C7）与其自身 §9.4 的「三处」自相矛盾，正确是 **3 处** | **勘误** |

**一句话**：G1-A 的对账结论**数据面 100% 复现、行号面仅 `vector_adapter.py` 一族漂移**；F1b 把「最小侵入改写」做实了，使得中文文案回写不再有被静默抹掉的危险——但 **`description_zh` 尚未进入白名单，这是 G1-B 动工前唯一必须先补的代码前置**。

---

## 0.5 测量时点与仓库状态快照（**必读**）

**测量时点**：2026-09-25 22:02–22:03（本地）。
**`HEAD`**：`5c9ace10a4ca4bb96860db3a48debf9ddcf496bf`（仍为审计基线，未变）。
**服务**：未启动。**pytest**：未运行。**git 写操作**：无。

### 0.5.1 `git status --porcelain`（46 条，比 G1-A §14.1 的 17 条大幅增长 —— 13 张卡已落地）

```text
 M agent/audit/chain.py
 M agent/audit/facade.py
 M agent/digital_life_persona.py
 M agent/model_router/adapters.py
 M agent/rate_limiter.py
 M agent/server_port_guard.py
 M agent/skills_mgmt/enhancer.py
 M agent/skills_mgmt/executor.py
 M agent/skills_mgmt/file_store.py
 M agent/skills_mgmt/index_cache.py
 M agent/skills_mgmt/loader.py
 M agent/skills_mgmt/registry.py
 M agent/skills_mgmt/service.py
 M agent/skills_mgmt/vector_adapter.py
 M agent/tool_gate.py
 M agent/tools/__init__.py
 M agent/tools_prompt_guard.py
 M app_server.py
 M plugins/chat.py
 M start_yunshu.bat
 M tests/unit/conftest.py
 M tests/unit/test_tool_gate.py
 M tests/unit/test_tools_prompt_alignment.py
?? _tmp_rootcause_probe/evidence/probeC_C1_BEFORE_raw_notools_1790331133.json
?? _tmp_rootcause_probe/evidence/probeC_C2_AFTER_aligned_notools_1790331135.json
?? _tmp_rootcause_probe/evidence/probeC_C3_POSITIVE_raw_with_tools_1790331138.json
?? docs/audit_skill_governance/
?? scripts/audit_governance_check.py
?? scripts/audit_retention.py
?? scripts/verify_index_drift.py
?? scripts/verify_route_log_sink.py
?? scripts/watchdog_yunshu.py
?? tests/unit/test_audit_governance_check.py
?? tests/unit/test_audit_read_path.py
?? tests/unit/test_backpressure.py
?? tests/unit/test_confirm_gate_no_bypass.py
?? tests/unit/test_retrieval_silent_failures.py
?? tests/unit/test_route_log_sink.py
?? tests/unit/test_s2_gate_is_not_false_green.py
?? tests/unit/test_skill_registry_audit.py
?? tests/unit/test_skill_update_audit.py
?? tests/unit/test_startup_no_gap.py
?? tests/unit/test_tool_count_consistency.py
?? tests/unit/test_undo_merge_governance_state.py
?? tests/unit/test_update_meta_no_data_loss.py
```

**`git status --porcelain -- data/` 为空** ⇒ `data/` 下**没有任何文件**与 HEAD 有差异，23 个 `skill.md` 全部是提交态内容。

### 0.5.2 G1-B 相关文件的 `git diff --numstat`（本卡实测）

```text
29	19	agent/digital_life_persona.py
90	2	agent/skills_mgmt/enhancer.py
190	8	agent/skills_mgmt/file_store.py      ← F1b
241	14	agent/skills_mgmt/index_cache.py
49	2	agent/skills_mgmt/loader.py          ← C1
59	7	agent/skills_mgmt/registry.py
125	6	agent/skills_mgmt/service.py
679	94	agent/skills_mgmt/vector_adapter.py
```

**交接核对**：任务卡说 F1b 是 `file_store.py` **+190/−8** —— **实测 `190 8` 完全一致**，说明 F1b 是本工作区对该文件的**唯一**未提交改动。
`vector_adapter.py` 现为 **+679/−94**（G1-A §14.2 记的是 612 行变更）⇒ **C1 之外还有后续改动**，这是行号漂移的原因。

### 0.5.3 `data/` 下的数据文件 mtime

```text
Name                             Length LastWriteTime
----                             ------ -------------
skills_mgmt.json                 188312 2026/9/25 18:43:31   ← 比 G1-A 测量时点新
skills.json                       10973 2026/9/23 1:20:00
descriptors.json                 364381 2026/9/17 21:40:56
skills_descriptions_overlay.json    534 2026/9/19 2:28:49
capability_manifest.json         699684 2026/9/24 23:32:01
skills_repo/.index/cache.json     34591 2026/9/25 19:20:49
```

`data/skills_repo/` 下 mtime 最新的两个是 `scripted-selftest/skill.md` 与 `memory_summary/skill.md`（均 `2026/9/25 18:43:57`），但 **`git status -- data/` 为空** ⇒ 它们是**同内容重写**（mtime 变了、字节没变）。**不影响任何结论。**

> **本卡副作用的自查**：以上 mtime 全部早于本卡开工时间（22:02）⇒ 本卡的探针**没有触碰生产 `data/`**。
>
> **但必须诚实声明**：`git status --porcelain` 的条数在本卡执行期间由 **46 条涨到 58 条**。逐条比对后，**新增的 13 条全部来自其他并发卡**，不是本卡产物：
>
> ```text
>  M .gitignore
>  M agent/orchestrator/orchestrator.py
>  M agent/workflow_learning/learner.py
>  M agent/workflow_learning/models.py
>  M agent/workflow_learning/repository.py
>  M agent/workflow_learning/service.py
> ?? scripts/audit_reseal_daily_root.py
> ?? tests/unit/test_advert_equals_dispatch_smart.py
> ?? tests/unit/test_audit_chain_concurrency.py
> ?? tests/unit/test_daily_root_reseal.py
> ?? tests/unit/test_undo_merge_dst_snapshot.py
> ?? tests/unit/test_workflow_learning_sanity.py
> ?? undefined/
> ```
>
> ⇒ **本卡的仓库内产物仍然只有本报告一个文件**；`data/` 仍然为空差异。**但这也意味着：G1-B 开工时看到的 `git status` 会比本报告记录的更脏 —— 行号复核必须当场重跑。**

---

## 1. G1-A §14.4 的 4 条重新对账命令：逐条原始输出与对照

> **执行说明（必须声明）**：第 ① 条原文用 `grep -n`。本机 `pwsh` 的 `PATH` 里**没有 `grep`**（实测 `Get-Command grep` 无输出）。因此改用**等价的 PowerShell 原语 `Select-String`**，**正则模式逐字照抄**，输出格式为 `文件:行号:行内容`。**这是唯一的方法替换，模式与目标文件未改。**

### ① 重核行号

**照卡执行的命令（替换见上）**：

```powershell
Select-String -Path agent/skills_mgmt/registry.py -Pattern "def as_legacy_rows"
Select-String -Path agent/skills_mgmt/vector_adapter.py -Pattern "def ensure_indexed","def _drop_native_chroma_ids_locked","def _remove_skill_vector","_indexed_content_hash"
Select-String -Path agent/digital_life_persona.py -Pattern "_SKILL_PROMPTS","def _build_skill_instructions"
```

**原始输出**：

```text
### 1a
agent\skills_mgmt\registry.py:205:    def as_legacy_rows(self) -> List[Dict[str, Any]]:
### 1b
agent\skills_mgmt\vector_adapter.py:246:        self._indexed_content_hash: Dict[str, str] = {}
agent\skills_mgmt\vector_adapter.py:700:        self._indexed_content_hash.clear()
agent\skills_mgmt\vector_adapter.py:703:    def _drop_native_chroma_ids_locked(self, skill_ids: Any) -> None:
agent\skills_mgmt\vector_adapter.py:719:    def ensure_indexed(self, *, force: bool = False) -> int:
agent\skills_mgmt\vector_adapter.py:764:                            # _indexed_content_hash。若只清 _indexed_skill_ids，下面的
agent\skills_mgmt\vector_adapter.py:789:                                    set(self._indexed_content_hash)) - current_ids):
agent\skills_mgmt\vector_adapter.py:794:                         if self._indexed_content_hash.get(sid) != hashes[sid]}
agent\skills_mgmt\vector_adapter.py:862:                            self._indexed_content_hash[sid] = hashes[sid]
agent\skills_mgmt\vector_adapter.py:878:                            self._indexed_content_hash[skill_id] = hashes[skill_id]
agent\skills_mgmt\vector_adapter.py:892:                                self._indexed_content_hash[skill_id] = hashes[skill_id]
agent\skills_mgmt\vector_adapter.py:962:    def _remove_skill_vector(self, skill_id: str) -> None:
agent\skills_mgmt\vector_adapter.py:967:    def _remove_skill_vector_locked(self, skill_id: str) -> None:
agent\skills_mgmt\vector_adapter.py:1012:        self._indexed_content_hash.pop(skill_id, None)
### 1c
agent\digital_life_persona.py:42:    _SKILL_PROMPTS = {
agent\digital_life_persona.py:425:    def _build_skill_instructions(self) -> str:
agent\digital_life_persona.py:442:        for sid, prompt in self._SKILL_PROMPTS.items():
```

**与 G1-A 当时的预期对照**：

| 落点 | G1-A 报告的值 | 本次实测 | 漂移 |
|---|---|---|---|
| `registry.py::as_legacy_rows` | 「当前工作区 `205-245`」（§9.3 / §0.5） | **205** | **0（完全命中）** |
| `vector_adapter.py::_indexed_content_hash`（字段定义） | `:220`（§9.5.1） | **246** | **+26** |
| `vector_adapter.py::_drop_native_chroma_ids_locked` | `:636-650`（§9.5.1） | **703** | **+67** |
| `vector_adapter.py::ensure_indexed` | 基线 `:349`；C1 后未给行号 | **719** | 无法对照（G1-A 未给 C1 后行号） |
| `vector_adapter.py::_remove_skill_vector` | `:920-927`（§9.5.1） | **962** | **+42** |
| `digital_life_persona.py::_SKILL_PROMPTS` | `42-62` | **42** | **0（起始行命中）** |
| `digital_life_persona.py::_build_skill_instructions` | 基线 `415-439`；§8.3 引用 `425-435` | **425** | **0（与 §8.3 命中）** |

**结论**：**漂移只发生在 `vector_adapter.py` 一族**（因为它在 C1 之后又被改过，`git diff --numstat` 现为 +679/−94）。**`registry.py` 与 `digital_life_persona.py` 的两个关键落点行号一个都没动。**
G1-A §0.5 要求「G1-B 开工前应重新对账一次行号」——**已完成，结果如上**。凡本报告后续引用 `vector_adapter.py` 的行号，均以**本次实测**为准。

### ② 重跑 §11.1 脚本 A（数据类结论）

**命令**：`python %TEMP%\g1b0\g1a_repro.py`（脚本**逐字复用 G1-A §11.1**，`ROOT` 为仓库绝对路径；脚本原文见 G1-A §11.1）

**原始输出**：

```text
== 清册 ==
S1 skill.md            : 23
S2 skills_mgmt.json    : 22
S3 data/skills.json    : 30 (镜像 agent/data/skills.json 同内容)
S4 overlay             : 4 ['self_reflection', 'email-helper', 'memory_summary', 'scripted-selftest']
S5 _CURATED_DESCRIPTIONS: 4 ['self_reflection', 'email-helper', 'memory_summary', 'scripted-selftest']
S6 _SKILL_PROMPTS      : 7 ['self_reflection', 'memory_summary', 'emotion_expression', 'proactive_suggestion', 'context_aware', 'safety_guard', 'voice_interaction']
S7 BUILTIN_EXTENSIONS  : 7 ['self_reflection', 'memory_summary', 'emotion_expression', 'proactive_suggestion', 'context_aware', 'safety_guard', 'voice_interaction']
S8 descriptors.json    : 29
S9 .index/cache.json   : 23

== S1 vs S2 : 双侧技能对比 ==
双侧: 15  描述不同: 15
S1 独有: ['context_aware', 'emotion_expression', 'memory_summary', 'proactive_suggestion', 'safety_guard', 'scripted-selftest', 'self_reflection', 'voice_interaction']
S2 独有: ['code-observability', 'engineering-test-delivery', 'frontend-state-sync', 'global-core-principles', 'self-explanatory-ui', 'skill', 'testing-anti-patterns']
23 ∪ 22 是否等于 legacy 30 : True 30

== 缓存与向量校验 ==
S9 hash 命中: 23 / 23  S9 description == S1: 23 / 23
chroma 向量条数: 8  覆盖 ids: ['context_aware', 'emotion_expression', 'memory_summary', 'proactive_suggestion', 'safety_guard', 'scripted-selftest', 'self_reflection', 'voice_interaction']
chroma description == S1 的条数: 8
```

**与 G1-A §11.1「预期输出（本次实测，逐字）」对照：逐行完全相同，无一处差异。**

### ③ 重跑 §11.5 脚本 E（capability 缺口）

**命令**：`python %TEMP%\g1b0\g1a_capreg.py`（脚本**逐字复用 G1-A §11.5**）

**原始输出**：

```text
items: 114
skills: 23 | skills with non-empty description: 0
tools : 91 | tools  with non-empty description: 91

sample skill item keys: ['name', 'capability_id', 'tenant_id', 'namespace', 'version', 'aliases', 'kind', 'tool_type', 'owner', 'registry_source', 'declared_in', 'location', 'location_reason', 'location_source', 'location_declared', 'location_confidence', 'plane', 'effect', 'risk', 'permission_level', 'needs_approval', 'internal', 'enabled', 'deprecated', 'llm_callable', 'callable_mode', 'callable_by', 'trigger', 'description', 'schema_registered', 'input_schema', 'result_schema', 'host_executor', 'mark', 'reachable', 'main_line_status', 'impl_status', 'impl_status_reason', 'health']
sample skill description: ''
sample tool  description: '把 unified diff 补丁应用到工作区（支持 --- a/路径、+++ b/路径 文件头与 ` -l,s +l,s ` hunk）。先逐 hunk 校验上下文，全部通过才写盘：任何一处不匹配都不会产生半截改动。dry_run=true 时只校验不写。不能删除文件，拒绝越出项目根目录的路径。Apply a '
```

**与 G1-A 预期「items: 114 / skills: 23 / skills with non-empty description: 0 / tools: 91 / tools with non-empty description: 91」对照：完全一致。**

**本卡补充（G1-A 未做的追问）**：既然 manifest 里**工具**条目也 0 条含 `description` 键（§5.4 实测），工具的描述从哪来？实测答案是**两条不同的构造路径**：

| 构造路径 | 代码位置 | description 来源 |
|---|---|---|
| 工具 `CapabilityRecord.from_tool_meta(meta, …)` | `agent/capregistry/spec.py:245`，`:290` 写 `description=str(getattr(meta, "description", "") or "")` | **YAML**（`agent.lines.models.load_tool_meta()`，`view.py:199`） |
| 技能 `CapabilityRecord.from_manifest_entry(d, …)` | `agent/capregistry/spec.py:307`，`:344` 写 `description=str(e.get("description") or "")` | **manifest 条目**（`view.py:232-243`） |

⇒ 这**正面支持** G1-A §9.4 的改法：技能必须**经过 manifest**，因此必须让 `callability.py` 把 `description` 写进 skill 条目。**不存在「绕开 manifest 也能补上」的替代路径。**

### ④ 确认「主轨先占位」仍然存在

**命令**：`python -c "import inspect,agent.skills_mgmt.registry as r;print(inspect.getsource(r.SkillRegistry.as_legacy_rows))"`

**原始输出（去控制台乱码后按 `registry.py:205-245` 逐字核对）**：

```python
    def as_legacy_rows(self) -> List[Dict[str, Any]]:
        """输出与旧 data/skills.json 行同构的只读列表（id/name/enabled/
        description/params），供需要旧格式的下游消费。"""
        svc = self._svc()
        rows: List[Dict[str, Any]] = []
        seen: Set[str] = set()
        # 主轨
        try:
            for skill in svc.store.list_all():
                sid = skill.id
                if sid in seen:
                    continue
                seen.add(sid)
                rows.append({
                    "id": sid,
                    "name": getattr(skill, "name", sid),
                    "enabled": bool(getattr(skill, "enabled", True)),
                    "description": str(getattr(skill, "description", "")
                                       or ""),
                    "params": dict(getattr(skill, "default_params", {})
                                   or {}),
                })
        except Exception:  # noqa: BLE001
            pass
        # 文件轨独有（persona 内置等，主轨未注册）
        try:
            meta_idx = svc.file_store.load_metadata_index(refresh=False)
            for sid, meta in sorted(meta_idx.items()):
                if sid in seen:
                    continue
                seen.add(sid)
                rows.append({
                    "id": sid,
                    "name": str(meta.get("name") or sid),
                    "enabled": bool(meta.get("enabled", True)),
                    "description": str(meta.get("description", "") or ""),
                    "params": {},
                })
        except Exception:  # noqa: BLE001
            pass
        return rows
```

（PowerShell 管道的控制台编码把中文 docstring/注释显示成乱码，上面是用 `read` 工具按 `registry.py:205-245` 读出的**正确文本**，逻辑与 `inspect.getsource` 输出一致。）

**判定**：**「主轨先占位、文件轨只补缺失」结构完好**：
- 主轨分支 `:211-228`，`seen.add(sid)` 在 `:217`；
- 文件轨分支 `:229-242`，`if sid in seen: continue` 在 `:233-235`。

**未被任何卡顺手改掉**。⇒ **M3 的改动目标依然有效，且行号与 G1-A 预告的 `205-245` 完全一致。**

### 1.5 4 条命令的总结论

| 命令 | 结果 |
|---|---|
| ① 行号 | **只有 `vector_adapter.py` 漂移**；`registry.py` / `digital_life_persona.py` 的关键落点未动 |
| ② 数据类结论 | **逐字复现**，零漂移 |
| ③ capability 缺口 | **完全一致**（0/23 仍成立） |
| ④ 合并规则 | **「主轨先占位」仍在** |

---

## 2. G1-A 关键结论逐条复核

### 2.1 技能描述存储的 **10 处**清单

| ID | 位置 | G1-A 条数 | **本次实测条数** | G1-A 行号 | **本次行号** | 判定 |
|---|---|---|---|---|---|---|
| **S1** | `data/skills_repo/<id>/skill.md` front matter `description` | 23 | **23** | 无行号（逐文件） | 无行号 | **存在、未变** |
| **S2** | `data/skills_mgmt.json[<id>].description` | 22 | **22** | `.gitignore:224` | **`.gitignore:224`** | **存在、未变**（文件 mtime 2026-09-25 18:43，但计数与内容口径未变） |
| **S3** | `data/skills.json`（legacy） | 30 | **30** | `.gitignore:148` | **`.gitignore:148`** | **存在、未变** |
| **S3b** | `agent/data/skills.json`（S3 字节级镜像） | 30 | **30** | `.gitignore:506` | **`.gitignore:506`** | **存在、未变**；md5 双方均为 `293ecbe151c91c167773abe745fc690c`，10973 B，**字节相同** |
| **S4** | `data/skills_descriptions_overlay.json`；读方 `plugins/skills.py::_apply_desc_overlay` | 4 | **4** | `plugins/skills.py:145-155` | **`145-155`** | **存在、行号未变** |
| **S5** | `plugins/skills.py::_CURATED_DESCRIPTIONS`；写方路由 | 4 | **4** | `:116-121`；写方 `:459-480` | **`:116-121`；写方 `:459-480`** | **存在、行号未变** |
| **S6** | `agent/digital_life_persona.py::_SKILL_PROMPTS` | 7 | **7** | `:42-62`；`_build_skill_instructions` `:415-439`（§8.3 引 `425-435`）；`_loaded_skill_ids` `:436` | **`:42-…`**；**`:425`**；**`:446`** | **存在**；`_SKILL_PROMPTS`/`_build_skill_instructions` 行号命中；**`_loaded_skill_ids` 由 436 → 446（+10）** |
| **S7** | `agent/extensions/base.py::BUILTIN_EXTENSIONS["skill"]` | 7 | **7** | `:98-142`（`"skill": [` 在 `:99`） | **`:98-142`，`"skill": [` 在 `:99`** | **存在、行号未变** |
| **S8** | `data/descriptors.json` 的 `capability.description` | 29 | **29** | `.gitignore:226` | **`.gitignore:226`** | **存在、未变**（mtime 仍为 `2026-09-17 21:40:56` —— 与 G1-A 实测的**同一时刻**） |
| **S9** | `data/skills_repo/.index/cache.json` | 23 | **23** | `.gitignore:250` | **`.gitignore:250`** | **存在、未变**；hash 命中 23/23，description 与 S1 逐字相同 23/23 |
| **S10** | `data/skill_vectors/native_chroma/chroma.sqlite3` | 8 | **8** | `.gitignore:211` | **`.gitignore:211`** | **存在、未变**；8/8 与 S1 逐字一致 |

**判定：10/10 处全部存在，条数一个没变，行号仅 `_loaded_skill_ids` 一处漂移（+10）。**

#### 2.1.1 顺带复核的三个「单一存储」语义（G1-A §8.2 的裁定前提）

| 断言 | 本次实测 | 判定 |
|---|---|---|
| S3b 是 S3 的字节级镜像 | `293ecbe151c91c167773abe745fc690c` / `293ecbe151c91c167773abe745fc690c` / `True` / 10973 / 10973 | **成立** |
| overlay 4 条中 3 条**结构性永不生效**（`plugins/skills.py:151-152` 要求原描述为空） | `self_reflection` 描述长 **61**、`memory_summary` **65**、`scripted-selftest` **89** —— 均非空 ⇒ 条件不成立 | **成立**（G1-A 报 61/65/89，**逐字相同**） |
| 第 4 条 `email-helper` 是**死键** | `skills_repo` 无目录 / `skills_mgmt.json` 无键 / `data/skills.json` 无 id —— 三处皆无 | **成立** |
| 合并视图 = S1 ∪ S2（30 条） | `as_legacy_rows()` 行数 **30**，`set(rows) == repo_ids ∪ mgmt_ids` → `True` | **成立** |

### 2.2 **15/23 双描述冲突**是否仍然存在（逐条重跑相似度）

**方法**：对每个 id 取 `S1 = yaml.safe_load(skill.md front matter)["description"]`、`S2 = skills_mgmt.json[id]["description"]`，算 `difflib.SequenceMatcher(None, S1, S2).ratio()`。**该度量反推出的 15 个数值与 G1-A §4.1 表格逐个相同，可判定 G1-A 用的就是同一度量。**

**原始输出**：

```text
=== A. 15 组冲突：S1 vs S2 相似度重跑 (difflib.SequenceMatcher.ratio) ===
双侧: 15  描述不同: 15
rank similarity  L_S1  L_S2  skill_id
1    0.077       210   101   pd-brainstorming-697b717a-skill
2    0.097       237   94    pd-verification-before-completion-af010352-skill
3    0.110       246   100   pd-frontend-design-77ea5c4e-skill
4    0.118       246   109   pd-receiving-code-review-8934157e-skill
5    0.122       212   100   pd-finishing-a-development-branch-e085de5a-skill
6    0.140       118   53    pd-dispatching-parallel-agents-b8065ccd-skill
7    0.141       116   54    pd-executing-plans-95cbf64a-skill
8    0.144       119   48    pd-requesting-code-review-ca5ae995-skill
9    0.169       96    46    pd-writing-plans-f846e3a2-skill
10   0.169       208   87    pd-using-git-worktrees-d516703a-skill
11   0.174       171   82    pd-using-superpowers-3aea3fc9-skill
12   0.180       97    36    pd-subagent-driven-development-8c375695-skill
13   0.190       103   55    pd-systematic-debugging-556faa20-skill
14   0.222       91    44    pd-test-driven-development-8562c8ad-skill
15   0.913       139   137   pd-writing-skills-5da20e67-skill
min=0.077 max=0.913  <0.25 的组数=14  区间[0.077,0.222]内=14
```

**与 G1-A §4.1 表格逐格对照**：

| 排名 | G1-A 相似度 | 本次 | G1-A L_S1 / L_S2 | 本次 | 一致？ |
|---|---|---|---|---|---|
| 1 pd-brainstorming | 0.077 | **0.077** | 210 / 101 | 210 / 101 | ✔ |
| 2 pd-verification-before-completion | 0.097 | **0.097** | 237 / 94 | 237 / 94 | ✔ |
| 3 pd-frontend-design | 0.110 | **0.110** | 246 / 100 | 246 / 100 | ✔ |
| 4 pd-receiving-code-review | 0.118 | **0.118** | 246 / 109 | 246 / 109 | ✔ |
| 5 pd-finishing-a-development-branch | 0.122 | **0.122** | 212 / 100 | 212 / 100 | ✔ |
| 6 pd-dispatching-parallel-agents | 0.140 | **0.140** | 118 / 53 | 118 / 53 | ✔ |
| 7 pd-executing-plans | 0.141 | **0.141** | 116 / 54 | 116 / 54 | ✔ |
| 8 pd-requesting-code-review | 0.144 | **0.144** | 119 / 48 | 119 / 48 | ✔ |
| 9 pd-writing-plans | 0.169 | **0.169** | 96 / 46 | 96 / 46 | ✔ |
| 10 pd-using-git-worktrees | 0.169 | **0.169** | 208 / 87 | 208 / 87 | ✔ |
| 11 pd-using-superpowers | 0.174 | **0.174** | 171 / 82 | 171 / 82 | ✔ |
| 12 pd-subagent-driven-development | 0.180 | **0.180** | 97 / 36 | 97 / 36 | ✔ |
| 13 pd-systematic-debugging | 0.190 | **0.190** | 103 / 55 | 103 / 55 | ✔ |
| 14 pd-test-driven-development | 0.222 | **0.222** | 91 / 44 | 91 / 44 | ✔ |
| 15 pd-writing-skills | 0.913 | **0.913** | 139 / 137 | 139 / 137 | ✔ |

**判定：15/23 冲突成立、15/15 全分歧成立、14 组落在 [0.077, 0.222] 成立、1 组 0.913 成立。逐条相似度与长度，15 项全部一致 —— 零漂移。**

**本卡补充**：`data/skills_mgmt.json` 的 mtime 已是 `2026-09-25 18:43:31`（G1-A 测量时点之后），但 **15 组的文案与长度一个字符都没变** ⇒ 该次重写是**内容等价**的。⇒ **G1-A 的「英→中互译」归因依然成立**，M2 要搬的中文译文与 G1-A 看到的是同一批。

### 2.3 唯一源裁定 = `data/skills_repo/<id>/skill.md`（前置理由复核）

**裁定本身**：G1-A §7.1 裁定唯一事实源为 skill.md front matter。**本次不推翻、不重开**，只核它列出的**前置理由是否仍成立**。

**理由 1（决定性）：只有 skill.md 被 git 跟踪** —— 命令与原始输出：

```text
### git ls-files --error-unmatch (skill.md / overlay)
data/skills_descriptions_overlay.json
data/skills_repo/self_reflection/skill.md
exit=0
### git check-ignore -v
.gitignore:224:data/skills_mgmt.json	data/skills_mgmt.json
.gitignore:148:data/skills.json	data/skills.json
.gitignore:506:agent/data/skills.json	agent/data/skills.json
.gitignore:226:data/descriptors.json	data/descriptors.json
.gitignore:250:data/skills_repo/.index/	data/skills_repo/.index/cache.json
.gitignore:211:data/skill_vectors/	data/skill_vectors/native_chroma/chroma.sqlite3
### git ls-files --error-unmatch for all 23 skill.md
tracked-check done        ← 循环里对 23 个 skill.md 逐个 --error-unmatch，无一个 NOT-TRACKED
```

**判定**：**理由 1 完全成立**。6 条 `check-ignore` 命中行号与 G1-A 写的 `.gitignore:224 / :148 / :506 / :226 / :250 / :211` **逐个相同**；且**新增了 G1-A 未做的更强验证**：不是只抽查 `self_reflection`，而是**对 23 个 skill.md 全量** `--error-unmatch`，**23/23 全部命中**。

**理由 2（skill.md 已是三个运行消费者的实际来源）**：`loader.py` / `context_injector.py` / `vector_adapter.py` 三路都落到 `load_metadata_index()`。C1 改了 `loader.py`（+49/−2）与 `vector_adapter.py`，故**必须重核这条链是否还成立**：

| 消费路径 | G1-A 引用 | 现状 |
|---|---|---|
| 检索 · TF-IDF | `loader.py:393` | `loader.py` 已被 C1 改动。**关键依赖未变**：三路仍取 `fs.load_metadata_index()`（`file_store.py:578-601`，走 `SkillIndexCache` 或 `SkillMDParser.parse(skill.md)`） |
| 检索 · 向量路补字段 | `loader.py:699` | 同上 |
| 检索 · BM25 路补字段 | `loader.py:795` | 同上 |
| 模型可见 | `context_injector.py:318` | 未在 `git status` 中，**未变** |
| 向量编码文本 | `vector_adapter.py:151-157` → 现 `_build_vector_text` 在 **`:285`**，`_vector_text_and_hash` 在 **`:682`** | 行号漂移，语义未变 |

**判定：理由 2 成立（结论未变），但 `vector_adapter.py` 的行号引用需以 `:285` / `:682` 替换。**

**理由 3（skill.md 已有可靠失效机制）**：脚本 A 实测 `S9 hash 命中: 23/23`、`S9 description == S1: 23/23`。**成立。**

**理由 4（唯一在「模型可见」链路上的一手来源）**：`context_injector.py` 未被改动，仍读 `SkillMatch.description` ← `loader.load_metadata_index()`。**成立。**

> **附**：G1-A §7.3 列的 5 项代价中，「`app_server.py:708-724` 的 `SkillsManager` 直接调 `as_legacy_rows()`」这条**行号已漂移**：实测 `class SkillsManager` 在 **`app_server.py:992`**，`as_legacy_rows()` 调用点在 **`:1000`** 与 **`:1008`**（+284）。**结论（UI 走 `as_legacy_rows`）不变，行号须更新。**

### 2.4 `CapabilityRegistry` 的 skill 条目无 `description`：行号复核

**G1-A §0 C7 说「`spec.py:344` 已读、`to_dict():229` 已输出，只需补 `callability.py` 两处」。逐行复核：**

| # | 文件 | G1-A 行号 | **本次实测** | 内容 | 判定 |
|---|---|---|---|---|---|
| 1 | `agent/capregistry/spec.py` | `:229` | **`:229`** | ` "description": self.description, `（在 `to_dict()` 里） | **未漂移，已就绪** |
| 2 | `agent/capregistry/spec.py` | `:344` | **`:344`** | `description=str(e.get("description") or ""),`（在 `from_manifest_entry` 里） | **未漂移，已就绪** |
| 3 | `agent/lines/callability.py` | `:982-988` | **`:982-988`** | `_slot()` 的默认字典（`out.setdefault(...)`，14 个默认键） | **未漂移** |
| 4 | `agent/lines/callability.py` | `:1000-1002` | **`:1000-1003`** | `fm = _front_matter(md)` 在 **`:1000`**；`slot["enabled"]/["status"]/["params"]` 在 `:1001-1003` | **未漂移**（G1-A 写 1000-1002，实际赋值块到 1003，属表述宽松） |
| 5 | `agent/lines/callability.py` | `:1164-1169` | **`:1164-1169`** | `_skill_entry` 的返回字典（`skill_in_repo` 在 `:1165`） | **未漂移** |
| 6 | `scripts/sync_capability_manifest.py` | `:165` | **`:165`** | `changed = [f for f in _FIELD_SPEC + _SPEC_REQUIRED_FIELDS + ("mark",) …]` | **未漂移** |
| 7 | `scripts/sync_capability_manifest.py` | （隐含）`description` 不在 `_FIELD_SPEC` | **`:59-61`** | `_FIELD_SPEC = ("tool_name","tool_type","llm_callable","callable_mode","schema_registered","host_executor","permission_level","sandbox_allowed","reason")` —— **无 `description`** | **R6 成立** |

**实测缺口复核原始输出**：

```text
manifest entries: 114 | skill: 23 | tool: 91
skill 条目含 description 键的条数: 0
tool  条目含 description 键的条数: 0
```

**判定**：
- **「skill 条目 0/23 无 description」成立，且行号零漂移**；
- **新增事实**：**工具条目 0/91 也没有该键** —— 工具的 description 走 `spec.py:290`（YAML），技能的走 `spec.py:344`（manifest）。⇒ G1-A §9.4 的改法（往 manifest 里补键）是**唯一可行路径**；
- **勘误**：G1-A §0 C7 说「只需在 `callability.py` **两处**补键」，而它自己的 §9.4 明列 **3 处**（`:982-988` 默认键、`:1000` 之后赋值、`:1164-1169` 返回字典）。**实测结论：3 处都要动**——少了 `:1164-1169` 的返回字典，`facts` 里的 description 根本进不了 manifest 条目。**§0 C7 的「两处」是笔误。**

### 2.5 「改中文会砸检索」：触发句式 **17/23 → 13/23** 重跑

**命令**：`python %TEMP%\g1b0\g1b0_verify.py`（B 段，**逐字复用 G1-A §11.6 脚本 F**）

**原始输出**：

```text
=== B. 触发句式覆盖（G1-A §11.6 脚本 F 原文） ===
现状(全用 skill.md):   17/23 = 73.9%
若改用中文译文:        13/23 = 56.5%
仅 skill.md 命中、替代方案不命中的 id (5): ['pd-executing-plans-95cbf64a-skill', 'pd-finishing-a-development-branch-e085de5a-skill', 'pd-frontend-design-77ea5c4e-skill', 'pd-requesting-code-review-ca5ae995-skill', 'pd-subagent-driven-development-8c375695-skill']
```

**判定：17/23（73.9%）→ 13/23（56.5%）**，与 G1-A §0 C14 / §10 R2 的数值**完全一致，零漂移**。
**本卡补充**：失去触发句式的 5 条是 `pd-executing-plans` / `pd-finishing-a-development-branch` / `pd-frontend-design` / `pd-requesting-code-review` / `pd-subagent-driven-development`。
⇒ **R2 的硬约束不变：`description` 必须保留英文原文，中文另存 `description_zh`。这不是风格问题。**

### 2.6 其它可复核结论的现状

| G1-A 结论 | 复核方式 | 结果 |
|---|---|---|
| C8「`.index/cache.json` 安全，改 skill.md 无需手动重建缓存」 | 脚本 A | **成立**：hash 23/23、description 23/23 |
| C9「legacy 不是独立源，是合并视图快照」 | `set(DIRS) ∪ set(MGMT) == set(LEG)` → `True 30` | **成立** |
| R7「本地跑 `compare_skills_legacy_vs_repo.py` 报 15 处差异 + 7 个 only_legacy」 | 实跑该脚本（只读） | **成立**：退出码 `exit=1`，`[SET] 只有在旧格式: ['code-observability','engineering-test-delivery','frontend-state-sync','global-core-principles','self-explanatory-ui','skill','testing-anti-patterns']`（= 7 条），逐行 DIFF 全部落在 15 条 `pd-*` 的 `description` 字段 |
| C6「`self_reflection`/`memory_summary` 是 4 套文案 / 8 处存储」 | 脚本 A 清册 + overlay 运行时复核 | **成立** |
| R5「审计链 `descriptor.*` 占比」 | 本卡未重跑（与 G1-B 开工判据无关，且会对 `audit_chain.db` 做全表扫描） | **未复核（沿用 G1-A）** |

### 2.7 本次发现的 **G1-A 勘误（3 条）**

| # | G1-A 的说法 | 本次实测 | 性质 |
|---|---|---|---|
| **E-1** | M4 前端落点 = `yunshu-ui/src/pages/hub/memory/skills.tsx` **与** `skill-center.tsx` | `skill-center.tsx` 里 **`description` 零命中**（只有 tab 标签的 `desc`，`:21-24 / :58 / :70`）。真正渲染技能描述的是 **`skills.tsx:249`** 的 `{r.description && …}`（`:38` 是类型声明、`:264` 是空态分支、`:150` 是写 overlay 的调用） | **落点收窄为 1 个文件、1 处渲染** |
| **E-2** | M0 只需处理 `POST /api/skills/describe/auto`（`plugins/skills.py:459-480`） | 还有**第二条 overlay 写路径** `POST /api/skills/describe`（**`plugins/skills.py:443-456`**，`overlay[skill_id] = {"description": description}`，**可写任意 id**），前端调用点在 **`skills.tsx:150`**；另有 `service.py:1315` 的自动补描述直接写**主轨** | **M0 的写路径是 3 条，不是 1 条** |
| **E-3** | §0 C7「只需在 `callability.py` **两处**补键」 | 与自身 §9.4 的 3 处自相矛盾；**实测正确值是 3 处**（`:982-988`、`:1000` 之后、`:1164-1169`） | **数字勘误** |

---

## 3. F1b 的影响评估（G1-A 当时没有这个前置）

### 3.1 实测一：改 `skill.md` 的 `description`，除该行外是否逐字节不变？

**方法**：把 23 个技能目录 `copytree` 到 `%TEMP%` 下的**临时仓库**，`SkillFileStore(repo_path=临时仓库)`，逐个调 `update_meta(id, {"description": "G1B0-PROBE-DESC-<id>"})`；再用一个**与实现无关**的判定：把 front matter 中 `description` 顶层键所占的行范围（用模块自己的 `_find_fm_key` 定位）从**改前**与**改后**文本里各去掉，比较剩余部分是否逐字节相同。

**原始输出**：

```text
临时仓库: C:\Windows\TEMP\g1b0_f1b_uaifwlyi\skills_repo （生产 data/skills_repo 未被触碰）

=== T1: 改 description 后，除该键所在行外是否逐字节不变 ===
skill_id                                            orig_endNL new_endNL rest_identical  deltaB  eol
-------------------------------------------------------------------------------------------------------
context_aware                                       True       True      identical       -115    '\r\n'
emotion_expression                                  True       True      identical       -17     '\r\n'
memory_summary                                      True       True      identical       -163    '\r\n'
pd-brainstorming-697b717a-skill                     False      True      identical(+tailEOL)-185    '\r\n'
pd-dispatching-parallel-agents-b8065ccd-skill       False      True      identical(+tailEOL)-76     '\r\n'
pd-executing-plans-95cbf64a-skill                   False      True      identical(+tailEOL)-86     '\r\n'
pd-finishing-a-development-branch-e085de5a-skill    False      True      identical(+tailEOL)-170    '\r\n'
pd-frontend-design-77ea5c4e-skill                   False      True      identical(+tailEOL)-219    '\r\n'
pd-receiving-code-review-8934157e-skill             False      True      identical(+tailEOL)-213    '\r\n'
pd-requesting-code-review-ca5ae995-skill            False      True      identical(+tailEOL)-82     '\r\n'
pd-subagent-driven-development-8c375695-skill       False      True      identical(+tailEOL)-55     '\r\n'
pd-systematic-debugging-556faa20-skill              False      True      identical(+tailEOL)-68     '\r\n'
pd-test-driven-development-8562c8ad-skill           False      True      identical(+tailEOL)-53     '\r\n'
pd-using-git-worktrees-d516703a-skill               False      True      identical(+tailEOL)-177    '\r\n'
pd-using-superpowers-3aea3fc9-skill                 False      True      identical(+tailEOL)-142    '\r\n'
pd-verification-before-completion-af010352-skill    False      True      identical(+tailEOL)-195    '\r\n'
pd-writing-plans-f846e3a2-skill                     False      True      identical(+tailEOL)-68     '\r\n'
pd-writing-skills-5da20e67-skill                    False      True      identical(+tailEOL)-248    '\r\n'
proactive_suggestion                                True       True      identical       -81     '\r\n'
safety_guard                                        True       True      identical       -125    '\r\n'
scripted-selftest                                   True       True      identical       -140    '\r\n'
self_reflection                                     True       True      identical       -150    '\r\n'
voice_interaction                                   True       True      identical       -132    '\r\n'

不满足「除目标键外逐字节不变」的技能: 无 —— 23/23 通过
```

**读法**：`identical` = 改前改后**去掉 description 行范围后完全相同**（8 个原本有末尾换行的技能）；`identical(+tailEOL)` = 完全相同**且**额外在 EOF 恰好多出 1 个 CRLF（15 个原本缺末尾换行的 `pd-*`）。`deltaB` 是整文件字节数变化（负数是因为折行的英文长描述被短探针替换，属探针效应，**不是**替换机制造成的）。

**补充实测（EOL 事实）**：**23 个 `skill.md` 全部是 CRLF**（`b"\r\n"` 计数 == `b"\n"` 计数），例如 `pd-brainstorming` 首 80 字节 `b'---\r\nid: pd-brainstorming-697b717a-skill\r\nname: brainstorming\r\ndescription: You '`。⇒ `patch_front_matter` 的 `eol` 检测取到 CRLF，**补的也是 CRLF**，不会引入 LF/CRLF 混排。

**具体 diff 样例（`pd-brainstorming`，含末尾换行补全）**：

```diff
--- skill.md(原)
+++ skill.md(改后)
` -3,5 +3,3 `
 name: brainstorming
-description: You MUST use this before any creative work - creating features, building
-  components, adding functionality, or modifying behavior. Explores user intent, requirements
-  and design before implementation.。由 1 份素材蒸馏生成
+description: 改后的描述（探针）
 content_type: markdown
` -118,2 +116,2 `
 ## 来源
-- brainstorming
+- brainstorming
```

（第二个 hunk 就是「补末尾换行」：`- brainstorming`（无换行结尾）→ `+ brainstorming\r\n`。git 把它显示成一次删加，实际**只多了一个 CRLF**。净行数变化 `+2 / -4`。）

**判定 3.1**：**是。现在改 `description`，除了该键所占的行范围以外，逐字节不变。23/23 通过。** 折行标量（`description:` 跨 3 行）被**整体**替换，不会残留孤儿续行；键顺序、`content_type:` 等相邻键、body 全部原样。

### 3.2 实测二：15 个缺末尾换行的 `pd-*` 文件清单与「一次性补换行」

**当前清单（实测，原始输出）**：

```text
=== C. pd-* 缺末尾换行清单 ===
缺末尾换行的 skill.md 数: 15 / 23
   pd-brainstorming-697b717a-skill 5351 bytes, 末 16 字节: b'\n- brainstorming'
   pd-dispatching-parallel-agents-b8065ccd-skill 3206 bytes, 末 16 字节: b'-parallel-agents'
   pd-executing-plans-95cbf64a-skill 2894 bytes, 末 16 字节: b' executing-plans'
   pd-finishing-a-development-branch-e085de5a-skill 3366 bytes, 末 16 字节: b'velopment-branch'
   pd-frontend-design-77ea5c4e-skill 3286 bytes, 末 16 字节: b' frontend-design'
   pd-receiving-code-review-8934157e-skill 3260 bytes, 末 16 字节: b'ving-code-review'
   pd-requesting-code-review-ca5ae995-skill 2671 bytes, 末 16 字节: b'ting-code-review'
   pd-subagent-driven-development-8c375695-skill 3216 bytes, 末 16 字节: b'iven-development'
   pd-systematic-debugging-556faa20-skill 2760 bytes, 末 16 字节: b'ematic-debugging'
   pd-test-driven-development-8562c8ad-skill 2495 bytes, 末 16 字节: b'iven-development'
   pd-using-git-worktrees-d516703a-skill 3718 bytes, 末 16 字节: b'ng-git-worktrees'
   pd-using-superpowers-3aea3fc9-skill 1253 bytes, 末 16 字节: b'sing-superpowers'
   pd-verification-before-completion-af010352-skill 3333 bytes, 末 16 字节: b'efore-completion'
   pd-writing-plans-f846e3a2-skill 3271 bytes, 末 16 字节: b'\n- writing-plans'
   pd-writing-skills-5da20e67-skill 2484 bytes, 末 16 字节: b'- writing-skills'
其中 pd-* 前缀: 15
非 pd-* 的: []
```

**清单（15 个，全部是 `pd-*`）**：
`pd-brainstorming-697b717a-skill`、`pd-dispatching-parallel-agents-b8065ccd-skill`、`pd-executing-plans-95cbf64a-skill`、`pd-finishing-a-development-branch-e085de5a-skill`、`pd-frontend-design-77ea5c4e-skill`、`pd-receiving-code-review-8934157e-skill`、`pd-requesting-code-review-ca5ae995-skill`、`pd-subagent-driven-development-8c375695-skill`、`pd-systematic-debugging-556faa20-skill`、`pd-test-driven-development-8562c8ad-skill`、`pd-using-git-worktrees-d516703a-skill`、`pd-using-superpowers-3aea3fc9-skill`、`pd-verification-before-completion-af010352-skill`、`pd-writing-plans-f846e3a2-skill`、`pd-writing-skills-5da20e67-skill`。

**「一次性」实测**：

```text
=== T5: 换行补全是「一次性」还是每次追加 ===
第2次后末尾 CRLF 个数: 1 | 末尾 6 字节: b'lans\r\n'
第1→2次只差 description 行吗: True
第2次后是否出现连续空行结尾: False
```

**判定 3.2**：
- 缺末尾换行的**恰好 15 个文件，全部是 `pd-*`，无例外**（非 `pd-*` 的 8 个文件都正常以 CRLF 结尾）。
- 第一次 `update_meta` 后**每个文件恰好在 EOF 补 1 个 CRLF**；**第二次不再追加**（不会累积成空行）。
- ⇒ **这正是可以在 G1-B 里「提前声明的一次性 diff」**（见 §4.4）。

**另：幂等性实测**

```text
=== T3: 幂等性（同值重复 update_meta） ===
同值重写后字节变化: 无（23/23 幂等）
```

⇒ **M2 里「值未变就不写」的安全网是实测有效的**：重复执行 M2 不会产生噪声 diff。

### 3.3 实测三（**本轮最关键**）：M2 的前置条件是否还成立？

G1-A §9.2 的 M2 要求「为 15 条 `pd-*` 的 skill.md 增加 `description_zh` 键」。**F1b 之后这个前置变了，必须实测。**

**（a）`description_zh` 现在能不能被 `update_meta` 写进去？**

```text
=== T4: description_zh 能否被 update_meta 写入 ===
文件是否变化: False | 文件含 description_zh: False
```

**静态证据**：

```text
_META_FIELDS 起始行: 75
_META_FIELDS 结束行: 82
  含 description_zh? False
file_store.py 中出现 description_zh 次数: 0
```

`file_store.py:75-82` 的 18 个白名单键：
`id, name, description, category, tags, version, enabled, status, author, source, source_url, content_type, default_params, dependencies, config_schema, output_schema, is_sensitive, isolation_strategy`
—— **没有 `description_zh`**。且 `patch_front_matter` 在 `:277-281` 明确「白名单外的 patch 键不写进文件」。

**（b）手工写进 skill.md 的 `description_zh`，`parse()` 看得见吗？**

```text
=== T8: 把 description_zh 加入 _META_FIELDS（进程内 monkeypatch）后的可见性 ===
未打补丁: description_zh 可见 = False
打补丁后: description_zh 可见 = True | 值: '中文展示文案探针'
打补丁后 load_metadata_index 带出 description_zh = True
打补丁后 update_meta 能写入 description_zh = True | 索引读到: '新的中文展示文案'
```

因为 `SkillMDParser.parse()` 在 `file_store.py:154-155` 做 `meta = {k: v for k, v in meta.items() if k in _META_FIELDS}`，**白名单外键在 parse 阶段就被丢掉**。

**（c）F1b 的修复，让手工植入的 `description_zh` 不会被后续改写抹掉吗？**

```text
=== T7: 手工植入 description_zh，看 F1b 最小侵入改写是否原样保留 ===
注入位置行号(front matter 内): 3
注入后 parse 能否通过: 是
update_meta 后 description_zh 仍存在: True | 逐字保留: True | ['description_zh: 中文展示文案探针']
除 description 键外逐字节不变（含 description_zh 行）: True
```

**（d）对照：修复前的 `parse → serialize` 路径会把它删掉**（本卡实测复现旧路径）：

```text
当前 _META_FIELDS (18 个): [... 'description', ...]
含 description_zh: False
parse 后 meta 键: ['description', 'id', 'name']
serialize 后是否仍含 description_zh: False
serialize 后是否仍含 unknown_custom_field: False
---- serialize 输出（= 修复前 update_meta 的写回结果） ----
---
id: probe
name: probe
description: English original
---

body line
---- patch_front_matter 输出（F1b 之后的路径） ----
---
id: probe
name: probe
description: NEW English
description_zh: 中文展示文案
unknown_custom_field: keep-me
---
body line
```

`file_store.py:823-829` 的 docstring 自述：修复前 `update_meta` 就是 `parse → serialize`。

**判定 3.3（这是 G1-B 开工前必须知道的事）**：

| # | 结论 |
|---|---|
| **P-1** | **M2 现在还不能直接开做**：`description_zh` 不在 `_META_FIELDS`，用 `update_meta` 写会被**静默忽略**（只有一条 `update_meta.ignored_key` 的 debug 日志）。**必须先补 `file_store.py:75-82` 的那 1 行。** |
| **P-2** | **只补这 1 行就够了**：实测（进程内 monkeypatch，不改仓库文件）证明，把 `description_zh` 加进 `_META_FIELDS` 之后，`parse()` 可见、`load_metadata_index()` 带出、`update_meta()` 可写 —— **三件事一次到位，不需要改 `parse`/`serialize`/`patch_front_matter` 的任何逻辑**。 |
| **P-3** | **F1b 恰好把 M2 从「脆弱」变成「可存活」**：修复前，`description_zh` 即便被手工写进 skill.md，**下一次任何技能的启停走 `update_meta` 都会把它静默删除**（本卡已实测复现该删除行为）。**这是 G1-A 当时无法预见的收益 —— F1b 是 M2 的隐性前置。** |
| **P-4** | **不要用「直接手写文件」绕开白名单**：手写能落盘，但 `parse()` 看不见 ⇒ `load_metadata_index()` 不带出 ⇒ M3/M4 的读路径拿不到中文 ⇒ **UI 会从中文变英文（R1 触发）**。白名单那一行**必须补**。 |

### 3.4 **结论：G1-B 现在可以开工了吗？**

> ### ✅ **可以开工（YES），但 §4.2 的 S0（1 行白名单）必须最先做，且 §4.5 的决策点必须先有人拍板。**

**已满足的前置（4 项，全部实测）**：

| 前置 | 状态 | 证据 |
|---|---|---|
| ① 数据面稳定、G1-A 结论未失效 | ✅ | §1 命令②逐字复现；§2.2 逐条相似度零漂移；`git status -- data/` 为空 |
| ② 唯一源裁定仍有效（可进 CI） | ✅ | §2.3：23/23 skill.md 被跟踪；gitignore 行号未变 |
| ③ 合并规则未被顺手改掉 | ✅ | §1 命令④：主轨先占位仍在 `registry.py:205-245` |
| ④ **`update_meta` 已是最小侵入改写**（M2 的落盘安全） | ✅ | §3.1：23/23 通过；§3.2：幂等 23/23 |

**未满足的前置（3 项）**：

| # | 未满足项 | 严重度 | 补法 |
|---|---|---|---|
| **U-1** | **`description_zh` 不在 `_META_FIELDS`** | **阻塞（1 行代码）** | `file_store.py:75-82` 加 `description_zh`。**不做这一步，M2 直接静默失败**（§3.3 P-1） |
| **U-2** | **M0 的写路径冻结范围被 G1-A 低估** | 中（会导致 R11「删了又回来」） | 除 `plugins/skills.py:459-480` 外，还必须处理 **`:443-456`**（`POST /api/skills/describe`，可写任意 id）与 **`service.py:1624` 白名单**、**`service.py:1315`** 的自动补描述（§2.7 E-2） |
| **U-3** | **5 个产品决策未拍板** | 高（决定 M2 写什么） | 见 §4.5 |

**不需要等的前置（已澄清，可省掉）**：
- **不需要等 C1**（G1-A §9.5.2 已合流；本卡确认 `vector_adapter.py` 又被改过 +679/−94，C1 的机制仍在：`_drop_native_chroma_ids_locked` 在 `:703`、增量 erase 在 `:1012`）；
- **不需要为「15 个文件补换行」单独开卡**：把它作为 M2 的**已声明副作用**一次带过（§4.4）。

---
## 4. G1-B 的最终执行清单

### 4.1 要改的确切文件与站点数（**重新数一遍，每个数字给来源**）

| 步 | 文件 | 站点数 | 精确落点（**本次实测行号**） | **数字来源** |
|---|---|---|---|---|
| **S0** | `agent/skills_mgmt/file_store.py` | **1** | `_META_FIELDS`（`:75-82`）加 `"description_zh"` | `read file_store.py:75-82`（18 键，无 description_zh）+ §3.3 T8 的 monkeypatch 验证 |
| **M0** | `plugins/skills.py` | **2** | `:459-480`（`describe/auto` 改 no-op）、**`:443-456`（`describe` 单 id 也改 no-op / 拒绝写 overlay）** | `grep "api/skills/describe" plugins/skills.py` → `443` / `459` |
| **M0** | `agent/skills_mgmt/service.py` | **2** | `:1624-1628` 的 `allowed` 集合去掉 `"description"`；`:1315` 的 `self.update(s.id, {"description": new_desc})` 需显式决策（建议改为写 skill.md 或直接停用该动作） | `grep "description" service.py` → `:1624` 白名单、`:1315` 自动补全 |
| **M1** | `data/skills_repo/.migration/descriptions.baseline.json`（**新建**） | **1 新文件 / 23 条记录** | 目录名以 `.` 开头 ⇒ `file_store.py:587` 的 `entry.name.startswith(".")` 会跳过它 ⇒ **不会污染技能索引**（本卡实测该行） | `read file_store.py:585-591` |
| **M2** | `data/skills_repo/<id>/skill.md` | **15 个文件**（= 有 S2 中文译文的那 15 条） | 每条加 1 行 `description_zh:` | 命令② 输出 `双侧: 15  描述不同: 15`；S1 独有 8 / S2 独有 7 |
| **M3** | `agent/skills_mgmt/registry.py` | **3 行**（+1 个逃生开关） | `:222-223` description 取值改为「文件轨优先、回落主轨」；`:218-226` 主轨行字典加 `"description_zh"`；**`:236-242` 文件轨行字典也要加 `"description_zh"`** | `read registry.py:205-245`；**注意 G1-A 说「文件轨分支会自动带上 description_zh」是错的** —— 该分支是**显式 5 键字典**（`:236-242`），不加就与主轨行形状不一致 |
| **M4** | `yunshu-ui/src/pages/hub/memory/skills.tsx` | **1 处渲染 + 1 处类型** | `:249` 的 `{r.description && …}` 改为 `r.description_zh \|\| r.description`；`:38` 类型声明加 `description_zh?: string` | `grep description skills.tsx` → 38/144/150/249/264；`skill-center.tsx` **零命中**（§2.7 E-1） |
| **M4b** | `agent/skills_mgmt/searcher.py` | **待定**（R10） | `:41-62` 的 description 分词加权，改为 `description_zh` + `description` 同时计分 | G1-A §10 R10；**本卡未实测该器行为**（标【推测】需回归） |
| **M5** | `agent/lines/callability.py` | **3** | `:982-988` 加 `"description": ""` 默认键；`:1000` 之后加 `slot["description"] = str(fm.get("description") or "")`；`:1164-1169` 返回字典加 `"description": str(facts.get("description") or "")` | `read callability.py` 三处；`_skill_entry` 被 `:1198`/`:1374` 调用；**C7 说「两处」是笔误**（§2.4） |
| **M5** | `scripts/sync_capability_manifest.py` | **1** | `:165` 的元组加 `"description"`（`_FIELD_SPEC` 在 `:59-61` 无该键） | `read sync_capability_manifest.py:59-65, 155-166` |
| **M6** | `data/skills_descriptions_overlay.json`、`plugins/skills.py:116-121` | **2** | overlay 清空为 `{}`；`_CURATED_DESCRIPTIONS` 删除 | 命令② 清册；§2.1.1 三条永不生效 + 1 死键 |
| **M7** | （无代码改动）验证脚本 + 新增回归测试 | **1 新测试文件** | C1 机制的 V-verify / V-regress / V-guard | G1-A §9.5.2 |
| **M8** | （重跑，无代码改动） | 0 | `agent/descriptors/backfill.py`、`store.py` 的 legacy 重建 | G1-A §9.2 M8 |
| **M9** | `tests/unit/test_skill_description_single_source.py`（新建）+ workflow | **2 新文件** | G-1 … G-7 七组断言 | G1-A §9.6 |

**改动总量**：**6 个既有代码文件、约 13 个代码站点、15 个 skill.md 文件、1 个前端文件（1 处渲染）**；新增 3 个文件（baseline / 守卫测试 / workflow）。

### 4.2 分步顺序（**顺序不可颠倒**）

```text
S0  ★ description_zh 进 _META_FIELDS（1 行）          ← G1-B 第一件事，本卡新增
     │   不做则 M2 静默失败（§3.3 P-1）
     ▼
M0  ★ 冻结写路径（3 条写路径，不是 1 条）              ← 必须早于 M6，否则 overlay 会被重建（R11）
     ▼
M1  ★ 固化现状基线（新建 .migration/descriptions.baseline.json，23 条）
     │   目录以 . 开头 ⇒ 不进技能索引（file_store.py:587）
     ▼
M2  ★ 中文文案回写：15 个 skill.md 加 description_zh   ← 【不可颠倒①】必须早于 M3
     │   · description 一字不改（R2：若改中文，触发句式 17/23→13/23）
     │   · 副作用：15 个文件各补 1 个末尾 CRLF（一次性，见 §4.4）
     ▼
M4  ★ UI 优先读 description_zh                        ← 必须早于/同批于 M3
     │   · 目标：切换瞬间「人看到的文案逐字不变」
     ▼
M3  ★ 改合并规则 as_legacy_rows（文件轨优先）
     │   · 逃生开关 CP_SKILL_DESC_FROM_FILE_TRACK=0
     ▼
M5    CapabilityRegistry 补 description（3 + 1 站点）+ 重跑 sync
     ▼
M6    清理 overlay / _CURATED_DESCRIPTIONS（依赖 M0）
     ▼
M8  ★ 数据快照回填（descriptors / legacy）             ← 【不可颠倒②】必须晚于 M2/M3 定稿
     ▼
M7    向量同步验证（V-verify / V-regress / V-guard）—— 离线窗口做，别挂请求/启动线程
     ▼
M9    守卫测试 + CI 接入
```

**「不可颠倒」的两条**（G1-A §9.1，本卡复核**仍然成立**）：
1. **`description_zh` 回写必须早于合并规则改动** —— 因为 M3 之后 UI 的 `description` 会从中文变英文，只有 M4 先读到 `description_zh` 才能让文案**逐字不变**（R1）。
2. **数据快照回填必须晚于 skill.md 定稿** —— 否则 descriptors / legacy 记录的是中间态（R4 / R7）。

**本卡新增的第三条**：
3. **S0 必须早于 M2** —— 白名单不补，M2 的写入会被 `patch_front_matter` 的 `:277-281` 静默丢弃。

### 4.3 每步的验证方式与可回滚方式

| 步 | 验证方式（命令级） | 回滚方式 |
|---|---|---|
| **S0** | `python -c "from agent.skills_mgmt.file_store import _META_FIELDS as m;assert 'description_zh' in m;print(len(m))"` → 应输出 19 | **定向 revert 那一行**。**不要 `git checkout agent/skills_mgmt/file_store.py`** —— 该文件当前是「已修改未提交」（F1b +190/−8），整文件 checkout 会把 F1b 一起回退 |
| **M0** | `POST /api/skills/describe` 与 `POST /api/skills/describe/auto` 均返回 `applied: []` / 被拒；随后 overlay 文件的键数不再增长 | revert `plugins/skills.py` + `service.py` 的对应行 |
| **M1** | `python -c "import json;d=json.load(open('data/skills_repo/.migration/descriptions.baseline.json',encoding='utf-8'));print(len(d['skills']), sum(1 for v in d['skills'] if v['s1']!=v['s2']))"` ⇒ `23 15` | 删文件 |
| **M2** | 逐条断言 ① `fm["description_zh"] == baseline[id]["s2"]`（15/15）；② `fm["description"]` **逐字未变**（15/15）；③ `git diff --stat data/skills_repo` 只有 15 个文件、每个 +2 行（1 行 `description_zh` + 1 行末尾换行） | `git checkout data/skills_repo/`（**这一步是安全的：`git status -- data/` 为空，23 个 skill.md 全部是提交态**） |
| **M4** | 人工打开技能管理页，核对 15 条 `pd-*` 的中文说明与 M2 之前**逐字相同** | revert 前端并重新构建 |
| **M3** | 断言 15 条的 `description` == skill.md 的 `description`（15/15），且 `description_zh` == baseline（15/15）；再置 `CP_SKILL_DESC_FROM_FILE_TRACK=0` 断言回退到主轨 | revert `registry.py` **或** 置环境变量为 `0`（不改代码即可回滚） |
| **M5** | 三条断言（G1-A §9.4）：① manifest 23 条 skill 的 `description` 非空；② 逐字等于 skill.md；③ `build_registry().list_envelope()` 的 23 条 skill `description` 非空 | revert 两个 .py 并重跑 `sync`；manifest 本身也被跟踪，可 checkout |
| **M6** | `grep -rn "_CURATED_DESCRIPTIONS" agent/ plugins/ tests/` = 0；overlay 为 `{}`；**重启后不再出现 4 条** | revert 两文件 |
| **M7** | 改一条 description ⇒ `ensure_indexed.done` 的 `indexed_or_refreshed >= 1`；离线只读 SQLite 断言 `embedding_metadata.description` 已是新值；再断言加 `description_zh` **不改变** `_vector_text_and_hash` 的哈希（当前在 `vector_adapter.py:682`） | 重跑一次全量重建 |
| **M8** | `descriptors.json` mtime 更新；29 条 skill 的 description == 对应 skill.md；`python scripts/compare_skills_legacy_vs_repo.py` 的 15 处 DIFF **归零** | 重新生成（无历史依赖） |
| **M9** | `pytest tests/unit/test_skill_description_single_source.py` 全绿；**故意在主轨改一条 description ⇒ 必须变红** | 移除测试/workflow |

> **关于回滚的一条警告**：仓库里 8 个待改代码文件**全部是「已修改未提交」状态**（§0.5.1）。`git checkout <file>` 会**连同其他 12 张卡的成果一起回退**。⇒ **G1-B 的每一步回滚都必须是「定向 revert 自己那几行」，禁止整文件 checkout。**

### 4.4 **预期的一次性 diff（提前声明）**

| # | 一次性 diff | 预期规模 | 依据 |
|---|---|---|---|
| **D-1** | **15 个 `pd-*` 的 skill.md 各补 1 个末尾 CRLF** | 15 个文件 × 1 个 CRLF（共 30 字节） | §3.2 实测：`orig_endNL=False → new_endNL=True`，且「第 2 次不再追加」 |
| **D-2** | 15 个 skill.md 各新增 1 行 `description_zh: …` | 15 行 | §4.1 M2 |
| **D-3** | `.index/cache.json` 的 23 条里，被改的 15 条 hash 与 mtime 会更新 | 15 条 | `index_cache._entry_valid` 双校验（G1-A §7.2 理由 3）；命令② 实测 hash 23/23 命中 |
| **D-4** | `data/skills.json` / `agent/data/skills.json` 在 M8 重建后**双份同时**更新且仍互为字节镜像 | 2 个文件 | §2.1.1 实测两份 md5 相同 |
| **D-5** | `data/descriptors.json` 在 M8 后 mtime 更新 + 若干条文本改变（含 `pd-writing-skills` 的尾缀重复被修正） | 29 条 skill 中的 8 条文件轨记录 | G1-A §8.2 / R4 |
| **D-6** | 审计链追加一批 `descriptor.register`（**必须在迁移记录里显式声明为预期**） | G1-A 报现状 `descriptor.*` 53,612 条 / `capability:cp.skill.*` 927 条 | G1-A R5（**本卡未重跑，沿用**） |
| **D-7** | **不应出现**的 diff：`data/skills_repo/` 下 8 个非 `pd-*` 文件的 `description` 变化 | 0 | §3.1 实测 |

> **重要**：D-1 与 D-2 应当**出现在同一个 hunk 组里**（description 改写 hunk + EOF 换行 hunk）。若 reviewer 看到某个 `pd-*` 文件**只多了换行、没有 `description_zh`**，说明该文件写漏了。

### 4.5 **明确需要人类决策的步骤**

| # | 决策点 | 为什么必须由人定 | 本卡给的默认建议 |
|---|---|---|---|
| **H-1** | **`description` 到底留英文还是留中文？** | 产品问题，不是技术问题。数据只支持一个方向：留中文会让「含触发句式」从 **17/23 掉到 13/23**（§2.5 实测），并损失 `TDD`/`RED-GREEN-REFACTOR`/`PR` 等英文技术词的 BM25 命中 | **留英文原文，中文另存 `description_zh`**（= G1-A §7.1 的裁定，本卡复核其证据仍然成立） |
| **H-2** | **`description_zh` 的写作规范与谁执笔？** | 15 条中文译文现在只存在于 `skills_mgmt.json`（**gitignored**）。搬进 skill.md 后**它就是唯一副本**；后续中文的质量、术语一致性、是否做三段式（做什么/何时用/不做什么），是内容治理决策 | 先「**逐字搬运、不改一字**」（M2 的验证断言就是逐字相等），把「改文」留给 G1 正卡 |
| **H-3** | **7 条主轨独有技能（`code-observability` / `engineering-test-delivery` / `frontend-state-sync` / `global-core-principles` / `self-explanatory-ui` / `skill` / `testing-anti-patterns`）是否纳入唯一事实源？** | 它们**没有 skill.md 实体**，因此永远不受唯一源约束，也**永远不进检索与模型上下文**（G1-A R9）。纳入 = 要为它们新建 7 个 skill.md 并清空主轨 description，是一次**能力面变更**，不是描述治理 | **本轮显式声明为「非事实源域」，不动**。若要纳入，单开卡 |
| **H-4** | **`data/skills_descriptions_overlay.json` 与 `_CURATED_DESCRIPTIONS` 是删除还是保留为空？** | 涉及 `POST /api/skills/describe`（可写任意 id）这条运维入口的存废。G1-A 的裁定是「删除」，但它漏了这条路由 | 先按 M0 把 **2 条路由**冻结，**再**删数据（R11 的顺序要求） |
| **H-5** | **`compare_skills_legacy_vs_repo.py` 的 SKIP 语义怎么改？** | 现在是「文件缺失 ⇒ 打印 SKIP ⇒ 视同 ALL_MATCH」，是**假绿**（本卡实测该分支代码在 `:63-70`）。改成 CI 允许 SKIP / 迁移环境必须 FAIL，会改变 CI 的判定 | 采纳 G1-A §9.6 的双口径（CI PASS-SKIP；迁移校验 FAIL），但**需 CI owner 确认** |

---

## 5. 复现脚本原文

> 全部脚本写在**仓库之外**（`%TEMP%\g1b0\`），**未在仓库内新建除本报告外的任何文件**。

### 5.1 脚本 A（**逐字复用 G1-A §11.1，未改一行**）

见 `docs/audit_skill_governance/G1A_reconciliation.md` §11.1 的 `g1a_repro.py` 原文。本卡只把它写到 `%TEMP%\g1b0\g1a_repro.py` 并用 `python g1a_repro.py` 运行（工作目录 = 仓库根）。**脚本内含 `ROOT = r"C:\Users\Administrator\agent"` 硬编码，故位置无关。** 输出见 §1 命令②。

### 5.2 脚本 E（**逐字复用 G1-A §11.5，未改一行**）

见 G1-A §11.5 的 `g1a_capreg.py` 原文。输出见 §1 命令③。

### 5.3 **本卡新增**脚本 G：相似度 / 触发句式 / 末尾换行 / F1b 锚点

```python
# -*- coding: utf-8 -*-
"""G1-B0 复核对账：① 15 组冲突相似度重跑 ② 触发句式 ③ 末尾换行 ④ 静态锚点"""
import os, re, sys, json, hashlib, difflib, yaml
sys.stdout.reconfigure(encoding="utf-8")
ROOT = r"C:\Users\Administrator\agent"
D = os.path.join(ROOT, "data")

def fm_of(sid):
    txt = open(os.path.join(D, "skills_repo", sid, "skill.md"), encoding="utf-8").read()
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", txt, re.S)
    return (yaml.safe_load(m.group(1)) or {}) if m else {}

DIRS = sorted(d for d in os.listdir(os.path.join(D, "skills_repo"))
              if os.path.isfile(os.path.join(D, "skills_repo", d, "skill.md")))
FMS = {s: fm_of(s) for s in DIRS}
MGMT = json.load(open(os.path.join(D, "skills_mgmt.json"), encoding="utf-8"))

print("=== A. 15 组冲突：S1 vs S2 相似度重跑 (difflib.SequenceMatcher.ratio) ===")
rows = []
for s in DIRS:
    a = FMS[s].get("description")
    b = (MGMT.get(s) or {}).get("description")
    if not b:
        continue
    r = difflib.SequenceMatcher(None, a or "", b or "").ratio()
    rows.append((r, s, len(a or ""), len(b or "")))
rows.sort()
print("双侧:", len(rows), " 描述不同:", sum(1 for r,s,la,lb in rows if FMS[s].get("description") != MGMT[s]["description"]))
print(f"{'rank':<5}{'similarity':<12}{'L_S1':<6}{'L_S2':<6}skill_id")
for i, (r, s, la, lb) in enumerate(rows, 1):
    print(f"{i:<5}{r:<12.3f}{la:<6}{lb:<6}{s}")
print("min=%.3f max=%.3f  <0.25 的组数=%d  区间[0.077,0.222]内=%d" % (
    rows[0][0], rows[-1][0], sum(1 for r,_,_,_ in rows if r < 0.25),
    sum(1 for r,_,_,_ in rows if 0.070 <= r <= 0.230)))

print()
print("=== B. 触发句式覆盖（G1-A §11.6 脚本 F 原文） ===")
TRIG = re.compile('适用于|适用场景|使用场景|用于|用来|当[^，。；]{0,14}时|时使用|时调用|Use when|'
                  'Use this|使用本|调用本|触发条件|调用时机|在[^，。；]{0,12}之前|场景')
def fmd(sid):
    txt = open(os.path.join(D,'skills_repo',sid,'skill.md'), encoding='utf-8').read()
    m = re.match(r'^---\s*\n(.*?)\n---\s*\n', txt, re.S)
    return (yaml.safe_load(m.group(1)) or {}).get('description') or ''
ids = [d for d in os.listdir(os.path.join(D,'skills_repo'))
       if os.path.isfile(os.path.join(D,'skills_repo',d,'skill.md'))]
mgmt = json.load(open(os.path.join(D,'skills_mgmt.json'), encoding='utf-8'))
cur  = sum(1 for s in ids if TRIG.search(fmd(s)))
alt  = sum(1 for s in ids if TRIG.search((mgmt.get(s) or {}).get('description') or fmd(s)))
print('现状(全用 skill.md):   %d/%d = %.1f%%' % (cur, len(ids), cur/len(ids)*100))
print('若改用中文译文:        %d/%d = %.1f%%' % (alt, len(ids), alt/len(ids)*100))
only_cur = sorted(s for s in ids if TRIG.search(fmd(s)) and not TRIG.search((mgmt.get(s) or {}).get('description') or fmd(s)))
print('仅 skill.md 命中、替代方案不命中的 id (%d):' % len(only_cur), only_cur)

print()
print("=== C. pd-* 缺末尾换行清单 ===")
nonl = []
for s in DIRS:
    p = os.path.join(D, "skills_repo", s, "skill.md")
    raw = open(p, "rb").read()
    if raw and not raw.endswith(b"\n"):
        nonl.append((s, len(raw)))
print("缺末尾换行的 skill.md 数:", len(nonl), "/", len(DIRS))
for s, n in nonl:
    print("  ", s, n, "bytes, 末 16 字节:", open(os.path.join(D,"skills_repo",s,"skill.md"),"rb").read()[-16:])
print("其中 pd-* 前缀:", sum(1 for s,_ in nonl if s.startswith("pd-")))
print("非 pd-* 的:", [s for s,_ in nonl if not s.startswith("pd-")])

print()
print("=== D. F1b 现状静态锚点 ===")
src = open(os.path.join(ROOT, "agent", "skills_mgmt", "file_store.py"), encoding="utf-8").read()
lines = src.splitlines()
for i, l in enumerate(lines, 1):
    if re.match(r"^_META_FIELDS = \{", l):
        print("_META_FIELDS 起始行:", i)
        j = i
        while j <= len(lines) and lines[j-1].strip() != "}":
            j += 1
        print("_META_FIELDS 结束行:", j)
        print("  含 description_zh?", "description_zh" in "\n".join(lines[i-1:j]))
    if l.startswith("    def update_meta") or l.startswith("    def patch_front_matter") or l.startswith("    def _find_fm_key"):
        print("锚点:", i, l.strip())
print("file_store.py 中出现 description_zh 次数:", src.count("description_zh"))
```

### 5.4 **本卡新增**脚本 H：F1b 影响评估（**全程只在临时副本上做**）

```python
# -*- coding: utf-8 -*-
"""G1-B0 / F1b 影响评估：update_meta 的最小侵入性实测（全部在临时副本上做）"""
import os, re, sys, json, shutil, tempfile, difflib
sys.stdout.reconfigure(encoding="utf-8")
ROOT = r"C:\Users\Administrator\agent"
sys.path.insert(0, ROOT); os.chdir(ROOT)
from agent.skills_mgmt.file_store import SkillFileStore, SkillMDParser

SRC = os.path.join(ROOT, "data", "skills_repo")
DIRS = sorted(d for d in os.listdir(SRC) if os.path.isfile(os.path.join(SRC, d, "skill.md")))

def strip_desc(text):
    """去掉 front matter 里 description 顶层键所占行范围，返回其余文本"""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return text
    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i; break
    if end_idx is None: return text
    fm = lines[1:end_idx]
    span = SkillMDParser._find_fm_key(fm, "description")
    if span is None: return text
    return "".join(lines[:1] + fm[:span[0]] + fm[span[1]:] + lines[end_idx:])

def eol_of(b):
    return "\r\n" if b"\r\n" in b else "\n"

tmp = tempfile.mkdtemp(prefix="g1b0_f1b_")         # ← 系统临时目录，不碰生产 data/
repo = os.path.join(tmp, "skills_repo"); os.makedirs(repo)
for d in DIRS: shutil.copytree(os.path.join(SRC, d), os.path.join(repo, d))
fs = SkillFileStore(repo_path=repo)                 # ← 唯一的写目标

print("临时仓库:", repo, "（生产 data/skills_repo 未被触碰）")
print()
print("=== T1: 改 description 后，除该键所在行外是否逐字节不变 ===")
hdr = f"{'skill_id':<52}{'orig_endNL':<11}{'new_endNL':<10}{'rest_identical':<16}{'deltaB':<8}{'eol':<6}"
print(hdr); print("-"*len(hdr))
fails = []
for d in DIRS:
    p = os.path.join(repo, d, "skill.md")
    orig = open(p, "rb").read(); orig_txt = orig.decode("utf-8"); eol = eol_of(orig)
    fs.update_meta(d, {"description": "G1B0-PROBE-DESC-" + d})
    new = open(p, "rb").read(); new_txt = new.decode("utf-8")
    a, b = strip_desc(orig_txt), strip_desc(new_txt)
    if a == b:
        ok = True; note = "identical"
    elif (not a.endswith(("\n","\r"))) and b == a + eol:
        ok = True; note = "identical(+tailEOL)"
    else:
        ok = False; note = "DIFF!"; fails.append(d)
    print(f"{d:<52}{str(orig.endswith(b'\n')):<11}{str(new.endswith(b'\n')):<10}{note:<16}{len(new)-len(orig):<8}{repr(eol):<6}")
print()
print("不满足「除目标键外逐字节不变」的技能:", fails if fails else "无 —— 23/23 通过")

print()
print("=== T3: 幂等性（同值重复 update_meta） ===")
idem = []
for d in DIRS:
    p = os.path.join(repo, d, "skill.md"); before = open(p,"rb").read()
    cur = SkillMDParser.parse(before.decode("utf-8"))[0].get("description")
    fs.update_meta(d, {"description": cur})
    if open(p,"rb").read() != before: idem.append(d)
print("同值重写后字节变化:", idem if idem else "无（23/23 幂等）")

print()
print("=== T4: description_zh 能否被 update_meta 写入 ===")
p = os.path.join(repo, "pd-writing-skills-5da20e67-skill", "skill.md")
before = open(p,"rb").read()
fs.update_meta("pd-writing-skills-5da20e67-skill", {"description_zh": "中文展示文案探针"})
after = open(p,"rb").read()
print("文件是否变化:", before != after, "| 文件含 description_zh:", b"description_zh" in after)

print()
print("=== T5: 换行补全是「一次性」还是每次追加 ===")
p = os.path.join(repo, "pd-writing-plans-f846e3a2-skill", "skill.md")
fs.update_meta("pd-writing-plans-f846e3a2-skill", {"description": "第二轮-1"})
c1 = open(p,"rb").read()
fs.update_meta("pd-writing-plans-f846e3a2-skill", {"description": "第二轮-2"})
c2 = open(p,"rb").read()
print("第2次后末尾 CRLF 个数:", c2.count(b"\r\n") - c2.rstrip(b"\r\n").count(b"\r\n"), "| 末尾 6 字节:", c2[-6:])
print("第1→2次只差 description 行吗:", strip_desc(c1.decode()) == strip_desc(c2.decode()))
print("第2次后是否出现连续空行结尾:", c2.endswith(b"\r\n\r\n"))

shutil.rmtree(tmp, ignore_errors=True)
print("临时目录已清理；生产仓库未写入")
```

### 5.5 **本卡新增**脚本 I：`description_zh` 前置条件探针（monkeypatch 只在进程内）

```python
# -*- coding: utf-8 -*-
"""G1-B0：description_zh 前置条件探针（临时副本；monkeypatch 仅进程内）"""
import os, sys, shutil, tempfile
sys.stdout.reconfigure(encoding="utf-8")
ROOT = r"C:\Users\Administrator\agent"
sys.path.insert(0, ROOT); os.chdir(ROOT)
from agent.skills_mgmt import file_store as FS
from agent.skills_mgmt.file_store import SkillFileStore, SkillMDParser

SRC = os.path.join(ROOT, "data", "skills_repo")
SID = "pd-writing-skills-5da20e67-skill"
tmp = tempfile.mkdtemp(prefix="g1b0_zh_")
repo = os.path.join(tmp, "skills_repo"); os.makedirs(repo)
shutil.copytree(os.path.join(SRC, SID), os.path.join(repo, SID))
fs = SkillFileStore(repo_path=repo)
p = os.path.join(repo, SID, "skill.md")

# ---- T7：手工植入 description_zh，看 F1b 的最小侵入改写是否原样保留 ----
raw = open(p, encoding="utf-8", newline="").read()
lines = raw.split("\r\n")
i = next(k for k, l in enumerate(lines) if l.startswith("description:"))
lines.insert(i, "description_zh: 中文展示文案探针")
with open(p, "w", encoding="utf-8", newline="") as f:
    f.write("\r\n".join(lines))
before = open(p, "rb").read()
fs.update_meta(SID, {"description": "改写后的英文描述 PROBE"})
after = open(p, "rb").read()
zh_b = [l for l in before.decode().splitlines() if l.startswith("description_zh")]
zh_a = [l for l in after.decode().splitlines() if l.startswith("description_zh")]
print("update_meta 后 description_zh 仍存在:", bool(zh_a), "| 逐字保留:", zh_b == zh_a)

def strip_desc(text):
    ls = text.splitlines(keepends=True)
    e = next((k for k in range(1, len(ls)) if ls[k].strip() == "---"), None)
    if e is None: return text
    fm = ls[1:e]; span = SkillMDParser._find_fm_key(fm, "description")
    if span is None: return text
    return "".join(ls[:1] + fm[:span[0]] + fm[span[1]:] + ls[e:])
a1, a2 = strip_desc(before.decode()), strip_desc(after.decode())
print("除 description 键外逐字节不变（含 description_zh 行）:", a1 == a2 or a2 == a1 + "\r\n")

# ---- T8：把 description_zh 加入 _META_FIELDS（进程内）后的可见性 ----
m0 = SkillMDParser.parse(open(p, encoding="utf-8").read())[0]
print("未打补丁: description_zh 可见 =", "description_zh" in m0)
FS._META_FIELDS.add("description_zh")          # ← 只改内存里的 set，不动仓库文件
m1 = SkillMDParser.parse(open(p, encoding="utf-8").read())[0]
print("打补丁后: description_zh 可见 =", "description_zh" in m1, "| 值:", repr(m1.get("description_zh")))
fs._meta_index = None
idx = fs.load_metadata_index(refresh=True)
print("打补丁后 load_metadata_index 带出 description_zh =", "description_zh" in (idx.get(SID) or {}))
fs.update_meta(SID, {"description_zh": "新的中文展示文案"})
idx2 = fs.load_metadata_index(refresh=True)
print("打补丁后 update_meta 能写入 description_zh =", "description_zh" in open(p, encoding="utf-8").read(),
      "| 索引读到:", repr((idx2.get(SID) or {}).get("description_zh")))

shutil.rmtree(tmp, ignore_errors=True)
```

### 5.6 **本卡新增**脚本 J：修复前 `parse → serialize` 会删除白名单外键的证明

```python
# -*- coding: utf-8 -*-
"""证明：修复前的 parse→serialize 整文件重排会静默删除白名单外的 description_zh"""
import os, sys
sys.stdout.reconfigure(encoding="utf-8")
ROOT = r"C:\Users\Administrator\agent"; sys.path.insert(0, ROOT); os.chdir(ROOT)
from agent.skills_mgmt.file_store import SkillMDParser, _META_FIELDS
print("当前 _META_FIELDS (%d 个):" % len(_META_FIELDS), sorted(_META_FIELDS))
print("含 description_zh:", "description_zh" in _META_FIELDS)
content = ("---\n"
           "id: probe\n"
           "name: probe\n"
           "description: English original\n"
           "description_zh: 中文展示文案\n"
           "unknown_custom_field: keep-me\n"
           "---\n"
           "body line\n")
meta, body = SkillMDParser.parse(content)          # 修复前的第一步
print("parse 后 meta 键:", sorted(meta.keys()))
out = SkillMDParser.serialize(meta, body)          # 修复前的第二步（旧 update_meta 走这条）
print("serialize 后是否仍含 description_zh:", "description_zh" in out)
print("serialize 后是否仍含 unknown_custom_field:", "unknown_custom_field" in out)
print("---- serialize 输出 ----"); print(out)
print("---- patch_front_matter 输出（F1b 后的路径） ----")
print(SkillMDParser.patch_front_matter(content, {"description": "NEW English"}, None))
```

### 5.7 其它一次性命令（无需脚本）

```powershell
# 行号重核（等价于 G1-A §14.4 的 grep -n）
Select-String -Path agent/skills_mgmt/registry.py -Pattern "def as_legacy_rows"
Select-String -Path agent/skills_mgmt/vector_adapter.py -Pattern "def ensure_indexed","def _drop_native_chroma_ids_locked","def _remove_skill_vector","_indexed_content_hash"
Select-String -Path agent/digital_life_persona.py -Pattern "_SKILL_PROMPTS","def _build_skill_instructions"

# 合并规则原文
python -c "import inspect,agent.skills_mgmt.registry as r;print(inspect.getsource(r.SkillRegistry.as_legacy_rows))"

# git 跟踪 / 忽略
git ls-files --error-unmatch data/skills_repo/self_reflection/skill.md data/skills_descriptions_overlay.json
git check-ignore -v data/skills_mgmt.json data/skills.json agent/data/skills.json data/descriptors.json data/skills_repo/.index/cache.json data/skill_vectors/native_chroma/chroma.sqlite3

# 一致性现状（预期 exit=1，15 处 DIFF + 7 条 only_legacy）
python scripts/compare_skills_legacy_vs_repo.py

# F1b / 13 卡的规模
git diff --numstat -- agent/skills_mgmt/file_store.py agent/skills_mgmt/vector_adapter.py

# manifest 缺口的静态复核
python -c "import json;m=json.load(open('data/capability_manifest.json',encoding='utf-8'));sk=[e for e in m['entries'] if e.get('kind')=='skill'];print(len(sk), sum(1 for e in sk if 'description' in e))"
```

---

## 6. 本报告的局限与副作用声明

### 6.1 局限

1. **未启动服务、未跑 pytest**（含未跑全量）。所有结论来自静态读取 + 进程内只读构建（CapabilityRegistry）+ 临时副本上的写入实测。
2. **`SkillSearcher`（R10）与 `vector_adapter` 的真实重编码路径未实测**。M4b/M7 的行为仍属 G1-A 的口径，本卡只复核了**行号**（`_build_vector_text` 现为 `vector_adapter.py:285`、`_vector_text_and_hash` 现为 `:682`），未复核**运行时行为**。G1-A §13 已标为「机制实测、端到端推断」，本卡**未推进**。
3. **审计链 `descriptor.*` 统计（R5）未重跑**，沿用 G1-A 的数字；原因是它与 G1-B 的开工判据无关，且会对 `audit_chain.db` 做全表扫描。
4. **`data/skills_classes.json`、`agent/data/extensions.json` 仍未纳入**（G1-A §13 的局限 5、6 原样保留）。
5. **`description_zh` 的 monkeypatch 验证是「机制级」而非「改动级」**：本卡证明「补 1 行白名单即足够」，但**没有真的改那一行**（只读卡）。真正的端到端验证属于 G1-B 的 S0。
6. **第 ① 条对账命令用 `Select-String` 替代 `grep -n`**（本机无 `grep`）。正则模式与目标文件逐字未改，但**这是方法上的替换，必须在验收时知情**。
7. **`data/skills_mgmt.json` 的 mtime 已晚于 G1-A 的测量时点**。本卡用「15 组文案/长度零漂移」证明该次重写内容等价，但**没有逐字节比对历史版本**（历史上没有留存副本）。

### 6.2 副作用声明

| 项 | 说明 |
|---|---|
| 仓库内**新建**文件 | **仅本报告** `docs/audit_skill_governance/G1B0_recheck.md` |
| 仓库内**修改**的既有文件 | **0 个**。`git status --porcelain` 由开工时的 46 条涨到收工时的 58 条，但**逐条比对后新增的 13 条全部属于其他并发卡**（`agent/workflow_learning/*`、`agent/orchestrator/orchestrator.py`、`.gitignore`、`scripts/audit_reseal_daily_root.py`、`tests/unit/test_daily_root_reseal.py` 等，详见 §0.5.3 的声明），**无一条来自本卡** |
| `data/` 下任何文件 | **未被触碰**。`git status --porcelain -- data/` 为空；`data/` 与 `data/skills_repo/` 下所有 mtime **均早于开工时间**（最新的 `skills_mgmt.json` 为 18:43:31、`cache.json` 为 19:20:49） |
| 仓库外写入 | `%TEMP%\g1b0\` 下 6 个只读探针脚本；以及 `tempfile.mkdtemp()` 建的临时技能仓库（`copytree` 副本，跑完 `shutil.rmtree` 自清） |
| 进程 | 未启动任何服务；只跑了离线 Python 脚本（其中脚本 E 在进程内构建只读 CapabilityRegistry；脚本 H/I 的写入目标**全部是临时副本**） |
| 测试 | 未跑 pytest（含未跑全量） |
| git | **未执行任何写操作**（无 commit / push / checkout / stash / add / 建分支）。`HEAD` 收工仍为 `5c9ace10a4ca4bb96860db3a48debf9ddcf496bf` |
| 密钥 | 全程**未读取、未输出** `.env` 中的任何密钥值；未访问对外网络（未调用任何 LLM API） |

---

## 7. 给 G1-B 的一句话交接

> **数据面照旧、行号面只信 `vector_adapter.py` 的新行号；开工第一件事是把 `description_zh` 加进 `file_store.py:75-82` 的白名单（否则 M2 静默失败）；第二件事是把 M0 的写路径从 1 条扩到 3 条；然后按 `S0 → M0 → M1 → M2 → M4 → M3 → M5 → M6 → M8 → M7 → M9` 推进，并提前在 PR 描述里声明「15 个 `pd-*` 文件各补 1 个末尾 CRLF」这个一次性 diff。**
