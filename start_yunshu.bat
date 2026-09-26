@echo off
title Yunshu One-Click Start
echo.
echo  ======================================================
echo    Yunshu One-Click Start
echo    Backend API  : http://localhost:5678
echo    Frontend Dev : http://localhost:5173/static/
echo    Close the three popup console windows (backend / watchdog / frontend)
echo    to stop everything. Closing only the watchdog window leaves the backend up.
echo  ======================================================
echo.

REM ---- 仓库根 = 本脚本所在目录（可移植） ----
REM 【TASK-02 · W1 仓库卫生】原第 12 行是硬编码的
REM   set ROOT=C:\Users\Administrator\agent
REM 这正是本文件此前被 .gitignore 忽略的原因：机器特定。改用 %~dp0
REM （脚本自身所在目录，含末尾反斜杠）后不再机器特定 ⇒ 已从忽略名单移除，
REM 新克隆者可直接使用同一个入口。子进程的工作目录由 start 的 /D 参数指定，
REM 避免 cmd /k 里嵌套引号的解析坑（路径含空格时依然正确，已实测）。
set "ROOT=%~dp0"

REM ---- 0. If backend is already healthy, do NOT start a duplicate ----
REM     NOTE (A1 2026-09-25 更新): app_server.py still ends up replacing
REM     whatever listens on 5678, but it no longer kills the old instance at
REM     the very start of its boot. It first runs its whole fallible init and
REM     only then, behind a readiness gate (in-process /api/health must return
REM     200), kills the old instance and binds. So a duplicate launch that
REM     fails early now leaves the running backend untouched; one that DOES
REM     become ready still takes over the port. Either way, do not launch a
REM     duplicate: a successful takeover means a ~1s window where neither
REM     instance serves (measured 0.34s on this machine). See
REM     docs/audit_skill_governance/A1.md.
curl -s -f -o nul --max-time 2 http://127.0.0.1:5678/api/health >nul 2>&1
if not errorlevel 1 goto backend_up
echo  [1/3] Starting backend (cold start takes about 60-90s)...
start "Yunshu-Backend-5678" /D "%ROOT%" cmd /k "python app_server.py"

REM ---- 1.b Watchdog (A1): keeps the backend alive ----
REM     Probes http://127.0.0.1:5678/api/health every 30s; after 3 consecutive
REM     failures it kills the stale listener and re-launches app_server.py,
REM     appending a JSON Lines trace to logs\watchdog_yunshu.jsonl
REM     (ts / reason / old_pid / new_pid). Started only on the branch where we
REM     actually start the backend, so a second watchdog is not spawned when a
REM     healthy backend is already up. The script itself also guards against
REM     double-running via logs\watchdog_yunshu.lock.
echo  [1/3] Starting backend watchdog...
start "Yunshu-Watchdog" /D "%ROOT%" cmd /k "python scripts\watchdog_yunshu.py"
:backend_up

REM ---- 2. Start frontend only if port 5173 is free ----
curl -s -f -o nul --max-time 2 http://127.0.0.1:5173/static/ >nul 2>&1
if not errorlevel 1 goto frontend_up
echo  [2/3] Starting frontend Vite dev server...
start "Yunshu-Frontend-5173" /D "%ROOT%yunshu-ui" cmd /k "npm run dev"
:frontend_up

REM ---- 3. Poll until the backend API is REALLY ready, then open browser ----
echo  [3/3] Waiting for backend API on http://127.0.0.1:5678/api/health ...
set /a _tries=0
:wait_backend
set /a _tries+=1
if %_tries% gtr 60 (
    echo  [WARN] Backend not ready after ~120s. Check the "Yunshu-Backend-5678" window for errors.
    goto open_browser
)
curl -s -f -o nul --max-time 2 http://127.0.0.1:5678/api/health >nul 2>&1
if errorlevel 1 (
    ping -n 3 127.0.0.1 >nul
    goto wait_backend
)
echo  OK Backend API is ready, opening workbench...
:open_browser
echo.
start "" "http://localhost:5173/static/#/workbench"

echo.
echo  Started. Keep the three popup console windows open
echo  (Yunshu-Backend-5678 / Yunshu-Watchdog / Yunshu-Frontend-5173).
echo  Closing the backend window stops the API; the watchdog will then bring
echo  it back within ~3 health checks (default 90s) plus cold start.
echo.
pause
