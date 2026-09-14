@echo off
setlocal EnableExtensions
set "PYTHONNOUSERSITE=1"
set "PYTHONUTF8=1"
pushd "%~dp0.."
call conda run --no-capture-output --name local-product-search python -m ruff check app tests evals
if errorlevel 1 goto failed
call conda run --no-capture-output --name local-product-search python -m pytest -q %*
set "TEST_EXIT_CODE=%ERRORLEVEL%"
popd
exit /b %TEST_EXIT_CODE%
:failed
popd
exit /b 1
