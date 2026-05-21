@echo off
color 9
chcp 65001 >nul 2>&1
echo.
echo ========================================
echo   Translation API Server (Standalone)
echo ========================================
echo.

set VENV=%~dp0venv

if not exist "%VENV%" (
    echo Error: Virtual environment not found. Run setup.bat first.
    pause
    exit /b 1
)

call "%VENV%\Scripts\activate.bat"

for /f "tokens=2 delims=:" %%A in ('findstr "HTTP_port_number" "%~dp0settings.json"') do (
    set "PORT=%%A"
)
set PORT=%PORT:~1%
set PORT=%PORT: =%
set PORT=%PORT:"=%
set PORT=%PORT:,=%

set /a PORT=%PORT% 2>nul || (
    echo Error: Invalid port number in settings.json: [%PORT%]
    pause
    exit /b 1
)

echo   Port: %PORT%
echo   Press Ctrl+C to stop
echo.

python -s -E "%~dp0server.py"

deactivate
pause
