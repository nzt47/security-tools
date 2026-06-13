# 🚀 规划引擎快速参考

## 快速启动

```bash
# 正常模式
python main.py

# 调试模式（详细日志）
python main.py --debug

# 单次对话
python main.py --chat "你好"

# 查看状态
python main.py --status
```

## 安全保护

| 保护类型 | 阈值 | 触发条件 | 响应 |
|---------|------|---------|------|
| 超时保护 | 30秒 | 执行超过30秒 | 优雅终止，返回提示 |
| 循环检测 | 100次/分钟 | 1分钟内迭代>100次 | 检测并终止 |
| 状态卡死 | 10秒 | 状态不变超过10秒 | 检测并恢复 |

## 日志查看

```bash
# 查看所有日志
python main.py 2>&1

# 只看INFO级别
python main.py

# DEBUG详细模式
python main.py --debug 2>&1

# 过滤规划引擎日志
python main.py 2>&1 | grep "planning"
```

## 关键日志符号

- 🔍 分析/查询
- ✅ 成功
- ⚠️ 警告
- ❌ 错误
- 🔄 状态变化
- 💭 思考
- ⚡ 行动
- 🧠 反思
- 🚀 启动
- 📊 统计
- ⏱️ 超时

## 核心配置

在 `main.py` 中调整：

```python
CHAT_TIMEOUT_SECONDS = 30        # 对话超时（秒）
PLAN_EXECUTION_TIMEOUT = 60      # 计划执行超时（秒）
max_iterations_per_minute = 100  # 循环检测阈值
state_stuck_threshold = 10      # 状态卡死阈值（秒）
```

## 异常处理

所有异常都会：
1. 记录详细日志（堆栈跟踪）
2. 生成友好提示
3. 返回兜底响应
4. 不影响主程序

## 测试验证

```bash
# 运行所有测试
python test_planning.py

# 测试结果
✅ 数据模型
✅ 任务分解器
✅ 执行引擎
✅ 状态机
✅ 核心模块
✅ 复杂任务场景
```

## 文档资源

- [完整总结](file:///c:/Users/Administrator/agent/PLANNING_COMPLETE_SUMMARY.md)
- [使用指南](file:///c:/Users/Administrator/agent/PLANNING_README.md)
- [日志指南](file:///c:/Users/Administrator/agent/PLANNING_LOGGING_GUIDE.md)
- [安全机制](file:///c:/Users/Administrator/agent/PLANNING_SAFETY_MECHANISM.md)

## 常见问题

**Q: 超时太短？** → 调整 `CHAT_TIMEOUT_SECONDS`

**Q: 误判循环？** → 提高 `max_iterations_per_minute`

**Q: 日志太多？** → 使用默认INFO级别

**Q: 状态卡死？** → 检查 `state_stuck_threshold`

---

**有问题？看文档！** 📚
