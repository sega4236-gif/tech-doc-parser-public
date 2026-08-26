@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ========================================
echo    Этап 2: обогащение чанков
echo ========================================
python enrich_chunks.py
pause