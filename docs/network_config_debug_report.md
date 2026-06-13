# 网络配置调试报告
生成时间: 2026-06-09

## 一、前端日志汇总

### 1.1 正常流程日志（成功场景）

```
[网络配置] ====== 开始应用配置 ======
[网络配置] applyNetworkConfig 被调用
[网络配置] 收集到的配置: {
  "llm": {
    "enabled": true,
    "provider": "openai",
    "api_key": "***",
    "model": "gpt-4",
    "api_endpoint": "",
    "timeout": 60,
    "max_retries": 3
  },
  ...
}
[网络配置] [步骤1] 正在保存网络配置到 /api/network-config...
[网络配置] [请求] POST /api/network-config (超时: 30000ms)
[网络配置] [响应] /api/network-config 状态: 200 耗时: 123ms
[网络配置] [步骤1] 响应状态码: 200
[网络配置] [步骤1] 完整响应体: {
  "ok": true,
  "config": { ... },
  "message": "配置已保存"
}
[网络配置] [步骤1] ✓ 保存成功
[网络配置] [判断] isMaskedApiKey= false shouldConfigureLlm= true
[网络配置] [步骤2] 正在配置 LLM...
[网络配置] [请求] POST /api/config (超时: 60000ms)
[网络配置] [响应] /api/config 状态: 200 耗时: 3456ms
[网络配置] [步骤2] 响应状态码: 200
[网络配置] [步骤2] 原始响应文本: {"ok":true,"message":"LLM已配置并测试成功"}
[网络配置] [步骤2] ✓ LLM 配置成功!
[网络配置] [步骤2] 完整成功响应体: {
  "ok": true,
  "message": "LLM已配置并测试成功"
}
[网络配置] [步骤2] ✓ LLM 已连接并生效
[网络配置] ====== 配置应用完成 ======
```

### 1.2 超时场景日志

```
[网络配置] [请求] POST /api/config (超时: 60000ms)
[网络配置] [错误] /api/config 失败 (耗时: 60012ms)
[网络配置] [超时] /api/config 请求超时，超过 60000ms 限制
[网络配置] ====== applyNetworkConfig 异常 ======
[网络配置] 异常类型: Error
[网络配置] 异常消息: 请求超时（60秒）
[网络配置] 异常堆栈: Error: 请求超时（60秒）
    at apiFetchWithTimeout (network-config.js:231)
    ...
```

### 1.3 API Key 脱敏场景日志

```
[网络配置] [判断] isMaskedApiKey= true shouldConfigureLlm= false
[网络配置] [步骤2] 跳过 LLM 配置（API Key 是脱敏值，请重新输入完整 API Key）
[网络配置] ====== 配置应用完成 ======
```

### 1.4 LLM 配置失败场景日志

```
[网络配置] [步骤2] 响应状态码: 400
[网络配置] [步骤2] 原始响应文本: {"ok":false,"error":"API Key无效"}
[网络配置] [步骤2] JSON 解析失败: SyntaxError: Unexpected token o in JSON at position 0
[网络配置] [步骤2] ✗ LLM 配置失败: {"ok":false,"error":"API Key无效"}
```

---

## 二、后端日志（服务器控制台）

### 2.1 预期后端日志

```
📡 API 请求日志 [api_network_config]
[REQUEST] 接口: api_network_config
[REQUEST] 方法: POST
[REQUEST] 路径: /api/network-config
[REQUEST] 参数: (已脱敏)
[RESPONSE] api_network_config: 状态=200 耗时=0.123s
[网络配置] 更新配置: 开始
[网络配置] 更新配置: 完成

📡 API 请求日志 [api_config]
[REQUEST] 接口: api_config
[REQUEST] 方法: POST
[REQUEST] 路径: /api/config
[REQUEST] 参数: {"provider":"openai","api_key":"sk-***","model":"gpt-4"}
[LLM] 正在配置 LLM: provider=openai
[LLM] 发送测试消息: {"ok":true}
[LLM] LLM 配置完成
[RESPONSE] api_config: 状态=200 耗时=3.456s
```

---

## 三、前端状态检查方法

### 3.1 如果 LLM 配置成功但页面没变化

打开浏览器控制台，运行以下命令：

```javascript
// 1. 检查配置缓存
console.log('配置缓存:', __networkConfigCache);

// 2. 检查脏数据标记
console.log('脏数据标记:', __networkConfigDirty);

// 3. 检查页面状态提示
const statusEl = document.getElementById('nc-status');
console.log('状态元素:', {
  display: statusEl.style.display,
  textContent: statusEl.textContent,
  className: statusEl.className,
  background: statusEl.style.background,
  color: statusEl.style.color
});

// 4. 手动重新加载配置
loadNetworkConfig().then(() => console.log('配置已重新加载'));

// 5. 检查 LLM 配置是否生效（通过测试对话）
apiFetch('/api/config').then(r => r.json()).then(console.log);
```

### 3.2 验证 LLM 是否真正生效

在对话页面发送一条消息，查看服务器日志中是否有 LLM 调用记录：

```
[LLM] 请求: {"model":"gpt-4","messages":[{"role":"user","content":"测试"}]}
[LLM] 响应: 耗时=1.234s
```

---

## 四、常见问题排查

| 现象 | 原因 | 解决方案 |
|------|------|----------|
| 点击无反应 | JS 加载失败 | 按 F12 查看 Console 是否有 SyntaxError |
| 保存失败 | 后端未启动 | 检查服务器终端是否运行中 |
| LLM 配置失败 | API Key 无效 | 检查 Key 是否正确，提供商是否匹配 |
| 页面卡住 | 网络超时 | 检查网络连接，适当增加超时时间 |
| 配置不生效 | API Key 脱敏 | 重新输入完整 API Key，不要使用显示的 *** 值 |
| 状态不更新 | 缓存未刷新 | 调用 loadNetworkConfig() 重新加载 |

---

## 五、代码位置

| 文件 | 行号 | 说明 |
|------|------|------|
| network-config.js | 210-246 | apiFetchWithTimeout 函数 |
| network-config.js | 251-360 | applyNetworkConfig 函数 |
| app_server.py | 820-870 | /api/config 路由 |
| app_server.py | 1084-1157 | /api/network-config 路由 |
| digital_life.py | 1906-1942 | configure_llm 方法 |

---

## 六、调试步骤

1. 刷新页面 (Ctrl+F5)
2. 按 F12 打开控制台
3. 进入 🌐 网络配置
4. 输入完整 API Key（不要用 *** 值）
5. 点击 ⚡ 应用并即时生效
6. 复制控制台所有 `[网络配置]` 日志
7. 对比本报告中的预期日志
8. 如不符，将日志提供给后端排查
