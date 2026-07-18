"""EnvConfigManager — .env 文件配置管理器

职责:
- 管理 .env 文件的读写（单一敏感数据来源）
- 提供线程安全的 get/set/delete 操作
- 写入后自动更新 os.environ（热重载）
- 保留 .env 文件原有注释和格式

设计原则（三义）:
- 【不易】.env 是唯一敏感数据存储，不加密，依赖文件系统权限保护
- 【变易】所有修改都写入 .env 文件（UI 修改 → .env → os.environ → 代码读取）
- 【简易】原子写入：写临时文件 → rename，防止写入中断导致文件损坏

替代 SecureConfigManager:
- 取消加密存储中间层
- .env 文件作为唯一敏感数据来源
- UI 修改直接写入 .env，热重载到 os.environ
"""

import logging
import os
import tempfile
import threading
from pathlib import Path

from agent.logging_utils import log_dict

logger = logging.getLogger(__name__)


class EnvConfigManager:
    """.env 文件配置管理器

    线程安全，支持并发读写。
    所有写入操作自动同步到 os.environ（热重载）。

    Usage:
        manager = EnvConfigManager()
        manager.set('LLM_API_KEY', 'sk-xxx')  # 写入 .env + os.environ
        value = manager.get('LLM_API_KEY')     # 从 os.environ 读取
    """

    def __init__(self, env_file_path: str | Path = None):
        """初始化

        Args:
            env_file_path: .env 文件路径，默认为项目根目录的 .env
        """
        if env_file_path is None:
            # 项目根目录（agent/ 的上一级）
            project_root = Path(__file__).resolve().parent.parent
            env_file_path = project_root / ".env"
        self._env_file = Path(env_file_path)
        self._file_lock = threading.Lock()
        self._ensure_file_exists()

    def _ensure_file_exists(self):
        """确保 .env 文件存在，不存在则创建空文件"""
        if not self._env_file.exists():
            self._env_file.touch()
            logger.info(log_dict({
                'module_name': 'env_config',
                'action': 'env_config.init',
                'message': f'[Env配置] 已创建 .env 文件: {self._env_file}'
            }))

    def get(self, key: str, default: str = None) -> str:
        """从环境变量读取配置值

        Args:
            key: 环境变量名（如 'LLM_API_KEY'）
            default: 默认值

        Returns:
            配置值，未找到返回 default
        """
        return os.getenv(key, default)

    def set(self, key: str, value: str):
        """写入配置到 .env 文件 + 更新 os.environ（热重载）

        Args:
            key: 环境变量名（如 'LLM_API_KEY'）
            value: 配置值
        """
        with self._file_lock:
            self._update_env_file(key, value)
            # 同步更新 os.environ（热重载，立即生效）
            os.environ[key] = value
            logger.info(log_dict({
                'module_name': 'env_config',
                'action': 'env_config.set',
                'message': f'[Env配置] 已更新 {key}（.env + os.environ）'
            }))

    def delete(self, key: str):
        """从 .env 文件删除配置 + 从 os.environ 移除

        Args:
            key: 环境变量名
        """
        with self._file_lock:
            self._remove_from_env_file(key)
            os.environ.pop(key, None)
            logger.info(log_dict({
                'module_name': 'env_config',
                'action': 'env_config.delete',
                'message': f'[Env配置] 已删除 {key}'
            }))

    def reload(self):
        """重新加载 .env 文件到 os.environ

        用于手动触发重载（如外部修改了 .env 文件后）
        """
        with self._file_lock:
            if not self._env_file.exists():
                return
            content = self._env_file.read_text(encoding='utf-8')
            for line in content.splitlines():
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if '=' in line:
                    k, v = line.split('=', 1)
                    k = k.strip()
                    v = v.strip()
                    # 移除可能的引号
                    if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                        v = v[1:-1]
                    os.environ[k] = v
            logger.info(log_dict({
                'module_name': 'env_config',
                'action': 'env_config.reload',
                'message': '[Env配置] 已重新加载 .env 到 os.environ'
            }))

    def _update_env_file(self, key: str, value: str):
        """更新 .env 文件中的某个 KEY（存在则更新，不存在则追加）"""
        lines = []
        found = False

        if self._env_file.exists():
            content = self._env_file.read_text(encoding='utf-8')
            lines = content.splitlines()
            for i, line in enumerate(lines):
                stripped = line.strip()
                if stripped and not stripped.startswith('#') and '=' in stripped:
                    k = stripped.split('=', 1)[0].strip()
                    if k == key:
                        lines[i] = f'{key}={value}'
                        found = True
                        break

        if not found:
            # 追加到末尾
            if lines and lines[-1].strip():
                lines.append('')  # 空行分隔
            lines.append(f'{key}={value}')

        self._atomic_write(lines)

    def _remove_from_env_file(self, key: str):
        """从 .env 文件移除某个 KEY"""
        if not self._env_file.exists():
            return

        content = self._env_file.read_text(encoding='utf-8')
        lines = content.splitlines()
        new_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped and not stripped.startswith('#') and '=' in stripped:
                k = stripped.split('=', 1)[0].strip()
                if k == key:
                    continue  # 跳过要删除的行
            new_lines.append(line)

        self._atomic_write(new_lines)

    def _atomic_write(self, lines):
        """原子写入：写临时文件 → rename

        防止写入中断导致 .env 文件损坏。
        Windows 兼容：rename 不能覆盖已存在文件，需先删除目标。
        """
        # 确保目录存在
        self._env_file.parent.mkdir(parents=True, exist_ok=True)

        # 写入临时文件
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=str(self._env_file.parent),
            prefix='.env_',
            suffix='.tmp'
        )
        try:
            with os.fdopen(tmp_fd, 'w', encoding='utf-8') as f:
                f.write('\n'.join(lines))
                if lines:
                    f.write('\n')  # 末尾换行

            # Windows 需要先移除目标文件（os.rename 不能覆盖已存在文件）
            if self._env_file.exists():
                self._env_file.unlink()

            # rename 临时文件到目标（原子操作）
            os.rename(tmp_path, str(self._env_file))
        except Exception:
            # 清理临时文件
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise


# 模块级单例（懒加载）
_instance: EnvConfigManager | None = None


def get_env_config_manager() -> EnvConfigManager:
    """获取 EnvConfigManager 单例实例"""
    global _instance
    if _instance is None:
        _instance = EnvConfigManager()
    return _instance