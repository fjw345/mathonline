@echo off
chcp 65001 >nul
cd /d "%~dp0"
python -B -X utf8 tools\image_bank_workbench\app.py --port 8767 --open-browser
pause
