"""测试 Tavily API"""
import sys
sys.path.insert(0, '.')

from agent.web.http_client import HttpClient
from agent.web.search import SearchEngine

# 创建 HttpClient
http = HttpClient()

# 创建 SearchEngine
se = SearchEngine()
se.set_http_client(http)

# 设置 API Key
se.update_config({'tavily_api_key': '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'})

print("API Keys:", se._api_keys)
print("Config:", se._config)

# 测试搜索
result = se.search('人工智能', engine='tavily', num_results=3)
print('搜索结果:', result)
