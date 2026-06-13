# Agent 管理侧边栏设计文档

## 概述

为云枢数字生命体 Web 界面增加一个全局侧边栏组件，提供 5 个核心管理模块：
历史会话管理、技能管理、工具集成、人格配置、记忆管理。

## 架构

### 目录结构

```
agent/
├── app_server.py           # 精简 Flask 入口（仅保留路由 + API）
├── templates/
│   └── index.html          # 主 HTML 结构
├── static/
│   ├── css/
│   │   ├── main.css        # 全局样式
│   │   ├── chat.css        # 对话区域样式
│   │   ├── panorama.css    # 全景视图样式
│   │   └── sidebar.css     # 侧边栏专用样式
│   └── js/
│       ├── main.js         # 全局逻辑（标签切换、定时刷新等）
│       ├── chat.js         # 对话逻辑
│       ├── panorama.js     # 全景视图逻辑
│       └── sidebar/
│           ├── sidebar.js        # 侧边栏框架（折叠、导航、事件）
│           ├── history.js        # 模块1: 历史会话管理
│           ├── skills.js         # 模块2: 技能管理
│           ├── tools.js          # 模块3: 工具集成
│           ├── personality.js    # 模块4: 人格配置
│           └── memory.js         # 模块5: 记忆管理
├── data/
│   ├── personality.json    # 人格配置持久化
│   └── skills.json         # 技能配置持久化
```

### 页面布局

```
┌─────────────────────────────────────────────────┐
│  Topbar: 云枢 · 数字生命体   [设置] [刷新]     │
├──────────┬──────────────────────────────────────┤
│          │  Tab: 对话 | 全景                    │
│ Sidebar  │                                       │
│ 280/52px │  Main Content Area                    │
│ 5模块导航 │  (对话视图 / 全景视图)               │
│          │                                       │
├──────────┴──────────────────────────────────────┤
│  Status Bar                                      │
└─────────────────────────────────────────────────┘
```

- 侧边栏默认宽度 280px，折叠后 52px（仅图标）
- 折叠通过顶部汉堡按钮切换
- 5 个模块通过图标标签导航切换

### Flask 路由变更

```python
# 修改主页路由
@app.route("/")
def index():
    return render_template("index.html")

# 新增 API
GET/POST /api/personality        # 人格配置
GET/POST /api/skills             # 技能配置
GET/POST /api/tools/config       # 工具授权配置
DELETE   /api/history/<id>       # 删除单条历史
POST     /api/memory             # 手动添加记忆
DELETE   /api/memory/<id>        # 删除记忆
POST     /api/memory/compress    # 触发记忆压缩
```

## 模块详细设计

### 1. 历史会话管理

- **数据来源**: `_CHAT_HISTORY` + 持久化消息文件
- **功能**: 时间倒序列表、关键词搜索、日期分组、单条展开详情、删除确认
- **后端**: 基于现有 `/api/history` + 新增删除接口

### 2. 技能管理

- **数据来源**: 读写 `data/skills.json`
- **功能**: 技能卡片列表、启用/禁用切换、参数配置弹窗、添加/删除技能
- **预设技能**: 自省反思、记忆摘要、情感表达、主动建议、上下文感知

### 3. 工具集成

- **数据来源**: `agent.tools._registry` 实时读取
- **功能**: 工具列表、权限开关（允许/禁止）、使用统计、参数配置
- **后端**: 新增 `/api/tools/config` 接口

### 4. 人格配置

- **数据来源**: 读写 `data/personality.json`
- **功能**: 预设人格方案、6维滑动条调节、保存/恢复默认
- **人格维度**: 语气、情感、简练、主动、幽默、同理心

### 5. 记忆管理

- **数据来源**: 通过 MemoryManager 接口读取
- **功能**: 短期记忆列表、长期摘要展示、编辑/删除/置顶、手动添加、触发压缩

## 交互规则

- 所有删除操作弹窗确认
- 编辑操作提供成功/失败反馈提示
- 未保存修改离开时提示
- 侧边栏状态（折叠/展开、激活模块）使用 sessionStorage 保持
- 定时刷新（每 10 秒）更新侧边栏数据

## 数据模型

### skills.json

```json
{
  "skills": [
    {
      "id": "self_reflection",
      "name": "自省反思",
      "enabled": true,
      "description": "每次交互后自动反思自身状态",
      "params": { "frequency": "always", "depth": "normal" }
    }
  ]
}
```

### personality.json

```json
{
  "current_profile": "gentle_helper",
  "profiles": {
    "gentle_helper": {
      "name": "温和助人型",
      "params": {
        "tone": 0.6, "emotion": 0.7, "conciseness": 0.4,
        "initiative": 0.5, "humor": 0.3, "empathy": 0.8
      }
    }
  },
  "custom_params": {
    "tone": 0.6, "emotion": 0.7, "conciseness": 0.4,
    "initiative": 0.5, "humor": 0.3, "empathy": 0.8
  }
}
```
