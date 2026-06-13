#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
快速检查 LLM 配置状态
"""

import os
import sys

def check_llm_config():
    """检查 LLM 配置状态"""
    
    print("="*70)
    print("LLM Configuration Check")
    print("="*70)
    
    # 检查环境变量
    provider = os.getenv("LLM_PROVIDER", "")
    api_key = os.getenv("LLM_API_KEY", "")
    model = os.getenv("LLM_MODEL", "")
    base_url = os.getenv("LLM_BASE_URL", "")
    
    print("\nEnvironment Variables:")
    print(f"  LLM_PROVIDER: {provider or '(NOT SET)'}")
    print(f"  LLM_API_KEY:  {api_key[:15] + '...' if api_key else '(NOT SET)'}")
    print(f"  LLM_MODEL:    {model or '(NOT SET)'}")
    print(f"  LLM_BASE_URL: {base_url or '(NOT SET)'}")
    
    print("\n" + "="*70)
    
    if not api_key:
        print("\n[X] LLM API Key is NOT configured!")
        print("\nPlease choose one of the following options:")
        print("\nOption 1: Run setup wizard (recommended)")
        print("  python setup_llm.py")
        print("\nOption 2: Set environment variables manually")
        print("  Windows CMD:")
        print("    set LLM_PROVIDER=openai")
        print("    set LLM_API_KEY=your-api-key")
        print("    set LLM_MODEL=gpt-4o")
        print("\n  Windows PowerShell:")
        print("    $env:LLM_PROVIDER='openai'")
        print("    $env:LLM_API_KEY='your-api-key'")
        print("    $env:LLM_MODEL='gpt-4o'")
        print("\nOption 3: Check the guide")
        print("  type LLM_CONFIG_GUIDE.md")
        print("\n" + "="*70)
        return False
    
    print("\n[V] LLM API Key is configured!")
    print("\nYou can:")
    print("1. Test the connection:")
    print("   python test_llm_chat.py")
    print("\n2. Start Yunshu:")
    print("   python main.py")
    print("\n" + "="*70)
    return True

if __name__ == "__main__":
    check_llm_config()
