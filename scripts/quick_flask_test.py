#!/usr/bin/env python3
"""快速测试 Flask 传感器服务 API"""

import sys
import os

# 添加根目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 导入Flask应用
from flask import Flask, jsonify
import logging

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)

# 全局传感器实例
_sensor_instance = None

def get_sensor():
    """获取传感器实例"""
    global _sensor_instance
    if _sensor_instance is None:
        from sensor import BodySensor
        _sensor_instance = BodySensor(
            enable_change_detection=False,
            enable_event_monitor=False
        )
        logging.info("✅ 传感器系统初始化完成")
    return _sensor_instance

def create_app():
    """创建测试用 Flask 应用"""
    app = Flask(__name__)
    
    @app.route('/api/sensors/health')
    def health():
        try:
            sensor = get_sensor()
            return jsonify({
                'success': True,
                'status': 'healthy',
                'sensor_count': len(sensor._registry)
            })
        except Exception as e:
            logging.error(f"健康检查失败: {e}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500
    
    @app.route('/api/sensors')
    def get_all():
        try:
            sensor = get_sensor()
            data = sensor.collect_all()
            return jsonify({
                'success': True,
                'count': len(data),
                'data': [r.to_dict() for r in data]
            })
        except Exception as e:
            logging.error(f"获取传感器数据失败: {e}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500
    
    @app.route('/api/sensors/info')
    def info():
        try:
            sensor = get_sensor()
            sensor_info = sensor.get_sensor_info()
            return jsonify({
                'success': True,
                'data': sensor_info
            })
        except Exception as e:
            logging.error(f"获取传感器信息失败: {e}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500
    
    return app

def test_api():
    print("🧪 测试 Flask 传感器服务 API")
    print("=" * 60)
    
    app = create_app()
    
    # 创建测试客户端
    with app.test_client() as client:
        # 测试健康检查
        print("\n📡 测试健康检查接口...")
        response = client.get('/api/sensors/health')
        print(f"   状态码: {response.status_code}")
        if response.status_code == 200:
            data = response.get_json()
            print(f"   成功: {data.get('success')}")
            print(f"   传感器数量: {data.get('sensor_count')}")
        else:
            print(f"   失败: {response.get_json()}")
        
        # 测试传感器信息
        print("\n📡 测试获取传感器信息...")
        response = client.get('/api/sensors/info')
        print(f"   状态码: {response.status_code}")
        if response.status_code == 200:
            info = response.get_json()
            print(f"   成功: {info.get('success')}")
        else:
            print(f"   失败: {response.get_json()}")
        
        # 测试获取所有传感器数据
        print("\n📡 测试获取所有传感器数据...")
        response = client.get('/api/sensors')
        print(f"   状态码: {response.status_code}")
        if response.status_code == 200:
            data = response.get_json()
            print(f"   成功: {data.get('success')}")
            print(f"   数据条数: {data.get('count', 0)}")
            
            if data.get('success'):
                print("\n✅ Flask 传感器服务测试通过!")
                return True
        
        print("\n❌ Flask 传感器服务测试失败")
        return False

if __name__ == '__main__':
    try:
        success = test_api()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n❌ 测试异常: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
