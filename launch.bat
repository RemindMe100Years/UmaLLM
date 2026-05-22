@echo off
color 9
chcp 65001 >nul 2>&1
echo.

set VENV=%~dp0venv

if not exist "%VENV%" (
    echo Error: Virtual environment not found. Run setup.bat first.
    pause
    exit /b 1
)

call "%VENV%\Scripts\activate.bat"

python -s -E "%~dp0server.py"

deactivate
pause
