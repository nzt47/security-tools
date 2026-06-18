# 🎯 云枢 LLM 配置指南

## 快速配置

### 方式一：使用配置向导（推荐）

```bash
python setup_llm.py
```

按照提示选择：
1. LLM 提供商（OpenAI / Anthropic / 硅基流动 / Ollama）
2. 模型
3. API Key

---

## 支持的 LLM 提供商

### 1. OpenAI (GPT-4) ⭐ 推荐

**优点**：
- 强大的推理能力
- 稳定的 API 服务
- 支持函数调用

**配置**：
```bash
set LLM_PROVIDER=openai
set LLM_API_KEY=sk-your-api-key
set LLM_MODEL=gpt-4o
```

**获取 API Key**：
1. 访问 https://platform.openai.com/api-keys
2. 创建新的 API Key
3. 充值 credits（GPT-4 需要付费）

---

### 2. Anthropic (Claude) ⭐ 推荐

**优点**：
- 超长上下文（200K tokens）
- 优秀的代码能力
- 安全性高

**配置**：
```bash
set LLM_PROVIDER=anthropic
set LLM_API_KEY=sk-ant-your-api-key
set LLM_MODEL=claude-3-5-sonnet-latest
```

**获取 API Key**：
1. 访问 https://console.anthropic.com/settings/keys
2. 创建 API Key
3. 选择合适的订阅计划

---

### 3. 硅基流动 (SiliconFlow) 💰 便宜

**优点**：
- 便宜的国产方案
- 支持多种模型
- 中文优化

**配置**：
```bash
set LLM_PROVIDER=siliconflow
set LLM_API_KEY=sk-your-api-key
set LLM_MODEL=Qwen/Qwen2.5-7B-Instruct
set LLM_BASE_URL=https://api.siliconflow.cn/v1
```

**获取 API Key**：
1. 访问 https://www.siliconflow.cn
2. 注册并获取 API Key
3. 充值（有免费额度）

---

### 4. Ollama (本地模型) 🆓 免费

**优点**：
- 完全免费
- 本地运行，保护隐私
- 支持多种开源模型

**配置**：
```bash
set LLM_PROVIDER=ollama
set LLM_MODEL=qwen2.5
REM 无需设置 API Key
```

**安装 Ollama**：
1. 下载 https://ollama.com/download
2. 安装并启动服务
3. 下载模型：`ollama pull qwen2.5`

---

## 🚀 快速开始

### 第一步：设置环境变量

```bash
# Windows CMD
set LLM_PROVIDER=openai
set LLM_API_KEY=sk-your-key
set LLM_MODEL=gpt-4o

# Windows PowerShell
$env:LLM_PROVIDER="openai"
$env:LLM_API_KEY="sk-your-key"
$env:LLM_MODEL="gpt-4o"
```

### 第二步：运行测试

```bash
python test_llm_chat.py
```

### 第三步：启动云枢

```bash
python main.py
```

---

## 📝 永久配置

### 方式一：创建启动脚本

创建 `start_smart.bat`：

```batch
@echo off
set LLM_PROVIDER=openai
set LLM_API_KEY=sk-your-key
set LLM_MODEL=gpt-4o
python main.py
```

双击运行即可！

### 方式二：系统环境变量

1. 右键"此电脑" → 属性
2. 高级系统设置
3. 环境变量
4. 在"用户变量"中添加：
   - `LLM_API_KEY` = `sk-your-key`
   - `LLM_PROVIDER` = `openai`
   - `LLM_MODEL` = `gpt-4o`

### 方式三：使用配置向导

```bash
python setup_llm.py
```

---

## 🧪 验证配置

### 检查环境变量

```bash
echo %LLM_API_KEY%
echo %LLM_PROVIDER%
echo %LLM_MODEL%
```

### 测试连接

```bash
python test_llm_chat.py
```

成功输出：
```
======================================================================
[SUCCESS] All tests passed!
Yunshu can now have smart conversations!
======================================================================
```

---

## ❌ 常见问题

### 1. API Key 无效

**错误**：
```
AuthenticationError: Incorrect API key provided
```

**解决**：
- 检查 API Key 是否正确
- 检查是否过期
- 检查余额是否充足

### 2. 余额不足

**错误**：
```
RateLimitError: Exceeded usage limit
```

**解决**：
- 充值 credits
- 切换到更便宜的模型
- 使用硅基流动等国产方案

### 3. 网络问题

**错误**：
```
ConnectionError: Connection failed
```

**解决**：
- 检查网络连接
- 使用代理（如果需要）
- 尝试其他 API 端点

### 4. 模型不支持

**错误**：
```
InvalidRequestError: Model not found
```

**解决**：
- 检查模型名称是否正确
- 更新到支持的模型列表

---

## 💡 推荐配置

### 最佳性价比
```bash
set LLM_PROVIDER=siliconflow
set LLM_API_KEY=sk-your-key
set LLM_MODEL=Qwen/Qwen2.5-7B-Instruct
set LLM_BASE_URL=https://api.siliconflow.cn/v1
```

### 最佳性能
```bash
set LLM_PROVIDER=openai
set LLM_API_KEY=sk-your-key
set LLM_MODEL=gpt-4o
```

### 完全免费
```bash
set LLM_PROVIDER=ollama
set LLM_MODEL=qwen2.5
```

---

## 📚 相关文件

- `setup_llm.py` - LLM 配置向导
- `test_llm_chat.py` - LLM 连接测试
- `config.py` - 云枢配置文件
- `agent/digital_life.py` - 云枢主类

---

## 🎯 下一步

1. ✅ 运行配置向导：`python setup_llm.py`
2. ✅ 测试连接：`python test_llm_chat.py`
3. ✅ 启动智能对话：`python main.py`
4. ✅ 享受云枢的智能对话吧！🎉
