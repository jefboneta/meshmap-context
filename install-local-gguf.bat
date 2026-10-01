@echo off
setlocal
call "%~dp0install-meshmap-common.bat" local
exit /b %errorlevel%
