"""插件依赖管理 — 处理插件间的依赖关系和版本兼容

提供：
  - 依赖解析和安装
  - 版本冲突检测
  - 依赖树管理
  - 依赖缓存机制
"""

import json
import logging
import subprocess
import sys
import pkg_resources
from typing import Dict, Any, List, Optional, Tuple
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field

from agent.monitoring.tracing import get_trace_id

logger = logging.getLogger(__name__)


@dataclass
class Dependency:
    """依赖信息"""
    name: str
    version: str = ""
    optional: bool = False
    source: str = ""


@dataclass
class DependencyResolution:
    """依赖解析结果"""
    success: bool
    dependencies: List[Dict] = field(default_factory=list)
    conflicts: List[Dict] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)


class DependencyManager:
    """依赖管理器"""

    def __init__(self):
        self._cache: Dict[str, Dict] = {}
        self._installed_deps: Dict[str, str] = {}
        self._load_installed_deps()

    def _load_installed_deps(self):
        """加载已安装的依赖"""
        try:
            for pkg in pkg_resources.working_set:
                self._installed_deps[pkg.project_name.lower()] = pkg.version
        except Exception as e:
            logger.warning(json.dumps({"trace_id": get_trace_id(), "module_name": "dependency_manager", "action": "log", "msg": f"加载已安装依赖失败: {e}"}, ensure_ascii=False))

    def parse_dependencies(self, deps_str: str) -> List[Dependency]:
        """解析依赖字符串"""
        deps = []
        if not deps_str:
            return deps

        for line in deps_str.split('\n'):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            
            parts = line.split('==')
            name = parts[0].strip()
            version = parts[1].strip() if len(parts) > 1 else ""
            
            optional = False
            if name.endswith('[optional]'):
                name = name.replace('[optional]', '').strip()
                optional = True
            
            deps.append(Dependency(
                name=name,
                version=version,
                optional=optional
            ))
        
        return deps

    def resolve_dependencies(self, dependencies: List[Dependency]) -> DependencyResolution:
        """解析依赖并检查兼容性"""
        result = DependencyResolution(success=True)
        
        for dep in dependencies:
            info = self._check_dependency(dep)
            
            if info.get('installed'):
                if info.get('compatible'):
                    result.dependencies.append(info)
                else:
                    result.conflicts.append(info)
                    result.success = False
            elif dep.optional:
                info['status'] = 'optional_missing'
                result.dependencies.append(info)
            else:
                result.missing.append(dep.name)
                result.success = False
        
        return result

    def _check_dependency(self, dep: Dependency) -> Dict:
        """检查单个依赖"""
        name_lower = dep.name.lower()
        installed_version = self._installed_deps.get(name_lower)
        
        info = {
            'name': dep.name,
            'required_version': dep.version,
            'installed_version': installed_version,
            'installed': installed_version is not None,
            'optional': dep.optional,
        }
        
        if not installed_version:
            info['compatible'] = False
            info['reason'] = '未安装'
            return info
        
        if not dep.version:
            info['compatible'] = True
            info['reason'] = '无需版本限制'
            return info
        
        if self._check_version_compatibility(installed_version, dep.version):
            info['compatible'] = True
            info['reason'] = '版本兼容'
        else:
            info['compatible'] = False
            info['reason'] = f'版本不兼容: 安装了 {installed_version}, 需要 {dep.version}'
        
        return info

    def _check_version_compatibility(self, installed: str, required: str) -> bool:
        """检查版本兼容性"""
        try:
            from packaging import version
            installed_ver = version.parse(installed)
            
            if required.startswith('>='):
                min_ver = version.parse(required[2:])
                return installed_ver >= min_ver
            elif required.startswith('<='):
                max_ver = version.parse(required[2:])
                return installed_ver <= max_ver
            elif required.startswith('~='):
                # 兼容版本
                spec_ver = version.parse(required[2:])
                return installed_ver.major == spec_ver.major and installed_ver >= spec_ver
            elif required.startswith('!='):
                not_ver = version.parse(required[2:])
                return installed_ver != not_ver
            elif required.startswith('>'):
                min_ver = version.parse(required[1:])
                return installed_ver > min_ver
            elif required.startswith('<'):
                max_ver = version.parse(required[1:])
                return installed_ver < max_ver
            else:
                # 精确匹配
                return installed == required
        except Exception as e:
            logger.warning(json.dumps({"trace_id": get_trace_id(), "module_name": "dependency_manager", "action": "log", "msg": f"版本检查失败: {e}"}, ensure_ascii=False))
            return True

    def install_dependencies(self, dependencies: List[Dependency], 
                            force: bool = False) -> Dict:
        """安装依赖"""
        results = {
            'installed': [],
            'failed': [],
            'skipped': [],
        }

        for dep in dependencies:
            if dep.optional and not force:
                results['skipped'].append(dep.name)
                continue
            
            name = dep.name
            version_spec = dep.version
            
            if version_spec:
                pkg_spec = f"{name}=={version_spec}"
            else:
                pkg_spec = name
            
            try:
                logger.info(json.dumps({"trace_id": get_trace_id(), "module_name": "dependency_manager", "action": "pkg_spec", "msg": f"安装依赖: {pkg_spec}"}, ensure_ascii=False))
                result = subprocess.run(
                    [sys.executable, "-m", "pip", "install", pkg_spec],
                    capture_output=True,
                    text=True,
                    timeout=120
                )
                
                if result.returncode == 0:
                    results['installed'].append(dep.name)
                    logger.info(json.dumps({"trace_id": get_trace_id(), "module_name": "dependency_manager", "action": "dep.name", "msg": f"依赖安装成功: {dep.name}"}, ensure_ascii=False))
                else:
                    results['failed'].append({
                        'name': dep.name,
                        'error': result.stderr[:200]
                    })
                    logger.error(json.dumps({"trace_id": get_trace_id(), "module_name": "dependency_manager", "action": "dep.name", "msg": f"依赖安装失败: {dep.name}"}, ensure_ascii=False))
            
            except Exception as e:
                results['failed'].append({
                    'name': dep.name,
                    'error': str(e)
                })
                logger.error(json.dumps({"trace_id": get_trace_id(), "module_name": "dependency_manager", "action": "dep.name", "msg": f"依赖安装异常: {dep.name} - {e}"}, ensure_ascii=False))
        
        return results

    def get_dependency_tree(self, plugin_id: str, dependencies: List[Dependency]) -> Dict:
        """获取依赖树"""
        tree = {
            'plugin_id': plugin_id,
            'direct': [],
            'transitive': [],
        }
        
        for dep in dependencies:
            dep_info = {
                'name': dep.name,
                'version': dep.version,
                'optional': dep.optional,
                'installed': dep.name.lower() in self._installed_deps,
            }
            
            if dep.optional:
                tree['direct'].append(dep_info)
            else:
                tree['direct'].append(dep_info)
        
        return tree

    def check_extension_dependencies(self, ext_id: str, 
                                    ext_dependencies: List[str],
                                    installed_exts: List[str]) -> Dict:
        """检查扩展依赖"""
        missing = []
        for dep_ext in ext_dependencies:
            if dep_ext not in installed_exts:
                missing.append(dep_ext)
        
        return {
            'ext_id': ext_id,
            'dependencies': ext_dependencies,
            'missing': missing,
            'satisfied': [d for d in ext_dependencies if d not in missing],
            'all_satisfied': len(missing) == 0,
        }

    def generate_requirements(self, dependencies: List[Dependency]) -> str:
        """生成 requirements.txt 格式内容"""
        lines = []
        for dep in dependencies:
            if dep.version:
                lines.append(f"{dep.name}=={dep.version}")
            else:
                lines.append(dep.name)
        return '\n'.join(lines)

    def get_installed_packages(self) -> List[Dict]:
        """获取所有已安装包"""
        packages = []
        try:
            for pkg in pkg_resources.working_set:
                packages.append({
                    'name': pkg.project_name,
                    'version': pkg.version,
                    'location': pkg.location,
                    'requires': [str(r) for r in pkg.requires()],
                })
        except Exception as e:
            logger.warning(json.dumps({"trace_id": get_trace_id(), "module_name": "dependency_manager", "action": "log", "msg": f"获取已安装包失败: {e}"}, ensure_ascii=False))
        return packages

    def log_action(self, action: str, message: str, details: Dict = None):
        """记录操作日志"""
        logger.info(json.dumps({
            "trace_id": get_trace_id(),
            "module_name": "dependency_manager",
            "action": action,
            "message": message,
            "details": details or {},
            "timestamp": datetime.now().isoformat()
        }))


def get_dependency_manager() -> DependencyManager:
    """获取依赖管理器实例"""
    return DependencyManager()