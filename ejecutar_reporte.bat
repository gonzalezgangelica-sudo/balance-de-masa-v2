@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [INFO] No hay entorno virtual. Ejecutando crear_entorno.bat ...
  call "%~dp0crear_entorno.bat"
  if errorlevel 1 (
    echo [ERROR] No se pudo preparar el entorno.
    exit /b 1
  )
)

if not exist ".env" (
  echo [AVISO] No hay fichero .env en la carpeta del proyecto.
  echo         Ejecute configurar_credenciales.bat ^(crea .env aqui^)
  echo         o copie .env.example a .env y complete los valores.
  exit /b 1
)

set "START_DATE=%~1"
set "END_DATE=%~2"

:ask_dates
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

REM Validar fechas de calendario ^(rechaza p.ej. 31/06^) antes de lanzar el informe
".venv\Scripts\python.exe" -c "from generar_reporte_biomasa import parse_date_range; s,e=parse_date_range(r'%START_DATE%', r'%END_DATE%'); print('OK', s.isoformat(), e.isoformat())"
if errorlevel 1 (
  echo.
  echo [ERROR] Fechas no validas. No se admiten dias inexistentes ^(ejemplo: 31/06^).
  echo         Formato: dd/mm/aaaa  ^|  La fecha fin no puede ser anterior al inicio.
  echo.
  set "START_DATE="
  set "END_DATE="
  goto ask_dates
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
