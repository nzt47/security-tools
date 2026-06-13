// agent.js
const http = require('http');
const fs = require('fs');
const path = require('path');

const server = http.createServer((req, res) => {
    if (req.method === 'POST') {
        let body = '';
        req.on('data', chunk => body += chunk);
        req.on('end', () => {
            try {
                const data = JSON.parse(body);
                const dir = data.target_dir || path.join(require('os').homedir(), '云枢记忆');
                fs.mkdirSync(dir, { recursive: true });
                const file = path.join(dir, data.filename || '云枢记忆.txt');
                fs.appendFileSync(file, data.content + '\n\n');
                res.end(JSON.stringify({ status:'ok' }));
            } catch (e) {
                res.end(JSON.stringify({ status:'error', message: e.message }));
            }
        });
    } else {
        res.statusCode = 405;
        res.end('Method Not Allowed');
    }
});

server.listen(8123, '127.0.0.1', () => {
    console.log('云枢Agent启动，监听端口8123');
});