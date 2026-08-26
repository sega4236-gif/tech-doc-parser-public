@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ========================================
echo    Установка (один раз)
echo ========================================
echo [1/3] Проверяю Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo ❌ Python не найден! Скачай с python.org,
    echo    при установке ОБЯЗАТЕЛЬНО поставь "Add Python to PATH".
    pause
    exit /b
)
echo [2/3] Ставлю библиотеки...
pip install pymupdf requests
echo [3/3] Проверяю Ollama и качаю модель (~6 ГБ)...
ollama --version >nul 2>&1
if errorlevel 1 (
    echo ❌ Ollama не найдена! Скачай с ollama.com, установи,
    echo    перезапусти компьютер и запусти 1_install.bat снова.
    pause
    exit /b
)
ollama pull qwen3.5:9b
echo.
echo ✅ Готово! Клади PDF в папку и запускай 2_parse.bat
pause