/**
 * 云枢 日志分析脚本 —— 解析 %APPDATA%\云枢\logs 下的 JSON 日志
 * ----------------------------------------------------------------
 * 统计 renderer-load-failed（对应 did-fail-load）错误次数及分布，便于定位白屏/加载失败。
 * 用法：node scripts/analyze-logs.mjs [logDir]
 *       logDir 默认 %APPDATA%\云枢\logs，可传自定义目录（如 Node 脚本测试临时日志）。
 */
import { readdirSync, readFileSync, existsSync } from 'node:fs';
import path from 'node:path';

const DEFAULT_DIR = path.join(process.env.APPDATA ?? '', '云枢', 'logs');
const logDir = path.resolve(process.argv[2] ?? DEFAULT_DIR);

if (!existsSync(logDir)) {
  console.error(`[analyze-logs] 日志目录不存在: ${logDir}`);
  process.exit(1);
}

const files = readdirSync(logDir).filter((f) => f.endsWith('.log'));
if (files.length === 0) {
  console.log(`[analyze-logs] 日志目录为空: ${logDir}`);
  process.exit(0);
}

let total = 0;
let failed = 0;
let unparsed = 0;
const byCode = new Map();        // 错误码 → 次数
const byDescription = new Map(); // 错误描述 → 次数
const failedLines = [];          // 失败明细（按时间倒序展示最近 20 条）

for (const file of files) {
  const lines = readFileSync(path.join(logDir, file), 'utf8').split(/\r?\n/).filter(Boolean);
  for (const line of lines) {
    let rec;
    try {
      rec = JSON.parse(line);
    } catch {
      unparsed++; // 非 JSON 行（第三方输出等）不计入
      continue;
    }
    total++;
    if (rec.event === 'renderer-load-failed') {
      failed++;
      const code = String(rec.code ?? 'unknown');
      const desc = String(rec.description ?? 'unknown');
      byCode.set(code, (byCode.get(code) ?? 0) + 1);
      byDescription.set(desc, (byDescription.get(desc) ?? 0) + 1);
      failedLines.push({ file, ts: rec.ts ?? '', code, description: desc, url: rec.url ?? '' });
    }
  }
}

// 失败明细按时间倒序（同文件内顺序），展示最近 20 条
failedLines.reverse();
const recent = failedLines.slice(0, 20);

console.log('========== 云枢 日志统计 ==========');
console.log(`日志目录 : ${logDir}`);
console.log(`日志文件 : ${files.length} 个（${files.join(', ')}）`);
console.log(`JSON 日志 : ${total} 条`);
console.log(`did-fail-load (renderer-load-failed) 错误: ${failed} 次`);
if (unparsed > 0) console.log(`非 JSON 行（已忽略）: ${unparsed} 条`);

if (failed > 0) {
  console.log('\n--- 错误码分布 ---');
  for (const [code, n] of [...byCode.entries()].sort((a, b) => b[1] - a[1])) {
    console.log(`  ${code.padEnd(6)} ${n} 次`);
  }
  console.log('\n--- 错误描述分布 ---');
  for (const [desc, n] of [...byDescription.entries()].sort((a, b) => b[1] - a[1])) {
    console.log(`  [${n}x] ${desc}`);
  }
  console.log(`\n--- 最近 ${recent.length} 条失败明细（倒序）---`);
  for (const r of recent) {
    console.log(`  ${r.ts} code=${r.code} desc=${r.description} url=${r.url}（${r.file}）`);
  }
} else {
  console.log('\n🎉 未发现加载失败，应用运行正常。');
}
