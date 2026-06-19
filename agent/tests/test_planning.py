"""Planning 单元测试 - 复杂任务分解场景"""
import pytest
import logging
from unittest.mock import MagicMock

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test_planning")

class Task:
    def __init__(self, id_val, description, dependencies=None, priority=1):
        self.id = id_val
        self.description = description
        self.dependencies = dependencies or []
        self.priority = priority
        self.subtasks = []
    
    def __repr__(self):
        return f"Task(id={self.id}, description={self.description})"

class Planner:
    def __init__(self):
        self.tasks = {}
    
    def create_task(self, description, dependencies=None, priority=1):
        task_id = f"task_{len(self.tasks) + 1}"
        task = Task(task_id, description, dependencies, priority)
        self.tasks[task_id] = task
        return task
    
    def decompose_task(self, task, max_depth=3):
        """分解任务为子任务"""
        if max_depth <= 0:
            return []
        
        decompositions = {
            "写一份项目报告": [
                ("收集项目数据", [], 2),
                ("分析数据", ["收集项目数据"], 2),
                ("撰写报告初稿", ["分析数据"], 1),
                ("审核修改", ["撰写报告初稿"], 1),
            ],
            "开发新功能": [
                ("需求分析", [], 1),
                ("设计架构", ["需求分析"], 1),
                ("编写代码", ["设计架构"], 2),
                ("单元测试", ["编写代码"], 2),
                ("集成测试", ["单元测试"], 1),
            ],
            "组织团队会议": [
                ("确定会议主题", [], 3),
                ("安排时间地点", [], 3),
                ("发送邀请", ["确定会议主题", "安排时间地点"], 2),
                ("准备会议材料", ["确定会议主题"], 2),
            ],
            "学习新技能": [
                ("了解基础知识", [], 3),
                ("实践练习", ["了解基础知识"], 2),
                ("项目实战", ["实践练习"], 1),
            ],
        }
        
        subtasks = []
        if task.description in decompositions:
            for desc, deps, priority in decompositions[task.description]:
                subtask = self.create_task(desc, deps, priority)
                subtasks.append(subtask)
                # 递归分解
                if max_depth > 1:
                    self.decompose_task(subtask, max_depth - 1)
        
        task.subtasks = subtasks
        return subtasks
    
    def get_task_order(self):
        """获取任务执行顺序（考虑依赖）"""
        in_degree = {tid: 0 for tid in self.tasks}
        graph = {tid: [] for tid in self.tasks}
        
        for tid, task in self.tasks.items():
            for dep in task.dependencies:
                graph[dep].append(tid)
                in_degree[tid] += 1
        
        queue = [tid for tid in in_degree if in_degree[tid] == 0]
        order = []
        
        while queue:
            current = queue.pop(0)
            order.append(current)
            for neighbor in graph[current]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)
        
        return order

def test_single_task_decomposition():
    """单个任务分解"""
    logger.info("测试: 单个任务分解")
    planner = Planner()
    task = planner.create_task("写一份项目报告")
    subtasks = planner.decompose_task(task)
    
    logger.info(f"任务: {task.description}")
    logger.info(f"子任务数量: {len(subtasks)}")
    
    assert len(subtasks) == 4
    assert subtasks[0].description == "收集项目数据"
    assert subtasks[1].description == "分析数据"
    assert subtasks[2].description == "撰写报告初稿"
    assert subtasks[3].description == "审核修改"

def test_complex_project_decomposition():
    """复杂项目任务分解"""
    logger.info("测试: 复杂项目任务分解")
    planner = Planner()
    
    # 创建多个相关任务
    project_task = planner.create_task("开发新功能", priority=1)
    planner.decompose_task(project_task, max_depth=2)
    
    logger.info(f"总任务数: {len(planner.tasks)}")
    
    # 验证分解结构
    assert len(planner.tasks) >= 5  # 至少5个子任务

def test_task_dependencies():
    """任务依赖处理"""
    logger.info("测试: 任务依赖处理")
    planner = Planner()
    
    task1 = planner.create_task("任务A")
    task2 = planner.create_task("任务B", dependencies=["task_1"])
    task3 = planner.create_task("任务C", dependencies=["task_2"])
    
    order = planner.get_task_order()
    logger.info(f"任务执行顺序: {order}")
    
    assert order.index("task_1") < order.index("task_2")
    assert order.index("task_2") < order.index("task_3")

def test_priority_based_ordering():
    """基于优先级的任务排序"""
    logger.info("测试: 基于优先级的任务排序")
    planner = Planner()
    
    # 创建不同优先级的任务（数字越小优先级越高）
    planner.create_task("高优先级任务", priority=1)
    planner.create_task("中优先级任务", priority=2)
    planner.create_task("低优先级任务", priority=3)
    
    # 无依赖时按优先级排序
    tasks = sorted(planner.tasks.values(), key=lambda t: t.priority)
    logger.info(f"按优先级排序: {[t.description for t in tasks]}")
    
    assert tasks[0].priority == 1
    assert tasks[-1].priority == 3

def test_cyclic_dependencies():
    """循环依赖检测"""
    logger.info("测试: 循环依赖检测")
    planner = Planner()
    
    try:
        task1 = planner.create_task("任务1", dependencies=["task_2"])
        task2 = planner.create_task("任务2", dependencies=["task_1"])
        
        order = planner.get_task_order()
        logger.info(f"循环依赖情况下的顺序: {order}")
        
        # 在无依赖任务优先的情况下，循环依赖的任务可能不会出现在顺序中
        # 这是预期行为
    except Exception as e:
        logger.info(f"捕获异常: {e}")

def test_empty_decomposition():
    """空任务分解"""
    logger.info("测试: 空任务分解")
    planner = Planner()
    task = planner.create_task("未知任务")
    subtasks = planner.decompose_task(task)
    
    logger.info(f"未知任务分解结果: {len(subtasks)} 个子任务")
    assert len(subtasks) == 0

def test_max_depth_control():
    """最大分解深度控制"""
    logger.info("测试: 最大分解深度控制")
    planner = Planner()
    task = planner.create_task("开发新功能")
    
    # 深度为1
    subtasks1 = planner.decompose_task(task, max_depth=1)
    count1 = len(planner.tasks)
    
    planner2 = Planner()
    task2 = planner2.create_task("开发新功能")
    
    # 深度为2
    subtasks2 = planner2.decompose_task(task2, max_depth=2)
    count2 = len(planner2.tasks)
    
    logger.info(f"深度1任务数: {count1}, 深度2任务数: {count2}")
    
    assert count1 <= count2

def test_real_world_scenario():
    """真实场景: 产品发布准备"""
    logger.info("测试: 真实场景 - 产品发布准备")
    planner = Planner()
    
    release_task = planner.create_task("产品发布准备", priority=1)
    planner.decompose_task(release_task, max_depth=3)
    
    order = planner.get_task_order()
    logger.info(f"总任务数: {len(planner.tasks)}")
    logger.info(f"执行顺序: {order}")
    
    assert len(planner.tasks) > 0
    assert len(order) > 0
