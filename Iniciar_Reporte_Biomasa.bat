@echo off
setlocal EnableExtensions
cd /d "%~dp0"

:menu
cls
echo ========================================
echo  CALCULO_BIOMASA - Stolt Sea Farm
echo ========================================
echo.
echo  1. Instalar / reparar entorno
echo     (pide ruta a python.exe si no esta en PATH)
echo  2. Crear / editar .env (credenciales fijas)
echo  3. Generar reporte (pide fechas)
echo  4. Abrir ultimo reporte HTML
echo  5. Salir
echo.
set /p OPT=Elija opcion [1-5]: 

if "%OPT%"=="1" goto install
if "%OPT%"=="2" goto creds
if "%OPT%"=="3" goto report
if "%OPT%"=="4" goto open_last
if "%OPT%"=="5" goto end
echo Opcion no valida.
pause
goto menu

:install
call "%~dp0crear_entorno.bat"
pause
goto menu

:creds
call "%~dp0configurar_credenciales.bat"
goto menu

:report
call "%~dp0ejecutar_reporte.bat"
pause
goto menu

:open_last
for /f "delims=" %%F in ('dir /b /o-d "Reports\reporte_biomasa_*.html" 2^>nul') do (
  echo Abriendo %%F ...
  start "" "%~dp0Reports\%%F"
  goto after_open
)
echo [AVISO] No hay informes en Reports\. Genere uno primero (opcion 3).
:after_open
pause
goto menu

:end
endlocal
exit /b 0
