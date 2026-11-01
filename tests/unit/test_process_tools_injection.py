# -*- coding: utf-8 -*-
"""process_tools.start_process 的命令行注入回归测试。

背景：CodeQL `py/command-line-injection`（code scanning 告警 **#598**，critical）
—— 旧实现把用户传入的 `program` 直接放进 `subprocess.Popen([program, *args])`，
等于让外部字符串决定「执行哪个可执行文件」（白名单只做字符串比较，CodeQL 不认
它是 sanitizer，见 §8 陷阱 14）。

修复口径（本文件锁死）：
    1. 用户串只用于**比较**，进 argv[0] 的必须是**白名单元素本身**（规范化名字）；
    2. 参数只做形状校验（字符串列表、长度上界、无 NUL），不改写内容；
    3. 非白名单一律拒绝，错误文案仍含「白名单」（兼容既有 p0 用例）。
"""
from unittest.mock import MagicMock, patch

import pytest

from agent.tools.process_tools import (
    _resolve_whitelisted_program,
    _sanitize_args,
    start_process,
)


class TestResolveWhitelistedProgram:
    """程序名 → 白名单元素（用户串不进命令）"""

    @pytest.mark.unit
    def test_exact_name_returns_whitelist_entry(self):
        assert _resolve_whitelisted_program("notepad.exe") == "notepad.exe"

    @pytest.mark.unit
    def test_case_and_space_insensitive(self):
        assert _resolve_whitelisted_program("  NotePad.EXE  ") == "notepad.exe"

    @pytest.mark.unit
    def test_absolute_path_matches_by_tail(self):
        """带路径的写法仍被接受，但返回的是白名单元素而非用户串"""
        assert _resolve_whitelisted_program("C:\\Tools\\notepad.exe") == "notepad.exe"
        assert _resolve_whitelisted_program("/usr/bin/python3.exe") == "python3.exe"

    @pytest.mark.unit
    def test_unknown_or_tampered_returns_none(self):
        assert _resolve_whitelisted_program("evil.exe") is None
        # 尾段不等值（想靠参数混进白名单）一律拒绝
        assert _resolve_whitelisted_program("notepad.exe --help") is None
        assert _resolve_whitelisted_program("") is None
        assert _resolve_whitelisted_program(None) is None


class TestSanitizeArgs:
    """参数形状校验"""

    @pytest.mark.unit
    def test_none_is_empty_list(self):
        assert _sanitize_args(None) == []

    @pytest.mark.unit
    def test_valid_list_passes_through_unchanged(self):
        args = ["test.txt", "--flag", "值 with space"]
        assert _sanitize_args(args) == args

    @pytest.mark.unit
    def test_rejects_non_list(self):
        assert _sanitize_args("--version") is None
        assert _sanitize_args({"a": 1}) is None

    @pytest.mark.unit
    def test_rejects_bad_elements(self):
        assert _sanitize_args(["ok", 1]) is None
        assert _sanitize_args(["ok", None]) is None
        assert _sanitize_args(["a\x00b"]) is None

    @pytest.mark.unit
    def test_rejects_too_many_or_too_long(self):
        assert _sanitize_args(["a"] * 65) is None
        assert _sanitize_args(["x" * 4097]) is None


class TestStartProcessUsesCanonicalExecutable:
    """argv[0] 必须是白名单元素（CodeQL #598 的回归点）"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_absolute_user_path_is_replaced_by_whitelist_entry(self):
        """用户写 \\evil\\notepad.exe：进 Popen 的只有 notepad.exe"""
        mock_proc = MagicMock()
        mock_proc.pid = 4321
        with patch("subprocess.Popen", return_value=mock_proc) as mock_popen:
            result = start_process("C:\\evil\\notepad.exe", args=["a.txt"])
        assert result["ok"] is True
        assert mock_popen.call_args[0][0] == ["notepad.exe", "a.txt"]

    @pytest.mark.unit
    @pytest.mark.p0
    def test_non_whitelisted_rejected_before_popen(self):
        with patch("subprocess.Popen") as mock_popen:
            result = start_process("curl.exe.evil")
        assert result["ok"] is False
        assert "白名单" in result["error"]
        mock_popen.assert_not_called()

    @pytest.mark.unit
    @pytest.mark.p0
    def test_bad_args_rejected_before_popen(self):
        with patch("subprocess.Popen") as mock_popen:
            result = start_process("notepad.exe", args="--version")
        assert result["ok"] is False
        assert "args" in result["error"]
        mock_popen.assert_not_called()
