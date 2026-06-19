"""
DigitalLife 状态与快照管理 Mixin

从 digital_life.py 提取的状态/快照/日志管理相关方法。

期望宿主类提供以下属性:
- _vector_memory: VectorStore | None
- _snapshot_manager: StateSnapshotManager | None
- _memory: MemoryManager
- _session_id, _interaction_count, _running, _started_at, _current_mode
- _health_check_interval, _last_health_check, _config, _reflection_history
- _behavior: BehaviorController
- body: BodySensor
"""

import logging
from typing import Optional

from agent.state_manager import (
    get_state_manager as _get_state_manager,
    StateSaveResult,
    StateLoadResult,
)

logger = logging.getLogger(__name__)


class DigitalLifeStateMixin:
    """DigitalLife 状态与快照管理 (Mix-in)"""

    # ── 向量记忆 ──

    def get_memory_stats(self) -> dict:
        """获取向量记忆统计"""
        vs = getattr(self, '_vector_memory', None)
        if not vs:
            return {"available": False}
        return {
            "available": True,
            "total_memories": len(vs.items),
            "collection_name": vs.collection_name,
            "persist_dir": vs.persist_dir,
        }

    def search_memory(self, query: str, top_k: int = 5) -> list:
        """搜索向量记忆"""
        vs = getattr(self, '_vector_memory', None)
        if not vs:
            return []
        try:
            return vs.search(query, top_k)
        except Exception as e:
            logger.error("搜索记忆失败: %s", e)
            return []

    def _combined_search(self, query: str, limit: int = 10) -> str:
        """合并搜索向量记忆 + 黑匣子日志"""
        seen = set()
        all_results = []

        vector_hits = self.search_memory(query, top_k=limit)
        for item in vector_hits:
            text = item.content if hasattr(item, 'content') else str(item)
            if text not in seen:
                seen.add(text)
                all_results.append(("🧠 语义记忆", text))

        memory = getattr(self, '_memory', None)
        if memory:
            log_hits = memory.query_logs(search=query, limit=limit)
            for r in log_hits:
                text = f"[{r.get('event_type', '?')}] {r.get('data', {})}"
                if text not in seen:
                    seen.add(text)
                    all_results.append(("📋 事件日志", text))

        if not all_results:
            return f"没有找到与 '{query}' 相关的记忆。"

        lines = [f"找到 {len(all_results)} 条相关记忆："]
        for src, text in all_results[:limit]:
            lines.append(f"  {src} {text[:200]}")
        return "\n".join(lines)

    def clear_memory(self):
        """清空向量记忆"""
        vs = getattr(self, '_vector_memory', None)
        if vs:
            vs.clear()

    # ── P6 快照功能 ──

    def save_snapshot(self, snapshot_id: Optional[str] = None,
                      incremental: bool = False, force: bool = False) -> dict:
        """保存当前状态快照"""
        sm = getattr(self, '_snapshot_manager', None)
        if not sm:
            from agent.p6_snapshot import SnapshotResult
            return SnapshotResult(success=False, error_message="P6快照管理器未启用")

        logger.info("[P6] 保存状态快照...")
        result = sm.save_snapshot(self, snapshot_id=snapshot_id,
                                  incremental=incremental, force=force)
        if result.success:
            logger.info("[P6] [OK] 快照保存成功: %s", result.snapshot_id)
        else:
            logger.error("[P6] [FAIL] 快照保存失败: %s", result.error_message)
        return result

    def load_snapshot(self, snapshot_id: Optional[str] = None):
        """从快照恢复状态"""
        sm = getattr(self, '_snapshot_manager', None)
        if not sm:
            logger.error("[P6] [FAIL] P6快照管理器未启用")
            return None
        logger.info("[P6] 从快照恢复状态...")
        restored = sm.load_snapshot(digital_life_class=self.__class__,
                                     snapshot_id=snapshot_id)
        if restored:
            logger.info("[P6] [OK] 快照恢复成功")
        else:
            logger.error("[P6] [FAIL] 快照恢复失败")
        return restored

    def list_snapshots(self) -> list:
        """列出所有可用快照"""
        sm = getattr(self, '_snapshot_manager', None)
        return sm.list_snapshots() if sm else []

    def get_snapshot_performance(self) -> dict:
        """获取快照性能统计"""
        sm = getattr(self, '_snapshot_manager', None)
        if not sm:
            return {"available": False}
        return sm.performance_monitor.get_performance_summary()

    def print_snapshot_performance_panel(self):
        """打印快照性能监控面板"""
        sm = getattr(self, '_snapshot_manager', None)
        if not sm:
            print("P6快照管理器未启用")
            return
        sm.performance_monitor.print_performance_panel()

    def get_p6_snapshot_status(self) -> dict:
        """获取P6快照系统状态"""
        from agent.p6_snapshot import P6SnapshotManager
        available = P6SnapshotManager is not None
        return {
            "available": available,
            "enabled": getattr(self, '_snapshot_manager', None) is not None,
            "snapshots": self.list_snapshots(),
            "performance": self.get_snapshot_performance(),
        }

    # ── 状态持久化 ──

    def _build_state_data(self) -> dict:
        """构建状态数据字典"""
        state = {
            "version": "2.0",
            "session_id": getattr(self, '_session_id', None),
            "interaction_count": getattr(self, '_interaction_count', 0),
            "running": getattr(self, '_running', False),
            "started_at": getattr(self, '_started_at', None),
            "current_mode": (getattr(self, '_current_mode', None) or "").value
                           if hasattr(getattr(self, '_current_mode', None), 'value')
                           else str(getattr(self, '_current_mode', "NORMAL")),
            "health_check_interval": getattr(self, '_health_check_interval', 30),
            "last_health_check": getattr(self, '_last_health_check', None),
            "config": getattr(self, '_config', {}),
        }
        refs = getattr(self, '_reflection_history', None)
        if refs:
            state["reflection_history"] = refs[-10:]
        behavior = getattr(self, '_behavior', None)
        if behavior and hasattr(behavior, '_current_mode'):
            cm = behavior._current_mode
            state["behavior"] = {
                "current_mode": cm.value if hasattr(cm, 'value') else str(cm),
            }
        body = getattr(self, 'body', None)
        if body and hasattr(body, 'get_health_report'):
            try:
                state["body_status"] = body.get_health_report()
            except Exception:
                pass
        return state

    def save_state(self, state_id: Optional[str] = None) -> dict:
        """保存当前运行状态到文件"""
        try:
            state_data = self._build_state_data()
            result: StateSaveResult = _get_state_manager().save_state(state_data, state_id=state_id)
            if result.success:
                logger.info("状态保存成功: %s", result.state_id)
                return {"ok": True, "state_id": result.state_id,
                        "file_path": result.file_path, "data_size": result.data_size,
                        "elapsed_ms": result.elapsed_ms, "created_at": result.created_at}
            return {"ok": False, "error": result.error_message}
        except ImportError:
            return {"ok": False, "error": "状态管理器模块未找到"}
        except Exception as e:
            logger.error("状态保存异常: %s", e)
            return {"ok": False, "error": str(e)}

    def load_state(self, state_id: Optional[str] = None) -> dict:
        """从文件加载运行状态"""
        try:
            result: StateLoadResult = _get_state_manager().load_state(state_id=state_id)
            if result.success:
                data = result.state_data
                if 'interaction_count' in data:
                    self._interaction_count = data['interaction_count']
                if 'current_mode' in data:
                    try:
                        from agent.behavior_controller import BehaviorMode
                        mv = data['current_mode']
                        if hasattr(BehaviorMode, mv):
                            self._current_mode = getattr(BehaviorMode, mv)
                    except Exception:
                        pass
                if 'config' in data:
                    self._config = data['config']
                logger.info("状态加载成功: %s", result.state_id)
                return {"ok": True, "state_id": result.state_id,
                        "file_path": result.file_path, "elapsed_ms": result.elapsed_ms,
                        "state_data": data}
            return {"ok": False, "error": result.error_message}
        except ImportError:
            return {"ok": False, "error": "状态管理器模块未找到"}
        except Exception as e:
            logger.error("状态加载异常: %s", e)
            return {"ok": False, "error": str(e)}

    def list_states(self) -> list:
        """列出所有可用的状态文件"""
        try:
            mgr = _get_state_manager()
            return [{"state_id": s.state_id, "file_path": s.file_path,
                     "created_at": s.created_at.isoformat(),
                     "data_size": s.data_size, "version": s.version}
                    for s in mgr.list_states()]
        except ImportError:
            return []
        except Exception as e:
            logger.error("列出状态失败: %s", e)
            return []

    # ── 日志级别管理 ──

    def set_log_level(self, level: str, logger_name: Optional[str] = None) -> dict:
        """动态调整日志级别"""
        try:
            if _get_state_manager().set_log_level(level, logger_name):
                logger.info("日志级别调整成功: %s", level)
                return {"ok": True, "level": level, "logger": logger_name or "root"}
            return {"ok": False, "error": f"无效的日志级别: {level}"}
        except ImportError:
            return {"ok": False, "error": "状态管理器模块未找到"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def get_log_level(self, logger_name: Optional[str] = None) -> dict:
        """获取当前日志级别"""
        try:
            level = _get_state_manager().get_log_level(logger_name)
            return {"ok": True, "level": level, "logger": logger_name or "root"}
        except ImportError:
            return {"ok": False, "error": "状态管理器模块未找到"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def list_loggers(self) -> list:
        """列出所有已注册的日志记录器及其级别"""
        try:
            mgr = _get_state_manager()
            return [{"name": n, "level": l} for n, l in mgr.list_loggers()]
        except ImportError:
            return []
        except Exception as e:
            logger.error("列出日志记录器失败: %s", e)
            return []

    def __del__(self):
        """析构时释放资源"""
        try:
            self.stop()
            memory = getattr(self, '_memory', None)
            if memory:
                del self._memory
            body = getattr(self, 'body', None)
            if body:
                body.stop_file_watch()
                body.stop_event_monitor()
        except Exception:
            pass
