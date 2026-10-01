@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo MeshMap environment is missing. Run the installer again.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" "mindmap_app.py"
if errorlevel 1 (
    echo MeshMap closed with an error. Review the message above.
    pause
)
