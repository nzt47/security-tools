# AUDIT-ROOT-REPAIR —— 生产日根封印文件被测试进程写坏：根因、修复与残留

| 项 | 值 |
|---|---|
| 卡片代号 | **AUDIT-ROOT-REPAIR** |
| 基线 HEAD | `5c9ace10a4ca4bb96860db3a48debf9ddcf496bf`（工作区含 33 张卡的未提交改动） |
| 执行时间 | 2026-09-26（UTC），本机 UTC+8 |
| 出网 | **零**（全程未联网；`web_search`/`pip install` 均未使用） |
| 本卡改写的路径 | `data/audit/daily_roots.jsonl`（**仅追加 1 行**）、`scripts/audit_reseal_daily_root.py`（新增 `--chain-from`，默认行为不变）、`tests/unit/test_audit_production_isolation.py`（新增 3 条回归用例）、本文件 |
| git | **未做** `git add`/`git commit`；未做整文件 `git checkout` |

---

## 0. 一句话结论

1. **根因被差分证实**（不是读码推论）：`AuditChain.__init__` 原先只认显式 `roots_path=`、**不读**
   `AUDIT_ROOTS_PATH`，而门面 `facade.py` 读 ⇒ 测试进程「直接构造的链 + 默认开启的 `auto_seal`」
   把**测试小链的日根**追加进了**生产** `data/audit/daily_roots.jsonl`。主审计已在
   `agent/audit/chain.py:1165-1166` 修好那一行（本卡未再改动它，只做差分验证与回归加锁）。
2. **生产日根已修复**：为 2026-09-25 追加了第 14 行——一条**正确**的根
   （`leaf_count=440 / first_seq=72261 / last_seq=72700`），使默认解析（同日取最后一条）
   重新返回真值。追加前后 `sha256 60948294…` → `E4C1F1B8…`，旧 13 行**逐字节未动**。
3. **但 `audit_governance_check.py` 的 FAIL 数没有降到 0**：仍是 **FAIL=9 / WARN=2（rc=1）**。
   原因是**另一层**缺陷形态——日根文件的外层根链是**位置式**校验（`chain.py:3298-3319`），
   而 2026-09-25 的 3 条污染记录里第 2 条的 `prev_entry_hash` 就等于第 1 条的 `prev`
   ⇒ **断点在第 12 行，追加永远修不到断点之前**。
   9 条 FAIL **全部**由这一个断点派生（8 条 `root_chain_broken` + 1 条 09-25 自己的窗口失败）；
   唯一另有原因的是 2026-09-21（历史缺陷，本来就在 WARN 桶，本卡未动）。
4. **闭合 FAIL=0 的唯一路径已用副本证明**：把第 11–13 行这 3 条**测试污染记录**摘掉
   （保留我方新增的第 14 行）后，`audit_governance_check.py` 返回 **FAIL=0 WARN=2（rc=0）**。
   该动作与「封印日志只追加、不删改既有行」的纪律直接冲突 ⇒ **本卡不擅自执行**，见 §7 R-1 与 §8。

---

## 1. 根因：差分实测（不是读码）

### 1.1 缺陷形态（两行代码，同一语义两个入口）

| 入口 | 代码 | 是否读 `AUDIT_ROOTS_PATH` |
|---|---|---|
| 门面 | `agent/audit/facade.py:199`：`roots_path or os.getenv(_ENV_ROOTS_PATH) or DEFAULT_ROOTS_PATH` | **读** |
| 构造器（修复前） | `agent/audit/chain.py`：`self._roots_path = os.path.abspath(roots_path or DEFAULT_ROOTS_PATH)` | **不读** |
| 构造器（现状，主审计已改） | `agent/audit/chain.py:1165-1166`：`roots_path or os.getenv("AUDIT_ROOTS_PATH") or DEFAULT_ROOTS_PATH` | **读** |

`tests/conftest.py:255-287` 的会话级夹具把 `AUDIT_DB_PATH`/**`AUDIT_ROOTS_PATH`**/`AUDIT_SIGNING_KEY`
都指向会话临时目录 —— 但修复前**直接构造**的链根本不看这个 env，于是：

```
测试进程：AuditChain(<tmp>/audit.db)                  # 没有 roots_path=
   └─ auto_seal 默认 True（chain.py:1141）
        └─ _maybe_auto_seal()（chain.py:2400-2424）封「已过完的 UTC 日」的日根
             └─ daily_merkle_root(day) → _append_daily_root()
                  └─ 写 self._roots_path == 生产 data/audit/daily_roots.jsonl   ← 缺陷
```

`agent/tool_gate.py:1400` 早已把这条通路记为已知风险（"每日 Merkle 根会落到生产"）。

### 1.2 探针设计（三场景 A/B/C，全部写在仓库外）

探针脚本：`%TEMP%\auditroot_probe_step1.py`；结构化报告：`%TEMP%\auditroot_step1\step1_report.json`。

公共夹具：把**生产文件的前 10 行**（= 污染发生**之前**的干净状态，sha256
`bd7a566751a46950c6f894aca3d4ad3fc0ae7deda4eccb40956495fbac78a587`）写成副本当"DEFAULT 日根文件"，
再用一个**临时库**、**不传 `roots_path=`**、`auto_seal=True` 的链写入 4 条 ts 落在
`2026-09-25`（已过完的 UTC 日）的记录，等后台 writer 线程自动封存。

| 场景 | 模拟的代码版本 | `AUDIT_ROOTS_PATH` | 链实际绑定的日根文件 | 新增日根落到哪 |
|---|---|---|---|---|
| **A** `post_fix_env` | **现状**（读 env） | 指向隔离目录 | **隔离目录** | **隔离目录**（1 条）；DEFAULT 副本仍 10 行、sha 未变 |
| **B** `pre_fix_emul` | **修复前**（进程内遮蔽 `getenv("AUDIT_ROOTS_PATH")` ⇒ 等价于 `roots_path or DEFAULT_ROOTS_PATH`） | 同样指向隔离目录（**被无视**） | **DEFAULT 副本** | **DEFAULT 副本**（10→11 行）；隔离目录**根本不存在**（写了 0 条） |
| **C** `post_fix_noenv` | 现状 + **不设** env | 未设 | DEFAULT 副本 | DEFAULT 副本（= 生产回落路径，逐字不变） |

### 1.3 结果（关键字段，逐字取自 `step1_report.json`）

**A（修复后，env 生效）**

```text
effective_roots_path = ...\post_fix_env\isolated\daily_roots.ENV.jsonl
default_after        = {lines: 10, sha256: bd7a566751a46950c6f894aca3d4ad3fc0ae7deda4eccb40956495fbac78a587}
env_after            = {lines: 1,  sha256: 02de500d044f3b5fb44770abd3dfd342bcfcc01826ade93aa4cf726b4de0d66b}
env_records[0]       = date=2026-09-25 leaf_count=4 first_seq=1 last_seq=4 prev=000…0
```

**B（修复前，env 被无视 —— 这就是事故本身）**

```text
effective_roots_path = ...\pre_fix_emul\daily_roots.DEFAULT.jsonl     ← 生产等价路径
env_after            = {exists: false}                                ← 隔离目录一条都没写
default_new_records[0] = {"date": "2026-09-25", "first_seq": 1, "last_seq": 4, "leaf_count": 4,
                          "prev_entry_hash": "65c70ab7b528b5d9d5d2d97616cfd3e1b1a04a1afa164c206d275f40045f0a49",
                          "signer_public_key": "b73dd748ad340a2fa87a4646b6846965bc8fb544e7a8951bb9c22b877d98ae6f",
                          "signature_scheme": "ed25519"}
```

**C（修复后不设 env）**：落到 DEFAULT（= `DEFAULT_ROOTS_PATH`）⇒ **生产行为零变化**。

**生产面未被探针触碰**：探针运行前后，`data/audit/daily_roots.jsonl`
（`60948294…` / 11667 B / 13 行）与 `data/audit/audit_chain.db`（`2ee6ef46…`）的
size/mtime_ns/sha256 **完全相同**，`prod_unchanged = true`。

### 1.4 与现场物证的逐字段对照（B ≡ 生产第 11–13 行）

| 字段 | 场景 B 的产物 | 生产第 11 行 | 生产第 12 行 | 生产第 13 行 |
|---|---|---|---|---|
| `date` | 2026-09-25 | 2026-09-25 | 2026-09-25 | 2026-09-25 |
| `leaf_count` | **4** | **4** | **2** | **4** |
| `first_seq`/`last_seq` | **1 / 4** | **1 / 4** | **1 / 2** | **1 / 4** |
| `prev_entry_hash` | **65c70ab7…** | **65c70ab7…** | **65c70ab7…** | **65c70ab7…** |
| `signer_public_key` | **b73dd748…** | **b73dd748…** | **b73dd748…** | **b73dd748…** |
| 当日真值（只读 SQL） | — | 该日 **440** 条、seq **72261..72700**（`chain.entries(day=)` 返回 440） | 同左 | 同左 |

`leaf_count` 是 **2/4**、`first_seq` 全是 **1** ⇒ 那是**空库起算的测试小链**的形状，
不可能是生产库（生产真值 440 条）。三条记录的 `created_at` 是
`2026-09-26T00:00:00.654660/.769005/.871082+00:00`——**UTC 零点过后 0.65–0.87 s 内三个写入者**，
正是"跨过 UTC 零点的那一瞬间，多个测试进程的 `_maybe_auto_seal` 同时把 09-25 封掉"的签名。
（生产文件 mtime = `2026-09-26T00:00:00.889238Z`，与第三条记录的写入时刻一致。）

> **对 G1C.md R-1 第 4 点的更正**：该文档据"mtime 08:00:00（整点）"推断"有人手动跑了重封脚本"。
> 那是 **UTC+8 显示**造成的误读（08:00 本地 = 00:00 UTC）；且人工重封用的是生产库，
> 只会产出 `leaf_count=440` 的根，不可能产出 `2/4`。实测结论：**测试进程 auto_seal 所致**。

### 1.5 为什么没有采用"临时改回源码、跑完再还原"

卡片允许"临时改后立刻还原并核验 sha256"。本卡**刻意不用**，理由是实证过的风险：

- 工作区里**另有两张卡在跑测试**（本次执行期间实测到 `_tmp_rootcause_probe/`、
  `data/lifetrace/sources/*`、`agent/data/tool_trace.db*` 等其它进程的持续写入）。
  把 `chain.py` 改回缺陷版本哪怕几十秒，那些**并发测试进程**就会立刻把新的测试日根
  写进生产文件 —— **本缺陷的表现形式恰恰就是"测试进程写生产"**，不能用一个真实污染窗口去换证据。
- 等价性论证：修复前那一行的语义**恒等于** `roots_path or DEFAULT_ROOTS_PATH`
  ⇒ 在进程内把 `AUDIT_ROOTS_PATH` 的 `getenv` 遮蔽成 `None`、同时把 `DEFAULT_ROOTS_PATH`
  指向副本，与该分支**逐字等价**，且**零生产写入**。场景 B 即此实现（`step1_report.json`
  的 `mode=pre_fix_emul`，构造期遮蔽、构造后立即还原）。

---

## 2. 绕过 `AUDIT_ROOTS_PATH` 的构造点清单与处置（防御纵深）

### 2.1 全量统计

扫描仓库全部 `.py`（排除 `.git/__pycache__/venv/node_modules/.pytest_tmp`），用括号配对解析出
每个调用的**完整实参文本**（不是单行 grep）：

| 口径 | 数量 |
|---|---|
| `AuditChain(...)` / `AuditChain.reader(...)` / `AuditChain.writer(...)` / `get_audit_chain(...)` / `open_audit_chain(...)` 调用点（去掉注释/docstring） | **140** |
| 其中**显式传了** `roots_path=` | **96** |
| 未在调用点出现 `roots_path=` | **44** |

### 2.2 未传点的四类判断

| 类 | 说明 | 点位数 | 会不会写生产日根 |
|---|---|---|---|
| **A** | **直接构造 + 未传 `roots_path=` + 会 `append()` + `auto_seal` 默认开** | **5** | **修好 env 前：会** |
| **B** | 调用点写 `**params`，而 `params` 字典里**已有** `roots_path=` | 2 | 不会（已隔离） |
| **C** | 工厂 `get_audit_chain(...)`（透传 `**kwargs`，无参调用） | 25（含定义/注释/生产调用方与测试调用方） | 随构造器语义 ⇒ 修好后跟随 env |
| **D** | `AuditChain.reader(...)` | 10（含注释/docstring） | **结构上不可能**：`reader()` 强制 `auto_seal=False`（`chain.py:3400`） |

**A 类（本卡的核心清单，5 处，全部会 append）**

| # | 位置 | 调用 | 现状与处置 |
|---|---|---|---|
| A1 | `scripts/chaos_s4_03_drill.py:239` | `AuditChain(db_path=str(db_path))`（tmp 库，默认 `auto_seal=True`） | **范围外**（本卡文件范围不含该脚本）⇒ 建议补 `roots_path=<tmp>/daily_roots.jsonl, signing_key_path=<tmp>/k.pem`；当前由 `AUDIT_ROOTS_PATH` 兜住 |
| A2 | `tests/unit/test_guardrails_egress_chain.py:262` | `AuditChain(db_path=str(tmp_path / "audit.db"))` | **范围外** ⇒ 同上建议；当前由 env 兜住 |
| A3 | `tests/unit/test_guardrails_foreign_taint.py:454` | 同 A2 | 同上 |
| A4 | `tests/unit/test_guardrails_instruction_data.py:322` | 同 A2 | 同上 |
| A5 | `tests/unit/test_s6_01_ui_panels.py:620` | `AuditChain(str(tmp_path / "chain.db"))` | 同上 |

**B 类（grep 看起来"没传"，实际已隔离 —— 逐一读码确认）**

| 位置 | 证据 |
|---|---|
| `tests/unit/test_audit_read_path.py:43` | `params = dict(roots_path=str(tmp_path/"roots.jsonl"), …, auto_seal=False, …)`（:39-41）后 `**params` |
| `tests/unit/test_audit_journal_drain.py:61` | 同上（:58-59 含 `roots_path` + `auto_seal=False`） |

**特别说明（卡片点名的那条）**：`tests/unit/test_audit_governance_check.py` **不读生产日根文件**——
它在 `:90-94` 用 `roots_path=str(tmp_path/"daily_roots.jsonl")` 构造，且 CLI 调用一律带
`--db/--roots` 指向 tmp（`:102-105`、`:311-312`）。本卡**未改**该文件。

### 2.3 本卡的处置

1. **范围内（`tests/unit/test_audit_*.py`）无需补 `roots_path=`**：全部审计测试文件要么显式传、
   要么走 B 类 `params`。`tests/unit/conftest.py:742` 的 `s401_audit_chain` 也已显式传。
2. **新增 3 条回归用例**锁住这条通路（`tests/unit/test_audit_production_isolation.py`）：
   - `test_audit_roots_env_is_session_isolated`（只读）：会话级 `AUDIT_ROOTS_PATH` 必须在临时目录；
   - `test_ctor_honours_env_roots_path`：**直接构造**的链必须跟随 env（红了 = `chain.py` 那行被改回去）；
   - `test_auto_seal_of_test_chain_never_touches_production_roots`：复现"测试小链 + auto_seal"通路，
     断言新日根**只**落到隔离文件、形态为 `leaf_count=3/seq 1..3`，且**生产文件的 size/mtime_ns 不变**。
     为守住"不先污染再报红"的纪律，该用例**先断言绑定、后写入**。
3. **范围外的 A 类 5 处不擅自改**（文件不在本卡范围，且改动会与在跑的两张卡抢文件）；
   给出精确补丁建议（见上表），并说明**当前它们已被修好的 env 语义兜住**：
   `tests/conftest.py:247-287` 会话级设 `AUDIT_ROOTS_PATH` → 直接构造现在会跟随；
   且设置中心覆盖层**压不过**它（`agent/settings/resolver.py:333-334` 的 `env_locked`：
   env 已存在且未被覆盖层写过 ⇒ 覆盖层被跳过）。

### 2.4 残留缺口（本卡未闭合，已登记）

`tests/conftest.py:287` 用的是 `os.environ.setdefault(k, str(v))` ⇒ **外部继承**的
`AUDIT_ROOTS_PATH`（例如运维在 shell 里显式指向生产）会**压过**会话隔离。
修好 env 后这不再造成"新"污染（修复前无论如何都写生产），但它是隔离的薄弱点：

- 本卡的缓解：新增的只读守卫用例 `test_audit_roots_env_is_session_isolated` 会在这种情况下**红**；
- 建议（未做，属另一张卡的判断）：把 `AUDIT_*` 三键从 `setdefault` 改为**硬赋值 + 退出时还原**。

---

## 3. 生产日根数据修复（本卡核心交付）

### 3.1 动手前的备份

| 项 | 值 |
|---|---|
| 备份路径（**仓库外**） | `%TEMP%\auditroot_step3\daily_roots.BACKUP.jsonl` |
| sha256（= 修复前的生产文件） | `60948294FB73F0C2D28720059F12E94DC014BE8E9B72CD0AA3770F79DE2B42B0` |
| size / 行数 | 11667 B / 13 行 |
| mtime_ns（epoch） | `1790380800889238300`（= 2026-09-26T00:00:00.889238300Z） |
| 文件属性 | `ReadOnly, Archive`（`_protect_readonly` 单机降级保护；写入工具会临时解锁再复原） |

### 3.2 `prev_entry_hash` 取值裁定：`65c70ab7…`（**刻意偏离** chain 的默认语义）

`chain._append_daily_root()`（`chain.py:3013-3018`）与《审计日根封印与导入流程约定》§4.2 的既有
约定是：**新根 `prev_entry_hash` = 文件末行的 `entry_hash`**。若照办，新根会接到
**第 13 行（测试污染记录，`entry_hash=9f892d3e…`）**后面。本卡**不这么做**，裁定为：

```
prev_entry_hash = 65c70ab7b528b5d9d5d2d97616cfd3e1b1a04a1afa164c206d275f40045f0a49
                = 第 10 行（date=2026-09-23，**最后一条真实生产根**）的 entry_hash
```

理由（三条，均为可验证的事实而非偏好）：

1. **"接末行"什么也修不了**：外层根链是**位置式**遍历（见 §4.1），断点在第 12 行。
   接第 13 行只是让"新行 ↔ 第 13 行"连续，断点仍在原地；`FAIL` 计数**实测不变**（见 §4.4）。
2. **"接最后一条真实根"让文件在两条可能的重修路径下都能自愈**：
   - 若最终裁定"隔离掉 3 条污染行"：文件变成 `1..10 + 第 14 行`，
     `第10行 → 第14行` 的 `prev` 正好接上 ⇒ 根链连续（**已在副本上实测 FAIL=0**，§3.6/§4.4）；
   - 若最终裁定"把根链语义改为按日有效根"（`resolve_daily_root` 的"同日取最后一条"是 D5 已定的
     取用语义）：有效根序列是 `… 09-23 → 09-25(第14行)`，也正好连续。
   接第 13 行的方案在这两条路径下**都**不自洽。
3. **与卡片要求一致，且不是"硬编"**：`prev` 取自**文件里真实存在**的那条 09-23 根的
   `entry_hash`（`resolve_daily_root("2026-09-23")`，不是手写的字面量）；
   2026-09-24 见 §3.5。附带的自洽性：3 条污染记录**自己**用的 `prev` 也是 `65c70ab7…`
   （它们写下时读到的末行就是第 10 行）⇒ "我这条是 09-23 之后的下一环"这一语义与它们一致。

### 3.3 工具改动：`scripts/audit_reseal_daily_root.py` 新增 `--chain-from`

因为 `_append_daily_root` 固定取"末行"，`chain.reseal_daily_root()` 无法产出上面的 `prev`。
本卡在同一进程内做了**唯一的、显式的**遮蔽（不改 `agent/audit/chain.py` 一行）：

1. 新增 CLI 选项 `--chain-from {tail|YYYY-MM-DD|<64hex>}`（**默认 `tail` = 原行为逐字不变**）；
2. 非 `tail` 时解析为 `prev_entry_hash`：`YYYY-MM-DD` ⇒ 该日**有效根**的 `entry_hash`
   （`resolve_daily_root`；该日无记录 ⇒ 直接拒绝退出码 2，**绝不凭空编造 prev**）；
3. **只允许单日**（多目标一次追加会各自共用同一个 `prev`，当场制造新的断链 ⇒ 退出码 2）；
4. 写入仍走 chain 的既有实现（`_append_daily_root`：canonical JSON / fsync / 只读保护 /
   `entry_hash` 重算），仅把实例方法 `_last_root_entry_hash` 遮蔽为返回该值；
5. 自验输出增加一行"自验明细"（根哈希重算/签名/外层根链/叶子数），并在"根本体正确、只有外层根链失败"
   时给出显式判读 —— 避免运维把这种结果误读成"新根又算错了"。

回归：另一张卡的 `tests/unit/test_daily_root_reseal.py` **19 条全绿**（默认 `tail` 未被改变）。

### 3.4 执行顺序：副本预演 → 生产实写

**① 副本预演**（`--roots <副本>`、`--db <生产库（read-only）>`）：

```powershell
python scripts\audit_reseal_daily_root.py --db <repo>\data\audit\audit_chain.db `
  --roots %TEMP%\auditroot_step3\daily_roots.COPY.jsonl `
  --date 2026-09-25 --chain-from 2026-09-23 --apply
# 预演产出：root=cd62d03834ff16e7… leaf_count=440 seq 72261..72700 prev=65c70ab7…
#          自验：根哈希重算一致=True 签名=True 外层根链=False 叶子=440/440  ⇒ 退出码 3
```

**② 生产实写**（默认 `--db/--roots/--key-path` 即生产路径）：

```powershell
cd C:\Users\Administrator\agent
python scripts\audit_reseal_daily_root.py --date 2026-09-25 --chain-from 2026-09-23 --apply
# 退出码 3（**预期**，原因见下）；日根 13 行 → 14 行；台账零写入
```

**为什么退出码是 3 而不是 0**：工具在写后立即 `verify_daily_root(2026-09-25)`，
该调用同时校验"当日重放 + 签名 + **外层根链**"。当日重放与签名都通过了，
**只有外层根链失败**（断点在第 12 行，与本次追加无关）。工具按设计把"自验未通过"判为退出码 3 ——
这是**如实报错**而不是本次写入失败（不变量核对同时打印：只追加=True、台账零写入=True）。

### 3.5 前后对照

| 项 | 修复前 | 修复后 |
|---|---|---|
| sha256 | `60948294FB73F0C2D28720059F12E94DC014BE8E9B72CD0AA3770F79DE2B42B0` | `E4C1F1B82119B071FBD9D65567B0417FBDBEC0FE81142BED1CD1BE31F6CF59BA` |
| size | 11667 B | 12568 B（**+901 B = 恰好 1 行**） |
| 行数 | 13 | 14 |
| mtime（UTC） | 2026-09-26T00:00:00.889238Z | 2026-09-26T01:35:33.791389Z（仅因本次追加） |
| 属性 | ReadOnly | ReadOnly（写后自动复原） |
| `get_daily_root("2026-09-25")` | `leaf_count=4 / entry_hash=9f892d3e…`（**假根**） | `leaf_count=440 / entry_hash=F1EA611379DAC7E1…` |
| `verify_daily_root("2026-09-25")` | `root_hash_mismatch`（重算 `e3b0c442…`=空集 ≠ 记录） | **当日重算一致 440/440、签名有效**；仅外层根链断（既有断点） |

**只追加证据（独立于工具的自我声明）**：
`open(修复后,"rb").read().startswith(备份字节) == True`，`delta = 901 B`。

**新增的那一条（原文，逐字节）**

```json
{"algorithm":"sha256-merkle-v1","created_at":"2026-09-26T01:35:33.789389+00:00","date":"2026-09-25","degraded":false,"degraded_reason":"","entry_hash":"f1ea611379dac7e19be61e136c2aa6bb1dc7d1410ce833f5b757e48cbe174deb","first_self_hash":"4439556e5229ac85ea548926526224b273ae31075e865e941c46b4a0a58b88f1","first_seq":72261,"last_self_hash":"e5de097ea027b513dba3a808bdc3f7c2b873f871a9757ac23a06581786c65ac9","last_seq":72700,"leaf_count":440,"prev_entry_hash":"65c70ab7b528b5d9d5d2d97616cfd3e1b1a04a1afa164c206d275f40045f0a49","protected":false,"root_hash":"cd62d03834ff16e7866e436104be1d02fc9c43d9868591e23f9a7940ee89e4c8","schema_version":1,"signature":"63cfb3ec256eaaadf81edcbfdde3b5c4c93ebe3057b6c0aea63b99f75f6ed152e156a062d9b4ed95fe45c4dc923fff19774e5e78f92d0cf8472b90df2ee25100","signature_scheme":"ed25519","signer_public_key":"b73dd748ad340a2fa87a4646b6846965bc8fb544e7a8951bb9c22b877d98ae6f"}
```

**独立复算（另一段只读脚本，不依赖写入路径）**

```text
entry_hash 重算一致 = True          # DailyRoot.compute_entry_hash(prev) == entry_hash
签名有效          = True          # RootsSigner.verify(signed_message, sig, ed25519, 记录内公钥)
prev 指向最后一条真实根 = True      # == 第 10 行（2026-09-23）的 entry_hash
entries(day=2026-09-25) = 440  seq 72261 .. 72700
merkle 重算一致    = True          # == root_hash
last_self_hash == 该日末条 self_hash = True
last_self_hash     = e5de097ea027b513… == G2 报告的**全链链头**（交叉印证：封的是真链尾）
09-25 记录数 = 4   seal=1/2/3 = 污染记录；**seal=4 = 本次新增（默认取用生效）**
verify_daily_root(09-25, seal=1) → root_hash_mismatch（历史污染记录仍可取回取证）
```

### 3.6 关于 2026-09-24（如实说明，未硬编）

- **该日既没有日根，也没有任何链上记录**：只读 SQL `substr(ts,1,10)='2026-09-24'` ⇒ **0 条**；
  `daily_roots.jsonl` 里也没有 `date=2026-09-24` 的行。
- 因此"上一条正确根"就是 **2026-09-23**（第 10 行）——**不存在"跳过 09-24 的根"这回事**：
  09-24 不是"有数据却没封根"，而是**该日无数据**（`chain.py:3271-3273` 的设计原则：
  "对链上从来没有过记录的日期补一条空根 = 替历史下一个无凭据的结论"，故也不该为它补空根）。
- 佐证：09-23 那条根的 `last_seq=72260`、`leaf_count=51623`，恰好覆盖 seq 20638..72260；
  09-25 的 440 条是 seq 72261..72700 ⇒ 两段**首尾相接、无缝隙**。

---

## 4. 9 条 FAIL 的逐条归因

### 4.1 机制：位置式根链 + 单点断链的全局传播

```python
# agent/audit/chain.py:3298-3319（未改动）
def _verify_root_chain(self):
    recs = self._read_root_records()          # 文件**全部**行
    prev = GENESIS_PREV_HASH
    for i, rec in enumerate(recs):
        obj = DailyRoot.from_dict(rec)
        if obj.prev_entry_hash != prev:
            return (False, checked, f"每日根 {obj.date} 的 prev_entry_hash 与前一条 entry_hash 不一致")
        ...
        prev = obj.entry_hash
    return (True, checked, "")
```

这是**位置式**遍历：每一行必须接住**紧邻的上一行**，且**遇到第一个断点立即返回**。
文件断点唯一：

| 文件行 | date | entry_hash | prev_entry_hash | 位置式判定 |
|---|---|---|---|---|
| 10 | 2026-09-23 | `65c70ab7…` | `315fa180…`(=第9行) | ✅ |
| 11 | 2026-09-25 | `01f82798…` | `65c70ab7…`(=第10行) | ✅ |
| **12** | 2026-09-25 | `a39fa893…` | `65c70ab7…`（**≠ 第 11 行**） | ❌ **断点** |
| 13 | 2026-09-25 | `9f892d3e…` | `65c70ab7…`（≠ 第 12 行） | （到不了） |
| 14（本次新增） | 2026-09-25 | `f1ea6113…` | `65c70ab7…` | （到不了） |

`verify_daily_root(day)` 对**每一天**都调用同一个 `_verify_root_chain()` ⇒ 返回的
`chain_ok=False` 被**复用给所有日**；于是凡"自身重放 OK"的日，其 `reason` 都落到
`root_chain_broken`（它是 if-链里的最后一个分支，见 `chain.py:3203-3227`）。

### 4.2 逐条归因表

| # | 失败日 | 修复前 reason | 修复后 reason | 归因 | 是否本机制 |
|---|---|---|---|---|---|
| 1 | 2026-09-23（**窗口内**） | `root_chain_broken` | `root_chain_broken` | 该日根自身重放/签名/元数据全 OK，仅被断点带红 | ✅ 同一机制 |
| 2 | 2026-09-25（**窗口内**） | `root_hash_mismatch`（有效根=第 13 行假根，重算空集） | `root_chain_broken`（**当日重算一致 440/440**） | 修复前：该日有效根是假的；修复后：只剩断点 | ✅ 同一机制（内容已修，红因变成断点） |
| 3 | 2026-09-13 | `root_chain_broken` | 同 | 同上 | ✅ 同一机制 |
| 4 | 2026-09-14 | 同 | 同 | 同上 | ✅ 同一机制 |
| 5 | 2026-09-16 | 同 | 同 | 同上 | ✅ 同一机制 |
| 6 | 2026-09-17 | 同 | 同 | 同上 | ✅ 同一机制 |
| 7 | 2026-09-18 | 同 | 同 | 同上 | ✅ 同一机制 |
| 8 | 2026-09-19 | 同 | 同 | 同上 | ✅ 同一机制 |
| 9 | 2026-09-22 | 同 | 同 | 同上 | ✅ 同一机制 |

**分布**：9 条 = 窗口内 2 条（09-23、09-25）+ 篡改类桶 7 条（09-13/14/16/17/18/19/22）；
**全部 9 条同为"第 12 行位置式断链"这一个机制**。

### 4.3 唯一**不是**本机制的：2026-09-21（本来是 WARN，本卡未动）

- `2026-09-21 叶子=245（根记录 235）reason=root_hash_mismatch`：记录 235 叶、现算 245 叶
  （seq 20105..20349）——即**封印之后该日又被回填**的已知历史缺陷（D5/D3 报告已登记）。
- 它在重放窗口外，落在 `bad_history` 桶，数量 1 ≤ `--max-known-bad-roots`(默认 1) ⇒ **WARN**。
- **本卡没有顺手改它**：它需要"是否重封历史日根"的裁定（重封会把 245 叶写成新根，
  等于用今天的数据覆盖当日封印结论），属 D3/D5 的处置范围。前后对比中它的取值一字未变。

### 4.4 为什么"只追加"不能把 FAIL 降到 0（已用副本反证）

| 实验（全部在 `%TEMP%\auditroot_step3\` 的副本上做，生产文件零改动） | 结果 |
|---|---|
| 生产库副本 + **只追加**（14 行：13 行原样 + 第 14 行新根） | **FAIL=9 WARN=2（rc=1）** —— 与修复前**同为 9**，只是 09-25 的 reason 从 `root_hash_mismatch` 变为 `root_chain_broken` |
| 生产库副本 + **摘掉第 11–13 行**（11 行：1..10 + 第 14 行） | **FAIL=0 WARN=2（rc=0）**；G3 逐日：09-13…09-23 全 OK、**09-25 叶子=440（根记录 440）根链=True**、09-21 仍是 WARN |

⇒ **本卡的"追加"修好了数据（有效根恢复为真值），但修不了断点**；
把 FAIL 降到 0 必须处理那 3 条**测试污染记录**（它们不是生产证据），
而"删改既有行"与封印纪律直接冲突 ⇒ 见 §7 R-1（需卡片 owner 明确授权）与 §8（回滚/恢复指令）。

---

## 5. `audit_governance_check.py` 改前/改后原始输出

> 说明：**改前**那一份是用 `--roots %TEMP%\auditroot_step3\daily_roots.BACKUP.jsonl` 复跑的
> （内容与被改动前的生产文件**逐字节相同**：sha256 `60948294…`、13 行；只差输出头里的
> `日根:` 路径一行）。它与本卡开工时对生产文件直接跑的那次输出**逐条一致**
> （`FAIL（FAIL=9 WARN=2）`、9 条 FAIL 逐条同因）。**改后**那一份是对生产文件直接跑的。

### 5.1 改前（`FAIL=9 WARN=2`，rc=1）

```text
每日根 2026-09-14 的封印区间 seq 915..2782 内有 1786 条**其它日**记录（本日叶子 82 条）——这是旧校验口径（只按 seq 区间取叶）会误判 FAIL 的数据形态；成因是 seq 顺序与 ts 顺序不一致（回填/测试污染），不是链损坏
每日根 2026-09-25 的封印区间 seq 1..4 内有 4 条**其它日**记录（本日叶子 0 条）——这是旧校验口径（只按 seq 区间取叶）会误判 FAIL 的数据形态；成因是 seq 顺序与 ts 顺序不一致（回填/测试污染），不是链损坏
仓库根: C:\Users\Administrator\agent
台账: C:\Users\Administrator\agent\data\audit\audit_chain.db
日根: C:\Users\Administrator\AppData\Local\Temp\auditroot_step3\daily_roots.BACKUP.jsonl
覆盖层: C:\Users\Administrator\agent\data\ui_settings.json
本进程 pid=11432（只读巡检：不写任何文件、不写审计链）
[G1] 确认门豁免漂移：覆盖层 C:\Users\Administrator\agent\data\ui_settings.json 的 CP_TOOL_CONFIRM_LEVEL_EXEMPT
     覆盖层读取途径: OverrideStore（agent.settings.overrides）
     覆盖层: 键不存在（A2 清空后的预期状态）
     生效值（开关中心解析）: '' 来源=default
     名单为空 ⇒ 无豁免（G1 PASS）
[G2] 审计链完整性（verify_chain 全链重算）: C:\Users\Administrator\agent\data\audit\audit_chain.db
     条数=72700 seq=1..72700 链头=e5de097ea027b513… 末条时间=2026-09-25T23:47:14.824015+00:00
     重算 72700 条，耗时 2.24s ⇒ ok=True reason=ok 
[G3] 每日 Merkle 根覆盖与重放校验
     有记录的 UTC 日=17 有日根的日=10 日根记录=13
     有记录但无日根（差集 7 天）: 2025-08-10(48), 2025-08-11(58), 2026-09-12(553), 2026-09-15(285), 2026-09-20(361), 2027-10-19(1738), 2027-10-20(58)
     同日多条根（后一条生效）: 2026-09-14, 2026-09-25
     重放窗口（最近 2 个有根日）: 2026-09-23, 2026-09-25
     BAD  2026-09-13 叶子=361（根记录 361） 签名=True 根链=False 0.04s reason=root_chain_broken 每日根 2026-09-25 的 prev_entry_hash 与前一条 entry_hash 不一致
     BAD  2026-09-14 叶子=82（根记录 82） 签名=True 根链=False 0.04s reason=root_chain_broken 每日根 2026-09-25 的 prev_entry_hash 与前一条 entry_hash 不一致
     BAD  2026-09-16 叶子=5253（根记录 5253） 签名=True 根链=False 0.30s reason=root_chain_broken 每日根 2026-09-25 的 prev_entry_hash 与前一条 entry_hash 不一致
     BAD  2026-09-17 叶子=10792（根记录 10792） 签名=True 根链=False 0.57s reason=root_chain_broken 每日根 2026-09-25 的 prev_entry_hash 与前一条 entry_hash 不一致
     BAD  2026-09-18 叶子=323（根记录 323） 签名=True 根链=False 0.04s reason=root_chain_broken 每日根 2026-09-25 的 prev_entry_hash 与前一条 entry_hash 不一致
     BAD  2026-09-19 叶子=192（根记录 192） 签名=True 根链=False 0.03s reason=root_chain_broken 每日根 2026-09-25 的 prev_entry_hash 与前一条 entry_hash 不一致
     BAD  2026-09-21 叶子=245（根记录 235） 签名=True 根链=False 0.03s reason=root_hash_mismatch 重算=8303f3064f954de7… ≠ 记录=de9c5a1e69ed1252…（按 UTC 日取叶 245 条，seq 20105..20349）
     BAD  2026-09-22 叶子=288（根记录 288） 签名=True 根链=False 0.04s reason=root_chain_broken 每日根 2026-09-25 的 prev_entry_hash 与前一条 entry_hash 不一致
     BAD  2026-09-23 叶子=51623（根记录 51623） 签名=True 根链=False 2.68s reason=root_chain_broken 每日根 2026-09-25 的 prev_entry_hash 与前一条 entry_hash 不一致
     BAD  2026-09-25 叶子=0（根记录 4） 签名=True 根链=False 0.01s reason=root_hash_mismatch 重算=e3b0c44298fc1c14… ≠ 记录=eed4cb3fe873069c…（按 UTC 日取叶 0 条，seq 1..4）
[G4] 统计口径卫生（非业务 action）
     模式=(?i)(?:^|[._-])(?:test|tests|probe)(?:[._-]|$)|^x\.y$|global_test
     总记录=72700 不同 action=125 非业务记录=64 占比=0.0880%（阈值 0.1000%）
     · global_test_action               50 条（链只追加：这些记录**无法删除**）
     · x.y                              13 条（链只追加：这些记录**无法删除**）
     · ssrf_probe_test                  1 条（链只追加：这些记录**无法删除**）

[只读自检] 运行前后文件状态（size/mtime_ns）：
     C:\Users\Administrator\agent\data\audit\audit_chain.db                 55173120/1790380034830013900 → 55173120/1790380034830013900  未变化
     C:\Users\Administrator\AppData\Local\Temp\auditroot_step3\daily_roots.BACKUP.jsonl 11667/1790380800889238300 → 11667/1790380800889238300  未变化
     C:\Users\Administrator\agent\data\ui_settings.json                     405/1790335584116642000 → 405/1790335584116642000  未变化

=== 结论 ===
  G1   PASS 豁免名单为空/缺失
  G2   PASS 全链 72700 条重算一致
  G3   FAIL 日根 10 天（通过 0），缺根 7 天，窗口失败 2，历史失败 8
  G4   PASS 非业务=64(0.0880%)
WARN: G3 日根 2026-09-21 重放失败（reason=root_hash_mismatch，叶子 245 ≠ 根记录 235）：重算=8303f3064f954de7… ≠ 记录=de9c5a1e69ed1252…（按 UTC 日取叶 245 条，seq 20105..20349） —— 属**已知历史缺陷**（封印后又被回填；重算日根风险高，超出本卡范围，处置见 D3 报告）；窗口外失败日 1/1 未越界
WARN: G3 有记录的日缺 Merkle 日根 7 天（已知历史事实，不阻塞）：2025-08-10(48), 2025-08-11(58), 2026-09-12(553), 2026-09-15(285), 2026-09-20(361), 2027-10-19(1738), 2027-10-20(58)
FAIL: G3 最近有根日 2026-09-23 重放失败（reason=root_chain_broken）：每日根 2026-09-25 的 prev_entry_hash 与前一条 entry_hash 不一致 —— 封印路径正在产出错误日根
FAIL: G3 最近有根日 2026-09-25 重放失败（reason=root_hash_mismatch）：重算=e3b0c44298fc1c14… ≠ 记录=eed4cb3fe873069c…（按 UTC 日取叶 0 条，seq 1..4） —— 封印路径正在产出错误日根
FAIL: G3 日根 2026-09-13 的失败原因属**篡改类**（reason=root_chain_broken）：每日根 2026-09-25 的 prev_entry_hash 与前一条 entry_hash 不一致 —— 与「封印后被回填」的历史数据形态不同，需立即排查
FAIL: G3 日根 2026-09-14 的失败原因属**篡改类**（reason=root_chain_broken）：每日根 2026-09-25 的 prev_entry_hash 与前一条 entry_hash 不一致 —— 与「封印后被回填」的历史数据形态不同，需立即排查
FAIL: G3 日根 2026-09-16 的失败原因属**篡改类**（reason=root_chain_broken）：每日根 2026-09-25 的 prev_entry_hash 与前一条 entry_hash 不一致 —— 与「封印后被回填」的历史数据形态不同，需立即排查
FAIL: G3 日根 2026-09-17 的失败原因属**篡改类**（reason=root_chain_broken）：每日根 2026-09-25 的 prev_entry_hash 与前一条 entry_hash 不一致 —— 与「封印后被回填」的历史数据形态不同，需立即排查
FAIL: G3 日根 2026-09-18 的失败原因属**篡改类**（reason=root_chain_broken）：每日根 2026-09-25 的 prev_entry_hash 与前一条 entry_hash 不一致 —— 与「封印后被回填」的历史数据形态不同，需立即排查
FAIL: G3 日根 2026-09-19 的失败原因属**篡改类**（reason=root_chain_broken）：每日根 2026-09-25 的 prev_entry_hash 与前一条 entry_hash 不一致 —— 与「封印后被回填」的历史数据形态不同，需立即排查
FAIL: G3 日根 2026-09-22 的失败原因属**篡改类**（reason=root_chain_broken）：每日根 2026-09-25 的 prev_entry_hash 与前一条 entry_hash 不一致 —— 与「封印后被回填」的历史数据形态不同，需立即排查
FAIL（FAIL=9 WARN=2）
```

### 5.2 改后（`FAIL=9 WARN=2`，rc=1 —— FAIL 数未变，见 §4）

```text
每日根 2026-09-14 的封印区间 seq 915..2782 内有 1786 条**其它日**记录（本日叶子 82 条）——这是旧校验口径（只按 seq 区间取叶）会误判 FAIL 的数据形态；成因是 seq 顺序与 ts 顺序不一致（回填/测试污染），不是链损坏
仓库根: C:\Users\Administrator\agent
台账: C:\Users\Administrator\agent\data\audit\audit_chain.db
日根: C:\Users\Administrator\agent\data\audit\daily_roots.jsonl
覆盖层: C:\Users\Administrator\agent\data\ui_settings.json
本进程 pid=12684（只读巡检：不写任何文件、不写审计链）
[G1] 确认门豁免漂移：覆盖层 C:\Users\Administrator\agent\data\ui_settings.json 的 CP_TOOL_CONFIRM_LEVEL_EXEMPT
     覆盖层读取途径: OverrideStore（agent.settings.overrides）
     覆盖层: 键不存在（A2 清空后的预期状态）
     生效值（开关中心解析）: '' 来源=default
     名单为空 ⇒ 无豁免（G1 PASS）
[G2] 审计链完整性（verify_chain 全链重算）: C:\Users\Administrator\agent\data\audit\audit_chain.db
     条数=72700 seq=1..72700 链头=e5de097ea027b513… 末条时间=2026-09-25T23:47:14.824015+00:00
     重算 72700 条，耗时 2.12s ⇒ ok=True reason=ok 
[G3] 每日 Merkle 根覆盖与重放校验
     有记录的 UTC 日=17 有日根的日=10 日根记录=14
     有记录但无日根（差集 7 天）: 2025-08-10(48), 2025-08-11(58), 2026-09-12(553), 2026-09-15(285), 2026-09-20(361), 2027-10-19(1738), 2027-10-20(58)
     同日多条根（后一条生效）: 2026-09-14, 2026-09-25
     重放窗口（最近 2 个有根日）: 2026-09-23, 2026-09-25
     BAD  2026-09-13 叶子=361（根记录 361） 签名=True 根链=False 0.03s reason=root_chain_broken 每日根 2026-09-25 的 prev_entry_hash 与前一条 entry_hash 不一致
     BAD  2026-09-14 叶子=82（根记录 82） 签名=True 根链=False 0.04s reason=root_chain_broken 每日根 2026-09-25 的 prev_entry_hash 与前一条 entry_hash 不一致
     BAD  2026-09-16 叶子=5253（根记录 5253） 签名=True 根链=False 0.25s reason=root_chain_broken 每日根 2026-09-25 的 prev_entry_hash 与前一条 entry_hash 不一致
     BAD  2026-09-17 叶子=10792（根记录 10792） 签名=True 根链=False 0.58s reason=root_chain_broken 每日根 2026-09-25 的 prev_entry_hash 与前一条 entry_hash 不一致
     BAD  2026-09-18 叶子=323（根记录 323） 签名=True 根链=False 0.04s reason=root_chain_broken 每日根 2026-09-25 的 prev_entry_hash 与前一条 entry_hash 不一致
     BAD  2026-09-19 叶子=192（根记录 192） 签名=True 根链=False 0.03s reason=root_chain_broken 每日根 2026-09-25 的 prev_entry_hash 与前一条 entry_hash 不一致
     BAD  2026-09-21 叶子=245（根记录 235） 签名=True 根链=False 0.03s reason=root_hash_mismatch 重算=8303f3064f954de7… ≠ 记录=de9c5a1e69ed1252…（按 UTC 日取叶 245 条，seq 20105..20349）
     BAD  2026-09-22 叶子=288（根记录 288） 签名=True 根链=False 0.03s reason=root_chain_broken 每日根 2026-09-25 的 prev_entry_hash 与前一条 entry_hash 不一致
     BAD  2026-09-23 叶子=51623（根记录 51623） 签名=True 根链=False 2.51s reason=root_chain_broken 每日根 2026-09-25 的 prev_entry_hash 与前一条 entry_hash 不一致
     BAD  2026-09-25 叶子=440（根记录 440） 签名=True 根链=False 0.07s reason=root_chain_broken 每日根 2026-09-25 的 prev_entry_hash 与前一条 entry_hash 不一致
[G4] 统计口径卫生（非业务 action）
     模式=(?i)(?:^|[._-])(?:test|tests|probe)(?:[._-]|$)|^x\.y$|global_test
     总记录=72700 不同 action=125 非业务记录=64 占比=0.0880%（阈值 0.1000%）
     · global_test_action               50 条（链只追加：这些记录**无法删除**）
     · x.y                              13 条（链只追加：这些记录**无法删除**）
     · ssrf_probe_test                  1 条（链只追加：这些记录**无法删除**）

[只读自检] 运行前后文件状态（size/mtime_ns）：
     C:\Users\Administrator\agent\data\audit\audit_chain.db                 55173120/1790380034830013900 → 55173120/1790380034830013900  未变化
     C:\Users\Administrator\agent\data\audit\daily_roots.jsonl              12568/1790386533791389500 → 12568/1790386533791389500  未变化
     C:\Users\Administrator\agent\data\ui_settings.json                     405/1790335584116642000 → 405/1790335584116642000  未变化

=== 结论 ===
  G1   PASS 豁免名单为空/缺失
  G2   PASS 全链 72700 条重算一致
  G3   FAIL 日根 10 天（通过 0），缺根 7 天，窗口失败 2，历史失败 8
  G4   PASS 非业务=64(0.0880%)
WARN: G3 日根 2026-09-21 重放失败（reason=root_hash_mismatch，叶子 245 ≠ 根记录 235）：重算=8303f3064f954de7… ≠ 记录=de9c5a1e69ed1252…（按 UTC 日取叶 245 条，seq 20105..20349） —— 属**已知历史缺陷**（封印后又被回填；重算日根风险高，超出本卡范围，处置见 D3 报告）；窗口外失败日 1/1 未越界
WARN: G3 有记录的日缺 Merkle 日根 7 天（已知历史事实，不阻塞）：2025-08-10(48), 2025-08-11(58), 2026-09-12(553), 2026-09-15(285), 2026-09-20(361), 2027-10-19(1738), 2027-10-20(58)
FAIL: G3 最近有根日 2026-09-23 重放失败（reason=root_chain_broken）：每日根 2026-09-25 的 prev_entry_hash 与前一条 entry_hash 不一致 —— 封印路径正在产出错误日根
FAIL: G3 最近有根日 2026-09-25 重放失败（reason=root_chain_broken）：每日根 2026-09-25 的 prev_entry_hash 与前一条 entry_hash 不一致 —— 封印路径正在产出错误日根
FAIL: G3 日根 2026-09-13 的失败原因属**篡改类**（reason=root_chain_broken）：每日根 2026-09-25 的 prev_entry_hash 与前一条 entry_hash 不一致 —— 与「封印后被回填」的历史数据形态不同，需立即排查
FAIL: G3 日根 2026-09-14 的失败原因属**篡改类**（reason=root_chain_broken）：每日根 2026-09-25 的 prev_entry_hash 与前一条 entry_hash 不一致 —— 与「封印后被回填」的历史数据形态不同，需立即排查
FAIL: G3 日根 2026-09-16 的失败原因属**篡改类**（reason=root_chain_broken）：每日根 2026-09-25 的 prev_entry_hash 与前一条 entry_hash 不一致 —— 与「封印后被回填」的历史数据形态不同，需立即排查
FAIL: G3 日根 2026-09-17 的失败原因属**篡改类**（reason=root_chain_broken）：每日根 2026-09-25 的 prev_entry_hash 与前一条 entry_hash 不一致 —— 与「封印后被回填」的历史数据形态不同，需立即排查
FAIL: G3 日根 2026-09-18 的失败原因属**篡改类**（reason=root_chain_broken）：每日根 2026-09-25 的 prev_entry_hash 与前一条 entry_hash 不一致 —— 与「封印后被回填」的历史数据形态不同，需立即排查
FAIL: G3 日根 2026-09-19 的失败原因属**篡改类**（reason=root_chain_broken）：每日根 2026-09-25 的 prev_entry_hash 与前一条 entry_hash 不一致 —— 与「封印后被回填」的历史数据形态不同，需立即排查
FAIL: G3 日根 2026-09-22 的失败原因属**篡改类**（reason=root_chain_broken）：每日根 2026-09-25 的 prev_entry_hash 与前一条 entry_hash 不一致 —— 与「封印后被回填」的历史数据形态不同，需立即排查
FAIL（FAIL=9 WARN=2）
```

---

## 6. 链本体完整性复核（只读）

| 检查 | 命令/口径 | 结果 |
|---|---|---|
| 全链重算 | `audit_governance_check.py` G2（`verify_chain` 全链重算） | `条数=72700 seq=1..72700 链头=e5de097ea027b513… ok=True reason=ok`（修复前后相同） |
| 行数 / seq 区间 / 唯一性 | `SELECT COUNT(*), MIN(seq), MAX(seq), COUNT(DISTINCT seq) FROM audit_chain`（`mode=ro`） | `72700 / 1 / 72700 / 72700` |
| **seq 缺口** | 窗口函数 `LAG(seq)` 逐行比对 `seq = prev+1` | **缺口段数 = 0** |
| 重复 seq | `COUNT(*) - COUNT(DISTINCT seq)` | **0** |
| 该日条数 | `substr(ts,1,10)='2026-09-25'` | **440**（与根记录 `leaf_count=440` 一致） |
| 09-24 | 同上 | **0 条**（该日无记录，故无根可言） |
| 台账未被本卡写入 | 写前/写后文件 sha256 | `2EE6EF46841BF235…` → `2EE6EF46841BF235…`（不变）；工具亦打印"台账 无新增/无删除=True" |

**结论**：链本体（`audit_chain.db`）**自始至终是好的**——G2 全链 72700 条重算一致、
seq 无缺口无重复、本卡的操作（含测试运行）**没有使其丢行或改变 sha256**。

---

## 7. 未验证项与残留风险

| # | 风险 / 未验证项 | 触发信号 | 归属与建议 |
|---|---|---|---|
| **R-1** | **G3 仍 FAIL=9（本卡未闭合）**。9 条全部派生自日根文件第 12 行的**位置式断链**；"只追加"永远修不到断点之前。本卡已用副本证明：摘掉第 11–13 行（测试污染记录）+ 保留本卡新增的第 14 行 ⇒ **FAIL=0 WARN=2 rc=0** | `python scripts\audit_governance_check.py` ⇒ `FAIL（FAIL=9 WARN=2）` | **需要卡片 owner 明确裁定**：要么授权"隔离 3 条测试污染记录"（与"不删改既有行"纪律冲突，须走特批 + 逐字节备份 + 书面理由），要么把根链校验语义改为"按日有效根"（等于弱化"根文件被删改"的检出，属设计变更，**不在本卡权限内**）。**本卡刻意两者都不做。** 证据见 §4.4 |
| **R-2** | `tests/conftest.py:287` 用 `os.environ.setdefault` ⇒ **外部继承**的 `AUDIT_ROOTS_PATH` 会压过会话隔离 | 新增守卫用例 `test_audit_roots_env_is_session_isolated` 变红 | 建议改为硬赋值 + 退出还原（未做：会与其它卡的测试运行耦合）。缓解：本卡已加只读守卫用例 |
| **R-3** | 范围外 **5 处**构造点仍未显式传 `roots_path=`（§2.2 A 类） | 跨 UTC 零点跑测试时仍可能出现"测试链封日根" | 当前**已被修好的 env 语义兜住**（conftest 会话级隔离）；建议按 §2.2 表补 `roots_path=` 与 `signing_key_path=`。**附带一项本卡未处理的事实**：这些测试链此前是用**生产私钥**给日根签名的（污染记录里的 `signer_public_key=b73dd748…` 即生产身份）——补 `signing_key_path=` 可一并消除 |
| **R-4** | 未跑**全量** `tests/unit` | — | 本卡只跑与审计面相关的 **15 个文件：429 passed / 1 skipped / 0 failed**（含另一张卡的 `test_daily_root_reseal.py` 19 条）。全量套件在 G1C 报告里被记录为 >50 分钟、RSS 5.1 GB，与在跑的两张卡抢资源，**刻意为不跑** |
| **R-5** | 其余 **7 天缺根**（`2025-08-10/11`、`2026-09-12/15/20`、`2027-10-19/20`）仍是 WARN | G3 差集 7 天 | **已知历史事实**，本卡未动。注意 `2027-10-19/20` 是**未来日期**的合成数据（D5 工具默认跳过，需 `--include-future` 才处理） |
| **R-6** | 2026-09-21 历史缺陷（root_hash_mismatch，235 vs 245 叶）仍在 WARN | G3 WARN #1 | 属 D3/D5 处置范围，本卡**未动**（改动它会用今天的数据覆盖当日封印结论） |
| **R-7** | 本卡修改了**另一张卡的交付物** `scripts/audit_reseal_daily_root.py` | — | 改动是**纯增量**（新选项默认 `tail`，原路径逐字不变），且该卡的 `test_daily_root_reseal.py` 19 条全绿；若 D5 owner 不接受，可按 §8(b) 摘除 |
| **R-8** | `chain.py` 那一行的语义在多环境下的影响面**未在本卡验证** | 若某部署**显式设置** `AUDIT_ROOTS_PATH` 指向别处 | 那是显式配置 ⇒ 现在会生效（修复前被忽略）。生产环境**不设**该 env ⇒ 回落 `DEFAULT_ROOTS_PATH`，实测（场景 C）行为逐字不变 |
| **R-9** | 未做 UI 端到端、未启动常驻服务、未联网 | — | 卡片硬约束（禁止启动服务、零出网）；所有证据均为进程内/CLI 级 |
| **R-10** | G1C.md 的 R-1 归因（"有人手动跑了重封脚本"）**与实测不符** | — | 该文件把 UTC 显示成本地时间（08:00 = 00:00 UTC）而误判；实测为测试进程 `auto_seal`。**需 G1-C owner 更正**（本卡只在 §1.4 记录更正，不改那份文档） |

---

## 8. 回滚指令

> 所有路径均为绝对路径。**先做 ①**（撤销生产数据改动）；②③ 只影响本卡新增的代码/用例，可独立于 ①。

### ① 撤销本卡追加的那一条日根（生产数据回到 13 行 / 11667 B）

`data/audit/` 在 `.gitignore:49` 里 ⇒ **git 无法还原它**，必须按字节截断
（旧 13 行是新文件的**严格前缀**，故截断 = 逐字节还原）。

```powershell
$p = 'C:\Users\Administrator\agent\data\audit\daily_roots.jsonl'

# 1) 解除只读（生产文件由 _protect_readonly 置为只读）
attrib -R $p

# 2) 截断回 11667 字节（= 追加前的长度）
$fs = [System.IO.File]::Open($p, 'Open', 'Write'); $fs.SetLength(11667); $fs.Close()
#    若 pwsh 受限（无 .NET 静态调用权限），用 Python 等价物：
#    python -c "p=r'C:\Users\Administrator\agent\data\audit\daily_roots.jsonl'; b=open(p,'rb').read(); open(p,'wb').write(b[:11667])"

# 3) 复原只读保护并核验
attrib +R $p
(Get-FileHash $p -Algorithm SHA256).Hash          # 期望 60948294FB73F0C2D28720059F12E94DC014BE8E9B72CD0AA3770F79DE2B42B0
(Get-Item $p).Length                              # 期望 11667
(Get-Content $p | Measure-Object -Line).Lines     # 期望 13
```

**逐字节备份**（若上面的截断出任何意外，直接整文件还原）：
`Copy-Item -Force %TEMP%\auditroot_step3\daily_roots.BACKUP.jsonl <上面的 $p>`（sha256 `60948294…`）。

> 撤销后 `audit_governance_check.py` 回到 `FAIL=9 WARN=2`（09-25 的 reason 变回 `root_hash_mismatch`），
> 即 §5.1 的状态。

### ② 撤销 `scripts/audit_reseal_daily_root.py` 的改动（可选；不改也无副作用）

该文件是 **untracked**（`git status` 显示 `?? scripts/audit_reseal_daily_root.py`），
故不能 `git checkout` 还原；改动共 5 处，全部是**新增**，逐处删除即可回到原样：

1. `import re`（在 `import pathlib` 之后）；
2. 模块 docstring 里的 `【--chain-from：…】` 段；
3. `_HEX64_RE` + `_resolve_chain_from()`（在 `_read_bytes()` 之前）；
4. `--chain-from` 的 `p.add_argument(...)`（在 `--db` 之前）；
5. `main()` 内 4 小段：`prev_override = _resolve_chain_from(...)` 的 try/except、
   `chain-from` 的打印、`todo` 之后的单日守卫与实例遮蔽、自验明细那两段打印。

（默认值 `--chain-from tail` ⇒ 只要不显式传该选项，行为与改动前**逐字相同**，
故"忘记回滚"不会造成任何后果。）

### ③ 撤销 `tests/unit/test_audit_production_isolation.py` 的新增

该文件的改动是**追加**：删掉 `# AUDIT-ROOT-REPAIR（2026-09-26）…` 标题块之后的
3 个函数（`test_audit_roots_env_is_session_isolated`、`test_ctor_honours_env_roots_path`、
`test_auto_seal_of_test_chain_never_touches_production_roots`）与文件末尾的
`_prod_roots()/_stat()` 两个小助手，以及模块 docstring 里
`【AUDIT-ROOT-REPAIR（2026-09-26）追加的三条 …】` 那一段即可。
（该文件**在 git 里被跟踪**（` M`），但本卡禁止整文件 `git checkout`；如确需用 git，
只能对该单文件做**定向** `git checkout -- tests/unit/test_audit_production_isolation.py`，
这会同时丢掉上面 2 条**原有**用例之外的一切 —— 不建议。）

---

## 9. 残留物自证

### 9.1 仓库内：本卡写入的**全部**路径（4 个）

| 路径 | 动作 | 说明 |
|---|---|---|
| `data/audit/daily_roots.jsonl` | **追加 1 行**（13→14） | 旧 13 行逐字节未动（前缀断言 True） |
| `scripts/audit_reseal_daily_root.py` | 修改（纯增量） | 新增 `--chain-from`，默认 `tail` |
| `tests/unit/test_audit_production_isolation.py` | 修改（纯追加 3 用例） | 隔离回归加锁 |
| `docs/audit_skill_governance/AUDITROOT.md` | 新建 | 本文件 |

**没有**新建任何其它仓库内文件；**没有**写入 `agent/skills_mgmt/`、
`agent/tool_router_hybrid.py`、`plugins/`、`agent/workflow_learning/`、`yunshu-ui/`、`config.yaml`
（这些路径在 `git status` 里确有 ` M`；本卡对其**零写入**——本次执行期间实测到
`agent/tool_router_hybrid.py`、`agent/workflow_learning/*`、`agent/settings/registry.py` 等
**正在被其它进程改写**，与"工作区有 33 张卡在跑"一致）。`agent/audit/chain.py` **未改动**
（主审计的修复行 `1165-1166` 原样保留，已复核）。

**`git status --porcelain` 行数：开工前 126 → 收尾 127（+1）**。这 +1 只可能来自上表第 3 行：

- `data/audit/daily_roots.jsonl` 被 `.gitignore:49`（`data/audit/`）忽略 ⇒ 永不产生 porcelain 行；
- `docs/audit_skill_governance/` 与 `scripts/audit_reseal_daily_root.py` 在本卡开工前**已是**
  untracked（`??` 各一行，目录内的新文件不再增加行数）；
- `tests/unit/test_audit_production_isolation.py` 是**被跟踪**的文件，本卡的追加改动使它从
  干净变为 ` M` ⇒ **+1**。

### 9.2 仓库外：探针与证据（`%TEMP%\…`，均在仓库外）

| 路径 | 用途 |
|---|---|
| `%TEMP%\auditroot_probe_step1.py` | Step1 三场景差分探针（脚本） |
| `%TEMP%\auditroot_step1\step1_report.json` + 三个场景目录 | Step1 结构化报告与夹具/产物 |
| `%TEMP%\auditroot_step3\daily_roots.BACKUP.jsonl` | **生产文件逐字节备份**（回滚用，见 §8①） |
| `%TEMP%\auditroot_step3\{daily_roots.COPY.jsonl, daily_roots.FIXED_no_pollution.jsonl, audit_chain.COPY.db}` | 副本预演与 FAIL=0 反证夹具 |
| `%TEMP%\auditroot_step3\{prod_apply.txt, prod_check_before_clean.txt, prod_check_after.txt, copy_apply.txt, fixed_check.txt, copy14_check.txt, verify_repair.py, verify_repair.txt, pytest_after.txt, pytest_run2.txt}` | 全部原始输出与复算脚本 |
| `%TEMP%\auditroot_{scan_callsites,callsites2}.py` + `*.txt`、`auditroot_reseal_dryrun.txt`、`auditroot_before_gov.txt`、`inspect_line.py` | 构造点扫描、dry-run 与首次基线输出 |

### 9.3 行为自证

- **未** `git add` / `git commit`（全程只读 git 查询）；
- **未** 整文件 `git checkout`；**未** `taskkill` 任何 python 进程；**未** 启动常驻服务；
- 所有探针/夹具/输出**全部写在仓库外**的绝对路径（`C:\Users\Administrator\AppData\Local\Temp\…`）；
- 生产文件的 mtime 只在 `2026-09-26T01:35:33Z` 那一次追加时变化；此后两次单测运行
  （48.67 s / 47.79 s）前后 `sha256= E4C1F1B8…`、`size=12568`、`mtime_ns` **完全相同**；
- `audit_chain.db` 的 sha256 在全部操作前后保持 `2EE6EF46841BF235…`。


