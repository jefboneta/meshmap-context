@echo off
setlocal
call "%~dp0install-meshmap-common.bat" deepseek
exit /b %errorlevel%
