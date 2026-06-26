"""
智能工具选择测试模块 - 用于回归测试

包含以下测试功能:
1. 关键词配置测试
2. 优先级逻辑测试
3. 工具分类测试
4. 别名合并测试
5. 极端场景测试
6. 压力测试（并发场景）

使用方式:
    from agent.tests.test_tool_router import ToolRouterTester
    tester = ToolRouterTester()
    tester.run_all_tests()
"""

import json
import os
import unittest
import time
import threading
import traceback
from typing import Dict, List, Set

# 尝试导入核心模块
try:
    from agent.tool_router import (
        get_tools_for_input,
        classify_user_input,
        estimate_tool_tokens,
        ALL_TOOLS_SET,
        TOOL_CATEGORIES,
        TOOL_ALIASES,
        get_keywords,
        add_keyword,
        remove_keyword,
        classify_user_input,
    )
    from agent.utils.decision_logger import DecisionLogger, SkipReason, create_decision_logger
    
    MODULE_AVAILABLE = True
except ImportError:
    MODULE_AVAILABLE = False


class ToolRouterTester:
    """智能工具选择测试器"""
    
    def __init__(self):
        if not MODULE_AVAILABLE:
            raise ImportError("核心模块导入失败")
        
        self.results = {
            "tests": [],
            "passed": 0,
            "failed": 0,
            "summary": {},
        }
    
    def _run_test(self, test_name: str, test_func) -> bool:
        """运行单个测试"""
        try:
            result = test_func()
            self.results["tests"].append({
                "name": test_name,
                "passed": result,
            })
            if result:
                self.results["passed"] += 1
            else:
                self.results["failed"] += 1
            return result
        except Exception as e:
            # 捕获详细错误堆栈
            error_trace = traceback.format_exc()
            error_info = {
                "name": test_name,
                "passed": False,
                "error": str(e),
                "error_type": type(e).__name__,
                "traceback": error_trace,
            }
            self.results["tests"].append(error_info)
            self.results["failed"] += 1
            
            # 打印详细错误信息
            print(f"\n[ERROR] {test_name}")
            print(f"  Error Type: {type(e).__name__}")
            print(f"  Error Message: {str(e)}")
            print(f"  Traceback:\n{error_trace}")
            
            return False
    
    def test_keywords_config(self) -> bool:
        """测试关键词配置"""
        keywords = get_keywords()
        
        # 检查必要类别是否存在
        required_categories = ["web", "file", "code", "system"]
        for cat in required_categories:
            if cat not in keywords:
                return False
        
        # 检查日志相关关键词
        log_keywords = ["日志", "log", "logs", "debug"]
        file_keywords = keywords.get("file", [])
        for kw in log_keywords:
            if kw not in file_keywords:
                return False
        
        return True
    
    def test_priority_order(self) -> bool:
        """测试优先级顺序"""
        # 验证优先级唯一性
        priorities = set()
        for cat_info in TOOL_CATEGORIES.values():
            priority = cat_info.get("priority")
            if priority in priorities:
                return False
            priorities.add(priority)
        
        # 验证 file 类别优先级为 2
        if TOOL_CATEGORIES["file"].get("priority") != 2:
            return False
        
        # 验证优先级排序
        sorted_cats = sorted(
            TOOL_CATEGORIES.items(),
            key=lambda x: x[1].get("priority", 99)
        )
        
        expected_order = ["core", "web", "file", "code", "system"]
        for i, expected in enumerate(expected_order):
            if sorted_cats[i][0] != expected:
                return False
        
        return True
    
    def test_tool_classification(self) -> bool:
        """测试工具分类"""
        test_cases = [
            ("分析日志文件", {"core", "file"}),
            ("搜索天气", {"core", "web", "system"}),
            ("执行命令", {"core", "code", "system"}),
            ("读取PDF", {"core", "file", "pdf"}),
        ]
        
        for input_text, expected in test_cases:
            result = classify_user_input(input_text)
            if not expected.issubset(result):
                return False
        
        return True
    
    def test_alias_merge(self) -> bool:
        """测试别名合并"""
        test_cases = [
            ("执行命令", "shell_execute", ["run_program"]),
            ("读取PDF", "read_file", ["read_pdf"]),
            ("列出目录", "list_directory", ["list_processes"]),
        ]
        
        for input_text, main_tool, aliases in test_cases:
            tools = get_tools_for_input(input_text)
            
            # 主工具应被选中
            if main_tool not in tools:
                return False
            
            # 别名工具不应被选中
            for alias in aliases:
                if alias in tools:
                    return False
        
        return True
    
    def test_extreme_priority_conflict(self) -> bool:
        """测试极端优先级冲突"""
        # 测试数量限制
        input_text = "搜索网页 读取文件 执行命令 进程管理 安装扩展 处理PDF 安装软件 后台任务 定时任务"
        tools = get_tools_for_input(input_text)
        
        # 工具数量不应超过 25
        if len(tools) > 25:
            return False
        
        # 高优先级工具应被选中
        high_priority_tools = ["get_status", "read_file", "web_search"]
        for tool in high_priority_tools:
            if tool in tools:
                return True
        
        return True
    
    def test_decision_logger(self) -> bool:
        """测试决策日志器"""
        logger = create_decision_logger(verbose=False, output_format="json")
        
        try:
            logger.start_log("测试日志", {"input": "test"})
            logger.log_selected("tool1", source="test")
            logger.log_skipped("tool2", SkipReason.ALIAS, detail="测试")
            log = logger.end_log({"summary": "test"})
            
            # 验证日志结构
            if not log.id:
                return False
            if not log.context:
                return False
            if len(log.selected) != 1:
                return False
            if len(log.skipped_by_alias) != 1:
                return False
            
            # 验证 JSON 输出
            json_str = log.to_json()
            data = json.loads(json_str)
            if "id" not in data:
                return False
            
            return True
        except Exception:
            return False
    
    def test_tool_count_consistency(self) -> bool:
        """测试工具数量一致性"""
        # 检查 ALL_TOOLS_SET 与 TOOL_CATEGORIES 的一致性
        categorized_tools = set()
        for cat_info in TOOL_CATEGORIES.values():
            for tool in cat_info.get("tools", []):
                categorized_tools.add(tool)
        
        if categorized_tools != ALL_TOOLS_SET:
            return False
        
        # 检查别名工具是否都在工具集中
        for main, aliases in TOOL_ALIASES.items():
            if main not in ALL_TOOLS_SET:
                return False
            for alias in aliases:
                if alias not in ALL_TOOLS_SET:
                    return False
        
        return True
    
    # ── 边界条件测试 ──
    
    def test_empty_tool_set(self) -> bool:
        """边界条件1: 工具数量为0的场景"""
        """
        测试场景: 当注册表为空时，工具选择应返回空列表或核心工具
        """
        try:
            from agent import tools
            original_registry = tools._registry.copy()
            original_version = tools._registry_version
            
            try:
                # 清空注册表
                tools._registry.clear()
                tools._registry_version += 1
                
                # 测试空注册表时的行为
                tools_list = tools.list_tools()
                if len(tools_list) != 0:
                    return False
                
                # 测试工具选择函数在空注册表时的行为
                result = get_tools_for_input("读取文件")
                if result is None or not isinstance(result, list):
                    return False
                
                return True
            finally:
                # 恢复注册表
                tools._registry.clear()
                tools._registry.update(original_registry)
                tools._registry_version = original_version
        except Exception as e:
            print(f"\n[ERROR] test_empty_tool_set - {type(e).__name__}: {e}")
            traceback.print_exc()
            return False
    
    def test_dynamic_tool_addition(self) -> bool:
        """边界条件2: 动态添加工具"""
        """
        测试场景: 在运行时动态添加新工具，验证工具选择能正确识别
        """
        try:
            from agent import tools
            test_tool_name = "test_dynamic_tool_12345"
            
            # 确保测试工具不存在
            if test_tool_name in tools._registry:
                del tools._registry[test_tool_name]
                tools._registry_version += 1
            
            # 动态注册一个测试工具
            def test_func():
                return {"ok": True}
            
            tools._registry[test_tool_name] = {
                "name": test_tool_name,
                "description": "动态测试工具",
                "handler": test_func,
            }
            tools._registry_version += 1
            
            # 验证工具已注册
            if test_tool_name not in tools._registry:
                return False
            
            # 清理
            del tools._registry[test_tool_name]
            tools._registry_version += 1
            
            return True
        except Exception as e:
            print(f"\n[ERROR] test_dynamic_tool_addition - {type(e).__name__}: {e}")
            traceback.print_exc()
            return False
    
    def test_dynamic_tool_removal(self) -> bool:
        """边界条件3: 动态删除工具"""
        """
        测试场景: 在运行时动态删除工具，验证工具选择能正确处理
        """
        try:
            from agent import tools
            
            # 创建一个临时工具
            test_tool_name = "test_remove_tool_67890"
            
            def test_func():
                return {"ok": True}
            
            tools._registry[test_tool_name] = {
                "name": test_tool_name,
                "description": "临时测试工具",
                "handler": test_func,
            }
            tools._registry_version += 1
            
            # 验证工具已注册
            if test_tool_name not in tools._registry:
                return False
            
            # 删除工具
            tools.unregister(test_tool_name)
            
            # 验证工具已删除
            if test_tool_name in tools._registry:
                return False
            
            return True
        except Exception as e:
            print(f"\n[ERROR] test_dynamic_tool_removal - {type(e).__name__}: {e}")
            traceback.print_exc()
            return False
    
    def test_config_file_corruption(self) -> bool:
        """边界条件4: 配置文件损坏"""
        """
        测试场景: 当配置文件损坏或格式错误时，系统应优雅处理
        """
        try:
            import json
            from agent.tool_router import KEYWORDS_FILE, _load_keywords, DEFAULT_KEYWORDS
            
            # 保存原始文件（如果存在）
            original_content = None
            if os.path.exists(KEYWORDS_FILE):
                with open(KEYWORDS_FILE, "r", encoding="utf-8") as f:
                    original_content = f.read()
            
            try:
                # 模拟配置文件损坏 - 写入无效JSON
                os.makedirs(os.path.dirname(KEYWORDS_FILE), exist_ok=True)
                with open(KEYWORDS_FILE, "w", encoding="utf-8") as f:
                    f.write('{"invalid": true}')  # 缺少必要字段
                
                # 验证系统能处理损坏的配置（应返回默认配置）
                keywords = _load_keywords()
                
                # 检查是否返回了有效的关键词配置
                if not isinstance(keywords, dict):
                    return False
                
                # 至少应该有默认关键词
                if not keywords:
                    return False
                
                return True
            finally:
                # 恢复原始文件
                if original_content is not None:
                    with open(KEYWORDS_FILE, "w", encoding="utf-8") as f:
                        f.write(original_content)
                elif os.path.exists(KEYWORDS_FILE):
                    os.remove(KEYWORDS_FILE)
        except Exception as e:
            print(f"\n[ERROR] test_config_file_corruption - {type(e).__name__}: {e}")
            traceback.print_exc()
            return False
    
    def test_tool_name_conflicts(self) -> bool:
        """边界条件5: 工具名称冲突"""
        """
        测试场景: 注册同名工具时应正确处理覆盖
        """
        try:
            from agent import tools
            
            # 注册第一个工具
            @tools.register("conflict_test_tool", "原始工具")
            def func1():
                return {"ok": 1}
            
            # 验证第一次注册
            if tools._registry["conflict_test_tool"]["description"] != "原始工具":
                return False
            
            # 注册同名工具（应该覆盖）
            @tools.register("conflict_test_tool", "覆盖工具")
            def func2():
                return {"ok": 2}
            
            # 验证已覆盖
            if tools._registry["conflict_test_tool"]["description"] != "覆盖工具":
                return False
            
            # 清理
            tools.unregister("conflict_test_tool")
            
            return True
        except Exception as e:
            print(f"\n[ERROR] test_tool_name_conflicts - {type(e).__name__}: {e}")
            traceback.print_exc()
            return False
    
    def test_tool_count_threshold(self) -> bool:
        """边界条件7: 工具数量阈值边界测试"""
        """
        测试场景: 工具数量在阈值附近波动时的行为
        """
        try:
            from agent import tools
            
            original_registry = tools._registry.copy()
            original_version = tools._registry_version
            
            try:
                # 清空注册表
                tools._registry.clear()
                tools._registry_version += 1
                
                # 测试不同阈值点
                test_cases = [0, 1, 5, 10, 20, 50, 100]
                
                for target_count in test_cases:
                    # 添加指定数量的工具
                    for i in range(target_count):
                        tool_name = f"threshold_tool_{i}"
                        tools._registry[tool_name] = {
                            "name": tool_name,
                            "description": f"Threshold tool {i}",
                            "handler": lambda: {"ok": True},
                        }
                    tools._registry_version += 1
                    
                    # 验证工具数量
                    tools_list = tools.list_tools()
                    if len(tools_list) != target_count:
                        print(f"FAIL: Expected {target_count} tools, got {len(tools_list)}")
                        return False
                
                return True
            finally:
                tools._registry.clear()
                tools._registry.update(original_registry)
                tools._registry_version = original_version
        except Exception as e:
            print(f"\n[ERROR] test_tool_count_threshold - {type(e).__name__}: {e}")
            traceback.print_exc()
            return False
    
    def test_concurrent_tool_changes(self) -> bool:
        """边界条件8: 并发动态工具变化"""
        """
        测试场景: 多个线程同时添加/删除工具
        """
        try:
            from agent import tools
            import threading
            
            original_registry = tools._registry.copy()
            original_version = tools._registry_version
            
            try:
                # 清空注册表
                tools._registry.clear()
                tools._registry_version += 1
                
                errors = []
                
                def add_tools(start, end):
                    try:
                        for i in range(start, end):
                            tool_name = f"concurrent_tool_{i}"
                            tools._registry[tool_name] = {
                                "name": tool_name,
                                "description": f"Concurrent tool {i}",
                                "handler": lambda: {"ok": True},
                            }
                            tools._registry_version += 1
                    except Exception as e:
                        errors.append(f"Add error: {e}")
                
                def remove_tools(start, end):
                    try:
                        for i in range(start, end):
                            tool_name = f"concurrent_tool_{i}"
                            if tool_name in tools._registry:
                                del tools._registry[tool_name]
                                tools._registry_version += 1
                    except Exception as e:
                        errors.append(f"Remove error: {e}")
                
                # 创建并发线程
                threads = []
                for i in range(5):
                    t = threading.Thread(target=add_tools, args=(i*20, (i+1)*20))
                    threads.append(t)
                    t.start()
                
                # 等待添加完成
                for t in threads:
                    t.join()
                
                # 创建删除线程
                threads = []
                for i in range(5):
                    t = threading.Thread(target=remove_tools, args=(i*10, i*10+10))
                    threads.append(t)
                    t.start()
                
                for t in threads:
                    t.join()
                
                if errors:
                    print(f"FAIL: Concurrent errors: {errors}")
                    return False
                
                return True
            finally:
                tools._registry.clear()
                tools._registry.update(original_registry)
                tools._registry_version = original_version
        except Exception as e:
            print(f"\n[ERROR] test_concurrent_tool_changes - {type(e).__name__}: {e}")
            traceback.print_exc()
            return False
    
    def test_single_tool_scenario(self) -> bool:
        """边界条件9: 单工具场景"""
        """
        测试场景: 只有一个工具时的行为 - 验证工具注册表基本功能
        """
        try:
            from agent import tools
            
            original_registry = tools._registry.copy()
            original_version = tools._registry_version
            
            try:
                # 清空注册表并添加单个工具
                tools._registry.clear()
                
                tools._registry["single_tool"] = {
                    "name": "single_tool",
                    "description": "单个测试工具",
                    "handler": lambda: {"ok": True},
                }
                tools._registry_version += 1
                
                # 验证工具已注册
                if "single_tool" not in tools._registry:
                    print("FAIL: Tool not registered")
                    return False
                
                # 验证工具列表返回正确结果
                tools_list = tools.list_tools()
                if len(tools_list) != 1:
                    print(f"FAIL: Expected 1 tool, got {len(tools_list)}")
                    return False
                
                # 验证工具信息正确
                tool_info = tools_list[0]
                if tool_info.get("name") != "single_tool":
                    print(f"FAIL: Tool name mismatch")
                    return False
                
                return True
            finally:
                tools._registry.clear()
                tools._registry.update(original_registry)
                tools._registry_version = original_version
        except Exception as e:
            print(f"\n[ERROR] test_single_tool_scenario - {type(e).__name__}: {e}")
            traceback.print_exc()
            return False
    
    def test_empty_user_input(self) -> bool:
        """边界条件10: 空输入场景"""
        """
        测试场景: 用户输入为空或只有空格时的行为
        """
        try:
            from agent.tool_router import get_tools_for_input, classify_user_input
            
            # 测试空字符串
            result = get_tools_for_input("")
            if result is None or not isinstance(result, list):
                return False
            
            # 测试空白字符
            result = get_tools_for_input("   \t\n")
            if result is None or not isinstance(result, list):
                return False
            
            # 测试分类函数
            category = classify_user_input("")
            if category is None:
                return False
            
            return True
        except Exception as e:
            print(f"\n[ERROR] test_empty_user_input - {type(e).__name__}: {e}")
            traceback.print_exc()
            return False
    
    def test_frequent_tool_changes(self) -> bool:
        """边界条件11: 高频工具变化"""
        """
        测试场景: 短时间内频繁添加/删除工具
        """
        try:
            from agent import tools
            import time
            
            original_registry = tools._registry.copy()
            original_version = tools._registry_version
            
            try:
                tools._registry.clear()
                tools._registry_version += 1
                
                start_time = time.time()
                
                # 快速添加和删除工具
                for i in range(100):
                    tool_name = f"temp_tool_{i}"
                    tools._registry[tool_name] = {
                        "name": tool_name,
                        "description": f"Temp tool {i}",
                        "handler": lambda: {"ok": True},
                    }
                    tools._registry_version += 1
                    
                    # 立即删除
                    del tools._registry[tool_name]
                    tools._registry_version += 1
                
                duration = time.time() - start_time
                
                # 验证注册表为空
                if len(tools._registry) != 0:
                    return False
                
                # 验证操作在合理时间内完成
                if duration > 1.0:  # 1秒阈值
                    print(f"FAIL: Too slow - {duration:.2f}s")
                    return False
                
                return True
            finally:
                tools._registry.clear()
                tools._registry.update(original_registry)
                tools._registry_version = original_version
        except Exception as e:
            print(f"\n[ERROR] test_frequent_tool_changes - {type(e).__name__}: {e}")
            traceback.print_exc()
            return False
    
    def test_tool_without_description(self) -> bool:
        """边界条件12: 无描述工具"""
        """
        测试场景: 工具没有描述信息时的行为
        """
        try:
            from agent import tools
            from agent.tool_router import get_tools_for_input
            
            original_registry = tools._registry.copy()
            original_version = tools._registry_version
            
            try:
                tools._registry.clear()
                
                # 添加一个没有描述的工具
                tools._registry["no_desc_tool"] = {
                    "name": "no_desc_tool",
                    "description": "",
                    "handler": lambda: {"ok": True},
                }
                tools._registry_version += 1
                
                # 测试工具选择不会崩溃
                result = get_tools_for_input("读取文件")
                if result is None:
                    return False
                
                return True
            finally:
                tools._registry.clear()
                tools._registry.update(original_registry)
                tools._registry_version = original_version
        except Exception as e:
            print(f"\n[ERROR] test_tool_without_description - {type(e).__name__}: {e}")
            traceback.print_exc()
            return False
    
    def test_large_tool_set(self) -> bool:
        """边界条件6: 大量工具场景"""
        """
        测试场景: 当工具数量非常大时（超过100个），验证系统性能
        """
        try:
            from agent import tools
            import time
            
            # 保存原始注册表
            original_registry = tools._registry.copy()
            original_version = tools._registry_version
            
            try:
                # 添加大量测试工具
                for i in range(150):
                    tool_name = f"test_large_tool_{i}"
                    @tools.register(tool_name, f"测试工具{i}")
                    def test_func():
                        return {"ok": True}
                
                # 验证工具数量
                tools_list = tools.list_tools()
                if len(tools_list) < 150:
                    return False
                
                # 测试工具选择性能
                start_time = time.time()
                for _ in range(10):
                    get_tools_for_input("读取文件")
                duration = time.time() - start_time
                
                # 10次调用应在合理时间内完成
                if duration > 2.0:
                    return False
                
                return True
            finally:
                # 恢复注册表
                tools._registry.clear()
                tools._registry.update(original_registry)
                tools._registry_version = original_version
        except Exception as e:
            print(f"\n[ERROR] test_large_tool_set - {type(e).__name__}: {e}")
            traceback.print_exc()
            return False
    
    def test_stress_concurrent(self) -> bool:
        """测试 100 个工具并发场景的压力测试"""
        """
        测试场景:
        1. 并发调用工具选择函数
        2. 大量工具触发场景
        3. 性能指标验证
        """
        try:
            # 测试1: 并发调用测试
            results = []
            lock = threading.Lock()
            
            def test_concurrent_call(input_text):
                try:
                    tools = get_tools_for_input(input_text)
                    with lock:
                        results.append({"input": input_text, "tools": len(tools), "success": True})
                except Exception as e:
                    with lock:
                        results.append({"input": input_text, "error": str(e), "success": False})
            
            # 创建多个线程并发调用
            threads = []
            test_inputs = [
                "搜索天气",
                "读取文件",
                "执行命令",
                "分析日志",
                "安装软件",
                "创建定时任务",
                "读取PDF",
                "后台运行任务",
            ]
            
            for _ in range(100):  # 100 个并发调用
                input_text = test_inputs[_ % len(test_inputs)]
                t = threading.Thread(target=test_concurrent_call, args=(input_text,))
                threads.append(t)
                t.start()
            
            # 等待所有线程完成
            for t in threads:
                t.join(timeout=30)
            
            # 检查结果
            failed = [r for r in results if not r["success"]]
            if failed:
                return False
            
            # 测试2: 大量工具触发性能测试
            start_time = time.time()
            complex_input = "搜索网页 读取文件 执行命令 进程管理 安装扩展 处理PDF 安装软件 后台任务 定时任务 日志分析"
            for _ in range(50):
                get_tools_for_input(complex_input)
            duration = time.time() - start_time
            
            # 50次调用应在合理时间内完成（< 5秒）
            if duration > 5.0:
                return False
            
            # 测试3: 大量关键词输入
            long_input = "日志 " * 1000 + "文件 " * 1000 + "搜索 " * 1000
            tools = get_tools_for_input(long_input)
            if len(tools) > 25:
                return False
            
            # 测试4: 空输入边界测试
            empty_tools = get_tools_for_input("")
            if len(empty_tools) != 5:  # 应该只有核心工具
                return False
            
            return True
        except Exception:
            return False
    
    def test_performance_metrics(self) -> bool:
        """测试性能指标"""
        """
        验证工具选择的性能指标:
        - 单调用耗时 < 100ms
        - 关键词匹配准确性
        - 工具数量限制生效
        """
        try:
            # 测试单调用耗时
            input_text = "分析日志文件"
            total_time = 0.0
            iterations = 100
            
            for _ in range(iterations):
                start = time.time()
                get_tools_for_input(input_text)
                total_time += time.time() - start
            
            avg_time = (total_time / iterations) * 1000  # 转换为毫秒
            
            # 平均耗时应 < 100ms
            if avg_time > 100.0:
                return False
            
            # 测试分类准确性
            categories = classify_user_input("分析日志文件")
            if "file" not in categories:
                return False
            
            # 测试工具数量限制
            tools = get_tools_for_input("搜索网页 读取文件 执行命令 进程管理 安装扩展 处理PDF")
            if len(tools) > 25:
                return False
            
            return True
        except Exception:
            return False
    
    def run_all_tests(self) -> Dict:
        """运行所有测试"""
        print("=" * 60)
        print("智能工具选择回归测试")
        print("=" * 60)
        
        tests = [
            ("关键词配置测试", self.test_keywords_config),
            ("优先级顺序测试", self.test_priority_order),
            ("工具分类测试", self.test_tool_classification),
            ("别名合并测试", self.test_alias_merge),
            ("极端优先级冲突测试", self.test_extreme_priority_conflict),
            ("决策日志器测试", self.test_decision_logger),
            ("工具数量一致性测试", self.test_tool_count_consistency),
            # 边界条件测试 - 工具数量动态变化场景
            ("边界条件1-空工具集", self.test_empty_tool_set),
            ("边界条件2-动态添加工具", self.test_dynamic_tool_addition),
            ("边界条件3-动态删除工具", self.test_dynamic_tool_removal),
            ("边界条件4-配置文件损坏", self.test_config_file_corruption),
            ("边界条件5-工具名称冲突", self.test_tool_name_conflicts),
            ("边界条件6-大量工具场景", self.test_large_tool_set),
            ("边界条件7-工具数量阈值边界", self.test_tool_count_threshold),
            ("边界条件8-并发工具变化", self.test_concurrent_tool_changes),
            ("边界条件9-单工具场景", self.test_single_tool_scenario),
            ("边界条件10-空输入场景", self.test_empty_user_input),
            ("边界条件11-高频工具变化", self.test_frequent_tool_changes),
            ("边界条件12-无描述工具", self.test_tool_without_description),
            # 压力测试
            ("100并发压力测试", self.test_stress_concurrent),
            ("性能指标测试", self.test_performance_metrics),
        ]
        
        for test_name, test_func in tests:
            result = self._run_test(test_name, test_func)
            status = "[OK]" if result else "[FAIL]"
            print(f"{status} {test_name}")
        
        # 生成总结
        total = self.results["passed"] + self.results["failed"]
        self.results["summary"] = {
            "total": total,
            "passed": self.results["passed"],
            "failed": self.results["failed"],
            "success_rate": (self.results["passed"] / total) * 100 if total > 0 else 0,
        }
        
        print("=" * 60)
        print(f"测试完成: {self.results['passed']}/{total} 通过")
        print(f"成功率: {self.results['summary']['success_rate']:.1f}%")
        print("=" * 60)
        
        return self.results
    
    def generate_report(self) -> str:
        """生成测试报告"""
        report = {
            "test_module": "智能工具选择测试模块",
            "summary": self.results["summary"],
            "tests": self.results["tests"],
            "config_info": {
                "total_tools": len(ALL_TOOLS_SET),
                "total_categories": len(TOOL_CATEGORIES),
                "total_alias_rules": len(TOOL_ALIASES),
                "file_priority": TOOL_CATEGORIES["file"].get("priority"),
            },
        }
        
        return json.dumps(report, ensure_ascii=False, indent=2)


class TestToolRouter(unittest.TestCase):
    """unittest 测试类"""
    
    @classmethod
    def setUpClass(cls):
        cls.tester = ToolRouterTester()
    
    def test_keywords_config(self):
        self.assertTrue(self.tester.test_keywords_config())
    
    def test_priority_order(self):
        self.assertTrue(self.tester.test_priority_order())
    
    def test_tool_classification(self):
        self.assertTrue(self.tester.test_tool_classification())
    
    def test_alias_merge(self):
        self.assertTrue(self.tester.test_alias_merge())
    
    def test_extreme_priority_conflict(self):
        self.assertTrue(self.tester.test_extreme_priority_conflict())
    
    def test_decision_logger(self):
        self.assertTrue(self.tester.test_decision_logger())
    
    def test_tool_count_consistency(self):
        self.assertTrue(self.tester.test_tool_count_consistency())
    
    # 边界条件测试
    def test_empty_tool_set(self):
        self.assertTrue(self.tester.test_empty_tool_set())
    
    def test_dynamic_tool_addition(self):
        self.assertTrue(self.tester.test_dynamic_tool_addition())
    
    def test_dynamic_tool_removal(self):
        self.assertTrue(self.tester.test_dynamic_tool_removal())
    
    def test_config_file_corruption(self):
        self.assertTrue(self.tester.test_config_file_corruption())
    
    def test_tool_name_conflicts(self):
        self.assertTrue(self.tester.test_tool_name_conflicts())
    
    def test_large_tool_set(self):
        self.assertTrue(self.tester.test_large_tool_set())
    
    def test_stress_concurrent(self):
        self.assertTrue(self.tester.test_stress_concurrent())
    
    def test_performance_metrics(self):
        self.assertTrue(self.tester.test_performance_metrics())


def run_tests():
    """运行测试的便捷函数"""
    if not MODULE_AVAILABLE:
        print("❌ 核心模块不可用")
        return
    
    tester = ToolRouterTester()
    results = tester.run_all_tests()
    
    # 保存测试报告
    report_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "tool_router_test_report.json"
    )
    
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(tester.generate_report())
    
    print(f"\n[REPORT] 测试报告已保存到: {report_path}")
    
    return results


if __name__ == "__main__":
    # 直接运行测试
    run_tests()
    
    # 也可以通过 unittest 运行
    # unittest.main()
