/**
 * fix-gitignore.mjs —— 检测并修复 yunshu-ui/.gitignore
 * ------------------------------------------------
 * 目标：确保排除所有构建产物与本地配置，防止误提交（如 dist/、release/、.env.production）。
 * 行为（幂等）：
 *   - 逐项检测必需排除规则，缺失则按分组追加
 *   - 已存在的不重复写入，可安全反复执行
 * 用法：node scripts/fix-gitignore.mjs
 */
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const root = join(dirname(fileURLToPath(import.meta.url)), '..'); // yunshu-ui/
const gitignorePath = join(root, '.gitignore');

/** 必需排除规则分组：注释 + 规则列表 */
const REQUIRED_GROUPS = [
  {
    comment: '# 构建产物（Vite / Electron / electron-builder）',
    rules: ['dist/', 'dist-ssr/', 'dist-electron/', 'release/', 'node_modules/'],
  },
  {
    comment: '# 本地配置与密钥（保留 .env.example 作为模板）',
    rules: ['.env', '.env.*', '!.env.example', '*.local'],
  },
  {
    comment: '# 覆盖率 / 测试临时产物',
    rules: ['coverage/', '.nyc_output/', '.pytest_cache/'],
  },
];

let gitignore = '';
if (existsSync(gitignorePath)) {
  gitignore = readFileSync(gitignorePath, 'utf8');
}
const existingLines = new Set(gitignore.split(/\r?\n/).map((l) => l.trim()).filter(Boolean));
const added = [];
const already = [];

for (const group of REQUIRED_GROUPS) {
  const missing = group.rules.filter((r) => !existingLines.has(r));
  if (missing.length === 0) {
    already.push(...group.rules);
    continue;
  }
  // 追加分组（含注释头），保持可读性
  added.push(...missing);
  gitignore += `\n${group.comment}\n${missing.join('\n')}\n`;
}

if (added.length > 0) {
  writeFileSync(gitignorePath, gitignore, 'utf8');
}

console.log('=== yunshu-ui/.gitignore 修复报告 ===');
console.log(`新增 ${added.length} 条：\n  ${added.join('\n  ')}`);
console.log(`已存在 ${already.length} 条：${already.join(', ')}`);
console.log(added.length > 0 ? '✓ 已写入修复' : '✓ 无需修复');
