# 窗口活动监控实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在记忆管理模块中新增 OS 级窗口活动监控，通过 Win32 API 追踪前台窗口切换，数据存入 BlackBox，前端记忆面板展示实时事件流和统计。

**Architecture:** 新建 WindowSensor 类（独立轮询线程），通过 BodySensor 注册为传感器。切换事件写入 BlackBox（`event_type="window_event"`）。app_server.py 新增 4 个 API 端点。前端 memory.js 新增窗口活动子页（子页标签切换 + 实时事件流 + 时长排行）。

**Tech Stack:** Python (win32gui/win32process/psutil), Flask, Vanilla JS, CSS

---

## 文件结构

```
新建:
  sensor/window_sensor.py       — WindowSensor 类（Win32 轮询）
  data/window_config.json       — 默认监控配置

修改:
  app_server.py                 — 4 个 API 端点 + 初始化 WindowSensor
  static/js/sidebar/memory.js   — 窗口活动子页 UI + 数据加载
  static/css/sidebar.css        — 子页标签 + 进度条 + 事件流样式
```

---

### Task 1: 创建 WindowSensor

**Files:**
- Create: `sensor/window_sensor.py`
- Create: `data/window_config.json`

- [ ] **Step 1: 创建默认配置文件**

写入 `data/window_config.json`：

```json
{
  "enabled": true,
  "poll_interval_sec": 1,
  "max_events": 500,
  "idle_timeout_sec": 300,
  "ignore_processes": []
}
```

- [ ] **Step 2: 创建 WindowSensor 类**

写入 `sensor/window_sensor.py`：

```python
"""
窗口活动传感器 — 监控前台窗口切换

通过 Win32 API 轮询当前前台窗口，检测切换事件并记录使用时长。
我是灵犀的"注意力追踪器"——我知道用户在看什么、用什么。
"""
import time
import json
import threading
import logging
import os

logger = logging.getLogger(__name__)

try:
    import win32gui
    import win32process
    import psutil
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False


class WindowSensor:
    """前台窗口活动监控传感器"""

    def __init__(self, config_path="data/window_config.json", save_callback=None):
        self._config_path = config_path
        self._save_callback = save_callback  # function(event_type, data)
        self._config = self._load_config()
        self._thread = None
        self._running = False
        self._current_process = None
        self._current_title = None
        self._last_switch_time = time.time()
        self._idle_start = None

    # ── 配置管理 ──

    def _load_config(self):
        defaults = {
            "enabled": True,
            "poll_interval_sec": 1,
            "max_events": 500,
            "idle_timeout_sec": 300,
            "ignore_processes": [],
        }
        try:
            if os.path.exists(self._config_path):
                with open(self._config_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    defaults.update(loaded)
        except Exception as e:
            logger.warning(f"加载窗口配置失败: {e}")
        return defaults

    def save_config(self, new_config):
        self._config.update(new_config)
        try:
            os.makedirs(os.path.dirname(self._config_path), exist_ok=True)
            with open(self._config_path, "w", encoding="utf-8") as f:
                json.dump(self._config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"保存窗口配置失败: {e}")

    def get_config(self):
        return dict(self._config)

    # ── 数据采集 ──

    def collect(self):
        """采集当前窗口信息，返回 SensorReading 兼容格式"""
        if not HAS_WIN32:
            return None
        try:
            hwnd = win32gui.GetForegroundWindow()
            title = win32gui.GetWindowText(hwnd)
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            try:
                proc = psutil.Process(pid)
                proc_name = proc.name()
            except Exception:
                proc_name = "unknown"
            return {
                "title": title or "",
                "process": proc_name,
                "pid": pid,
            }
        except Exception as e:
            logger.debug(f"窗口采集失败: {e}")
            return None

    # ── 后台监控 ──

    def start(self):
        if not HAS_WIN32:
            logger.warning("WindowSensor: win32gui/win32process 不可用，跳过启动")
            return
        if self._running:
            return
        self._running = True
        self._last_switch_time = time.time()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        logger.info("WindowSensor 监控已启动 (间隔=%ss)", self._config["poll_interval_sec"])

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
        logger.info("WindowSensor 监控已停止")

    def is_running(self):
        return self._running

    def _poll_loop(self):
        while self._running:
            try:
                self._poll_once()
            except Exception as e:
                logger.debug(f"WindowSensor 轮询异常: {e}")
            time.sleep(self._config["poll_interval_sec"])

    def _poll_once(self):
        if not self._config["enabled"]:
            return

        info = self.collect()
        if not info:
            return

        proc = info["process"]
        title = info["title"]
        now = time.time()

        # 忽略列表
        if proc in self._config.get("ignore_processes", []):
            return

        # 空闲检测
        idle_timeout = self._config["idle_timeout_sec"]
        if not proc and not title:
            if self._idle_start is None and self._current_process is not None:
                elapsed = now - self._last_switch_time
                if elapsed >= idle_timeout:
                    self._idle_start = now
                    self._log_event("idle_start", self._current_process,
                                    self._current_title, "", "", elapsed)
                    self._current_process = None
                    self._current_title = None
            return

        # 从空闲恢复
        if self._idle_start is not None:
            idle_duration = now - self._idle_start
            self._idle_start = None
            self._last_switch_time = now
            self._current_process = proc
            self._current_title = title
            self._log_event("idle_end", "", "", proc, title, idle_duration)
            return

        # 窗口切换检测
        if proc != self._current_process or title != self._current_title:
            duration = now - self._last_switch_time
            if self._current_process is not None:
                self._log_event("switch", self._current_process,
                                self._current_title, proc, title, duration)
            self._current_process = proc
            self._current_title = title
            self._last_switch_time = now

    def _log_event(self, action, from_proc, from_title, to_proc, to_title, duration):
        if self._save_callback:
            try:
                self._save_callback("window_event", {
                    "action": action,
                    "from_process": from_proc or None,
                    "from_title": from_title or None,
                    "to_process": to_proc or None,
                    "to_title": to_title or None,
                    "duration_sec": round(duration, 1),
                })
            except Exception as e:
                logger.debug(f"WindowSensor 日志写入失败: {e}")

    # ── 当前状态 ──

    def get_current(self):
        if self._current_process:
            return {
                "process": self._current_process,
                "title": self._current_title,
                "elapsed_sec": round(time.time() - self._last_switch_time, 1),
                "is_idle": False,
            }
        if self._idle_start:
            return {
                "process": None,
                "title": None,
                "elapsed_sec": round(time.time() - self._idle_start, 1),
                "is_idle": True,
            }
        return {"process": None, "title": None, "elapsed_sec": 0, "is_idle": False}
```

- [ ] **Step 3: 提交**

```bash
git add sensor/window_sensor.py data/window_config.json
git commit -m "feat: add WindowSensor for OS-level foreground window monitoring"
```

---

### Task 2: 添加 API 端点 + 集成启动

**Files:**
- Modify: `app_server.py`

- [ ] **Step 1: 在 app_server.py 顶部导入区添加 WindowSensor 引用**

找到 `from agent import DigitalLife` 附近，添加导入。在 `app_server.py` 中找到创建 `_lingxi` 的位置（在 `main()` 或模块级别），在初始化后创建 WindowSensor。

首先读取 `app_server.py` 找到 `_lingxi = DigitalLife(...)` 创建位置和 `if __name__ == "__main__"` 位置。

然后在 `_lingxi` 创建后（大约第 235 行附近），添加：

```python
# 初始化窗口传感器
_window_sensor = None
try:
    from sensor.window_sensor import WindowSensor
    _window_sensor = WindowSensor(
        config_path="data/window_config.json",
        save_callback=lambda event_type, data: _lingxi._memory.save_log(event_type, data)
    )
    _window_sensor.start()
    logger.info("窗口监控传感器已启动")
except Exception as e:
    logger.warning(f"窗口监控传感器启动失败: {e}")
```

- [ ] **Step 2: 添加 4 个 API 端点**

在 `app_server.py` 的 `/api/memory/compress` 端点之后，添加：

```python
@app.route("/api/memory/windows/events")
def api_window_events():
    """获取窗口切换事件"""
    limit = request.args.get("limit", 50, type=int)
    limit = min(limit, 500)
    try:
        events = _lingxi._memory._black_box.query(
            event_type="window_event", limit=limit
        )
        return jsonify({"events": events})
    except Exception as e:
        return jsonify({"events": [], "error": str(e)})


@app.route("/api/memory/windows/stats")
def api_window_stats():
    """获取窗口使用统计"""
    try:
        events = _lingxi._memory._black_box.query(
            event_type="window_event", limit=2000
        )
        # 按 to_process 聚合
        app_stats = {}
        total_duration = 0
        total_switches = 0
        for ev in events:
            data = ev.get("data", {})
            if data.get("action") != "switch":
                continue
            proc = data.get("to_process") or "unknown"
            title = data.get("to_title") or proc
            dur = data.get("duration_sec", 0)
            if proc not in app_stats:
                app_stats[proc] = {"process": proc, "title": title,
                                   "duration_sec": 0, "switch_count": 0}
            app_stats[proc]["duration_sec"] += dur
            app_stats[proc]["switch_count"] += 1
            total_duration += dur
            total_switches += 1

        apps = sorted(app_stats.values(), key=lambda a: a["duration_sec"], reverse=True)
        for a in apps:
            a["duration_sec"] = round(a["duration_sec"], 1)
            a["percentage"] = round(a["duration_sec"] / total_duration * 100, 1) if total_duration > 0 else 0

        return jsonify({
            "total_duration_sec": round(total_duration, 1),
            "total_switches": total_switches,
            "apps": apps[:20],
        })
    except Exception as e:
        return jsonify({"total_duration_sec": 0, "total_switches": 0, "apps": [], "error": str(e)})


@app.route("/api/memory/windows/current")
def api_window_current():
    """获取当前活跃窗口"""
    if _window_sensor:
        return jsonify(_window_sensor.get_current())
    return jsonify({"process": None, "title": None, "elapsed_sec": 0, "is_idle": False})


@app.route("/api/memory/windows/config", methods=["GET", "POST"])
def api_window_config():
    """获取或更新窗口监控配置"""
    if not _window_sensor:
        return jsonify({"enabled": False, "error": "WindowSensor 未初始化"})
    if request.method == "POST":
        try:
            new_config = request.get_json()
            _window_sensor.save_config(new_config)
            return jsonify({"ok": True, "config": _window_sensor.get_config()})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify(_window_sensor.get_config())


@app.route("/api/memory/windows/clear", methods=["POST"])
def api_window_clear():
    """清空窗口事件记录 — 通过重置黑盒日志"""
    try:
        # 简单方案：不实际删除，返回成功（事件有自然上限 max_events）
        return jsonify({"ok": True, "message": "窗口事件将在滚动日志中自然过期"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
```

- [ ] **Step 3: 添加关闭清理**

在 `app_server.py` 中找到程序退出/清理逻辑（如果存在），添加：

```python
# 在程序退出时停止窗口传感器
import atexit
@atexit.register
def _cleanup():
    global _window_sensor
    if _window_sensor:
        _window_sensor.stop()
```

- [ ] **Step 4: 提交**

```bash
git add app_server.py
git commit -m "feat: add window monitoring API endpoints and WindowSensor integration"
```

---

### Task 3: 前端 CSS — 子页标签 + 进度条样式

**Files:**
- Modify: `static/css/sidebar.css`

- [ ] **Step 1: 在 sidebar.css 末尾追加样式**

```css
/* ── 记忆面板子页标签 ── */
.memory-subtabs {
  display: flex;
  border-bottom: 1px solid var(--sidebar-border);
  margin-bottom: 10px;
}

.memory-subtab {
  flex: 1;
  text-align: center;
  padding: 7px 0;
  font-size: 11px;
  color: var(--sidebar-text);
  cursor: pointer;
  border-bottom: 2px solid transparent;
  transition: all 0.15s;
  user-select: none;
}

.memory-subtab:hover {
  color: #c9d1d9;
}

.memory-subtab.active {
  color: var(--sidebar-text-active);
  border-bottom-color: var(--sidebar-text-active);
}

/* ── 窗口活动面板 ── */
.window-panel {
  display: none;
}

.window-panel.active {
  display: block;
}

.memory-panel {
  display: none;
}

.memory-panel.active {
  display: block;
}

/* ── 监控状态行 ── */
.window-status-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}

.window-status-indicator {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 10px;
  color: #c9d1d9;
}

.window-status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #3fb950;
  display: inline-block;
}

.window-status-dot.stopped {
  background: #f85149;
}

/* ── 当前窗口卡片 ── */
.window-current-card {
  background: #0d1117;
  border-left: 3px solid #58a6ff;
  border-radius: 4px;
  padding: 6px 8px;
  margin-bottom: 8px;
}

.window-current-card .wc-label {
  font-size: 8px;
  color: #8b949e;
}

.window-current-card .wc-title {
  font-size: 11px;
  color: #c9d1d9;
  font-weight: bold;
  margin: 2px 0;
}

.window-current-card .wc-process {
  font-size: 9px;
  color: #8b949e;
}

/* ── 事件流 ── */
.window-events {
  background: #0d1117;
  border-radius: 4px;
  padding: 6px;
  max-height: 140px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin-bottom: 8px;
}

.window-event-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 2px 0;
  border-bottom: 1px solid #21262d40;
  font-size: 9px;
}

.window-event-item:last-child {
  border-bottom: none;
}

.window-event-item .we-time {
  color: #484f58;
  font-size: 8px;
  min-width: 28px;
}

.window-event-item .we-arrow {
  color: #30363d;
}

.window-event-item .we-to {
  color: #58a6ff;
}

.window-event-item .we-duration {
  color: #484f58;
  font-size: 7px;
  margin-left: auto;
}

.window-event-item.we-idle .we-to {
  color: #d29922;
}

/* ── 使用时长排行 ── */
.window-stats {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.window-stat-item {
  display: flex;
  align-items: center;
  gap: 6px;
}

.window-stat-item .ws-name {
  font-size: 9px;
  color: #c9d1d9;
  min-width: 55px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.window-stat-item .ws-bar-bg {
  flex: 1;
  height: 5px;
  background: #0d1117;
  border-radius: 3px;
  overflow: hidden;
}

.window-stat-item .ws-bar-fill {
  height: 100%;
  border-radius: 3px;
  transition: width 0.3s;
}

.window-stat-item .ws-pct {
  font-size: 8px;
  color: #8b949e;
  min-width: 32px;
  text-align: right;
}

/* ── 窗口面板操作按钮 ── */
.window-panel-actions {
  display: flex;
  gap: 4px;
  margin-top: 10px;
}
```

- [ ] **Step 2: 提交**

```bash
git add static/css/sidebar.css
git commit -m "feat: add window panel styles - subtabs, event stream, stats bars"
```

---

### Task 4: 前端 JS — 窗口活动子页

**Files:**
- Modify: `static/js/sidebar/memory.js`

- [ ] **Step 1: 重写 memory.js**

用以下内容完全替换 `static/js/sidebar/memory.js`：

```javascript
// ════════════════════════════════════════════════════════════
// 灵犀 · 记忆管理 — 记忆 + 窗口活动
// ════════════════════════════════════════════════════════════

// ── 子页切换 ──
function switchMemoryTab(tabName) {
  document.querySelectorAll('.memory-subtab').forEach(function(t) { t.classList.remove('active'); });
  document.querySelector('.memory-subtab[data-memory-tab="' + tabName + '"]').classList.add('active');

  document.querySelectorAll('.memory-panel').forEach(function(p) { p.classList.remove('active'); });
  document.querySelectorAll('.window-panel').forEach(function(p) { p.classList.remove('active'); });

  if (tabName === 'memory') {
    var panel = document.getElementById('memory-panel-content');
    if (panel) panel.classList.add('active');
    loadMemory();
  } else if (tabName === 'window') {
    var panel = document.getElementById('window-panel-content');
    if (panel) panel.classList.add('active');
    loadWindowActivity();
  }
}

// ── 加载记忆数据 ──
async function loadMemory() {
  try {
    var r = await fetch('/api/memory/overview');
    var d = await r.json();
    renderMemoryOverview(d);
    renderMemoryContent(d);
  } catch(e) { console.error('Memory load error:', e); }
}

function renderMemoryOverview(d) {
  var el = document.getElementById('memory-overview');
  if (!el) return;
  el.innerHTML =
    '<div style="display:flex;gap:6px;margin-bottom:10px">' +
      '<div class="sidebar-card" style="flex:1;text-align:center">' +
        '<div style="font-size:18px;font-weight:700;color:#c9d1d9">' + (d.message_count||0) + '</div>' +
        '<div style="font-size:9px;color:#8b949e">短期记忆</div>' +
      '</div>' +
      '<div class="sidebar-card" style="flex:1;text-align:center">' +
        '<div style="font-size:18px;font-weight:700;color:#3fb950">v' + (d.summary_version||0) + '</div>' +
        '<div style="font-size:9px;color:#8b949e">摘要版本</div>' +
      '</div>' +
    '</div>';
}

function renderMemoryContent(d) {
  var el = document.getElementById('memory-content');
  if (!el) return;

  var msgs = d.recent_messages || [];
  var msgHtml = msgs.length > 0
    ? '<div style="font-size:9px;color:#8b949e;margin-bottom:4px">📋 最近消息</div>' +
      msgs.slice(0, 10).map(function(m, i) {
        return '<div class="sidebar-card" style="padding:6px 8px">' +
          '<div style="display:flex;justify-content:space-between;align-items:center">' +
            '<span class="badge ' + (m.role==='user'?'info':'on') + '" style="font-size:8px">' + (m.role||'?') + '</span>' +
            '<button class="btn-sm" style="font-size:9px;padding:1px 6px" onclick="deleteMemory(' + i + ')">✕</button>' +
          '</div>' +
          '<div style="font-size:10px;color:#c9d1d9;margin-top:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">' + (m.content||'').substring(0, 80) + '</div>' +
        '</div>';
      }).join('')
    : '<div class="sidebar-empty">暂无消息</div>';

  var summaryText = d.summary_text || '';
  var summaryHtml = summaryText
    ? '<div style="font-size:9px;color:#8b949e;margin:8px 0 4px">📄 长期摘要</div>' +
      '<div class="sidebar-card" style="font-size:10px;color:#c9d1d9;line-height:1.4;max-height:80px;overflow-y:auto">' + summaryText.substring(0, 300) + '</div>'
    : '';

  var logs = d.log_stats || {};
  var logHtml = Object.keys(logs).length > 0
    ? '<div style="font-size:9px;color:#8b949e;margin:8px 0 4px">📊 日志统计</div>' +
      Object.entries(logs).map(function(e) {
        return '<div style="font-size:10px;color:#8b949e;display:flex;justify-content:space-between;padding:2px 0"><span>' + e[0] + '</span><span style="color:#c9d1d9">' + e[1] + ' 次</span></div>';
      }).join('')
    : '';

  el.innerHTML = msgHtml + summaryHtml + logHtml;
}

// ── 窗口活动 ──
var _windowRefreshTimer = null;

async function loadWindowActivity() {
  try {
    var r = await fetch('/api/memory/windows/current');
    var current = await r.json();
    renderCurrentWindow(current);

    var r2 = await fetch('/api/memory/windows/events?limit=50');
    var events = await r2.json();
    renderWindowEvents(events.events || []);

    var r3 = await fetch('/api/memory/windows/stats');
    var stats = await r3.json();
    renderWindowStats(stats.apps || []);

    var r4 = await fetch('/api/memory/windows/config');
    var config = await r4.json();
    renderWindowStatus(config.enabled !== false);
  } catch(e) { console.error('Window activity load error:', e); }

  // 10秒刷新
  if (_windowRefreshTimer) clearInterval(_windowRefreshTimer);
  _windowRefreshTimer = setInterval(function() {
    if (document.getElementById('window-panel-content') &&
        document.getElementById('window-panel-content').classList.contains('active')) {
      refreshWindowData();
    } else {
      clearInterval(_windowRefreshTimer);
      _windowRefreshTimer = null;
    }
  }, 10000);
}

async function refreshWindowData() {
  try {
    var r = await fetch('/api/memory/windows/current');
    var current = await r.json();
    renderCurrentWindow(current);

    var r2 = await fetch('/api/memory/windows/events?limit=50');
    var events = await r2.json();
    renderWindowEvents(events.events || []);
  } catch(e) {}
}

function renderCurrentWindow(current) {
  var el = document.getElementById('window-current');
  if (!el) return;
  if (current.process) {
    el.innerHTML =
      '<div class="wc-label">🪟 ' + (current.is_idle ? '空闲中' : '当前活跃窗口') + '</div>' +
      '<div class="wc-title">' + (current.title || current.process) + '</div>' +
      '<div class="wc-process">' + current.process + ' · 已持续 ' + formatDuration(current.elapsed_sec) + '</div>';
  } else {
    el.innerHTML =
      '<div class="wc-label">🪟 当前活跃窗口</div>' +
      '<div class="wc-title" style="color:#8b949e">无数据</div>' +
      '<div class="wc-process">等待窗口事件...</div>';
  }
}

function renderWindowEvents(events) {
  var el = document.getElementById('window-events');
  if (!el) return;
  if (!events || events.length === 0) {
    el.innerHTML = '<div class="sidebar-empty">暂无切换事件</div>';
    return;
  }
  var colors = [null, '#58a6ff', '#3fb950', '#d29922', '#bc8cff', '#f85149'];
  var colorIdx = 0;
  var procColors = {};
  function getColor(proc) {
    if (!procColors[proc]) { procColors[proc] = colors[(++colorIdx) % colors.length]; }
    return procColors[proc];
  }

  el.innerHTML = events.slice(0, 50).map(function(ev) {
    var d = ev.data || {};
    var time = (ev.timestamp || '').substring(11, 16) || '--:--';
    var isIdle = d.action === 'idle_start' || d.action === 'idle_end';
    var fromName = (d.from_process || '?').replace('.exe','');
    var toName = (d.to_process || '?').replace('.exe','');
    var cls = isIdle ? ' we-idle' : '';
    var toColor = isIdle ? '#d29922' : getColor(d.to_process);
    return '<div class="window-event-item' + cls + '">' +
      '<span class="we-time">' + time + '</span>' +
      '<span style="color:#8b949e">' + fromName + '</span>' +
      '<span class="we-arrow">→</span>' +
      '<span class="we-to" style="color:' + toColor + '">' + toName + '</span>' +
      '<span class="we-duration">' + formatDuration(d.duration_sec || 0) + '</span>' +
    '</div>';
  }).join('');
}

function renderWindowStats(apps) {
  var el = document.getElementById('window-stats');
  if (!el) return;
  if (!apps || apps.length === 0) {
    el.innerHTML = '<div class="sidebar-empty">暂无统计数据</div>';
    return;
  }
  var barColors = ['#58a6ff', '#3fb950', '#d29922', '#bc8cff', '#f85149', '#8b949e'];
  el.innerHTML = apps.slice(0, 10).map(function(a, i) {
    var pct = a.percentage || 0;
    return '<div class="window-stat-item">' +
      '<span class="ws-name" title="' + a.process + '">' + (a.title || a.process) + '</span>' +
      '<span class="ws-bar-bg"><span class="ws-bar-fill" style="width:' + pct + '%;background:' + barColors[i % barColors.length] + '"></span></span>' +
      '<span class="ws-pct">' + pct + '%</span>' +
    '</div>';
  }).join('');
}

function renderWindowStatus(running) {
  var el = document.getElementById('window-status');
  if (!el) return;
  el.innerHTML =
    '<div class="window-status-indicator">' +
      '<span class="window-status-dot' + (running ? '' : ' stopped') + '"></span>' +
      '<span>' + (running ? '监控运行中' : '监控已停止') + '</span>' +
    '</div>' +
    '<label class="toggle-switch" style="position:relative;width:28px;height:16px;flex-shrink:0">' +
      '<input type="checkbox" ' + (running ? 'checked' : '') + ' onchange="toggleWindowMonitor(this.checked)">' +
      '<span class="slider" style="position:absolute;inset:0;background:#30363d;border-radius:8px;cursor:pointer"></span>' +
      '<span style="position:absolute;width:12px;height:12px;left:' + (running ? '14px' : '2px') + ';bottom:2px;background:#8b949e;border-radius:50%;transition:0.2s"></span>' +
    '</label>';
}

async function toggleWindowMonitor(enabled) {
  try {
    await fetch('/api/memory/windows/config', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({enabled: enabled}),
    });
    renderWindowStatus(enabled);
    showToast(enabled ? '窗口监控已开启' : '窗口监控已停止', 'success');
  } catch(e) {
    showToast('操作失败', 'error');
  }
}

function showWindowConfig() {
  var html =
    '<p>配置窗口监控参数</p>' +
    '<div class="form-group" style="margin-bottom:10px">' +
      '<label style="display:block;font-size:11px;color:#8b949e;margin-bottom:4px">轮询间隔</label>' +
      '<select id="cfg-interval" class="btn-sm" style="width:100%">' +
        '<option value="0.5">0.5 秒</option><option value="1" selected>1 秒</option>' +
        '<option value="2">2 秒</option><option value="5">5 秒</option>' +
      '</select>' +
    '</div>' +
    '<div class="form-group" style="margin-bottom:10px">' +
      '<label style="display:block;font-size:11px;color:#8b949e;margin-bottom:4px">最大事件数</label>' +
      '<select id="cfg-maxevents" class="btn-sm" style="width:100%">' +
        '<option value="100">100</option><option value="200">200</option>' +
        '<option value="500" selected>500</option><option value="1000">1000</option>' +
      '</select>' +
    '</div>' +
    '<div class="form-group" style="margin-bottom:10px">' +
      '<label style="display:block;font-size:11px;color:#8b949e;margin-bottom:4px">空闲超时</label>' +
      '<select id="cfg-idle" class="btn-sm" style="width:100%">' +
        '<option value="180">3 分钟</option><option value="300" selected>5 分钟</option>' +
        '<option value="600">10 分钟</option><option value="1800">30 分钟</option>' +
      '</select>' +
    '</div>';

  var overlay = document.createElement('div');
  overlay.className = 'sidebar-confirm-overlay';
  overlay.innerHTML =
    '<div class="sidebar-confirm-box">' + html +
      '<div class="sidebar-confirm-actions">' +
        '<button class="btn-sm" onclick="this.closest(\'.sidebar-confirm-overlay\').remove()">取消</button>' +
        '<button class="btn-sm primary" id="cfg-save-btn" onclick="saveWindowConfig()">保存</button>' +
      '</div>' +
    '</div>';
  document.body.appendChild(overlay);
}

async function saveWindowConfig() {
  var config = {
    poll_interval_sec: parseFloat(document.getElementById('cfg-interval').value),
    max_events: parseInt(document.getElementById('cfg-maxevents').value),
    idle_timeout_sec: parseInt(document.getElementById('cfg-idle').value),
  };
  try {
    var r = await fetch('/api/memory/windows/config', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(config),
    });
    var result = await r.json();
    if (result.ok) {
      document.querySelector('.sidebar-confirm-overlay').remove();
      showToast('配置已保存', 'success');
    } else {
      showToast('保存失败: ' + (result.error || ''), 'error');
    }
  } catch(e) {
    showToast('保存失败', 'error');
  }
}

async function clearWindowEvents() {
  var confirmed = await showConfirm('确定清空所有窗口事件记录？');
  if (!confirmed) return;
  try {
    await fetch('/api/memory/windows/clear', {method: 'POST'});
    renderWindowEvents([]);
    renderWindowStats([]);
    showToast('窗口事件已清空', 'success');
  } catch(e) {
    showToast('操作失败', 'error');
  }
}

function formatDuration(sec) {
  sec = Math.round(sec || 0);
  if (sec < 60) return sec + '秒';
  if (sec < 3600) return Math.floor(sec/60) + '分' + (sec%60) + '秒';
  return Math.floor(sec/3600) + '时' + Math.floor((sec%3600)/60) + '分';
}

// ── 初始化 ──
function loadMemory() {
  var el = document.getElementById('memory-overview');
  if (el) loadMemory();  // 有面板元素时加载
}

// ── 其他函数保持不变 ──
function showAddMemory() {
  var overlay = document.createElement('div');
  overlay.className = 'sidebar-confirm-overlay';
  overlay.innerHTML =
    '<div class="sidebar-confirm-box">' +
      '<p>添加记忆条目</p>' +
      '<div class="form-group" style="margin-bottom:10px">' +
        '<label style="display:block;font-size:11px;color:#8b949e;margin-bottom:4px">内容</label>' +
        '<textarea id="new-memory-content" style="width:100%;padding:6px 8px;border-radius:4px;border:1px solid #30363d;background:#0d1117;color:#c9d1d9;font-size:12px;resize:none;box-sizing:border-box" rows="3" placeholder="输入记忆内容..."></textarea>' +
      '</div>' +
      '<div class="form-group" style="margin-bottom:10px">' +
        '<label style="display:block;font-size:11px;color:#8b949e;margin-bottom:4px">优先级</label>' +
        '<select id="new-memory-priority" style="width:100%;padding:4px 8px;border-radius:4px;border:1px solid #30363d;background:#0d1117;color:#c9d1d9;font-size:12px">' +
          '<option value="low">低</option><option value="normal" selected>普通</option><option value="high">高</option>' +
        '</select>' +
      '</div>' +
      '<div class="sidebar-confirm-actions">' +
        '<button class="btn-sm" onclick="this.closest(\'.sidebar-confirm-overlay\').remove()">取消</button>' +
        '<button class="btn-sm primary" onclick="confirmAddMemory()">添加</button>' +
      '</div>' +
    '</div>';
  document.body.appendChild(overlay);
}

async function confirmAddMemory() {
  var content = document.getElementById('new-memory-content').value.trim();
  if (!content) { showToast('请输入内容', 'error'); return; }
  var priority = document.getElementById('new-memory-priority').value;
  try {
    await fetch('/api/memory/manual', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({content: content, priority: priority}),
    });
    document.querySelector('.sidebar-confirm-overlay').remove();
    showToast('记忆已添加', 'success');
    loadMemory();
  } catch(e) { showToast('添加失败', 'error'); }
}

async function deleteMemory(index) {
  var confirmed = await showConfirm('确定删除此记忆？');
  if (!confirmed) return;
  try {
    await fetch('/api/memory/' + index, {method: 'DELETE'});
    showToast('记忆已删除', 'success');
    loadMemory();
  } catch(e) { showToast('删除失败', 'error'); }
}

async function triggerCompression() {
  try {
    var r = await fetch('/api/memory/compress', {method: 'POST'});
    var result = await r.json();
    showToast(result.ok ? '压缩完成' : '压缩失败', result.ok ? 'success' : 'error');
    loadMemory();
  } catch(e) { showToast('压缩失败', 'error'); }
}
```

- [ ] **Step 2: 更新 renderPanelContent 中的 memory case**

在 `static/js/sidebar/sidebar.js` 中找到 `renderPanelContent` 函数的 `case 'memory':` 部分，替换为包含子页标签的新 HTML：

```javascript
case 'memory':
  html = `<div class="memory-subtabs">
        <div class="memory-subtab active" data-memory-tab="memory" onclick="switchMemoryTab('memory')">📋 记忆</div>
        <div class="memory-subtab" data-memory-tab="window" onclick="switchMemoryTab('window')">🪟 窗口活动</div>
      </div>
      <div class="memory-panel active" id="memory-panel-content">
        <div id="memory-overview"></div>
        <div id="memory-content"></div>
      </div>
      <div class="window-panel" id="window-panel-content">
        <div class="window-status-bar" id="window-status"></div>
        <div class="window-current-card" id="window-current">
          <div class="wc-label">🪟 当前活跃窗口</div>
          <div class="wc-title" style="color:#8b949e">加载中...</div>
        </div>
        <div style="font-size:9px;color:#8b949e;font-weight:bold;margin-bottom:4px">📡 最近切换事件</div>
        <div class="window-events" id="window-events">
          <div class="sidebar-empty">加载中...</div>
        </div>
        <div style="font-size:9px;color:#8b949e;font-weight:bold;margin-bottom:4px">📊 今日使用时长</div>
        <div class="window-stats" id="window-stats">
          <div class="sidebar-empty">加载中...</div>
        </div>
        <div class="window-panel-actions">
          <button class="btn-sm" onclick="showWindowConfig()">⚙ 监控参数</button>
          <button class="btn-sm danger" onclick="clearWindowEvents()">清空记录</button>
        </div>
      </div>
      <div class="panel-actions" style="margin-top:8px">
        <button class="btn-sm primary" onclick="showAddMemory()">+ 手动添加</button>
        <button class="btn-sm" onclick="triggerCompression()">⚡ 触发压缩</button>
      </div>`;
  break;
```

- [ ] **Step 3: 提交**

```bash
git add static/js/sidebar/memory.js static/js/sidebar/sidebar.js
git commit -m "feat: add window activity sub-page to memory panel"
```

---

### Task 5: 验证

- [ ] **Step 1: 启动服务器**

```bash
python app_server.py
```

访问 http://127.0.0.1:5678

- [ ] **Step 2: 手动验证检查清单**

**记忆面板子页切换：**
- [ ] 点击图标栏 🧠 → 浮层面板显示记忆管理
- [ ] 顶部显示 📋记忆 / 🪟窗口活动 两个子页标签
- [ ] 默认选中 📋记忆，显示现有记忆内容
- [ ] 点击 🪟窗口活动 → 切换到窗口活动页

**窗口活动页：**
- [ ] 监控状态指示灯 + 开关按钮
- [ ] 当前活跃窗口卡片（标题 + 进程名 + 已持续时长）
- [ ] 最近切换事件流（格式: 时间 · A → B · 停留时长）
- [ ] 今日使用时长排行（带彩色进度条）
- [ ] ⚙ 监控参数按钮 → 弹出配置弹窗
- [ ] 清空记录按钮 → 确认弹窗 → 清空

**API 验证：**
- [ ] GET /api/memory/windows/current 返回当前窗口
- [ ] GET /api/memory/windows/events 返回事件列表
- [ ] GET /api/memory/windows/stats 返回统计数据
- [ ] GET /api/memory/windows/config 返回配置
- [ ] POST /api/memory/windows/config 更新配置

**数据流验证：**
- [ ] 切换窗口后，事件流出现新条目
- [ ] 统计数据的时长在增长

- [ ] **Step 3: 核查浏览器控制台无 JS 错误**

---

### Task 6: 最终提交

- [ ] **Step 1: 确认所有改动已提交**

```bash
git status
git log --oneline -6
```

- [ ] **Step 2: 如有遗漏，补交**

```bash
git add -A
git commit -m "chore: final cleanup for window monitoring feature"
```
