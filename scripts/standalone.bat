@echo off
setlocal
call "%~dp0phyzflow.bat" %*
exit /b %errorlevel%
