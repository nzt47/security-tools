"""
搜索引擎降级机制测试脚本

测试场景：
1. 模拟 DuckDuckGo 搜索失败
2. 验证自动降级到 Tavily API 的切换逻辑
3. 检查详细的切换日志和参数
"""

import sys
import os
import logging
import json
import time

# 设置日志格式
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)

def test_fallback_mechanism():
    """测试搜索引擎降级机制"""
    from agent.web.search import SearchEngine
    
    logger.info("=" * 80)
    logger.info("【测试开始】搜索引擎降级机制测试")
    logger.info("=" * 80)
    
    # 读取配置文件中的 Tavily API Key
    config_path = "agent/data/network_config.json"
    tavily_api_key = ""
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
            tavily_api_key = config.get('search_api_keys', {}).get('tavily', '')
            if tavily_api_key:
                logger.info("✅ 已从配置文件读取 Tavily API Key")
            else:
                logger.warning("⚠️ 配置文件中的 Tavily API Key 为空")
    except Exception as e:
        logger.error(f"❌ 读取配置文件失败: {e}")
    
    # 创建搜索引擎实例
    search_engine = SearchEngine({
        "default_engine": "duckduckgo",
        "engine_priority": ["duckduckgo", "tavily", "bing", "brave", "google"],
        "engine_enabled": {
            "duckduckgo": True,  # DuckDuckGo 启用
            "tavily": True,       # Tavily 启用
            "bing": False,
            "brave": False,
            "google": False,
        },
        "tavily_api_key": tavily_api_key,
        "timeout": 5,  # 5秒超时，方便测试
    })
    
    # 模拟 DuckDuckGo 失败
    logger.info("\n" + "=" * 80)
    logger.info("【测试场景 1】模拟 DuckDuckGo 搜索失败，验证降级到 Tavily")
    logger.info("=" * 80)
    
    # 由于无法真正控制 DuckDuckGo 的网络，我们可以：
    # 1. 设置一个很短的 timeout 来触发超时
    # 2. 或者禁用 DuckDuckGo 来强制降级
    
    logger.info("\n📝 测试步骤：")
    logger.info("   1. 设置 DuckDuckGo 超时为 1 秒（模拟网络延迟或超时）")
    logger.info("   2. 执行搜索，应该会触发降级到 Tavily")
    logger.info("   3. 验证 Tavily 返回了正确的搜索结果")
    
    # 设置超时为 1 秒来模拟快速失败
    search_engine.set_timeout(1)
    
    logger.info("\n🚀 开始执行搜索...")
    logger.info("-" * 80)
    
    # 执行搜索
    result = search_engine.search(
        query="人工智能最新发展",
        num_results=5,
        page=1
    )
    
    logger.info("-" * 80)
    
    # 打印结果
    logger.info("\n" + "=" * 80)
    logger.info("【搜索结果】")
    logger.info("=" * 80)
    logger.info(f"成功状态: {result.get('ok')}")
    logger.info(f"使用的引擎: {result.get('engine')}")
    logger.info(f"是否使用了降级: {result.get('fallback_used')}")
    logger.info(f"降级次数: {result.get('fallback_count')}")
    logger.info(f"耗时: {result.get('elapsed', 0):.2f}秒")
    logger.info(f"结果数量: {len(result.get('results', []))}")
    
    if result.get('fallback_history'):
        logger.info("\n📋 降级历史:")
        for idx, history in enumerate(result.get('fallback_history', []), 1):
            logger.info(f"  #{idx} 引擎: {history.get('engine')} | 状态: {history.get('status')} | 原因: {history.get('reason')}")
    
    if result.get('error'):
        logger.info(f"\n❌ 错误信息: {result.get('error')}")
    
    # 打印前几条结果
    if result.get('results'):
        logger.info("\n📄 搜索结果（前 3 条）:")
        for idx, item in enumerate(result.get('results', [])[:3], 1):
            logger.info(f"  #{idx} {item.get('title')}")
            logger.info(f"      URL: {item.get('url')}")
            logger.info(f"      来源: {item.get('source')}")
            logger.info("")
    
    # 测试场景 2: 禁用 DuckDuckGo，强制使用 Tavily
    logger.info("\n" + "=" * 80)
    logger.info("【测试场景 2】禁用 DuckDuckGo，直接使用 Tavily")
    logger.info("=" * 80)
    
    search_engine.set_engine_enabled("duckduckgo", False)
    logger.info("✅ DuckDuckGo 已禁用")
    
    # 重置超时
    search_engine.set_timeout(30)
    
    logger.info("\n🚀 开始执行搜索...")
    logger.info("-" * 80)
    
    result = search_engine.search(
        query="人工智能最新发展",
        num_results=5,
        page=1
    )
    
    logger.info("-" * 80)
    
    # 打印结果
    logger.info("\n" + "=" * 80)
    logger.info("【搜索结果】")
    logger.info("=" * 80)
    logger.info(f"成功状态: {result.get('ok')}")
    logger.info(f"使用的引擎: {result.get('engine')}")
    logger.info(f"是否使用了降级: {result.get('fallback_used')}")
    logger.info(f"降级次数: {result.get('fallback_count')}")
    logger.info(f"耗时: {result.get('elapsed', 0):.2f}秒")
    logger.info(f"结果数量: {len(result.get('results', []))}")
    
    if result.get('results'):
        logger.info("\n📄 搜索结果（前 3 条）:")
        for idx, item in enumerate(result.get('results', [])[:3], 1):
            logger.info(f"  #{idx} {item.get('title')}")
            logger.info(f"      URL: {item.get('url')}")
            logger.info(f"      来源: {item.get('source')}")
            logger.info("")
    
    # 打印当前状态
    logger.info("\n" + "=" * 80)
    logger.info("【当前搜索引擎状态】")
    logger.info("=" * 80)
    status = search_engine.get_current_status()
    print(json.dumps(status, indent=2, ensure_ascii=False))
    
    logger.info("\n" + "=" * 80)
    logger.info("【测试完成】")
    logger.info("=" * 80)

if __name__ == "__main__":
    test_fallback_mechanism()
