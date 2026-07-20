@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo ========================================
echo  CALCULO_BIOMASA - Instalacion entorno
echo ========================================
echo.

REM Comprobar Python (py launcher o python)
set "PYEXE="
where py >nul 2>&1
if %ERRORLEVEL% EQU 0 (
  set "PYEXE=py -3"
) else (
  where python >nul 2>&1
  if %ERRORLEVEL% EQU 0 (
    set "PYEXE=python"
  )
)

if not defined PYEXE (
  echo [ERROR] No se encontro Python 3 en el PATH.
  echo         Instale Python 3.11+ desde https://www.python.org/downloads/
  echo         y marque "Add python.exe to PATH".
  exit /b 1
)

echo [INFO] Python detectado: %PYEXE%
%PYEXE% --version
if errorlevel 1 (
  echo [ERROR] No se pudo ejecutar Python.
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo [INFO] Creando entorno virtual .venv ...
  %PYEXE% -m venv .venv
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

set "USER_CREDS=%LOCALAPPDATA%\Stolt\CALCULO_BIOMASA\credentials.env"
if exist "%USER_CREDS%" (
  echo [INFO] Credenciales de usuario encontradas ^(ocultas^):
  echo        %USER_CREDS%
  echo        No hace falta recrear .env en esta carpeta.
) else if exist ".env" (
  echo [INFO] .env del proyecto encontrado.
  echo        Recomendado: ejecutar configurar_credenciales.bat
  echo        para moverlas al perfil de usuario ^(ocultas y persistentes^).
) else (
  echo [AVISO] Aun no hay credenciales configuradas.
  echo         Ejecute: configurar_credenciales.bat
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
