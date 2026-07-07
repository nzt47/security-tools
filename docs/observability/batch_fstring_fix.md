# 全项目 f-string 嵌套引号批量修复

## 修复时间
2026-07-07

## 修复背景
CI Run #71 中 E2E 端到端验证失败，根因是 `agent/task_scheduler.py` 第 224 行的 f-string 嵌套引号 SyntaxError 导致 app_server.py 启动失败，所有端点 Connection refused。

同时，架构影响可见性检查报告 8 个文件因 f-string 问题解析失败。

## 修复文件清单（共 5 个文件，13 处）

| 文件 | 行号 | 修复内容 |
|------|------|----------|
| agent/task_scheduler.py | 224 | `task['name']` → `task["name"]` |
| agent/network_config.py | 252 | `instance.get('name')` → `instance.get("name")` 等 |
| agent/network_config.py | 820 | `instance.get('name')` → `instance.get("name")` 等 |
| agent/network_config.py | 828 | `new_instance['id']` → `new_instance["id"]` 等 |
| agent/network_config.py | 848 | `new_instance['id']` → `new_instance["id"]` 等 |
| agent/network/config_manager.py | 249 | `instance.get('name')` → `instance.get("name")` 等 |
| agent/network/config_manager.py | 783 | `instance.get('name')` → `instance.get("name")` 等 |
| agent/network/config_manager.py | 791 | `new_instance['id']` → `new_instance["id"]` 等 |
| agent/network/config_manager.py | 811 | `new_instance['id']` → `new_instance["id"]` 等 |
| agent/p6/snapshot.py | 731 | `state.get('mode', 'NORMAL')` → `state.get("mode", "NORMAL")` |
| agent/p6/snapshot.py | 795 | `', '.join(state['tools'])` → `", ".join(state["tools"])` |
| agent/p6/snapshot.py | 920 | `('增量' if ... else '完整')` → `("增量" if ... else "完整")` |
| agent/tools/file_tools.py | 618 | `info['type']` → `info["type"]` 等 |

## 修复方式
统一将 f-string 内部的单引号 `'xxx'` 改为双引号 `"xxx"`，与之前 state_manager.py 修复方式相同。

## 预期结果
- E2E 端到端验证通过（app_server.py 不再因 SyntaxError 启动失败）
- 架构影响可见性检查中 8 个文件解析失败问题解决
