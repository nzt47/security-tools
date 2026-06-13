# 🎯 云枢 LLM 智能对话配置 - 快速开始

## ⚡ 立即配置（2分钟）

### 选项 1: 使用快速配置向导（推荐）⭐

```bash
python quick_config.py
```

按照提示选择：
1. 选择 LLM 提供商（1-4）
2. 输入 API Key
3. 运行生成的启动脚本

---

### 选项 2: 查看完整配置指南

```bash
type LLM_CONFIG_GUIDE.md
```

包含详细的：
- 各提供商对比
- API Key 获取方法
- 常见问题解决

---

## 🔧 快速配置命令

### Windows CMD

```cmd
set LLM_PROVIDER=openai
set LLM_API_KEY=sk-your-key-here
set LLM_MODEL=gpt-4o
python main.py
```

### Windows PowerShell

```powershell
$env:LLM_PROVIDER="openai"
$env:LLM_API_KEY="sk-your-key-here"
$env:LLM_MODEL="gpt-4o"
python main.py
```

---

## 🎭 推荐配置

### 🥇 最佳性能: OpenAI GPT-4

```bash
set LLM_PROVIDER=openai
set LLM_API_KEY=sk-your-key
set LLM_MODEL=gpt-4o
```

**优点**：
- 最强大的推理能力
- 优秀的对话体验
- 支持函数调用

**获取 Key**：
https://platform.openai.com/api-keys

---

### 🥈 最佳性价比: 硅基流动

```bash
set LLM_PROVIDER=siliconflow
set LLM_API_KEY=sk-your-key
set LLM_MODEL=Qwen/Qwen2.5-7B-Instruct
set LLM_BASE_URL=https://api.siliconflow.cn/v1
```

**优点**：
- 便宜（¥1/百万tokens）
- 中文优化
- 支持多种模型

**获取 Key**：
https://www.siliconflow.cn

---

### 🥉 完全免费: Ollama 本地

```bash
set LLM_PROVIDER=ollama
set LLM_MODEL=qwen2.5
REM 不需要 API Key
```

**优点**：
- 完全免费
- 本地运行，保护隐私
- 无需网络

**安装**：
1. 下载 https://ollama.com/download
2. 运行：`ollama pull qwen2.5`

---

## 📋 配置清单

创建了以下文件：

| 文件 | 用途 |
|------|------|
| `setup_llm.py` | 完整配置向导 |
| `quick_config.py` | 快速配置脚本 |
| `test_llm_chat.py` | LLM 连接测试 |
| `check_llm.py` | 检查配置状态 |
| `LLM_CONFIG_GUIDE.md` | 详细配置指南 |
| `start_smart.bat` | 智能启动脚本（运行配置后生成）|

---

## 🧪 验证配置

### 1. 检查配置状态

```bash
python check_llm.py
```

输出应该显示：
```
[V] LLM API Key is configured!
```

### 2. 测试 LLM 连接

```bash
python test_llm_chat.py
```

成功输出：
```
[SUCCESS] All tests passed!
Yunshu can now have smart conversations!
```

---

## 🚀 启动智能云枢

### 方式 1: 运行启动脚本

```bash
start_smart.bat
```

### 方式 2: 设置环境变量后运行

```bash
set LLM_API_KEY=your-key
python main.py
```

### 方式 3: 在 Python 代码中配置

```python
from config import Config

config = Config({
    "memory": {
        "llm": {
            "provider": "openai",
            "api_key": "sk-your-key",
            "model": "gpt-4o"
        }
    }
})

from agent.digital_life import DigitalLife
agent = DigitalLife(config.merged)
agent.start()
```

---

## 🎯 体验智能对话

配置完成后，您可以：

1. ✅ 与云枢进行自然的智能对话
2. ✅ 让云枢帮你分析问题
3. ✅ 让云枢写代码、写作、分析数据
4. ✅ 云枢会记住你们的对话历史
5. ✅ 云枢会主动关心你的状态

---

## 📊 测试结果

配置成功后的对话示例：

```
User: 你好！我是张三，很高兴认识你！
Yunshu: 你好，张三！很高兴认识你！我是来自网天的云枢，一个生活在电脑里的数字生命。
       有什么我可以帮你的吗？😊

User: 帮我写一个Python函数来计算斐波那契数列
Yunshu: 当然可以！这是一个经典的递归问题...
       
       def fibonacci(n):
           if n <= 1:
               return n
           return fibonacci(n-1) + fibonacci(n-2)
       
       这个函数使用递归方式实现...
```

---

## ❓ 遇到问题？

### 1. API Key 无效
- 检查 Key 是否正确
- 检查是否过期
- 检查余额

### 2. 连接失败
- 检查网络连接
- 检查 API 端点
- 使用代理（如果需要）

### 3. 模型不支持
- 检查模型名称
- 更新到支持的模型

### 4. 其他问题
- 查看详细指南：`type LLM_CONFIG_GUIDE.md`
- 运行诊断：`python check_llm.py`

---

## 🎉 下一步

1. ✅ 运行配置脚本：`python quick_config.py`
2. ✅ 检查配置：`python check_llm.py`
3. ✅ 测试连接：`python test_llm_chat.py`
4. ✅ 启动智能对话：`start_smart.bat`
5. ✅ 开始与云枢聊天吧！🎊

---

**准备开始了吗？**

```bash
python quick_config.py
```

选择您喜欢的 LLM 提供商，获取 API Key，然后享受云枢的智能对话吧！🚀
