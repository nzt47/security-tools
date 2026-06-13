#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
云枢 LLM API Key 配置脚本

支持多种 LLM 提供商:
- OpenAI (GPT-4, GPT-3.5)
- Anthropic (Claude)
- 本地模型 (Ollama)
- 硅基流动 (SiliconFlow)
"""

import os
import sys
import json
from pathlib import Path

class LLMConfigManager:
    """LLM 配置管理器"""
    
    # 支持的提供商
    PROVIDERS = {
        'openai': {
            'name': 'OpenAI',
            'models': ['gpt-4o', 'gpt-4-turbo', 'gpt-4', 'gpt-3.5-turbo'],
            'default_model': 'gpt-4o',
            'api_key_hint': 'sk-...',
            'base_url': 'https://api.openai.com/v1'
        },
        'anthropic': {
            'name': 'Anthropic (Claude)',
            'models': ['claude-sonnet-4-20250514', 'claude-3-5-sonnet-latest', 'claude-3-opus', 'claude-3-haiku'],
            'default_model': 'claude-3-5-sonnet-latest',
            'api_key_hint': 'sk-ant-...',
            'base_url': 'https://api.anthropic.com'
        },
        'siliconflow': {
            'name': 'SiliconFlow (硅基流动)',
            'models': ['Qwen/Qwen2.5-7B-Instruct', 'deepseek-ai/DeepSeek-V2.5', 'THUDM/glm-4-9b-chat'],
            'default_model': 'Qwen/Qwen2.5-7B-Instruct',
            'api_key_hint': 'sk-...',
            'base_url': 'https://api.siliconflow.cn/v1'
        },
        'ollama': {
            'name': 'Ollama (本地模型)',
            'models': ['llama3', 'qwen2.5', 'deepseek-v2', 'glm4'],
            'default_model': 'qwen2.5',
            'api_key_hint': '(本地模型无需API Key)',
            'base_url': 'http://localhost:11434/v1'
        }
    }
    
    def __init__(self):
        self.config_file = Path.home() / '.Yunshu' / 'llm_config.json'
        self.config_file.parent.mkdir(parents=True, exist_ok=True)
    
    def select_provider(self) -> str:
        """选择 LLM 提供商"""
        print("\n" + "="*70)
        print("选择 LLM 提供商")
        print("="*70)
        
        providers = list(self.PROVIDERS.keys())
        for i, key in enumerate(providers, 1):
            info = self.PROVIDERS[key]
            print(f"{i}. {info['name']}")
            print(f"   默认模型: {info['default_model']}")
            print(f"   API Key格式: {info['api_key_hint']}")
            print()
        
        while True:
            try:
                choice = input("请选择提供商 (输入数字 1-4): ").strip()
                idx = int(choice) - 1
                if 0 <= idx < len(providers):
                    return providers[idx]
            except ValueError:
                pass
            print("输入无效，请重新选择")
    
    def select_model(self, provider: str) -> str:
        """选择模型"""
        info = self.PROVIDERS[provider]
        models = info['models']
        
        print("\n可用模型:")
        for i, model in enumerate(models, 1):
            marker = " (默认)" if model == info['default_model'] else ""
            print(f"{i}. {model}{marker}")
        
        while True:
            try:
                choice = input(f"\n请选择模型 (默认按回车): ").strip()
                if not choice:
                    return info['default_model']
                idx = int(choice) - 1
                if 0 <= idx < len(models):
                    return models[idx]
            except ValueError:
                pass
            print("输入无效，请重新选择")
    
    def get_api_key(self, provider: str) -> str:
        """获取 API Key"""
        info = self.PROVIDERS[provider]
        
        # 优先从环境变量读取
        env_key = f"{provider.upper()}_API_KEY"
        env_value = os.getenv(env_key)
        if env_value:
            print(f"\n✓ 检测到环境变量 {env_key}")
            return env_value
        
        # 尝试从配置文件读取
        if self.config_file.exists():
            with open(self.config_file, 'r') as f:
                saved = json.load(f)
                if provider in saved and saved[provider].get('api_key'):
                    print(f"\n✓ 检测到已保存的 {info['name']} API Key")
                    return saved[provider]['api_key']
        
        # 交互式输入
        print(f"\n请输入 {info['name']} API Key:")
        print(f"(提示: {info['api_key_hint']})")
        print(f"(也可以设置环境变量 {env_key})")
        
        api_key = input("API Key: ").strip()
        
        if not api_key:
            print("未输入 API Key，将跳过配置")
            return ""
        
        return api_key
    
    def get_base_url(self, provider: str) -> str:
        """获取 API 基础 URL"""
        info = self.PROVIDERS[provider]
        
        # 对于本地模型，始终使用默认URL
        if provider == 'ollama':
            return info['base_url']
        
        # 尝试从环境变量读取
        env_key = f"{provider.upper()}_BASE_URL"
        env_value = os.getenv(env_key)
        if env_value:
            return env_value
        
        # 使用默认URL
        return info['base_url']
    
    def save_config(self, provider: str, model: str, api_key: str, base_url: str):
        """保存配置"""
        config = {
            'provider': provider,
            'model': model,
            'api_key': api_key,
            'base_url': base_url,
            'configured_at': str(Path(__file__).parent / 'config.py')
        }
        
        with open(self.config_file, 'w') as f:
            json.dump(config, f, indent=2)
        
        print(f"\n✓ 配置已保存到: {self.config_file}")
    
    def configure(self):
        """交互式配置流程"""
        print("\n" + "="*70)
        print("🎭 云枢 LLM 配置向导")
        print("="*70)
        print("\n配置 LLM API Key，让云枢能够进行真正的智能对话！")
        
        # 1. 选择提供商
        provider = self.select_provider()
        info = self.PROVIDERS[provider]
        
        # 2. 选择模型
        model = self.select_model(provider)
        
        # 3. 获取 API Key
        api_key = self.get_api_key(provider)
        
        # 4. 获取 Base URL
        base_url = self.get_base_url(provider)
        
        if not api_key and provider != 'ollama':
            print("\n⚠️ 未提供 API Key，跳过保存")
            print("\n要手动设置环境变量:")
            print(f"  set {provider.upper()}_API_KEY=your_api_key")
            return None
        
        # 5. 保存配置
        self.save_config(provider, model, api_key, base_url)
        
        # 6. 生成使用说明
        print("\n" + "="*70)
        print("配置完成！")
        print("="*70)
        
        # 生成启动脚本
        script_content = f'''@echo off
REM 云枢智能对话启动脚本
REM 自动设置 LLM API Key

set {provider.upper()}_API_KEY={api_key}
set {provider.upper()}_MODEL={model}
set {provider.upper()}_BASE_URL={base_url}

python main.py
'''
        
        script_file = Path.home() / '.Yunshu' / 'start_smart.bat'
        with open(script_file, 'w') as f:
            f.write(script_content)
        
        print(f"\n✓ 已生成智能启动脚本: {script_file}")
        
        # 生成环境变量设置脚本
        env_script = f'''@echo off
REM 设置 {info['name']} 环境变量
REM 复制此文件内容到系统环境变量或每次运行前执行

set {provider.upper()}_API_KEY={api_key}
set {provider.upper()}_MODEL={model}
set {provider.upper()}_BASE_URL={base_url}
'''
        
        env_file = Path.home() / '.Yunshu' / 'set_env.bat'
        with open(env_file, 'w') as f:
            f.write(env_script)
        
        print(f"✓ 已生成环境变量脚本: {env_file}")
        
        print("\n使用方法:")
        print("1. 方式一: 运行智能启动脚本")
        print(f"   .\\{script_file.name}")
        print("")
        print("2. 方式二: 手动设置环境变量后运行")
        print(f"   .\\{env_file.name}")
        print("   python main.py")
        print("")
        print("3. 方式三: 直接运行（需要系统环境变量已设置）")
        print("   python main.py")
        
        return {
            'provider': provider,
            'model': model,
            'api_key': api_key,
            'base_url': base_url
        }
    
    def test_connection(self, config: dict) -> bool:
        """测试连接"""
        if not config or not config.get('api_key'):
            print("\n⚠️ 未配置 API Key，跳过连接测试")
            return False
        
        try:
            print(f"\n正在测试 {config['provider']} 连接...")
            
            # 这里需要根据不同提供商实现测试逻辑
            if config['provider'] == 'openai':
                from openai import OpenAI
                client = OpenAI(api_key=config['api_key'], base_url=config.get('base_url'))
                response = client.chat.completions.create(
                    model=config['model'],
                    messages=[{"role": "user", "content": "Hi"}],
                    max_tokens=10
                )
                print(f"✓ 连接成功！模型: {response.model}")
                return True
            
            elif config['provider'] == 'anthropic':
                import anthropic
                client = anthropic.Anthropic(api_key=config['api_key'])
                response = client.messages.create(
                    model=config['model'],
                    max_tokens=10,
                    messages=[{"role": "user", "content": "Hi"}]
                )
                print(f"✓ 连接成功！模型: {response.model}")
                return True
            
            elif config['provider'] == 'siliconflow':
                from openai import OpenAI
                client = OpenAI(api_key=config['api_key'], base_url=config['base_url'])
                response = client.chat.completions.create(
                    model=config['model'],
                    messages=[{"role": "user", "content": "Hi"}],
                    max_tokens=10
                )
                print(f"✓ 连接成功！模型: {response.model}")
                return True
            
            elif config['provider'] == 'ollama':
                from openai import OpenAI
                client = OpenAI(base_url=config['base_url'], api_key="not-needed")
                response = client.chat.completions.create(
                    model=config['model'],
                    messages=[{"role": "user", "content": "Hi"}],
                    max_tokens=10
                )
                print(f"✓ 连接成功！模型: {response.model}")
                return True
            
        except ImportError as e:
            print(f"\n⚠️ 缺少必要的库: {e}")
            print("请安装: pip install openai anthropic")
            return False
        except Exception as e:
            print(f"\n✗ 连接失败: {e}")
            return False
        
        return False

def main():
    """主函数"""
    manager = LLMConfigManager()
    
    # 检查是否已有配置
    if manager.config_file.exists():
        print("检测到已有配置:")
        with open(manager.config_file, 'r') as f:
            config = json.load(f)
            print(f"  提供商: {config.get('provider')}")
            print(f"  模型: {config.get('model')}")
            print(f"  API Key: {config.get('api_key', '')[:10]}...")
        
        choice = input("\n是否重新配置? (y/N): ").strip().lower()
        if choice != 'y':
            # 测试现有配置
            config = manager.configure_from_file()
            if config:
                manager.test_connection(config)
            return
    
    # 开始配置
    config = manager.configure()
    
    if config:
        # 测试连接
        manager.test_connection(config)
        
        print("\n" + "="*70)
        print("下一步:")
        print("="*70)
        print("1. 运行智能启动脚本:")
        print("   .\\" + str(Path.home() / '.Yunshu' / 'start_smart.bat').replace('\\', '\\\\'))
        print("")
        print("2. 或者设置环境变量后运行:")
        print("   python main.py")
        print("")
        print("3. 测试智能对话:")
        print("   python test_llm_chat.py")

def configure_from_file(self):
    """从配置文件加载"""
    if self.config_file.exists():
        with open(self.config_file, 'r') as f:
            return json.load(f)
    return None

if __name__ == "__main__":
    main()
