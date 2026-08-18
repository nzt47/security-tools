/**
 * 云枢 打包体积报告脚本（打包后自动执行）
 * ------------------------------------------------
 * 功能：
 *   1. 统计当前产物体积（安装包 / win-unpacked / asar / 文件数）；
 *   2. 校验 afterPack 剔除项（6 个文件必须不存在）与保留项（ffmpeg/d3dcompiler 必须存在）；
 *   3. 追加历史记录 release/volume-history.json，并生成对比报告 release/volume-report.md。
 *
 * 接入：scripts/dist-electron.mjs 在 electron-builder 成功后自动调用（无需人工执行）。
 * 用法：node scripts/report-volume.mjs [releaseDir]
 *       releaseDir 默认 release/（可用 npm run dist:electron 一键触发）
 */
import { readFileSync, writeFileSync, mkdirSync, existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.join(__dirname, '..');

// 与 scripts/afterPack.mjs 保持一致的剔除/保留清单（【不易】：改清单须同步两处）
const MUST_BE_REMOVED = [
  'LICENSES.chromium.html',
  'dxcompiler.dll',
  'dxil.dll',
  'vk_swiftshader.dll',
  'vk_swiftshader_icd.json',
  'vulkan-1.dll',
];
const MUST_BE_KEPT = [
  'ffmpeg.dll',
  'd3dcompiler_47.dll',
  ['resources', 'elevate.exe'], // 在 resources/ 子目录
];

function dirSizeMB(dir) {
  let total = 0;
  let count = 0;
  const walk = (p) => {
    for (const entry of readdirSafe(p)) {
      const full = path.join(p, entry);
      try {
        const st = statSync(full);
        if (st.isDirectory()) walk(full);
        else { total += st.size; count++; }
      } catch { /* 忽略瞬时错误 */ }
    }
  };
  walk(dir);
  return { totalMB: total / 1024 / 1024, count };
}

// 最小化 fs 导入（避免与 afterPack 的 import 风格冲突，均使用 node:fs）
import { readdirSync, statSync } from 'node:fs';
function readdirSafe(p) {
  try { return readdirSync(p); } catch { return []; }
}

function findInstaller(releaseDir) {
  const exes = readdirSafe(releaseDir).filter((f) => /\.exe$/.test(f) && !/Uninstall/.test(f));
  if (exes.length === 0) return null;
  return path.join(releaseDir, exes[0]);
}

function main() {
  const releaseDir = path.resolve(process.argv[2] ?? path.join(projectRoot, 'release'));
  if (!existsSync(releaseDir)) {
    console.error(`[report-volume] release 目录不存在: ${releaseDir}`);
    process.exit(1);
  }

  const installer = findInstaller(releaseDir);
  if (!installer) {
    console.error('[report-volume] 未找到安装包（*.exe）');
    process.exit(1);
  }
  const installerMB = statSync(installer).size / 1024 / 1024;

  const unpackedDir = path.join(releaseDir, 'win-unpacked');
  const unpacked = existsSync(unpackedDir)
    ? dirSizeMB(unpackedDir)
    : { totalMB: 0, count: 0 };

  const asarPath = path.join(unpackedDir, 'resources', 'app.asar');
  const asarMB = existsSync(asarPath) ? statSync(asarPath).size / 1024 / 1024 : 0;

  // 校验剔除项 / 保留项（保留项支持相对子路径，如 ['resources','elevate.exe']）
  const removedCheck = MUST_BE_REMOVED.map((f) => ({
    file: f,
    ok: !existsSync(path.join(unpackedDir, f)),
  }));
  const keptCheck = MUST_BE_KEPT.map((f) => ({
    file: Array.isArray(f) ? f.join('/') : f,
    ok: existsSync(path.join(unpackedDir, ...(Array.isArray(f) ? f : [f]))),
  }));
  const removedFail = removedCheck.filter((x) => !x.ok);
  const keptFail = keptCheck.filter((x) => !x.ok);
  const allOk = removedFail.length === 0 && keptFail.length === 0;

  // 历史记录
  const histFile = path.join(releaseDir, 'volume-history.json');
  const history = existsSync(histFile)
    ? JSON.parse(readFileSync(histFile, 'utf8'))
    : [];
  const now = new Date();
  const stamp = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')} ${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}`;
  const record = {
    date: stamp,
    installerMB: Number(installerMB.toFixed(2)),
    unpackedMB: Number(unpacked.totalMB.toFixed(1)),
    files: unpacked.count,
    asarMB: Number(asarMB.toFixed(2)),
    check: allOk ? 'PASS' : 'FAIL',
  };
  history.push(record);

  // 生成 markdown 报告
  const table = history
    .map((r, i) => `| ${i + 1} | ${r.date} | ${r.installerMB} | ${r.unpackedMB} | ${r.files} | ${r.asarMB} | ${r.check} |`)
    .join('\n');
  const report = `# 云枢 打包体积报告（自动生成）

> 由 \`scripts/report-volume.mjs\` 在每次 \`npm run dist:electron\` 后自动生成，勿手工编辑。

## 最新一次（${stamp}）

| 指标 | 值 |
|---|---|
| 安装包 | ${installerMB.toFixed(2)} MB |
| win-unpacked | ${unpacked.totalMB.toFixed(1)} MB / ${unpacked.count} 文件 |
| app.asar | ${asarMB.toFixed(2)} MB |
| afterPack 校验 | ${allOk ? '✅ 通过（6 剔除 + 3 保留）' : '❌ 失败：' + [...removedFail.map(x=>x.file), ...keptFail.map(x=>x.file)].join(', ')} |

## 历史对比

| # | 时间 | 安装包(MB) | win-unpacked(MB) | 文件数 | asar(MB) | 校验 |
|---|---|---|---|---|---|---|
${table}
`;
  writeFileSync(path.join(releaseDir, 'volume-report.md'), report, 'utf8');
  writeFileSync(histFile, JSON.stringify(history, null, 2), 'utf8');

  console.log('[report-volume] 安装包:', installerMB.toFixed(2) + 'MB',
    '| win-unpacked:', unpacked.totalMB.toFixed(1) + 'MB / ' + unpacked.count + ' 文件',
    '| asar:', asarMB.toFixed(2) + 'MB');
  console.log('[report-volume] afterPack 校验:', allOk ? '✅ 通过' : '❌ 失败');
  if (!allOk) {
    [...removedFail, ...keptFail].forEach((x) => console.warn('  - 未达标:', x.file));
    process.exitCode = 1;
  }
  console.log('[report-volume] 报告已生成: release/volume-report.md（历史: release/volume-history.json）');
}

main();
