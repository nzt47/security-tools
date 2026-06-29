@echo off
chcp 65001 >nul
cd /d "%~dp0\.."
call venv\Scripts\activate.bat
python scripts\daily_quality_report_scheduler.py
deactivate