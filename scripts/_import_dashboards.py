#!/usr/bin/env python3
"""导入全链路监控仪表盘到 Grafana"""
import json
import os
import sys

import requests

# 【P1 修复 2026-07-20】所有配置从环境变量读取，避免硬编码
GRAFANA_URL = os.environ.get("GRAFANA_URL", "http://localhost:3000")
GRAFANA_USER = os.environ.get("GRAFANA_ADMIN_USER", "admin")
GRAFANA_PASSWORD = os.environ.get("GRAFANA_ADMIN_PASSWORD")
if not GRAFANA_PASSWORD:
    print("ERROR: GRAFANA_ADMIN_PASSWORD 环境变量未设置，无法导入仪表盘")
    sys.exit(1)

def main():
    # 1. 获取 Prometheus 数据源
    print("获取 Prometheus 数据源...")
    response = requests.get(
        f"{GRAFANA_URL}/api/datasources",
        auth=(GRAFANA_USER, GRAFANA_PASSWORD)
    )
    datasources = response.json()
    prom_ds = None
    for ds in datasources:
        if ds.get("type") == "prometheus":
            prom_ds = ds
            break

    if not prom_ds:
        print("ERROR: 未找到 Prometheus 数据源")
        return

    print(f"  数据源: {prom_ds['name']}, UID: {prom_ds['uid']}")

    # 2. 读取仪表盘 JSON
    dashboard_files = [
        "monitoring/grafana/dashboards/yunshu-full-monitoring.json",
        "monitoring/grafana/dashboards/yunshu-alerts-monitor.json",
        "monitoring/grafana/dashboards/yunshu-monitor.json",
    ]

    for db_file in dashboard_files:
        try:
            with open(db_file, "r", encoding="utf-8") as f:
                dashboard = json.load(f)

            title = dashboard.get("title", "Unknown")
            print(f"\n导入仪表盘: {title}")

            # 3. 构建导入请求
            import_payload = {
                "dashboard": dashboard,
                "overwrite": True,
                "inputs": [{
                    "name": "DS_PROMETHEUS",
                    "type": "datasource",
                    "pluginId": "prometheus",
                    "value": prom_ds["uid"]
                }]
            }

            # 4. 导入仪表盘
            response = requests.post(
                f"{GRAFANA_URL}/api/dashboards/import",
                auth=(GRAFANA_USER, GRAFANA_PASSWORD),
                json=import_payload
            )

            if response.status_code == 200:
                result = response.json()
                print(f"  [OK] 导入成功! ID: {result.get('id')}, UID: {result.get('uid')}")
                print(f"  地址: {GRAFANA_URL}/d/{result.get('uid')}")
            else:
                print(f"  [ERROR] 导入失败: {response.status_code}")
                print(f"  {response.text[:300]}")
        except FileNotFoundError:
            print(f"\n[WARN] 文件不存在: {db_file}")
        except Exception as e:
            print(f"\n[ERROR] 导入异常: {str(e)}")

if __name__ == "__main__":
    main()