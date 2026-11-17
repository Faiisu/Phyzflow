@echo off
setlocal EnableExtensions DisableDelayedExpansion

if not "%~2"=="" (
    echo Usage: run-workspace.bat [project-directory] >&2
    exit /b 2
)
where uv.exe >nul 2>&1
if errorlevel 1 (
    echo Install uv from https://docs.astral.sh/uv/getting-started/installation/ and try again. >&2
    exit /b 1
)

set "REPO_DIR=%~dp0"
set "PROJECT_DIR=%REPO_DIR%workspace-test\sheets-demo"
if not "%~1"=="" set "PROJECT_DIR=%~f1"
if not exist "%PROJECT_DIR%\." (
    echo Workspace directory does not exist: "%PROJECT_DIR%" >&2
    exit /b 1
)

set "PYTHON=%PROJECT_DIR%\.venv\Scripts\python.exe"
if not exist "%PYTHON%" (
    uv.exe venv --allow-existing --python 3.12 "%PROJECT_DIR%\.venv"
    if errorlevel 1 exit /b 1
)
uv.exe pip install --python "%PYTHON%" --reinstall-package workspace-sdk "%REPO_DIR%workspace"
if errorlevel 1 exit /b 1

"%PROJECT_DIR%\.venv\Scripts\workspace.exe" --project "%PROJECT_DIR%" ui
exit /b %errorlevel%
