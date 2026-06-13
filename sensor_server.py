"""
云枢感知底座 — Web 可视化仪表盘

快速 API（健康+传感器列表）立即加载，
传感器读数采用按类别点按式加载（懒加载），避免全量采集超时。
"""
import os
import json
import logging
import platform
import webbrowser
import concurrent.futures
from flask import Flask, jsonify, render_template_string, request

from sensor import BodySensor

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
app = Flask(__name__)

# ── 预采集数据缓存 ──
_CACHE = {"health": [], "sensors": [], "readings": {},
          "blueprint": {"items": [], "total": 0},
          "file_blueprint": {"items": [], "total": 0},
          "software_blueprint": {"items": [], "total": 0}}

# ── 8 维度标签体系 ──
TAG_DIMENSIONS = [
    {"key": "domain", "label": "目标域", "values": ["硬件感知", "软件感知", "行为感知", "环境感知"]},
    {"key": "locus", "label": "内外方位", "values": ["内部感知", "外部感知", "边界感知"]},
    {"key": "temporal", "label": "动静属性", "values": ["静态配置", "动态运行", "增量变化"]},
    {"key": "method", "label": "采集方式", "values": ["主动探测", "被动监听", "系统查询", "对比检测"]},
    {"key": "layer", "label": "感知层次", "values": ["物理层", "系统层", "应用层"]},
    {"key": "role", "label": "功能角色", "values": ["基础生存", "性能监控", "安全防护", "社交通信", "环境适应"]},
    {"key": "datatype", "label": "数据特征", "values": ["数值量", "状态量", "事件量", "配置量"]},
    {"key": "control", "label": "可干预性", "values": ["仅可观测", "可配置"]},
]


# ── 配置文件监控目录 ──────────────────────────────────────────────
def _get_config_watch_dirs():
    """发现系统关键配置目录（仅限精准子目录，避免递归监控过大范围）"""
    dirs = []
    system_root = os.environ.get("SystemRoot", "")
    # 系统网络配置（hosts 等）
    etc = os.path.join(system_root, "System32", "drivers", "etc")
    if os.path.isdir(etc):
        dirs.append(etc)
    # 组策略
    gpo = os.path.join(system_root, "System32", "GroupPolicy")
    if os.path.isdir(gpo):
        dirs.append(gpo)
    # AppData 启动项（小而关键的目录）
    appdata = os.environ.get("APPDATA")
    if appdata:
        startup = os.path.join(appdata, "Microsoft", "Windows", "Start Menu", "Programs", "Startup")
        if os.path.isdir(startup):
            dirs.append(startup)
    # LocalAppData 关键子目录
    localappdata = os.environ.get("LOCALAPPDATA")
    if localappdata:
        for sub in [r"Microsoft\Windows", r"Programs\Common"]:
            d = os.path.join(localappdata, sub)
            if os.path.isdir(d):
                dirs.append(d)
    return list(set(dirs))


# 配置文件扩展名过滤
CONFIG_FILE_INCLUDE = [
    "*.conf", "*.cfg", "*.ini", "*.xml", "*.json", "*.yaml", "*.yml",
    "*.toml", "*.env", "*.props", "*.targets",
    # 系统关键文件（无扩展名）
    "hosts", "networks", "protocols", "services",
    "hostname", "resolv.conf",
    ".gitconfig", ".bashrc", ".zshrc", ".profile",
    "*.ps1", "*.bat", "*.cmd",
]


def _init_cache():
    """初始化 BodySensor 并预采集数据。"""
    logger.info("正在初始化感知底座并预采集数据...")
    watch_dirs = _get_config_watch_dirs()
    logger.info(f"配置文件监控目录: {len(watch_dirs)} 个")
    for d in watch_dirs:
        logger.info(f"  · {d}")
    bs = BodySensor(
        enable_change_detection=True, enable_event_monitor=False,
        watch_dirs=watch_dirs, file_include=CONFIG_FILE_INCLUDE,
    )

    # 健康数据
    _CACHE["health"] = [r.to_dict() for r in bs.collect_quick()]
    logger.info(f"健康数据: {len(_CACHE['health'])} 条")

    # 传感器列表
    _CACHE["sensors"] = bs.get_sensor_info()
    logger.info(f"传感器列表: {len(_CACHE['sensors'])} 个")

    # 逐类别预采集
    from sensor.sensor_reading import Category
    for cat_enum, sensor in bs._sensors.items():
        if sensor is None:
            continue
        try:
            data = sensor.collect()
            readings = data if isinstance(data, list) else [data]
            cat_name = cat_enum.value
            try:
                bs._apply_tags(readings)
            except Exception:
                pass
            _CACHE["readings"][cat_name] = [r.to_dict() for r in readings]
            logger.info(f"  {cat_name}: {len(readings)} 条")
        except Exception as e:
            cat_name = cat_enum.value
            _CACHE["readings"][cat_name] = []
            logger.warning(f"  {cat_name}: 失败 - {e}")

    # 硬件变更（change_detector 单独处理，不在 _sensors 循环中）
    try:
        if bs.change_detector:
            changes = bs.change_detector.collect()
            _CACHE["readings"]["change"] = [r.to_dict() for r in (changes if isinstance(changes, list) else [changes])]
            logger.info(f"  change: {len(_CACHE['readings']['change'])} 条")
        else:
            _CACHE["readings"]["change"] = []
            logger.info("  change: 0 条（未启用）")
    except Exception as e:
        _CACHE["readings"]["change"] = []
        logger.warning(f"  change: 失败 - {e}")

    # 硬件蓝图
    try:
        bp_data = bs.blueprint.collect()
        bp_items = []
        for r in (bp_data if isinstance(bp_data, list) else []):
            bp_items.append({
                "name": r.sensor_name,
                "value": r.value,
                "method": r.metadata.get("method", "") if r.metadata else "",
                "device_type": r.metadata.get("device_type", "") if r.metadata else "",
                "data_origin": r.metadata.get("data_origin", "") if r.metadata else "",
            })
        _CACHE["blueprint"] = {"items": bp_items, "total": len(bp_items)}
        logger.info(f"硬件蓝图: {len(bp_items)} 项")
    except Exception as e:
        logger.warning(f"硬件蓝图: 失败 - {e}")

    # 文件蓝图
    try:
        fb_data = bs.file_blueprint.collect()
        fb_items = []
        for r in (fb_data if isinstance(fb_data, list) else []):
            fb_items.append({
                "name": r.sensor_name,
                "value": r.value,
                "method": r.metadata.get("method", "") if r.metadata else "",
                "device_type": r.metadata.get("device_type", "") if r.metadata else "",
                "data_origin": r.metadata.get("data_origin", "") if r.metadata else "",
            })
        _CACHE["file_blueprint"] = {"items": fb_items, "total": len(fb_items)}
        logger.info(f"文件蓝图: {len(fb_items)} 项")
    except Exception as e:
        logger.warning(f"文件蓝图: 失败 - {e}")

    # 软件蓝图
    try:
        sb_data = bs.software_blueprint.collect()
        sb_items = []
        for r in (sb_data if isinstance(sb_data, list) else []):
            sb_items.append({
                "name": r.sensor_name,
                "value": r.value,
                "method": r.metadata.get("method", "") if r.metadata else "",
                "device_type": r.metadata.get("device_type", "") if r.metadata else "",
                "data_origin": r.metadata.get("data_origin", "") if r.metadata else "",
            })
        _CACHE["software_blueprint"] = {"items": sb_items, "total": len(sb_items)}
        logger.info(f"软件蓝图: {len(sb_items)} 项")
    except Exception as e:
        logger.warning(f"软件蓝图: 失败 - {e}")

    logger.info("预采集完成！")
    return bs


# 启动时预采集
bs = _init_cache()


HTML = r"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>云枢 · 感知底座</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,'Segoe UI',sans-serif;background:#0d1117;color:#c9d1d9;padding:20px}
h1{font-size:24px;margin-bottom:4px}
.sub{color:#8b949e;font-size:14px;margin-bottom:16px}

/* 网格布局 */
.grid-2{display:grid;grid-template-columns:1fr 1fr;gap:16px;max-width:1400px;margin:0 auto}
@media(max-width:900px){.grid-2{grid-template-columns:1fr}}

/* 卡片 */
.card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px;margin-bottom:16px}
.card h2{font-size:16px;margin-bottom:12px;color:#58a6ff;border-bottom:1px solid #21262d;padding-bottom:8px}

/* 健康指标卡 */
.quick-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:10px}
.metric{text-align:center;padding:12px;background:#0d1117;border-radius:8px;border:1px solid #21262d}
.metric .value{font-size:26px;font-weight:700}
.metric .label{font-size:12px;color:#8b949e;margin-top:4px}
.metric.normal .value{color:#3fb950}
.metric.warning .value{color:#d29922}
.metric.critical .value{color:#f85149}

/* 传感器网格 */
.sensor-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:8px}
.sensor-card{background:#0d1117;border:1px solid #21262d;border-radius:6px;padding:10px;cursor:pointer;transition:border-color .2s}
.sensor-card:hover{border-color:#58a6ff}
.sensor-card .top{display:flex;align-items:center;gap:8px;margin-bottom:4px}
.sensor-card .dot{width:8px;height:8px;border-radius:50%;flex-shrink:0}
.sensor-card .dot.on{background:#3fb950;box-shadow:0 0 4px #3fb95088}
.sensor-card .dot.off{background:#30363d}
.sensor-card .name{font-size:13px}
.sensor-card .badge{font-size:10px;color:#8b949e;background:#21262d;padding:1px 6px;border-radius:4px}
.sensor-card .count{font-size:11px;color:#8b949e;margin-top:4px;padding-left:16px}
.sensor-card .count.loading{color:#d29922}
.sensor-card .count.fail{color:#f85149}
.sensor-card.active{border-color:#1f6feb;background:#1f6feb15}

/* 读数面板 */
#readings-panel{min-height:100px}
.readings{max-height:450px;overflow-y:auto;font-size:13px}
.reading-row{display:flex;justify-content:space-between;padding:5px 0;border-bottom:1px solid #21262d;gap:8px}
.reading-row:last-child{border-bottom:none}
.reading-row .r-name{color:#8b949e;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.reading-row .r-val{font-weight:500;white-space:nowrap}
.reading-row.sev-warning .r-val{color:#d29922}
.reading-row.sev-critical .r-val{color:#f85149}

/* 刷新按钮 */
#refresh-btn{position:fixed;bottom:20px;right:20px;cursor:pointer;padding:8px 20px;border-radius:20px;border:1px solid #30363d;background:#161b22;color:#c9d1d9;font-size:13px;z-index:100}
#refresh-btn:hover{background:#21262d}
#update-time{position:fixed;bottom:24px;left:20px;font-size:12px;color:#8b949e;z-index:100}

/* 蓝图面板 */
.blueprint-items{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:4px;font-size:12px}
.blueprint-item{padding:4px 8px;background:#0d1117;border-radius:4px}
.blueprint-item .bp-method{color:#8b949e;font-size:10px}
</style>
</head>
<body>
<h1>🧠 云枢 · 感知底座</h1>
<div class="sub">PC 硬件/软件/行为传感器系统 · 点击传感器查看读数</div>

<div class="grid-2">
  <div>
    <!-- 快速健康 -->
    <div class="card">
      <h2>⚡ 快速健康</h2>
      <div class="quick-grid" id="quick-metrics"></div>
    </div>

    <!-- 元认知引擎（阶段二） -->
    <div class="card" id="cognitive-card" style="border-color:#bc8cff">
      <h2 style="color:#bc8cff">🧠 元认知引擎</h2>
      <div id="cognitive-body">
        <div class="sub" style="text-align:center;padding:20px 0">⏳ 加载中...</div>
      </div>
    </div>

    <!-- 硬件蓝图 -->
    <div class="card">
      <h2>📐 硬件蓝图</h2>
      <div id="blueprint-panel"></div>
    </div>

    <!-- 文件蓝图 -->
    <div class="card">
      <h2>📁 文件蓝图</h2>
      <div id="file-blueprint-panel"></div>
    </div>

    <!-- 软件蓝图 -->
    <div class="card">
      <h2>📦 软件蓝图</h2>
      <div id="software-blueprint-panel"></div>
    </div>
  </div>

  <div>
    <!-- 维度筛选 -->
    <div class="card">
      <h2>🏷️ 多维度筛选 <span id="filter-summary" style="font-size:13px;color:#8b949e"></span></h2>
      <div id="filter-panel"></div>
      <div style="margin-top:8px;display:flex;gap:8px;align-items:center;flex-wrap:wrap">
        <button id="clear-filter-btn" onclick="clearFilter()" style="display:none;padding:4px 12px;border-radius:12px;border:1px solid #30363d;background:#161b22;color:#c9d1d9;font-size:12px;cursor:pointer">✕ 清除筛选</button>
        <span id="filter-result-count" style="font-size:12px;color:#8b949e"></span>
      </div>
    </div>

    <!-- 读数面板 -->
    <div class="card">
      <h2 id="reading-title">📡 传感器读数</h2>
      <div id="readings-panel">
        <div class="sub" style="text-align:center;padding:40px 0">⏳ 加载中...</div>
      </div>
    </div>

    <!-- 传感器列表（快速跳转） -->
    <div class="card">
      <h2>🔘 传感器快捷 <span id="sensor-count" style="font-size:13px;color:#8b949e"></span></h2>
      <div class="sensor-grid" id="sensor-list"></div>
    </div>
  </div>
</div>

<button id="refresh-btn" onclick="refreshAll()">🔄 刷新</button>
<div id="update-time"></div>

<style>
.dim-row{display:flex;align-items:flex-start;gap:6px;margin-bottom:6px;flex-wrap:wrap}
.dim-label{font-size:11px;color:#8b949e;min-width:56px;padding-top:4px;flex-shrink:0}
.dim-values{display:flex;flex-wrap:wrap;gap:4px}
.tag-pill{font-size:11px;padding:3px 10px;border-radius:10px;border:1px solid #30363d;background:#0d1117;color:#8b949e;cursor:pointer;transition:all .15s;user-select:none}
.tag-pill:hover{border-color:#58a6ff;color:#c9d1d9}
.tag-pill.active{background:#1f6feb30;border-color:#1f6feb;color:#58a6ff}
</style>

<script>
const CAT_ICONS = {
  cpu:'🔲',gpu:'🎮',memory:'🧮',battery:'🔋',disk:'💾',
  network:'🌐',board:'🔌',chassis:'🖥️',port:'🔗',peripheral:'🖱️',
  process:'⚙️',file:'📁',environment:'🌿',activity:'📊',display:'🖥️',
  audio:'🔊',change:'🔄',system:'🏥'
};

// 类别 → 8 维度默认标签（与 tags.py _CATEGORY_TAGS 一致）
const CATEGORY_TAGS = {
  cpu:['硬件感知','内部感知','动态运行','主动探测','物理层','性能监控','数值量','仅可观测'],
  gpu:['硬件感知','内部感知','动态运行','主动探测','物理层','性能监控','数值量','仅可观测'],
  memory:['硬件感知','内部感知','动态运行','主动探测','系统层','性能监控','数值量','仅可观测'],
  battery:['硬件感知','内部感知','动态运行','主动探测','物理层','基础生存','数值量','仅可观测'],
  disk:['硬件感知','内部感知','动态运行','主动探测','物理层','基础生存','数值量','仅可观测'],
  network:['硬件感知','外部感知','动态运行','主动探测','系统层','社交通信','数值量','可配置'],
  board:['硬件感知','内部感知','静态配置','主动探测','物理层','基础生存','配置量','仅可观测'],
  chassis:['硬件感知','内部感知','静态配置','主动探测','物理层','基础生存','状态量','仅可观测'],
  change:['硬件感知','内部感知','增量变化','对比检测','物理层','基础生存','事件量','仅可观测'],
  file:['软件感知','内部感知','增量变化','被动监听','应用层','环境适应','事件量','仅可观测'],
  environment:['环境感知','内部感知','静态配置','系统查询','系统层','环境适应','配置量','仅可观测'],
  activity:['行为感知','内部感知','增量变化','对比检测','系统层','性能监控','数值量','仅可观测'],
  system:['软件感知','内部感知','动态运行','系统查询','系统层','环境适应','状态量','可配置'],
};

let selectedTags = {};

// 带超时的 fetch
async function fetchJSON(url, ms=10000) {
  const ctrl = new AbortController();
  const id = setTimeout(() => ctrl.abort(), ms);
  try {
    const r = await fetch(url, {signal: ctrl.signal});
    clearTimeout(id);
    if (!r.ok) throw new Error(r.statusText);
    return await r.json();
  } catch(e) {
    clearTimeout(id);
    if (e.name === 'AbortError') throw new Error('超时');
    throw e;
  }
}

// ===== 初始化 =====
async function refreshAll() {
  document.getElementById('update-time').textContent = '刷新中...';
  await Promise.allSettled([loadHealth(), loadFilter(), loadSensors(), loadBlueprint(), loadFileBlueprint(), loadSoftwareBlueprint(), loadCognitive()]);
  document.getElementById('update-time').textContent =
    '更新: ' + new Date().toLocaleTimeString();
}

// ===== 健康数据 =====
async function loadHealth() {
  try {
    const data = await fetchJSON('/api/health', 8000);
    const el = document.getElementById('quick-metrics');
    el.innerHTML = data.map(r => {
      let cls = 'normal';
      if (r.severity === 'warning') cls = 'warning';
      else if (r.severity === 'critical') cls = 'critical';
      return `<div class="metric ${cls}">
        <div class="value">${r.value}${r.unit}</div>
        <div class="label">${r.description}</div>
      </div>`;
    }).join('');
  } catch(e) {
    document.getElementById('quick-metrics').innerHTML =
      '<div class="sub" style="color:#f85149">❌ 健康数据加载失败</div>';
  }
}

// ===== 传感器列表（快速跳转） =====
async function loadSensors() {
  try {
    const data = await fetchJSON('/api/sensors', 8000);
    const el = document.getElementById('sensor-list');
    document.getElementById('sensor-count').textContent =
      `· ${data.filter(s=>s.enabled).length}/${data.length} 已开启`;

    el.innerHTML = data.map(s => {
      const icon = CAT_ICONS[s.category] || '📡';
      return `<div class="sensor-card" data-category="${s.category}"
                onclick="jumpToCategory('${s.category}')">
        <div class="top">
          <span class="dot ${s.enabled?'on':'off'}"></span>
          <span class="name">${icon} ${s.label||s.name}</span>
          <span class="badge">${s.category||''}</span>
        </div>
        <div class="count" id="count-${s.category}">${s.category ? (CATEGORY_TAGS[s.category] ? '查看' : '') : ''}</div>
      </div>`;
    }).join('');
  } catch(e) {
    document.getElementById('sensor-list').innerHTML =
      '<div class="sub" style="color:#f85149">❌ 加载失败</div>';
  }
}

// 点击传感器 → 设置维度筛选
function jumpToCategory(category) {
  const tags = CATEGORY_TAGS[category];
  if (!tags) return;
  // 更新 selectedTags
  selectedTags = {};
  const dimKeys = ['domain','locus','temporal','method','layer','role','datatype','control'];
  for (let i = 0; i < dimKeys.length && i < tags.length; i++) {
    selectedTags[dimKeys[i]] = tags[i];
  }
  // 刷新 UI
  document.querySelectorAll('.tag-pill').forEach(p => p.classList.remove('active'));
  for (const [key, val] of Object.entries(selectedTags)) {
    const pill = document.querySelector(`.tag-pill[data-dim="${key}"][data-tag="${val}"]`);
    if (pill) pill.classList.add('active');
  }
  loadFilteredReadings();
}

// ===== 维度筛选器 =====
async function loadFilter() {
  try {
    const dims = await fetchJSON('/api/tag-dimensions', 8000);
    const el = document.getElementById('filter-panel');
    el.innerHTML = dims.map(d => {
      const vals = d.values.map(v =>
        `<span class="tag-pill" data-dim="${d.key}" data-tag="${v}"
           onclick="toggleTag('${d.key}','${v}')">${v}</span>`
      ).join('');
      return `<div class="dim-row"><span class="dim-label">${d.label}</span><div class="dim-values">${vals}</div></div>`;
    }).join('');

    // 恢复之前的选择（如果有）
    for (const [key, val] of Object.entries(selectedTags)) {
      const pill = el.querySelector(`.tag-pill[data-dim="${key}"][data-tag="${val}"]`);
      if (pill) pill.classList.add('active');
    }

    await loadFilteredReadings();
  } catch(e) {
    document.getElementById('filter-panel').innerHTML =
      '<div class="sub" style="color:#f85149">❌ 维度加载失败</div>';
  }
}

// ===== 标签筛选 =====
function toggleTag(dim, tag) {
  if (selectedTags[dim] === tag) {
    delete selectedTags[dim];
  } else {
    selectedTags[dim] = tag;
  }
  // 更新 UI
  document.querySelectorAll(`.tag-pill[data-dim="${dim}"]`).forEach(p => p.classList.remove('active'));
  const pill = document.querySelector(`.tag-pill[data-dim="${dim}"][data-tag="${tag}"]`);
  if (selectedTags[dim]) {
    document.querySelectorAll(`.tag-pill[data-dim="${dim}"][data-tag="${selectedTags[dim]}"]`).forEach(p => p.classList.add('active'));
  }
  loadFilteredReadings();
}

function clearFilter() {
  selectedTags = {};
  document.querySelectorAll('.tag-pill').forEach(p => p.classList.remove('active'));
  document.getElementById('clear-filter-btn').style.display = 'none';
  loadFilteredReadings();
}

async function loadFilteredReadings() {
  const panel = document.getElementById('readings-panel');
  const tagValues = Object.values(selectedTags);
  const summary = document.getElementById('filter-summary');
  const clearBtn = document.getElementById('clear-filter-btn');
  const countEl = document.getElementById('filter-result-count');

  if (tagValues.length === 0) {
    summary.textContent = '· 全部数据（点击标签筛选）';
    clearBtn.style.display = 'none';
  } else {
    summary.textContent = '· 已选 ' + tagValues.length + ' 个维度';
    clearBtn.style.display = '';
  }

  panel.innerHTML = '<div class="sub" style="text-align:center;padding:20px 0">⏳ 筛选...</div>';

  try {
    const tagsParam = tagValues.join(',');
    const url = tagsParam ? '/api/filter?tags=' + encodeURIComponent(tagsParam) : '/api/filter';
    const data = await fetchJSON(url, 25000);
    const readings = data.readings || [];

    countEl.textContent = readings.length + ' 条读数';

    if (readings.length === 0) {
      panel.innerHTML = '<div class="sub" style="text-align:center;padding:20px 0">📭 无匹配读数</div>';
      return;
    }

    panel.className = 'readings';
    panel.innerHTML = readings.map(r => {
      let cls = '';
      if (r.severity === 'warning') cls = 'sev-warning';
      else if (r.severity === 'critical') cls = 'sev-critical';
      const shortName = r.sensor_name.length > 50 ? r.sensor_name.slice(0,50)+'…' : r.sensor_name;
      const icon = CAT_ICONS[r.category] || '📡';
      return `<div class="reading-row ${cls}">
        <span class="r-name" title="${r.sensor_name}">${icon} ${shortName}</span>
        <span class="r-val">${r.value}${r.unit}</span>
      </div>`;
    }).join('');
  } catch(e) {
    panel.innerHTML = '<div class="sub" style="text-align:center;padding:20px 0;color:#f85149">❌ 加载失败: ' + e.message + '</div>';
  }
}

// ===== 硬件蓝图 =====
async function loadBlueprint() {
  try {
    const data = await fetchJSON('/api/blueprint', 15000);
    const el = document.getElementById('blueprint-panel');
    if (!data.items || data.items.length === 0) {
      el.innerHTML = '<div class="sub">无数据</div>';
      return;
    }
    const byOrigin = {};
    for (const item of data.items) {
      const o = item.data_origin || 'unknown';
      if (!byOrigin[o]) byOrigin[o] = [];
      byOrigin[o].push(item);
    }
    const ORIGIN_LABELS = {
      software: '🖥️ 从软件工具读取',
      direct: '🔬 从硬件直接读取',
      inference: '⚡ 推断项',
      manual: '🔧 需人工检查'
    };
    const ORIGIN_ORDER = ['software','direct','inference','manual'];
    let html = '<div class="sub">' + data.total + ' 项硬件检测清单</div>';
    for (const origin of ORIGIN_ORDER) {
      const items = byOrigin[origin];
      if (!items || items.length === 0) continue;
      html += '<div style="margin-top:8px"><strong style="font-size:12px">' + (ORIGIN_LABELS[origin]||origin) + '</strong> ' + items.length + ' 项</div>';
      html += '<div class="blueprint-items">';
      for (const item of items) {
        html += '<div class="blueprint-item"><span class="bp-method">' + item.value + '</span>';
        const shortName = item.name.replace('blueprint_','').replace(/_/g,' ');
        html += '<br>' + shortName + '</div>';
      }
      html += '</div>';
    }
    el.innerHTML = html;
  } catch(e) {
    document.getElementById('blueprint-panel').innerHTML =
      '<div class="sub" style="color:#f85149">❌ 蓝图加载失败</div>';
  }
}

// ===== 通用蓝图渲染 =====
function renderBlueprint(data, el) {
  if (!data.items || data.items.length === 0) {
    el.innerHTML = '<div class="sub">无数据</div>';
    return;
  }
  const groups = {};
  for (const item of data.items) {
    const g = item.method || 'unknown';
    if (!groups[g]) groups[g] = [];
    groups[g].push(item);
  }
  const LABELS = {
    software_detectable: '✅ 软件检测',
    inference: '⚡ 推断',
    manual_check: '🔧 需人工检查'
  };
  const ORDER = ['software_detectable','inference','manual_check'];
  let html = '<div class="sub">' + data.total + ' 项</div>';
  for (const key of ORDER) {
    const items = groups[key];
    if (!items || items.length === 0) continue;
    html += '<div style="margin-top:6px"><strong style="font-size:11px">' + (LABELS[key]||key) + '</strong> ' + items.length + ' 项</div>';
    html += '<div class="blueprint-items">';
    for (const item of items) {
      const shortName = item.name.replace(/^[a-z]+_blueprint_/, '').replace(/_/g,' ');
      html += '<div class="blueprint-item"><span class="bp-method">' + (item.value||'') + '</span><br>' + shortName + '</div>';
    }
    html += '</div>';
  }
  el.innerHTML = html;
}

// ===== 文件蓝图 =====
async function loadFileBlueprint() {
  try {
    const data = await fetchJSON('/api/file-blueprint', 15000);
    renderBlueprint(data, document.getElementById('file-blueprint-panel'));
  } catch(e) {
    document.getElementById('file-blueprint-panel').innerHTML =
      '<div class="sub" style="color:#f85149">❌ 文件蓝图加载失败</div>';
  }
}

// ===== 软件蓝图 =====
async function loadSoftwareBlueprint() {
  try {
    const data = await fetchJSON('/api/software-blueprint', 15000);
    renderBlueprint(data, document.getElementById('software-blueprint-panel'));
  } catch(e) {
    document.getElementById('software-blueprint-panel').innerHTML =
      '<div class="sub" style="color:#f85149">❌ 软件蓝图加载失败</div>';
  }
}

// ===== 元认知引擎 =====
async function loadCognitive() {
  try {
    const [status, prompt, reject] = await Promise.all([
      fetch('/api/cognitive/status').then(r => r.text()),
      fetch('/api/cognitive/prompt').then(r => r.text()),
      fetchJSON('/api/cognitive/reject'),
    ]);

    const el = document.getElementById('cognitive-body');
    const shortPrompt = prompt.split('\n').slice(0,3).join('<br>');

    el.innerHTML = `
      <div style="font-size:13px;background:#1c1225;border-radius:8px;padding:12px">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
          <strong style="color:#bc8cff">身体状况</strong>
          <span style="font-size:11px;padding:2px 10px;border-radius:10px;background:${reject.rejected?'#f8514930':'#3fb95030'};color:${reject.rejected?'#f85149':'#3fb950'};border:1px solid ${reject.rejected?'#f8514980':'#3fb95080'}">
            ${reject.rejected ? '❌ 拒绝任务' : '✅ 可执行任务'}
          </span>
        </div>
        <div style="color:#e6d5ff;margin-bottom:8px;line-height:1.5">${status}</div>
        <div style="font-size:11px;color:#8b949e;margin-bottom:4px;border-top:1px solid #30363d;padding-top:8px">提示词注入</div>
        <div style="font-size:11px;color:#c9d1d9;background:#0d1117;padding:8px;border-radius:4px;font-family:monospace;line-height:1.4">${escapedPrompt}...</div>
        ${reject.rejected ? `<div style="margin-top:8px;padding:6px 8px;background:#f8514915;border:1px solid #f8514940;border-radius:4px;font-size:11px;color:#f85149">
          ⚠️ ${reject.reason}</div>` : ''}
      </div>`;
  } catch(e) {
    document.getElementById('cognitive-body').innerHTML =
      '<div class="sub" style="color:#f85149">❌ 加载失败: ' + e.message + '</div>';
  }
}

// 自动刷新
refreshAll();
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(HTML)


@app.route("/api/health")
def api_health():
    return jsonify(_CACHE["health"])


@app.route("/api/sensors")
def api_sensors():
    return jsonify(_CACHE["sensors"])


@app.route("/api/category/<cat>")
def api_category(cat):
    cat_data = _CACHE["readings"].get(cat)
    if cat_data is None:
        return jsonify({"readings": [], "error": f"未知类别: {cat}"}), 400
    return jsonify({"readings": cat_data})


@app.route("/api/blueprint")
def api_blueprint():
    return jsonify(_CACHE["blueprint"])


@app.route("/api/file-blueprint")
def api_file_blueprint():
    return jsonify(_CACHE["file_blueprint"])


@app.route("/api/software-blueprint")
def api_software_blueprint():
    return jsonify(_CACHE["software_blueprint"])


@app.route("/api/tag-dimensions")
def api_tag_dimensions():
    return jsonify(TAG_DIMENSIONS)


@app.route("/api/filter")
def api_filter():
    tags_str = request.args.get("tags", "")
    if not tags_str:
        # 无筛选 → 返回全部
        all_readings = []
        for cat_readings in _CACHE["readings"].values():
            all_readings.extend(cat_readings)
        return jsonify({"readings": all_readings, "count": len(all_readings)})

    selected = [t.strip() for t in tags_str.split(",") if t.strip()]
    result = []
    for cat_readings in _CACHE["readings"].values():
        for r in cat_readings:
            r_tags = r.get("tags", [])
            if all(t in r_tags for t in selected):
                result.append(r)

    return jsonify({"readings": result, "count": len(result), "tags": selected})


# ── 挂载元认知引擎（PromptInjector） ──
try:
    from cognitive import PromptInjector
    from cognitive.flask_adapter import register_prompt_routes
    _cognitive_injector = PromptInjector()
    register_prompt_routes(app, _cognitive_injector, _CACHE)
    logger.info("元认知引擎已挂载，API: /api/cognitive/*")
except Exception as e:
    logger.warning("元认知引擎挂载失败: %s", e)


if __name__ == "__main__":
    print("=" * 50)
    print("  云枢感知底座 · Web 仪表盘")
    print("  http://127.0.0.1:5678")
    print("=" * 50)
    print("  点击传感器卡片按类别加载数据")
    print("=" * 50)
    webbrowser.open("http://127.0.0.1:5678")
    app.run(host="127.0.0.1", port=5678, debug=False, threaded=False)
