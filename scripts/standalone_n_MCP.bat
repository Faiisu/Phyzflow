@echo off
setlocal EnableExtensions DisableDelayedExpansion

where uv.exe >nul 2>&1
if errorlevel 1 (
    echo Install uv from https://docs.astral.sh/uv/getting-started/installation/ and try again. >&2
    exit /b 1
)

for %%I in ("%~dp0..") do set "REPO_DIR=%%~fI"
set "DEFAULT_PROJECT=1"
set "PROJECT_DIR=%REPO_DIR%\default-workspace"
set "FIRST_ARG=%~1"
if defined FIRST_ARG (
    if not "%FIRST_ARG:~0,1%"=="-" (
        set "DEFAULT_PROJECT=0"
        set "PROJECT_DIR=%~f1"
        shift
    )
)

set "EXTRA_ARGS="
:collect_args
if "%~1"=="" goto args_done
set "EXTRA_ARGS=%EXTRA_ARGS% "%~1""
shift
goto collect_args
:args_done

if "%DEFAULT_PROJECT%"=="1" if not exist "%PROJECT_DIR%\." (
    mkdir "%PROJECT_DIR%"
    if errorlevel 1 exit /b 1
)
if not exist "%PROJECT_DIR%\." (
    echo Workspace directory does not exist: "%PROJECT_DIR%" >&2
    exit /b 1
)

set "PYTHON=%PROJECT_DIR%\.venv\Scripts\python.exe"
if not exist "%PYTHON%" (
    uv.exe venv --allow-existing --python 3.12 "%PROJECT_DIR%\.venv"
    if errorlevel 1 exit /b 1
)
uv.exe pip install --python "%PYTHON%" --reinstall-package workspace-sdk "%REPO_DIR%\workspace[mcp]"
if errorlevel 1 exit /b 1

set "WORKSPACE_CLI=%PROJECT_DIR%\.venv\Scripts\workspace.exe"
if "%DEFAULT_PROJECT%"=="1" if not exist "%PROJECT_DIR%\pyproject.toml" (
    "%WORKSPACE_CLI%" --project "%PROJECT_DIR%" init --with-main
    if errorlevel 1 exit /b 1
)

"%PYTHON%" "%REPO_DIR%\scripts\start_mcp_workspace.py" --project "%PROJECT_DIR%"%EXTRA_ARGS%
exit /b %errorlevel%
