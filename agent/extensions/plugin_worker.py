"""第三方插件包子进程 worker（P2 进程隔离，2026-11-22）。

【协议】stdin/stdout 各一行 JSON（UTF-8），stdout **只**走协议；插件自己的
print 被重定向到 stderr，避免污染协议。

  宿主 → worker: {"op":"init","config":{...}}
  worker → 宿主: {"op":"ready","tools":[{"name","description","schema"}]}
  宿主 → worker: {"op":"call","tool":"x","args":{...}}
  worker → 宿主: {"ok":true,"result":...} / {"ok":false,"error":"...","error_code":"..."}
  宿主 → worker: {"op":"shutdown"}
  worker → 宿主: {"ok":true}

【安全边界（如实声明）】
- 本模块只负责**进程边界 + 超时硬杀 + 最小环境变量**；它不是文件系统/网络沙箱。
- 插件代码仍以当前用户身份运行，能读当前用户能读的文件；真正的 OS 级沙箱
  （AppContainer / seccomp / 容器）是下一步。
- 环境变量只传白名单（PATH/SYSTEMROOT/TEMP/PYTHONPATH 等），**不传**
  FLASK_API_TOKEN / DEEPSEEK_API_KEY 等密钥。
"""
from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

_PROTOCOL_OUT = sys.stdout
_MAX_RESULT_CHARS = 2_000_000


def _write(obj: Dict[str, Any]) -> None:
    _PROTOCOL_OUT.write(json.dumps(obj, ensure_ascii=False) + "\n")
    _PROTOCOL_OUT.flush()


def _read() -> Dict[str, Any] | None:
    line = sys.stdin.readline()
    if not line:
        return None
    try:
        obj = json.loads(line)
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def _load_plugin(install_path: str):
    p = Path(install_path)
    if p.is_file():
        sys.path.insert(0, str(p.parent))
        module_name = p.stem
    else:
        # 包目录：import 的是包名，sys.path 要放**父目录**
        sys.path.insert(0, str(p.parent))
        module_name = p.name
    deps = (p / ".deps") if p.is_dir() else None
    if deps is not None and deps.is_dir():
        sys.path.insert(0, str(deps))
    module = importlib.import_module(module_name)
    cls = getattr(module, "Plugin", None)
    instance = cls() if cls is not None else getattr(module, "plugin", None)
    if instance is None:
        raise RuntimeError("插件未导出 Plugin 类或 plugin 实例")
    return instance


def _call_handler(handler, args: Dict[str, Any]):
    result = handler(**args)
    if inspect.iscoroutine(result):
        result = asyncio.run(result)
    return result


def main() -> int:
    if len(sys.argv) < 3:
        return 2
    install_path, plugin_id = sys.argv[1], sys.argv[2]
    # 插件自己的 print / 第三方库的 stdout 一律进 stderr；协议只走 _PROTOCOL_OUT。
    sys.stdout = sys.stderr
    init = _read()
    if init is None:
        _write({"op": "error", "error": "缺少 init 消息"})
        return 2
    config = init.get("config") if isinstance(init.get("config"), dict) else {}
    try:
        instance = _load_plugin(install_path)
        if hasattr(instance, "on_load"):
            instance.on_load({"plugin_id": plugin_id, "config": config})
        tools: List[Dict[str, Any]] = []
        handlers: Dict[str, Any] = {}
        if hasattr(instance, "get_tools"):
            for item in instance.get_tools() or []:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()
                if not name:
                    continue
                handler = item.get("handler")
                if not callable(handler):
                    continue
                handlers[name] = handler
                schema = item.get("schema")
                tools.append({
                    "name": name,
                    "description": str(item.get("description") or ""),
                    "schema": schema if isinstance(schema, dict) else {},
                })
        _write({"op": "ready", "tools": tools})
    except BaseException as exc:  # noqa: BLE001  worker 必须把启动失败告知宿主
        _write({"op": "error", "error": type(exc).__name__})
        return 2

    while True:
        msg = _read()
        if msg is None:
            break
        op = str(msg.get("op") or "")
        if op == "shutdown":
            try:
                if hasattr(instance, "on_unload"):
                    instance.on_unload()
            except Exception:
                pass
            _write({"ok": True})
            return 0
        if op != "call":
            _write({"ok": False, "error": "未知 op", "error_code": "PLUGIN_BAD_REQUEST"})
            continue
        tool = str(msg.get("tool") or "")
        args = msg.get("args") if isinstance(msg.get("args"), dict) else {}
        handler = handlers.get(tool)
        if handler is None:
            _write({"ok": False, "error": "未知插件工具: " + tool,
                    "error_code": "PLUGIN_TOOL_NOT_FOUND"})
            continue
        try:
            result = _call_handler(handler, args)
            try:
                text = json.dumps(result, ensure_ascii=False, default=str)
            except Exception:
                text = json.dumps(str(result), ensure_ascii=False)
            if len(text) > _MAX_RESULT_CHARS:
                _write({"ok": False, "error": "插件返回结果过大",
                        "error_code": "PLUGIN_RESULT_TOO_LARGE"})
                continue
            _PROTOCOL_OUT.write('{"ok":true,"result":' + text + "}\n")
            _PROTOCOL_OUT.flush()
        except BaseException as exc:  # noqa: BLE001  单次调用失败不能拖垮 worker
            _write({"ok": False, "error": type(exc).__name__,
                    "error_code": "PLUGIN_TOOL_ERROR"})


if __name__ == "__main__":
    raise SystemExit(main())
