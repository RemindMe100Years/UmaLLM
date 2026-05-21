@echo off
color 9
chcp 65001 >nul 2>&1
echo.
echo ========================================
echo   Installing Dependencies
echo ========================================
echo.

set VENV=%~dp0venv

if not exist "%VENV%" (
    echo Creating virtual environment...
    python -m venv "%VENV%"
    echo.
)

call "%VENV%\Scripts\activate.bat"

python -m pip install --upgrade pip
echo.
pip install flask flask-cors waitress litellm
echo.
set /p AWS_CHOICE="Install AWS Bedrock/SageMaker support (botocore)? [Y/n]: "
if /i not "%AWS_CHOICE%"=="n" if /i not "%AWS_CHOICE%"=="N" (
    pip install botocore
    echo.
)

deactivate
echo.
echo ========================================
echo   Installation Complete
echo ========================================
pause
