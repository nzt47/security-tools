/**
 * pre-commit 安全检查 —— afterPack 剔除清单不得包含核心运行时文件
 * ------------------------------------------------
 * 作用：防止开发者在修改 scripts/afterPack.mjs 时，误把已实测"不可删"的核心文件
 *       （ffmpeg.dll / d3dcompiler_47.dll / elevate.exe）加进 DROP 剔除清单。
 *       一旦误加，安装版会在 detach 独立窗口或渲染时崩溃（实测依据见架构文档 8.2.2）。
 *
 * 独立运行：node scripts/precommit-check.mjs            （检查当前工作区文件）
 * 被 Hook 调用：见 scripts/hooks/pre-commit（git commit 前自动执行）
 * 退出码：0 = 安全；1 = 检测到风险（Hook 会阻止提交）。
 */
import { readFileSync, existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const afterPackPath = process.argv[2] ?? path.join(__dirname, 'afterPack.mjs');

// 核心保留文件（实测不可删，与 afterPack.mjs 注释、report-volume.mjs MUST_BE_KEPT 一致）
const MUST_BE_KEPT = ['ffmpeg.dll', 'd3dcompiler_47.dll', 'elevate.exe'];

function extractDropEntries(source) {
  // 提取 DROP 数组内的字符串字面量（兼容单双引号）
  const match = source.match(/const\s+DROP\s*=\s*\[([\s\S]*?)\];/);
  if (!match) return { entries: [], error: '未找到 DROP 数组定义' };
  const body = match[1];
  const entries = [];
  const re = /['"]([^'"]+)['"]/g;
  let m;
  while ((m = re.exec(body)) !== null) entries.push(m[1]);
  return { entries, error: null };
}

function main() {
  if (!existsSync(afterPackPath)) {
    console.error(`[precommit] afterPack 文件不存在: ${afterPackPath}`);
    process.exit(1);
  }
  const source = readFileSync(afterPackPath, 'utf8');
  const { entries, error } = extractDropEntries(source);
  if (error) {
    console.error(`[precommit] 无法解析 DROP 数组: ${error}`);
    process.exit(1); // 解析失败按风险处理（宁可拦截）
  }

  const dangerous = entries.filter((e) => MUST_BE_KEPT.includes(e));
  if (dangerous.length > 0) {
    console.error(
      '[precommit] ❌ 检测到误删核心文件风险：afterPack.mjs 的 DROP 清单包含已实测"不可删"文件：',
      dangerous.join(', '),
    );
    console.error('  依据：删除 ffmpeg.dll → 渲染进程异常；删除 d3dcompiler_47.dll → detach 独立窗口崩溃；elevate.exe → NSIS 提权助手（见架构文档 8.2.2 / 非核心资源清理分析.md）。');
    console.error('  请从 DROP 中移除这些项后再提交。');
    process.exit(1);
  }

  console.log(`[precommit] ✅ afterPack 剔除清单安全（当前 ${entries.length} 项，无核心文件）。`);
  process.exit(0);
}

main();
