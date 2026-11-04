"""测试 Tavily API（**密钥只从环境变量取，脚本内不落明文**）

【C-6-B4 2026-11-04】原脚本把一串 tavily key 硬编码在这里并整表打印；
现在改成 `TAVILY_API_KEY` 环境变量 + 只打印指纹/布尔状态。
"""
import os
import sys
sys.path.insert(0, ".")

from agent.web.http_client import HttpClient
from agent.web.search import SearchEngine


api_key = (os.environ.get("TAVILY_API_KEY") or "").strip()
if not api_key:
    print("未设置 TAVILY_API_KEY，跳过（不会用硬编码密钥）")
    raise SystemExit(2)

# 创建 HttpClient
http = HttpClient()

# 创建 SearchEngine
se = SearchEngine()
se.set_http_client(http)

# 设置 API Key
se.update_config({"tavily_api_key": api_key})

print("API Keys configured:", {name: bool(value) for name, value in se._api_keys.items()})
print("Config engines:", sorted((se._config or {}).keys()))

# 测试搜索
result = se.search("人工智能", engine="tavily", num_results=3)
print("搜索结果条数:", len((result or {}).get("results") or []))