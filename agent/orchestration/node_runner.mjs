// E4-1 第一期 runner：把模型写的 JS 关进 vm 沙箱，只暴露 tools.call / spawn /
// console / sleep。协议：stdout 每行一个 JSON，stdin 每行一个 JSON（宿主回写）。
//
// 【不易·脚本拿不到 require/process】用户脚本经 vm.runInContext 在**新 realm** 里执行，
// 沙箱对象只含四个冻结成员；node 侧再用 --permission 削掉 fs 越界与网络。
// 【不易·输出双计】runner 自己也算 stdout+stderr 字节（宿主另算一遍，防绕过）。
// 【简易·退出】done/error 后只销毁 stdin，让事件循环自然退出（立刻 process.exit
//   会截断还没冲出去的 stdout 管道）。
'use strict';

import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const scriptPath = process.argv[2];
const maxOutput = Number(process.env.ORCH_MAX_OUTPUT || '1048576');
const maxResultChars = Number(process.env.ORCH_RESULT_CHARS || '8000');

let outBytes = 0;
let nextId = 1;
const pending = new Map();

function countBytes(text) {
  const size = Buffer.byteLength(text, 'utf8');
  outBytes += size;
  return outBytes > maxOutput;
}

function writeLine(obj) {
  const text = JSON.stringify(obj) + '\n';
  const over = countBytes(text);
  process.stdout.write(text);
  if (over) {
    // 输出洪泛：宿主会看到 cap 行并用 proc_tree 杀树（这里先自己停手）。
    try { process.stderr.write('[orchestration] output cap exceeded\n'); } catch (e) {}
    process.exit(3);
  }
}

function writeStderr(text) {
  const body = String(text) + '\n';
  const over = countBytes(body);
  try { process.stderr.write(body); } catch (e) {}
  if (over) {
    writeLine({ t: 'cap', kind: 'output' });
    process.exit(3);
  }
}

function safeJson(value) {
  try { return JSON.stringify(value); } catch (e) { return String(value); }
}

function shrink(value) {
  let text;
  try { text = JSON.stringify(value === undefined ? null : value); } catch (e) { text = safeJson(String(value)); }
  if (typeof text !== 'string') return null;
  if (text.length <= maxResultChars) {
    try { return JSON.parse(text); } catch (e) { return text; }
  }
  return { truncated: true, chars: text.length, text: text.slice(0, maxResultChars) };
}

const consoleApi = Object.freeze({
  log: function () { writeStderr(Array.prototype.map.call(arguments, function (a) {
    return typeof a === 'string' ? a : safeJson(a);
  }).join(' ')); },
  error: function () { writeStderr(Array.prototype.map.call(arguments, function (a) {
    return typeof a === 'string' ? a : safeJson(a);
  }).join(' ')); },
  warn: function () { writeStderr(Array.prototype.map.call(arguments, function (a) {
    return typeof a === 'string' ? a : safeJson(a);
  }).join(' ')); },
});

function rpc(message) {
  return new Promise(function (resolve) {
    const id = nextId++;
    pending.set(id, resolve);
    const payload = Object.assign({ id: id }, message);
    writeLine(payload);
  });
}

function toEnvelope(message) {
  if (message && message.ok) {
    const out = { ok: true, value: message.value };
    if (message.truncated) out.truncated = true;
    return out;
  }
  return { ok: false, code: (message && message.code) || 'tool_error',
           error: (message && message.error) || '' };
}

const call = function (name, args) {
  const safeArgs = (args && typeof args === 'object' && !Array.isArray(args)) ? args : {};
  return rpc({ t: 'call', name: String(name || ''), args: safeArgs }).then(toEnvelope);
};

const spawn = function (tasks, options) {
  const list = Array.isArray(tasks) ? tasks : [];
  const opts = (options && typeof options === 'object') ? options : {};
  const message = { t: 'spawn', tasks: list };
  if (opts.max_concurrency !== undefined) message.max_concurrency = opts.max_concurrency;
  return rpc(message).then(toEnvelope);
};

const sleep = function (ms) {
  const wait = Math.max(0, Math.min(Number(ms) || 0, 60000));
  return new Promise(function (resolve) { setTimeout(resolve, wait); });
};

const tools = Object.freeze({ call: call });
const sandbox = {
  tools: tools,
  spawn: spawn,
  console: consoleApi,
  sleep: sleep,
};

function handleHostLine(line) {
  const text = String(line || '').trim();
  if (!text) return;
  let message;
  try { message = JSON.parse(text); } catch (e) { return; }
  if (!message || message.t !== 'result') return;
  const resolve = pending.get(message.id);
  if (!resolve) return;
  pending.delete(message.id);
  resolve(message);
}

let buffer = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', function (chunk) {
  buffer += chunk;
  let index;
  while ((index = buffer.indexOf('\n')) >= 0) {
    handleHostLine(buffer.slice(0, index));
    buffer = buffer.slice(index + 1);
  }
});

function finish(code) {
  writeStderr('[orchestration] finished code=' + String(code));
  try { process.stdin.destroy(); } catch (e) {}
  process.exitCode = code;
}

process.on('unhandledRejection', function (reason) {
  writeLine({ t: 'error', ok: false,
              message: String((reason && reason.message) || reason).slice(0, 2000) });
  finish(1);
});

let source = '';
try {
  source = readFileSync(scriptPath, 'utf8');
} catch (e) {
  writeLine({ t: 'error', ok: false, message: 'script_unreadable' });
  finish(1);
}

if (source) {
  try {
    const wrapped = '(async () => {\n' + source + '\n})()';
    const context = vm.createContext(sandbox, { name: 'orchestration' });
    const running = vm.runInContext(wrapped, context, { filename: 'main.mjs', displayErrors: true });
    Promise.resolve(running).then(function (value) {
      writeLine({ t: 'done', ok: true, result: shrink(value) });
      finish(0);
    }).catch(function (err) {
      const message = String((err && err.message) || err).slice(0, 2000);
      const stack = String((err && err.stack) || '').slice(-4000);
      writeLine({ t: 'error', ok: false, message: message, stack: stack });
      finish(1);
    });
  } catch (err) {
    writeLine({ t: 'error', ok: false,
                message: String((err && err.message) || err).slice(0, 2000),
                stack: String((err && err.stack) || '').slice(-4000) });
    finish(1);
  }
}
