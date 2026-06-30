#!/usr/bin/env python3
"""
基于工具描述自动提取关键词并分类的 Demo
"""

import os
import sys
import json
import re

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class ToolKeywordExtractor:
    """工具描述关键词提取器"""
    
    def __init__(self):
        # 预定义的类别关键词模板
        self.category_patterns = {
            "web": [
                r"搜索|查找|网页|网站|url|http|https|新闻|网络|查询|联网|translate|翻译|search|web|internet|fetch",
                r"新闻|资讯|热点|最新|文章|页面|链接|抓取",
            ],
            "file": [
                r"文件|读取|写入|目录|文件夹|保存|压缩|解压|zip|tar|diff",
                r"log|日志|debug|分析日志",
                r"read|write|file|directory|folder",
            ],
            "code": [
                r"执行|命令|shell|终端|cmd|powershell|bash",
                r"json|yaml|xml|格式化|校验|转换|代码|脚本",
                r"review|humanize|架构图",
            ],
            "system": [
                r"进程|启动|程序|运行|天气|温度|process|weather|stop",
                r"notepad|calc|白名单|命令行",
            ],
            "extension": [
                r"扩展|插件|安装|卸载|技能|MCP|通道|discover|configure",
            ],
            "pdf": [
                r"pdf|读取|合并|拆分|信息提取",
            ],
            "software": [
                r"软件|安装|卸载|搜索|列表|package",
            ],
            "async": [
                r"异步|后台|任务|提交|状态|结果|cancel",
            ],
            "schedule": [
                r"定时|任务|创建|暂停|恢复|取消|cron|timer",
            ],
        }
        
        # 中文停用词
        self.stop_words = set([
            "的", "是", "在", "有", "和", "了", "我", "你", "他", "她", "它",
            "这", "那", "这些", "那些", "什么", "怎么", "如何", "为什么",
            "可以", "能够", "应该", "需要", "必须", "可能", "会", "将",
            "一个", "一些", "任何", "所有", "每个", "其他", "以及", "等等",
        ])
    
    def extract_keywords(self, description: str) -> list:
        """
        从工具描述中提取关键词
        
        Args:
            description: 工具描述文本
        
        Returns:
            提取的关键词列表
        """
        keywords = []
        
        # 1. 分词 - 简单的基于标点和空格的分词
        text = description.lower()
        
        # 去除标点符号
        text = re.sub(r"[^\w\s\u4e00-\u9fff]", " ", text)
        
        # 分词
        tokens = text.split()
        
        # 2. 过滤停用词和短词
        for token in tokens:
            # 过滤短词
            if len(token) < 2:
                continue
            
            # 过滤停用词
            if token in self.stop_words:
                continue
            
            # 过滤纯数字
            if token.isdigit():
                continue
            
            keywords.append(token)
        
        # 3. 提取双词组合
        for i in range(len(tokens) - 1):
            combined = tokens[i] + tokens[i+1]
            if combined not in keywords:
                keywords.append(combined)
        
        return list(set(keywords))  # 去重
    
    def classify_by_description(self, description: str, threshold: float = 0.3) -> list:
        """
        根据工具描述自动分类
        
        Args:
            description: 工具描述文本
            threshold: 匹配阈值（匹配关键词占比）
        
        Returns:
            匹配的类别列表
        """
        keywords = self.extract_keywords(description)
        if not keywords:
            return []
        
        matched_categories = []
        
        for category, patterns in self.category_patterns.items():
            match_count = 0
            
            for pattern in patterns:
                for kw in keywords:
                    if re.search(pattern, kw):
                        match_count += 1
                        break  # 每个pattern只计数一次
            
            # 计算匹配度
            if len(keywords) > 0:
                match_ratio = match_count / len(patterns)
                if match_ratio >= threshold:
                    matched_categories.append((category, match_ratio))
        
        # 按匹配度排序
        matched_categories.sort(key=lambda x: x[1], reverse=True)
        
        return [cat for cat, ratio in matched_categories]
    
    def suggest_keywords(self, description: str) -> list:
        """
        根据描述建议关键词
        
        Args:
            description: 工具描述文本
        
        Returns:
            建议的关键词列表
        """
        keywords = self.extract_keywords(description)
        
        # 额外添加一些相关关键词
        suggestions = set(keywords)
        
        # 根据描述内容添加扩展关键词
        if re.search(r"日志|log|debug", description.lower()):
            suggestions.update(["日志", "log", "logs", "debug", "分析", "分析日志"])
        
        if re.search(r"文件|file", description.lower()):
            suggestions.update(["文件", "读取", "写入", "目录", "file", "read", "write"])
        
        if re.search(r"执行|命令|shell", description.lower()):
            suggestions.update(["执行", "命令", "shell", "终端", "cmd"])
        
        return sorted(list(suggestions))


def test_auto_classification():
    """测试自动分类功能"""
    print("=" * 80)
    print("基于工具描述自动提取关键词 Demo")
    print("=" * 80)
    
    extractor = ToolKeywordExtractor()
    
    # 测试用例
    test_tools = [
        {
            "name": "analyze_logs",
            "description": "分析日志文件，提取关键信息和错误模式",
            "expected_category": "file",
        },
        {
            "name": "search_web",
            "description": "在互联网上搜索最新信息和新闻",
            "expected_category": "web",
        },
        {
            "name": "execute_shell",
            "description": "执行shell命令并返回结果",
            "expected_category": "code",
        },
        {
            "name": "manage_process",
            "description": "管理系统进程，支持启动和停止",
            "expected_category": "system",
        },
        {
            "name": "install_extension",
            "description": "安装和配置扩展插件",
            "expected_category": "extension",
        },
        {
            "name": "parse_pdf",
            "description": "解析PDF文件，提取文本内容",
            "expected_category": "pdf",
        },
        {
            "name": "schedule_task",
            "description": "创建定时任务，支持cron表达式",
            "expected_category": "schedule",
        },
    ]
    
    print("\n测试结果:")
    print("-" * 80)
    
    for tool in test_tools:
        print(f"\n工具: {tool['name']}")
        print(f"描述: {tool['description']}")
        
        # 提取关键词
        keywords = extractor.extract_keywords(tool['description'])
        print(f"提取的关键词: {keywords}")
        
        # 建议关键词
        suggestions = extractor.suggest_keywords(tool['description'])
        print(f"建议的关键词: {suggestions}")
        
        # 自动分类
        categories = extractor.classify_by_description(tool['description'])
        print(f"自动分类: {categories}")
        print(f"预期分类: {tool['expected_category']}")
        
        # 验证结果
        if tool['expected_category'] in categories:
            print("✅ 分类正确")
        else:
            print("❌ 分类不正确")


def demo_auto_register_tool():
    """演示自动注册新工具的流程"""
    print("\n" + "=" * 80)
    print("演示：自动注册新工具流程")
    print("=" * 80)
    
    extractor = ToolKeywordExtractor()
    
    # 新工具信息
    new_tool = {
        "name": "analyze_logs",
        "description": "分析日志文件，提取关键信息和错误模式",
    }
    
    print(f"\n新工具信息:")
    print(f"  名称: {new_tool['name']}")
    print(f"  描述: {new_tool['description']}")
    
    # 1. 自动提取关键词
    keywords = extractor.extract_keywords(new_tool['description'])
    print(f"\n1. 提取关键词: {keywords}")
    
    # 2. 自动分类
    categories = extractor.classify_by_description(new_tool['description'])
    print(f"2. 自动分类: {categories}")
    
    # 3. 建议添加到的类别
    if categories:
        best_category = categories[0]
        print(f"3. 建议添加到类别: {best_category}")
        
        # 4. 建议的关键词
        suggestions = extractor.suggest_keywords(new_tool['description'])
        print(f"4. 建议添加的关键词: {suggestions}")
        
        # 5. 生成注册代码
        print(f"\n5. 生成的注册代码:")
        print(f"""
# 自动生成的工具注册代码
TOOL_CATEGORIES["{best_category}"]["tools"].append("{new_tool['name']}")

# 添加相关关键词
for kw in {suggestions}:
    add_keyword("{best_category}", kw)
""")


if __name__ == "__main__":
    test_auto_classification()
    demo_auto_register_tool()
    
    print("\n" + "=" * 80)
    print("✅ Demo 完成!")
    print("=" * 80)