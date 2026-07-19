---
id: scripted-selftest
name: 带脚本技能示例
description: 三层架构示例技能，演示 skill.md 元数据 + scripts/main.py 执行脚本 + default_params 参数注入的完整契约，作为新增带脚本技能的参考模板
category: example
tags: [example, scripted, reference, l3-execution]
version: 1.0.1
enabled: true
status: approved
author: agent-team
source: manual
content_type: markdown
default_params:
  greeting: hello
  count: 3
---

# 带脚本技能示例

三层架构示例技能，演示完整的 L3 脚本执行契约：skill.md 元数据 + scripts/main.py 执行脚本 + default_params 参数注入。

## 适用场景

- **参考模板**：作为新增带脚本技能的起点，复制本目录后修改
- **契约验证**：验证 SkillExecutor 的脚本执行路径、参数注入、结果解析
- **回归测试**：verify_migrated_skills.py 默认模式会执行本技能，确认 L3 有脚本分支工作正常

## 触发条件

满足以下任一条件时由 SkillLoader 匹配后交由 SkillExecutor 执行：

- 用户意图命中 `带脚本技能示例` 或 `scripted-selftest` 名称
- 显式调用 `SkillManager.execute("scripted-selftest", params=...)`
- 验证脚本 `verify_migrated_skills.py` 批量执行时

## 使用方式

1. SkillLoader 按意图匹配到本技能
2. SkillExecutor 查找 `scripts/main.py`，从 front matter 读取 `default_params`
3. 参数通过 stdin JSON 传入脚本，脚本读取后处理
4. 脚本 stdout 最后一行必须为合法 JSON，作为执行结果被解析

## 参数说明

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| greeting | str | hello | 回声字符串，脚本原样回显到 `echo` 字段 |
| count | int | 3 | 数值参数，脚本原样回显到 `count` 字段 |

调用方可覆盖默认参数：

```python
mgr.execute("scripted-selftest", params={"greeting": "hi", "count": 5})
```

## 脚本契约

- **输入**：stdin JSON，含 `greeting` (str) 和 `count` (int)
- **输出**：stdout 最后一行为 JSON，结构如下
  ```json
  {"ok": true, "echo": "hello", "count": 3}
  ```
- **退出码**：0 表示成功，非 0 视为失败
- **校验**：`result.success=true`、`exit_code=0`、`result.result` 非空且含 `ok` 键

## 输出特征

- `ok` (bool)：固定 true，标识脚本执行成功
- `echo` (str)：回显输入的 `greeting` 参数
- `count` (int)：回显输入的 `count` 参数

## 不变量

- 脚本必须从 stdin 读取参数，不从命令行参数读取
- 脚本 stdout 最后一行必须是合法 JSON，其他行被忽略
- 脚本不得写入文件系统或发起网络请求
- 退出码非 0 时，无论 stdout 内容如何，均视为执行失败

## 验证方式

```bash
# 默认模式：本技能与其他无脚本技能一起被验证
python scripts/verify_migrated_skills.py

# 自测模式：在临时仓库创建独立副本验证
python scripts/verify_migrated_skills.py --self-test
```

默认模式输出示例：
```
[L3] SkillExecutor.execute (分支化: 无脚本→SCRIPT_NOT_FOUND / 有脚本→执行校验)
     ├─ [trace] list_scripts 耗时=1.00ms scripts=['main.py']
     ├─ [trace] 参数注入 耗时=0.00ms params={'greeting': 'hello', 'count': 3}
     ├─ [trace] execute 耗时=67.23ms success=True exit=0
     ├─ [trace] 结果校验 耗时=0.00ms result={'ok': True, 'echo': 'hello', 'count': 3}
     └─ [trace] 总耗时=69.23ms
     [OK] scripted-selftest: success exit=0 scripts=['main.py'] result_keys=['ok', 'echo', 'count']
```
