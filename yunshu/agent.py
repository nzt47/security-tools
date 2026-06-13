```python
import http.server
import json
import os
import urllib.parse

class AgentHandler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers['Content-Length'])
        data = json.loads(self.rfile.read(length).decode('utf-8'))
        action = data.get('action', '')
        target_dir = data.get('target_dir', os.path.expanduser('~/云枢记忆'))
        os.makedirs(target_dir, exist_ok=True)
        
        if action == 'save':
            filename = data.get('filename', '云枢记忆.txt')
            content = data.get('content', '')
            filepath = os.path.join(target_dir, filename)
            with open(filepath, 'a', encoding='utf-8') as f:
                f.write(content + '\n\n')
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({"status":"ok"}).encode())
        else:
            self.send_response(400)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # 不输出日志

if __name__ == '__main__':
    server = http.server.HTTPServer(('127.0.0.1', 8123), AgentHandler)
    print('云枢Agent启动，监听端口8123')
    server.serve_forever()
```