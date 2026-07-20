@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

echo ========================================
echo  CALCULO_BIOMASA - Instalacion entorno
echo ========================================
echo.
echo  Python no tiene por que estar en el PATH del sistema.
echo  Indique la ruta completa a python.exe ^(o pulse Enter si
echo  acepta la sugerencia detectada / guardada^).
echo.

set "PYFILE="
set "SUGGESTED="
set "PATH_CFG=%~dp0.python_path"

REM --- 1) Ruta guardada en .python_path ---
if exist "%PATH_CFG%" (
  set /p SUGGESTED=<"%PATH_CFG%"
  set "SUGGESTED=!SUGGESTED:"=!"
  if defined SUGGESTED if not exist "!SUGGESTED!" (
    echo [AVISO] La ruta guardada ya no existe: !SUGGESTED!
    set "SUGGESTED="
  )
)

REM --- 2) Si no hay guardada, intentar detectar en PATH ---
if not defined SUGGESTED (
  where py >nul 2>&1
  if !ERRORLEVEL! EQU 0 (
    for /f "delims=" %%P in ('where py 2^>nul') do (
      if not defined SUGGESTED set "SUGGESTED=%%P"
    )
  )
)
if not defined SUGGESTED (
  where python >nul 2>&1
  if !ERRORLEVEL! EQU 0 (
    for /f "delims=" %%P in ('where python 2^>nul') do (
      REM Evitar el stub de WindowsApps si hay otra entrada real
      echo %%P | findstr /i /c:"\WindowsApps\python.exe" >nul
      if errorlevel 1 (
        if not defined SUGGESTED set "SUGGESTED=%%P"
      ) else (
        if not defined SUGGESTED set "SUGGESTED=%%P"
      )
    )
  )
)

if defined SUGGESTED (
  echo [INFO] Sugerencia: !SUGGESTED!
  echo.
  set /p CONFIRM=Usar esta ruta? [S/n]: 
  if /i "!CONFIRM!"=="n" (
    set "SUGGESTED="
  ) else if /i "!CONFIRM!"=="no" (
    set "SUGGESTED="
  ) else (
    set "PYFILE=!SUGGESTED!"
  )
)

if not defined PYFILE (
  echo.
  echo Ejemplos:
  echo   C:\Users\%USERNAME%\AppData\Local\Programs\Python\Python313\python.exe
  echo   C:\Users\%USERNAME%\AppData\Local\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0\python.exe
  echo.
  set /p PYFILE=Ruta completa a python.exe: 
  set "PYFILE=!PYFILE:"=!"
)

if not defined PYFILE (
  echo [ERROR] No se indico ninguna ruta de Python.
  exit /b 1
)

if not exist "!PYFILE!" (
  echo [ERROR] No existe el fichero: !PYFILE!
  echo         Compruebe la ruta e intente de nuevo.
  exit /b 1
)

echo [INFO] Comprobando Python: !PYFILE!
"!PYFILE!" --version
if errorlevel 1 (
  echo [ERROR] No se pudo ejecutar ese python.exe
  exit /b 1
)

> "%PATH_CFG%" echo !PYFILE!
echo [INFO] Ruta guardada en .python_path ^(local, no se sube a git^).
echo.

if not exist ".venv\Scripts\python.exe" (
  echo [INFO] Creando entorno virtual .venv ...
  "!PYFILE!" -m venv .venv
  if errorlevel 1 (
    echo [ERROR] Fallo al crear el entorno virtual.
    exit /b 1
  )
) else (
  echo [INFO] Entorno virtual ya existe.
)

echo [INFO] Actualizando pip e instalando dependencias...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 exit /b 1
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo [ERROR] Fallo al instalar requirements.txt
  exit /b 1
)

if exist ".env" (
  echo [INFO] Credenciales del proyecto encontradas: .env
) else (
  echo [AVISO] Aun no hay credenciales configuradas.
  echo         Ejecute: configurar_credenciales.bat
  echo         ^(crea .env en esta misma carpeta^)
)

if not exist "Reports" mkdir "Reports"
if not exist "logs" mkdir "logs"

echo.
echo [OK] Entorno listo.
echo.
echo Siguiente paso:
echo   1. configurar_credenciales.bat   ^(solo la primera vez / si cambian passwords^)
echo   2. Iniciar_Reporte_Biomasa.bat   o   ejecutar_reporte.bat DD/MM/AAAA DD/MM/AAAA
echo.
endlocal
exit /b 0
