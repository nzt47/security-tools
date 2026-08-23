/**
 * 云枢 自动化安装测试脚本（Windows）
 * ------------------------------------------------------------------
 * 模拟用户首次安装并启动应用，验证：
 *   1. NSIS 安装向导可静默安装（/D= 指定干净目录，规避旧安装目录记忆干扰）
 *   2. 安装目录包含卸载程序（Uninstall 云枢.exe）→ 验证卸载功能完整
 *   3. 启动应用后 %APPDATA%\云枢\logs 自动创建并按 JSON 记录 window-created
 *   4. 卸载后应用退出、安装目录清理
 *
 * 用法：node scripts/install-test.mjs [installer.exe] [installDir]
 *       默认安装包 release/云枢 Setup 0.1.0.exe；默认目录 %TEMP%\yunshu-install-test
 * 退出码：0=全部通过，1=任一环节失败
 */
import { execFileSync, spawn } from 'node:child_process';
import { existsSync, readdirSync, readFileSync, rmSync } from 'node:fs';
import path from 'node:path';
import os from 'node:os';

const DEFAULT_INSTALLER = path.resolve('release', '云枢 Setup 0.1.0.exe');
const installer = path.resolve(process.argv[2] ?? DEFAULT_INSTALLER);
const installDir = path.resolve(
  process.argv[3] ?? path.join(os.tmpdir(), 'yunshu-install-test'),
);
const appExe = path.join(installDir, '云枢.exe');
const uninstaller = path.join(installDir, 'Uninstall 云枢.exe');
const logDir = path.join(process.env.APPDATA ?? '', '云枢', 'logs');

let failures = 0;
let passed = 0;

function check(name, ok, detail = '') {
  if (ok) {
    passed++;
    console.log(`  ✅ ${name}${detail ? `（${detail}）` : ''}`);
  } else {
    failures++;
    console.error(`  ❌ ${name}${detail ? `（${detail}）` : ''}`);
  }
}

function waitUntil(desc, fn, timeoutMs = 60_000, intervalMs = 1000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    if (fn()) return true;
    Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, intervalMs);
  }
  console.error(`    [超时] 等待 ${desc} 超过 ${timeoutMs / 1000}s`);
  return false;
}

/** 读取最新日志文件中指定 event 的记录（无则 null） */
function findLatestLogEvent(event) {
  if (!existsSync(logDir)) return null;
  const files = readdirSync(logDir).filter((f) => f.endsWith('.log')).sort();
  if (files.length === 0) return null;
  const content = readFileSync(path.join(logDir, files[files.length - 1]), 'utf8');
  const lines = content.split(/\r?\n/).filter(Boolean);
  for (let i = lines.length - 1; i >= 0; i--) {
    try {
      const rec = JSON.parse(lines[i]);
      if (rec.event === event) return rec;
    } catch { /* 非 JSON 行跳过 */ }
  }
  return null;
}

async function main() {
  console.log('========== 云枢 自动化安装测试 ==========');
  console.log(`安装包  : ${installer}`);
  console.log(`安装目录: ${installDir}`);
  console.log(`日志目录: ${logDir}`);

  // 0. 预检 + 清理测试目录
  check('安装包存在', existsSync(installer), path.basename(installer));
  if (!existsSync(installer)) { console.error('缺少安装包，终止测试'); process.exit(1); }
  if (existsSync(installDir)) rmSync(installDir, { recursive: true, force: true });

  // 1. 静默安装（/D= 指定目录，NSIS 要求放参数末尾）
  console.log('静默安装中...');
  try {
    execFileSync(installer, ['/S', `/D=${installDir}`], { stdio: 'ignore', timeout: 120_000 });
  } catch (e) {
    check('静默安装执行成功', false, String(e.message));
    process.exit(1);
  }
  check('安装完成（云枢.exe 就位）', waitUntil('安装完成', () => existsSync(appExe), 60_000));

  // 2. 卸载程序（NSIS 卸载功能验证）
  check('卸载程序已集成（Uninstall 云枢.exe）', existsSync(uninstaller));

  // 3. 启动应用并验证日志自动创建
  const baselineTs = findLatestLogEvent('window-created')?.ts ?? '';
  console.log('启动应用，等待日志自动创建...');
  const child = spawnDetached(appExe);
  void child;
  const logCreated = waitUntil(
    '新的 window-created 日志',
    () => {
      const rec = findLatestLogEvent('window-created');
      return !!rec && rec.ts !== baselineTs && !!rec.ts;
    },
    30_000,
    1000,
  );
  check('启动后 %APPDATA%\\云枢\\logs 自动写入 window-created', logCreated);
  if (logCreated) {
    const rec = findLatestLogEvent('window-created');
    check(
      '日志格式为 JSON 且含关键字段',
      !!rec?.ts && rec.module === 'main' && !!rec?.width && !!rec?.title,
      `event=${rec.event} module=${rec.module} title=${rec.title}`,
    );
  }

  // 4. 结束应用进程（为卸载做准备）
  killApp();

  // 5. 静默卸载并验证清理
  console.log('卸载中...');
  try {
    execFileSync(uninstaller, ['/S'], { stdio: 'ignore', timeout: 60_000 });
  } catch (e) {
    check('卸载执行成功', false, String(e.message));
  }
  check('卸载后安装目录清理', waitUntil('卸载完成', () => !existsSync(appExe), 60_000));
  check('卸载后无残留进程', !appProcessAlive());

  console.log('\n========== 测试结果 ==========');
  console.log(`通过: ${passed} 项，失败: ${failures} 项`);
  process.exit(failures > 0 ? 1 : 0);
}

/** 分离启动应用进程（不阻塞） */
function spawnDetached(exe) {
  const child = spawn(exe, [], { detached: true, stdio: 'ignore' });
  child.unref();
  return child;
}

function killApp() {
  try {
    execFileSync('taskkill', ['/IM', '云枢.exe', '/F', '/T'], { stdio: 'ignore' });
  } catch { /* 已退出 */ }
}

function appProcessAlive() {
  try {
    execFileSync('taskkill', ['/IM', '云枢.exe', '/F', '/T'], { stdio: 'ignore' });
    return true; // 还能杀掉 = 进程存活
  } catch {
    return false;
  }
}

main().catch((e) => {
  console.error('[install-test] 异常:', e);
  process.exit(1);
});
