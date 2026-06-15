@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] No existe entorno virtual. Ejecuta primero crear_entorno.bat
  exit /b 1
)

if "%~1"=="" (
  echo Uso:
  echo   ejecutar_reporte.bat DD/MM/AAAA DD/MM/AAAA
  echo Ejemplo:
  echo   ejecutar_reporte.bat 01/04/2026 30/04/2026
  echo.
  echo Credenciales:
  echo   - Usa DB_USER y DB_PASSWORD como variables de entorno
  echo   - O keyring ya configurado por el script
  exit /b 1
)

set START_DATE=%~1
set END_DATE=%~2

".venv\Scripts\python.exe" generar_reporte_biomasa.py --start "%START_DATE%" --end "%END_DATE%"
set EXITCODE=%ERRORLEVEL%

echo.
if %EXITCODE% EQU 0 (
  echo [OK] Reporte generado correctamente.
) else (
  echo [ERROR] Fallo al generar reporte. Codigo: %EXITCODE%
)

exit /b %EXITCODE%
