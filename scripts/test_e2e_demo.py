"""端到端测试演示脚本"""
import asyncio
import tempfile
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)8s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

from planning.core import PlanningCore

class FileSystem:
    """模拟文件系统"""
    def __init__(self):
        self.files = {}
    
    def create_file(self, *args, **kwargs):
        filename = kwargs.get('filename', args[0] if args else 'unknown')
        content = kwargs.get('content', args[1] if len(args) > 1 else '')
        self.files[filename] = content
        print(f"   [工具调用] create_file(filename='{filename}', content='{content}') -> 成功")
        return f"文件 {filename} 创建成功"
    
    def write_file(self, *args, **kwargs):
        filename = kwargs.get('filename', args[0] if args else 'unknown')
        content = kwargs.get('content', args[1] if len(args) > 1 else '')
        if filename in self.files:
            self.files[filename] += content
        else:
            self.files[filename] = content
        print(f"   [工具调用] write_file(filename='{filename}', content='{content[:30]}...') -> 成功")
        return f"已写入内容到 {filename}"
    
    def read_file(self, *args, **kwargs):
        filename = kwargs.get('filename', args[0] if args else 'unknown')
        content = self.files.get(filename, "文件不存在")
        print(f"   [工具调用] read_file(filename='{filename}') -> '{content[:30]}...'")
        return content

class SearchService:
    """模拟搜索服务"""
    def search(self, *args, **kwargs):
        query = kwargs.get('query', args[0] if args else 'unknown')
        print(f"   [工具调用] search(query='{query}') -> 成功")
        return f"搜索结果: {query} 的相关信息"

class EmailService:
    """模拟邮件服务"""
    def send_email(self, *args, **kwargs):
        to = kwargs.get('to', args[0] if args else 'unknown')
        subject = kwargs.get('subject', args[1] if len(args) > 1 else 'unknown')
        body = kwargs.get('body', args[2] if len(args) > 2 else '')
        print(f"   [工具调用] send_email(to='{to}', subject='{subject}', body='{body[:30]}...') -> 成功")
        return f"邮件已发送到 {to}"

async def main():
    print("="*80)
    print("🎯 端到端测试演示 - 复杂工作流场景")
    print("="*80)
    
    with tempfile.TemporaryDirectory() as tmp_dir:
        core = PlanningCore(config={
            "reflector": {"persist_dir": tmp_dir},
            "decomposer": {"max_subtasks": 10},
            "executor": {"max_retries": 2},
            "react": {"max_iterations": 10}
        })
        
        fs = FileSystem()
        search = SearchService()
        email = EmailService()
        
        core.register_tool("create_file", fs.create_file)
        core.register_tool("write_file", fs.write_file)
        core.register_tool("read_file", fs.read_file)
        core.register_tool("search", search.search)
        core.register_tool("send_email", email.send_email)
        
        task = "首先使用create_file创建一个名为 report.txt 的文件，然后使用search搜索关于销售数据的信息，接着使用write_file将搜索结果写入 report.txt 文件，最后使用send_email发送邮件通知管理员"
        
        print("\n📋 步骤1: 创建执行计划")
        print("-"*60)
        plan = await core.plan(task)
        
        print(f"\n✅ 计划创建成功!")
        print(f"   计划ID: {plan.id}")
        print(f"   计划状态: {plan.state}")
        print(f"   子任务数量: {len(plan.tasks)}")
        for i, t in enumerate(plan.tasks):
            print(f"   任务{i+1}: [{t.id}] {t.description} (依赖: {t.dependencies})")
        
        print("\n🚀 步骤2: 执行计划")
        print("-"*60)
        executed_plan = await core.execute_plan(plan)
        
        print(f"\n✅ 计划执行完成!")
        print(f"   计划状态: {executed_plan.state}")
        print(f"   是否成功: {executed_plan.is_success()}")
        for i, t in enumerate(executed_plan.tasks):
            print(f"   任务{i+1}: [{t.id}] {t.description} - 状态: {t.status}")
        
        print("\n📊 步骤3: 验证结果")
        print("-"*60)
        if "report.txt" in fs.files:
            print(f"   ✅ 文件 report.txt 已创建")
            print(f"   内容: {fs.files['report.txt']}")
        else:
            print(f"   ❌ 文件 report.txt 未创建")
        
        print("\n" + "="*80)
        print("🎉 端到端测试演示完成!")
        print("="*80)

if __name__ == "__main__":
    asyncio.run(main())