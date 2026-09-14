@echo off
setlocal EnableExtensions
set "PYTHONUTF8=1"
set "PYTHONNOUSERSITE=1"

if /I "%~1"=="--help" goto show_help
if not "%~1"=="" (
    echo ERROR: Unknown argument "%~1".
    goto show_help
)

pushd "%~dp0.."

where conda >nul 2>nul
if errorlevel 1 (
    echo ERROR: Conda was not found in PATH. Run scripts\setup_conda_env.bat first.
    popd
    exit /b 1
)

call conda run --no-capture-output --name local-product-search python -c "import uvicorn, app.main" >nul 2>nul
if errorlevel 1 (
    echo ERROR: The local-product-search environment is missing required libraries.
    echo Run scripts\setup_conda_env.bat, then start this file again.
    popd
    exit /b 1
)

echo Starting Wantnote at http://127.0.0.1:8000
echo Press Ctrl+C to stop the server.
call conda run --no-capture-output --name local-product-search python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
set "APP_EXIT_CODE=%ERRORLEVEL%"
popd
exit /b %APP_EXIT_CODE%

:show_help
echo Usage: %~nx0
echo Starts Wantnote at http://127.0.0.1:8000
exit /b 0
