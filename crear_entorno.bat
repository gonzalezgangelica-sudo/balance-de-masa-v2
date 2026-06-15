@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [INFO] Creando entorno virtual...
  py -3 -m venv .venv
)

echo [INFO] Instalando dependencias...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt

echo.
echo [OK] Entorno listo.
echo     Ejecuta: ejecutar_reporte.bat
endlocal
