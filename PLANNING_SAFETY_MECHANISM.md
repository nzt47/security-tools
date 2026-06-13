# 规划引擎安全保护机制

## 概述

为了防止规划引擎在执行过程中出现死循环、状态卡死等异常情况，我们添加了完善的安全保护机制。

## 安全保护特性

### 1. 🔒 执行超时保护

**超时配置：**
```python
CHAT_TIMEOUT_SECONDS = 30  # 单次对话超时时间
PLAN_EXECUTION_TIMEOUT_SECONDS = 60  # 计划执行超时时间
```

**实现方式：**
- **Unix系统**：使用 `signal.SIGALRM` 信号实现精确超时
- **Windows系统**：使用线程 + `join(timeout)` 实现超时保护

**触发条件：**
- 单次对话处理超过30秒
- 计划执行超过60秒

**兜底响应：**
```python
"抱歉，您的请求处理时间过长。为了保证系统稳定，我已自动终止了本次操作。建议您简化问题或稍后重试。"
```

### 2. 🔄 死循环检测

**检测机制：**

`ExecutionMonitor` 类实现了两级检测：

#### 2.1 快速循环检测
```python
max_iterations_per_minute = 100  # 每分钟最大迭代次数
```

**触发条件：**
- 在1分钟内，同一任务迭代超过100次

**检测逻辑：**
```python
def record_iteration(self, identifier: str) -> bool:
    # 记录每次迭代
    # 检查1分钟时间窗口内的迭代次数
    # 如果超过阈值，判定为快速循环
```

**兜底响应：**
```python
"抱歉，我检测到执行异常，已自动终止。请稍后重试。"
```

#### 2.2 状态卡死检测
```python
state_stuck_threshold = 10  # 状态卡死阈值（秒）
```

**触发条件：**
- 同一状态保持超过10秒未变化

**检测逻辑：**
```python
def check_state_change(self, identifier: str, new_state: str) -> bool:
    # 记录状态变化时间
    # 检查状态是否长时间未变化
    # 如果超过阈值，判定为状态卡死
```

**兜底响应：**
```python
"抱歉，系统似乎陷入了某种状态。我已尝试恢复，请重新发起请求。"
```

### 3. 🛡️ 异常兜底处理

#### 3.1 三层异常捕获

**第一层：对话执行层**
```python
def safe_chat(Yunshu, user_input: str, timeout: int = CHAT_TIMEOUT_SECONDS):
    try:
        result = Yunshu.chat(user_input)
    except Exception as e:
        return f"抱歉，处理您的请求时遇到了问题：{str(e)}", e
```

**第二层：线程执行层**
```python
chat_thread = threading.Thread(target=chat_execution, daemon=True)
chat_thread.start()
chat_thread.join(timeout)  # 30秒超时

if chat_thread.is_alive():
    # 超时处理
    return "抱歉，您的请求处理时间过长...", TimeoutException()
```

**第三层：监控检测层**
```python
# 检查执行监控
if not monitor.record_iteration(execution_id):
    # 循环检测异常
    return "抱歉，我检测到执行异常...", LoopDetectionException()

# 检查状态监控
if not monitor.check_state_change(execution_id, new_state):
    # 状态卡死异常
    return "抱歉，系统似乎陷入了某种状态...", StateStuckException()
```

#### 3.2 自定义异常类型

```python
class TimeoutException(Exception):
    """超时异常"""
    pass

class LoopDetectionException(Exception):
    """循环检测异常"""
    pass

class StateStuckException(Exception):
    """状态卡死异常"""
    pass
```

### 4. 📊 日志记录

#### 4.1 详细执行日志

**日志级别配置：**
```python
# INFO级别（默认）
planning_modules = [
    "planning.core",      # 核心调度器
    "planning.decomposer", # 任务分解器
    "planning.executor",  # 执行引擎
    "planning.reflector", # 反思引擎
    "planning.state_machine",  # 状态机
    "planning.react",     # ReAct循环
    "planning.models",    # 数据模型
]

# DEBUG模式启用
python main.py --debug
```

**日志输出示例：**
```
17:31:40 [    INFO] planning.core        : ============================================================
17:31:40 [    INFO] planning.core        : 🔍 [规划引擎] 开始创建执行计划
17:31:40 [    INFO] planning.core        :    任务描述: 帮我检查系统状态...
17:31:40 [    INFO] planning.decomposer  : 🔧 [规则分解] 开始基于关键词的任务分解
17:31:40 [    INFO] planning.react       : 🔄 [ReAct循环] 开始执行
17:31:40 [    INFO] planning.react       : 🔁 [迭代 1/10] 开始
17:31:40 [    INFO] planning.react       :    💭 步骤1: 思考阶段...
17:31:40 [    INFO] planning.react       :    ⚡ 步骤2: 行动阶段...
17:31:40 [    INFO] planning.react       :    ✅ 行动完成: 成功
```

#### 4.2 异常日志记录

```python
# 超时异常
logger.error(f"⏱️ 对话执行超时（{timeout}秒）")
logger.error(f"   尝试生成超时响应...")

# 循环检测
logger.error(f"⚠️ 检测到快速循环: {identifier}")
logger.error(f"   最近1分钟内迭代了 {record['recent_count']} 次")

# 状态卡死
logger.error(f"⚠️ 检测到状态卡死: {identifier}")
logger.error(f"   当前状态: {new_state}")
logger.error(f"   卡死时间: {stuck_time:.1f}秒")

# 执行异常
logger.error(f"❌ 对话执行异常: {e}")
logger.error(f"堆栈:\n{traceback.format_exc()}")
```

## 使用方式

### 1. 正常模式运行

```bash
python main.py
```

日志输出：
```
============================================================
17:31:40 [    INFO] 云枢                  : ================================================================
17:31:40 [    INFO] 云枢                  : 日志系统配置完成
17:31:40 [    INFO] 云枢                  : 调试模式: 关闭
17:31:40 [    INFO] 云枢                  : ================================================================
17:31:40 [    INFO] 云枢                  : ================================================================
17:31:40 [    INFO] 云枢                  : 🚀 云枢 (Yunshu) 启动中...
============================================================
```

### 2. 调试模式运行

```bash
python main.py --debug
```

获得更详细的日志：
```
17:31:40 [    INFO] planning.decomposer  : 🔍 [任务分解器] 开始分解任务
17:31:40 [    INFO] planning.decomposer  :    原始任务: 帮我检查CPU、内存和磁盘
17:31:40 [   DEBUG] planning.decomposer  : 🔧 [规则分解] 开始基于关键词的任务分解
17:31:40 [   DEBUG] planning.decomposer  :    使用分隔符 '然后' 分割任务
```

### 3. 单次对话模式

```bash
python main.py --chat "你好，帮我检查系统状态"
```

输出：
```
云枢: 我是来自网天的云枢。让我帮您检查一下系统状态...
```

## 异常处理示例

### 示例1: 执行超时

**用户输入：**
```
帮我分析一下最近1000次系统日志，生成完整报告
```

**系统行为：**
```
17:31:40 [    INFO] 云枢                  : 💬 [安全执行] 开始处理对话
17:31:40 [    INFO] 云枢                  :    用户输入: 帮我分析一下最近1000次系统日志...
17:31:40 [    INFO] 云枢                  :    超时设置: 30秒
17:31:40 [    INFO] 云枢                  : ================================================================
17:31:41 [    INFO] planning.core         : 🔍 [规划引擎] 开始创建执行计划
...
17:32:10 [    INFO] 云枢                  : ⏱️ 对话执行超时（30秒）
17:32:10 [    INFO] 云枢                  :    尝试生成超时响应...

云枢: 抱歉，您的请求处理时间过长。为了保证系统稳定，我已自动终止了本次操作。建议您简化问题或稍后重试。
```

### 示例2: 死循环检测

**用户输入：**
```
不断重复检查CPU状态
```

**系统行为：**
```
17:31:40 [    INFO] planning.react       : 🔄 [ReAct循环] 开始执行
17:31:40 [    INFO] planning.react       : 🔁 [迭代 1/10] 开始
17:31:40 [    INFO] planning.react       :    ⚡ 步骤2: 行动阶段...
17:31:41 [    INFO] planning.react       : 🔁 [迭代 2/10] 开始
17:31:41 [    INFO] planning.react       :    ⚡ 步骤2: 行动阶段...
... (快速重复)
17:31:40 [    INFO] 云枢                  : ⚠️ 检测到快速循环: chat_20240101_173140
17:31:40 [    INFO] 云枢                  :    最近1分钟内迭代了 105 次
17:31:40 [    INFO] 云枢                  :    触发阈值: 100

云枢: 抱歉，我检测到执行异常，已自动终止。请稍后重试。
```

### 示例3: 状态卡死

**系统行为：**
```
17:31:40 [    INFO] planning.state_machine: 计划状态转换: ready → executing
17:31:40 [    INFO] 云枢                  : 🔄 状态变化: plan_abc123 -> executing
17:31:50 [    INFO] 云枢                  : ⚠️ 检测到状态卡死: plan_abc123
17:31:50 [    INFO] 云枢                  :    当前状态: executing
17:31:50 [    INFO] 云枢                  :    卡死时间: 10.5秒

云枢: 抱歉，系统似乎陷入了某种状态。我已尝试恢复，请重新发起请求。
```

## 性能考虑

### 1. 线程开销
- 使用守护线程（daemon=True）
- 超时后自动终止，不影响主程序

### 2. 内存占用
- 监控数据使用线程锁保护
- 定期清理历史记录
- 监控器支持按需重置

### 3. 延迟影响
- 线程检查开销 < 1ms
- 超时检测时间窗口：1分钟
- 状态卡死检测阈值：10秒

## 配置参数

### 可调整参数

在 `main.py` 中：

```python
# 超时配置
CHAT_TIMEOUT_SECONDS = 30  # 单次对话超时
PLAN_EXECUTION_TIMEOUT_SECONDS = 60  # 计划执行超时

# 循环检测
max_iterations_per_minute = 100  # 每分钟最大迭代

# 状态卡死
state_stuck_threshold = 10  # 状态卡死阈值（秒）
```

### 建议配置

**开发环境（需要详细日志）：**
```bash
python main.py --debug
```

**生产环境（需要稳定运行）：**
```bash
# 保持默认配置
python main.py
# 或调整超时参数
CHAT_TIMEOUT_SECONDS = 60 python main.py
```

## 故障排查

### 问题1: 超时频繁

**症状：**
- 正常任务也被判定为超时

**可能原因：**
- 系统负载过高
- LLM API响应慢
- 网络延迟

**解决方案：**
```python
# 调整超时时间
CHAT_TIMEOUT_SECONDS = 60  # 增加到60秒
```

### 问题2: 误判循环

**症状：**
- 正常任务被误判为循环

**可能原因：**
- 阈值设置过低
- 真实需要多次迭代

**解决方案：**
```python
# 调整阈值
max_iterations_per_minute = 200  # 提高到200次
```

### 问题3: 状态卡死误判

**症状：**
- 长时间任务被误判为卡死

**可能原因：**
- 状态停留时间阈值过低

**解决方案：**
```python
# 调整阈值
state_stuck_threshold = 30  # 提高到30秒
```

## 总结

安全保护机制确保了规划引擎的稳定运行：

✅ **超时保护**：防止长时间无响应  
✅ **循环检测**：自动识别并终止异常循环  
✅ **状态监控**：检测状态卡死并恢复  
✅ **异常兜底**：三层异常捕获，确保有响应  
✅ **详细日志**：便于问题排查和监控  
✅ **可配置**：参数可调整，适配不同场景  

有任何问题，请查看日志输出或联系开发者！
