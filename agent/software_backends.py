"""
软件后端模块 - 提供不同平台的软件安装支持
"""
import logging
import json
import uuid

logger = logging.getLogger(__name__)


def _trace_id():
    """生成简短 trace_id"""
    return uuid.uuid4().hex[:16]


class ChocolateyBackend:
    """Chocolatey 后端（Windows）"""
    def install(self, package_name, version=None):
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "software_backends", "action": "software_backend.chocolatey.install", "package_name": package_name}, ensure_ascii=False))
        return True

    def uninstall(self, package_name):
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "software_backends", "action": "software_backend.chocolatey.uninstall", "package_name": package_name}, ensure_ascii=False))
        return True

    def update(self, package_name):
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "software_backends", "action": "software_backend.chocolatey.update", "package_name": package_name}, ensure_ascii=False))
        return True


class PipBackend:
    """Pip 后端"""
    def install(self, package_name, version=None):
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "software_backends", "action": "software_backend.pip.install", "package_name": package_name}, ensure_ascii=False))
        return True

    def uninstall(self, package_name):
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "software_backends", "action": "software_backend.pip.uninstall", "package_name": package_name}, ensure_ascii=False))
        return True

    def update(self, package_name):
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "software_backends", "action": "software_backend.pip.update", "package_name": package_name}, ensure_ascii=False))
        return True


class NpmBackend:
    """Npm 后端"""
    def install(self, package_name, version=None):
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "software_backends", "action": "software_backend.npm.install", "package_name": package_name}, ensure_ascii=False))
        return True

    def uninstall(self, package_name):
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "software_backends", "action": "software_backend.npm.uninstall", "package_name": package_name}, ensure_ascii=False))
        return True

    def update(self, package_name):
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "software_backends", "action": "software_backend.npm.update", "package_name": package_name}, ensure_ascii=False))
        return True


class WebDownloadBackend:
    """Web 下载后端"""
    def download(self, url, destination):
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "software_backends", "action": "software_backend.webdownload.download", "url": url, "destination": destination}, ensure_ascii=False))
        return True


class GitHubBackend:
    """GitHub 后端"""
    def clone(self, repo_url, destination):
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "software_backends", "action": "software_backend.github.clone", "repo_url": repo_url, "destination": destination}, ensure_ascii=False))
        return True
