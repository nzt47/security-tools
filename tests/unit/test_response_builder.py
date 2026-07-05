"""ResponseBuilder 单元测试 — 统一响应构建"""
from agent.orchestrator.response_builder import ResponseBuilder, Response


class TestResponseBuilder:
    """ResponseBuilder 静态工厂方法测试"""

    def test_success_response(self):
        r = ResponseBuilder.success(data={"key": "val"}, msg="操作成功")
        assert r.success is True
        assert r.data == {"key": "val"}
        assert r.msg == "操作成功"
        assert r.error is None

    def test_success_default_msg(self):
        r = ResponseBuilder.success(data="ok")
        assert r.msg == "ok"

    def test_success_no_data(self):
        r = ResponseBuilder.success()
        assert r.success is True
        assert r.data is None

    def test_error_response(self):
        r = ResponseBuilder.error(error="发生错误", msg="处理失败")
        assert r.success is False
        assert r.error == "发生错误"
        assert r.msg == "处理失败"

    def test_error_default_msg(self):
        r = ResponseBuilder.error(error="test")
        assert r.msg == "error"

    def test_rejection_response(self):
        r = ResponseBuilder.rejection(reason="权限不足", mode="strict")
        assert r.success is False
        assert r.error == "权限不足"
        assert r.msg == "rejected"
        assert r.metadata["mode"] == "strict"

    def test_guard_blocked_response(self):
        r = ResponseBuilder.guard_blocked(reason="敏感内容", pattern="sql_injection")
        assert r.success is False
        assert "安全护栏" in r.error
        assert r.msg == "blocked_by_guard"
        assert r.metadata["matched_pattern"] == "sql_injection"

    def test_workflow_result_response(self):
        r = ResponseBuilder.workflow_result(output="done", intent="search", confidence=0.9)
        assert r.success is True
        assert r.data["output"] == "done"
        assert r.data["intent"] == "search"
        assert r.data["confidence"] == 0.9
        assert r.msg == "handled_by_workflow"

    def test_llm_result_response(self):
        r = ResponseBuilder.llm_result(text="你好", model="claude-4")
        assert r.success is True
        assert r.data["text"] == "你好"
        assert r.data["model"] == "claude-4"
        assert r.msg == "llm_response"

    def test_offline_response(self):
        r = ResponseBuilder.offline(reason="网络不可用")
        assert r.success is True
        assert "离线模式" in r.data["text"]
        assert r.msg == "offline"

    def test_offline_default_reason(self):
        r = ResponseBuilder.offline()
        assert "离线模式" in r.data["text"]

    def test_from_exception_response(self):
        try:
            raise ValueError("测试异常")
        except ValueError as e:
            r = ResponseBuilder.from_exception(e, msg="处理异常")
            assert r.success is False
            assert r.error == "测试异常"
            assert r.msg == "处理异常"

    def test_from_exception_default_msg(self):
        try:
            raise RuntimeError("运行时错误")
        except RuntimeError as e:
            r = ResponseBuilder.from_exception(e)
            assert r.msg == "internal_error"


class TestResponseDataclass:
    """Response 数据类功能测试"""

    def test_to_dict_without_error(self):
        r = Response(success=True, data=[1, 2, 3], msg="ok")
        d = r.to_dict()
        assert d["success"] is True
        assert d["data"] == [1, 2, 3]
        assert d["msg"] == "ok"
        assert "error" not in d

    def test_to_dict_with_error(self):
        r = Response(success=False, error="出错了", msg="fail")
        d = r.to_dict()
        assert d["success"] is False
        assert d["error"] == "出错了"

    def test_to_dict_with_metadata(self):
        r = Response(success=True, msg="ok", metadata={"version": "2.0"})
        d = r.to_dict()
        assert d["metadata"] == {"version": "2.0"}

    def test_to_dict_immutable(self):
        r = Response(success=True, data={"key": "val"})
        d = r.to_dict()
        assert d["data"]["key"] == "val"
