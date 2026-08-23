#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Mock SSH 服务器 — 本地完整演练 diagnose_ssh.sh / diagnose_ssh.ps1 的诊断逻辑

无需真实 sshd 即可在本地演练 5 层诊断：
  层3 TCP 端口   监听端口即通过
  层4 SSH banner 发送标准 SSH-2.0 banner（--no-banner 模拟非 SSH 服务）
  层5 认证       ssh 客户端握手后按模式断开（Connection closed）或文本拒绝

用法:
  python mock_ssh_server.py [--port 2222]          # 默认：banner 正常 + 握手后关闭
  python mock_ssh_server.py --port 2222 --no-banner  # 模拟非 SSH 服务（banner 层 FAIL）
  python mock_ssh_server.py --port 2222 --auth-deny  # 握手后发送 Permission denied 文本
  配合诊断：bash scripts/dev/diagnose_ssh.sh --host 127.0.0.1 --port 2222 --user demo --key <key>

退出码：常驻服务，Ctrl+C 停止。
"""

import argparse
import socket
import threading

SSH_BANNER = b"SSH-2.0-OpenSSH_mock_9.5\r\n"
AUTH_DENY_MSG = b"Permission denied (publickey).\r\n"
RECV_TIMEOUT = 8  # 等待客户端后续数据的秒数


def handle_conn(conn, auth_deny: bool, no_banner: bool):
    try:
        if not no_banner:
            conn.sendall(SSH_BANNER)
            print("  [mock] 已发送 banner: SSH-2.0-OpenSSH_mock_9.5")
        else:
            print("  [mock] --no-banner：不发送 banner（模拟非 SSH 服务）")
            conn.close()
            return
        # 等待客户端版本交换/握手数据（真实 ssh 客户端会发来 SSH-2.0-<client> 版本行）
        conn.settimeout(RECV_TIMEOUT)
        try:
            data = conn.recv(1024)
        except socket.timeout:
            print("  [mock] 客户端在 %ss 内无数据，关闭连接" % RECV_TIMEOUT)
            conn.close()
            return
        if data:
            head = data.split(b"\r\n", 1)[0][:60].decode("utf-8", "replace")
            print("  [mock] 收到客户端首包: %r" % head)
        if auth_deny:
            conn.sendall(AUTH_DENY_MSG)
            print("  [mock] 已发送拒绝文本: Permission denied (publickey).")
        else:
            print("  [mock] 握手后关闭连接（模拟 KEX 失败）")
        conn.close()
    except OSError as exc:
        print("  [mock] 连接异常: %s" % exc)
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=2222)
    parser.add_argument("--no-banner", action="store_true",
                        help="不发送 SSH banner（模拟非 SSH 服务）")
    parser.add_argument("--auth-deny", action="store_true",
                        help="收到客户端数据后发送 Permission denied 文本")
    args = parser.parse_args()

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", args.port))
    srv.listen(16)
    print("== Mock SSH 服务器 ==  127.0.0.1:%d" % args.port)
    print("   banner: %s" % ("有 (SSH-2.0-OpenSSH_mock_9.5)" if not args.no_banner else "无"))
    print("   auth-deny: %s" % args.auth_deny)
    print("   Ctrl+C 停止")
    try:
        while True:
            conn, addr = srv.accept()
            print("[mock] 新连接: %s:%d" % (addr[0], addr[1]))
            threading.Thread(target=handle_conn, args=(conn, args.auth_deny, args.no_banner),
                             daemon=True).start()
    except KeyboardInterrupt:
        print("\n[mock] 已停止")
    finally:
        srv.close()


if __name__ == "__main__":
    main()
