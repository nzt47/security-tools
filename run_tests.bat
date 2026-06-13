# 云枢系统测试运行脚本
# 提供便捷的测试执行命令

@echo off
setlocal enabledelayedexpansion

echo ========================================
echo 云枢系统测试运行脚本
echo ========================================
echo.

:: 检查Python环境
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到Python，请先安装Python 3.9+
    exit /b 1
)

:: 检查pytest是否安装
pip show pytest >nul 2>&1
if errorlevel 1 (
    echo [提示] 正在安装pytest和相关依赖...
    pip install pytest pytest-cov pytest-mock pytest-asyncio
)

:: 创建测试报告目录
if not exist "test_reports" mkdir "test_reports"

:: 解析命令行参数
set "TEST_MODE=all"
set "COVERAGE=true"
set "REPORT=true"

:parse_args
if "%~1"=="" goto run_tests
if /i "%~1"=="unit" set "TEST_MODE=unit"
if /i "%~1"=="integration" set "TEST_MODE=integration"
if /i "%~1"=="quick" set "TEST_MODE=quick"
if /i "%~1"=="all" set "TEST_MODE=all"
if /i "%~1"=="--no-coverage" set "COVERAGE=false"
if /i "%~1"=="--no-report" set "REPORT=false"
if /i "%~1"=="--help" goto show_help
shift
goto parse_args

:run_tests
echo [配置] 测试模式: %TEST_MODE%
echo [配置] 覆盖率统计: %COVERAGE%
echo [配置] 生成报告: %REPORT%
echo.

:: 构建pytest命令
set "PYTEST_CMD=pytest"

if "%TEST_MODE%"=="unit" (
    set "PYTEST_CMD=!PYTEST_CMD! tests/unit/"
) else if "%TEST_MODE%"=="integration" (
    set "PYTEST_CMD=!PYTEST_CMD! tests/integration/"
) else if "%TEST_MODE%"=="quick" (
    set "PYTEST_CMD=!PYTEST_CMD! -m "not slow""
) else (
    set "PYTEST_CMD=!PYTEST_CMD! tests/"
)

:: 添加覆盖率选项
if "%COVERAGE%"=="true" (
    set "PYTEST_CMD=!PYTEST_CMD! --cov=agent --cov-report=html --cov-report=term-missing --cov-report=xml"
)

:: 添加报告选项
if "%REPORT%"=="true" (
    set "PYTEST_CMD=!PYTEST_CMD! --html=test_reports/html_report.html --self-contained-html"
)

:: 添加详细输出
set "PYTEST_CMD=!PYTEST_CMD! -v --tb=short"

echo [执行] !PYTEST_CMD!
echo.
echo ========================================
echo.

:: 执行测试
!PYTEST_CMD!

set "TEST_EXIT_CODE=!ERRORLEVEL!"

echo.
echo ========================================
echo 测试执行完成
echo ========================================
echo.

:: 检查测试结果
if !TEST_EXIT_CODE! equ 0 (
    echo [结果] 所有测试通过！
    echo.
    echo 覆盖率报告已生成: test_reports/htmlcov/index.html
) else (
    echo [结果] 测试失败，退出码: !TEST_EXIT_CODE!
    echo.
    echo 请查看上述测试输出了解失败原因。
)

:: 运行覆盖率检查
if "%COVERAGE%"=="true" (
    echo.
    echo [覆盖率检查]
    python tests/coverage_checker.py
)

exit /b !TEST_EXIT_CODE!

:show_help
echo 使用方法: run_tests.bat [模式] [选项]
echo.
echo 模式:
echo   unit        - 仅运行单元测试
echo   integration - 仅运行集成测试
echo   quick       - 仅运行快速测试（不含slow标记）
echo   all         - 运行所有测试（默认）
echo.
echo 选项:
echo   --no-coverage - 禁用覆盖率统计
echo   --no-report   - 禁用HTML报告生成
echo   --help        - 显示此帮助信息
echo.
echo 示例:
echo   run_tests.bat unit
echo   run_tests.bat integration --no-coverage
echo   run_tests.bat quick
exit /b 0
