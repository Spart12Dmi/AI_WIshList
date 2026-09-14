@echo off
setlocal EnableExtensions EnableDelayedExpansion
set "PYTHONNOUSERSITE=1"
set "PYTHONUTF8=1"

rem Creates the local product-search environment on Windows.
rem Usage:
rem   scripts\setup_conda_env.bat
rem   scripts\setup_conda_env.bat --pull-model
rem   scripts\setup_conda_env.bat --model qwen3:1.7b --pull-model
rem   scripts\setup_conda_env.bat --recreate

set "ENVIRONMENT_NAME=local-product-search"
set "PYTHON_VERSION=3.11"
set "MODEL_NAME=qwen3:4b"
set "PULL_MODEL=0"
set "INSTALL_BROWSER=0"
set "RECREATE=0"
set "SCRIPT_DIRECTORY=%~dp0"
set "REQUIREMENTS_FILE=%SCRIPT_DIRECTORY%..\requirements.txt"

:parse_arguments
if "%~1"=="" goto arguments_parsed
if /I "%~1"=="--pull-model" (
    set "PULL_MODEL=1"
    shift
    goto parse_arguments
)
if /I "%~1"=="--install-browser" (
    set "INSTALL_BROWSER=1"
    shift
    goto parse_arguments
)
if /I "%~1"=="--recreate" (
    set "RECREATE=1"
    shift
    goto parse_arguments
)
if /I "%~1"=="--model" (
    if "%~2"=="" (
        echo ERROR: --model requires an Ollama model name.
        exit /b 1
    )
    set "MODEL_NAME=%~2"
    shift
    shift
    goto parse_arguments
)
if /I "%~1"=="--help" goto show_help
echo ERROR: Unknown argument "%~1".
goto show_help

:arguments_parsed
where conda >nul 2>nul
if errorlevel 1 (
    echo ERROR: Conda was not found in PATH.
    echo Install Miniconda or Anaconda, open a new Command Prompt, and run this file again.
    exit /b 1
)

if "%RECREATE%"=="1" (
    echo Removing environment "%ENVIRONMENT_NAME%" if it exists...
    call conda env remove --yes --name "%ENVIRONMENT_NAME%"
    if errorlevel 1 (
        echo NOTE: No existing environment was removed. Continuing with setup.
    )
)

call conda env list | findstr /I /R /C:"^[* ]*%ENVIRONMENT_NAME% " >nul
if errorlevel 1 (
    echo Creating Conda environment "%ENVIRONMENT_NAME%" with Python %PYTHON_VERSION%...
    call conda create --yes --name "%ENVIRONMENT_NAME%" "python=%PYTHON_VERSION%" pip
    if errorlevel 1 goto conda_failed
) else (
    echo Using existing Conda environment "%ENVIRONMENT_NAME%".
)

echo Upgrading pip tooling...
call conda run --no-capture-output --name "%ENVIRONMENT_NAME%" python -m pip install --no-user --upgrade pip setuptools wheel
if errorlevel 1 goto pip_failed

echo Installing PyTorch with CUDA 12.8 support...
call conda run --no-capture-output --name "%ENVIRONMENT_NAME%" python -m pip install --no-user torch==2.10.0 torchvision==0.25.0 torchaudio==2.10.0 --index-url https://download.pytorch.org/whl/cu128
if errorlevel 1 goto pip_failed

echo Installing application libraries...
call conda run --no-capture-output --name "%ENVIRONMENT_NAME%" python -m pip install --no-user --requirement "%REQUIREMENTS_FILE%"
if errorlevel 1 goto pip_failed

echo.
echo Verifying Python, PyTorch, and CUDA...
call conda run --no-capture-output --name "%ENVIRONMENT_NAME%" python -c "import sys, torch; print(f'Python: {sys.version.split()[0]}'); print(f'PyTorch: {torch.__version__}'); print(f'PyTorch CUDA build: {torch.version.cuda}'); print(f'CUDA available: {torch.cuda.is_available()}'); print(f'GPU: {torch.cuda.get_device_name(0)}' if torch.cuda.is_available() else 'GPU: not detected')"
if errorlevel 1 goto verification_failed

if "%PULL_MODEL%"=="1" (
    call :find_ollama
    if not defined OLLAMA_EXECUTABLE (
        echo ERROR: Ollama is not installed or is not in PATH.
        echo Install it from https://ollama.com/download/windows, then run this file again.
        exit /b 1
    )
    echo.
    echo Downloading local model "%MODEL_NAME%"...
    "!OLLAMA_EXECUTABLE!" pull "%MODEL_NAME%"
    if errorlevel 1 (
        echo ERROR: Ollama could not download "%MODEL_NAME%".
        exit /b 1
    )
) else (
    call :find_ollama
    if not defined OLLAMA_EXECUTABLE (
        echo.
        echo Ollama is not installed yet. Get it from https://ollama.com/download/windows
        echo Then run: ollama pull %MODEL_NAME%
    ) else (
        echo.
        echo To download the default model, run:
        echo   ollama pull %MODEL_NAME%
        echo Or rerun this file with --pull-model.
    )
)

if "%INSTALL_BROWSER%"=="1" (
    echo.
    echo Installing Chromium for JavaScript-rendered product pages...
    call conda run --no-capture-output --name "%ENVIRONMENT_NAME%" python -m playwright install chromium
    if errorlevel 1 (
        echo ERROR: Chromium installation failed.
        exit /b 1
    )
)

echo.
echo Setup complete.
echo Activate the environment with:
echo   conda activate %ENVIRONMENT_NAME%
exit /b 0

:find_ollama
set "OLLAMA_EXECUTABLE="
if exist "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" set "OLLAMA_EXECUTABLE=%LOCALAPPDATA%\Programs\Ollama\ollama.exe"
if defined OLLAMA_EXECUTABLE exit /b 0
for %%G in (ollama.exe) do set "OLLAMA_EXECUTABLE=%%~$PATH:G"
exit /b 0

:conda_failed
echo ERROR: Conda could not create the environment.
exit /b 1

:pip_failed
echo ERROR: A Python package installation failed.
exit /b 1

:verification_failed
echo ERROR: PyTorch installed but the verification command failed.
exit /b 1

:show_help
echo Usage: %~nx0 [--pull-model] [--install-browser] [--model MODEL_NAME] [--recreate]
echo.
echo   --pull-model       Download the selected Ollama model after library setup.
echo   --install-browser  Download Chromium for JavaScript-rendered shop pages.
echo   --model NAME       Set a model, such as qwen3:1.7b.
echo   --recreate         Remove and recreate the Conda environment.
exit /b 1
