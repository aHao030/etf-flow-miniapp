@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy RemoteSigned -File "%~dp0..\scripts\open-release.ps1" %*
set "ETF_RESULT=%ERRORLEVEL%"
if "%~1"=="" if not "%ETF_RESULT%"=="0" pause
exit /b %ETF_RESULT%
