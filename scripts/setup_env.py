#!/usr/bin/env python3
"""
本地环境搭建自动化脚本
根据 QUICK_START.md 自动化完成云枢智能体本地开发环境的搭建
"""

import os
import sys
import subprocess
import platform
from pathlib import Path


def print_banner():
    """打印横幅"""
    print("="*70)
    print("🚀 云枢智能体 - 本地环境搭建自动化脚本")
    print("="*70)
    print()


def print_step(step_num, total, title):
    """打印步骤标题"""
    print()
    print("-"*70)
    print(f"📌 步骤 [{step_num}/{total}]: {title}")
    print("-"*70)


def print_success(message):
    """打印成功信息"""
    print(f"✅ {message}")


def print_warning(message):
    """打印警告信息"""
    print(f"⚠️  {message}")


def print_error(message):
    """打印错误信息"""
    print(f"❌ {message}")


def print_info(message):
    """打印信息"""
    print(f"ℹ️  {message}")


def run_command(cmd, cwd=None, check=True, timeout=120):
    """执行命令并返回结果"""
    print_info(f"执行命令: {cmd}")
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        if result.stdout:
            print(result.stdout.strip())
        if result.returncode != 0 and check:
            if result.stderr:
                print_error(result.stderr.strip())
            return False
        return True
    except subprocess.TimeoutExpired:
        print_error(f"命令执行超时 ({timeout}秒)")
        return False
    except Exception as e:
        print_error(f"命令执行失败: {str(e)}")
        return False


def check_python():
    """检查Python环境"""
    print_info("检查Python版本...")
    
    try:
        result = subprocess.run(
            [sys.executable, "--version"],
            capture_output=True,
            text=True
        )
        version = result.stdout.strip()
        print_success(f"Python 版本: {version}")
        
        # 检查版本号
        major, minor = sys.version_info.major, sys.version_info.minor
        if major >= 3 and minor >= 8:
            print_success(f"Python 版本满足要求 (>= 3.8)")
            return True
        else:
            print_error(f"Python 版本过低，需要 >= 3.8")
            return False
    except Exception as e:
        print_error(f"检查Python版本失败: {str(e)}")
        return False


def check_pip():
    """检查pip"""
    print_info("检查pip...")
    
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "--version"],
            capture_output=True,
            text=True
        )
        version = result.stdout.strip()
        print_success(f"pip 版本: {version}")
        return True
    except Exception as e:
        print_error(f"检查pip失败: {str(e)}")
        return False


def create_virtual_env(project_dir):
    """创建虚拟环境"""
    venv_dir = project_dir / "venv"
    
    if venv_dir.exists():
        print_warning("虚拟环境已存在，跳过创建")
        return True
    
    print_info("创建Python虚拟环境...")
    
    try:
        subprocess.run(
            [sys.executable, "-m", "venv", str(venv_dir)],
            check=True
        )
        print_success(f"虚拟环境创建成功: {venv_dir}")
        return True
    except Exception as e:
        print_error(f"创建虚拟环境失败: {str(e)}")
        return False


def get_venv_python(project_dir):
    """获取虚拟环境中的Python路径"""
    venv_dir = project_dir / "venv"
    if platform.system() == "Windows":
        return str(venv_dir / "Scripts" / "python.exe")
    else:
        return str(venv_dir / "bin" / "python")


def install_dependencies(project_dir, venv_python):
    """安装依赖"""
    requirements_file = project_dir / "requirements.txt"
    
    if not requirements_file.exists():
        print_warning("requirements.txt 不存在，跳过依赖安装")
        return True
    
    print_info("安装Python依赖包...")
    
    # 先升级pip
    print_info("升级pip...")
    run_command(f'"{venv_python}" -m pip install --upgrade pip', check=False)
    
    # 安装依赖
    print_info("安装项目依赖...")
    cmd = f'"{venv_python}" -m pip install -r "{requirements_file}"'
    success = run_command(cmd, timeout=300)
    
    if success:
        print_success("依赖安装完成")
    else:
        print_error("依赖安装失败，请手动检查")
    
    return success


def check_opentelemetry(venv_python):
    """检查OpenTelemetry依赖"""
    print_info("检查OpenTelemetry依赖...")
    
    packages = [
        "opentelemetry-api",
        "opentelemetry-sdk",
        "opentelemetry-exporter-otlp-proto-grpc",
        "opentelemetry-exporter-jaeger-thrift"
    ]
    
    missing = []
    for pkg in packages:
        try:
            result = subprocess.run(
                [venv_python, "-c", f"import {pkg.replace('-', '_')}; print('ok')"],
                capture_output=True,
                text=True
            )
            if "ok" in result.stdout:
                print_success(f"  {pkg} 已安装")
            else:
                missing.append(pkg)
        except:
            missing.append(pkg)
    
    if missing:
        print_warning(f"缺少OpenTelemetry包: {', '.join(missing)}")
        print_info("尝试安装缺失的包...")
        for pkg in missing:
            run_command(f'"{venv_python}" -m pip install {pkg}', check=False, timeout=60)
    
    return True


def check_config_files(project_dir):
    """检查配置文件"""
    print_info("检查配置文件...")
    
    config_files = [
        (".env.example", ".env"),
        ("config.yaml.example", "config.yaml")
    ]
    
    for example, actual in config_files:
        example_path = project_dir / example
        actual_path = project_dir / actual
        
        if actual_path.exists():
            print_success(f"  {actual} 已存在")
        elif example_path.exists():
            print_info(f"  从 {example} 复制创建 {actual}")
            import shutil
            shutil.copy2(str(example_path), str(actual_path))
            print_success(f"  {actual} 创建成功")
        else:
            print_warning(f"  {example} 不存在，请手动创建 {actual}")
    
    return True


def create_directories(project_dir):
    """创建必要的目录"""
    print_info("创建必要的目录...")
    
    dirs = [
        "logs",
        "data",
        "temp",
        "monitoring"
    ]
    
    for d in dirs:
        dir_path = project_dir / d
        if not dir_path.exists():
            dir_path.mkdir(parents=True, exist_ok=True)
            print_success(f"  创建目录: {d}")
        else:
            print_info(f"  目录已存在: {d}")
    
    return True


def verify_setup(project_dir, venv_python):
    """验证环境搭建"""
    print()
    print("="*70)
    print("🔍 验证环境搭建结果")
    print("="*70)
    
    results = {}
    
    # 1. 虚拟环境
    venv_exists = (project_dir / "venv").exists()
    results["虚拟环境"] = "✅" if venv_exists else "❌"
    
    # 2. 测试导入
    try:
        result = subprocess.run(
            [venv_python, "-c", "import sys; print('Python OK')"],
            capture_output=True,
            text=True
        )
        results["Python运行"] = "✅" if "Python OK" in result.stdout else "❌"
    except:
        results["Python运行"] = "❌"
    
    # 3. 检查主文件
    main_py = project_dir / "main.py"
    app_server_py = project_dir / "app_server.py"
    has_main = main_py.exists() or app_server_py.exists()
    results["主程序文件"] = "✅" if has_main else "❌"
    
    # 4. 监控模块
    monitoring_dir = project_dir / "agent" / "monitoring"
    has_monitoring = monitoring_dir.exists()
    results["可观测性模块"] = "✅" if has_monitoring else "❌"
    
    # 打印结果
    print()
    for item, status in results.items():
        print(f"  {status} {item}")
    
    # 统计
    passed = sum(1 for v in results.values() if v == "✅")
    total = len(results)
    
    print()
    print(f"验证结果: {passed}/{total} 项通过")
    
    if passed == total:
        print_success("环境搭建完成！")
        print()
        print("🚀 快速启动:")
        if platform.system() == "Windows":
            print(f"   激活虚拟环境: .\\venv\\Scripts\\activate")
        else:
            print(f"   激活虚拟环境: source venv/bin/activate")
        print(f"   启动服务: python app_server.py")
        print(f"   健康检查: python scripts/health_check.py")
        return True
    else:
        print_warning("部分项目未通过，请检查上方日志")
        return False


def main():
    print_banner()
    
    # 获取项目根目录（脚本的上级目录）
    script_dir = Path(__file__).parent.resolve()
    project_dir = script_dir.parent
    
    print_info(f"项目目录: {project_dir}")
    print_info(f"操作系统: {platform.system()} {platform.release()}")
    print()
    
    total_steps = 8
    current_step = 0
    
    # 步骤1: 检查Python
    current_step += 1
    print_step(current_step, total_steps, "检查Python环境")
    if not check_python():
        print_error("Python环境检查失败，请先安装 Python 3.8+")
        sys.exit(1)
    
    # 步骤2: 检查pip
    current_step += 1
    print_step(current_step, total_steps, "检查pip")
    if not check_pip():
        print_error("pip检查失败")
        sys.exit(1)
    
    # 步骤3: 创建虚拟环境
    current_step += 1
    print_step(current_step, total_steps, "创建虚拟环境")
    if not create_virtual_env(project_dir):
        print_error("创建虚拟环境失败")
        sys.exit(1)
    
    # 获取虚拟环境Python路径
    venv_python = get_venv_python(project_dir)
    
    # 步骤4: 安装依赖
    current_step += 1
    print_step(current_step, total_steps, "安装Python依赖")
    install_dependencies(project_dir, venv_python)
    
    # 步骤5: 检查OpenTelemetry
    current_step += 1
    print_step(current_step, total_steps, "检查OpenTelemetry依赖")
    check_opentelemetry(venv_python)
    
    # 步骤6: 检查配置文件
    current_step += 1
    print_step(current_step, total_steps, "检查配置文件")
    check_config_files(project_dir)
    
    # 步骤7: 创建目录
    current_step += 1
    print_step(current_step, total_steps, "创建必要目录")
    create_directories(project_dir)
    
    # 步骤8: 验证环境
    current_step += 1
    print_step(current_step, total_steps, "验证环境搭建")
    success = verify_setup(project_dir, venv_python)
    
    print()
    print("="*70)
    if success:
        print("🎉 环境搭建完成！祝您开发愉快！")
    else:
        print("⚠️  环境搭建部分失败，请检查上述错误信息")
    print("="*70)
    
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()