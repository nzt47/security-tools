/**
 * 分析 app.asar 体积构成（临时诊断脚本）
 * 用法：node scripts/analyze-asar.mjs
 */
import { listPackage } from '@electron/asar';
import { statSync } from 'node:fs';

const asarPath = 'c:/Users/Administrator/agent/yunshu-ui/release/win-unpacked/resources/app.asar';
const list = listPackage(asarPath);

// 逐文件统计实际字节（electron-builder asar 内每个文件都是独立 header 条目）
// listPackage 返回路径数组；用 extractFile 判断大小成本高，改为按目录前缀分类统计条目数
// 说明：此处用"文件数占比"近似；真实字节需 readFileSync 全量遍历（体积大，跳过）
const dirs = new Map();
for (const f of list) {
  const top = f.replace(/\\/g, '/').split('/')[1] ?? '(root)';
  dirs.set(top, (dirs.get(top) ?? 0) + 1);
}
console.log('=== app.asar 顶层目录文件数统计 ===');
for (const [k, v] of [...dirs.entries()].sort((a, b) => b[1] - a[1])) {
  console.log(`  ${k}: ${v}`);
}

console.log('\n=== 关键文件大小 ===');
const key = [
  'dist/index.html',
  'dist/assets/index-BXOOpgCe.js',
  'dist/assets/index-BXOOpgCe.js.map',
  'dist/assets/index-B8GYUcNy.css',
  'dist-electron/main.js',
  'dist-electron/preload.cjs',
  'package.json',
];
for (const f of key) {
  if (list.includes('\\' + f)) console.log(`  ${f}: 存在`);
}
