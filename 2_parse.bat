@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ========================================
echo    Этап 1: PDF в Markdown
echo ========================================
dir /b *.pdf >nul 2>&1
if errorlevel 1 (
    echo ❌ В папке нет ни одного PDF!
    echo    Положи файл рядом со скриптами и запусти снова.
    pause
    exit /b
)
python parse_pdf.py
pause