@echo off
setlocal EnableExtensions

set "MODE=%~1"
set "SOURCE_DIR=%~dp0"
set "INSTALL_DIR=%LOCALAPPDATA%\MeshMap"
set "PY_CMD="

if /i not "%MODE%"=="deepseek" if /i not "%MODE%"=="local" (
    echo Usage: install-meshmap-common.bat deepseek^|local
    exit /b 2
)

if exist "%SystemRoot%\py.exe" set "PY_CMD=py -3"
if not defined PY_CMD (
    where py >nul 2>nul && set "PY_CMD=py -3"
)
if not defined PY_CMD (
    where python >nul 2>nul && set "PY_CMD=python"
)
if not defined PY_CMD if exist "%ProgramFiles%\Python312\python.exe" set "PY_CMD=%ProgramFiles%\Python312\python.exe"

if not defined PY_CMD (
    where winget >nul 2>nul
    if not errorlevel 1 (
        echo Python 3 was not found. Installing Python 3.12 for your Windows user...
        winget install --id Python.Python.3.12 --exact --scope user --accept-package-agreements --accept-source-agreements
        if exist "%SystemRoot%\py.exe" set "PY_CMD=py -3"
        if not defined PY_CMD (
            where python >nul 2>nul && set "PY_CMD=python"
        )
    )
)

if not defined PY_CMD (
    echo ERROR: Python 3.10 or newer is required.
    echo Install Python from https://www.python.org/downloads/windows/ and rerun this installer.
    pause
    exit /b 1
)

%PY_CMD% -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)"
if errorlevel 1 (
    echo ERROR: Python 3.10 or newer is required. Found:
    %PY_CMD% --version
    pause
    exit /b 1
)

if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%"
copy /y "%SOURCE_DIR%mindmap_app.py" "%INSTALL_DIR%\mindmap_app.py" >nul
copy /y "%SOURCE_DIR%meshmap_network.py" "%INSTALL_DIR%\meshmap_network.py" >nul
copy /y "%SOURCE_DIR%setup_profile.py" "%INSTALL_DIR%\setup_profile.py" >nul
copy /y "%SOURCE_DIR%requirements-mindmap.txt" "%INSTALL_DIR%\requirements-mindmap.txt" >nul
copy /y "%SOURCE_DIR%MINDMAP_SETUP.md" "%INSTALL_DIR%\MINDMAP_SETUP.md" >nul
if exist "%INSTALL_DIR%\mindmap_static" rmdir /s /q "%INSTALL_DIR%\mindmap_static"
xcopy /e /i /y "%SOURCE_DIR%mindmap_static" "%INSTALL_DIR%\mindmap_static" >nul
copy /y "%SOURCE_DIR%start-meshmap.bat" "%INSTALL_DIR%\start-meshmap.bat" >nul
if errorlevel 1 (
    echo ERROR: Could not copy MeshMap files from %SOURCE_DIR%
    pause
    exit /b 1
)

if not exist "%INSTALL_DIR%\.venv\Scripts\python.exe" (
    echo Creating a private Python environment. Anaconda is not required.
    %PY_CMD% -m venv "%INSTALL_DIR%\.venv"
    if errorlevel 1 goto :failed
)

echo Installing MeshMap dependencies...
"%INSTALL_DIR%\.venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r "%INSTALL_DIR%\requirements-mindmap.txt"
if errorlevel 1 goto :failed

set "SERVER_PATH="
if /i "%MODE%"=="local" (
    for /d %%D in ("%SOURCE_DIR%llama-*-bin-win-*") do (
        if exist "%%~fD\llama-server.exe" (
            set "SOURCE_SERVER_DIR=%%~fD"
            set "SERVER_DIR_NAME=%%~nxD"
        )
    )
    if defined SERVER_DIR_NAME (
        if not exist "%INSTALL_DIR%\%SERVER_DIR_NAME%" mkdir "%INSTALL_DIR%\%SERVER_DIR_NAME%"
        robocopy "%SOURCE_SERVER_DIR%" "%INSTALL_DIR%\%SERVER_DIR_NAME%" /E /NFL /NDL /NJH /NJS /NC /NS >nul
        if errorlevel 8 goto :failed
        set "SERVER_PATH=%INSTALL_DIR%\%SERVER_DIR_NAME%\llama-server.exe"
    )
)

"%INSTALL_DIR%\.venv\Scripts\python.exe" "%INSTALL_DIR%\setup_profile.py" --provider "%MODE%" --server-exe "%SERVER_PATH%"
if errorlevel 1 goto :failed

powershell -NoProfile -ExecutionPolicy Bypass -Command "$ws=New-Object -ComObject WScript.Shell; $link=$ws.CreateShortcut([Environment]::GetFolderPath('Desktop') + '\MeshMap.lnk'); $link.TargetPath='%INSTALL_DIR%\start-meshmap.bat'; $link.WorkingDirectory='%INSTALL_DIR%'; $link.Description='Start MeshMap'; $link.Save()" >nul 2>nul

echo.
echo MeshMap is installed at: %INSTALL_DIR%
if /i "%MODE%"=="deepseek" (
    echo Provider: DeepSeek API. Open Settings in MeshMap and enter your API key.
) else (
    echo Provider: Local GGUF.
    if not defined SERVER_PATH echo No bundled llama-server was found. Choose llama-server.exe in Settings.
    echo Choose your existing .gguf file in Settings; the model is not copied.
)
echo A desktop shortcut named MeshMap was created.
echo.
start "MeshMap" "%INSTALL_DIR%\start-meshmap.bat"
exit /b 0

:failed
echo.
echo Installation did not finish. The error above may indicate a Python or network issue.
echo You can rerun this installer after fixing the reported issue.
pause
exit /b 1
