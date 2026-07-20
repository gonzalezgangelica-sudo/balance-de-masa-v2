@echo off
setlocal EnableExtensions
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [INFO] Preparando entorno...
  call "%~dp0crear_entorno.bat"
  if errorlevel 1 exit /b 1
)

".venv\Scripts\python.exe" configurar_credenciales.py
set EXITCODE=%ERRORLEVEL%
echo.
pause
exit /b %EXITCODE%
