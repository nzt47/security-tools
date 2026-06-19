# 搜索引擎实例管理 实现计划

> **For agentic workers:** 使用 `superpowers:subagent-driven-development` 或 `superpowers:executing-plans` 逐任务执行。步骤使用 `- [ ]` 跟踪。

**目标**：用户可通过 UI 动态添加/删除/配置搜索引擎，无需改代码

**架构**：
- `network_config.json` 新增 `search_instances` 数组，API Key 加密存储在 `.secure_config.json`
- `SearchEngine` 新增 `_search_custom()` 通用 handler，通过注册机制动态加入引擎注册表
- 前端复用 LLM 实例的卡片列表 + 模态框模式，分基本设置和 API 解析规则两个 tab

**技术栈**：Python Flask + `openai` SDK（HTTP 请求用已有 `_http_client`）+ 前端原生 JS

**涉及文件**：
- `agent/network_config.py` — 数据模型 + 加密 + 注册逻辑
- `agent/web/search.py` — 自定义引擎通用 handler
- `agent/server_routes/routes_config.py` — CRUD API + 测试 API
- `templates/index.html` — 模态框 + 列表 HTML
- `static/js/network-config.js` — 前端 CRUD 逻辑

---

### Task 1: 数据模型与存储

**Files:**
- Modify: `agent/network_config.py` — 默认配置 + `_ensure_config_structure` + 加密存储 + 加载

**Interfaces:**
- Consumes: `SecureConfigManager.set_secure_value() / get_secure_value()`
- Produces: `_DEFAULT_SEARCH_INSTANCE` 常量, `config['search_instances']` 数组

- [ ] **Step 1: 添加默认搜索实例常量和默认配置**

在 `agent/network_config.py` 中 `_DEFAULT_NETWORK_CONFIG` 的 `search_api_keys` 之后添加 `search_instances: []`。
在 `_DEFAULT_MCP_SERVICE` 常量附近添加 `_DEFAULT_SEARCH_INSTANCE`：

```python
# 搜索实例默认配置
_DEFAULT_SEARCH_INSTANCE = {
    "id": "",
    "name": "",
    "engine_type": "custom",
    "api_endpoint": "",
    "http_method": "GET",
    "query_param": "q",
    "api_key": "",
    "auth_header": "Authorization: Bearer {key}",
    "results_path": "data",
    "title_field": "title",
    "url_field": "url",
    "snippet_field": "snippet",
    "enabled": True,
    "is_default": False,
    "timeout": 30,
    "created_at": "",
    "updated_at": "",
}
```

- [ ] **Step 2: `_ensure_config_structure` 确保 search_instances 存在**

在 `_ensure_config_structure` 的 `search_api_keys` 之后添加：

```python
# 确保 search_instances 配置存在
if 'search_instances' not in self._cache:
    self._cache['search_instances'] = []

# 确保搜索实例都有 ID
for inst in self._cache.get('search_instances', []):
    if not inst.get('id'):
        inst['id'] = str(uuid.uuid4())
```

- [ ] **Step 3: `get_all()` 和 `get_raw_config()` 中解密搜索实例的 API Key**

在 `get_all()` 和 `get_raw_config()` 的搜索引擎 API Key 加载之后，添加搜索实例 Key 解密：

```python
# 加载搜索实例的敏感信息
for inst in config.get('search_instances', []):
    inst_id = inst.get('id', '')
    if inst_id:
        inst['api_key'] = self._load_secure(
            f'search_{inst_id}_api_key',
            inst.get('api_key', '')
        )
```

在 `get_all()` 的脱敏段添加搜索实例 Key 脱敏：

```python
# 搜索实例 API Key 脱敏
for inst in safe_config.get('search_instances', []):
    if inst.get('api_key'):
        v = inst['api_key']
        inst['api_key'] = '***' + v[-4:] if len(v) > 4 else '***'
```

- [ ] **Step 4: `update()` 方法处理搜索实例的 Key 加密**

在 `update()` 的 `# 处理搜索引擎 API Key` 之后，加搜索实例处理：

```python
# 处理搜索实例
if 'search_instances' in updates:
    self._update_search_instances(updates['search_instances'])
```

- [ ] **Step 5: 实现 `_update_search_instances()` 方法**

参照 `_update_llm_instances()` 的模式：

```python
def _update_search_instances(self, instances: list):
    """更新搜索实例配置"""
    config = self._load()
    for inst in instances:
        inst_id = inst.get('id')
        if not inst_id:
            # 新增
            inst['id'] = str(uuid.uuid4())
            inst['created_at'] = datetime.datetime.now().isoformat()
            inst['updated_at'] = inst['created_at']
            api_key = inst.get('api_key', '')
            if api_key and api_key != '***' and not api_key.startswith('***'):
                self._save_secure(f'search_{inst["id"]}_api_key', api_key)
            config['search_instances'].append(inst)
            self._add_change_log('add', 'search_instance', {'id': inst['id'], 'name': inst.get('name')})
        else:
            existing = next((i for i in config['search_instances'] if i['id'] == inst_id), None)
            if existing:
                api_key = inst.get('api_key', '')
                if api_key and api_key != '***' and not api_key.startswith('***'):
                    self._save_secure(f'search_{inst_id}_api_key', api_key)
                existing.update(inst)
                existing['updated_at'] = datetime.datetime.now().isoformat()
                self._add_change_log('update', 'search_instance', {'id': inst_id, 'name': inst.get('name')})
```

- [ ] **Step 6: 语法检查**

```bash
python -c "import ast; ast.parse(open('agent/network_config.py', encoding='utf-8').read()); print('OK')"
```

---

### Task 2: 自定义搜索引擎通用 Handler

**Files:**
- Modify: `agent/web/search.py` — 新增 `_search_custom()`

**Interfaces:**
- Consumes: `instance` dict（Task 1 的数据模型）, `query: str`, `num_results: int`
- Produces: `{"ok": bool, "results": list, "engine": str}` 统一格式

- [ ] **Step 1: 添加导入和辅助方法**

在 `search.py` 头部/辅助区域添加：

```python
from urllib.parse import quote_plus

def _json_get(obj, path: str):
    """按点号分隔的键路径获取值，如 'data.items' → obj['data']['items']"""
    if not path:
        return obj
    parts = path.strip(".").split(".")
    current = obj
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list):
            try:
                idx = int(part)
                current = current[idx]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return current
```

- [ ] **Step 2: 实现 `_search_custom()` 方法**

放在 `_search_firecrawl` 之后（或在 `_register_builtin_engines` 之前）：

```python
def _search_custom(self, instance: dict, query: str, num_results: int = 10,
                   page: int = 1, **kwargs) -> dict:
    """通用自定义搜索引擎 handler"""
    if not instance.get('api_endpoint'):
        return {"ok": False, "error": "API 端点 URL 未配置"}

    # 1. 构建 URL
    url = instance['api_endpoint'].replace('{query}', quote_plus(query))
    if instance.get('http_method', 'GET') == 'GET' and instance.get('query_param'):
        # 如果 URL 中没有 {query}，追加查询参数
        if '{query}' not in instance['api_endpoint']:
            sep = '&' if '?' in url else '?'
            url += f"{sep}{instance['query_param']}={quote_plus(query)}"

    # 2. 构建请求头
    headers = {}
    api_key = instance.get('api_key', '')
    auth_template = instance.get('auth_header', '')
    if auth_template and api_key:
        header_str = auth_template.replace('{key}', api_key)
        if ': ' in header_str:
            name, value = header_str.split(': ', 1)
            headers[name.strip()] = value.strip()
        elif ' ' in header_str:
            # "Bearer {key}" 风格
            parts = header_str.split(' ', 1)
            headers['Authorization'] = header_str.replace('{key}', api_key)
        else:
            headers[header_str] = api_key

    # 3. HTTP 请求
    if not self._http_client:
        return {"ok": False, "error": "HTTP 客户端未配置"}

    timeout = instance.get('timeout', 30)
    if instance.get('http_method', 'GET') == 'POST':
        result = self._http_client.post(url, headers=headers, timeout=timeout)
    else:
        result = self._http_client.get(url, headers=headers, timeout=timeout)

    if not result.get("ok"):
        return result

    # 4. 解析 JSON 响应
    try:
        data = result.get("data", "")
        if isinstance(data, str):
            json_data = json.loads(data)
        else:
            json_data = data
    except (json.JSONDecodeError, TypeError):
        return {"ok": False, "error": "解析 API 响应 JSON 失败"}

    # 5. 沿 results_path 取结果数组
    results_path = instance.get('results_path', '')
    raw_results = _json_get(json_data, results_path) if results_path else json_data
    if raw_results is None:
        raw_results = []
    if isinstance(raw_results, dict):
        raw_results = [raw_results]
    if not isinstance(raw_results, list):
        raw_results = []

    # 6. 提取标准化字段
    title_f = instance.get('title_field', 'title')
    url_f = instance.get('url_field', 'url')
    snippet_f = instance.get('snippet_field', 'snippet')

    results = []
    for item in raw_results[:num_results]:
        if not isinstance(item, dict):
            continue
        results.append({
            "title": item.get(title_f, '') or '',
            "url": item.get(url_f, '') or '',
            "snippet": item.get(snippet_f, '') or '',
            "source": instance.get('name', 'custom'),
        })

    return {
        "ok": True,
        "results": results,
        "total_estimate": len(raw_results),
        "engine": instance.get('name', 'custom'),
    }
```

- [ ] **Step 3: 语法检查**

```bash
python -c "import ast; ast.parse(open('agent/web/search.py', encoding='utf-8').read()); print('OK')"
```

---

### Task 3: 注册逻辑 — `apply_search_instances()`

**Files:**
- Modify: `agent/network_config.py` — 新增注册方法 + 集成到 `apply_to_app()`

**Interfaces:**
- Consumes: `SearchEngine.register_engine()` / `SearchEngine.remove_engine()` / `SearchEngine.set_engine_priority()` / `SearchEngine.set_engine_enabled()`
- Produces: 在 `apply_to_app()` 中注册/注销自定义引擎

- [ ] **Step 1: 给 `SearchEngine` 添加 `remove_engine()` 和 `set_default_engine()` 方法**

在 `agent/web/search.py` 中添加：

```python
def remove_engine(self, name: str) -> bool:
    """从注册表中移除搜索引擎"""
    if name not in self._engine_registry:
        return False
    del self._engine_registry[name]
    if name in self._engine_priority:
        self._engine_priority = [e for e in self._engine_priority if e != name]
    self._engine_enabled.pop(name, None)
    self._stats["engine_usage"].pop(name, None)
    self._stats["engine_timing"].pop(name, None)
    self._api_keys.pop(name, None)
    logger.info("[搜索引擎] 已移除: %s", name)
    return True

def set_default_engine(self, name: str):
    """设置默认搜索引擎"""
    if name and name not in self._engine_registry:
        raise ValueError(f"引擎 {name} 未注册")
    self._default_engine = name or self._config.get("default_engine", "duckduckgo")
    logger.info("[搜索引擎] 默认引擎已设为: %s", self._default_engine)
```

- [ ] **Step 2: 在 `network_config.py` 中实现 `_register_search_instance()` 和 `_unregister_search_instance()`**

在 `_update_mcp_config` 附近添加：

```python
def _register_search_instance(self, instance: dict, search_engine):
    """注册单个搜索实例到 SearchEngine"""
    if not search_engine:
        return
    inst_id = instance.get('id', '')
    name = instance.get('name', inst_id)
    engine_type = instance.get('engine_type', 'custom')
    enabled = instance.get('enabled', True)

    if engine_type == 'custom':
        # 自定义引擎：注册通用 handler
        from functools import partial
        handler = partial(search_engine._search_custom, instance)
        search_engine.register_engine(
            name=inst_id,
            label=name,
            handler=handler,
            needs_key=bool(instance.get('api_key')),
            description=f"自定义搜索引擎: {instance.get('api_endpoint', '')}",
        )
    else:
        # 内置引擎：已通过 _register_builtin_engines 注册，只需更新 API Key
        if instance.get('api_key'):
            search_engine._api_keys[engine_type] = instance['api_key']
        if name != engine_type and instance.get('api_key'):
            # 也支持用实例名作为别名
            pass

    # 设置启用状态
    search_engine.set_engine_enabled(inst_id, enabled)

    # 如果是默认引擎
    if instance.get('is_default'):
        search_engine.set_default_engine(inst_id)
```

- [ ] **Step 3: 实现 `apply_search_instances()`**

```python
def apply_search_instances(self, search_engine=None):
    """将搜索实例注册到 SearchEngine"""
    if not search_engine:
        return
    config = self.get_raw_config()
    instances = config.get('search_instances', [])
    logger.info("[网络配置] 开始注册 %d 个搜索实例...", len(instances))

    # 先清理之前已注册的自定义引擎
    for inst in instances:
        inst_id = inst.get('id', '')
        if inst_id in search_engine._engine_registry:
            search_engine.remove_engine(inst_id)

    # 重新注册
    default_set = False
    for inst in instances:
        if not inst.get('enabled', True):
            continue
        self._register_search_instance(inst, search_engine)
        if inst.get('is_default'):
            default_set = True

    # 更新优先级：自定义引擎排在前面
    custom_ids = [inst['id'] for inst in instances if inst.get('id') and inst.get('enabled', True)]
    if custom_ids:
        priority = list(search_engine._engine_priority)
        # 把自定义 ID 移到前面
        for cid in custom_ids:
            if cid in priority:
                priority.remove(cid)
        priority = custom_ids + priority
        search_engine.set_engine_priority(priority)
```

- [ ] **Step 4: 在 `apply_to_app()` 中集成**

在 `apply_to_app()` 方法的搜索引擎配置应用之后添加：

```python
# 注册搜索实例
try:
    if app_instance and hasattr(app_instance, '_web_search'):
        self.apply_search_instances(app_instance._web_search)
        logger.info("[网络配置] 搜索实例已注册")
    else:
        logger.warning("[网络配置] 应用实例无 _web_search 属性，跳过搜索实例注册")
except Exception as e:
    logger.warning("[网络配置] 注册搜索实例失败: %s", e, exc_info=True)
```

- [ ] **Step 5: 语法检查**

```bash
python -c "import ast; ast.parse(open('agent/network_config.py', encoding='utf-8').read()); print('OK')"
python -c "import ast; ast.parse(open('agent/web/search.py', encoding='utf-8').read()); print('OK')"
```

---

### Task 4: CRUD API 路由

**Files:**
- Modify: `agent/server_routes/routes_config.py` — 添加搜索实例 CRUD + 测试 API

**Interfaces:**
- Consumes: `ncm` (NetworkConfigManager), `web_search` (SearchEngine)
- Produces: 6 个新 API 端点

- [ ] **Step 1: 实现 `GET /api/search/instances`**

在 MCP 相关路由之后添加（参考 LLM 实例 API 的模式）：

```python
# ═══════════════════════════════════════════════════
#  搜索引擎实例管理
# ═══════════════════════════════════════════════════

@app.route("/api/search/instances", methods=["GET"])
@require_token
@log_request(show_response=False)
def api_search_instances_get():
    try:
        config = ncm.get_all()
        instances = config.get('search_instances', [])
        return jsonify({"ok": True, "instances": instances})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
```

- [ ] **Step 2: 实现 `POST /api/search/instances`**

```python
@app.route("/api/search/instances", methods=["POST"])
@require_token
@log_request()
def api_search_instance_add():
    try:
        data = request.get_json() or {}
        instance = data.get("instance", {})
        
        # 验证
        errors = validate_search_instance(instance)
        if errors:
            return jsonify({"ok": False, "errors": errors}), 400
        
        config = ncm.get_raw_config()
        new_inst = dict(_DEFAULT_SEARCH_INSTANCE)
        new_inst.update(instance)
        new_inst['id'] = str(uuid.uuid4())
        new_inst['created_at'] = datetime.datetime.now().isoformat()
        new_inst['updated_at'] = new_inst['created_at']
        
        # 加密保存 API Key
        api_key = new_inst.get('api_key', '')
        if api_key and not api_key.startswith('***'):
            ncm._save_secure(f'search_{new_inst["id"]}_api_key', api_key)
        
        config['search_instances'].append(new_inst)
        ncm._save(config)
        ncm._add_change_log('add', 'search_instance', {'id': new_inst['id'], 'name': new_inst['name']})
        
        # 即时注册到搜索引擎
        if web_search:
            ncm._register_search_instance(new_inst, web_search)
            ncm.apply_search_instances(web_search)
        
        return jsonify({"ok": True, "instance": new_inst})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
```

- [ ] **Step 3: 实现 `PUT /api/search/instances/<id>` 和 `DELETE /api/search/instances/<id>`**

```python
@app.route("/api/search/instances/<string:instance_id>", methods=["PUT"])
@require_token
@log_request()
def api_search_instance_update(instance_id):
    try:
        data = request.get_json() or {}
        updates = data.get("updates", {})
        config = ncm.get_raw_config()
        instances = config.get('search_instances', [])
        
        for inst in instances:
            if inst.get('id') == instance_id:
                api_key = updates.get('api_key', '')
                if api_key and api_key != '***' and not api_key.startswith('***'):
                    ncm._save_secure(f'search_{instance_id}_api_key', api_key)
                elif api_key and api_key.startswith('***'):
                    updates.pop('api_key', None)
                
                inst.update(updates)
                inst['updated_at'] = datetime.datetime.now().isoformat()
                ncm._save(config)
                ncm._add_change_log('update', 'search_instance', {'id': instance_id, 'name': inst.get('name')})
                
                # 重新注册
                if web_search:
                    ncm.apply_search_instances(web_search)
                
                return jsonify({"ok": True, "instance": inst})
        
        return jsonify({"ok": False, "error": "实例不存在"}), 404
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/search/instances/<string:instance_id>", methods=["DELETE"])
@require_token
@log_request()
def api_search_instance_delete(instance_id):
    try:
        config = ncm.get_raw_config()
        before = len(config.get('search_instances', []))
        config['search_instances'] = [i for i in config.get('search_instances', []) if i.get('id') != instance_id]
        
        if len(config['search_instances']) < before:
            ncm._save(config)
            ncm._save_secure(f'search_{instance_id}_api_key', '')
            ncm._add_change_log('delete', 'search_instance', {'id': instance_id})
            
            # 从搜索引擎移除
            if web_search:
                web_search.remove_engine(instance_id)
            
            return jsonify({"ok": True})
        return jsonify({"ok": False, "error": "实例不存在"}), 404
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
```

- [ ] **Step 4: 实现默认和测试 API**

```python
@app.route("/api/search/instances/<string:instance_id>/default", methods=["POST"])
@require_token
@log_request()
def api_search_instance_set_default(instance_id):
    try:
        config = ncm.get_raw_config()
        instances = config.get('search_instances', [])
        
        found = any(i.get('id') == instance_id for i in instances)
        if not found:
            return jsonify({"ok": False, "error": "实例不存在"}), 404
        
        # 清除其他实例的 is_default
        for inst in instances:
            inst['is_default'] = (inst.get('id') == instance_id)
        
        ncm._save(config)
        ncm._add_change_log('update', 'search_instance', {'id': instance_id, 'action': 'set_default'})
        
        # 更新搜索引擎默认
        if web_search:
            web_search.set_default_engine(instance_id)
        
        return jsonify({"ok": True, "message": "已设为默认搜索引擎"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/search/instances/<string:instance_id>/test", methods=["POST"])
@require_token
@log_request()
def api_search_instance_test(instance_id):
    """测试搜索实例连通性"""
    try:
        config = ncm.get_raw_config()
        instances = config.get('search_instances', [])
        inst = next((i for i in instances if i.get('id') == instance_id), None)
        if not inst:
            return jsonify({"ok": False, "error": "实例不存在"}), 404
        
        if not web_search:
            return jsonify({"ok": False, "error": "搜索引擎未初始化"}), 503
        
        if inst.get('engine_type') == 'custom':
            from web.search import _json_get
            # 直接调用通用 handler
            result = web_search._search_custom(inst, "test", num_results=2)
        else:
            # 内置引擎
            result = web_search.search(query="test", engine=inst.get('engine_type', ''), num_results=2)
        
        return jsonify({
            "ok": result.get("ok", False),
            "results": result.get("results", [])[:2],
            "total": result.get("total_estimate", 0),
            "engine": result.get("engine", ""),
            "error": result.get("error", ""),
        })
    except Exception as e:
        logger.error("[搜索实例] 测试失败: %s", e, exc_info=True)
        return jsonify({"ok": False, "error": str(e)}), 500
```

- [ ] **Step 5: 添加验证函数**

放在 LLM 实例验证函数附近或 CRUD 路由之前：

```python
def validate_search_instance(instance: dict) -> List[str]:
    """验证搜索实例配置"""
    errors = []
    if not instance.get('name'):
        errors.append('名称不能为空')
    engine_type = instance.get('engine_type', '')
    if not engine_type:
        errors.append('引擎类型不能为空')
    if engine_type == 'custom':
        if not instance.get('api_endpoint'):
            errors.append('自定义引擎必须提供 API 端点 URL')
    if instance.get('timeout', 30) < 1 or instance.get('timeout', 30) > 300:
        errors.append('超时必须在 1-300 秒之间')
    return errors
```

- [ ] **Step 6: 在路由文件中导入所需模块**

在 `routes_config.py` 头部添加导入：

```python
import uuid
import datetime
from copy import deepcopy
```

注意：`_DEFAULT_SEARCH_INSTANCE` 需要从 `network_config` 导入或直接定义。

- [ ] **Step 7: 语法检查**

```bash
python -c "import ast; ast.parse(open('agent/server_routes/routes_config.py', encoding='utf-8').read()); print('OK')"
```

---

### Task 5: 前端 HTML — 模态框和列表

**Files:**
- Modify: `templates/index.html` — 添加搜索引擎管理区块 + 模态框

- [ ] **Step 1: 在 LLM 实例管理之后添加搜索引擎管理区块**

在 `index.html` 的 LLM 实例管理 section 之后（MCP 服务管理之前），添加搜索引擎管理区块：

```html
<!-- 搜索引擎管理 -->
<div class="network-section">
  <div class="network-section-header">
    <span class="section-icon">🔍</span>
    <span class="section-title">搜索引擎管理</span>
    <button class="btn-sm primary" onclick="showSearchInstanceModal()">+ 添加引擎</button>
  </div>
  <div class="network-section-body">
    <div class="help-tip" style="margin-bottom:8px">
      <span class="help-icon">💡</span>
      添加自定义搜索引擎须提供 API 端点 URL 和字段映射规则。
      内置引擎（Tavily/Firecrawl/Bing/Google等）可在下方配置 API Key。
    </div>
    <div id="search-instances-list" class="instances-list"></div>
  </div>
</div>
```

- [ ] **Step 2: 添加搜索引擎实例模态框**

在 LLM 实例模态框之后添加（参考其结构）：

```html
<!-- 搜索引擎实例编辑模态框 -->
<div id="search-instance-modal" class="modal-overlay" style="display:none" onclick="if(event.target===this)hideSearchInstanceModal()">
  <div class="modal" style="width:520px">
    <div class="modal-header">
      <h3 id="search-modal-title">添加搜索引擎</h3>
      <span class="modal-close" onclick="hideSearchInstanceModal()">&times;</span>
    </div>
    <div class="modal-body">
      <!-- Tab 切换 -->
      <div class="tab-bar" style="display:flex;gap:2px;margin-bottom:12px">
        <button class="tab-btn active" data-tab="search-basic" onclick="switchSearchTab('search-basic')">基本设置</button>
        <button class="tab-btn" data-tab="search-api" onclick="switchSearchTab('search-api')">解析规则</button>
      </div>
      <form id="search-instance-form">
        <!-- 基本设置 -->
        <div id="tab-search-basic" class="tab-panel">
          <div class="form-group">
            <label>名称 *</label>
            <input type="text" id="si-form-name" placeholder="我的搜索引擎" required>
          </div>
          <div class="form-group">
            <label>引擎类型</label>
            <select id="si-form-engine-type" onchange="onSearchEngineTypeChange()">
              <option value="custom">自定义引擎</option>
              <optgroup label="内置引擎">
                <option value="tavily">Tavily</option>
                <option value="firecrawl">Firecrawl</option>
                <option value="bing">Bing</option>
                <option value="google">Google</option>
                <option value="brave">Brave</option>
                <option value="duckduckgo">DuckDuckGo</option>
                <option value="baidu">百度</option>
                <option value="sogou">搜狗</option>
                <option value="so360">360搜索</option>
              </optgroup>
            </select>
          </div>
          <div class="form-group si-custom-field">
            <label>API 端点 URL <span class="hint">使用 {query} 作为查询占位符</span></label>
            <input type="text" id="si-form-endpoint" placeholder="https://api.example.com/search?q={query}">
          </div>
          <div class="form-group si-custom-field">
            <label>API Key <span class="sensitive-hint">🔒 加密存储</span></label>
            <input type="password" id="si-form-api-key" placeholder="sk-..." autocomplete="off">
          </div>
          <div class="form-group si-custom-field">
            <label>认证头模板 <span class="hint">使用 {key} 作为占位符</span></label>
            <input type="text" id="si-form-auth-header" value="Authorization: Bearer {key}">
          </div>
          <div class="form-row-inline">
            <div class="form-row half si-custom-field">
              <label>HTTP 方法</label>
              <select id="si-form-http-method">
                <option value="GET">GET</option>
                <option value="POST">POST</option>
              </select>
            </div>
            <div class="form-row half">
              <label>超时 (秒)</label>
              <input type="number" id="si-form-timeout" min="1" max="300" value="30">
            </div>
          </div>
        </div>
        <!-- 解析规则 -->
        <div id="tab-search-api" class="tab-panel" style="display:none">
          <div class="help-tip" style="margin-bottom:8px">
            <span class="help-icon">🔧</span>
            配置 API 返回的 JSON 结构中，结果列表和字段的路径。
          </div>
          <div class="form-group si-custom-field">
            <label>查询参数名 <span class="hint">URL 中的查询参数名</span></label>
            <input type="text" id="si-form-query-param" value="q">
          </div>
          <div class="form-group si-custom-field">
            <label>结果 JSON 路径 <span class="hint">结果数组的键路径，如 data.items</span></label>
            <input type="text" id="si-form-results-path" value="data">
          </div>
          <div class="form-row-inline si-custom-field">
            <div class="form-row half">
              <label>标题字段</label>
              <input type="text" id="si-form-title-field" value="title">
            </div>
            <div class="form-row half">
              <label>URL 字段</label>
              <input type="text" id="si-form-url-field" value="url">
            </div>
          </div>
          <div class="form-group si-custom-field">
            <label>摘要字段</label>
            <input type="text" id="si-form-snippet-field" value="snippet">
          </div>
        </div>
      </form>
    </div>
    <div class="modal-footer">
      <button class="btn-secondary" onclick="hideSearchInstanceModal()">取消</button>
      <button class="btn-primary" onclick="saveSearchInstance()">保存</button>
    </div>
  </div>
</div>
```

- [ ] **Step 3: 添加 CSS 样式**

在 `index.html` 的 `<style>` 区域添加：

```css
.tab-btn{background:#161b22;border:1px solid #1e3a5f;padding:6px 16px;color:#8b949e;cursor:pointer;font-size:13px;border-radius:4px 4px 0 0}
.tab-btn.active{background:#0d1117;color:#58a6ff;font-weight:500;border-color:#58a6ff}
.tab-panel{padding:8px 0}
.search-instance-card{...} /* 复用 llm-instance-card 的样式 */
```

（实际样式可以复用 LLM instance card 的 CSS，只需确保 `search-instance-card` 使用同样的类名即可复用）

---

### Task 6: 前端 JS — CRUD 逻辑和 UI 交互

**Files:**
- Modify: `static/js/network-config.js` — 添加搜索引擎实例的加载、渲染、CRUD、测试

- [ ] **Step 1: 加载和渲染**

在 MCP 相关函数之后添加：

```javascript
// ════════════════════════════════════════════════════════════
// 搜索引擎实例管理
// ════════════════════════════════════════════════════════════

let __searchInstances = [];
let __editingSearchId = null;

async function loadSearchInstances() {
  try {
    const res = await apiFetch('/api/search/instances');
    const result = await res.json();
    if (result.ok) {
      __searchInstances = result.instances || [];
      renderSearchInstances(__searchInstances);
    }
  } catch (e) {
    console.error('[网络配置] 加载搜索引擎实例失败:', e);
  }
}

function renderSearchInstances(instances) {
  const container = document.getElementById('search-instances-list');
  if (!container) return;
  
  if (instances.length === 0) {
    container.innerHTML = '<div class="empty-state">暂无搜索引擎实例，点击上方添加</div>';
    return;
  }
  
  // 查找哪个是默认
  const defaultId = __networkConfigCache?.search?.default_engine || '';
  
  container.innerHTML = instances.map(inst => {
    const id = inst.id || inst.name;
    const isDefault = inst.is_default || (id === defaultId);
    const isCustom = inst.engine_type === 'custom';
    const endpoint = isCustom ? inst.api_endpoint : `内置引擎: ${inst.engine_type}`;
    
    return `
    <div class="llm-instance-card ${inst.enabled ? '' : 'disabled'}">
      <div class="llm-instance-header">
        <div style="flex:1">
          <div class="llm-instance-name">${escapeHtml(inst.name)}</div>
          <div class="llm-instance-meta">${inst.engine_type} · ${isCustom ? '自定义' : '内置'}</div>
        </div>
        <div class="llm-instance-actions">
          ${isDefault ? '<span class="default-badge">默认</span>' : ''}
          <label class="toggle-switch small">
            <input type="checkbox" ${inst.enabled ? 'checked' : ''} onchange="toggleSearchInstance('${id}', this.checked)">
            <span class="toggle-slider"></span>
          </label>
          <button class="btn-xs" onclick="testSearchInstance('${id}')" title="测试">▶</button>
          <button class="btn-xs" onclick="editSearchInstance('${id}')" title="编辑">✏️</button>
          <button class="btn-xs danger" onclick="deleteSearchInstance('${id}', '${escapeHtml(inst.name)}')" title="删除">🗑</button>
        </div>
      </div>
      <div class="llm-instance-body">
        <div class="llm-instance-endpoint">📍 ${escapeHtml(endpoint)}</div>
        <div class="llm-instance-stats">
          <span>⏱ ${inst.timeout}s</span>
          <span>🔑 ${inst.api_key ? 'Key已配置' : '无Key'}</span>
        </div>
      </div>
      <button class="llm-set-default" onclick="setDefaultSearchInstance('${id}')" ${isDefault ? 'disabled' : ''}>
        ${isDefault ? '✓ 已设为默认' : '设为默认'}
      </button>
    </div>`;
  }).join('');
}
```

- [ ] **Step 2: 模态框控制 + Tab 切换**

```javascript
function showSearchInstanceModal(instanceId) {
  const modal = document.getElementById('search-instance-modal');
  const title = document.getElementById('search-modal-title');
  const form = document.getElementById('search-instance-form');

  if (instanceId) {
    title.textContent = '编辑搜索引擎';
    __editingSearchId = instanceId;
    const inst = __searchInstances.find(i => (i.id || i.name) === instanceId);
    if (inst) {
      set('si-form-name', inst.name);
      set('si-form-engine-type', inst.engine_type);
      set('si-form-endpoint', inst.api_endpoint || '');
      set('si-form-api-key', inst.api_key || '');
      set('si-form-auth-header', inst.auth_header || 'Authorization: Bearer {key}');
      set('si-form-http-method', inst.http_method || 'GET');
      set('si-form-timeout', inst.timeout || 30);
      set('si-form-query-param', inst.query_param || 'q');
      set('si-form-results-path', inst.results_path || 'data');
      set('si-form-title-field', inst.title_field || 'title');
      set('si-form-url-field', inst.url_field || 'url');
      set('si-form-snippet-field', inst.snippet_field || 'snippet');
    }
  } else {
    title.textContent = '添加搜索引擎';
    __editingSearchId = null;
    form.reset();
    set('si-form-auth-header', 'Authorization: Bearer {key}');
    set('si-form-http-method', 'GET');
    set('si-form-timeout', 30);
    set('si-form-query-param', 'q');
    set('si-form-results-path', 'data');
    set('si-form-title-field', 'title');
    set('si-form-url-field', 'url');
    set('si-form-snippet-field', 'snippet');
  }
  
  onSearchEngineTypeChange(); // 切换 custom 字段显示
  modal.style.display = 'block';
}

function hideSearchInstanceModal() {
  document.getElementById('search-instance-modal').style.display = 'none';
  __editingSearchId = null;
}

function switchSearchTab(tabId) {
  document.querySelectorAll('#search-instance-modal .tab-panel').forEach(p => p.style.display = 'none');
  document.querySelectorAll('#search-instance-modal .tab-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('tab-' + tabId).style.display = 'block';
  document.querySelector(`#search-instance-modal .tab-btn[data-tab="${tabId}"]`).classList.add('active');
}

function onSearchEngineTypeChange() {
  const isCustom = get('si-form-engine-type') === 'custom';
  document.querySelectorAll('.si-custom-field').forEach(el => {
    el.style.display = isCustom ? '' : 'none';
  });
}
```

- [ ] **Step 3: CRUD 操作**

```javascript
async function saveSearchInstance() {
  const instance = {
    name: get('si-form-name'),
    engine_type: get('si-form-engine-type'),
    api_endpoint: get('si-form-endpoint'),
    api_key: get('si-form-api-key'),
    auth_header: get('si-form-auth-header'),
    http_method: get('si-form-http-method'),
    timeout: num('si-form-timeout'),
    query_param: get('si-form-query-param'),
    results_path: get('si-form-results-path'),
    title_field: get('si-form-title-field'),
    url_field: get('si-form-url-field'),
    snippet_field: get('si-form-snippet-field'),
    enabled: true,
  };
  
  if (!instance.name.trim()) { alert('名称不能为空'); return; }
  
  try {
    let result;
    if (__editingSearchId) {
      result = await apiFetch(`/api/search/instances/${__editingSearchId}`, {
        method: 'PUT',
        body: JSON.stringify({ updates: instance }),
      });
    } else {
      result = await apiFetch('/api/search/instances', {
        method: 'POST',
        body: JSON.stringify({ instance }),
      });
    }
    const data = await result.json();
    if (data.ok) {
      await loadSearchInstances();
      hideSearchInstanceModal();
      showNetworkStatus(__editingSearchId ? '✓ 搜索引擎已更新' : '✓ 搜索引擎已添加', 'ok');
    } else {
      alert('操作失败: ' + (data.error || '未知错误'));
    }
  } catch (e) {
    alert('操作失败: ' + e.message);
  }
}

async function deleteSearchInstance(instanceId, name) {
  if (!confirm(`确定要删除搜索引擎 "${name}" 吗？`)) return;
  try {
    const result = await apiFetch(`/api/search/instances/${instanceId}`, { method: 'DELETE' });
    const data = await result.json();
    if (data.ok) {
      await loadSearchInstances();
      showNetworkStatus('✓ 搜索引擎已删除', 'ok');
    } else {
      alert('删除失败: ' + (data.error || '未知错误'));
    }
  } catch (e) {
    alert('删除失败: ' + e.message);
  }
}

async function toggleSearchInstance(instanceId, enabled) {
  try {
    await apiFetch(`/api/search/instances/${instanceId}`, {
      method: 'PUT',
      body: JSON.stringify({ updates: { enabled } }),
    });
    await loadSearchInstances();
  } catch (e) {
    console.error('切换状态失败:', e);
  }
}

async function setDefaultSearchInstance(instanceId) {
  try {
    const result = await apiFetch(`/api/search/instances/${instanceId}/default`, { method: 'POST' });
    const data = await result.json();
    if (data.ok) {
      await loadSearchInstances();
      showNetworkStatus('✓ 已设为默认搜索引擎', 'ok');
    } else {
      alert('操作失败: ' + (data.error || '未知错误'));
    }
  } catch (e) {
    alert('操作失败: ' + e.message);
  }
}

async function editSearchInstance(instanceId) {
  const inst = __searchInstances.find(i => (i.id || i.name) === instanceId);
  if (inst) showSearchInstanceModal(inst.id || inst.name);
  else alert('未找到该实例');
}
```

- [ ] **Step 4: 测试功能**

```javascript
async function testSearchInstance(instanceId) {
  const btn = event.target;
  btn.textContent = '⏳';
  btn.disabled = true;
  try {
    const result = await apiFetch(`/api/search/instances/${instanceId}/test`, { method: 'POST' });
    const data = await result.json();
    if (data.ok && data.results && data.results.length > 0) {
      const preview = data.results.slice(0, 2).map(r => `• ${r.title || '(无标题)'}`).join('\n');
      alert('✓ 测试成功！\n\n返回 ' + data.total + ' 条结果，前 2 条：\n' + preview);
    } else {
      alert('✗ 测试失败: ' + (data.error || '无返回结果'));
    }
  } catch (e) {
    alert('✗ 测试异常: ' + e.message);
  } finally {
    btn.textContent = '▶';
    btn.disabled = false;
  }
}
```

- [ ] **Step 5: 在 `loadNetworkConfig()` 中加载搜索引擎实例**

在 `loadNetworkConfig()` 的 `loadMcpServices()` 之后添加：

```javascript
// 加载搜索引擎实例
await loadSearchInstances();
```

- [ ] **Step 6: 在 `loadNetworkConfig()` 及 `applyNetworkConfig()` 中传递搜索引擎实例**

在 `collectNetworkConfig()` 的 `search_api_keys` 之前添加 `search_instances`：

```javascript
// 收集搜索引擎实例 ID（数据已由后端维护，这里只需标记哪些引擎由实例管理）
const config = {
  ...
  search_instances: __searchInstances,  // 保持实例列表
  ...
};
```

---

## 自审检查

- [ ] **Spec 覆盖**：所有 spec 中的功能点（数据模型、CRUD API、通用 handler、注册逻辑、前端 UI、测试接口）都有对应 Task
- [ ] **无占位符**：所有步骤包含完整代码，没有 TBD/TODO
- [ ] **类型一致性**：字段名在数据模型、handler、API 路由、前端 JS 中保持一致
