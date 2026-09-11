# `match` 子集规范（OPA 等效判定器）

> 适用：`Policy.match`（§3.11）｜实现：`agent/policy/matcher.py`｜
> 权威来源：本文件与 `matcher.support_matrix()` **必须一致**，由
> `tests/unit/test_policy_matcher.py::test_doc_matches_support_matrix` 守着——
> 文档与实现的漂移在这里会变成红灯，而不是变成「策略以为在拦、实际没拦」。

## 〇、为什么是「等效」而不是 OPA

P4 架构裁定（TASK-S4-02 §一.3）：单机不强制引入 OPA/WASM，采用**等效声明式策略
引擎**，但**接口形状必须兼容未来替换 OPA**
（`PolicyEngine.check(policy_ctx) -> {allow|deny|ask, policy_id}`）。

因此表达式求值被隔离在 `matcher.py` 一个模块内：将来换真 OPA 时，只需把
`match` 编译成预编译 WASM 或在进程内嵌 OPA，`store` / `engine` / 执行点接线不动。

**判定输入**是 §5.6 的 `input` 文档（`PolicyContext.input`）。叶子用**点分字段路径**
寻址：`capability.trust.risk_level`。允许写 `input.capability.trust.risk_level`
（`input.` 前缀在装载期归一化去掉）。

---

## 一、支持：叶子比较（field path comparison）

```json
{"field": "capability.trust.risk_level", "op": "eq", "value": "destructive"}
```

`op` 缺省为 `eq`。完整算子表：

| op | 语义 | `value` 形态 |
|---|---|---|
| `eq` | 相等（int/float 数值等价；`true` 不等于 `1`） | 标量 / list / dict |
| `ne` | 不等（路径缺失 ⇒ 真） | 标量 / list / dict |
| `in` | **集合成员**：字段值 ∈ `value` | **list** |
| `not_in` | 集合非成员（路径缺失 ⇒ 真） | **list** |
| `contains` | 字段（str 子串 / list 元素）包含 `value` | 标量 |
| `startswith` | 字符串前缀（字段必须是 str） | str |
| `endswith` | 字符串后缀（字段必须是 str） | str |
| `glob` | fnmatch 通配（字段转 str 后匹配） | str |
| `gt` | 大于（数值同型，或 str 之间） | 标量 |
| `gte` | 大于等于 | 标量 |
| `lt` | 小于 | 标量 |
| `lte` | 小于等于 | 标量 |
| `exists` | 路径存在（**不得带 `value`**） | — |
| `not_exists` | 路径不存在 | — |

**值域纪律**：`op` 不在上表内 ⇒ **装载期报错**（`PolicyValidationError`），不会
静默判 False。

## 二、支持：集合成员（set membership）

即 `in` / `not_in`，只做**一层**成员判定：

```json
{"field": "tenant.id", "op": "in", "value": ["t-alpha", "t-beta"]}
```

不做「集合套集合」的传递成员判定（Rego 里也需显式展开）。`value` 不是 list ⇒
装载期报错。

## 三、支持：布尔组合（boolean combination）

| 写法 | 别名 | 语义 |
|---|---|---|
| `{"all": [e1, e2, ...]}` | `and` | 全部为真（空数组 ⇒ 真） |
| `{"any": [e1, e2, ...]}` | `or` | 任一为真（空数组 ⇒ 假） |
| `{"not": e}` | — | 取反 |

可任意嵌套（深度上限 16，超过判为「疑似生成错误」并报错）。

## 四、支持：简写映射（shorthand map）

一个 dict 里**没有** `all`/`any`/`not` 保留键时，其余每个键都是**字段路径**，值为
字面量（等价 `eq`）或**单算子 dict**：

```json
{
  "capability.trust.risk_level": "destructive",
  "target.external": true,
  "tenant.id": {"in": ["t-alpha", "t-beta"]},
  "all": [{"field": "attributes.payload_bytes", "op": "gt", "value": 0}]
}
```

一个 dict 内：**所有简写项之间、简写项与保留键之间，一律 AND**。

空 match（`{}`）⇒ **恒真**（显式声明的「全命中」）。`match` 缺失 ⇒ 装载期报错：
「空 match 会命中全部输入，必须显式声明」。

## 五、明确**不支持**（设计边界，不是待办）

命中下列任一项时**装载期直接报错**并给出替代做法。绝不静默当作 False——
静默失真就是假安全。

| 不支持的算子/构造 | 原因与替代 |
|---|---|
| `regex` / `matches` / `re_match` / `match` | **ReDoS 面**：策略文本可能由 LLM 生成，不给它可写灾难性回溯的口子。用 `startswith` / `endswith` / `contains` / `glob` |
| `walk` | 无遍历语义。用 `in` + 显式集合 |
| `count` | 无聚合语义。把计数上移为 capability 的聚合字段 |
| `some` / `every` | 无迭代语义。用 `in` + 显式集合 |
| `sprintf` | 无格式化语义。用 `message_template` |
| `now` / `time_now_ns` | **破坏决策可重放性**（模拟器要求同一输入同一结果）。用 `effective_range` |

| 不支持的路径/构造 | 原因与替代 |
|---|---|
| `data.*` 文档查找、`import` | 引擎无外部文档空间（无隐式 I/O）。用 `attributes.*` |
| `with` 覆盖、`json.patch` 等内建 | 语义与决策无关 |
| 用户自定义规则/函数（`deny[msg] { ... }`） | 无 Rego 编译器。改用多条 Policy（首个命中生效）+ `effective_range` 分层 |
| comprehension（`[x \| x := ...]`） | 无循环语义 |
| 算术与字符串插值 | 判定不需要；文案用 `message_template` |
| 数组下标路径（`a.b[0].c`） | 路径只做 dict 逐段查询。需按下标判定请改用 `in` |
| 路径通配（`a.*.b`、`a.?`） | 路径不寻址通配；比较值请用 `glob` |
| match 根为数组 | 组合必须显式写 `{"all": [...]}` / `{"any": [...]}` |
| match 为 `true`/`null` | 空匹配请写 `{}` 或显式条件 |
| 时间/随机/环境读取 | 破坏可重放性 |
| **任何网络或执行副作用**（`http.send`、`socket`、`subprocess`、`exec(`、`requests.` …） | **P7.1-20**：策略引擎只出决策、不做网络动作；LLM 不得在策略中生成网络副作用。见下节 |

## 六、禁止 token 清单（P7.1-20 装载期拦截）

`match` 子树做 JSON 规范化并小写化后，出现下列任一子串即**拒绝装载**
（`agent/policy/models.py::FORBIDDEN_MATCH_TOKENS`）：

```
http.send  http_send  https.send  http.request  http_request  net.http  net.send
socket  fetch(  curl   subprocess  os.system  os.popen  exec(  eval(  import
require(  __import__  requests.  urllib  httpx  aiohttp
```

这条检查针对的是**真实会发生的抄写错误**：设计文档 §5.6 的 Rego 示例里就有
`http.send(_); msg := "数据出域"`，而 P7.1-20 明确更正它是「策略意图伪代码」。
把它抄成策略文本必须被拦住，而不是被静默接受。

## 七、求值语义细节

| 情形 | 结果 | 理由 |
|---|---|---|
| 路径缺失 + `eq` | 假 | Rego 里未定义即不满足 |
| 路径缺失 + `ne` / `not_in` / `not_exists` | 真 | 取反同理 |
| 路径缺失 + `in` | 假 | 无值不可能是集合成员 |
| 字段为 `null`（键存在） | 按 `null` 参与比较，**不**等同缺失 | `exists` 与 `eq null` 是两件事 |
| `gt`/`lt` 两端类型不可比 | **假** + 计入 `type_errors` | 绝不判真 |
| `startswith` 遇非 str 字段 | **假** + 计入 `type_errors` | 同上 |
| `eq` 遇 `true` vs `1` | 假 | Python 里 `True == 1`，策略语义上必须可区分 |
| `eq` 遇 `1` vs `1.0` | 真 | 数值等价 |
| `eq` 遇 `"1"` vs `1` | 假 | 跨类型不等价 |

`MatchResult` 会一并返回 `leaves` / `type_errors` / `missing_fields`，供诊断
「策略太宽/太窄/键名写错」。求值是**纯函数**：同一 `input` + 同一策略必得同一结果
（这是 P7.2-19 模拟器可重放的前提）。

## 八、`message_template` 渲染

只替换 `{simple_name}` 形态的占位符，**不使用 `str.format`**：

- `"{a.__class__}".format(a=x)` 会经属性访问读到类型对象——把策略作者（可能是 LLM）
  可控的模板交给 `format` 等于开一个属性遍历面；
- 未提供的占位符**原样保留**（便于发现文案缺参，而不是抛异常打断决策路径）。

可用占位符：`capability_id` / `policy_id` / `policy_version` / `policy_owner` /
`effect` / `tenant_id` / `actor` / `action`，以及 `input` 各字段组的**扁平叶子**
（`capability.trust.data_class` → `{data_class}`、`target.host` → `{target_host}`）。
