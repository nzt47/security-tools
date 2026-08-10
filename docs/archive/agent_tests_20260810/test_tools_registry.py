"""MCP 工具注册表单元测试"""
import pytest
from agent.tools import register, unregister, call, list_tools, get_tool_defs, get_tool_schema, clear, ToolError


def setup_function():
    """每个测试前清理注册表"""
    clear()


def test_register_decorator():
    """使用装饰器注册工具"""
    @register("test_tool", "测试工具")
    def test_tool_func(name):
        return f"Hello {name}"
    
    tools = list_tools()
    assert len(tools) == 1
    assert tools[0]["name"] == "test_tool"
    assert tools[0]["description"] == "测试工具"


def test_register_with_handler():
    """使用 handler 参数注册工具"""
    def my_tool(param1, param2):
        return param1 + param2
    
    register("my_tool", "我的工具", handler=my_tool)
    
    tools = list_tools()
    assert len(tools) == 1
    assert tools[0]["name"] == "my_tool"


def test_register_with_schema():
    """注册工具时提供 JSON Schema"""
    schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "value": {"type": "number"}
        }
    }
    
    @register("schema_tool", "带schema的工具", schema=schema)
    def schema_tool_func(**kwargs):
        return kwargs
    
    tool_schema = get_tool_schema("schema_tool")
    assert tool_schema is not None
    assert tool_schema["properties"]["name"]["type"] == "string"


def test_register_override():
    """重复注册应覆盖现有工具"""
    @register("same_tool", "版本1")
    def version1():
        return "v1"
    
    @register("same_tool", "版本2")
    def version2():
        return "v2"
    
    result = call("same_tool")
    assert result == "v2"


def test_call_tool():
    """调用已注册的工具"""
    @register("greet", "打招呼")
    def greet(name="World"):
        return f"Hello {name}!"
    
    result = call("greet", name="Alice")
    assert result == "Hello Alice!"


def test_call_tool_without_params():
    """调用工具时不传参数"""
    @register("hello", "说hello")
    def hello():
        return "Hello!"
    
    result = call("hello")
    assert result == "Hello!"


def test_call_tool_with_name_in_params():
    """通过 params 传递工具名称"""
    @register("param_tool", "参数工具")
    def param_tool(value):
        return value * 2
    
    result = call(name="param_tool", value=5)
    assert result == 10


def test_call_unknown_tool():
    """调用不存在的工具应抛出异常"""
    with pytest.raises(ToolError) as excinfo:
        call("unknown_tool")
    
    assert "未知工具" in str(excinfo.value)


def test_call_tool_missing_name():
    """缺少工具名称应抛出异常"""
    with pytest.raises(ToolError) as excinfo:
        call()
    
    assert "缺少工具名称" in str(excinfo.value)


def test_call_tool_with_kwargs():
    """使用关键字参数调用工具"""
    @register("calculator", "计算器")
    def calculator(a, b, op="add"):
        if op == "add":
            return a + b
        elif op == "mul":
            return a * b
        return None
    
    result = call("calculator", a=10, b=5, op="mul")
    assert result == 50


def test_unregister():
    """注销工具"""
    @register("to_remove", "要删除的工具")
    def to_remove():
        return "removed"
    
    assert len(list_tools()) == 1
    
    unregister("to_remove")
    assert len(list_tools()) == 0


def test_unregister_nonexistent():
    """注销不存在的工具应静默处理"""
    unregister("nonexistent")  # 不应抛出异常


def test_list_tools():
    """列出所有工具"""
    @register("tool1", "工具1")
    def tool1():
        pass
    
    @register("tool2", "工具2")
    def tool2():
        pass
    
    tools = list_tools()
    assert len(tools) == 2
    names = [t["name"] for t in tools]
    assert "tool1" in names
    assert "tool2" in names


def test_get_tool_defs():
    """获取工具定义（OpenAI格式）"""
    @register("test_def", "测试定义")
    def test_def():
        pass
    
    defs = get_tool_defs()
    assert len(defs) == 1
    assert defs[0]["function"]["name"] == "test_def"
    assert defs[0]["function"]["description"] == "测试定义"


def test_get_tool_defs_with_whitelist():
    """使用白名单过滤工具"""
    @register("tool_a", "工具A")
    def tool_a():
        pass
    
    @register("tool_b", "工具B")
    def tool_b():
        pass
    
    defs = get_tool_defs(whitelist=["tool_a"])
    assert len(defs) == 1
    assert defs[0]["function"]["name"] == "tool_a"


def test_get_tool_schema():
    """获取工具的JSON Schema"""
    @register("schema_test", "schema测试")
    def schema_test():
        pass
    
    schema = get_tool_schema("schema_test")
    assert schema is not None
    assert schema["type"] == "object"


def test_get_tool_schema_nonexistent():
    """获取不存在工具的schema应返回None"""
    schema = get_tool_schema("nonexistent")
    assert schema is None


def test_clear():
    """清空工具注册表"""
    @register("tool1", "工具1")
    def tool1():
        pass
    
    @register("tool2", "工具2")
    def tool2():
        pass
    
    assert len(list_tools()) == 2
    
    clear()
    assert len(list_tools()) == 0


def test_tool_execution_error():
    """工具执行异常应被包装为ToolError"""
    @register("error_tool", "会出错的工具")
    def error_tool():
        raise ValueError("内部错误")
    
    with pytest.raises(ToolError) as excinfo:
        call("error_tool")
    
    assert "执行失败" in str(excinfo.value)
    assert "内部错误" in str(excinfo.value)