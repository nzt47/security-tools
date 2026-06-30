"""扩展安装引擎 — 提供通用的下载、解压、依赖安装能力

所有扩展类型的安装器共享此引擎的基础功能：
- 从 GitHub / URL / 本地路径 获取扩展包
- 解析和安装 Python 依赖
- 验证扩展包的完整性
- 统一错误处理和日志
"""

import json
import uuid
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
import tarfile
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]



class InstallEngine:
    """安装引擎 — 处理扩展包的获取、解压和依赖安装"""

    @staticmethod
    def parse_source(source: str) -> Tuple[str, str, str]:
        """解析来源字符串为 (type, location, detail)

        支持的格式:
          - github:user/repo[/path]     → GitHub 仓库
          - url:https://...              → 直接 URL
          - local:/path/to/dir           → 本地目录
          - npm:package-name             → npm 包
          - pip:package-name             → pip 包
          - path:/path/to/dir            → 本地路径（同 local）
        """
        if ":" in source:
            scheme, rest = source.split(":", 1)
            if scheme == "github":
                parts = rest.split("/", 2)
                if len(parts) >= 2:
                    repo = f"{parts[0]}/{parts[1]}"
                    subpath = parts[2] if len(parts) > 2 else ""
                    return ("github", repo, subpath)
                return ("github", rest, "")
            elif scheme in ("url", "https", "http"):
                return ("url", rest if scheme == "url" else source, "")
            elif scheme in ("local", "path"):
                return ("local", rest, "")
            elif scheme == "npm":
                return ("npm", rest, "")
            elif scheme == "pip":
                return ("pip", rest, "")
            elif scheme == "builtin":
                return ("builtin", rest, "")

        # 默认：本地路径
        return ("local", source, "")

    @staticmethod
    def download_from_github(repo: str, subpath: str, target_dir: Path) -> bool:
        """从 GitHub 下载扩展包

        尝试顺序：
        1. 使用 gh CLI（推荐，支持认证）
        2. 使用 git clone
        3. 下载 ZIP 归档
        """
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "installer", "action": "github.repo.subpath", "msg": f"[安装引擎] 从 GitHub 下载: {repo}/{subpath}"}, ensure_ascii=False))

        # 方法 1: gh CLI
        try:
            result = subprocess.run(
                ["gh", "repo", "view", repo, "--json", "name"],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode == 0:
                logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "installer", "action": "cli.repo", "msg": f"[安装引擎] 使用 gh CLI 克隆: {repo}"}, ensure_ascii=False))
                clone_dir = target_dir / "repo"
                subprocess.run(
                    ["git", "clone", "--depth", "1",
                     f"https://github.com/{repo}.git", str(clone_dir)],
                    capture_output=True, timeout=120,
                )
                if clone_dir.exists():
                    if subpath:
                        src = clone_dir / subpath
                        if src.exists():
                            InstallEngine._merge_dir(src, target_dir)
                            shutil.rmtree(clone_dir, ignore_errors=True)
                            return True
                    else:
                        InstallEngine._merge_dir(clone_dir, target_dir)
                        shutil.rmtree(clone_dir, ignore_errors=True)
                        return True
            return False
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

        # 方法 2: git clone
        try:
            clone_dir = target_dir / "repo"
            subprocess.run(
                ["git", "clone", "--depth", "1",
                 f"https://github.com/{repo}.git", str(clone_dir)],
                capture_output=True, timeout=120,
            )
            if clone_dir.exists():
                if subpath:
                    src = clone_dir / subpath
                    if src.exists():
                        InstallEngine._merge_dir(src, target_dir)
                        shutil.rmtree(clone_dir, ignore_errors=True)
                        return True
                else:
                    InstallEngine._merge_dir(clone_dir, target_dir)
                    shutil.rmtree(clone_dir, ignore_errors=True)
                    return True
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            logger.warning(json.dumps({"trace_id": _trace_id(), "module_name": "installer", "action": "git.clone", "msg": f"[安装引擎] git clone 失败: {e}"}, ensure_ascii=False))

        # 方法 3: 下载 ZIP
        try:
            import urllib.request
            zip_url = f"https://api.github.com/repos/{repo}/zipball/main"
            zip_path = target_dir / "archive.zip"
            logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "installer", "action": "zip.zip_url", "msg": f"[安装引擎] 下载 ZIP: {zip_url}"}, ensure_ascii=False))

            urllib.request.urlretrieve(zip_url, zip_path)
            if zip_path.exists():
                with zipfile.ZipFile(zip_path, "r") as zf:
                    # ZIP 中第一层是 GitHub 自动生成的目录名，跳过
                    members = zf.namelist()
                    top_dir = members[0].split("/")[0] if members else ""
                    for member in members:
                        parts = member.split("/", 1)
                        if len(parts) > 1:
                            dest = target_dir / parts[1]
                        else:
                            continue
                        if member.endswith("/"):
                            dest.mkdir(parents=True, exist_ok=True)
                        else:
                            dest.parent.mkdir(parents=True, exist_ok=True)
                            with zf.open(member) as src_f:
                                with open(dest, "wb") as dst_f:
                                    dst_f.write(src_f.read())

                zip_path.unlink()
                logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "installer", "action": "zip", "msg": f"[安装引擎] ZIP 下载并解压完成"}, ensure_ascii=False))
                return True
        except Exception as e:
            logger.warning(json.dumps({"trace_id": _trace_id(), "module_name": "installer", "action": "zip", "msg": f"[安装引擎] ZIP 下载失败: {e}"}, ensure_ascii=False))

        return False

    @staticmethod
    def download_from_url(url: str, target_dir: Path) -> bool:
        """从 URL 下载扩展包"""
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "installer", "action": "url.url", "msg": f"[安装引擎] 从 URL 下载: {url}"}, ensure_ascii=False))
        try:
            import urllib.request
            parsed = urlparse(url)
            # 如果是 GitHub raw 内容
            if "raw.githubusercontent.com" in parsed.netloc:
                file_path = target_dir / os.path.basename(parsed.path)
                urllib.request.urlretrieve(url, file_path)
                return True

            # ZIP 文件
            if url.endswith(".zip"):
                zip_path = target_dir / "ext.zip"
                urllib.request.urlretrieve(url, zip_path)
                with zipfile.ZipFile(zip_path, "r") as zf:
                    zf.extractall(target_dir)
                zip_path.unlink()
                return True

            # tar.gz
            if url.endswith(".tar.gz") or url.endswith(".tgz"):
                tgz_path = target_dir / "ext.tar.gz"
                urllib.request.urlretrieve(url, tgz_path)
                with tarfile.open(tgz_path, "r:gz") as tf:
                    tf.extractall(target_dir)
                tgz_path.unlink()
                return True

            # 通用文件下载
            file_path = target_dir / os.path.basename(parsed.path.rstrip("/"))
            urllib.request.urlretrieve(url, file_path)
            return True

        except Exception as e:
            logger.error(json.dumps({"trace_id": _trace_id(), "module_name": "installer", "action": "url", "msg": f"[安装引擎] URL 下载失败: {e}"}, ensure_ascii=False))
            return False

    @staticmethod
    def copy_from_local(source_path: str, target_dir: Path) -> bool:
        """从本地路径复制扩展包"""
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "installer", "action": "source_path", "msg": f"[安装引擎] 从本地复制: {source_path}"}, ensure_ascii=False))
        src = Path(source_path)
        if not src.exists():
            logger.error(json.dumps({"trace_id": _trace_id(), "module_name": "installer", "action": "source_path", "msg": f"[安装引擎] 本地路径不存在: {source_path}"}, ensure_ascii=False))
            return False

        try:
            if src.is_file():
                shutil.copy2(src, target_dir / src.name)
            elif src.is_dir():
                InstallEngine._merge_dir(src, target_dir)
            return True
        except Exception as e:
            logger.error(json.dumps({"trace_id": _trace_id(), "module_name": "installer", "action": "log", "msg": f"[安装引擎] 本地复制失败: {e}"}, ensure_ascii=False))
            return False

    @staticmethod
    def install_npm_package(package: str, target_dir: Path) -> bool:
        """安装 npm 包到目标目录"""
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "installer", "action": "npm.package", "msg": f"[安装引擎] 安装 npm 包: {package}"}, ensure_ascii=False))
        try:
            result = subprocess.run(
                ["npm", "install", "--prefix", str(target_dir), package],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode != 0:
                logger.warning(json.dumps({"trace_id": _trace_id(), "module_name": "installer", "action": "npm.result.stderr", "msg": f"[安装引擎] npm 安装失败: {result.stderr[:200]}"}, ensure_ascii=False))
                return False
            return True
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            logger.warning(json.dumps({"trace_id": _trace_id(), "module_name": "installer", "action": "npm", "msg": f"[安装引擎] npm 不可用: {e}"}, ensure_ascii=False))
            return False

    @staticmethod
    def install_pip_package(package: str) -> bool:
        """安装 pip 包"""
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "installer", "action": "pip.package", "msg": f"[安装引擎] 安装 pip 包: {package}"}, ensure_ascii=False))
        try:
            subprocess.run(
                [sys.executable, "-m", "pip", "install", package],
                capture_output=True, text=True, timeout=120,
            )
            return True
        except subprocess.TimeoutExpired as e:
            logger.error(json.dumps({"trace_id": _trace_id(), "module_name": "installer", "action": "pip", "msg": f"[安装引擎] pip 安装超时: {e}"}, ensure_ascii=False))
            return False

    @staticmethod
    def install_dependencies(dependencies: List[str]) -> List[str]:
        """批量安装 Python 依赖，返回失败列表"""
        failed = []
        for dep in dependencies:
            if not InstallEngine.install_pip_package(dep):
                failed.append(dep)
        return failed

    @staticmethod
    def ensure_dir(path: Path) -> Path:
        """确保目录存在"""
        path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def _merge_dir(src: Path, dst: Path):
        """合并源目录到目标目录（递归复制）"""
        for item in src.iterdir():
            s = src / item.name
            d = dst / item.name
            if s.is_dir():
                shutil.copytree(s, d, dirs_exist_ok=True)
            else:
                shutil.copy2(s, d)

    @staticmethod
    def detect_package_type(target_dir: Path) -> Optional[str]:
        """检测扩展包的类型

        返回: "python" | "node" | "static" | None
        """
        files = set(f.name for f in target_dir.iterdir() if f.is_file())

        if "setup.py" in files or "pyproject.toml" in files:
            return "python"
        if "package.json" in files:
            return "node"
        if "extension.json" in files or "manifest.json" in files:
            return "static"

        # 检查子目录
        for f in target_dir.iterdir():
            if f.is_dir():
                sub_files = set(sf.name for sf in f.iterdir() if sf.is_file())
                if "setup.py" in sub_files or "pyproject.toml" in sub_files:
                    return "python"
                if "package.json" in sub_files:
                    return "node"

        return None

    @staticmethod
    def load_extension_json(target_dir: Path) -> Optional[Dict[str, Any]]:
        """从扩展目录加载 extension.json / manifest.json"""
        for name in ("extension.json", "manifest.json"):
            path = target_dir / name
            if path.exists():
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        return json.load(f)
                except json.JSONDecodeError:
                    pass
        return None
