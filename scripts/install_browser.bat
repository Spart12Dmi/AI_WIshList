@echo off
setlocal EnableExtensions
set "PYTHONNOUSERSITE=1"

where conda >nul 2>nul
if errorlevel 1 (
    echo ERROR: Conda was not found in PATH.
    exit /b 1
)

echo Installing the Chromium browser used by the product-page fallback...
call conda run --no-capture-output --name local-product-search python -m playwright install chromium
if errorlevel 1 (
    echo ERROR: Chromium installation failed. Run scripts\setup_conda_env.bat first.
    exit /b 1
)
echo Chromium is ready. Start the app with scripts\run_app.bat
