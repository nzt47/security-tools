import sys

# 逃逸尝试：向宿主文件系统写入文件（应被静态预扫描拦截，绝不执行）
with open("pwned.txt", "w") as f:
    f.write("owned")

print("done")
