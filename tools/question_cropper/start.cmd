@echo off
cd /d "%~dp0\..\.."
echo Open http://127.0.0.1:8766/ in your browser.
echo Keep this window open while reviewing. Press Ctrl+C to stop.
python -X utf8 tools\question_cropper\app.py
if errorlevel 1 pause
