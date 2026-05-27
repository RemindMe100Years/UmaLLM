@echo off
color 9
chcp 65001 >nul

set "VENV=%~dp0venv"

if not exist "%VENV%" (
    echo Error: Virtual environment not found. Run setup.bat first.
    pause
    exit /b 1
)

call "%VENV%\Scripts\activate.bat"

echo.
findstr /c:"\"jamdict_sanity_check\": true" "%~dp0scripts\settings.json" >nul 2>&1 && (python "%~dp0scripts\test_jamdict.py" 2>nul || echo Warning: Jamdict test failed - sanity check will be disabled)

python -s -E "%~dp0scripts\server.py"

deactivate
pause
