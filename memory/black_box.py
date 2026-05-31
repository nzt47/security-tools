"""黑匣子日志 - JSONL 格式，按文件大小滚动，支持查询与分析
加密版本: 使用AES-256-GCM加密敏感数据字段

并发安全：
    使用 threading.Lock() 保护文件写入操作，确保多线程安全。
"""

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)
logger.info("[BlackBox] 加载中...")

HAS_SECURITY_UTILS = False


def _load_security_utils():
    """延迟加载安全工具模块，避免循环导入"""
    global HAS_SECURITY_UTILS, LogEncryptor, DataSanitizer

    if HAS_SECURITY_UTILS:
        return True

    try:
        import agent
        import agent.security_utils
        globals()['LogEncryptor'] = agent.security_utils.LogEncryptor
        globals()['DataSanitizer'] = agent.security_utils.DataSanitizer
        HAS_SECURITY_UTILS = True
        logger.info("安全工具模块已加载")
        return True
    except (ImportError, AttributeError, ValueError) as e:
        logger.warning(f"安全工具模块加载失败: {e}，日志将以明文存储")
        return False


class BlackBoxError(Exception):
    """黑匣子操作异常"""
    pass


class BlackBox:
    """黑匣子日志系统

    以 JSONL 格式记录事件，按文件大小自动滚动。
    支持按时间、事件类型、关键字查询。

    安全特性:
    - 敏感数据自动脱敏 (API Key、密码、邮箱、电话等)
    - 加密存储 (AES-256-GCM)
    - 支持解密查询

    文件命名：blackbox_001.jsonl, blackbox_002.jsonl, ...
    """

    DEFAULT_ENCRYPT_FIELDS = ["data", "user_input", "response", "content", "message"]
    DEFAULT_SANITIZE_FIELDS = ["data", "user_input", "response", "content", "message"]

    def __init__(self, log_dir="./memory_data/blackbox",
                 max_size_bytes=10 * 1024 * 1024,
                 max_files=10,
                 encryption_enabled=True,
                 encryption_key_env="LINGXI_ENCRYPT_KEY"):
        """初始化黑匣子

        Args:
            log_dir: 日志目录
            max_size_bytes: 单个文件最大大小
            max_files: 保留的最大文件数
            encryption_enabled: 是否启用加密
            encryption_key_env: 加密密钥环境变量名
        """
        logger.info("[BlackBox] __init__ 开始初始化")
        self.log_dir = Path(log_dir)
        self.max_size_bytes = max_size_bytes
        self.max_files = max_files
        self._counter = 0
        self._write_lock = threading.Lock()
        logger.info(f"[BlackBox] 日志目录: {self.log_dir}")
        logger.info(f"[BlackBox] 单个文件最大: {self.max_size_bytes} 字节")
        logger.info(f"[BlackBox] 最大文件数: {self.max_files}")
        self._ensure_dir()

        _load_security_utils()

        self.encryption_enabled = encryption_enabled and HAS_SECURITY_UTILS
        self.encryptor = None
        self.sanitizer = None
        self.encrypt_fields = self.DEFAULT_ENCRYPT_FIELDS
        self.sanitize_fields = self.DEFAULT_SANITIZE_FIELDS

        if self.encryption_enabled:
            logger.info("[BlackBox] 初始化加密模块")
            try:
                self.encryptor = LogEncryptor(key_env_var=encryption_key_env)
                self.sanitizer = DataSanitizer()
                logger.info("[BlackBox] 加密模块已初始化")
                logger.info(f"  - 加密字段: {self.encrypt_fields}")
                logger.info(f"  - 脱敏字段: {self.sanitize_fields}")
            except Exception as e:
                logger.error(f"[BlackBox] 初始化加密模块失败: {e}")
                self.encryption_enabled = False
        else:
            logger.warning("[BlackBox] 黑匣子以明文模式运行")
        
        logger.info("[BlackBox] __init__ 初始化完成")

    def _ensure_dir(self):
        """确保日志目录存在"""
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def _sanitize_entry(self, entry):
        """脱敏日志条目中的敏感信息"""
        logger.debug(f"[黑匣子] 开始脱敏处理: {entry['id']}")
        if not self.sanitizer:
            logger.debug(f"[黑匣子] 无脱敏器，跳过")
            return entry

        result = dict(entry)
        for field in self.sanitize_fields:
            if field in result:
                logger.debug(f"[黑匣子] 处理字段: {field}")
                if isinstance(result[field], dict):
                    logger.debug(f"[黑匣子] 字典类型，调用 sanitize_dict")
                    result[field] = self.sanitizer.sanitize_dict(result[field])
                elif isinstance(result[field], str):
                    logger.debug(f"[黑匣子] 字符串类型，调用 sanitize_string")
                    result[field] = self.sanitizer.sanitize_string(result[field])
        logger.debug(f"[黑匣子] 脱敏完成")
        return result

    def _encrypt_entry(self, entry):
        """加密日志条目"""
        logger.debug(f"[黑匣子] 开始加密处理: {entry['id']}")
        if not self.encryptor:
            logger.debug(f"[黑匣子] 无加密器，跳过")
            return entry

        result = dict(entry)
        for field in self.encrypt_fields:
            if field in result and result[field] is not None:
                logger.debug(f"[黑匣子] 加密字段: {field}")
                result[field] = self.encryptor.encrypt_string(json.dumps(result[field], ensure_ascii=False))
                result[f"_{field}_encrypted"] = True
                logger.debug(f"[黑匣子] {field} 已加密")
        logger.debug(f"[黑匣子] 加密完成")
        return result

    def _decrypt_entry(self, entry):
        """解密日志条目"""
        logger.debug(f"[黑匣子] 开始解密处理: {entry['id']}")
        if not self.encryptor:
            logger.debug(f"[黑匣子] 无加密器，跳过")
            return entry

        result = dict(entry)
        encrypted_fields = [k for k in result.keys() if k.startswith('_') and k.endswith('_encrypted')]
        logger.debug(f"[黑匣子] 发现 {len(encrypted_fields)} 个加密字段")

        for enc_key in encrypted_fields:
            field_name = enc_key[1:-10]
            if field_name in result:
                try:
                    logger.debug(f"[黑匣子] 解密字段: {field_name}")
                    decrypted = self.encryptor.decrypt_string(str(result[field_name]))
                    result[field_name] = json.loads(decrypted)
                    logger.debug(f"[黑匣子] {field_name} 已解密")
                except (json.JSONDecodeError, TypeError) as e:
                    logger.warning(f"[黑匣子] {field_name} 解密失败: {e}")
        logger.debug(f"[黑匣子] 解密完成")
        return result

    def _get_current_file(self):
        """获取当前写入文件（最新编号的文件）"""
        files = sorted(self.log_dir.glob("blackbox_*.jsonl"))
        if not files:
            return self.log_dir / "blackbox_001.jsonl"
        return files[-1]

    def _next_file(self):
        """创建下一个编号的文件"""
        files = sorted(self.log_dir.glob("blackbox_*.jsonl"))
        if not files:
            return self.log_dir / "blackbox_001.jsonl"
        last_num = int(files[-1].stem.split("_")[1])
        new_file = self.log_dir / f"blackbox_{last_num + 1:03d}.jsonl"
        self._enforce_max_files()
        return new_file

    def _enforce_max_files(self):
        """删除超出 max_files 的最旧文件"""
        files = sorted(self.log_dir.glob("blackbox_*.jsonl"))
        while len(files) >= self.max_files:
            files[0].unlink()
            files = sorted(self.log_dir.glob("blackbox_*.jsonl"))

    def log(self, event_type, data):
        """记录一条事件日志，返回事件 ID（线程安全）

        安全处理流程:
        1. 脱敏敏感数据
        2. 加密数据字段
        3. 写入JSONL文件
        """
        self._counter += 1
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        entry = {
            "id": f"bb_{self._counter:04d}",
            "timestamp": timestamp,
            "event_type": event_type,
            "data": data,
            "_encrypted": self.encryption_enabled
        }

        logger.debug(f"[黑匣子] 开始记录日志: {entry['id']}")
        logger.debug(f"[黑匣子] 事件类型: {event_type}")
        logger.debug(f"[黑匣子] 加密状态: {self.encryption_enabled}")

        entry = self._sanitize_entry(entry)
        entry = self._encrypt_entry(entry)

        line = json.dumps(entry, ensure_ascii=False) + "\n"

        try:
            with self._write_lock:
                current = self._get_current_file()
                if current.exists() and current.stat().st_size + len(line.encode()) > self.max_size_bytes:
                    current = self._next_file()
                    logger.debug(f"[黑匣子] 文件大小超限，切换到新文件")

                with open(current, "a", encoding="utf-8") as f:
                    f.write(line)

                if self.encryption_enabled:
                    logger.debug(f"加密日志写入成功: {entry['id']}")
                else:
                    logger.debug(f"明文日志写入成功: {entry['id']}")
        except OSError as e:
            raise BlackBoxError(f"写入日志失败: {e}") from e

        return entry["id"]

    def query(self, event_type=None, start=None, end=None, search=None, limit=100, decrypt=True):
        """查询日志条目"""
        results = []
        files = sorted(self.log_dir.glob("blackbox_*.jsonl"), reverse=True)
        logger.debug(f"[黑匣子] 查询开始，找到 {len(files)} 个文件")

        for file_path in files:
            logger.debug(f"[黑匣子] 读取文件: {file_path.name}")
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                        except json.JSONDecodeError:
                            continue

                        if decrypt and entry.get("_encrypted"):
                            logger.debug(f"[黑匣子] 需要解密")
                            entry = self._decrypt_entry(entry)

                        if event_type and entry.get("event_type") != event_type:
                            continue
                        if start and entry.get("timestamp", "") < start:
                            continue
                        if end and entry.get("timestamp", "") > end:
                            continue
                        if search:
                            data_str = json.dumps(entry.get("data", {}), ensure_ascii=False)
                            if search not in data_str:
                                continue

                        results.append(entry)
                        if len(results) >= limit:
                            logger.debug(f"[黑匣子] 达到返回结果数量已达限制")
                            return results
            except OSError:
                continue

        logger.debug(f"[黑匣子] 查询完成，返回 {len(results)} 条记录")
        return results

    def analyze(self, event_type=None):
        """统计分析日志"""
        files = sorted(self.log_dir.glob("blackbox_*.jsonl"), reverse=True)
        type_counts = {}
        total_encrypted = 0

        for file_path in files:
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                        except json.JSONDecodeError:
                            continue

                        if entry.get("_encrypted"):
                            total_encrypted += 1
                        et = entry.get("event_type", "unknown")
                        type_counts[et] = type_counts.get(et, 0) + 1
            except OSError:
                continue

        result = type_counts if not event_type else {"count": type_counts.get(event_type, 0)}
        if total_encrypted > 0:
            result["_metadata"] = {
                "total_entries": sum(type_counts.values()),
                "encrypted_entries": total_encrypted,
                "encryption_enabled": self.encryption_enabled
            }
        return result

    def get_stats(self):
        """获取黑匣子统计信息"""
        files = sorted(self.log_dir.glob("blackbox_*.jsonl"))
        total_size = sum(f.stat().st_size for f in files)
        entry_count = 0

        for file_path in files:
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    entry_count += sum(1 for line in f if line.strip())
            except OSError:
                continue

        return {
            "file_count": len(files),
            "total_size_bytes": total_size,
            "total_entries": entry_count,
            "encryption_enabled": self.encryption_enabled,
            "encryption_available": HAS_SECURITY_UTILS,
            "max_file_size": self.max_size_bytes,
            "max_files": self.max_files
        }

    def migrate_to_encrypted(self):
        """迁移现有明文日志到加密格式"""
        logger.info("=== 开始迁移日志到加密格式...")
        if not self.encryption_enabled:
            logger.error("加密未启用，无法迁移")
            return {"migrated": 0, "errors": ["encryption_disabled"]}

        migrated = 0
        errors = []

        files = sorted(self.log_dir.glob("blackbox_*.jsonl"))
        logger.info(f"发现 {len(files)} 个文件待处理")

        for file_idx, file_path in enumerate(files):
            logger.info(f"处理文件 {file_idx+1}/{len(files)}: {file_path.name}")
            temp_path = file_path.with_suffix('.tmp')
            file_migrated = 0
            try:
                with open(file_path, "r", encoding="utf-8") as src, \
                     open(temp_path, "w", encoding="utf-8") as dst:
                    for line_idx, line in enumerate(src):
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                            if not entry.get("_encrypted"):
                                logger.debug(f"处理条目 {line_idx+1}: 明文，需要加密")
                                entry = self._sanitize_entry(entry)
                                entry = self._encrypt_entry(entry)
                                entry["_encrypted"] = True
                                entry["_migrated"] = True
                            else:
                                logger.debug(f"处理条目 {line_idx+1}: 已加密，跳过")
                            dst.write(json.dumps(entry, ensure_ascii=False) + "\n")
                            file_migrated += 1
                        except json.JSONDecodeError as e:
                            logger.warning(f"JSON解析错误: {e}")
                            errors.append(f"JSON解析错误: {e}")
                            continue

                temp_path.replace(file_path)
                migrated += file_migrated
                logger.info(f"迁移完成: {file_path.name}, {file_migrated} 条记录")
            except Exception as e:
                logger.error(f"迁移失败 {file_path.name}: {e}")
                errors.append(f"迁移失败 {file_path.name}: {e}")
                if temp_path.exists():
                    temp_path.unlink()
                continue

        logger.info(f"迁移完成: {migrated} 条记录")
        return {"migrated": migrated, "errors": errors}
