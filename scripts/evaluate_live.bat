@echo off
setlocal EnableExtensions
set "PYTHONNOUSERSITE=1"
set "PYTHONUTF8=1"
pushd "%~dp0.."
call conda run --no-capture-output --name local-product-search python -m evals.live %*
set "EVAL_EXIT_CODE=%ERRORLEVEL%"
popd
exit /b %EVAL_EXIT_CODE%
