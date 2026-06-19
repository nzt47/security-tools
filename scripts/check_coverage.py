import xml.etree.ElementTree as ET

try:
    tree = ET.parse('coverage.xml')
    root = tree.getroot()
    
    print("=" * 60)
    print("模块覆盖率汇总")
    print("=" * 60)
    
    # 获取总体覆盖率
    overall_rate = float(root.get('line-rate')) * 100
    print(f"总体覆盖率: {overall_rate:.2f}%")
    print()
    
    # 按模块分组
    modules = {}
    for cls in root.findall('.//class'):
        filename = cls.get('filename')
        if filename.startswith('agent/'):
            module_name = filename.replace('agent/', '').replace('.py', '')
            line_rate = float(cls.get('line-rate')) * 100
            
            if module_name not in modules or line_rate > modules[module_name]:
                modules[module_name] = line_rate
    
    # 按覆盖率排序输出
    print("各模块覆盖率（按覆盖率从低到高）:")
    print("-" * 60)
    for module, rate in sorted(modules.items(), key=lambda x: x[1]):
        status = "✅" if rate >= 90 else "⚠️" if rate >= 70 else "❌"
        print(f"{status} {module}: {rate:.2f}%")
    
    print()
    # 统计达标情况
    total = len(modules)
    passed = sum(1 for r in modules.values() if r >= 90)
    print(f"达标模块（≥90%）: {passed}/{total}")
    
except Exception as e:
    print(f"读取覆盖率报告失败: {e}")