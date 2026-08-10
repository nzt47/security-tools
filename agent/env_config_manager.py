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

P1 安全加固:
- .env 文件创建/写入后自动设置权限为 600（仅 owner 可读写）
- Unix: os.chmod(path, 0o600)
- Windows: icacls /inheritance:r 移除继承 + 仅授权当前用户与 SYSTEM
- 失败降级为 warning，不阻塞主流程

P3 配置审计:
- 所有 set/delete 操作自动写入 logs/config_audit.jsonl
- 敏感 key（API_KEY/TOKEN/WEBHOOK/SECRET 等）的值自动脱敏
- 审计日志独立于业务日志，便于合规审计与事后溯源
"""

import getpass
import json
import logging
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime
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

    def __init__(self, env_file_path: str | Path | None = None):
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
        # 【CHG-2026-0810】跨进程文件锁（.env.lock）：防多进程并发写 .env
        # 与 _file_lock（进程内线程锁）互补：进程内互斥 + 跨进程互斥
        self._lock_file = Path(str(self._env_file) + '.lock')
        self._lock_fd = None
        self._ensure_file_exists()

    def _ensure_file_exists(self):
        """确保 .env 文件存在，不存在则创建空文件并设置权限"""
        if not self._env_file.exists():
            self._env_file.touch()
            logger.info(log_dict({
                'module_name': 'env_config',
                'action': 'env_config.init',
                'message': f'[Env配置] 已创建 .env 文件: {self._env_file}'
            }))
        # 【P1 安全加固】无论新建还是已存在，确保权限为 600
        # 已存在文件可能由其他工具创建（如 vim touch），权限未必符合要求
        self._secure_file_permissions()

    def get(self, key: str, default: str | None = None) -> str | None:
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
            # 【P3 配置审计】记录修改前的值（用于审计追踪）
            old_value = os.environ.get(key)
            self._update_env_file(key, value)
            # 同步更新 os.environ（热重载，立即生效）
            os.environ[key] = value
            # 【P3 配置审计】写入审计日志（脱敏后）
            self._audit_log('set', key, old_value, value)
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
            # 【P3 配置审计】记录删除前的值（用于审计追踪）
            old_value = os.environ.get(key)
            self._remove_from_env_file(key)
            os.environ.pop(key, None)
            # 【P3 配置审计】写入审计日志（脱敏后）
            self._audit_log('delete', key, old_value, None)
            logger.info(log_dict({
                'module_name': 'env_config',
                'action': 'env_config.delete',
                'message': f'[Env配置] 已删除 {key}'
            }))

    # ──────────────────────────────────────────────────────────────────────────
    # 【P3 配置审计日志】配置变更追踪
    #
    # 设计原则（三义）:
    # - 【不易】审计日志独立于业务日志，写入 logs/config_audit.jsonl
    #   每行一条 JSON，便于工具化分析与合规审计
    # - 【变易】敏感 key（API_KEY/TOKEN/WEBHOOK/SECRET 等）的值自动脱敏
    #   保留前 4 + 后 4 字符，中间用 *** 替代，防止明文泄露
    # - 【简易】失败降级为 warning，不阻塞主流程（写入已成功，不应回滚）
    # ──────────────────────────────────────────────────────────────────────────

    # 敏感 key 匹配模式（大小写不敏感）
    _SENSITIVE_KEY_PATTERN = re.compile(
        r'API_KEY|TOKEN|WEBHOOK|SECRET|PASSWORD|CREDENTIAL',
        re.IGNORECASE
    )

    def _mask_sensitive_value(self, key: str, value: str | None) -> str | None:
        """脱敏敏感配置值

        规则:
        - 非敏感 key：原值返回
        - 敏感 key 且值长度 > 8：保留前 4 + *** + 后 4
        - 敏感 key 且值长度 <= 8：全替换为 ***
        - None 值：返回 None（用于 delete 操作的 new_value）

        Args:
            key: 配置 key（用于判断是否敏感）
            value: 配置值

        Returns:
            脱敏后的值
        """
        if value is None:
            return None
        if not self._SENSITIVE_KEY_PATTERN.search(key):
            return value
        # 敏感 key 脱敏
        if len(value) <= 8:
            return '***'
        return f'{value[:4]}***{value[-4:]}'

    def _get_audit_log_path(self) -> Path:
        """获取审计日志文件路径

        路径: <项目根>/logs/config_audit.jsonl
        目录不存在时自动创建
        """
        project_root = Path(__file__).resolve().parent.parent
        logs_dir = project_root / 'logs'
        logs_dir.mkdir(parents=True, exist_ok=True)
        return logs_dir / 'config_audit.jsonl'

    def _audit_log(self, action: str, key: str, old_value: str | None, new_value: str | None):
        """写入配置变更审计日志

        Args:
            action: 操作类型（'set' / 'delete'）
            key: 配置 key
            old_value: 修改前的值（将脱敏）
            new_value: 修改后的值（将脱敏）
        """
        try:
            entry = {
                'timestamp': datetime.now().isoformat(),
                'action': action,
                'key': key,
                'old_value': self._mask_sensitive_value(key, old_value),
                'new_value': self._mask_sensitive_value(key, new_value),
                'user': getpass.getuser(),
                'pid': os.getpid(),
                'trace_id': os.environ.get('TRACE_ID'),
            }
            log_path = self._get_audit_log_path()
            # JSONL 格式：每行一个 JSON 对象，追加写入
            with open(log_path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(entry, ensure_ascii=False) + '\n')
        except Exception as e:
            # 失败降级：仅 warning，不阻塞主流程
            # 原因：审计日志是合规增强，不应破坏配置写入（写入已成功）
            logger.warning(log_dict({
                'module_name': 'env_config',
                'action': 'env_config.audit_log_failed',
                'message': f'[Env配置] 审计日志写入失败（不阻塞主流程）: {e}'
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

    def _backup_env_file(self):
        """【CHG-2026-0810】写前自动备份 .env 到 .env.backups/（防误清空兜底）

        背景: 并行会话测试/脚本曾把仓库根 .env 覆盖为单 KEY 测试值（事故复盘见
        docs/reports/parallel_session_sync_notice_20260810.md）。
        【不易】.env 是唯一敏感数据来源，任何写入前先留可回滚快照；
        即使被误写/误清空，也能从最近备份一键恢复。
        仅备份非空文件（空文件无可备份内容），避免为每次空写生成无用备份。
        """
        if not self._env_file.exists() or self._env_file.stat().st_size == 0:
            return
        backups_dir = self._env_file.parent / '.env.backups'
        backups_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime('%Y%m%d%H%M%S')
        backup_path = backups_dir / f'env.bak.{stamp}'
        i = 1
        while backup_path.exists():  # 同一秒多次写入：追加序号
            backup_path = backups_dir / f'env.bak.{stamp}_{i}'
            i += 1
        shutil.copy2(self._env_file, backup_path)
        logger.info(log_dict({
            'module_name': 'env_config',
            'action': 'env_config.backup',
            'message': f'[Env配置] 写入前已备份 .env → {backup_path.name}'
        }))
        self._prune_backups(backups_dir)

    @staticmethod
    def _prune_backups(backups_dir: Path, keep: int = 50):
        """保留最近 keep 份备份，清理更旧的（防无限堆积）"""
        try:
            backups = sorted(backups_dir.glob('env.bak.*'),
                             key=lambda p: p.name, reverse=True)
            for old in backups[keep:]:
                old.unlink()
        except OSError:
            pass  # 清理失败不影响主流程

    def _acquire_process_lock(self):
        """获取跨进程文件锁（写 .env 期间与其它进程互斥）

        【变易】Windows 用 msvcrt.locking，Unix 用 fcntl.flock；
        锁文件为独立的 .env.lock（不锁 .env 本体，避免 rename 替换导致锁失效）。
        注意: msvcrt.locking 要求锁定区域至少 1 字节，空文件需先写占位字节。
        【CHG-2026-0810】日志增强：记录获取耗时，>1s 提示可能锁竞争
        （多进程同时写 .env 时的排查线索）。
        """
        t0 = time.monotonic()
        logger.info(log_dict({
            'module_name': 'env_config',
            'action': 'env_config.lock_acquire_start',
            'message': f'[Env配置] 尝试获取跨进程锁: {self._lock_file.name}'
        }))
        self._lock_fd = open(self._lock_file, 'a+', encoding='utf-8')
        if sys.platform == 'win32':
            import msvcrt
            self._lock_fd.seek(0, 2)
            if self._lock_fd.tell() == 0:
                self._lock_fd.write('\0')
                self._lock_fd.flush()
            self._lock_fd.seek(0)
            msvcrt.locking(self._lock_fd.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(self._lock_fd.fileno(), fcntl.LOCK_EX)
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        logger.info(log_dict({
            'module_name': 'env_config',
            'action': 'env_config.lock_acquired',
            'message': f'[Env配置] 已获取跨进程锁（耗时 {elapsed_ms}ms）',
            'lock_wait_ms': elapsed_ms
        }))
        if elapsed_ms > 1000:
            logger.warning(log_dict({
                'module_name': 'env_config',
                'action': 'env_config.lock_contention',
                'message': (
                    f'[Env配置] 锁获取耗时 {elapsed_ms}ms > 1s，'
                    f'疑似其它进程正在写 .env（锁竞争），请排查'
                ),
                'lock_wait_ms': elapsed_ms
            }))

    def _release_process_lock(self):
        """释放跨进程文件锁（与 _acquire_process_lock 配对）"""
        try:
            if sys.platform == 'win32':
                import msvcrt
                msvcrt.locking(self._lock_fd.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._lock_fd.fileno(), fcntl.LOCK_UN)
        finally:
            self._lock_fd.close()
            self._lock_fd = None
        logger.info(log_dict({
            'module_name': 'env_config',
            'action': 'env_config.lock_released',
            'message': f'[Env配置] 已释放跨进程锁: {self._lock_file.name}'
        }))

    def _update_env_file(self, key: str, value: str):
        """更新 .env 文件中的某个 KEY（存在则更新，不存在则追加）"""
        # 【CHG-2026-0810】写前自动备份（防误清空兜底）
        self._backup_env_file()

        lines = []
        found = False

        if self._env_file.exists():
            content = self._env_file.read_text(encoding='utf-8')
            # 【CHG-2026-0810】空内容守卫：文件非空但读出来为空 → 疑似被误清空
            if content.strip() == '' and self._env_file.stat().st_size > 0:
                logger.warning(log_dict({
                    'module_name': 'env_config',
                    'action': 'env_config.empty_content_guard',
                    'message': (
                        f'[Env配置] 警告: .env 存在但内容为空'
                        f'（{self._env_file.stat().st_size} 字节），疑似被误清空，'
                        f'已自动备份，本次写入继续'
                    )
                }))
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
        # 【CHG-2026-0810】写前自动备份（防误清空兜底）
        self._backup_env_file()

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
        【CHG-2026-0810】全程持有跨进程文件锁，与其它进程写 .env 互斥
        （进程内互斥由调用方的 self._file_lock 保证）。
        """
        # 跨进程文件锁（确保目录存在后锁文件可创建）
        self._env_file.parent.mkdir(parents=True, exist_ok=True)
        t0 = time.monotonic()
        logger.info(log_dict({
            'module_name': 'env_config',
            'action': 'env_config.write_start',
            'message': f'[Env配置] 开始写入 .env（{len(lines)} 行）'
        }))
        self._acquire_process_lock()
        try:
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

                # 【P1 安全加固】rename 后权限继承自临时文件（mkstemp 默认 0o600 + umask），
                # 不一定符合 600 要求（特别是 Windows），需显式重设
                self._secure_file_permissions()
            except Exception:
                # 清理临时文件
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
                raise
        finally:
            self._release_process_lock()
        logger.info(log_dict({
            'module_name': 'env_config',
            'action': 'env_config.write_done',
            'message': (
                f'[Env配置] 写入 .env 完成（{len(lines)} 行，'
                f'总耗时 {int((time.monotonic() - t0) * 1000)}ms）'
            )
        }))

    def _secure_file_permissions(self):
        """【P1 安全】设置 .env 文件权限为 600（仅 owner 可读写）

        跨平台策略:
        - Unix/Linux: os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        - Windows: icacls /inheritance:r 移除继承 + /grant:r 仅授权当前用户与 SYSTEM

        设计原则（三义）:
        - 【不易】.env 是唯一敏感数据存储，权限保护是核心安全边界
        - 【变易】跨平台差异通过运行时分支处理
        - 【简易】失败降级为 warning，不阻塞主流程（避免权限设置失败导致写入回滚）
        """
        if not self._env_file.exists():
            return

        try:
            if sys.platform == 'win32':
                # Windows: icacls 限制 ACL
                # /inheritance:r 移除所有继承权限
                # /grant:r 替换式授权（仅保留指定的）
                username = os.environ.get('USERNAME') or os.environ.get('USER', '')
                cmd = ['icacls', str(self._env_file), '/inheritance:r']
                if username:
                    cmd.extend(['/grant:r', f'{username}:F'])
                cmd.extend(['/grant:r', 'SYSTEM:F'])

                # CREATE_NO_WINDOW 避免弹出控制台窗口
                creationflags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
                # 中文 Windows 下 icacls 输出 GBK 编码，默认 utf-8 解码会失败
                # 使用 errors='replace' 容错（我们只关心 returncode，不依赖输出内容）
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    encoding='utf-8',
                    errors='replace',
                    timeout=5,
                    creationflags=creationflags,
                    check=False,
                )
                if result.returncode != 0:
                    logger.warning(log_dict({
                        'module_name': 'env_config',
                        'action': 'env_config.secure_permissions_failed',
                        'message': f'[Env配置] Windows icacls 设置权限失败: {result.stderr.strip()}'
                    }))
                    return
            else:
                # Unix/Linux: chmod 600（仅 owner 可读写）
                os.chmod(self._env_file, stat.S_IRUSR | stat.S_IWUSR)

            logger.info(log_dict({
                'module_name': 'env_config',
                'action': 'env_config.secure_permissions',
                'message': f'[Env配置] 已设置 .env 文件权限为 600（仅 owner 可读写）'
            }))
        except Exception as e:
            # 失败降级：仅 warning，不抛异常
            # 原因：权限设置是安全增强，不应破坏主流程（写入已成功）
            logger.warning(log_dict({
                'module_name': 'env_config',
                'action': 'env_config.secure_permissions_failed',
                'message': f'[Env配置] 设置文件权限失败（不阻塞主流程）: {e}'
            }))


# 模块级单例（懒加载）
_instance: EnvConfigManager | None = None


def get_env_config_manager() -> EnvConfigManager:
    """获取 EnvConfigManager 单例实例"""
    global _instance
    if _instance is None:
        _instance = EnvConfigManager()
    # [mypy 窄化] _instance 全局声明为 EnvConfigManager | None，
    # mypy 无法通过 if 窄化 global 变量类型，assert 显式收窄为 EnvConfigManager
    assert _instance is not None
    return _instance
