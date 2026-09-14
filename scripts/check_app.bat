@echo off
setlocal EnableExtensions
set "PYTHONNOUSERSITE=1"
pushd "%~dp0.."
call conda run --no-capture-output --name local-product-search python -X utf8 -m app.diagnostics %*
set "CHECK_EXIT_CODE=%ERRORLEVEL%"
popd
exit /b %CHECK_EXIT_CODE%
