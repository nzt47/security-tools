import logging
import json
import uuid

logger = logging.getLogger(__name__)

def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]


class SoftwareManager:
    def __init__(self):
        self._installed_software = {}
    
    def check_updates(self):
        return []
    
    def install(self, software_name):
        return True
    
    def uninstall(self, software_name):
        return True
    
    def get_installed_software(self):
        return list(self._installed_software.keys())
    
    def is_installed(self, software_name):
        return software_name in self._installed_software


def _safe_call(func, *args, action="safe_call", **kwargs):
    """安全调用包装器——捕获异常并记录结构化日志后重新抛出

    用于边界显性化：可能失败的操作应通过此包装器调用，
    确保异常被记录后再向上传播，而非静默吞掉。
    """
    try:
        return func(*args, **kwargs)
    except Exception as e:
        logger.error(json.dumps({
            "trace_id": _trace_id(),
            "module_name": "software_manager",
            "action": action + ".failed",
            "error": f"{type(e).__name__}: {e}",
        }, ensure_ascii=False))
        raise
