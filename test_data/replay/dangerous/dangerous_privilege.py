import subprocess

# 逃逸尝试：提权/任意命令执行（应被静态预扫描拦截，绝不执行）
subprocess.run(["whoami"])

print("ok")
