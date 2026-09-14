@echo off
setlocal EnableExtensions
set "PYTHONNOUSERSITE=1"
set "PYTHONUTF8=1"
set "OLLAMA_MODEL=qwen3:4b"
set "LLM_CONTEXT_TOKENS=4096"
pushd "%~dp0.."
call conda run --no-capture-output --name local-product-search python -c "import httpx; from app.config import get_settings; s=get_settings(); r=httpx.get(s.ollama_base_url+'/api/tags',timeout=5); r.raise_for_status(); assert s.ollama_model in {m['name'] for m in r.json()['models']}, 'Run ollama pull qwen3:4b first'; print('Using',s.ollama_model,'with',s.llm_context_tokens,'context tokens')"
if errorlevel 1 goto failed
call scripts\run_app.bat
set "APP_EXIT_CODE=%ERRORLEVEL%"
popd
exit /b %APP_EXIT_CODE%
:failed
echo Qwen is not ready. Start Ollama and run: ollama pull qwen3:4b
popd
exit /b 1
