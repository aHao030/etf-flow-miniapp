@echo off
setlocal
pushd "%~dp0"
set "ETF_PY=%USERPROFILE%\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
if not exist "%ETF_PY%" set "ETF_PY=python"
set "PYTHONIOENCODING=utf-8"
if "%~1"=="" goto show_status
"%ETF_PY%" scripts\release.py %*
set "ETF_RESULT=%ERRORLEVEL%"
popd
exit /b %ETF_RESULT%
:show_status
chcp 65001 >nul
"%ETF_PY%" scripts\release.py status | node scripts\platform-status.mjs --display
pause
popd
