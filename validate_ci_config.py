#!/usr/bin/env python3
"""CI配置验证脚本 - 检查Python版本和平台矩阵配置"""

import yaml
import os

def load_workflow(filepath):
    """加载GitHub Actions工作流配置文件"""
    if not os.path.exists(filepath):
        print(f"❌ 配置文件不存在: {filepath}")
        return None
    
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    except yaml.YAMLError as e:
        print(f"❌ YAML解析错误: {e}")
        return None

def analyze_matrix(config):
    """分析矩阵配置"""
    results = []
    jobs = config.get('jobs', {})
    
    for job_name, job_config in jobs.items():
        strategy = job_config.get('strategy', {})
        matrix = strategy.get('matrix', {})
        
        if not matrix:
            continue
        
        python_versions = matrix.get('python-version', [])
        os_list = matrix.get('os', [])
        fail_fast = strategy.get('fail-fast', True)
        
        has_py38 = '3.8' in python_versions
        has_py39 = '3.9' in python_versions
        has_py310 = '3.10' in python_versions
        has_py311 = '3.11' in python_versions
        has_py312 = '3.12' in python_versions
        has_windows = 'windows-latest' in os_list
        has_ubuntu = 'ubuntu-latest' in os_list
        
        results.append({
            'job_name': job_name,
            'python_versions': python_versions,
            'os_list': os_list,
            'fail_fast': fail_fast,
            'combinations': len(python_versions) * len(os_list),
            'coverage': {
                'py38': has_py38,
                'py39': has_py39,
                'py310': has_py310,
                'py311': has_py311,
                'py312': has_py312,
                'windows': has_windows,
                'ubuntu': has_ubuntu
            }
        })
    
    return results

def print_report(results):
    """打印验证报告"""
    print("=" * 70)
    print("          CI工作流矩阵配置验证报告")
    print("=" * 70)
    print()
    
    total_combinations = 0
    expected_combinations = 40  # 4个任务 × 5个Python版本 × 2个平台
    covered_combinations = 0
    
    for result in results:
        print(f"📋 任务: {result['job_name']}")
        print(f"   Python版本: {result['python_versions']}")
        print(f"   平台: {result['os_list']}")
        print(f"   组合数: {result['combinations']}")
        print(f"   Fail-fast: {'开启' if result['fail_fast'] else '关闭'}")
        print(f"   覆盖状态:")
        print(f"     Python 3.8: {'✅' if result['coverage']['py38'] else '❌'}")
        print(f"     Python 3.9: {'✅' if result['coverage']['py39'] else '❌'}")
        print(f"     Python 3.10: {'✅' if result['coverage']['py310'] else '❌'}")
        print(f"     Python 3.11: {'✅' if result['coverage']['py311'] else '❌'}")
        print(f"     Python 3.12: {'✅' if result['coverage']['py312'] else '❌'}")
        print(f"     Windows: {'✅' if result['coverage']['windows'] else '❌'}")
        print(f"     Ubuntu: {'✅' if result['coverage']['ubuntu'] else '❌'}")
        
        # 检查是否完整覆盖
        is_complete = all([
            result['coverage']['py38'],
            result['coverage']['py39'],
            result['coverage']['py310'],
            result['coverage']['py311'],
            result['coverage']['py312'],
            result['coverage']['windows'],
            result['coverage']['ubuntu']
        ])
        
        if is_complete:
            print(f"   ✅ 完整覆盖所有版本和平台")
            covered_combinations += result['combinations']
        else:
            print(f"   ⚠️ 存在覆盖缺口")
        
        total_combinations += result['combinations']
        print()
    
    print("=" * 70)
    print(f"📊 汇总统计")
    print("=" * 70)
    print(f"总测试任务数: {len(results)}")
    print(f"总组合数: {total_combinations}")
    print(f"预期组合数: {expected_combinations}")
    print(f"完整覆盖组合数: {covered_combinations}")
    
    if covered_combinations == expected_combinations:
        print()
        print("🎉 验证通过！CI配置已完整覆盖所有Python版本和平台")
        print("   Python版本: 3.8, 3.9, 3.10, 3.11, 3.12")
        print("   平台: Ubuntu, Windows")
        print("   总测试组合: 40个")
    else:
        print()
        print(f"⚠️ 验证不完全！")
        print(f"   已覆盖: {covered_combinations}/{expected_combinations}")
        print(f"   缺口: {expected_combinations - covered_combinations}个组合")
    
    return covered_combinations == expected_combinations

def generate_test_plan(results):
    """生成测试执行计划"""
    print()
    print("=" * 70)
    print("          测试执行计划")
    print("=" * 70)
    
    plan = []
    for result in results:
        for py_ver in result['python_versions']:
            for os_name in result['os_list']:
                plan.append({
                    'job': result['job_name'],
                    'python': py_ver,
                    'os': os_name,
                    'status': 'pending'
                })
    
    print(f"共 {len(plan)} 个测试组合:")
    print()
    
    # 按平台分组显示
    platforms = ['ubuntu-latest', 'windows-latest']
    python_versions = ['3.8', '3.9', '3.10', '3.11', '3.12']
    
    for os_name in platforms:
        os_label = "Ubuntu" if os_name == 'ubuntu-latest' else "Windows"
        print(f"🖥️ {os_label}:")
        
        for py_ver in python_versions:
            matching_jobs = [p['job'] for p in plan if p['python'] == py_ver and p['os'] == os_name]
            job_list = ", ".join(matching_jobs)
            print(f"   Python {py_ver}: {job_list}")
        
        print()
    
    return plan

if __name__ == "__main__":
    workflow_path = '.github/workflows/test.yml'
    
    print("🔍 正在加载CI工作流配置...")
    config = load_workflow(workflow_path)
    
    if not config:
        exit(1)
    
    print("🔍 正在分析矩阵配置...")
    results = analyze_matrix(config)
    
    print("📝 生成验证报告...")
    print()
    is_complete = print_report(results)
    
    print("📋 生成测试执行计划...")
    generate_test_plan(results)
    
    # 保存验证报告
    report_path = 'CI_VALIDATION_REPORT.txt'
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(f"CI配置验证报告\n")
        f.write(f"生成时间: {__import__('datetime').datetime.now()}\n")
        f.write(f"完整覆盖: {'是' if is_complete else '否'}\n")
        f.write(f"总组合数: {sum(r['combinations'] for r in results)}\n")
    
    print(f"✅ 验证报告已保存到: {report_path}")