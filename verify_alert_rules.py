#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
验证 Prometheus 告警规则配置
"""

import yaml
import json
from pathlib import Path
from datetime import datetime

def verify_alert_rules():
    """验证告警规则文件"""
    print("="*70)
    print("🔍 Prometheus 告警规则验证")
    print("="*70)
    
    alert_files = [
        "monitoring/alerts.yml",
        "monitoring/alerts_production.yml"
    ]
    
    results = {}
    
    for file_path in alert_files:
        print(f"\n验证文件：{file_path}")
        print("-" * 70)
        
        if not Path(file_path).exists():
            print(f"❌ 文件不存在")
            results[file_path] = False
            continue
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)
            
            print(f"✅ YAML 格式正确")
            
            # 验证结构
            if 'groups' not in config:
                print(f"❌ 缺少 groups 字段")
                results[file_path] = False
                continue
            
            total_rules = 0
            alerts_by_severity = {}
            alerts_by_category = {}
            
            for group in config['groups']:
                group_name = group.get('name', 'unknown')
                print(f"\n告警组：{group_name}")
                
                if 'interval' in group:
                    print(f"  评估间隔：{group['interval']}")
                
                rules = group.get('rules', [])
                total_rules += len(rules)
                
                for rule in rules:
                    if 'alert' in rule:
                        alert_name = rule['alert']
                        severity = rule.get('labels', {}).get('severity', 'unknown')
                        
                        # 按严重级别统计
                        alerts_by_severity[severity] = alerts_by_severity.get(severity, 0) + 1
                        
                        # 按类别统计（从名称推断）
                        if 'ErrorRate' in alert_name:
                            category = '错误率'
                        elif 'Latency' in alert_name:
                            category = '延迟'
                        elif 'Security' in alert_name or 'Attack' in alert_name:
                            category = '安全'
                        elif 'CPU' in alert_name or 'Memory' in alert_name:
                            category = '系统资源'
                        elif 'Conversation' in alert_name:
                            category = '对话系统'
                        elif 'Down' in alert_name or 'Missing' in alert_name or 'NoTraffic' in alert_name:
                            category = '服务可用性'
                        elif 'Connections' in alert_name:
                            category = '业务指标'
                        else:
                            category = '其他'
                        
                        alerts_by_category[category] = alerts_by_category.get(category, 0) + 1
                        
                        # 显示规则详情
                        expr = rule.get('expr', 'N/A')
                        for_duration = rule.get('for', 'N/A')
                        summary = rule.get('annotations', {}).get('summary', 'N/A')
                        
                        print(f"  ✅ {alert_name}")
                        print(f"     级别：{severity}")
                        print(f"     条件：{expr[:60]}...")
                        print(f"     持续：{for_duration}")
                        print(f"     说明：{summary}")
            
            print(f"\n📊 统计汇总")
            print(f"  总规则数：{total_rules}")
            print(f"  按严重级别:")
            for severity, count in sorted(alerts_by_severity.items()):
                print(f"    {severity}: {count} 个")
            print(f"  按类别:")
            for category, count in sorted(alerts_by_category.items()):
                print(f"    {category}: {count} 个")
            
            results[file_path] = True
            
        except yaml.YAMLError as e:
            print(f"❌ YAML 解析错误：{e}")
            results[file_path] = False
        except Exception as e:
            print(f"❌ 错误：{e}")
            results[file_path] = False
    
    # 汇总
    print("\n" + "="*70)
    print("✅ 验证汇总")
    print("="*70)
    
    for file_path, success in results.items():
        status = "✅" if success else "❌"
        print(f"{status} {file_path}")
    
    all_success = all(results.values())
    
    if all_success:
        print("\n🎉 所有告警规则文件验证通过!")
    else:
        print("\n⚠️  部分文件验证失败")
    
    return all_success

def check_prometheus_config():
    """检查 Prometheus 主配置"""
    print("\n" + "="*70)
    print("📁 Prometheus 主配置检查")
    print("="*70)
    
    prometheus_config = "monitoring/prometheus.yml"
    
    if not Path(prometheus_config).exists():
        print(f"❌ 配置文件不存在")
        return False
    
    try:
        with open(prometheus_config, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
        
        print(f"✅ YAML 格式正确")
        
        # 检查 rule_files
        rule_files = config.get('rule_files', [])
        if rule_files:
            print(f"\n✅ 已配置 rule_files:")
            for rf in rule_files:
                print(f"   - {rf}")
                if Path(f"monitoring/{rf}").exists():
                    print(f"     ✅ 文件存在")
                else:
                    print(f"     ⚠️  文件不存在")
        else:
            print(f"\n⚠️  未配置 rule_files")
        
        # 检查 scrape_configs
        scrape_configs = config.get('scrape_configs', [])
        print(f"\n📊 监控目标配置:")
        for sc in scrape_configs:
            job_name = sc.get('job_name', 'unknown')
            targets = sc.get('static_configs', [{}])[0].get('targets', [])
            interval = sc.get('scrape_interval', 'N/A')
            
            print(f"  Job: {job_name}")
            print(f"    目标：{targets}")
            print(f"    间隔：{interval}")
        
        return True
        
    except Exception as e:
        print(f"❌ 错误：{e}")
        return False

def generate_deployment_checklist():
    """生成部署检查清单"""
    print("\n" + "="*70)
    print("📋 生成部署检查清单")
    print("="*70)
    
    checklist = """
# Prometheus 告警规则部署检查清单

## 1. 配置文件验证

- [ ] monitoring/alerts.yml 存在且格式正确
- [ ] monitoring/alerts_production.yml 存在且格式正确
- [ ] monitoring/prometheus.yml 已配置 rule_files
- [ ] 所有告警规则 YAML 语法验证通过

## 2. Docker 环境准备

- [ ] Docker Desktop 已启动
- [ ] Docker 版本 >= 20.10
- [ ] Docker Compose 版本 >= 2.0

## 3. 启动监控栈

```bash
# 启动服务
docker-compose -f docker-compose.monitoring.yml up -d

# 查看日志
docker-compose -f docker-compose.monitoring.yml logs -f prometheus

# 验证服务
docker-compose -f docker-compose.monitoring.yml ps
```

- [ ] Prometheus 容器运行正常
- [ ] Grafana 容器运行正常
- [ ] 无错误日志

## 4. Prometheus 验证

访问 http://localhost:9090

- [ ] Prometheus UI 可访问
- [ ] Status → Rules 页面显示 18 个告警规则
- [ ] 所有规则状态为 OK（无触发）
- [ ] Targets 页面显示 yunshu 和 prometheus 为 UP

## 5. 告警规则验证

在 Prometheus UI (http://localhost:9090) 执行以下查询：

### 5.1 检查规则加载
```promql
ALERTS
```
- [ ] 显示所有 18 个告警规则
- [ ] alertstate 为 inactive（未触发）

### 5.2 测试错误率告警
```promql
sum(rate(yunshu_http_requests_total{status=~"5.."}[5m])) / sum(rate(yunshu_http_requests_total[5m]))
```
- [ ] 当前错误率 < 5%（正常）
- [ ] 如果 > 5%，应触发 warning 告警

### 5.3 测试延迟告警
```promql
histogram_quantile(0.95, rate(yunshu_http_request_duration_seconds_bucket[5m]))
```
- [ ] 当前 95 分位延迟 < 500ms（正常）
- [ ] 如果 > 500ms，应触发 warning 告警

### 5.4 测试安全告警
```promql
sum(rate(yunshu_security_blocks_total[5m]))
```
- [ ] 当前拦截速率 < 3 次/分（正常）
- [ ] 如果 > 3 次/分，应触发 warning 告警

## 6. Grafana 验证

访问 http://localhost:3000 (admin/admin123)

- [ ] Grafana UI 可访问
- [ ] Prometheus 数据源配置正确
- [ ] Yunshu Monitor 仪表盘已导入
- [ ] 所有面板显示数据

## 7. 告警通知测试

### 7.1 手动触发测试告警

在 Prometheus 执行：
```promql
# 临时触发高 CPU 告警（如果当前 CPU < 70%）
yunshu_cpu_usage_percent > 0
```

- [ ] 告警状态变为 pending
- [ ] 2 分钟后告警状态变为 firing
- [ ] 收到通知（邮件/IM/电话）

### 7.2 验证通知渠道

- [ ] 邮件通知正常
- [ ] Slack/钉钉/企业微信通知正常
- [ ] 电话通知正常（如有配置）

## 8. 性能测试

### 8.1 并发请求测试

```bash
# 使用 ab 或 wrk 进行压力测试
ab -n 1000 -c 10 http://localhost:5678/api/health
```

- [ ] 错误率告警未触发（正常情况）
- [ ] 延迟告警可能触发（如果响应慢）
- [ ] Prometheus 抓取正常

### 8.2 安全拦截测试

```bash
# 发送危险指令
curl -X POST http://localhost:5678/api/chat \\
  -H "Content-Type: application/json" \\
  -d '{"message":"rm -rf /","voice":false}'
```

- [ ] 返回 403 Forbidden
- [ ] 安全拦截计数 +1
- [ ] 如果频率高，触发安全告警

## 9. 故障恢复测试

### 9.1 模拟服务宕机

```bash
# 停止 Yunshu 服务
# 在另一个终端执行 Ctrl+C
```

- [ ] 1 分钟后触发 YunshuDown 告警
- [ ] Prometheus Target 显示 DOWN

### 9.2 恢复服务

```bash
# 重新启动 Yunshu
python app_server.py
```

- [ ] 告警自动恢复
- [ ] Target 恢复为 UP

## 10. 文档和监控

- [ ] 告警规则文档已更新
- [ ] 运维手册已包含告警响应流程
- [ ] 值班表已配置
- [ ] 联系人列表已更新

---

**检查完成时间**: ___________
**检查人**: ___________
**备注**: ___________
"""
    
    # 保存检查清单
    checklist_file = "alert_deployment_checklist.md"
    with open(checklist_file, 'w', encoding='utf-8') as f:
        f.write(checklist)
    
    print(f"✅ 部署检查清单已保存：{checklist_file}")
    print("\n" + checklist[:500] + "...")
    
    return True

def main():
    print("="*70)
    print("🔍 Prometheus 告警规则验证工具")
    print("="*70)
    print(f"时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # 验证告警规则
    alert_rules_ok = verify_alert_rules()
    
    # 检查 Prometheus 配置
    prometheus_config_ok = check_prometheus_config()
    
    # 生成部署检查清单
    checklist_ok = generate_deployment_checklist()
    
    # 汇总
    print("\n" + "="*70)
    print("✅ 最终验证结果")
    print("="*70)
    
    results = {
        "告警规则文件": alert_rules_ok,
        "Prometheus 配置": prometheus_config_ok,
        "部署检查清单": checklist_ok,
    }
    
    for item, result in results.items():
        status = "✅" if result else "❌"
        print(f"{status} {item}")
    
    all_passed = all(results.values())
    
    if all_passed:
        print("\n🎉 所有验证通过！")
        print("\n下一步:")
        print("1. 启动 Docker Desktop")
        print("2. 运行：docker-compose -f docker-compose.monitoring.yml up -d")
        print("3. 访问 Prometheus: http://localhost:9090")
        print("4. 访问 Grafana: http://localhost:3000")
        print("5. 按照检查清单逐项验证")
    else:
        print("\n⚠️  部分验证失败，请检查上方详情")
    
    return all_passed

if __name__ == "__main__":
    import sys
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
