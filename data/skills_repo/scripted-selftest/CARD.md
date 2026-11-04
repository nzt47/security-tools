# 带脚本技能示例

> LLM 整理，逐条经**原文引用校验**：verified 24 / 24（未通过的已丢弃）。

## 何时使用

- 作为新增带脚本技能的参考模板，可复制本目录后修改
- 用于验证 SkillExecutor 的脚本执行路径、参数注入与结果解析
- verify_migrated_skills.py 默认模式会执行本技能，用于回归测试 L3 脚本分支
- 用户意图命中技能名称时由 SkillLoader 匹配后交由 SkillExecutor 执行
- 可通过 SkillManager.execute 显式调用本技能并传参
- 验证脚本批量执行时也会触发本技能

## 步骤

- SkillLoader 按意图匹配到本技能
- SkillExecutor 查找 scripts/main.py 并从 front matter 读取 default_params
- 参数通过 stdin JSON 传入脚本，脚本读取后处理
- 脚本 stdout 最后一行必须是合法 JSON，作为执行结果被解析

## 参数

- greeting 为字符串参数，默认 hello，脚本原样回显到 echo 字段
- count 为整数参数，默认 3，脚本原样回显到 count 字段
- 调用方可以覆盖默认参数
- 脚本输入为 stdin JSON，含 greeting 字符串和 count 整数
- 脚本输出为 stdout 最后一行的 JSON，结构含 ok、echo、count

## 约束 / 反例

- 脚本必须从 stdin 读取参数，不能从命令行参数读取
- stdout 最后一行必须是合法 JSON，其他行被忽略
- 脚本不得写入文件系统或发起网络请求
- 退出码非 0 时无论 stdout 内容如何都视为执行失败
- 退出码 0 表示成功，非 0 视为失败
- 结果校验要求 success=true、exit_code=0、result 非空且含 ok 键

## 示例

- 默认模式下用验证脚本验证本技能
- 自测模式在临时仓库创建独立副本进行验证
- 默认模式输出示例展示脚本列表、参数注入、执行与结果校验的 trace
