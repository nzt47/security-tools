"""
软件后端模块 - 提供不同平台的软件安装支持
"""
import logging
import json
import uuid

logger = logging.getLogger(__name__)

def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]



class ChocolateyBackend:
    """Chocolatey 后端（Windows）"""
    def install(self, package_name, version=None):
        logger.info(f"[Chocolatey] 安装: {package_name}")
        return True
    
    def uninstall(self, package_name):
        logger.info(f"[Chocolatey] 卸载: {package_name}")
        return True
    
    def update(self, package_name):
        logger.info(f"[Chocolatey] 更新: {package_name}")
        return True


class PipBackend:
    """Pip 后端"""
    def install(self, package_name, version=None):
        logger.info(f"[Pip] 安装: {package_name}")
        return True
    
    def uninstall(self, package_name):
        logger.info(f"[Pip] 卸载: {package_name}")
        return True
    
    def update(self, package_name):
        logger.info(f"[Pip] 更新: {package_name}")
        return True


class NpmBackend:
    """Npm 后端"""
    def install(self, package_name, version=None):
        logger.info(f"[Npm] 安装: {package_name}")
        return True
    
    def uninstall(self, package_name):
        logger.info(f"[Npm] 卸载: {package_name}")
        return True
    
    def update(self, package_name):
        logger.info(f"[Npm] 更新: {package_name}")
        return True


class WebDownloadBackend:
    """Web 下载后端"""
    def download(self, url, destination):
        logger.info(f"[WebDownload] 下载: {url} -> {destination}")
        return True


class GitHubBackend:
    """GitHub 后端"""
    def clone(self, repo_url, destination):
        logger.info(f"[GitHub] 克隆: {repo_url} -> {destination}")
        return True


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
            "module_name": "software_backends",
            "action": action + ".failed",
            "error": f"{type(e).__name__}: {e}",
        }, ensure_ascii=False))
        raise
