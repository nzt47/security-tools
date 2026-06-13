#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
验证 Prometheus 和 Grafana 配置
"""

import requests
import json
import sys
from pathlib import Path

BASE_URL = "http://127.0.0.1:5678"

def check_docker():
    """检查 Docker 状态"""
    import subprocess
    try:
        result = subprocess.run(['docker', '--version'], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            print("✅ Docker 已安装")
            return True
        else:
            print("❌ Docker 未正确安装")
            return False
    except Exception as e:
        print(f"❌ Docker 检查失败：{e}")
        return False

def verify_grafana_config():
    """验证 Grafana 配置文件"""
    print("\n" + "="*70)
    print("📁 验证 Grafana 配置文件")
    print("="*70)
    
    files_to_check = [
        "docker-compose.monitoring.yml",
        "monitoring/prometheus.yml",
        "monitoring/grafana/datasources/prometheus.yml",
        "monitoring/grafana/dashboards/dashboard.yml",
        "monitoring/grafana/dashboards/yunshu-monitor.json",
    ]
    
    all_exist = True
    for file_path in files_to_check:
        if Path(file_path).exists():
            print(f"✅ {file_path}")
        else:
            print(f"❌ {file_path} 不存在")
            all_exist = False
    
    # 验证 JSON 格式
    print("\n验证 JSON 文件格式:")
    json_files = [
        "monitoring/grafana/dashboards/yunshu-monitor.json"
    ]
    
    for json_file in json_files:
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            print(f"✅ {json_file} - 格式正确")
            
            # 检查仪表盘基本结构
            if 'title' in data:
                print(f"   仪表盘名称：{data['title']}")
            if 'panels' in data:
                print(f"   面板数量：{len(data['panels'])}")
        except Exception as e:
            print(f"❌ {json_file} - 格式错误：{e}")
            return False
    
    return all_exist

def verify_prometheus_config():
    """验证 Prometheus 配置"""
    print("\n" + "="*70)
    print("📁 验证 Prometheus 配置")
    print("="*70)
    
    prometheus_config = "monitoring/prometheus.yml"
    
    try:
        with open(prometheus_config, 'r', encoding='utf-8') as f:
            content = f.read()
        
        print(f"✅ {prometheus_config}")
        print("\n配置内容:")
        print("-" * 70)
        print(content)
        print("-" * 70)
        
        # 检查关键配置
        if 'yunshu' in content:
            print("\n✅ Yunshu job 已配置")
        if 'scrape_interval' in content:
            print("✅ 抓取间隔已配置")
        if 'metrics_path' in content:
            print("✅ 指标路径已配置")
            
        return True
        
    except Exception as e:
        print(f"❌ 读取配置失败：{e}")
        return False

def verify_yunshu_metrics():
    """验证 Yunshu 指标端点"""
    print("\n" + "="*70)
    print("📊 验证 Yunshu 指标端点")
    print("="*70)
    
    try:
        resp = requests.get(f"{BASE_URL}/metrics", timeout=5)
        
        if resp.status_code == 200:
            print(f"✅ /metrics 端点正常 (状态码：{resp.status_code})")
            
            content = resp.text
            lines = [l for l in content.split('\n') if l and not l.startswith('#')]
            
            print(f"✅ 指标总数：{len(lines)}")
            
            # 检查关键指标
            metrics_to_check = {
                'yunshu_http_requests_total': 'HTTP 请求',
                'yunshu_http_request_duration_seconds': '请求耗时',
                'yunshu_security_blocks_total': '安全拦截',
                'yunshu_cpu_usage_percent': 'CPU 使用率',
                'yunshu_memory_usage_percent': '内存使用率',
                'yunshu_conversations_total': '对话次数',
            }
            
            print("\n关键指标检查:")
            for metric_name, metric_desc in metrics_to_check.items():
                if metric_name in content:
                    count = len([l for l in lines if metric_name in l])
                    print(f"✅ {metric_desc}: {count} 条记录")
                else:
                    print(f"⚠️  {metric_desc}: 暂无数据")
            
            # 显示部分指标示例
            print("\n指标示例:")
            sample_metrics = [
                'yunshu_http_requests_total',
                'yunshu_security_blocks_total',
                'yunshu_conversations_total',
            ]
            
            for metric in sample_metrics:
                matches = [l for l in lines if metric in l][:2]
                for m in matches:
                    print(f"  {m[:120]}")
            
            return True
        else:
            print(f"❌ /metrics 返回错误：{resp.status_code}")
            return False
            
    except requests.exceptions.ConnectionError:
        print(f"❌ 无法连接到 {BASE_URL}")
        print("   请确保 Yunshu 服务器正在运行")
        return False
    except Exception as e:
        print(f"❌ 错误：{e}")
        return False

def verify_business_metrics():
    """验证业务指标"""
    print("\n" + "="*70)
    print("📊 验证业务指标")
    print("="*70)
    
    try:
        resp = requests.get(f"{BASE_URL}/metrics", timeout=5)
        content = resp.text
        
        business_metrics = {
            'yunshu_user_logins_total': '用户登录',
            'yunshu_api_calls_total': 'API 调用',
            'yunshu_conversations_total': '对话次数',
            'yunshu_tool_calls_total': '工具调用',
            'yunshu_active_connections': '活跃连接',
        }
        
        print("业务指标检查:")
        for metric_name, metric_desc in business_metrics.items():
            if metric_name in content:
                count = len([l for l in content.split('\n') if metric_name in l and not l.startswith('#')])
                print(f"✅ {metric_desc}: {count} 条记录")
            else:
                print(f"⚠️  {metric_desc}: 指标已定义（需要触发操作后才有数据）")
        
        return True
        
    except Exception as e:
        print(f"❌ 错误：{e}")
        return False

def show_deployment_instructions():
    """显示部署说明"""
    print("\n" + "="*70)
    print("📚 部署说明")
    print("="*70)
    
    print("""
Docker 未运行时的部署步骤:

1. 启动 Docker Desktop
   - Windows: 在开始菜单搜索 "Docker Desktop"
   - 等待 Docker 图标变为绿色（表示运行中）

2. 启动监控栈
   docker-compose -f docker-compose.monitoring.yml up -d

3. 验证服务
   docker-compose -f docker-compose.monitoring.yml ps

4. 访问界面
   - Prometheus: http://localhost:9090
   - Grafana: http://localhost:3000
     用户名：admin
     密码：admin123

5. 导入仪表盘
   - 登录 Grafana
   - 点击 Dashboards → Import
   - 上传 monitoring/grafana/dashboards/yunshu-monitor.json
   - 选择 Prometheus 数据源
   - 点击 Import

替代方案（不使用 Docker）:
1. 手动安装 Prometheus 和 Grafana
2. 参考 PROMETHEUS_GRAFANA_DEPLOYMENT_GUIDE.md
""")

def main():
    print("="*70)
    print("🔍 Prometheus & Grafana 配置验证")
    print("="*70)
    
    # 检查 Docker
    docker_available = check_docker()
    
    # 验证配置文件
    grafana_ok = verify_grafana_config()
    prometheus_ok = verify_prometheus_config()
    
    # 验证 Yunshu 指标
    yunshu_ok = verify_yunshu_metrics()
    business_ok = verify_business_metrics()
    
    # 汇总
    print("\n" + "="*70)
    print("✅ 验证汇总")
    print("="*70)
    
    results = {
        "Docker 可用": docker_available,
        "Grafana 配置": grafana_ok,
        "Prometheus 配置": prometheus_ok,
        "Yunshu 指标端点": yunshu_ok,
        "业务指标定义": business_ok,
    }
    
    for item, result in results.items():
        status = "✅" if result else "❌"
        print(f"{status} {item}")
    
    all_passed = all(results.values())
    
    if all_passed and docker_available:
        print("\n🎉 所有检查通过！可以启动 Docker Compose")
        print("\n运行命令:")
        print("  docker-compose -f docker-compose.monitoring.yml up -d")
    elif all_passed and not docker_available:
        print("\n✅ 配置文件检查通过")
        show_deployment_instructions()
    else:
        print("\n⚠️  部分检查未通过，请查看上方详情")
    
    return all_passed

if __name__ == "__main__":
    try:
        success = main()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\n验证中断")
        sys.exit(1)
    except Exception as e:
        print(f"\n✗ 验证失败：{e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
