"""MCP服务演示脚本 - 展示如何在云枢中调用Hermes多引擎搜索技能

运行方式：
    python mcp_services/demo_mcp_integration.py

本脚本演示完整的MCP集成流程：
1. 创建MCP服务（Hermes风格）
2. 创建MCP客户端
3. 在云枢中配置和注册
4. 调用MCP工具
"""

import asyncio
import json
import sys
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from mcp_services.multi_search_engine import MultiSearchEngine
from mcp_services.mcp_client import MCPClient
from mcp_services.yunshu_mcp_bridge import YunshuMCPBridge, MCP_SERVICE_TEMPLATES


async def demo_step1_direct_search():
    """步骤1: 直接使用搜索服务（无需MCP协议）"""
    print("\n" + "=" * 60)
    print("步骤1: 直接使用搜索服务")
    print("=" * 60)

    search = MultiSearchEngine()

    # 执行中文搜索
    result = search.search(
        query="人工智能 最新发展",
        engines=["baidu", "sogou", "360"],
        num_results=3,
        language="zh"
    )

    print(f"\n[OK] 查询成功: {result['query']}")
    print(f"[INFO] 使用引擎: {result['total_engines']} 个")
    print(f"[INFO] 引擎列表: {', '.join(result['engines_used'])}")

    print("\n搜索结果:")
    for engine_result in result["results"]:
        print(f"\n  【{engine_result['engine_name']}】")
        for r in engine_result["results"][:2]:
            print(f"    • {r['title']}")
            print(f"      {r['snippet'][:70]}...")


async def demo_step2_mcp_protocol():
    """步骤2: 通过MCP协议调用"""
    print("\n" + "=" * 60)
    print("步骤2: 通过MCP协议调用服务")
    print("=" * 60)

    # 创建MCP客户端（连接到multi_search_engine服务）
    client = MCPClient("python", ["mcp_services/multi_search_engine.py"])

    try:
        # 初始化连接
        print("\n[1] 初始化MCP连接...")
        await client.initialize()
        print(f"    [OK] 服务: {client.server_info.get('name')}")
        print(f"    [OK] 版本: {client.server_info.get('version')}")
        print(f"    [OK] 工具数: {len(client.tools)}")

        # 列出可用工具
        print("\n[2] 可用工具:")
        for tool in client.tools:
            print(f"    • {tool.name}")
            print(f"      {tool.description}")

        # 调用搜索工具
        print("\n[3] 调用search工具...")
        result = await client.call_tool("search", {
            "query": "机器学习 2024",
            "engines": ["baidu", "google"],
            "num_results": 3
        })

        print(f"    [OK] 查询: {result['query']}")
        print(f"    [OK] 引擎: {', '.join(result['engines_used'])}")
        print(f"    [OK] 结果数: {sum(len(r['results']) for r in result['results'])}")

        for engine_result in result["results"][:2]:
            print(f"\n    【{engine_result['engine_name']}】")
            for r in engine_result["results"][:2]:
                print(f"      • {r['title']}")

        # 获取引擎列表
        print("\n[4] 获取支持的引擎...")
        engines_result = await client.call_tool("get_engines", {})

        global_engines = [k for k, v in engines_result["engines"].items() if v["category"] == "global"]
        chinese_engines = [k for k, v in engines_result["engines"].items() if v["category"] == "chinese"]

        print(f"    全球引擎: {', '.join(global_engines)}")
        print(f"    中国引擎: {', '.join(chinese_engines)}")

        # 获取统计
        print("\n[5] 获取使用统计...")
        stats = await client.call_tool("get_stats", {})
        print(f"    总搜索次数: {stats['stats']['total_searches']}")

    finally:
        await client.stop()
        print("\n    [OK] MCP连接已关闭")


async def demo_step3_yunshu_integration():
    """步骤3: 云枢集成"""
    print("\n" + "=" * 60)
    print("步骤3: 云枢MCP集成")
    print("=" * 60)

    # 创建云枢MCP桥接器
    bridge = YunshuMCPBridge()

    # 安装多引擎搜索服务
    print("\n[1] 在云枢中安装MCP服务...")
    install_result = await bridge.install_service("multi-search-engine")
    print(f"    [OK] 安装成功!")
    print(f"    [INFO] 服务ID: {install_result['service_id']}")
    print(f"    [INFO] 名称: {install_result['name']}")
    print(f"    [INFO] 工具: {', '.join(install_result['tools'])}")

    # 列出已安装的服务
    print("\n[2] 云枢MCP服务列表:")
    for svc in bridge.list_services():
        print(f"    * {svc['name']} ({svc['id']})")
        print(f"      状态: {svc['status']}")
        print(f"      工具: {', '.join(svc['tools'])}")

    # 调用工具（模拟云枢调用方式）
    print("\n[3] 通过云枢接口调用MCP工具...")

    # 搜索工具
    search_result = await bridge.call_tool("multi-search-engine", "search", {
        "query": "量子计算 最新突破",
        "engines": ["baidu", "sogou", "360", "so"],
        "num_results": 2
    })

    print(f"    [OK] 搜索完成")
    print(f"    [INFO] 查询: {search_result['query']}")
    print(f"    [INFO] 使用引擎: {', '.join(search_result['engines_used'])}")

    print("\n    搜索结果预览:")
    for engine_result in search_result["results"][:2]:
        print(f"\n    【{engine_result['engine']}】{engine_result['engine_name']}:")
        for r in engine_result["results"][:1]:
            print(f"      * {r['title']}")
            print(f"        {r['snippet'][:60]}...")

    # 获取引擎列表
    print("\n[4] 获取引擎列表...")
    engines_result = await bridge.call_tool("multi-search-engine", "get_engines", {})

    print("\n    支持的引擎:")
    for eng_id, meta in list(engines_result["engines"].items())[:8]:
        key_status = "[需要密钥]" if meta.get("need_key") else "[无需密钥]"
        print(f"    * {eng_id}: {meta['name']} ({meta['language']}) - {key_status}")

    # 注册为云枢工具
    print("\n[5] 注册为云枢可调用工具...")
    tools = bridge._tool_handlers

    # 演示格式化输出
    print("\n    格式化搜索结果示例:")
    print("    " + "-" * 50)

    lines = [f"[搜索] 量子计算 最新突破"]
    lines.append(f"使用引擎: {', '.join(search_result['engines_used'])}")
    lines.append("")

    for engine_result in search_result["results"][:3]:
        lines.append(f"【{engine_result['engine_name']}】")
        for r in engine_result["results"][:2]:
            lines.append(f"  * {r['title']}")
            lines.append(f"    {r['snippet'][:50]}...")
        lines.append("")

    print("    " + "\n    ".join(lines[:10]))
    print("    " + "-" * 50)

    # 清理
    await bridge.stop_service("multi-search-engine")
    print("\n    [OK] 云枢MCP服务已停止")


async def demo_step4_network_config():
    """步骤4: 网络配置示例"""
    print("\n" + "=" * 60)
    print("步骤4: 云枢网络配置(JSON示例)")
    print("=" * 60)

    # 展示如何在network_config.json中配置MCP服务
    config_example = {
        "mcp": {
            "enabled": True,
            "services": [
                {
                    "id": "multi-search-engine",
                    "name": "多引擎搜索",
                    "description": "Hermes/OpenClaw多引擎搜索技能",
                    "command": "python",
                    "args": ["mcp_services/multi_search_engine.py"],
                    "protocol": "stdio",
                    "timeout": 30,
                    "enabled": True,
                    "tools": ["search", "get_engines", "get_stats"]
                },
                {
                    "id": "filesystem",
                    "name": "文件系统",
                    "description": "MCP官方文件系统服务",
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-filesystem"],
                    "protocol": "stdio",
                    "timeout": 30,
                    "enabled": True
                }
            ]
        }
    }

    print("\n在 agent/data/network_config.json 中添加以下配置:")
    print("-" * 50)
    print(json.dumps(config_example, indent=2, ensure_ascii=False))
    print("-" * 50)

    print("\n或者通过API安装:")
    print("-" * 50)
    print("""
POST /api/extensions/mcp/install
Content-Type: application/json

{
    "service_id": "multi-search-engine"
}

响应:
{
    "ok": true,
    "service_id": "multi-search-engine",
    "name": "多引擎搜索",
    "tools": ["search", "get_engines", "get_stats"]
}
    """)
    print("-" * 50)


async def demo_step5_tool_call():
    """步骤5: 云枢工具调用示例"""
    print("\n" + "=" * 60)
    print("步骤5: 云枢调用MCP工具的方式")
    print("=" * 60)

    print("""
在云枢的数字生命系统中，可以通过以下方式调用MCP工具：

方式1: 直接通过MCP桥接器调用
------------------------------------------------
    bridge = YunshuMCPBridge()
    await bridge.install_service("multi-search-engine")

    result = await bridge.call_tool("multi-search-engine", "search", {
        "query": "AI新闻",
        "engines": ["baidu", "sogou"],
        "num_results": 10
    })

方式2: 通过注册的工具函数调用
------------------------------------------------
    from mcp_services.yunshu_mcp_bridge import register_mcp_tools_to_yunshu

    bridge = YunshuMCPBridge()
    await bridge.install_service("multi-search-engine")
    tools = register_mcp_tools_to_yunshu(bridge)

    # 调用注册的mcp_search工具
    result = await tools["mcp_search"](
        query="AI新闻",
        engines=["baidu", "sogou"]
    )

方式3: 通过API调用
------------------------------------------------
    POST /api/mcp/call
    Content-Type: application/json

    {
        "service_id": "multi-search-engine",
        "tool": "search",
        "arguments": {
            "query": "AI新闻",
            "engines": ["baidu", "sogou"],
            "num_results": 10
        }
    }

方式4: 在对话中自然语言调用
------------------------------------------------
    用户: "帮我用百度和搜狗搜索最新的AI新闻"

    云枢AI理解意图后，调用:
    -> mcp_search(query="AI新闻", engines=["baidu", "sogou"])

    返回格式化结果给用户
    """)


async def main():
    """主演示流程"""
    print("\n" + "=" * 60)
    print("[云枢MCP集成演示] 调用Hermes多引擎搜索技能")
    print("=" * 60)

    try:
        # 步骤1: 直接使用搜索服务
        await demo_step1_direct_search()

        # 步骤2: 通过MCP协议调用
        await demo_step2_mcp_protocol()

        # 步骤3: 云枢集成
        await demo_step3_yunshu_integration()

        # 步骤4: 网络配置示例
        await demo_step4_network_config()

        # 步骤5: 工具调用示例
        await demo_step5_tool_call()

        print("\n" + "=" * 60)
        print("[演示完成]")
        print("=" * 60)

        print("\n相关文件:")
        print("  - mcp_services/multi_search_engine.py - MCP服务实现")
        print("  - mcp_services/mcp_client.py - MCP客户端")
        print("  - mcp_services/yunshu_mcp_bridge.py - 云枢MCP桥接")
        print("  - agent/data/network_config.json - 网络配置(需添加mcp.servers)")

        print("\n下一步:")
        print("  1. 运行: python mcp_services/demo_mcp_integration.py")
        print("  2. 在云枢中配置network_config.json添加MCP服务")
        print("  3. 通过API安装MCP服务并调用")

    except Exception as e:
        print(f"\n[演示失败] {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
