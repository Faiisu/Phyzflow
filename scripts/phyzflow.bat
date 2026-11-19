@echo off
setlocal EnableExtensions DisableDelayedExpansion

where uv.exe >nul 2>&1
if errorlevel 1 (
    echo Install uv from https://docs.astral.sh/uv/getting-started/installation/ and try again. >&2
    exit /b 1
)

for %%I in ("%~dp0..") do set "REPO_DIR=%%~fI"
set "VENV_DIR=%REPO_DIR%\.venv"
set "PYTHON=%VENV_DIR%\Scripts\python.exe"

if not exist "%PYTHON%" (
    uv.exe venv --allow-existing --python 3.12 "%VENV_DIR%"
    if errorlevel 1 exit /b 1
)

uv.exe pip install --python "%PYTHON%" --reinstall-package workspace-sdk "%REPO_DIR%\workspace[mcp]"
if errorlevel 1 exit /b 1

"%VENV_DIR%\Scripts\workspace.exe" app %*
exit /b %errorlevel%
