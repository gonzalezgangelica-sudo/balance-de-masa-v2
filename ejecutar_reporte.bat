@echo off
setlocal EnableExtensions
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [INFO] No hay entorno virtual. Ejecutando crear_entorno.bat ...
  call "%~dp0crear_entorno.bat"
  if errorlevel 1 (
    echo [ERROR] No se pudo preparar el entorno.
    exit /b 1
  )
)

set "USER_CREDS=%LOCALAPPDATA%\Stolt\CALCULO_BIOMASA\credentials.env"
if not exist "%USER_CREDS%" if not exist ".env" (
  echo [AVISO] No hay credenciales configuradas.
  echo         Ejecute configurar_credenciales.bat ^(recomendado^)
  echo         o cree un .env en la carpeta del proyecto.
  exit /b 1
)

set "START_DATE=%~1"
set "END_DATE=%~2"

if "%START_DATE%"=="" (
  set /p START_DATE=Fecha inicio dd/mm/aaaa: 
)
if "%END_DATE%"=="" (
  set /p END_DATE=Fecha fin dd/mm/aaaa: 
)

if "%START_DATE%"=="" (
  echo [ERROR] Debe indicar fecha de inicio.
  echo Uso: ejecutar_reporte.bat DD/MM/AAAA DD/MM/AAAA
  exit /b 1
)
if "%END_DATE%"=="" (
  echo [ERROR] Debe indicar fecha de fin.
  echo Uso: ejecutar_reporte.bat DD/MM/AAAA DD/MM/AAAA
  exit /b 1
)

echo.
echo [INFO] Generando reporte %START_DATE% - %END_DATE% ...
echo        (Business Central puede tardar varios minutos)
echo.

".venv\Scripts\python.exe" generar_reporte_biomasa.py --start "%START_DATE%" --end "%END_DATE%"
set EXITCODE=%ERRORLEVEL%

echo.
if %EXITCODE% NEQ 0 (
  echo [ERROR] Fallo al generar reporte. Codigo: %EXITCODE%
  if exist "logs\" (
    echo         Revise logs\ si existe un fichero de error.
  )
  exit /b %EXITCODE%
)

echo [OK] Reporte generado correctamente.

for /f "delims=" %%F in ('dir /b /o-d "Reports\reporte_biomasa_*.html" 2^>nul') do (
  echo [INFO] Abriendo %%F
  start "" "%~dp0Reports\%%F"
  goto :done_open
)
echo [AVISO] No se encontro HTML en Reports\ para abrir automaticamente.

:done_open
endlocal
exit /b 0
