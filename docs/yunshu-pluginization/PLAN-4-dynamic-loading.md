# PLAN-4：动态装载（阶段 4，可选）

> 配套总览：`docs/yunshu-pluginization/README.md`
> 目标：第三方/新功能插件「丢进目录即生效」，前端无需发版即可发现新插件。
> 前置：阶段 1（后端插件体系）、阶段 2（前端插槽体系）。

---

## 1. 后端：插件目录扫描自动加载

**现状**（阶段 1）：`plugins/__init__.py` 用显式 import 清单加载，顺序确定、依赖明确。

**本阶段改造**：

1. 新增 `plugins/loader.py`：

```python
# plugins/loader.py
import importlib
import pkgutil
from pathlib import Path
from .plugin_api import get_plugins, register_plugin

PLUGIN_DIR = Path(__file__).parent

def load_all() -> int:
    """扫描 plugins/ 目录下所有 .py 模块（跳过 _ 开头和 plugin_api/services/loader 本身），
    导入它们以触发 register_plugin()。返回成功加载数。"""
    loaded = 0
    for mod in pkgutil.iter_modules([str(PLUGIN_DIR)]):
        if mod.name.startswith("_") or mod.name in {"plugin_api", "services", "loader"}:
            continue
        try:
            importlib.import_module(f".{mod.name}", __package__)
            loaded += 1
        except Exception as exc:  # noqa: BLE001
            # 记录到日志，单插件失败不阻断整体
            print(f"[plugins] 加载 {mod.name} 失败: {exc}")
    return loaded

def refresh_manifest() -> dict:
    """重新加载（先清空注册表再扫描），返回最新 manifest。"""
    from .plugin_api import _REGISTRY
    _REGISTRY.clear()
    load_all()
    return manifest()
```

2. 新增端点 `POST /api/plugins/reload`（需 `require_token`）：调用 `refresh_manifest()` 并返回新 manifest。
3. 装配器启动时改为调用 `loader.load_all()`（替换显式 import 清单；或保留显式清单作为「内置插件」，扫描结果合并）。
4. 错误隔离：单个插件 import 失败只记日志，不影响其他插件与整体启动。

**约束**：

- 插件文件名即插件名候选；`PLUGIN.name` 以模块内声明为准，重名时以 `register_plugin` 幂等逻辑先到先得并告警。
- 扫描目录 = `plugins/`；第三方插件目录可在装配器配置中追加（`PLUGIN_DIRS` 环境变量，可选）。

---

## 2. 前端：运行时发现与动态挂载

1. 新增 `yunshu-ui/src/plugins/pluginDiscovery.ts`：

```ts
// 拉取 /api/plugins，返回 { plugins: PluginInfo[] }
export interface PluginInfo {
  name: string;
  version: string;
  description: string;
  schema: Record<string, any> | null;
  routes: string[];
}
export async function fetchPlugins(): Promise<PluginInfo[]> { ... }
```

2. `PluginPanel`（阶段 3 产物）改为：
   - 挂载时 `fetchPlugins()`，提供「刷新」按钮（调 `POST /api/plugins/reload` 后再拉取）；
   - 新插件出现时自动出现在列表中，无需发版。

3. **插槽动态挂载（可选进阶）**：插件 manifest 若声明 `clientSlot?: { slotId: string; module: string }`（如 `"module": "/plugins/my-plugin.js"`），前端用动态 `import()` 加载该模块，调用其 `register(slotRegistry)` 导出函数挂进插槽：

```ts
// pluginDiscovery.ts 进阶
const mod = await import(/* @vite-ignore */ info.clientSlot.module);
if (typeof mod.register === 'function') mod.register(registry);
```

> ⚠️ 动态 import 跨域/路径需按 Vite 的 `public/` 或后端静态目录约束；单人内部工具建议先用「Schema 驱动面板」（无需动态 JS）覆盖 90% 场景，动态 JS 装载作为进阶能力。

---

## 3. 完成标准（阶段 4 结束）

- [ ] 新写一个 `plugins/demo_plugin.py`（含 schema）放入目录 → 启动后自动出现在 `/api/plugins`
- [ ] `POST /api/plugins/reload` 不重启进程即可刷新清单；单插件损坏不影响启动
- [ ] 前端插件面板有「刷新」按钮，能发现新插件
- [ ] （进阶）manifest 声明 `clientSlot` 的插件可被前端动态加载并挂入插槽
