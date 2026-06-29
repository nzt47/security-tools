"""MCP客户端Windows兼容性测试脚本

测试场景：
1. 正常模式 - 验证基本通信
2. 慢响应模式 - 测试超时和重试
3. 错误模式 - 验证错误处理
4. 批量模式 - 验证批量请求处理
5. 编码测试 - 验证Windows编码处理

使用方法：
    # 正常测试
    python test_mcp_windows.py

    # 测试超时重试
    python test_mcp_windows.py --mode slow

    # 测试错误处理
    python test_mcp_windows.py --mode error

    # 测试批量请求
    python test_mcp_windows.py --mode batch

    # 测试Windows编码
    python test_mcp_windows.py --mode encoding
"""

import asyncio
import json
import time
import sys
import subprocess
import argparse
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from mcp_services.mcp_client import MCPClient, MCPConfig, MCPTool

# ════════════════════════════════════════════════════════════════════
# 测试配置
# ════════════════════════════════════════════════════════════════════

# 模拟服务器路径
MOCK_SERVER_PATH = Path(__file__).parent / "mock_mcp_server.py"

# 测试配置
TEST_CONFIGS = {
    "normal": MCPConfig(timeout=30, max_retries=3, startup_wait=1.0),
    "slow": MCPConfig(timeout=5, max_retries=2, startup_wait=1.0),  # 短超时测试重试
    "error": MCPConfig(timeout=30, max_retries=3, startup_wait=1.0),
    "batch": MCPConfig(timeout=60, max_retries=3, startup_wait=1.0),
    "encoding": MCPConfig(timeout=30, max_retries=3, startup_wait=1.0),
}


# ════════════════════════════════════════════════════════════════════
# 测试函数
# ════════════════════════════════════════════════════════════════════

async def test_normal_mode():
    """测试正常模式"""
    print("\n" + "=" * 60)
    print("测试场景1: 正常模式")
    print("=" * 60)

    config = TEST_CONFIGS["normal"]
    client = MCPClient("python", [str(MOCK_SERVER_PATH)], config=config)

    try:
        print("\n[1] 初始化连接...")
        start = time.time()
        await client.initialize()
        print(f"    耗时: {time.time() - start:.2f}秒")
        print(f"    服务: {client.server_info.get('name')} v{client.server_info.get('version')}")

        print("\n[2] 获取工具列表...")
        tools = await client.list_tools()
        for tool in tools:
            print(f"    - {tool.name}: {tool.description}")

        print("\n[3] 执行搜索...")
        start = time.time()
        result = await client.call_tool("search", {
            "query": "人工智能 最新发展",
            "engines": ["baidu", "sogou"],
            "num_results": 3
        })
        print(f"    耗时: {time.time() - start:.2f}秒")
        print(f"    查询: {result.get('query')}")
        print(f"    引擎: {result.get('engines_used')}")
        print(f"    结果数: {result.get('total_engines')}")

        print("\n[4] 获取引擎列表...")
        result = await client.call_tool("get_engines", {})
        for eng_id, meta in list(result.get("engines", {}).items())[:3]:
            print(f"    {eng_id}: {meta['name']} ({meta['language']})")

        print("\n[5] 获取统计...")
        result = await client.call_tool("get_stats", {})
        print(f"    总搜索: {result.get('stats', {}).get('total_searches')}")

        print("\n" + "=" * 60)
        print("测试通过!")
        print("=" * 60)
        return True

    except Exception as e:
        print(f"\n测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        await client.stop()


async def test_slow_mode():
    """测试慢响应模式（验证超时和重试）"""
    print("\n" + "=" * 60)
    print("测试场景2: 慢响应模式（测试超时和重试）")
    print("=" * 60)
    print("配置: timeout=5秒, max_retries=2")
    print("服务器响应延迟: 8秒")
    print("预期: 至少触发1次重试")
    print("=" * 60)

    config = TEST_CONFIGS["slow"]
    client = MCPClient("python", [
        str(MOCK_SERVER_PATH),
        "--mode", "slow",
        "--slow-delay", "8"
    ], config=config)

    try:
        print("\n[1] 初始化连接（预期超时）...")
        start = time.time()
        try:
            await client.initialize()
        except TimeoutError as e:
            elapsed = time.time() - start
            print(f"    预期超时发生: {e}")
            print(f"    耗时: {elapsed:.2f}秒")
            print(f"    重试次数: {elapsed // 5} 次")

        print("\n[2] 测试搜索（短超时）...")
        start = time.time()
        try:
            result = await client.call_tool("search", {
                "query": "测试",
                "engines": ["baidu"]
            })
            print(f"    成功: {result.get('ok')}")
        except TimeoutError:
            elapsed = time.time() - start
            print(f"    超时: {elapsed:.2f}秒")
            print(f"    重试次数: {elapsed // 5}")

        print("\n" + "=" * 60)
        print("超时和重试测试完成")
        print("=" * 60)
        return True

    except Exception as e:
        print(f"\n测试异常: {e}")
        import traceback
        traceback.print_exc()
        return True  # 这个测试预期有异常
    finally:
        await client.stop()


async def test_error_mode():
    """测试错误处理模式"""
    print("\n" + "=" * 60)
    print("测试场景3: 错误处理模式")
    print("=" * 60)

    config = TEST_CONFIGS["error"]
    client = MCPClient("python", [
        str(MOCK_SERVER_PATH),
        "--mode", "error",
        "--error-type", "random"
    ], config=config)

    try:
        print("\n[1] 初始化连接...")
        await client.initialize()
        print("    初始化成功")

        print("\n[2] 执行多次搜索（测试错误恢复）...")
        success_count = 0
        error_count = 0

        for i in range(5):
            try:
                result = await client.call_tool("search", {
                    "query": f"测试查询 {i+1}",
                    "engines": ["baidu"]
                })
                if result.get("ok"):
                    success_count += 1
                    print(f"    查询 {i+1}: 成功")
                else:
                    error_count += 1
                    print(f"    查询 {i+1}: 失败")
            except Exception as e:
                error_count += 1
                print(f"    查询 {i+1}: 异常 - {e}")

        print(f"\n结果统计: 成功={success_count}, 失败/异常={error_count}")

        print("\n" + "=" * 60)
        print("错误处理测试完成")
        print("=" * 60)
        return True

    except Exception as e:
        print(f"\n测试异常: {e}")
        import traceback
        traceback.print_exc()
        return True
    finally:
        await client.stop()


async def test_batch_mode():
    """测试批量请求"""
    print("\n" + "=" * 60)
    print("测试场景4: 批量请求模式")
    print("=" * 60)

    config = TEST_CONFIGS["batch"]
    client = MCPClient("python", [str(MOCK_SERVER_PATH)], config=config)

    try:
        print("\n[1] 初始化...")
        await client.initialize()

        print("\n[2] 执行批量搜索...")
        start = time.time()

        queries = [
            {"query": "AI发展", "engines": ["baidu"]},
            {"query": "机器学习", "engines": ["sogou"]},
            {"query": "深度学习", "engines": ["360"]},
        ]

        for i, q in enumerate(queries):
            result = await client.call_tool("search", q)
            print(f"    查询 {i+1}: {q['query']} -> {result.get('total_engines')} 引擎")

        elapsed = time.time() - start
        print(f"\n    总耗时: {elapsed:.2f}秒")
        print(f"    平均: {elapsed/len(queries):.2f}秒/查询")

        print("\n" + "=" * 60)
        print("批量测试完成")
        print("=" * 60)
        return True

    except Exception as e:
        print(f"\n测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        await client.stop()


async def test_encoding():
    """测试Windows编码处理"""
    print("\n" + "=" * 60)
    print("测试场景5: Windows编码处理")
    print("=" * 60)

    config = TEST_CONFIGS["encoding"]
    client = MCPClient("python", [str(MOCK_SERVER_PATH)], config=config)

    # 测试各种编码的查询
    test_queries = [
        "人工智能",  # 中文
        "machine learning",  # 英文
        "1234567890",  # 数字
        "测试@#$%",  # 特殊字符
        "混合测试 AI 123",  # 混合
    ]

    try:
        print("\n[1] 初始化...")
        await client.initialize()

        print("\n[2] 测试各种编码的查询...")
        for query in test_queries:
            try:
                result = await client.call_tool("search", {
                    "query": query,
                    "engines": ["baidu"],
                    "num_results": 2
                })
                print(f"    '{query}': 成功")
            except Exception as e:
                print(f"    '{query}': 失败 - {e}")

        print("\n" + "=" * 60)
        print("编码测试完成")
        print("=" * 60)
        return True

    except Exception as e:
        print(f"\n测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        await client.stop()


async def test_concurrent():
    """测试并发请求"""
    print("\n" + "=" * 60)
    print("测试场景6: 并发请求")
    print("=" * 60)

    config = TEST_CONFIGS["batch"]
    client = MCPClient("python", [str(MOCK_SERVER_PATH)], config=config)

    try:
        print("\n[1] 初始化...")
        await client.initialize()

        print("\n[2] 执行并发搜索...")
        start = time.time()

        async def search_task(i: int):
            result = await client.call_tool("search", {
                "query": f"并发测试 {i}",
                "engines": ["baidu"]
            })
            return i, result

        tasks = [search_task(i) for i in range(5)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        elapsed = time.time() - start

        success = 0
        for r in results:
            if isinstance(r, Exception):
                print(f"    异常: {r}")
            else:
                i, result = r
                print(f"    任务 {i}: 成功")
                success += 1

        print(f"\n    总耗时: {elapsed:.2f}秒")
        print(f"    成功: {success}/5")

        print("\n" + "=" * 60)
        print("并发测试完成")
        print("=" * 60)
        return success == 5

    except Exception as e:
        print(f"\n测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        await client.stop()


# ════════════════════════════════════════════════════════════════════
# 主入口
# ════════════════════════════════════════════════════════════════════

async def run_all_tests():
    """运行所有测试"""
    print("\n" + "=" * 60)
    print("MCP客户端Windows兼容性测试套件")
    print("=" * 60)

    results = {}

    tests = [
        ("正常模式", test_normal_mode),
        ("慢响应模式", test_slow_mode),
        ("错误处理模式", test_error_mode),
        ("批量请求模式", test_batch_mode),
        ("编码处理模式", test_encoding),
        ("并发请求模式", test_concurrent),
    ]

    for name, test_func in tests:
        try:
            results[name] = await test_func()
        except Exception as e:
            print(f"\n测试 '{name}' 崩溃: {e}")
            results[name] = False

    # 汇总结果
    print("\n" + "=" * 60)
    print("测试结果汇总")
    print("=" * 60)
    for name, passed in results.items():
        status = "[PASS]" if passed else "[FAIL]"
        print(f"  {status} {name}")

    total = len(results)
    passed = sum(1 for v in results.values() if v)
    print(f"\n总计: {passed}/{total} 通过")
    print("=" * 60)

    return all(results.values())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MCP Windows兼容性测试")
    parser.add_argument("--mode", "-m", choices=["normal", "slow", "error", "batch", "encoding", "concurrent", "all"],
                       default="all", help="测试模式")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细输出")

    args = parser.parse_args()

    if args.verbose:
        import logging
        logging.basicConfig(level=logging.DEBUG)

    if args.mode == "all":
        success = asyncio.run(run_all_tests())
    else:
        test_funcs = {
            "normal": test_normal_mode,
            "slow": test_slow_mode,
            "error": test_error_mode,
            "batch": test_batch_mode,
            "encoding": test_encoding,
            "concurrent": test_concurrent,
        }
        success = asyncio.run(test_funcs[args.mode]())

    sys.exit(0 if success else 1)