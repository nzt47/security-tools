# Q1 —— 114 项能力的确认分级（L2/L3）与 owner 存在性审计

> 审计对象：`C:\Users\Administrator\agent\`（云枢，Windows 单机）
> 审计方式：**只读**。所有数字来自本次实际读取/统计，脚本与原始输出见 §4。
> 清单基线：`data/capability_manifest.json`，`generated_at = 2026-09-20T21:04:35`，`schema_version = 2`
> 审计时间：2026-09-25

---

## 0. 核心结论（先读这段）

| # | 结论 | 证据 |
|---|---|---|
| 1 | 114 项 = **91 tool + 23 skill**，与锚点一致 | `data/capability_manifest.json` → `entries` 实测，见 §4.1 |
| 2 | **L2/L3 只有 20 项，全部是 tool，技能一项都没有** | L2=10、L3=10；技能 23 条的 `confirm_level` 字段**根本不存在** |
| 3 | 任务书说的"每条约 31 个字段"**不准确**：实测每条 **57 个字段**，键并集 62 | §4.2 |
| 4 | **owner 存在率 114/114 = 100%，但 0 条可用作"责任人"**：114 条全是 `builtin`（归属枚举：谁把它装进来的），仓库里**没有**任何"人/团队负责人"字段 | `agent/lines/models.py:85 OWNERS`、§2、§4.1 |
| 5 | 确认级**全部是派生的**，YAML 零声明：`confirm_level_declared` 非空 = **0/91**，`confirm_level_overridden` = **0/91** | §4.1 |
| 6 | **存在 3 条可绕过确认门的实测路径**（1 条正在生效、1 条是 fail-open 代码缺陷、1 条是整条链路根本不过闸门） | §5 |
| 7 | 「30 天零调用」**取不到**：唯一持久台账 `agent/data/tool_trace.db` 只覆盖 **3/91** 个真实工具，且全窗口只有 **12.8 天** | §4.6、§6 |
| 8 | 一处**注释与实测相反**：`agent/server_auth.py:151-153` 称"本部署未配置任何令牌"，实测 `.env:421` 已设 `FLASK_API_TOKEN` | §7 第 7 行 |

---

## 1. L0–L3 的精确定义（逐字引用）

### 1.1 四级值域与语义 —— `agent/lines/models.py:52-65`

```python
# agent/lines/models.py:57
CONFIRM_LEVELS = ("L0", "L1", "L2", "L3")

# agent/lines/models.py:60-65
CONFIRM_LEVEL_SEMANTICS = {
    "L0": "免确认（只读且低风险）",
    "L1": "摘要确认（展示将执行什么 + 影响范围，**可批量确认**）",
    "L2": "逐次确认（每次都要人点，且单次有效）",
    "L3": "默认禁止（仅**显式预授权** —— SA + scope —— 才能执行）",
}
```

配套的上游（`agent/lines/models.py:53-56` 逐字）：

> 【为什么新建而不是复用技能域的 L0/L1/L2】`agent/skills_mgmt/approval.py:71` 的
> `APPROVAL_LEVELS=("L0","L1","L2")` 是**技能/策略审批**域的口径（L2=manual_required），
> 与"一次工具调用要不要人点确认"不是同一件事（值域都少一级）。两域各自独立，不互相 import

**实测确认**：`agent/skills_mgmt/approval.py:71` 确为 `APPROVAL_LEVELS = ("L0", "L1", "L2")`（**3 级**）。
⇒ **仓库里存在两套并行的"确认级"概念**，值域不同、互不 import、互不换算。审计时**不能**把技能侧的 L0/L1/L2 与工具侧的 L0–L3 混用。

### 1.2 派生规则表 —— `agent/lines/models.py:127-177`

```python
# agent/lines/models.py:127
def derive_confirm_level(plane: str, effect: str, risk: str) -> str:
    ...
# agent/lines/models.py:168-177   ← 实际判定体
    p = str(plane or "").strip().lower()
    e = str(effect or "").strip().lower()
    r = str(risk or "").strip().lower()
    if r == "critical" or e == "extend" or p == "govern":
        return "L3"
    if r == "high":
        return "L2"
    if e in ("write", "execute") or r == "medium":
        return "L1"
    return "L0"
```

docstring（`agent/lines/models.py:130-137`）的规则表：

| 条件 | 级别 | 语义 |
|---|---|---|
| `risk: critical` \| `effect: extend` \| `plane: govern` | **L3** | 默认禁止（须显式预授权） |
| `risk: high` | **L2** | 逐次确认 |
| `effect: write` \| `effect: execute` \| `risk: medium` | **L1** | 摘要确认（可批量） |
| 其余（`effect: read` 且 `risk: low`） | **L0** | 免确认 |

**顺序敏感**：`models.py:139-144` 自述——先判 L3 再判 L2，否则 `shell_execute`(critical+act) 与 `generate_tool`(critical+govern) 会错误落进 L2。

### 1.3 判定输入 = 哪三个字段

| 输入 | 来源 | 值域（`agent/lines/models.py`） |
|---|---|---|
| `plane` | `data/tool_definitions/<name>.yaml` 的 `plane` | `:41 PLANES = ("resident","perceive","act","govern")` |
| `effect` | 同上 `effect` | `:42 EFFECTS = ("read","write","execute","extend")` |
| `risk` | 同上 `risk` | `:43 RISKS = ("low","medium","high","critical")` |

**不是人工填的字段**（`models.py:128`「唯一派生口径；不是人工填的字段」）。YAML 可写 `confirm_level` 覆盖，但：
- 降级（比派生值更宽）**必须**给 `confirm_level_reason`，否则被丢弃（`models.py:460-489 _resolve_confirm_level`）；
- **实测 91/91 个 YAML 一个都没写**（`confirm_level_declared` 非空 = 0）。

### 1.4 两个配套概念（不要混淆）

| 概念 | 定义位置 | 判据 | 与 L0–L3 的关系 |
|---|---|---|---|
| `needs_approval` | `agent/lines/models.py:180-193 needs_approval_for` | `plane==govern ∨ effect==extend ∨ risk ∈ {"critical","high"}`（`:124 _APPROVAL_FROM_RISK`） | **等价于 `confirm_level ∈ {L2,L3}`**（实测 20 = 20） |
| `permission_level` | 派生 `public/internal/restricted` | —— | `L0 ⟺ public`（`models.py:158` 有一致性证明） |

### 1.5 运行时消费点 —— `agent/tool_gate.py:1210-1349 _confirm_level_outcome`

分支顺序（`tool_gate.py:1216-1230` docstring）：
1. 未登记 / 空 / **L0** ⇒ 放行；
2. 总开关 `CP_TOOL_GATE_APPROVAL_ENFORCE=0` ⇒ 告警后放行（`:1291-1297`）；
3. 分级开关 `CP_TOOL_CONFIRM_LEVEL_ENFORCE=0` ⇒ 告警后放行（`:1299-1305`）；
4. 影子模式 `CP_TOOL_CONFIRM_LEVEL_SHADOW=1` ⇒ 只告警不拦（`:1327-1335`）；
5. 身份不可知 + 非交互 ⇒ **拒绝**（`:1379-1388`）；
6. **SA 预授权命中 ⇒ 放行**（`:1391-1399`，`decision=preauthorized`）；
7. 非交互且无预授权 ⇒ 拒绝、不挂单（`:1402-1413`）；
8. 交互路径 ⇒ L1 可复用批准，L2/L3 单次有效（`:1347-1349`）。

---

## 2. Owner 字段的真实语义（回答"每项的 owner 是否存在"）

`agent/lines/models.py:80-85` 逐字：

```python
#: 能力归属：谁把它装进来的（仓库里此前**没有**这个概念）
#:   builtin           随仓库发布（data/tool_definitions/*.yaml + 内置注册点）
#:   local-installed   本机安装的扩展/MCP 服务（data/extensions、mcp 连接）
#:   tenant-installed  租户安装（预留；当前恒为空 —— 单机单用户）
#:   marketplace       来自扩展市场（SOURCE_MARKET）
OWNERS = ("builtin", "local-installed", "tenant-installed", "marketplace")
```

**实测**：

| 指标 | 值 |
|---|---|
| `owner` 字段存在（有该键） | 114 / 114 |
| `owner == "builtin"` | **114 / 114**（100%） |
| owner 为空 / 缺失 / 占位 | **0** |
| owner ≠ builtin | **0** |
| **存在"责任人"语义字段**（owner_contact / maintainer / 负责人） | **不存在**（62 个键的并集中没有任何一项） |

> **结论（直接回答提问）**：`owner` **字段存在且 100% 非空**，但它回答的是"**代码从哪来的**"（provenance），**不是**"**谁负责这个能力**"。
> ⇒ 从治理视角看，**114 项能力的责任人是 0 个**。审计问题"每项的 owner 是否存在"若指"可问责的人"，答案是**全部不存在**；若指字段，答案是**全部存在，但 114 条取值完全相同、不具备任何区分度**。

`data/capability_manifest.json` 的 `spec_required_fields` 把 `owner` 列为必填：
`["capability_id","kind","location","owner","version","tenant_id","namespace","registry_source","location_source","entity_versioned"]`
—— 所以它**不可能**缺失；用它做"孤儿能力"判据是**无效判据**。

---

## 3. L2 / L3 明细（20 条，全部为 tool）

### 3.1 L3 —— 10 条（默认禁止，须显式预授权）

| # | id | plane | effect | risk | owner | permission | trigger | main_line | 声明文件 |
|---|---|---|---|---|---|---|---|---|---|
| 1 | `connect_mcp` | govern | extend | high | builtin | restricted | model | denied_by_effect | data/tool_definitions/connect_mcp.yaml |
| 2 | `disconnect_mcp` | govern | extend | medium | builtin | restricted | model | denied_by_effect | data/tool_definitions/disconnect_mcp.yaml |
| 3 | `ext_configure` | govern | extend | medium | builtin | restricted | model | denied_by_effect | data/tool_definitions/ext_configure.yaml |
| 4 | `ext_install` | govern | extend | high | builtin | restricted | model | denied_by_effect | data/tool_definitions/ext_install.yaml |
| 5 | `ext_toggle` | govern | extend | medium | builtin | restricted | model | denied_by_effect | data/tool_definitions/ext_toggle.yaml |
| 6 | `ext_uninstall` | govern | extend | high | builtin | restricted | model | denied_by_effect | data/tool_definitions/ext_uninstall.yaml |
| 7 | `generate_tool` | govern | extend | critical | builtin | restricted | model | denied_by_effect | data/tool_definitions/generate_tool.yaml |
| 8 | `run_sandbox` | act | execute | critical | builtin | restricted | model | **visible** | data/tool_definitions/run_sandbox.yaml |
| 9 | `scan_mcp` | govern | extend | medium | builtin | restricted | model | denied_by_effect | data/tool_definitions/scan_mcp.yaml |
| 10 | `shell_execute` | act | execute | critical | builtin | restricted | model | **visible** | data/tool_definitions/shell_execute.yaml |

### 3.2 L2 —— 10 条（逐次确认，单次有效）

| # | id | plane | effect | risk | owner | permission | trigger | main_line | 声明文件 |
|---|---|---|---|---|---|---|---|---|---|
| 1 | `apply_patch` | act | write | high | builtin | restricted | model | visible | data/tool_definitions/apply_patch.yaml |
| 2 | `decompress` | act | write | high | builtin | restricted | model | visible | data/tool_definitions/decompress.yaml |
| 3 | `edit` | act | write | high | builtin | restricted | model | visible | data/tool_definitions/edit.yaml |
| 4 | `ext_send_channel` | act | execute | high | builtin | restricted | model | not_assembled | data/tool_definitions/ext_send_channel.yaml |
| 5 | `fan_out` | act | execute | high | builtin | restricted | model | visible | data/tool_definitions/fan_out.yaml |
| 6 | `git` | act | execute | high | builtin | restricted | model | visible | data/tool_definitions/git.yaml |
| 7 | `run_program` | act | execute | high | builtin | restricted | model | not_assembled | data/tool_definitions/run_program.yaml |
| 8 | `schedule_task` | act | execute | high | builtin | restricted | **system** | not_assembled | data/tool_definitions/schedule_task.yaml |
| 9 | `workspace_delete` | act | write | high | builtin | restricted | model | not_assembled | data/tool_definitions/workspace_delete.yaml |
| 10 | `write_file` | act | write | high | builtin | restricted | model | visible | data/tool_definitions/write_file.yaml |

### 3.3 交叉校验：与 `models.py` 注释里的声称值对拍

| 注释声称（`agent/lines/models.py`） | 实测 | 是否一致 |
|---|---|---|
| `:116-117` "`risk: high` 有 **13 个**" | 13 | 一致 |
| `:120` "只有 13 个 high 进 L2" | L2 = 10（13 个 high 中 3 个被抬到 L3） | 措辞不精确，但 `:142-144` 已自述此点 |
| `:142-144` "13 个 high 里有 3 个（connect_mcp / ext_install / ext_uninstall）同时是 govern ⇒ L3" | 完全一致 | 一致 |
| `:148-149` "`effect: execute` + `risk: low` 实测 **2 个工具**（notify / run_lint）" | notify=execute/low、run_lint=execute/low | 一致 |
| `:289-290` "YAML 里 `confirm_level` 一个都没写（实测 91/91 均无）" | `confirm_level_declared` 非空 = **0/91** | 一致 |
| `:158` "`confirm_level==L0` ⟺ `permission_level==public`" | L0(34) + skill(23) = public(57)；internal(37)=L1(37)；restricted(20)=L2+L3(20) | 一致 |
| `agent/settings/registry.py:1678` "confirm_level >= L2 的工具（实测 20 个）" | **20** | 一致 |

---

## 4. 实测统计：脚本与原始输出

### 4.0 脚本 A —— 清单确认级统计（`q1_audit.py`）

```python
# -*- coding: utf-8 -*-
import json, collections
P = r"C:\Users\Administrator\agent\data\capability_manifest.json"
d = json.load(open(P, encoding="utf-8"))
entries = d["entries"]
tools = [e for e in entries if e.get("kind") == "tool"]
skills = [e for e in entries if e.get("kind") == "skill"]
print("== 0. 总数 ==")
print("entries=%d tool=%d skill=%d" % (len(entries), len(tools), len(skills)))
print("manifest.counts.total=%s by_kind=%s" % (d["counts"]["total"], d["counts"]["by_kind"]))
print("generated_at=%s schema_version=%s" % (d["generated_at"], d["schema_version"]))
print()
print("== 1. confirm_level 字段存在率 ==")
print("含 confirm_level 键: %d / %d" % (sum(1 for e in entries if "confirm_level" in e), len(entries)))
print("  tool: %d / %d" % (sum(1 for e in tools if "confirm_level" in e), len(tools)))
print("  skill: %d / %d" % (sum(1 for e in skills if "confirm_level" in e), len(skills)))
print()
print("== 2. 确认级分布 ==")
def dist(lst):
    c = collections.Counter()
    for e in lst:
        if "confirm_level" not in e: c["(字段缺失)"] += 1
        else:
            v = (e.get("confirm_level") or "").strip().upper()
            c[v if v else "(空串)"] += 1
    return c
dt, ds = dist(tools), dist(skills)
print("%-12s %6s %6s %6s" % ("级别","tool","skill","合计"))
for k in ["L0","L1","L2","L3","(空串)","(字段缺失)"]:
    a,b = dt.get(k,0), ds.get(k,0)
    if a or b: print("%-12s %6d %6d %6d" % (k,a,b,a+b))
print()
print("== 3. L2 / L3 明细 ==")
for want in ("L3","L2"):
    rows=[e for e in entries if (e.get("confirm_level") or "").strip().upper()==want]
    print("--- %s (%d 条) ---" % (want,len(rows)))
    print("%-3s %-22s %-6s %-8s %-9s %-9s %-9s %-10s %-16s %s" % ("#","id","kind","plane","effect","risk","owner","permission","trigger","declared_in"))
    for i,e in enumerate(sorted(rows,key=lambda x:(x["kind"],x["tool_name"])),1):
        print("%-3d %-22s %-6s %-8s %-9s %-9s %-9s %-10s %-16s %s" % (i,e["tool_name"],e["kind"],e.get("plane"),e.get("effect"),e.get("risk"),e.get("owner"),e.get("permission_level"),e.get("trigger"),e.get("declared_in")))
print()
print("== 4. owner ==")
oc=collections.Counter((e.get("owner") or "(空/缺失)") for e in entries)
print("owner 分布:", dict(oc))
print("owner 空/缺失/占位: %d" % sum(1 for e in entries if (e.get("owner") or "").strip() in ("","unknown","default","n/a","TODO","tbd","-")))
print("owner != builtin: %d" % sum(1 for e in entries if (e.get("owner") or "")!="builtin"))
print()
print("== 5. effect/risk/plane 分布 ==")
for f in ("effect","risk","plane"):
    print("-- %s --" % f)
    ct=collections.Counter((e.get(f) or "(空)") for e in tools); cs=collections.Counter((e.get(f) or "(空)") for e in skills)
    for k in sorted(set(ct)|set(cs)): print("   %-10s tool=%-4d skill=%-4d" % (k,ct.get(k,0),cs.get(k,0)))
print()
print("== 6. needs_approval / permission_level / trigger ==")
print("needs_approval True: total=%d tool=%d skill=%d" % (sum(1 for e in entries if e.get("needs_approval")),sum(1 for e in tools if e.get("needs_approval")),sum(1 for e in skills if e.get("needs_approval"))))
print("permission_level:", dict(collections.Counter(e.get("permission_level") for e in entries)))
print("trigger:", dict(collections.Counter(e.get("trigger") for e in entries)))
print("llm_callable=False 的 tool:", [e["tool_name"] for e in tools if not e.get("llm_callable")])
print("callable_mode=manual 条数:", sum(1 for e in entries if e.get("callable_mode")=="manual"))
print()
print("== 7. risk high/critical 全量 ==")
hc=[e for e in entries if (e.get("risk") or "") in ("high","critical")]
print("共 %d 条；tool=%d skill=%d" % (len(hc),sum(1 for e in hc if e["kind"]=="tool"),sum(1 for e in hc if e["kind"]=="skill")))
for e in sorted(hc,key=lambda x:(x["kind"],x["tool_name"])):
    print("  %-6s %-18s risk=%-9s confirm=%-6s effect=%-8s plane=%-8s" % (e["kind"],e["tool_name"],e.get("risk"),e.get("confirm_level","(无字段)"),e.get("effect"),e.get("plane")))
print()
print("== 8. effect=extend 或 plane=govern ==")
for e in sorted([e for e in entries if e.get("effect")=="extend" or e.get("plane")=="govern"],key=lambda x:x["tool_name"]):
    print("  %-6s %-18s effect=%-8s plane=%-8s risk=%-9s confirm=%s" % (e["kind"],e["tool_name"],e.get("effect"),e.get("plane"),e.get("risk"),e.get("confirm_level","(无字段)")))
print()
print("== 9. 一致性 ==")
bad=[e for e in entries if "confirm_level" in e and (e.get("confirm_level") or "")!=(e.get("confirm_level_derived") or "")]
print("confirm_level != confirm_level_derived: %d" % len(bad))
for e in bad: print("  %-18s level=%s derived=%s overridden=%s" % (e["tool_name"],e.get("confirm_level"),e.get("confirm_level_derived"),e.get("confirm_level_overridden")))
print("confirm_level_overridden=True: %d" % sum(1 for e in entries if e.get("confirm_level_overridden")))
print("confirm_level_declared 非空: %d" % sum(1 for e in entries if (e.get("confirm_level_declared") or "").strip()))
```

### 4.1 脚本 A 原始输出（逐字）

```text
== 0. 总数 ==
entries=114 tool=91 skill=23
manifest.counts.total=114 by_kind={'tool': 91, 'skill': 23}
generated_at=2026-09-20T21:04:35 schema_version=2

== 1. confirm_level 字段存在率 ==
含 confirm_level 键: 91 / 114
  tool: 91 / 91
  skill: 0 / 23

== 2. 确认级分布 ==
级别             tool  skill     合计
L0               34      0     34
L1               37      0     37
L2               10      0     10
L3               10      0     10
(字段缺失)            0     23     23

== 3. L2 / L3 明细 ==
--- L3 (10 条) ---
#   id                     kind   plane    effect    risk      owner     permission trigger          declared_in
1   connect_mcp            tool   govern   extend    high      builtin   restricted model            data/tool_definitions/connect_mcp.yaml
2   disconnect_mcp         tool   govern   extend    medium    builtin   restricted model            data/tool_definitions/disconnect_mcp.yaml
3   ext_configure          tool   govern   extend    medium    builtin   restricted model            data/tool_definitions/ext_configure.yaml
4   ext_install            tool   govern   extend    high      builtin   restricted model            data/tool_definitions/ext_install.yaml
5   ext_toggle             tool   govern   extend    medium    builtin   restricted model            data/tool_definitions/ext_toggle.yaml
6   ext_uninstall          tool   govern   extend    high      builtin   restricted model            data/tool_definitions/ext_uninstall.yaml
7   generate_tool          tool   govern   extend    critical  builtin   restricted model            data/tool_definitions/generate_tool.yaml
8   run_sandbox            tool   act      execute   critical  builtin   restricted model            data/tool_definitions/run_sandbox.yaml
9   scan_mcp               tool   govern   extend    medium    builtin   restricted model            data/tool_definitions/scan_mcp.yaml
10  shell_execute          tool   act      execute   critical  builtin   restricted model            data/tool_definitions/shell_execute.yaml
--- L2 (10 条) ---
#   id                     kind   plane    effect    risk      owner     permission trigger          declared_in
1   apply_patch            tool   act      write     high      builtin   restricted model            data/tool_definitions/apply_patch.yaml
2   decompress             tool   act      write     high      builtin   restricted model            data/tool_definitions/decompress.yaml
3   edit                   tool   act      write     high      builtin   restricted model            data/tool_definitions/edit.yaml
4   ext_send_channel       tool   act      execute   high      builtin   restricted model            data/tool_definitions/ext_send_channel.yaml
5   fan_out                tool   act      execute   high      builtin   restricted model            data/tool_definitions/fan_out.yaml
6   git                    tool   act      execute   high      builtin   restricted model            data/tool_definitions/git.yaml
7   run_program            tool   act      execute   high      builtin   restricted model            data/tool_definitions/run_program.yaml
8   schedule_task          tool   act      execute   high      builtin   restricted system           data/tool_definitions/schedule_task.yaml
9   workspace_delete       tool   act      write     high      builtin   restricted model            data/tool_definitions/workspace_delete.yaml
10  write_file             tool   act      write     high      builtin   restricted model            data/tool_definitions/write_file.yaml

== 4. owner ==
owner 分布: {'builtin': 114}
owner 空/缺失/占位: 0
owner != builtin: 0

== 5. effect/risk/plane 分布 ==
-- effect --
   execute    tool=17   skill=1
   extend     tool=8    skill=0
   read       tool=40   skill=22
   write      tool=26   skill=0
-- risk --
   critical   tool=3    skill=0
   high       tool=13   skill=0
   low        tool=50   skill=23
   medium     tool=25   skill=0
-- plane --
   act        tool=41   skill=0
   govern     tool=8    skill=0
   perceive   tool=35   skill=0
   resident   tool=7    skill=23

== 6. needs_approval / permission_level / trigger ==
needs_approval True: total=20 tool=20 skill=0
permission_level: {'restricted': 20, 'internal': 37, 'public': 57}
trigger: {'model': 89, 'none': 1, 'system': 24}
llm_callable=False 的 tool: ['process_distill_run', 'schedule_task']
callable_mode=manual 条数: 24

== 7. risk high/critical 全量 ==
共 16 条；tool=16 skill=0
  tool   apply_patch        risk=high      confirm=L2     effect=write    plane=act
  tool   connect_mcp        risk=high      confirm=L3     effect=extend   plane=govern
  tool   decompress         risk=high      confirm=L2     effect=write    plane=act
  tool   edit               risk=high      confirm=L2     effect=write    plane=act
  tool   ext_install        risk=high      confirm=L3     effect=extend   plane=govern
  tool   ext_send_channel   risk=high      confirm=L2     effect=execute  plane=act
  tool   ext_uninstall      risk=high      confirm=L3     effect=extend   plane=govern
  tool   fan_out            risk=high      confirm=L2     effect=execute  plane=act
  tool   generate_tool      risk=critical  confirm=L3     effect=extend   plane=govern
  tool   git                risk=high      confirm=L2     effect=execute  plane=act
  tool   run_program        risk=high      confirm=L2     effect=execute  plane=act
  tool   run_sandbox        risk=critical  confirm=L3     effect=execute  plane=act
  tool   schedule_task      risk=high      confirm=L2     effect=execute  plane=act
  tool   shell_execute      risk=critical  confirm=L3     effect=execute  plane=act
  tool   workspace_delete   risk=high      confirm=L2     effect=write    plane=act
  tool   write_file         risk=high      confirm=L2     effect=write    plane=act

== 8. effect=extend 或 plane=govern ==
  tool   connect_mcp        effect=extend   plane=govern   risk=high      confirm=L3
  tool   disconnect_mcp     effect=extend   plane=govern   risk=medium    confirm=L3
  tool   ext_configure      effect=extend   plane=govern   risk=medium    confirm=L3
  tool   ext_install        effect=extend   plane=govern   risk=high      confirm=L3
  tool   ext_toggle         effect=extend   plane=govern   risk=medium    confirm=L3
  tool   ext_uninstall      effect=extend   plane=govern   risk=high      confirm=L3
  tool   generate_tool      effect=extend   plane=govern   risk=critical  confirm=L3
  tool   scan_mcp           effect=extend   plane=govern   risk=medium    confirm=L3

== 9. 一致性 ==
confirm_level != confirm_level_derived: 0
confirm_level_overridden=True: 0
confirm_level_declared 非空: 0
```

### 4.2 字段数实测（修正任务书的"约 31 个字段"）

```text
每条目字段数分布: {57: 114}        ← 114 条**全部**是 57 个字段
entries 键并集大小: 62
其中 confirm_level* 五连字段只出现在 91 个 tool 上：
  confirm_level / confirm_level_derived / confirm_level_declared /
  confirm_level_overridden / confirm_level_reason   → 各 91/114
技能专属字段（各 23/114）：skill_in_repo / skill_in_catalog / skill_in_mgmt /
  has_scripts / skill_status
```

### 4.3 技能 23 项的确认级 —— **字段根本不存在**

```text
含 confirm_level 键: tool 91/91, skill 0/23
```

技能专项字段实测（`effect / risk / has_scripts`）：

| 技能 | effect | risk | has_scripts | llm_callable | callable_mode |
|---|---|---|---|---|---|
| `scripted-selftest` | **execute** | low | **True** | False | manual |
| 其余 22 项（context_aware / emotion_expression / memory_summary / 18 个 `pd-*-skill` / proactive_suggestion / safety_guard / self_reflection / voice_interaction） | read | low | False | False | manual |

⇒ 23 个技能**全部**满足 `llm_callable=False ∧ callable_mode=manual`，manifest 给的 `reason` 是「纯提示词技能：由 ContextInjector 按意图注入，模型不发起调用」。
⇒ **但 `scripted-selftest` 是 `effect=execute` + 带脚本**，它既不携带 `confirm_level`（技能域根本没这个字段），执行路径也确实**不过 tool_gate**（见 §5.3）。

### 4.4 脚本 B —— 清单 × 运行时台账交叉核对（`q1_final.py`）

```python
# -*- coding: utf-8 -*-
"""Q1 终稿分析：清单 × 确认级 × owner × 运行时台账（trace/audit）交叉。"""
import json, sqlite3, collections, datetime, os

MAN='C:/Users/Administrator/agent/data/capability_manifest.json'
TRACE='C:/Users/Administrator/agent/agent/data/tool_trace.db'
AUDIT='C:/Users/Administrator/agent/data/audit/audit_chain.db'
d=json.load(open(MAN,encoding='utf-8')); E=d['entries']
by={e['tool_name']:e for e in E}
def lvl(e): return (e.get('confirm_level') or '').strip().upper() or None

print('== A. delegate / 被豁免工具的级别 ==')
for n in ('delegate','fan_out','git','run_program','workspace_delete','write_file'):
    e=by.get(n)
    print('   %-18s level=%s risk=%s effect=%s plane=%s perm=%s'%(n, lvl(e), e.get('risk'), e.get('effect'), e.get('plane'), e.get('permission_level')))

print(); print('== B. 豁免名单生效值 ==')
ov=json.load(open('C:/Users/Administrator/agent/data/ui_settings.json',encoding='utf-8'))['overrides']
for k,v in ov.items():
    if 'CONFIRM' in k or 'APPROVAL' in k or 'GATE' in k:
        print('   %s = %r  (actor=%s, at=%s, risk=%s)'%(k, v.get('value'), v.get('actor'), v.get('updated_at'), v.get('risk')))
print('   overrides 总条数:', len(ov))

print(); print('== C. 运行时确认台账（audit_chain）按工具聚合 ==')
con=sqlite3.connect('file:%s?mode=ro'%AUDIT, uri=True); cur=con.cursor()
agg=collections.defaultdict(collections.Counter); last={}
for subj,dec,ts,n in cur.execute("select subject, json_extract(payload,'$.decision'), min(ts), count(*) from audit_chain where action='tool.confirm_decision' group by 1,2"):
    agg[subj][dec]+=n; last[subj]=max(last.get(subj,''), ts or '')
print('   %-24s %-6s %-6s %s'%('tool','level','count','decisions'))
for subj in sorted(agg, key=lambda s:-sum(agg[s].values())):
    e=by.get(subj)
    print('   %-24s %-6s %-6d %s'%(subj, (lvl(e) if e else '(非能力/测试)'), sum(agg[subj].values()), dict(agg[subj])))

print(); print('== D. 持久化调用台账覆盖度（agent/data/tool_trace.db）==')
con=sqlite3.connect('file:%s?mode=ro'%TRACE, uri=True); cur=con.cursor()
tt=list(cur.execute('select tool_name, count(*) from tool_traces group by 1'))
real=[(n,c) for n,c in tt if n in by]; fake=[(n,c) for n,c in tt if n not in by]
print('   tool_traces 行数=%d  distinct tool=%d'%(sum(c for _,c in tt), len(tt)))
print('   其中【清单内真实工具】%d 个: %s'%(len(real), sorted(real, key=lambda x:-x[1])))
print('   其中【不存在于 114 项清单的名字】（测试夹具）%d 个: %s'%(len(fake), sorted(fake, key=lambda x:-x[1])))

print(); print('== E. 「30天零调用 ∧ 无 owner ∧ 非L2/L3」候选集 ==')
called={n for n,_ in real}
l23={n for n,e in by.items() if lvl(e) in ('L2','L3')}
noowner={n for n,e in by.items() if (e.get('owner') or '').strip() in ('','unknown','n/a','TODO')}
cand=[e for e in E if e['tool_name'] not in called and e['tool_name'] not in l23 and e['kind']=='tool']
print('   清单工具总数 91；L2/L3=%d；有调用记录=%d；owner 为空=%d'%(len(l23),len(called),len(noowner)))
print('   候选（零记录 ∧ 非L2/L3）= %d 个'%len(cand))
```

### 4.5 脚本 B 原始输出（关键段）

```text
== A. delegate / 被豁免工具的级别 ==
   delegate           level=L1 risk=medium effect=execute plane=act perm=internal
   fan_out            level=L2 risk=high   effect=execute plane=act perm=restricted
   git                level=L2 risk=high   effect=execute plane=act perm=restricted
   run_program        level=L2 risk=high   effect=execute plane=act perm=restricted
   workspace_delete   level=L2 risk=high   effect=write   plane=act perm=restricted
   write_file         level=L2 risk=high   effect=write   plane=act perm=restricted

== B. 豁免名单生效值 ==
   CP_TOOL_CONFIRM_LEVEL_EXEMPT = 'fan_out,delegate'  (actor=tok_ffd912584295, at=2026-09-22T22:55:42+0800, risk=A)
   overrides 总条数: 2        ← 另一条是 AUDIT_TRACE_EVENTS=True

== C. 运行时确认台账（audit_chain）按工具聚合 ==
   tool                     level  count  decisions
   write_file               L2     208    {'approved': 11, 'denied_no_identity': 88, 'denied_non_interactive': 67, 'exempted': 2, 'preauthorized': 14, 'request_failed': 13, 'shadow_alert': 13}
   probe_approval_e2e_tool  (非能力/测试) 35   {'approved': 21, 'rejected': 14}
   shell_execute            L3     31     {'approved': 1, 'preauthorized': 30}
   __selftest_tenant_isolation__ (非能力/测试) 12  {'approved': 4, 'preauthorized': 8}
   delegate                 L1     10     {'approved': 2, 'exempted': 6, 'shadow_alert': 2}
   __selftest_confirm_level__ (非能力/测试) 8  {'denied_no_identity': 4, 'preauthorized': 4}
   ext_install              L3     7      {'approved': 7}
   fan_out                  L2     4      {'exempted': 4}
   __sample__               (非能力/测试) 3  {'approved': 1, 'denied_no_identity': 1, 'preauthorized': 1}
   git                      L2     2      {'exempted': 2}
   run_program              L2     1      {'exempted': 1}
   run_sandbox              L3     1      {'approved': 1}
   workspace_delete         L2     1      {'exempted': 1}

== D. 持久化调用台账覆盖度（agent/data/tool_trace.db）==
   tool_traces 行数=1072  distinct tool=15
   其中【清单内真实工具】3 个: [('shell_execute', 27), ('read_file', 6), ('list_directory', 1)]
   其中【不存在于 114 项清单的名字】（测试夹具）12 个:
        [('op', 212), ('t0', 92), ('t1', 92), ('t2', 92), ('t3', 92), ('t4', 92),
         ('t5', 92), ('t6', 92), ('t7', 92), ('search', 47), ('calc', 24), ('get_current_time', 19)]
   91 个工具中**有调用记录**的: 3 / 91
   91 个工具中**零记录**的: 88 / 91
   时间戳范围: 2026-09-08T18:20:22 ~ 2026-09-21T13:02:11   （跨度 12.8 天）
   max(timestamp) 距审计日(2026-09-25)：3.5 天
   unified_traces 行数=3716  distinct capability_id=4:
        [('', 2802), ('cp.builtin.read_file', 315), ('cp.builtin.shell_execute', 315), ('cp.builtin.write_file', 284)]
   时间戳范围: 2026-09-10T04:55:06 ~ 2026-09-24T16:31:34

== E. 候选集 ==
   清单工具总数 91；L2/L3=20；有调用记录=3；owner 为空=0
   候选（零记录 ∧ 非L2/L3）= 69 个
```

### 4.6 调用计数数据在哪、是否可得

| 数据源 | 路径 | 行数 | 覆盖 | 可得性 |
|---|---|---|---|---|
| 工具调用台账（权威写路径） | `agent/data/tool_trace.db` → `tool_traces` | 1072 | **3 / 91** 个真实工具；另 12 个名字是测试夹具 | 可读，**不可用**（覆盖 3.3%，窗口 12.8 天） |
| 统一 Trace | 同库 `unified_traces` | 3716 | 4 个 `capability_id`（含 2802 条空值） | 可读，覆盖 **3 / 91** |
| 确认决策审计 | `data/audit/audit_chain.db` → `action='tool.confirm_decision'` | 323 | 13 个 subject，其中 3 个是 `__selftest_*`/`__sample__` | 只记"被闸门拦过"的调用，**不记 L0/L1 放行调用** |
| 内存健康计数 | `agent/tools/__init__.py:345-361 _tool_health` | 进程内 | `call_count` 字段 | **不落盘**（`clear()` 即清零，`:853-861`），进程重启即失 |

---

## 5. 关键问题：不可逆能力能否被自动路由/自动执行而绕过确认门？

**结论：能，存在 3 条实测路径。按严重度排序。**

### 5.1 【正在生效】操作员豁免名单把 L2 工具 `fan_out` 放行了

**证据链**：

1. `data/ui_settings.json` 的覆盖层实测值（§4.5B）：
   ```text
   CP_TOOL_CONFIRM_LEVEL_EXEMPT = "fan_out,delegate"
   actor = tok_ffd912584295, updated_at = 2026-09-22T22:55:42+0800, risk = "A"
   ```
2. `fan_out` 的确认级 = **L2**（`data/tool_definitions/fan_out.yaml`；manifest `confirm_level:"L2"`，`risk:"high"`，`effect:"execute"`，`permission_level:"restricted"`）。
3. 生效值来源优先级 `agent/settings/resolver.py:55`：
   `SOURCE_PRIORITY = (SOURCE_ENV, SOURCE_OVERRIDE, SOURCE_CONFIG, SOURCE_DEFAULT)`
   —— `.env` 里该键**被注释掉**（`.env:367` 是 `# CP_TOOL_CONFIRM_LEVEL_EXEMPT=delegate,fan_out`），故 **ENV 层为空 ⇒ 覆盖层生效**。
4. 闸门对豁免名单的处置 —— `agent/tool_gate.py:1314-1325`：
   ```python
   if _is_confirm_level_exempt(func_name):
       _warn_once(... "命中豁免名单 %s ⇒ 免摘要确认直接放行（本应 %s）" ...)
       _audit_confirm_decision(tool=func_name, level=level, decision="exempted", ...)
       return None          # ← 放行
   ```
5. 运行时实证：`audit_chain` 里 `fan_out` 有 **4 条** `decision='exempted'`；`git`/`run_program`/`workspace_delete`/`write_file` 各有 1–2 条同类记录（§4.5C）——说明该名单**历史上被反复用于放行 L2 高危工具**。

> **判断**：`fan_out`（`effect=execute`，即"改变世界"，且是并发分派器）当前**完全不触发人工确认**即可被模型自动路由执行。
> 这条不是缺陷——它是**设计好的、可审计的放宽杠杆**（`agent/tool_exemptions.py:1-30`、`agent/server_routes/routes_settings.py:358`）。但它意味着 §3.2 L2 名单的**实际生效条数是 9 条，不是 10 条**。

### 5.2 【代码缺陷】`agent/tools/__init__.py::call()` 的闸门调用是 fail-open

`agent/tools/__init__.py:400-408`（**唯一汇聚点**的原文）：

```python
    # 集中式工具闸门（**唯一汇聚点**：所有调用方——含 orchestrator 直连——
    # 都必经此处；fail-open，闸门异常视为放行；被拒直接 return，不抛异常）
    try:
        from agent.tool_gate import check_tool_call as _gate_check
        _denied = _gate_check(name, params)
    except Exception:  # noqa: BLE001  闸门故障/不可用 ⇒ 放行
        _denied = None
    if _denied is not None:
        return _denied
```

`from agent.tool_gate import ...` **写在 try 内部**。因此：

- 若 `agent.tool_gate` 因**任何**原因导入失败（依赖缺失、语法错误、循环导入、被改名/删文件），异常被吞掉，`_denied = None`，**全部 114 项能力（含 `shell_execute`/`run_sandbox`/`generate_tool`）无确认放行**。
- 闸门**内部**的 fail-closed 保护（`agent/tool_gate.py:721-740`：治理动作异常 ⇒ 拒绝）**救不了这一层**——它只在 `check_tool_call` 被成功调用后才生效。
- 对照：同一文件对注入防御层的写法是**先 import 模块顶层、再调用**（`:393 _injection_denied = check_injection_guard(name, params)`，同样只出 deny 不出 allow），且注释 `:390-392` 明确写"**因此不可能把 `tool_gate` 的既有层短路成死代码**" —— 这句话对"导入失败"这一情形**不成立**。

> 【推测】按当前仓库状态 `import agent.tool_gate` 是成功的（`tests/unit/test_confirm_level.py` 依赖它），因此这条路径**当前是潜在风险、不是现实缺口**。但它是一个**单点、静默、无告警**的全局致盲开关。

### 5.3 【整条链路不过闸门】技能脚本执行：`effect=execute` 但从不经过 `tool_gate`

**代码路径（三段，逐跳可查）**：

| 跳 | 位置 | 内容 |
|---|---|---|
| ① HTTP 入口 | `agent/server_routes/routes_skills_mgmt.py:1424-1446` | ```app.route("/api/skills-mgmt/<skill_id>/execute", methods=["POST"])` + `require_token` → `_svc().execute_skill_script(...)` |
| ①' 第二入口 | `agent/server_routes/routes_skills_mgmt.py:1501-1553` | `POST /api/skills-mgmt/slash/<skill_id>`，`command="execute"` → `svc.execute_skill_script(...)` |
| ② 服务层 | `agent/skills_mgmt/service.py:2051-2103` | `execute_skill_script()` → `self.executor.execute(...)` |
| ③ 执行体 | `agent/skills_mgmt/executor.py:202-212` | `proc = subprocess.run([self.python_exe, "-u", str(script_path)], input=stdin_data, capture_output=True, text=True, timeout=use_timeout, cwd=str(skill_dir), env=safe_env, ...)` |

**实测确认**：
- `agent/skills_mgmt/executor.py` 全文件**不 import `agent.tool_gate`**，也不调用 `check_tool_call` 或 `agent.tools.call`（grep `tool_gate` 在 `agent/skills_mgmt/*.py` 零命中）。
- 该路径**不在** `agent/capregistry/call_sites.py::EXEMPT_CALL_SITES`（例外表共 12 条，逐条核对无此项）——即**既没走闸门，也没被登记为例外**，属未登记直调。
- 唯一的约束是 `subprocess.run` 的 `timeout` + `cwd` + 环境变量白名单（`executor.py:120 _build_safe_env`），**没有**路径白名单、没有审批、没有 `confirm_level`。
- 对应技能 `scripted-selftest`：manifest 实测 `effect="execute"`、`has_scripts=true`、`risk="low"`、`llm_callable=false`、`callable_mode="manual"`（§4.3）。
- 鉴权面只有 `require_token`（`agent/server_auth.py:102-115`），而 `.env:421` 实测**设了单一共享令牌** `FLASK_API_TOKEN`（64 字符），`CP_UI_TOKENS` **未设** ⇒ 全部调用者共用同一把令牌，无法区分到人（`agent/security/service_account.py:11` 自述"**唯一一把共享令牌**"）。

> **判断（事实）**：存在一条 **HTTP → 子进程执行任意技能 Python 脚本**的路径，它**不经过 `tool_gate`、不落入 L0–L3 任何一级、不产生 `tool.confirm_decision` 审计**。
> 【推测】模型要走到这条路需要先用某个工具对 `127.0.0.1:5678` 发 POST（`web_post` 在清单里存在且 `main_line_status=not_assembled`），并持有共享令牌 —— 中间环节本次未实测验证，故标为推测。**但路径本身不需要任何推测**。

### 5.4 【当前不可达，设计上就是出口】SA 预授权

`agent/tool_gate.py:1391-1399`：

```python
    if identity == "service_account" and _preauthorized(func_name, identity, args, level):
        logger.info("[tool_gate] 工具 %s 以 **SA 预授权** 执行（%s，身份 %s）", func_name, level, identity)
        _audit_confirm_decision(..., decision="preauthorized", ...)
        return GUARD_PASS          # ← L2/L3 免人工确认直接执行
```

**实测可达性**：
- 凭据默认路径 `agent/security/service_account.py:104-106` → `data/service_accounts.json`。
- 实测 `Test-Path data\service_accounts.json` = **False**；`.env` 中 `CP_SERVICE_ACCOUNTS_PATH` / `CP_SERVICE_ACCOUNT_KEY` **均未设置**。
- `preauthorize()` 在 `current_service_account() is None` 时返回 `False`（`service_account.py:936-937`）。
⇒ **没有已注册的 SA，预授权路径当前不可能触发**。这一条**不是缺口**：它是 L3 语义（"仅显式预授权才能执行"）的**唯一合法出口**，且落 `decision=preauthorized` 审计。

- 审计台账里确有 **44 条** `preauthorized`（shell_execute 30 / write_file 14），时间**全部集中在 2026-09-20 09:26–11:01**，且与该时段的 `__selftest_confirm_level__`、`__selftest_tenant_isolation__` 记录**逐条交错**。
  【推测】这批记录来自一次 e2e 测试会话（测试用临时 SA 上下文），而非生产 SA —— 因为生产凭据文件不存在。**这不足以推翻"路径存在"的事实**，但足以说明"该路径在本部署未被真实使用"。

### 5.5 已排除的候选（逐条给依据，避免误报）

| 候选 | 结论 | 依据 |
|---|---|---|
| `agent/async_executor.py` 后台线程 | **过闸门** | `:22 from agent.tools import call as call_tool`；`:280` 注释"经 agent.tools.call ⇒ 过 tool_gate"；`agent/capregistry/call_sites.py:177-190` 登记为 `funnel_no_identity`（**不是绕过**，缺口是身份缺失 ⇒ `session_source` 退化为 `cli`） |
| `mcp_services/yunshu_mcp_server.py::_handle_tools_call` | **过闸门** | `agent/capregistry/call_sites.py:199-211`；`_tools.call(...)` |
| `agent/tools/mcp_connector.py::_handler` | **过闸门** | `call_sites.py:298-313`，经 `register_dynamic(source='mcp')` 进 `_registry` |
| `agent/skills_mgmt/mcp_adapter.py` 远程直调 | **当前不可达** | `call_sites.py:216-229 / 318-333`；`_check_mcp_sdk()` 抛 ImportError（实测 `import mcp` 失败） |
| `agent/mcp_executor.py` | **死模块** | `call_sites.py:236-260`，生产零调用方 |
| `POST /capabilities/invoke` | **是收口定义处** | `agent/capregistry/invoke.py:5-11` 铁律；`call_sites.py:263-286`，`via_gate=True` |
| 无身份/非交互调用 | **拒绝，不挂单** | `tool_gate.py:1379-1388 / 1402-1413`；实测 `write_file` 有 88 条 `denied_no_identity` + 67 条 `denied_non_interactive`（§4.5C） |

---

## 6. 「瘦身豁免名单」：30 天零调用 ∧ 无 owner ∧ 非 L2/L3

### 6.1 三个条件逐一实测

| 条件 | 实测结果 | 能否作为判据 |
|---|---|---|
| **30 天零调用** | **无法判定**。唯一持久台账 `agent/data/tool_trace.db::tool_traces` 只覆盖 **3/91** 个真实工具（`shell_execute` 27 / `read_file` 6 / `list_directory` 1），其余 12 个名字（`op`/`t0`…`t7`/`search`/`calc`/`get_current_time`）**不在 114 项清单内**（测试夹具）；整表时间跨度 **2026-09-08 ~ 2026-09-21 = 12.8 天**，**凑不出 30 天窗口**。`unified_traces` 更差：4 个 `capability_id`，其中 2802/3716 条为空值 | 不可用 |
| **无 owner** | **0 条**。`owner` 114/114 = `builtin`（§4.1）。该字段是 provenance 枚举，**不是责任人** | 无区分度（会把全部 114 条判成"有 owner"，把 0 条判成"无 owner"） |
| **非 L2/L3** | 可判定且确定：L2/L3 = 20 条（§3） | 可用 |

> **因此：三条件严格取交集 = 空集。**「30天零调用 ∧ 无 owner ∧ 非L2/L3」在本仓库**无法产出非空名单**，原因是前两个条件的数据不存在（详见 §7）。

### 6.2 退而求其次：可用的替代判据与清单

把"30 天零调用"降级为「**在唯一可得台账里零记录**」（并显式声明它 ≠ 真零调用），并保留"非 L2/L3"：

**候选池 = 91 个工具 − 20 个 L2/L3 − 3 个有记录 = 69 个；再加 23 个技能（全部无 confirm_level、零记录）= 92 项。**

其中**真正值得优先裁的**是那些"已在主线装配里占 token"的（`main_line_status=visible`）——16 个：

| # | 工具 | effect | risk | confirm | llm_callable | callable_mode | permission |
|---|---|---|---|---|---|---|---|
| 1 | `arch_diagram` | write | low | L1 | True | auto | internal |
| 2 | `code_review` | read | low | L0 | True | auto | public |
| 3 | `compress` | write | medium | L1 | True | auto | internal |
| 4 | `delegate` | execute | medium | **L1（当前被豁免名单覆盖，见 §5.1）** | True | auto | internal |
| 5 | `diff_files` | read | low | L0 | True | auto | public |
| 6 | `get_file_info` | read | low | L0 | True | auto | public |
| 7 | `get_task_result` | read | low | L0 | True | auto | public |
| 8 | `get_task_status` | read | low | L0 | True | auto | public |
| 9 | `grep` | read | low | L0 | True | auto | public |
| 10 | `remember` | write | low | L1 | True | auto | internal |
| 11 | `run_lint` | execute | low | L1 | True | auto | internal |
| 12 | `run_tests` | execute | medium | L1 | True | auto | internal |
| 13 | `search_files` | read | low | L0 | True | auto | public |
| 14 | `search_memory` | read | low | L0 | True | auto | public |
| 15 | `submit_task` | execute | medium | L1 | True | auto | internal |
| 16 | `todo_write` | write | low | L1 | True | auto | internal |

**muted 的 3 个**（`main_line.muted`，已不发但仍在册）：`web_batch`、`web_extract`、`web_search`。

**未装配的 50 个**（`main_line_status=not_assembled`，零 token 成本，属"清理台账"而非瘦身）：
`browser_close`、`browser_navigate`、`browser_screenshot`、`cancel_scheduled_task`、`cancel_task`、`data_convert`、`data_format_detect`、`distill_process_from_knowledge`、`ext_discover`、`ext_list`、`get_clipboard`、`get_pdf_info`、`get_persona_info`、`get_preferences`、`get_sensor_summary`、`get_status`、`get_weather`、`humanize_zh`、`json_query`、`kb_capture`、`kb_card`、`kb_discuss`、`kb_distill`、`kb_lint`、`kb_search`、`list_async_tasks`、`list_mcp_connections`、`list_processes`、`list_scheduled_tasks`、`look_at_screen`、`merge_pdf`、`notify`、`pause_scheduled_task`、`process_distill_run`、`read_pdf`、`read_pdf_tables`、`resume_scheduled_task`、`search_lifetrace`、`set_clipboard`、`split_pdf`、`sqlite_query`、`stop_process`、`trigger_distillation`、`web_download`、`web_get`、`web_post`、`weekly_report`、`workspace_init`、`workspace_list`、`workspace_write`。

**技能 23 项**：全部 `effect=read`、`risk=low`（唯 `scripted-selftest` 是 `effect=execute`+`has_scripts=true`）、`llm_callable=False`、`callable_mode=manual`、零调用记录、无 confirm_level 字段。
⇒ 其中 **22 个纯提示词技能**可直接进豁免候选；**`scripted-selftest` 必须排除**（它 `effect=execute`，且 §5.3 证明它能不经闸门执行子进程）。

### 6.3 使用这份名单前的三条强制提醒

1. **本名单不是"30 天零调用"名单**，而是"**在唯一可得台账里零记录**"名单。台账只覆盖 3/91，因此 69 个候选里绝大多数是**"从没被记账"**而非**"从没被调用"**。
2. **`owner` 条件未参与筛选**（它 100% = builtin，无区分度）。若组织上确实需要"孤儿能力"判据，需**先新增** `owner_contact` / `maintainer` 字段（当前不存在）。
3. **必须排除 L2/L3 20 项**（`agent/capregistry/pruning.py:127-129` 已把 L2/L3 定为裁剪保护：`PROTECTED_CONFIRM_LEVELS = ("L2","L3")`；`agent/lines/assembler.py:293` 同样豁免）。本名单已排除。

---

## 7. 设计文档里 Q1 的期望产出中，**现在根本取不到**的数据

| # | 期望产出 | 状态 | 原因与证据 |
|---|---|---|---|
| 1 | **「30 天零调用」的调用计数** | **取不到** | 唯一持久台账 `agent/data/tool_trace.db::tool_traces` 覆盖 **3/91**，跨度 **12.8 天**（<30）；`unified_traces` 覆盖 3/91 且 75% 行 `capability_id` 为空；内存计数器 `agent/tools/__init__.py:345-361` **不落盘**、`clear()` 清零（`:853-861`）。⇒ 需先补一个**按能力、按天、持久化**的调用计数表 |
| 2 | **「无 owner」的能力** | **判不出来** | `owner` 是 **provenance 枚举**（`agent/lines/models.py:85 OWNERS`），实测 114/114 = `builtin`，且 `owner` 被 `spec_required_fields` 强制必填 ⇒ **永不为空**，无法识别孤儿能力。仓库 62 个字段键里**没有**任何负责人字段 |
| 3 | **23 个技能的 L0/L1/L2/L3 确认级** | **概念上不存在** | `confirm_level` 五连字段只存在于 91 个 tool 条目（§4.1）。技能侧走 `agent/skills_mgmt/approval.py:71 APPROVAL_LEVELS=("L0","L1","L2")`（**只有 3 级、语义不同**），且该值**不参与**任何"这次调用要不要人点确认"的判定。`tests/unit/test_confirm_level.py:357-364` 甚至**断言**技能条目"**不该有**"工具侧 confirm_level 字段 |
| 4 | **不可逆性（irreversibility）字段** | **不存在** | 62 个键中无该字段；只能用 `effect ∈ {write,execute,extend}` + `risk` 三轴**近似**推断"不可逆"。任务书问"哪些属于不可逆能力"——清单**没有**正面回答该问题的字段 |
| 5 | **每项能力的调用主体/最后调用时间** | 部分可得、不可信 | `audit_chain` 的 `tool.confirm_decision` 有 `identity`/`session_source`/`ts`，但**只覆盖被闸门拦下的调用**（13 个 subject，其中 3 个是测试夹具）；L0/L1 放行调用**完全不留痕** |
| 6 | **任务书声称的"每条约 31 个字段"** | 实测不符 | 实测 **114 条全部 = 57 个字段**，键并集 62（§4.2） |
| 7 | **`agent/server_auth.py` 注释声称的"本部署未配置任何令牌"** | 实测相反 | 注释 `:151-153`："本部署实测**未配置任何令牌**（`.env` 未设 `CP_UI_TOKENS`、`FLASK_API_TOKEN` 亦为空）⇒ `authorize_token()` 走 `SRC_NO_TOKEN_CONFIGURED` **直接放行**"。**实测** `.env:421` 有 `FLASK_API_TOKEN=<64 字符>`（非空、未注释）⇒ `_API_TOKEN_ENABLED=True`（`server_auth.py:47-48`）⇒ 走 `SRC_SHARED_TOKEN` 分支，**并非 fail-open 放行**。注释已过期，会误导审计结论 |
| 8 | **技能侧"是否带脚本执行面"的结构化风险级别** | 有字段但无风险档 | `has_scripts`（23/114）存在，但 `scripted-selftest` 的 `risk` 仍是 `low`（§4.3）——**带脚本执行面却不提升 risk**，导致 `derive_confirm_level` 若被应用到技能上仍会算出 L1 而非 L3 |

---

## 8. 建议的最小修补（按优先级，均不扩大本次只读范围）

| 优先级 | 动作 | 位置 |
|---|---|---|
| P0 | 把 `from agent.tool_gate import check_tool_call` 提到模块顶层，或在 `except` 分支改为 **fail-closed**（治理动作直接 `_deny`） | `agent/tools/__init__.py:402-406` |
| P0 | 技能脚本执行收口到 `agent.tools.call()`（或至少在 `executor.execute` 前插一次 `check_tool_call`），并把该调用点登记进 `EXEMPT_CALL_SITES` | `agent/skills_mgmt/executor.py:202`、`agent/capregistry/call_sites.py:129` |
| P1 | 复核 `CP_TOOL_CONFIRM_LEVEL_EXEMPT=fan_out,delegate` 是否仍需生效；`fan_out` 是 L2 且 `effect=execute` | `data/ui_settings.json` |
| P1 | 给 `scripted-selftest` 提升 `risk`（或显式声明其为治理动作），使其带脚本执行面这件事被风险档反映 | `data/skills_repo/scripted-selftest/skill.md` |
| P2 | 新增持久化的"能力 × 天"调用计数表（当前无任何可信数据源） | 新表；可挂在 `agent/data/tool_trace.db` 同库 |
| P2 | 新增 `owner_contact`（责任人）字段，否则"孤儿能力"永远判不出 | `agent/lines/models.py` + manifest schema |
| P3 | 修正 `agent/server_auth.py:151-153` 的过期注释 | `agent/server_auth.py:151` |

---

## 附：本次实际执行的只读命令清单

```text
python -X utf8 q1_audit.py            # 清单确认级统计（§4.0/§4.1）
python -X utf8 q1_final.py            # 清单 × 台账交叉（§4.4/§4.5）
python -X utf8 q1_db.py / q1_db2.py / q1_db3.py   # audit_chain.db 只读 sqlite（uri mode=ro）
python -X utf8 q1_trace.py            # tool_trace.db 只读
python -X utf8 q1_set.py              # data/ui_settings.json 覆盖层
python -X utf8 q1_slim.py             # 瘦身候选分层
Select-String .env -Pattern "FLASK_API_TOKEN|CP_TOOL_*|CP_SERVICE_ACCOUNT*"
Test-Path data\service_accounts.json  → False
```

*未执行：pytest / 启动服务 / git 写操作 / 任何文件修改（除本报告）。*
