"""TaskScheduler system_command 安全闸门测试

背景：``TaskScheduler.run_task`` 的 system_command 分支此前直接
``subprocess.Popen(command, shell=True)``，**没有任何权限校验** —— 定时任务可绕过
「危险命令会被安全系统阻止」的承诺，而且是**无人值守**执行（daemon 线程）。

修复后执行前必须经过 ``TaskScheduler._guard_scheduled_command``，判定口径与交互式
工具 ``agent/tools/system_tools.py`` 的 ``shell_execute`` 完全一致：
critical 直接拒绝 / warning 再走 check_action / 其余放行 / 异常 fail-closed。

本文件只验证闸门行为，**所有用例都 mock 掉
``agent.task_scheduler.subprocess.Popen``**，绝不真的执行命令。
"""

import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import agent.task_scheduler as ts_module
from agent.permission_system import PermissionResult, PermissionSystem
from agent.task_scheduler import TaskScheduler

pytestmark = pytest.mark.unit


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def scheduler():
    """新建 TaskScheduler 实例（_yunshu_ref 默认 None）"""
    return TaskScheduler()


@pytest.fixture
def patch_paths(tmp_path, monkeypatch):
    """模块级文件路径重定向到 tmp_path，避免污染 data/"""
    paths = {
        "history": tmp_path / "task_history.jsonl",
        "heartbeat": tmp_path / "heartbeat_history.json",
        "tasks": tmp_path / "scheduled_tasks.json",
    }
    monkeypatch.setattr(ts_module, "TASK_HISTORY_FILE", paths["history"])
    monkeypatch.setattr(ts_module, "HEARTBEAT_HISTORY_FILE", paths["heartbeat"])
    monkeypatch.setattr(ts_module, "SCHEDULED_TASKS_FILE", paths["tasks"])
    return paths


@pytest.fixture
def patch_scheduler_config():
    """固定 scheduler 相关配置读取（避免真实读盘 / 受本机配置影响）"""
    config = {"command_timeout": 30, "max_history_lines": 1000}
    patches = [
        patch("agent.monitoring.observability_config.get_scheduler_command_timeout",
              side_effect=lambda: config["command_timeout"]),
        patch("agent.monitoring.observability_config.get_scheduler_max_history_lines",
              side_effect=lambda: config["max_history_lines"]),
    ]
    for p in patches:
        p.start()
    yield config
    for p in patches:
        p.stop()


@pytest.fixture(autouse=True)
def reset_fallback_permission_system(monkeypatch):
    """隔离模块级回退权限系统缓存（避免用例间互相污染）"""
    monkeypatch.setattr(ts_module, "_fallback_permission_system", None)
    yield
    monkeypatch.setattr(ts_module, "_fallback_permission_system", None)


# ============================================================================
# 替身工具
# ============================================================================

def _permission(check_text_result, action_result=None):
    """权限系统替身（只实现闸门用到的 check_text / check_action）"""
    perm = MagicMock()
    perm.check_text.return_value = check_text_result
    if action_result is not None:
        perm.check_action.return_value = action_result
    return perm


def _yunshu_with(perm):
    """注入了 _permission 的 DigitalLife 替身"""
    dl = MagicMock()
    dl._permission = perm
    return dl


def _wire_popen(mock_popen, returncode=0, out=("ok\n", "")):
    """让 mock 的 Popen 返回一个可用进程对象"""
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate.return_value = out
    mock_popen.return_value = proc
    return proc


def _last_history_record(paths):
    """读回调度器写入的最后一条执行历史（证明函数尾部落盘逻辑照常执行）"""
    lines = paths["history"].read_text(encoding="utf-8").strip().splitlines()
    return json.loads(lines[-1])


# ============================================================================
# 闸门拦截（critical / warning）
# ============================================================================

class TestGuardBlocksDangerousCommands:
    """危险定时命令必须被拦在 subprocess.Popen 之前"""

    @patch("agent.task_scheduler.subprocess.Popen")
    def test_critical_command_blocked(self, mock_popen, scheduler, patch_paths,
                                      patch_scheduler_config):
        """check_text 返回 critical → 拒绝，且 Popen 未被调用"""
        perm = _permission({
            "safe": False,
            "level": "critical",
            "matches": [{"description": "递归删除根目录", "category": "文件系统"}],
        })
        scheduler._yunshu_ref = _yunshu_with(perm)
        scheduler.add_command_task("危险任务", "rm -rf /", interval_sec=60)

        result = scheduler.run_task(scheduler.tasks[0])

        assert result["status"] == "failed"
        assert "危险命令被安全系统阻止" in result["error"]
        assert "递归删除根目录" in result["error"]
        mock_popen.assert_not_called()
        perm.check_text.assert_called_once_with("rm -rf /")
        perm.check_action.assert_not_called()
        # 被拒任务照样留痕：函数尾部逻辑未被跳过（本修复刻意不 return）
        assert result["end_time"]
        assert _last_history_record(patch_paths)["status"] == "failed"

    @patch("agent.task_scheduler.subprocess.Popen")
    def test_warning_command_denied_by_check_action(self, mock_popen, scheduler,
                                                    patch_paths, patch_scheduler_config):
        """check_text 返回 warning 且 check_action 拒绝 → 拒绝，Popen 未被调用"""
        perm = _permission(
            {
                "safe": False,
                "level": "warning",
                "matches": [{"description": "递归删除操作", "category": "文件系统"}],
            },
            action_result=PermissionResult(allowed=False, reason="需要人工确认"),
        )
        scheduler._yunshu_ref = _yunshu_with(perm)
        scheduler.add_command_task("警告任务", "rm -r ./tmp", interval_sec=60)

        result = scheduler.run_task(scheduler.tasks[0])

        assert result["status"] == "failed"
        assert "权限系统拒绝" in result["error"]
        assert "需要人工确认" in result["error"]
        mock_popen.assert_not_called()
        # check_action 的 action 键沿用 shell_execute 的 "工具名:warning:描述" 口径
        action_key = perm.check_action.call_args[0][0]
        assert action_key.startswith("system_command:warning:")
        assert "递归删除操作" in action_key


# ============================================================================
# 闸门放行
# ============================================================================

class TestGuardAllowsSafeCommands:
    """同一闸门不得误伤良性命令（既有 system_command 行为零回归）"""

    @patch("agent.task_scheduler.subprocess.Popen")
    def test_warning_command_allowed_by_check_action(self, mock_popen, scheduler,
                                                     patch_paths, patch_scheduler_config):
        """warning 但 check_action 放行 → 正常执行一次"""
        _wire_popen(mock_popen, returncode=0, out=("warn-ok\n", ""))
        perm = _permission(
            {
                "safe": False,
                "level": "warning",
                "matches": [{"description": "递归删除操作"}],
            },
            action_result=PermissionResult(allowed=True),
        )
        scheduler._yunshu_ref = _yunshu_with(perm)
        scheduler.add_command_task("放行任务", "rm -r ./ok", interval_sec=60)

        result = scheduler.run_task(scheduler.tasks[0])

        assert result["status"] == "success"
        assert result["output"] == "warn-ok"
        mock_popen.assert_called_once()
        perm.check_action.assert_called_once()

    @patch("agent.task_scheduler.subprocess.Popen")
    def test_benign_command_executes_with_real_permission_fallback(
            self, mock_popen, scheduler, patch_paths, patch_scheduler_config):
        """_yunshu_ref 未注入时走真实 PermissionSystem 回退 → 良性命令照常执行"""
        _wire_popen(mock_popen, returncode=0, out=("hello\n", ""))
        assert scheduler._yunshu_ref is None  # 未注入（测试/脚本直连场景）
        scheduler.add_command_task("良性任务", "echo hello", interval_sec=60)

        result = scheduler.run_task(scheduler.tasks[0])

        assert result["status"] == "success"
        assert result["output"] == "hello"
        mock_popen.assert_called_once()
        # 回退实例是真权限系统（有真实策略/正则），不是"跳过检查"
        assert isinstance(ts_module._fallback_permission_system, PermissionSystem)

    def test_guard_passes_benign_command(self, scheduler):
        """直接调用守卫：良性命令返回空串（放行）"""
        assert scheduler._guard_scheduled_command("echo hello") == ""


# ============================================================================
# fail-closed
# ============================================================================

class TestGuardFailsClosed:
    """安全检查不可用时必须拒绝执行，绝不静默放行"""

    @patch("agent.task_scheduler.subprocess.Popen")
    def test_check_text_exception_is_fail_closed(self, mock_popen, scheduler,
                                                 patch_paths, patch_scheduler_config):
        """权限系统抛异常 → fail-closed 拒绝，Popen 未被调用"""
        perm = _permission(None)
        perm.check_text.side_effect = RuntimeError("permission backend down")
        scheduler._yunshu_ref = _yunshu_with(perm)
        scheduler.add_command_task("异常任务", "echo hi", interval_sec=60)

        result = scheduler.run_task(scheduler.tasks[0])

        assert result["status"] == "failed"
        assert result["error"] == "安全检查系统故障，拒绝执行定时命令任务"
        mock_popen.assert_not_called()

    @patch("agent.task_scheduler.subprocess.Popen")
    def test_permission_system_construction_failure_is_fail_closed(
            self, mock_popen, scheduler, patch_paths, patch_scheduler_config):
        """连回退 PermissionSystem() 都构造失败 → fail-closed 拒绝"""
        with patch("agent.permission_system.PermissionSystem",
                   side_effect=RuntimeError("cannot init permission system")):
            assert scheduler._yunshu_ref is None
            scheduler.add_command_task("异常任务", "echo hi", interval_sec=60)
            result = scheduler.run_task(scheduler.tasks[0])

        assert result["status"] == "failed"
        assert result["error"] == "安全检查系统故障，拒绝执行定时命令任务"
        mock_popen.assert_not_called()


# ============================================================================
# 回退路径：未注入 _yunshu_ref 也必须有防护
# ============================================================================

class TestFallbackPermissionPath:
    """未注入 _yunshu_ref 的路径历史上就是无防护通道，必须仍做真实检查"""

    @patch("agent.task_scheduler.subprocess.Popen")
    def test_yunshu_ref_none_still_checks_critical(self, mock_popen, scheduler,
                                                   patch_paths, patch_scheduler_config):
        """_yunshu_ref is None + 真实回退权限系统 → 真实 critical 命令仍被拦"""
        assert scheduler._yunshu_ref is None
        scheduler.add_command_task("危险任务", "rm -rf /", interval_sec=60)

        result = scheduler.run_task(scheduler.tasks[0])

        assert result["status"] == "failed"
        assert "危险命令被安全系统阻止" in result["error"]
        mock_popen.assert_not_called()

    @patch("agent.task_scheduler.subprocess.Popen")
    def test_yunshu_ref_without_permission_attr_falls_back(
            self, mock_popen, scheduler, patch_paths, patch_scheduler_config):
        """注入了引用但对象上没有 _permission → 同样回退，而不是跳过检查"""
        scheduler._yunshu_ref = SimpleNamespace()  # 无 _permission 属性
        scheduler.add_command_task("危险任务", "rm -rf /", interval_sec=60)

        result = scheduler.run_task(scheduler.tasks[0])

        assert result["status"] == "failed"
        assert "危险命令被安全系统阻止" in result["error"]
        mock_popen.assert_not_called()


# ============================================================================
# 空命令
# ============================================================================

class TestEmptyCommandRejected:

    @pytest.mark.parametrize("command", ["", "   ", "\t"])
    @patch("agent.task_scheduler.subprocess.Popen")
    def test_empty_command_rejected(self, mock_popen, command, scheduler,
                                    patch_paths, patch_scheduler_config):
        """空/纯空白命令被拒（顺带守住"无命令即无执行"）"""
        scheduler.add_command_task("空任务", command, interval_sec=60)

        result = scheduler.run_task(scheduler.tasks[0])

        assert result["status"] == "failed"
        assert result["error"] == "命令为空"
        mock_popen.assert_not_called()


# ============================================================================
# 回归护栏：其他任务类型不受影响
# ============================================================================

class TestOtherTaskTypesUnaffected:
    """本次修复只加闸门，不得改变 python_func / heartbeat 分支行为"""

    def test_python_func_unaffected(self, scheduler, patch_paths, patch_scheduler_config):
        func = MagicMock()
        scheduler.add_interval_task("py任务", func, interval_seconds=60)

        result = scheduler.run_task(scheduler.tasks[0])

        assert result["status"] == "success"
        func.assert_called_once()

    def test_python_func_exception_unaffected(self, scheduler, patch_paths,
                                              patch_scheduler_config):
        def _boom():
            raise ValueError("boom")

        scheduler.add_interval_task("py异常", _boom, interval_seconds=60)

        result = scheduler.run_task(scheduler.tasks[0])

        assert result["status"] == "failed"
        assert "boom" in result["error"]

    def test_heartbeat_unaffected(self, scheduler, patch_paths, patch_scheduler_config):
        hb_result = {
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "checks": {},
        }
        scheduler._heartbeat_func = MagicMock(return_value=hb_result)
        task = {"type": "heartbeat", "name": "hb", "task_id": "hb_1",
                "last_run": None, "enabled": True}

        result = scheduler.run_task(task)

        assert result["status"] == "healthy"
        assert json.loads(result["output"])["status"] == "healthy"
        scheduler._heartbeat_func.assert_called_once()
        assert patch_paths["heartbeat"].exists()
