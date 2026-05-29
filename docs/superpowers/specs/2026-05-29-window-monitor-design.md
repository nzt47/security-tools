# 窗口活动监控功能设计文档

## 概述

在记忆管理模块中扩展 OS 级窗口活动监控功能，通过 Win32 API 实时追踪前台窗口切换事件，将数据存入 BlackBox 日志系统，并在记忆管理面板中提供实时事件流和基础统计分析。

## 设计目标

- 实时监控 Windows 操作系统级别的窗口切换事件
- 记录每个窗口的使用时长和切换频率
- 提供简洁的使用时长统计排行
- 与现有记忆管理体系（BlackBox / MemoryManager）紧密集成
- 支持可配置的监控参数
- UI 风格与整体界面保持一致

---

## 架构

```
WindowSensor (Win32 API 轮询)
    ↓ 窗口切换事件
BlackBox (event_type="window_event")
    ↓ 日志持久化
MemoryManager (save_log / query_logs)
    ↓ API 暴露
GET /api/memory/windows/events  → 实时事件流
GET /api/memory/windows/stats   → 时长统计
    ↓ 前端消费
记忆管理面板 → 🪟 窗口活动子页
```

### 组件清单

| 组件 | 文件 | 说明 |
|------|------|------|
| WindowSensor | `sensor/window_sensor.py`（新建） | 通过 Win32 API 轮询前台窗口 |
| 配置存储 | `data/window_config.json`（新建） | 监控参数持久化 |
| API 路由 | `app_server.py`（修改） | 新增 3 个窗口监控端点 |
| 记忆面板 UI | `static/js/sidebar/memory.js`（修改） | 新增窗口活动子页 |
| 面板样式 | `static/css/sidebar.css`（修改） | 子页标签、事件流、进度条样式 |

---

## 数据模型

### 窗口事件（BlackBox 日志）

```json
{
  "id": "bb_0123",
  "timestamp": "2026-05-29T14:32:05",
  "event_type": "window_event",
  "data": {
    "action": "switch",
    "from_process": "Code.exe",
    "from_title": "VS Code",
    "to_process": "chrome.exe",
    "to_title": "Google Chrome",
    "duration_sec": 245
  }
}
```

- `action`: switch（切换）/ idle_start（空闲开始）/ idle_end（空闲结束）
- `duration_sec`: 在上一窗口停留的秒数
- 首次启动时 `from_*` 字段为 null

### 统计响应（/api/memory/windows/stats）

```json
{
  "total_duration_sec": 3600,
  "total_switches": 24,
  "since": "2026-05-29T10:00:00",
  "apps": [
    {
      "process": "chrome.exe",
      "title": "Google Chrome",
      "duration_sec": 1620,
      "switch_count": 12,
      "percentage": 45
    }
  ]
}
```

### 配置（data/window_config.json）

```json
{
  "enabled": true,
  "poll_interval_sec": 1,
  "max_events": 500,
  "idle_timeout_sec": 300,
  "ignore_processes": []
}
```

---

## API 设计

### GET /api/memory/windows/events

返回最近 N 条窗口切换事件（默认 50 条，最大 500）。

查询参数: `?limit=50`

### GET /api/memory/windows/stats

返回当前会话的窗口使用统计（时长、频率、占比）。

查询参数: `?since=ISO时间戳`（可选，默认今日 00:00）

### GET /api/memory/windows/config

返回当前监控配置。

### POST /api/memory/windows/config

更新监控配置。Body: `{ "enabled": true, "poll_interval_sec": 2, ... }`

### POST /api/memory/windows/clear

清空窗口事件记录。

---

## 前端设计

### 记忆管理面板结构调整

现有记忆管理面板顶部新增两个子页标签：

```
┌──────────────────────────┐
│ 🧠 记忆管理           ✕ │
├──────────────────────────┤
│ 📋 记忆  │ 🪟 窗口活动  │
├──────────────────────────┤
│                          │
│    (子页内容区)           │
│                          │
└──────────────────────────┘
```

### 窗口活动子页内容

1. **监控状态栏** — 绿色指示灯 + "监控运行中/已停止" + 开关按钮
2. **当前活跃窗口** — 蓝色左边框卡片，显示窗口标题、进程名、已持续时长
3. **实时事件流** — 最近 50 条切换事件，格式: `时间 · A → B · 停留时长`，10 秒自动刷新
4. **今日使用时长排行** — 按累计时长降序，带彩色进度条（蓝/绿/黄/紫）
5. **操作按钮** — ⚙ 监控参数（弹窗配置）/ 清空记录（确认弹窗）

### 配置弹窗

弹出式表单，包含：
- 启停开关
- 轮询间隔（0.5s / 1s / 2s / 5s 下拉选择）
- 最大事件数（100 / 200 / 500 / 1000）
- 空闲超时（3min / 5min / 10min / 30min）
- 忽略进程列表（逗号分隔文本输入）

---

## 实现要点

1. **WindowSensor** 使用 `win32gui` / `win32process` / `psutil` 获取窗口信息
2. 每 `poll_interval_sec` 秒检查一次前台窗口，与上次不同时记录切换事件
3. 切换事件通过 `_lingxi._memory.save_log("window_event", data)` 写入 BlackBox
4. 统计 API 通过扫描 BlackBox 日志文件中 `event_type="window_event"` 的记录，按 `to_process` 分组聚合时长和次数
5. 前端子页切换时才开始加载窗口数据（懒加载），减少不必要请求
6. 配置变更即时生效，无需重启

## 文件变更清单

| 操作 | 文件 |
|------|------|
| 新建 | `sensor/window_sensor.py` |
| 新建 | `data/window_config.json` |
| 修改 | `app_server.py` — 添加 API 路由 + 初始化 WindowSensor |
| 修改 | `static/js/sidebar/memory.js` — 添加窗口活动子页 |
| 修改 | `static/css/sidebar.css` — 添加子页标签、进度条样式 |
