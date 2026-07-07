"""输出后置验证门控 — 单元测试

覆盖:
    - _validate_output 三种状态 (passed/failed/skipped)
    - _load_output_schema 从元数据加载
    - ExecutionResult.to_dict() 包含新字段
    - jsonschema 缺失时降级
"""
import json
import sys
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from agent.skills_mgmt.executor import SkillExecutor, ExecutionResult
from agent.skills_mgmt.exceptions import ErrorCode


class TestValidateOutput:
    """_validate_output 三态测试"""

    def setup_method(self):
        self.exe = SkillExecutor()
        self.schema = {
            "type": "object",
            "required": ["page_count"],
            "properties": {
                "page_count": {"type": "integer", "minimum": 0},
                "file_path": {"type": "string"},
            },
        }

    def test_passed(self):
        """输出符合 schema → passed"""
        status, errors = self.exe._validate_output(
            {"page_count": 5, "file_path": "/tmp/a.pdf"},
            self.schema, "s1",
        )
        assert status == "passed"
        assert errors == []

    def test_failed_type_mismatch(self):
        """类型不匹配 → failed"""
        status, errors = self.exe._validate_output(
            {"page_count": "5", "file_path": "/tmp/a.pdf"},
            self.schema, "s1",
        )
        assert status == "failed"
        assert len(errors) == 1
        assert errors[0]["field"] == "page_count"
        assert "integer" in errors[0]["message"]

    def test_failed_missing_required(self):
        """缺少必填字段 → failed"""
        status, errors = self.exe._validate_output(
            {"file_path": "/tmp/a.pdf"},
            self.schema, "s1",
        )
        assert status == "failed"
        assert len(errors) == 1

    def test_failed_constraint_violation(self):
        """约束违反(minimum) → failed"""
        status, errors = self.exe._validate_output(
            {"page_count": -1, "file_path": "/tmp/a.pdf"},
            self.schema, "s1",
        )
        assert status == "failed"
        assert len(errors) == 1

    def test_skipped_no_schema(self):
        """无 schema → skipped"""
        status, errors = self.exe._validate_output(
            {"page_count": 5}, {}, "s1",
        )
        assert status == "skipped"
        assert errors == []

    def test_skipped_empty_schema(self):
        """空 schema → skipped"""
        status, errors = self.exe._validate_output(
            {"anything": True}, {}, "s1",
        )
        assert status == "skipped"

    def test_skipped_jsonschema_missing(self):
        """jsonschema 未安装 → skipped"""
        with patch("builtins.__import__", side_effect=ImportError):
            status, errors = self.exe._validate_output(
                {"page_count": 5}, self.schema, "s1",
            )
        assert status == "skipped"

    def test_skipped_invalid_schema(self):
        """schema 本身非法 → skipped (不阻塞)"""
        bad_schema = {"type": "not_a_real_type"}
        status, errors = self.exe._validate_output(
            {"x": 1}, bad_schema, "s1",
        )
        assert status == "skipped"
        assert len(errors) == 1
        assert errors[0]["field"] == "(schema)"


class TestLoadOutputSchema:
    """_load_output_schema 从元数据加载"""

    def test_load_valid_schema(self):
        """正常加载 output_schema"""
        exe = SkillExecutor()
        mock_fs = MagicMock()
        mock_fs.get_metadata.return_value = {
            "output_schema": {"type": "object", "required": ["x"]},
        }
        exe.fs = mock_fs

        schema = exe._load_output_schema("test-skill")
        assert schema == {"type": "object", "required": ["x"]}

    def test_load_missing_schema(self):
        """元数据无 output_schema → 空 dict"""
        exe = SkillExecutor()
        mock_fs = MagicMock()
        mock_fs.get_metadata.return_value = {"id": "test", "name": "Test"}
        exe.fs = mock_fs

        schema = exe._load_output_schema("test-skill")
        assert schema == {}

    def test_load_non_dict_schema(self):
        """output_schema 非对象 → 空 dict + 警告"""
        exe = SkillExecutor()
        mock_fs = MagicMock()
        mock_fs.get_metadata.return_value = {"output_schema": "not-a-dict"}
        exe.fs = mock_fs

        schema = exe._load_output_schema("test-skill")
        assert schema == {}

    def test_load_metadata_error(self):
        """get_metadata 抛异常 → 空 dict + 警告"""
        exe = SkillExecutor()
        mock_fs = MagicMock()
        mock_fs.get_metadata.side_effect = RuntimeError("disk error")
        exe.fs = mock_fs

        schema = exe._load_output_schema("test-skill")
        assert schema == {}


class TestExecutionResult:
    """ExecutionResult 新字段测试"""

    def test_to_dict_includes_validation_status(self):
        """to_dict 包含 validation_status 字段"""
        r = ExecutionResult(
            skill_id="s1", script_name="main.py",
            success=True, exit_code=0,
            stdout="", stderr="", duration_ms=10.0,
            validation_status="passed",
        )
        d = r.to_dict()
        assert d["validation_status"] == "passed"
        assert "validation_errors" not in d  # 空列表不输出

    def test_to_dict_includes_validation_errors(self):
        """to_dict 包含 validation_errors 字段(非空时)"""
        errors = [{"field": "count", "message": "type mismatch"}]
        r = ExecutionResult(
            skill_id="s1", script_name="main.py",
            success=False, exit_code=0,
            stdout="", stderr="", duration_ms=10.0,
            error="输出 schema 校验失败(1 处)",
            validation_status="failed",
            validation_errors=errors,
        )
        d = r.to_dict()
        assert d["validation_status"] == "failed"
        assert d["validation_errors"] == errors

    def test_to_dict_default_validation_status(self):
        """默认 validation_status 为 skipped"""
        r = ExecutionResult(
            skill_id="s1", script_name="main.py",
            success=True, exit_code=0,
            stdout="ok", stderr="", duration_ms=5.0,
        )
        d = r.to_dict()
        assert d["validation_status"] == "skipped"

    def test_to_dict_field_order_preserved(self):
        """原有字段顺序不被破坏(validation_status 在末尾)"""
        r = ExecutionResult(
            skill_id="s1", script_name="main.py",
            success=True, exit_code=0,
            stdout="", stderr="", duration_ms=5.0,
            result={"data": 1},
        )
        d = r.to_dict()
        keys = list(d.keys())
        # validation_status 必须在 result 之后
        assert keys.index("result") < keys.index("validation_status")
        # layer 仍在原位
        assert keys.index("layer") < keys.index("result")
