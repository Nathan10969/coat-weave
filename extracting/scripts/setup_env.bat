@echo off
REM Setup script for coating_kg project (Windows cmd version)
REM Usage:
REM   cd /d <repository-root>\extracting
REM   <repository-root>\extracting> scripts\setup_env.bat

setlocal

REM ---- 1. Check conda is available ----
where conda >nul 2>&1
if errorlevel 1 (
    echo.
    echo conda not found in PATH.
    echo.
    echo If you have Anaconda/Miniconda installed, run:
    echo   "%%USERPROFILE%%\miniconda3\Scripts\conda.exe" init cmd.exe
    echo or:
    echo   "%%USERPROFILE%%\anaconda3\Scripts\conda.exe" init cmd.exe
    echo.
    echo Then close + reopen cmd and re-run this script.
    echo If you have not installed conda yet, get Miniconda from:
    echo   https://docs.conda.io/en/latest/miniconda.html
    exit /b 1
)

REM ---- 2. Create env if not exists ----
conda env list | findstr /B /C:"coating " >nul 2>&1
if errorlevel 1 (
    echo ===^> Creating conda env 'coating' with Python 3.11...
    call conda create -n coating python=3.11 -y
    if errorlevel 1 (
        echo Failed to create conda env. Check error above.
        exit /b 1
    )
) else (
    echo ===^> conda env 'coating' already exists, skipping create.
)

REM ---- 3. Install dependencies ----
echo.
echo ===^> Installing dependencies into 'coating' env...
call conda run --no-capture-output -n coating pip install -r requirements.txt
if errorlevel 1 (
    echo Failed to install dependencies. Check error above.
    exit /b 1
)

REM ---- 4. Done ----
echo.
echo Setup complete.
echo.
echo Next steps (run in this same cmd window):
echo   conda activate coating
echo   python scripts\test_qwen.py
echo   python scripts\smoke_test_mineru.py

endlocal
