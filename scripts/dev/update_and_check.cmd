@echo off
rem Double-click entry point for update_and_check.ps1. The execution policy bypass applies to
rem this one PowerShell process only and changes no system setting. Options pass through.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0update_and_check.ps1" %*
set EXITCODE=%ERRORLEVEL%
pause
exit /b %EXITCODE%
