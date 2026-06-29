import os
import sys
import json
import subprocess
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
LOG_FILE = PROJECT_ROOT / "test_reports" / "scheduler.log"


def log(message, level="INFO"):
    """日志记录函数，同时输出到控制台和日志文件"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_line = f"[{timestamp}] [{level}] {message}\n"
    
    print(log_line.strip())
    
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(log_line)
    except Exception as e:
        print(f"写入日志失败: {e}")


def run_command(cmd, cwd=None):
    """运行命令并返回输出"""
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=120
        )
        return result.stdout, result.stderr, result.returncode
    except subprocess.TimeoutExpired:
        return "", "Command timed out", -1


def generate_quality_report():
    """生成每日质量报告"""
    log("开始生成每日质量报告...")
    
    report_path = PROJECT_ROOT / "test_reports" / "daily_quality_report.json"
    cmd = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "generate_quality_report.py"),
        "--type=daily",
        f"--output={report_path}"
    ]
    
    stdout, stderr, returncode = run_command(cmd, cwd=PROJECT_ROOT)
    
    if returncode == 0:
        log("✅ 质量报告生成成功")
        if stdout:
            log(stdout.strip(), "DEBUG")
        
        md_path = PROJECT_ROOT / "test_reports" / "daily_quality_report.md"
        if md_path.exists():
            with open(md_path, 'r', encoding='utf-8') as f:
                md_content = f.read()
            log(f"报告内容已保存到: {md_path}")
            return True, md_content
    else:
        log("❌ 质量报告生成失败", "ERROR")
        log(f"错误: {stderr}", "ERROR")
        return False, None


def commit_and_push():
    """提交并推送报告到 Git 仓库"""
    log("开始提交报告到 Git 仓库...")
    
    add_cmd = ["git", "add", "test_reports/daily_quality_report.md", "test_reports/daily_quality_report.json"]
    stdout, stderr, returncode = run_command(add_cmd, cwd=PROJECT_ROOT)
    if returncode != 0:
        log(f"❌ git add 失败: {stderr}", "ERROR")
        return False
    
    today = datetime.now().strftime("%Y-%m-%d")
    commit_cmd = ["git", "commit", "-m", f"docs: 更新每日质量报告 {today}"]
    stdout, stderr, returncode = run_command(commit_cmd, cwd=PROJECT_ROOT)
    
    if returncode == 0:
        log(f"✅ 提交成功: {stdout.strip()}")
        
        try:
            push_cmd = ["git", "push"]
            stdout, stderr, returncode = run_command(push_cmd, cwd=PROJECT_ROOT)
            
            if returncode == 0:
                log(f"✅ 推送成功: {stdout.strip()}")
                return True
            elif "has no upstream branch" in stderr:
                branch_cmd = ["git", "rev-parse", "--abbrev-ref", "HEAD"]
                branch_out, _, _ = run_command(branch_cmd, cwd=PROJECT_ROOT)
                branch_name = branch_out.strip()
                
                log(f"尝试设置 upstream: {branch_name}")
                push_upstream_cmd = ["git", "push", "--set-upstream", "origin", branch_name]
                stdout, stderr, returncode = run_command(push_upstream_cmd, cwd=PROJECT_ROOT)
                
                if returncode == 0:
                    log(f"✅ 推送成功: {stdout.strip()}")
                    return True
                else:
                    log(f"⚠️ 推送失败（需要配置 Git 凭证）: {stderr}", "WARNING")
                    return False
            else:
                log(f"⚠️ 推送失败: {stderr}", "WARNING")
                return False
        except Exception as e:
            log(f"⚠️ 推送过程异常: {str(e)}", "WARNING")
            return False
    else:
        log(f"⚠️ 无新变更需要提交: {stderr}")
        return False


def send_notification():
    """发送通知（可以扩展为企业微信、邮件等）"""
    log("发送通知...")
    
    report_path = PROJECT_ROOT / "test_reports" / "daily_quality_report.json"
    if report_path.exists():
        with open(report_path, 'r', encoding='utf-8') as f:
            report = json.load(f)
        
        summary = report.get('summary', {})
        status = summary.get('overall_status', 'UNKNOWN')
        score = summary.get('quality_score', 0)
        
        log(f"📊 质量报告通知")
        log(f"日期: {datetime.now().strftime('%Y-%m-%d')}")
        log(f"状态: {'✅ 通过' if status == 'PASS' else '❌ 失败'}")
        log(f"评分: {score}")
        
        recommendations = report.get('recommendations', [])
        if recommendations:
            log("\n建议:")
            for rec in recommendations:
                log(f"  - {rec}")


def main():
    """主入口"""
    log("="*60)
    log("云枢系统每日质量报告定时任务")
    log(f"启动时间: {datetime.now()}")
    log("="*60)
    
    try:
        success, md_content = generate_quality_report()
        
        if success:
            commit_and_push()
            send_notification()
        
        log("="*60)
        log(f"任务完成: {datetime.now()}")
        log("="*60)
        
        return 0
    except Exception as e:
        log(f"任务执行异常: {str(e)}", "ERROR")
        import traceback
        log(traceback.format_exc(), "ERROR")
        return 1


if __name__ == "__main__":
    sys.exit(main())