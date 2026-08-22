import os

# 逃逸尝试：读取环境变量密钥（应被静态预扫描拦截 + 环境白名单双保险）
key = os.environ.get("OPENAI_API_KEY")
print(key)
