"""MCP集成测试脚本 - 验证multi-search-engine技能调用"""

import asyncio
import logging
import sys
import os

# 添加当前目录到Python路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 设置详细日志
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger("mcp_test")

async def test_mcp_integration():
    """测试MCP集成"""
    print("\n" + "=" * 70)
    print("MCP 集成测试 - multi-search-engine")
    print("=" * 70)

    try:
        # 测试1: 导入桥接器
        print("\n[测试1] 导入MCP桥接器...")
        from mcp_services.yunshu_mcp_bridge import YunshuMCPBridge
        
        bridge = YunshuMCPBridge()
        print("  [OK] 桥接器导入成功")

        # 测试2: 安装服务
        print("\n[测试2] 安装multi-search-engine服务...")
        install_result = await bridge.install_service("multi-search-engine")
        print(f"  安装结果: {install_result}")
        if install_result.get("ok"):
            print("  [OK] 服务安装成功")
        else:
            print(f"  [失败] 安装失败: {install_result.get('error')}")
            return

        # 测试3: 调用搜索工具
        print("\n[测试3] 调用search工具...")
        search_result = await bridge.call_tool(
            "multi-search-engine",
            "search",
            {
                "query": "AI人工智能",
                "engines": ["baidu"],
                "num_results": 3
            }
        )
        print(f"  搜索结果: {search_result}")
        
        if search_result.get("ok"):
            results = search_result.get("results", [])
            print(f"  [OK] 搜索成功，返回 {len(results)} 条结果")
            for i, result in enumerate(results[:3], 1):
                print(f"    {i}. {result.get('title', '')}")
                print(f"       {result.get('url', '')}")
        else:
            print(f"  [失败] 搜索失败: {search_result.get('error')}")

        # 测试4: 获取引擎列表
        print("\n[测试4] 调用get_engines工具...")
        engines_result = await bridge.call_tool(
            "multi-search-engine",
            "get_engines",
            {}
        )
        if engines_result.get("ok"):
            engines = engines_result.get("engines", [])
            print(f"  [OK] 获取成功，共 {len(engines)} 个搜索引擎")
            print(f"  引擎列表: {', '.join(engines)}")
        else:
            print(f"  [失败] 获取失败: {engines_result.get('error')}")

        # 测试5: 获取统计信息
        print("\n[测试5] 调用get_stats工具...")
        stats_result = await bridge.call_tool(
            "multi-search-engine",
            "get_stats",
            {}
        )
        if stats_result.get("ok"):
            stats = stats_result.get("stats", {})
            print(f"  [OK] 获取成功")
            print(f"  统计信息: {stats}")
        else:
            print(f"  [失败] 获取失败: {stats_result.get('error')}")

        # 测试6: 停止服务
        print("\n[测试6] 停止服务...")
        stop_result = await bridge.stop_service("multi-search-engine")
        print(f"  [OK] 服务停止成功" if stop_result else "  [失败] 服务停止失败")

        print("\n" + "=" * 70)
        print("MCP 集成测试完成")
        print("=" * 70)

    except Exception as e:
        logger.error(f"MCP集成测试失败: {e}", exc_info=True)
        print(f"\n[错误] 测试失败: {e}")

async def test_extension_manager_registration():
    """测试扩展管理器注册"""
    print("\n" + "=" * 70)
    print("扩展管理器注册检查")
    print("=" * 70)

    try:
        from agent.extensions.base import BUILTIN_EXTENSIONS
        
        # 检查MCP扩展列表
        mcp_extensions = BUILTIN_EXTENSIONS.get("mcp", [])
        print(f"\n[检查] 内置MCP扩展数量: {len(mcp_extensions)}")
        
        # 查找multi-search-engine
        multi_search = None
        for ext in mcp_extensions:
            if ext.get("id") == "multi-search-engine":
                multi_search = ext
                break
        
        if multi_search:
            print("\n[OK] multi-search-engine已注册")
            print(f"  ID: {multi_search.get('id')}")
            print(f"  名称: {multi_search.get('name')}")
            print(f"  描述: {multi_search.get('description')}")
            print(f"  协议: {multi_search.get('protocol')}")
            print(f"  命令: {multi_search.get('command')}")
            print(f"  参数: {multi_search.get('args')}")
        else:
            print("\n[警告] multi-search-engine未在内置扩展列表中")

        # 列出所有MCP扩展
        print("\n[列表] 所有内置MCP扩展:")
        for ext in mcp_extensions:
            status = " [内置]" if ext.get("builtin") else ""
            print(f"  - {ext.get('id')}: {ext.get('name')}{status}")

    except Exception as e:
        logger.error(f"扩展管理器检查失败: {e}", exc_info=True)
        print(f"\n[错误] 检查失败: {e}")

if __name__ == "__main__":
    # 运行测试
    asyncio.run(test_mcp_integration())
    asyncio.run(test_extension_manager_registration())