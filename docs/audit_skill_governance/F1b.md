# F1b · 修复 update_meta 的静默数据丢失（高优先，阻塞 G1）

> 实施时间：2026-09-25 19:1x–19:3x｜实施者：F1b 卡
> 依据：`docs/audit_skill_governance/FINDINGS_DURING_IMPL.md` · F1（必读节）与主报告 F1b 行
> 状态：**已完成，未提交**（保持可审查状态；未 commit / 未 push / 未建分支）

---

## 0. 结论摘要

| 项 | 结果 |
|---|---|
| 修法 | **(a) 最小侵入序列化** —— 只重写被 patch 的顶层键所在行，其余逐字节保留 |
| 改动文件 | `agent/skills_mgmt/file_store.py`（+190 / −8）、新增 `tests/unit/test_update_meta_no_data_loss.py`（28 用例） |
| **`agent/skills_mgmt/models.py` 未改动** | `SkillMDParser` 实测定义在 `file_store.py:92`，**不在 models.py**（grep 见 §1） |
| 验收 1 | `python -m pytest tests/unit/test_update_meta_no_data_loss.py -q` → **28 passed** |
| 验收 2 | `python -m pytest tests/unit/test_skill_registry.py tests/unit/test_skill_registry_audit.py -q` → **19 passed** |
| 验收 3 | 沙箱复现前后对照：**5 FAIL → 0 FAIL**，diff 从「删 4 行 + 展开列表 + 丢末尾换行」变成**只改 1 行** |
| 验收 4 | `git status --porcelain -- data/` → **空** |
| 附加硬证据 | 生产 23 个 skill.md 的**只读副本**：一次启停**恒为 1 行差异**（23/23）、第 2 次写入起**字节完全稳定**（23/23） |
| 关键不变量 | **G1 可以开工**：启停不再产生格式噪声，也不再抹掉人工内容 |

**核心修复效果一句话**：`update_meta` 从「parse → serialize 整文件重排」改为「只改被 patch 的那一行」；
白名单的语义被拆成【允许改什么】(约束 patch) 与【保留什么】(以文件现状为准) 两件事。

---

## 1. 定位确认

### 1.1 SkillMDParser 在哪（任务卡要求先确认）

```
$ grep -rn "class SkillMDParser" agent/
agent\skills_mgmt\file_store.py:92:class SkillMDParser:
```

⇒ **定义在 agent/skills_mgmt/file_store.py，不在 models.py**，因此本卡**没有改动 models.py**
（任务卡允许改它，但无需改；不必要的越界改动一律不做）。

### 1.2 修复前的实现（原文，652–669 行）

```python
def update_meta(self, skill_id: str, patch: Dict[str, Any],
                new_instruction: Optional[str] = None) -> None:
    """更新技能元数据和使用说明"""
    with self._lock:
        meta, body = self._read_md(skill_id)
        meta.update({k: v for k, v in patch.items() if k in _META_FIELDS})   # 657 白名单过滤
        if new_instruction is not None:
            body = new_instruction
        md_content = SkillMDParser.serialize(meta, body)                      # 660 整文件重排
        skill_dir = self._skill_dir(skill_id)
        (skill_dir / _SKILL_MD).write_text(md_content, encoding="utf-8")      # 662 整文件覆写
        self._meta_index = None
```

两处根因：
1. `SkillMDParser.parse`（:143）与 `serialize`（:156）**各自都做了一次白名单裁剪**，
   于是 `_META_FIELDS` 同时扮演了「允许改什么」与「保留什么」⇒ 白名单外字段被静默删除；
2. `serialize` 用统一 yaml 风格重新输出 front matter + body ⇒ 注释、引号风格、行内列表、末尾换行全部丢失。

### 1.3 调用方（grep update_meta，全仓）

| 调用点 | 传参 | 本卡影响 |
|---|---|---|
| `agent/skills_mgmt/registry.py:189`（`_set_enabled` 文件轨分支） | `{"enabled": enabled}` | 签名/返回未变；已加端到端用例 |
| `agent/process_distill/solidify.py:316` | `{"status": final_status}` | 签名/返回未变；已加用例 |
| `tests/unit/test_skill_index_cache.py:283` | `{"name": ...}` | 既有回归已跑通 |

**跨卡读取声明**：registry.py / solidify.py 属其它卡的文件，读取时可能处于并发中间态。
我读到的调用点分别是 `registry.py:189`、`solidify.py:316`（19:1x 首读、19:3x 复核一致）；
本卡结论**不依赖这两个文件的内部实现**——端到端验证走的是公开 API（`SkillRegistry.set_enabled` / `toggle`）。

---

## 2. 复现（修复前）—— 仓库外临时目录

复现脚本：`%TEMP%\f1b_sandbox\repro_update_meta.py`（仓库外，未写入 data/skills_repo）
最小复现配方（注入的 skill.md 内容，逐字来自脚本）：

```markdown
---
id: demo-skill
name: Demo Skill
description: "a quoted description"
tags: [a, b, c]
# a hand-written comment
unknown_custom_field: KEEP_ME
enabled: true
---

# Demo

Body content.
```

```python
store = SkillFileStore(repo_path=str(tmp / "skills_repo"))   # tmp 在仓库外
store.update_meta("demo-skill", {"enabled": False})          # 等价于一次技能启停
```

### 2.1 修复前原始输出（repro_update_meta.py BEFORE-FIX）

> 说明：该次运行的 CJK 标签被控制台代码页（GBK）打乱（本机 pwsh 未设 `PYTHONIOENCODING`），
> **ASCII 证据不受影响**（before=True after=False、[FAIL]、字节数、diff、repr 均完整可读）；
> 修复后同脚本重跑已设 `PYTHONIOENCODING=utf-8`，输出见 §3。

```text
F1b sandbox repro [BEFORE-FIX]
sandbox tmp      = C:\Windows\TEMP\f1b_sandbox_78x6av4c
tmp 在仓库内?     = False
sandbox repo     = C:\Windows\TEMP\f1b_sandbox_78x6av4c\skills_repo

--- 1) 未知字段 unknown_custom_field: KEEP_ME ---
  before=True  after=False
  [FAIL] 未知字段保留

--- 2) YAML 注释 '# a hand-written comment' ---
  before=True  after=False
  [FAIL] 注释保留

--- 3) 格式: 引号 / 行内列表 / 末尾换行 ---
  description 引号保留: before=True  after=False
  [FAIL] description 引号保留
  tags 行内列表保留:    before=True  after=False
  front matter 行数:    7 -> 8
  [FAIL] tags 行内列表未被展开成块状
  末尾换行:             before=True  after=False
  [FAIL] 末尾换行保留
  字节数:               184 -> 129

--- 4) enabled 语义 ---
  after 含 'enabled: false': True
  [PASS] enabled 已改为 false

--- 5) 已解析白名单字段 round-trip ---
  keys before = ['description', 'enabled', 'id', 'name', 'tags']
  keys after  = ['description', 'enabled', 'id', 'name', 'tags']
  [PASS] 已解析字段 keys 无增无减
  值变化的字段 = {'enabled': (True, False)}
  [PASS] 值的语义变化仅限 enabled
  [PASS] body 内容保留

--- 6) 逐行 diff (before -> after) ---
  --- before
  +++ after
  @@ -1,13 +1,14 @@
   ---
   id: demo-skill
   name: Demo Skill
  -description: "a quoted description"
  -tags: [a, b, c]
  -# a hand-written comment
  -unknown_custom_field: KEEP_ME
  -enabled: true
  +description: a quoted description
  +tags:
  +- a
  +- b
  +- c
  +enabled: false
   ---
   
   # Demo
   
  -Body content.
  +Body content.

--- 7) 原始 repr ---
  BEFORE = '---\nid: demo-skill\nname: Demo Skill\ndescription: "a quoted description"\ntags: [a, b, c]\n# a hand-written comment\nunknown_custom_field: KEEP_ME\nenabled: true\n---\n\n# Demo\n\nBody content.\n'
  AFTER  = '---\nid: demo-skill\nname: Demo Skill\ndescription: a quoted description\ntags:\n- a\n- b\n- c\nenabled: false\n---\n\n# Demo\n\nBody content.'

失败检查数 = 5 ['未知字段保留', '注释保留', 'description 引号保留', 'tags 行内列表未被展开成块状', '末尾换行保留']
```

**这 5 条 FAIL 逐条对应任务卡 F1b 的指控**，全部复现成功（不是推测）：

| 指控 | 复现结果 |
|---|---|
| 未知字段被静默删除 | ✅ `unknown_custom_field: KEEP_ME` 消失 |
| YAML 注释被静默删除 | ✅ `# a hand-written comment` 消失 |
| description 的引号被剥离 | ✅ |
| `tags: [a, b, c]` 被展开成块状 | ✅ front matter 行数 7 → 8（4 行列表替换了 1 行） |
| 末尾换行被删除 | ✅ `Body content.` 后无换行符 |
| 已解析字段与值不丢 | ✅ 3 条 PASS（key 无增无减、值与 body 保留） |

---

## 3. 修复后 —— 同一脚本重跑（对照）

```text
F1b sandbox repro [AFTER-FIX-FINAL]
sandbox tmp      = C:\Windows\TEMP\f1b_sandbox_x970j79y
tmp 在仓库内?     = False
sandbox repo     = C:\Windows\TEMP\f1b_sandbox_x970j79y\skills_repo

--- 1) 未知字段 unknown_custom_field: KEEP_ME ---
  before=True  after=True 
  [PASS] 未知字段保留

--- 2) YAML 注释 '# a hand-written comment' ---
  before=True  after=True 
  [PASS] 注释保留

--- 3) 格式: 引号 / 行内列表 / 末尾换行 ---
  description 引号保留: before=True  after=True 
  [PASS] description 引号保留
  tags 行内列表保留:    before=True  after=True 
  front matter 行数:    7 -> 7
  [PASS] tags 行内列表未被展开成块状
  末尾换行:             before=True  after=True 
  [PASS] 末尾换行保留
  字节数:               184 -> 185

--- 4) enabled 语义 ---
  after 含 'enabled: false': True
  [PASS] enabled 已改为 false

--- 5) 已解析白名单字段 round-trip ---
  keys before = ['description', 'enabled', 'id', 'name', 'tags']
  keys after  = ['description', 'enabled', 'id', 'name', 'tags']
  [PASS] 已解析字段 keys 无增无减
  值变化的字段 = {'enabled': (True, False)}
  [PASS] 值的语义变化仅限 enabled
  [PASS] body 内容保留

--- 6) 逐行 diff (before -> after) ---
  --- before
  +++ after
  @@ -5,7 +5,7 @@
   tags: [a, b, c]
   # a hand-written comment
   unknown_custom_field: KEEP_ME
  -enabled: true
  +enabled: false
   ---
   
   # Demo

--- 7) 原始 repr ---
  BEFORE = '---\nid: demo-skill\nname: Demo Skill\ndescription: "a quoted description"\ntags: [a, b, c]\n# a hand-written comment\nunknown_custom_field: KEEP_ME\nenabled: true\n---\n\n# Demo\n\nBody content.\n'
  AFTER  = '---\nid: demo-skill\nname: Demo Skill\ndescription: "a quoted description"\ntags: [a, b, c]\n# a hand-written comment\nunknown_custom_field: KEEP_ME\nenabled: false\n---\n\n# Demo\n\nBody content.\n'

失败检查数 = 0 []
```

**对照结论**：BEFORE / AFTER 两个 repr 只有 `enabled: true` → `enabled: false` 一处不同；
字节 184 → 185（`false` 比 `true` 长 1 字节），其余 183 字节逐字节相同。

---

## 4. 实施期额外发现（第 5 类损失：行尾符被翻转）—— 已在同一处修复

复现过程中发现审计 **F1 未列出的第 5 类格式损失**：

`Path.write_text(text, encoding="utf-8")` 在 Windows 上以 `newline=None` 打开，
会把正文里的 `\n` **翻译成 `\r\n`** ⇒ 一次启停就把 LF 文件在磁盘上变成 CRLF
（`read_text` 读回时又翻译回 `\n`，所以纯文本比对**看不出来**，只有看原始字节才暴露）。

实测（沙箱，LF 文件）：

```text
original bytes == LF only : True
after update_meta CRLF?   : True
after raw repr            : b'---\r\nid: demo-skill\r\nenabled: false\r\n---\r\n\r\nBody'
```

生产事实（只读检查）：`data/skills_repo` 的 **23/23 个 skill.md 磁盘上都是 CRLF** ——
与「历史上每次启停都被 write_text 翻译过一次」一致（git `core.autocrlf=true` 会在 diff 中归一化，所以此前未被发现）。

**修复方式**：`update_meta` 改为按原文行尾符读、按原文行尾符写：

```python
with md_path.open("r", encoding="utf-8", newline="") as fp:   # 不做换行翻译
    original = fp.read()
...
with md_path.open("w", encoding="utf-8", newline="") as fp:   # 不做换行翻译
    fp.write(md_content)
```

⇒ LF 文件保持 LF、CRLF 文件保持 CRLF（`patch_front_matter` 用 `splitlines(keepends=True)` 切分，
未触碰的行连同其终止符原样拼接）。**本卡刻意不做全仓 LF 归一化**：那会在 23 个文件上产生
「与功能无关」的大 diff，属于另一件事。

---

## 5. 修法论证：为什么选 (a)，而不是 (b) ruamel.yaml

任务卡给的两个选项，先核对前置条件（结论与卡上预设不同，故必须写清）：

### 5.1 ruamel.yaml 的实测状态

```text
$ python -c "import ruamel.yaml; print('ruamel OK', ...)"
ruamel OK 0.18.17                          ← 它【是】装着的

$ Select-String -Path requirements*.txt,pyproject.toml -Pattern 'ruamel'
（无输出 —— 4 个 requirements 文件与 pyproject.toml 都没有声明它）

$ python -m pip show ruamel.yaml | Select-String 'Name|Version|Required-by'
Name: ruamel.yaml
Version: 0.18.17
Required-by: hermes-agent                  ← 它是别的包的传递依赖
```

⇒ **卡片预设的「若未安装则不引入新依赖」在此并不成立**：库能 import，但它**不是云枢的依赖**，
而是 `hermes-agent` 的传递依赖。直接 `import ruamel.yaml` 会把
「云枢依赖 hermes-agent 的依赖」这个隐式契约写进代码：hermes-agent 升级 / 移除 / 换锁文件时，
`update_meta` 会 ImportError，而它位于**技能启停的写路径**上
（`registry.set_enabled` 文件轨分支）。这仍然属于「引入新依赖」，与卡的硬约束相抵，**故否决 (b)**。

### 5.2 更根本的理由：验收要求的是逐字节不变，round-trip 模式给不了这个保证

任务卡要求「只有 enabled 那一行变，其余**字节**不变」。ruamel 的 round-trip 保留**注释与标量风格**，
但它是「**语义等价的再输出**」而非字节级保真：长行 / 标量折行受 `yaml.width`（默认 80）影响，
缩进与引号风格在特定输入下仍会被规范化。要让它达到字节级保真需额外调参并逐项验证
（成本高于 (a)，收益仅是「少写这段逻辑」）。而 (a) 的保真度是**结构性**的：
未被 patch 的行根本不参与序列化，只做字符串拼接。

### 5.3 结论

选 **(a) 最小侵入序列化**，并在实现上补掉 (a) 的两个已知坑：
1. 块值（嵌套 dict/list、多行标量）的续行必须整段替换，否则留孤儿行（§7.2）；
2. 必须按原文行尾符写回（§4）。

---

## 6. 白名单语义：把「允许改什么」与「保留什么」拆开

| 语义 | 作用于 | 意图 | 修复后行为 |
|---|---|---|---|
| **允许改什么** | `patch` 的键 | 防止调用方写入任意字段 | **不变**：patch 中不在 `_META_FIELDS` 的键一律忽略（另加 `logger.debug` 留痕，便于排查） |
| **保留什么** | 文件**既有**内容 | ——（修复前被错误地也套用了白名单） | **改为以文件现状为准**：白名单外的既有字段、注释、格式一律原样保留 |

关键实现细节：**读侧的 `SkillMDParser.parse` 白名单过滤（:143）刻意未改**。
理由是「保留」这件事因此变得平凡：`update_meta` 只拿得到白名单字段，从不会去「重写」未知字段，
未知字段自然只剩「原样留在文件里」这一种命运。若把 parse 的白名单也去掉，未知字段会进入内存 meta
并可能被下游写回，反而扩大爆炸半径。

另一个关键设计：**值语义未变的键整行不动**，这实现了 F1 建议 (c) 的效果（启停后 diff 只允许单行变化），
但做在代码里而不是做成 CI 守卫：

```python
if key in current and _meta_value_equal(current[key], value):
    continue  # 值语义未变 ⇒ 一个字节都不动
```

⇒ 对已经是 `enabled: true` 的文件调用 `update_meta({"enabled": True})` 时，**文件完全不被触碰**。

---

## 7. 实现要点（agent/skills_mgmt/file_store.py）

### 7.1 新增

| 位置 | 内容 |
|---|---|
| :50 | `import re` |
| :89–99 | `_meta_value_equal(a, b)` 模块级助手：宽松相等（异构类型 `__eq__` 抛错时按不等处理） |
| :179–318 | `SkillMDParser._FM_KEY_RE` / `_find_fm_key` / `patch_front_matter` |
| :804–837 | `SkillFileStore.update_meta` 重写（签名、返回、异常语义不变） |

### 7.2 patch_front_matter 的保证（docstring 与实现一致）

- 白名单外的既有字段：原样保留
- YAML 注释：原样保留
- 未被 patch 的键：行内容、键顺序、引号风格、`tags: [a, b, c]` 行内列表、缩进全部不变
- 行尾终止符（LF / CRLF）：与原文一致
- 值语义未变的键（含 patch 里显式给出的同值）：整行不动
- 有实际改动时**末尾保证以换行结尾**（POSIX；一次性归一化，此后稳定）
- 无实际改动时：**原样返回 content，不触碰文件**

**块范围判定**（`_find_fm_key`）：顶层键行（列 0 的 `key:`）连同其**缩进续行**一起替换；
列 0 的下一键与注释行视为块外（不会被误删）。
实施期实测捕获一个坑并已修：**多行标量的折行形如 `k: 'a` + 空行 + `  b'`，空行也属于块内** ——
若在空行处截断，替换后会残留 `  b'` 这样的残片（探针输出见 §8.3；该条先是 FAIL，修好后 PASS，并已加回归用例）。

### 7.3 update_meta 的兼容性（任务卡第 6 条）

- **签名未变**（单测用 `inspect.signature` 锁死参数名与默认值）
- **返回语义未变**：返回 `None`
- **异常语义未变**：技能不存在 → `SkillNotFoundError`；front matter 非法 / 未闭合 → `SkillFileError`
  （实现上仍先调 `self._read_md(skill_id)` 做同一套校验）
- **钩子 / 缓存副作用未变**：仍在锁外 `_notify_hooks(skill_id, "update")` 并 `_invalidate_index_cache(skill_id)`

### 7.4 本卡没有改的（显式声明）

| 项 | 状态 | 理由 |
|---|---|---|
| `SkillFileStore.create()`（:724） | **未改**，仍走 `serialize` + `write_text` | 它写的是**全新文件**，不存在「已有人工内容被抹掉」；其白名单约束的是**调用方传入的 dict**。**残余**：①调用方传入的白名单外字段仍会被丢；②Windows 上仍写 CRLF。建议单开卡 |
| `serialize()` / `parse()` | **未改** | 读侧白名单语义是本卡「保留」逻辑成立的前提（§6）；serialize 仍被 create / agentskills.io 路径使用 |
| registry.py / enhancer.py / executor.py / agent/tools/__init__.py / agent/audit/* | **未改** | 任务卡边界 |
| 全仓 skill.md 的 LF 归一化 | **未做** | 会在 23 个文件上产生与功能无关的大 diff，属另一件事 |

---

## 8. 验收命令与原始输出

### 8.1 新单测

```
$ python -m pytest tests/unit/test_update_meta_no_data_loss.py -q
platform win32 -- Python 3.12.0, pytest-9.1.1, pluggy-1.6.0
rootdir: C:\Users\Administrator\agent
configfile: pytest.ini
timeout: 120.0s
collected 28 items

tests\unit\test_update_meta_no_data_loss.py ............................ [100%]

================================== 所有测试通过！✓ ===================================
测试统计:
  通过: 28
  失败: 0
  跳过: 0
============================= 28 passed in 2.74s ==============================
```

用例覆盖（逐条对应任务卡第 5 条）：

| 卡上要求 | 用例 |
|---|---|
| 未知字段 + 注释 ⇒ 仍在 | `test_unknown_field_survives_update` / `test_yaml_comment_survives_update` |
| 改 enabled ⇒ 只有该行变、其余字节不变 | `test_only_enabled_line_changes`（逐行 + `before.replace(...) == after` 双重断言） |
| 未改 tags 时行内列表保持原样 | `test_inline_list_preserved_when_tags_not_patched`（并断言 front matter 行数不变） |
| 末尾换行保留 | `test_trailing_newline_preserved` / `test_missing_trailing_newline_is_restored` |
| 回归：enabled 读写语义不变 | `TestEnabledSemanticsRegression`（get_metadata / load_metadata_index / SkillRegistry.is_enabled） |
| 调用方仍工作 | `TestExistingCallers`（registry.set_enabled 文件轨端到端、toggle 往返回原字节、solidify 式 status 同步） |
| 兼容性 | `test_signature_and_return_unchanged`、异常语义 2 例 |
| 额外（实施期发现） | 多行值不残留孤儿行、Unicode 不转义、列表块、CRLF 保留、同值不写、白名单外 patch 键不改文件 |

全部用例使用 `tmp_path`，并在 fixture 里硬断言 repo 路径位于 tmp 之下（绝不触碰生产 skills_repo）。

### 8.2 既有回归（任务卡指定）

```
$ python -m pytest tests/unit/test_skill_registry.py tests/unit/test_skill_registry_audit.py -q
collected 19 items

tests\unit\test_skill_registry.py ..........                             [ 52%]
tests\unit\test_skill_registry_audit.py .........                        [100%]

================================== 所有测试通过！✓ ===================================
测试统计:
  通过: 19
  失败: 0
  跳过: 0
============================= 19 passed in 4.22s ==============================
```

### 8.2b 相邻测试（本卡自加的额外回归，非卡上要求）

```
$ python -m pytest tests/unit/test_skill_index_cache.py tests/unit/test_skills_mgmt.py \
      tests/unit/test_skill_update_audit.py tests/unit/test_meta_editor.py \
      tests/unit/test_agentskills_io_compat.py tests/unit/test_skill_file_store_path_traversal.py -q
collected 185 items

tests\unit\test_meta_editor.py ......................................    [ 20%]
tests\unit\test_skill_file_store_path_traversal.py ..................... [ 31%]
.............                                                            [ 38%]
tests\unit\test_skills_mgmt.py ...............x......................... [ 61%]
...................................                                      [ 80%]
tests\unit\test_skill_index_cache.py ........s....                       [ 87%]
tests\unit\test_skill_update_audit.py .........                          [ 91%]
tests\unit\test_agentskills_io_compat.py ...............                 [100%]

================================== 所有测试通过！✓ ===================================
测试统计:
  通过: 183
  失败: 0
  跳过: 1
SKIPPED [1] tests\unit\test_skill_index_cache.py:296: 需要 --runslow 选项才能运行慢速测试
XFAIL tests\unit/test_skills_mgmt.py::TestRetrievalEvaluation::test_skill_retrieval_precision_above_threshold
================= 183 passed, 1 skipped, 1 xfailed in 13.28s ==================
```

> 那个 xfail 是**修复前就存在**的检索质量基线问题（TF-IDF Precision@3=0.4444 < 0.6），与 F1b 无关；
> SKIP 是需要 `--runslow` 的慢测。均非本卡引入。

### 8.3 沙箱字节级探针（probe_raw.py，仓库外）

```text
=== A) 字节级：只有 enabled 行变化 ===
  [PASS] 行数相同 -- 19 vs 19
  [PASS] 恰好 1 行不同 -- [(12, 'enabled: true', 'enabled: false')]
  [PASS] 不同的那行是 enabled
=== B) 行尾终止符（LF 保持 LF / CRLF 保持 CRLF）===
  [PASS] LF 文件仍为纯 LF
  [PASS] CRLF 文件仍为 CRLF
  [PASS] CRLF 文件 enabled 已改
=== C) 末尾无换行的文件 → 补一个换行（POSIX）===
  [PASS] 末尾有换行 -- b'ed: false\n---\n\nBody\n'
  [PASS] body 未丢字
=== D) 嵌套块值替换不残留孤儿行 ===
  [PASS] 旧嵌套键 properties 已移除
  [PASS] 旧嵌套键 a 已移除
  [PASS] 新值已写入 -- '...unknown_custom_field: KEEP_ME\nconfig_schema:\n  type: object\nenabled: true\n...'
  [PASS] 后续 enabled 行未受影响
  [PASS] 注释与未知字段仍在
=== E) 新键追加到 front matter 末尾 ===
  [PASS] 新键已写入 / 在 front matter 内 / 解析可见
=== F) 白名单外的 patch 键：不改文件（且不删既有同名字段）===
  [PASS] 白名单外 patch 键被忽略(文件字节不变)
  [PASS] 既有未知字段仍在
=== G) 值未变 → 文件字节不变（重复启停无噪声）===
  [PASS] 同值 patch 不改字节
  [PASS] 改后再同值仍不改字节
  [PASS] tags 行内格式在两次启停后仍在
  [PASS] 注释在两次启停后仍在
=== H) new_instruction 替换 body，元数据行不动 ===
  [PASS] 新 body 已写入 / 旧 body 已替换 / front matter 未变行
  [PASS] parse 得到新 body -- '# New Body\n\nhello'
  [PASS] 末尾换行保留
  [PASS] enabled 语义未变
=== I) 异常语义（技能不存在 / front matter 非法）===
  [PASS] 不存在的技能抛 SkillNotFoundError
  [PASS] 未闭合 front matter 抛错 -- SkillFileError

PASS=30 FAIL=0 []
```

边界探针（probe_edges.py：多行值 / Unicode / 列表 / bool / None）：**PASS=13 FAIL=0**
（其中「多行块被整段替换」是**修复过程中先 FAIL 后修好**的一条，见 §7.2）。

### 8.4 生产文件副本端到端（只读生产的 23 个 skill.md 字节，写入全在临时目录）

```text
=== 生产文件行尾符 / 末尾换行（只读）===
CRLF 文件数 = 23/23；缺少末尾换行的文件数 = 15/23
缺末尾换行的 15 个是否全为 CRLF: True | 15 个是否全为 pd-*: True

skill_id                                   CRLF    首次+字节      t2==原+行尾符   t4==t2     差异行数
------------------------------------------------------------------------------------------------------------
context_aware                              True    1          True        True       1
emotion_expression                         True    1          True        True       1
memory_summary                             True    1          True        True       1
pd-brainstorming-697b717a-skill            True    3          True        True       1
pd-dispatching-parallel-agents-b8065ccd-sk True    3          True        True       1
pd-executing-plans-95cbf64a-skill          True    3          True        True       1
pd-finishing-a-development-branch-e085de5a True    3          True        True       1
pd-frontend-design-77ea5c4e-skill          True    3          True        True       1
pd-receiving-code-review-8934157e-skill    True    3          True        True       1
pd-requesting-code-review-ca5ae995-skill   True    3          True        True       1
pd-subagent-driven-development-8c375695-sk True    3          True        True       1
pd-systematic-debugging-556faa20-skill     True    3          True        True       1
pd-test-driven-development-8562c8ad-skill  True    3          True        True       1
pd-using-git-worktrees-d516703a-skill      True    3          True        True       1
pd-using-superpowers-3aea3fc9-skill        True    3          True        True       1
pd-verification-before-completion-af010352 True    3          True        True       1
pd-writing-plans-f846e3a2-skill            True    3          True        True       1
pd-writing-skills-5da20e67-skill           True    3          True        True       1
proactive_suggestion                       True    1          True        True       1
safety_guard                               True    1          True        True       1
scripted-selftest                          True    1          True        True       1
self_reflection                            True    1          True        True       1
voice_interaction                          True    1          True        True       1

行数不变: 23/23
t2 == 原字节（或原字节+该文件行尾符）: 23/23
第 2 次之后完全字节稳定 (t4==t2): 23/23
差异行数恒为 1: 23/23
```

含义分解（**这是本卡对 G1 的直接价值**）：
- **一次启停 = 恰好 1 行差异**（23/23），且那 1 行就是 `enabled:`；
- 从第 2 次写入起，**启停一个来回字节完全稳定**（23/23）；
- 8 个文件（末尾已有换行）启停一个来回**字节完全回到原样**；
- 15 个 `pd-*` 文件**当前缺少末尾换行**，修复后**首次**启停会把它补上
  （一次性的 POSIX 归一化；`+3` 字节 = `enabled: false` 比 `true` 多 1 字节 + 一个 `\r\n` 行尾），此后稳定。

样本（`pd-brainstorming-697b717a-skill`）一次启停的真实 diff：

```diff
@@ -14,7 +14,7 @@
 author: process_distill
 source: knowledge_distill
 status: approved
-enabled: true
+enabled: false
 ---
 
 # brainstorming
@@ -116,4 +116,4 @@
 ## 来源
-- brainstorming
+- brainstorming            ← 这一对是「补上文件末尾缺失的换行」，不是内容变化
```

### 8.5 未污染生产数据（任务卡硬要求）

```
$ git status --porcelain -- data/
（空输出）

$ git status --porcelain -- agent/skills_mgmt/file_store.py agent/skills_mgmt/models.py tests/unit/test_update_meta_no_data_loss.py
 M agent/skills_mgmt/file_store.py
?? tests/unit/test_update_meta_no_data_loss.py

$ git diff --numstat -- agent/skills_mgmt/file_store.py
190     8       agent/skills_mgmt/file_store.py
```

工作区里其余 `M ` 文件（chain.py / facade.py / registry.py / enhancer.py / …）属于**其它并行卡**，不是本卡所为。
`file_store.py` 的 5 个 hunk 与我的 4 处编辑一一对应：

```text
@@ -49,0 +50 @@ import os                    ← import re
@@ -87,0 +89,11 @@ def _trace_id()         ← _meta_value_equal
@@ -166,0 +179,140 @@ class SkillMDParser:   ← _find_fm_key + patch_front_matter
@@ -654 +806,16 @@ class SkillFileStore:  ← update_meta docstring
@@ -656,7 +823,10 @@ class SkillFileStore: ← update_meta body
```

---

## 9. 未验证项（显式声明，未做「应该可以」式结论）

| 项 | 状态 | 原因 |
|---|---|---|
| 全量 pytest（约 15k 用例） | **未验证** | 任务卡铁律 3 明令只跑卡列出的 targeted 测试 |
| 真实服务下的启停（HTTP 接口） | **未验证** | 环境事实：服务未运行；本卡不需要启动服务 |
| **在生产 data/skills_repo 上真实启停** | **未验证（且刻意不做）** | 任务卡特别警告：审计期间已发生过两处 skill.md 被误改。全部写入实验都在**仓库外临时目录**，生产只做 `read_bytes` 只读副本 |
| ruamel.yaml 方案到底能否做到字节级保真 | **未验证** | 未采用该方案（§5：依赖未声明 + 不保证字节保真）；不做无收益的对比实验 |
| `create()` 路径的白名单丢失与 CRLF 写入 | **未验证 / 未修** | 超出本卡范围（§7.4），已登记为残余 |
| 其它并行卡的改动是否影响本卡 | **未验证** | registry.py 等属别的卡；本卡端到端验证只依赖公开 API 与本次实测的调用点行号 |
| patch 值为不可 YAML 序列化对象时的行为 | **未验证** | 修复前后一致：`safe_dump` 抛错，且**发生在任何写入之前**，不会留下半写状态；未构造该类用例 |

**关于「不会半写」**：所有 yaml dump 都在改写 front matter 行列表**之前**完成，文件写入是最后一步的单个 write。
因此序列化失败时文件保持原样。

---

## 10. 对 G1 的结论与后续建议

1. **G1 的阻塞已解除**：启停 = 恰好 1 行 diff（23/23 生产文件副本实测），不再抹掉人工自定义字段与 YAML 注释。
   F1 警告的「一边改描述一边被启停抹掉」不再成立。
2. **建议给 G1 配一条廉价守卫**（与原 F1 建议 (c) 同源，且现在更容易实现）：
   CI 里跑「对每个 skill.md 副本做一次启停 → 断言 `git diff` 只涉及 `enabled:` 行」，
   做法与 §8.4 探针完全相同（只读生产、写入临时目录）。本卡未加进 CI，因为那要改 CI 配置（不在允许文件清单内）。
3. **迁移记录需预告 2 件事**，避免被误判为异常：
   - 15 个 `pd-*` 文件**首次**启停会各增加 1 个末尾换行（一次性，之后稳定）；
   - 本卡**没有**改动任何 `data/` 下的文件（`git status -- data/` 为空）。
4. **建议单开一张小卡**：`create()` 仍走 `serialize` + `write_text`
   （白名单裁剪调用方传入字段 + Windows 写 CRLF）。它不在启停路径上，危害等级低于 F1b，
   但它与 `update_meta` 现在对「行尾符」的处理不一致。

---

## 附：复现 / 验证脚本清单（全部在仓库外，未提交）

| 脚本 | 用途 |
|---|---|
| `%TEMP%\f1b_sandbox\repro_update_meta.py` | 修复前 / 后对照复现（§2 / §3） |
| `%TEMP%\f1b_sandbox\probe_raw.py` | 字节级 30 项探针（§8.3） |
| `%TEMP%\f1b_sandbox\probe_edges.py` | 多行 / Unicode / 列表 / bool / None 边界 13 项 |
| `%TEMP%\f1b_sandbox\probe_prod_copies3.py` | 生产 23 文件只读副本端到端（§8.4） |

> 这些脚本位于系统临时目录（`C:\Windows\TEMP` / `%LOCALAPPDATA%\Temp`），不属于仓库内容；
> 如需长期保留请另行归档 —— 本报告已内联全部关键输出，结论可离线复核。
