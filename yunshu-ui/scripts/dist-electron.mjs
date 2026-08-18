/**
 * 云枢 Electron 一键打包脚本（跨平台）
 * ------------------------------------------------
 * 流程：设 ELECTRON=1 → vite build（renderer + dist-electron 主进程/预加载）
 *       → electron-builder 打包为安装包（release/ 目录）
 *       → report-volume 自动生成体积对比报告（release/volume-report.md）
 * 说明：afterPack 剔除逻辑由 build.afterPack 固化，随 electron-builder 自动执行；
 *       本脚本在打包成功后额外调用 scripts/report-volume.mjs 做剔除校验与体积归档。
 * 用法：npm run dist:electron
 */
import { spawnSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

process.env.ELECTRON = '1';

function run(cmd, args) {
  console.log(`\n[dist:electron] → ${cmd} ${args.join(' ')}`);
  const res = spawnSync(cmd, args, { stdio: 'inherit', shell: process.platform === 'win32' });
  return res.status;
}

// 1. 构建 renderer + electron 壳（Electron 模式：base='./'，产出 dist/ 与 dist-electron/）
const buildStatus = run('npx', ['vite', 'build']);
if (buildStatus !== 0) process.exit(buildStatus ?? 1);

// 2. electron-builder 打包（afterPack 钩子自动执行剔除 + 校验；配置见 package.json "build"）
const packStatus = run('npx', ['electron-builder']);
if (packStatus !== 0) process.exit(packStatus ?? 1);

// 3. 自动生成体积对比报告 + 二次校验（报告与历史归档至 release/）
const reportStatus = run('node', [path.join(__dirname, 'report-volume.mjs')]);
process.exit(reportStatus ?? 0);
