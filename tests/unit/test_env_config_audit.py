"""EnvConfigManager 配置审计日志单元测试

测试范围:
1. 敏感 key 脱敏规则（_mask_sensitive_value）
2. set/delete 操作的审计日志写入（_audit_log）
3. 审计日志 JSONL 格式与字段完整性
4. 失败降级（审计日志写入失败不阻塞主流程）

设计原则（三义）:
- 【不易】审计日志是合规护城河，脱敏规则与字段不可变
- 【变易】测试通过 monkeypatch 重定向日志路径到临时目录，避免污染真实 logs/
- 【简易】每个用例独立隔离，无副作用
"""
import json
import os
from pathlib import Path

import pytest

from agent.env_config_manager import EnvConfigManager


# ════════════════════════════════════════════════════════════════════════════
# Fixture
# ════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def isolated_manager(tmp_path, monkeypatch):
    """创建隔离的 EnvConfigManager 实例

    - .env 文件写入临时目录（避免污染真实 .env）
    - 审计日志路径重定向到临时目录（避免污染真实 logs/）
    - 自动清理测试期间设置的环境变量
    """
    env_file = tmp_path / ".env"
    audit_log = tmp_path / "logs" / "config_audit.jsonl"
    # 预创建 logs 目录（真实 _get_audit_log_path 会自动创建，monkeypatch 替换后需手动）
    audit_log.parent.mkdir(parents=True, exist_ok=True)

    manager = EnvConfigManager(env_file_path=env_file)
    # 重定向审计日志路径到临时目录
    monkeypatch.setattr(manager, '_get_audit_log_path', lambda: audit_log)

    yield manager, audit_log

    # 清理测试设置的环境变量
    test_keys = ['LLM_API_KEY', 'DEBUG_MODE', 'SEARCH_TOKEN', 'WEBHOOK_URL',
                 'DB_PASSWORD', 'API_CREDENTIAL', 'NON_SENSITIVE_KEY', 'MODEL_NAME']
    for k in test_keys:
        os.environ.pop(k, None)


# ════════════════════════════════════════════════════════════════════════════
# 1. 脱敏规则测试
# ════════════════════════════════════════════════════════════════════════════

class TestMaskSensitiveValue:
    """敏感 key 脱敏规则测试"""

    def test_sensitive_key_long_value(self, isolated_manager):
        """敏感 key + 长 value（>8 字符）→ 前 4 + *** + 后 4"""
        manager, _ = isolated_manager
        result = manager._mask_sensitive_value('LLM_API_KEY', 'sk-1234567890abcdef')
        assert result == 'sk-1***cdef'

    def test_sensitive_key_short_value(self, isolated_manager):
        """敏感 key + 短 value（<=8 字符）→ ***"""
        manager, _ = isolated_manager
        result = manager._mask_sensitive_value('LLM_API_KEY', 'sk-123')
        assert result == '***'

    def test_sensitive_key_boundary_length_8(self, isolated_manager):
        """敏感 key + 正好 8 字符 value → ***（边界值）"""
        manager, _ = isolated_manager
        result = manager._mask_sensitive_value('LLM_API_KEY', '12345678')
        assert result == '***'

    def test_sensitive_key_boundary_length_9(self, isolated_manager):
        """敏感 key + 正好 9 字符 value → 前 4 + *** + 后 4（边界值）"""
        manager, _ = isolated_manager
        result = manager._mask_sensitive_value('LLM_API_KEY', '123456789')
        assert result == '1234***6789'

    def test_non_sensitive_key(self, isolated_manager):
        """非敏感 key + value → 原值返回"""
        manager, _ = isolated_manager
        result = manager._mask_sensitive_value('DEBUG_MODE', 'true')
        assert result == 'true'

    def test_none_value(self, isolated_manager):
        """None 值 → None（用于 delete 操作的 new_value）"""
        manager, _ = isolated_manager
        result = manager._mask_sensitive_value('LLM_API_KEY', None)
        assert result is None

    @pytest.mark.parametrize("key", [
        'LLM_API_KEY', 'SEARCH_TOKEN', 'WEBHOOK_URL',
        'DB_PASSWORD', 'API_CREDENTIAL', 'CLIENT_SECRET',
    ])
    def test_various_sensitive_patterns(self, isolated_manager, key):
        """各类敏感 key 模式（API_KEY/TOKEN/WEBHOOK/PASSWORD/CREDENTIAL/SECRET）"""
        manager, _ = isolated_manager
        value = 'abcdefghijklmnopqrstuvwxyz'  # 26 字符
        result = manager._mask_sensitive_value(key, value)
        assert result == 'abcd***wxyz'
        assert result != value  # 必须脱敏

    def test_non_sensitive_long_value_not_masked(self, isolated_manager):
        """非敏感 key + 长 value → 原值返回（不脱敏）"""
        manager, _ = isolated_manager
        value = 'abcdefghijklmnopqrstuvwxyz'
        result = manager._mask_sensitive_value('MODEL_NAME', value)
        assert result == value


# ════════════════════════════════════════════════════════════════════════════
# 2. set/delete 操作的审计日志写入
# ════════════════════════════════════════════════════════════════════════════

class TestAuditLogWrite:
    """审计日志写入测试"""

    def test_set_writes_audit_log(self, isolated_manager):
        """set 操作后审计日志包含 set 记录"""
        manager, audit_log = isolated_manager
        manager.set('DEBUG_MODE', 'true')

        assert audit_log.exists()
        lines = audit_log.read_text(encoding='utf-8').strip().split('\n')
        assert len(lines) >= 1

        entry = json.loads(lines[-1])
        assert entry['action'] == 'set'
        assert entry['key'] == 'DEBUG_MODE'
        assert entry['new_value'] == 'true'

    def test_delete_writes_audit_log(self, isolated_manager):
        """delete 操作后审计日志包含 delete 记录"""
        manager, audit_log = isolated_manager
        # 先 set 再 delete，确保有 old_value
        manager.set('DEBUG_MODE', 'true')
        manager.delete('DEBUG_MODE')

        lines = audit_log.read_text(encoding='utf-8').strip().split('\n')
        # 最后一条应该是 delete
        entry = json.loads(lines[-1])
        assert entry['action'] == 'delete'
        assert entry['key'] == 'DEBUG_MODE'
        assert entry['old_value'] == 'true'
        assert entry['new_value'] is None

    def test_set_sensitive_key_masked_in_audit(self, isolated_manager):
        """敏感 key 的 value 在审计日志中已脱敏"""
        manager, audit_log = isolated_manager
        manager.set('LLM_API_KEY', 'sk-1234567890abcdef')

        lines = audit_log.read_text(encoding='utf-8').strip().split('\n')
        entry = json.loads(lines[-1])
        assert entry['key'] == 'LLM_API_KEY'
        assert entry['new_value'] == 'sk-1***cdef'  # 已脱敏
        assert entry['new_value'] != 'sk-1234567890abcdef'  # 不是明文

    def test_set_non_sensitive_key_not_masked_in_audit(self, isolated_manager):
        """非敏感 key 的 value 在审计日志中原值记录"""
        manager, audit_log = isolated_manager
        manager.set('MODEL_NAME', 'gpt-4')

        lines = audit_log.read_text(encoding='utf-8').strip().split('\n')
        entry = json.loads(lines[-1])
        assert entry['key'] == 'MODEL_NAME'
        assert entry['new_value'] == 'gpt-4'  # 原值

    def test_delete_sensitive_key_old_value_masked(self, isolated_manager):
        """敏感 key 的 delete 操作，old_value 已脱敏"""
        manager, audit_log = isolated_manager
        # 先设置敏感值
        os.environ['LLM_API_KEY'] = 'sk-1234567890abcdef'
        manager.delete('LLM_API_KEY')

        lines = audit_log.read_text(encoding='utf-8').strip().split('\n')
        entry = json.loads(lines[-1])
        assert entry['action'] == 'delete'
        assert entry['key'] == 'LLM_API_KEY'
        assert entry['old_value'] == 'sk-1***cdef'  # 已脱敏

    def test_set_old_value_captured(self, isolated_manager):
        """set 操作记录修改前的 old_value"""
        manager, audit_log = isolated_manager
        # 第一次 set（old_value 为 None）
        manager.set('DEBUG_MODE', 'true')
        # 第二次 set（old_value 应为 'true'）
        manager.set('DEBUG_MODE', 'false')

        lines = audit_log.read_text(encoding='utf-8').strip().split('\n')
        # 最后一条记录的 old_value 应为 'true'
        entry = json.loads(lines[-1])
        assert entry['action'] == 'set'
        assert entry['key'] == 'DEBUG_MODE'
        assert entry['old_value'] == 'true'
        assert entry['new_value'] == 'false'


# ════════════════════════════════════════════════════════════════════════════
# 3. 审计日志格式与字段完整性
# ════════════════════════════════════════════════════════════════════════════

class TestAuditLogFormat:
    """审计日志 JSONL 格式与字段测试"""

    def test_jsonl_format(self, isolated_manager):
        """审计日志是合法 JSONL 格式（每行一个 JSON 对象）"""
        manager, audit_log = isolated_manager
        manager.set('DEBUG_MODE', 'true')
        manager.set('MODEL_NAME', 'gpt-4')

        content = audit_log.read_text(encoding='utf-8')
        lines = content.strip().split('\n')
        # 每行都应是合法 JSON
        for line in lines:
            entry = json.loads(line)
            assert isinstance(entry, dict)

    def test_required_fields(self, isolated_manager):
        """审计日志包含所有必需字段"""
        manager, audit_log = isolated_manager
        manager.set('DEBUG_MODE', 'true')

        entry = json.loads(audit_log.read_text(encoding='utf-8').strip().split('\n')[-1])
        required_fields = {
            'timestamp', 'action', 'key', 'old_value', 'new_value',
            'user', 'pid', 'trace_id'
        }
        assert required_fields.issubset(entry.keys())

    def test_timestamp_is_iso_format(self, isolated_manager):
        """timestamp 是 ISO 8601 格式"""
        manager, audit_log = isolated_manager
        manager.set('DEBUG_MODE', 'true')

        entry = json.loads(audit_log.read_text(encoding='utf-8').strip().split('\n')[-1])
        timestamp = entry['timestamp']
        # ISO 8601 格式应能被 fromisoformat 解析
        from datetime import datetime
        datetime.fromisoformat(timestamp)

    def test_pid_is_int(self, isolated_manager):
        """pid 是整数"""
        manager, audit_log = isolated_manager
        manager.set('DEBUG_MODE', 'true')

        entry = json.loads(audit_log.read_text(encoding='utf-8').strip().split('\n')[-1])
        assert isinstance(entry['pid'], int)

    def test_user_is_string(self, isolated_manager):
        """user 是字符串"""
        manager, audit_log = isolated_manager
        manager.set('DEBUG_MODE', 'true')

        entry = json.loads(audit_log.read_text(encoding='utf-8').strip().split('\n')[-1])
        assert isinstance(entry['user'], str)
        assert len(entry['user']) > 0

    def test_trace_id_from_env(self, isolated_manager, monkeypatch):
        """trace_id 从环境变量 TRACE_ID 读取"""
        monkeypatch.setenv('TRACE_ID', 'test-trace-123')
        manager, audit_log = isolated_manager
        manager.set('DEBUG_MODE', 'true')

        entry = json.loads(audit_log.read_text(encoding='utf-8').strip().split('\n')[-1])
        assert entry['trace_id'] == 'test-trace-123'

    def test_trace_id_none_when_not_set(self, isolated_manager, monkeypatch):
        """TRACE_ID 未设置时为 None"""
        monkeypatch.delenv('TRACE_ID', raising=False)
        manager, audit_log = isolated_manager
        manager.set('DEBUG_MODE', 'true')

        entry = json.loads(audit_log.read_text(encoding='utf-8').strip().split('\n')[-1])
        assert entry['trace_id'] is None


# ════════════════════════════════════════════════════════════════════════════
# 4. 失败降级测试
# ════════════════════════════════════════════════════════════════════════════

class TestAuditLogFailure:
    """审计日志失败降级测试"""

    def test_audit_log_failure_does_not_block_set(self, isolated_manager, monkeypatch):
        """审计日志写入失败时，set 操作仍应成功"""
        manager, audit_log = isolated_manager

        # 模拟审计日志路径抛异常（如权限不足）
        def raise_permission_error():
            raise PermissionError("模拟权限不足")
        monkeypatch.setattr(manager, '_get_audit_log_path', raise_permission_error)

        # set 操作不应抛异常
        manager.set('DEBUG_MODE', 'true')
        # os.environ 应已更新（主流程未阻塞）
        assert os.environ.get('DEBUG_MODE') == 'true'

    def test_audit_log_failure_does_not_block_delete(self, isolated_manager, monkeypatch):
        """审计日志写入失败时，delete 操作仍应成功"""
        manager, audit_log = isolated_manager
        os.environ['DEBUG_MODE'] = 'true'

        def raise_permission_error():
            raise PermissionError("模拟权限不足")
        monkeypatch.setattr(manager, '_get_audit_log_path', raise_permission_error)

        # delete 操作不应抛异常
        manager.delete('DEBUG_MODE')
        # os.environ 应已删除（主流程未阻塞）
        assert 'DEBUG_MODE' not in os.environ
