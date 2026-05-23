@echo off
color 9
chcp 65001 >nul 2>&1
echo.
echo ========================================
echo   Installing Dependencies
echo ========================================
echo.

cd /d "%~dp0"

if not exist "venv" (
    echo Creating virtual environment...
    python -m venv venv
    echo.
)

call "venv\Scripts\activate.bat"

python -m pip install --upgrade pip
echo.
pip install flask flask-cors waitress litellm
echo.
set /p AWS_CHOICE="Install AWS Bedrock/SageMaker support (botocore)? [Y/n]: "
if /i not "%AWS_CHOICE%"=="n" if /i not "%AWS_CHOICE%"=="N" (
    pip install botocore
    echo.
)

set /p JAM_CHOICE="Install Jamdict (Japanese dictionary for LLM sanity checks)? [y/N]: "
if /i "%JAM_CHOICE%"=="y" if /i "%JAM_CHOICE%"=="Y" (
    echo Installing jamdict...
    pip install jamdict
    echo.
    if not exist "%USERPROFILE%\.jamdict\data\jamdict.db" (
        echo Downloading jamdict database (~310 MB)...
        python -c "import urllib.request, os, tarfile, tempfile, shutil, lzma; url='https://files.pythonhosted.org/packages/source/j/jamdict-data/jamdict_data-1.5.tar.gz'; tmp=tempfile.mkdtemp(); tar_path=os.path.join(tmp, 'jamdict_data.tar.gz'); urllib.request.urlretrieve(url, tar_path); tar=tarfile.open(tar_path, 'r:gz'); db_dir=os.path.expanduser('~/.jamdict/data'); db_path=os.path.join(db_dir, 'jamdict.db'); xz_path=os.path.join(tmp, 'jamdict.db.xz'); [f_out.write(tar.extractfile(m).read()) for m in tar.getmembers() if m.name.endswith('jamdict.db.xz') for f_out in [open(xz_path, 'wb')]]; os.makedirs(db_dir, exist_ok=True); [f_out.write(lzma.open(xz_path, 'rb').read()) for f_out in [open(db_path, 'wb')]]; shutil.rmtree(tmp); print('Done:', os.path.getsize(db_path), 'bytes')"
        echo.
    )
)

deactivate
echo.
echo ========================================
echo   Installation Complete
echo ========================================
pause
