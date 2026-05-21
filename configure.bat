@echo off
chcp 65001 >nul 2>&1
color 9

set VENV=%~dp0venv

if not exist "%VENV%" (
    echo Error: Virtual environment not found. Run setup.bat first.
    pause
    exit /b 1
)

call "%VENV%\Scripts\activate.bat"
powershell -ExecutionPolicy Bypass -NoProfile -File "%~dp0configure.ps1"
deactivate
pause
