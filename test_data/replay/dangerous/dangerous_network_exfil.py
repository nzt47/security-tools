import socket

# 逃逸尝试：网络外联（应被静态预扫描拦截，绝不执行）
s = socket.socket()
s.connect(("127.0.0.1", 9999))
s.send(b"secret")

print("done")
